import argparse
import ctypes
import math
import struct
import time

import snap7
from snap7.type import Parameter, SrvArea

# Simulador de CLP Siemens S7 (ISO-on-TCP / S7comm) via Snap7 Server.
# Endereços implementados conforme a imagem:
# - DB51: DBF0, DBF8, DBF12
# - DB50: DBF0, DBF4, DBF8, DBF12, DBF20
# - DB49: DBF24, DBF28
# - DB52: DBF0, DBF4, DBF8, DBF12, DBF156, DBF160, DBF164, DBF172

TCP_PORT = 1102

def _write_real_be(db_buffer: ctypes.Array, byte_offset: int, value: float) -> None:
    struct.pack_into(">f", db_buffer, byte_offset, float(value))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _db_size_from_offsets(offsets: list[int], extra_bytes: int = 16) -> int:
    max_offset = max(offsets) if offsets else 0
    # REAL = 4 bytes, então precisamos pelo menos max_offset + 4.
    return int(max_offset + 4 + extra_bytes)


def _register_db(server: snap7.server.Server, db_number: int, size: int) -> ctypes.Array:
    # Importante: use um array numérico (c_uint8) ao invés de create_string_buffer (c_char[]).
    # Alguns clientes/driver leem incorretamente quando a área é registrada como c_char[].
    db_memory = (ctypes.c_uint8 * int(size))()
    server.register_area(SrvArea.DB, db_number, db_memory)
    return db_memory


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulador Siemens S7 (Snap7 Server) para Elipse E3 (MProt).")
    parser.add_argument("--port", type=int, default=TCP_PORT, help="Porta TCP (padrão: 102).")
    parser.add_argument("--cycle-ms", type=int, default=500, help="Tempo de ciclo em ms (padrão: 500).")
    parser.add_argument(
        "--pdu",
        type=int,
        default=480,
        help="Tamanho de PDU anunciado (padrão: 480; compatível com muitos drivers S7 antigos).",
    )
    parser.add_argument(
        "--log-events",
        action="store_true",
        help="Imprime eventos do servidor (ajuda a diagnosticar conexão/leitura do driver).",
    )
    args = parser.parse_args()

    # Mapa de tags (somente para referência/organização)
    tags: dict[int, dict[str, int]] = {
        51: {
            "Current": 0, 
            "Voltage": 8, 
            "Carga_Bateria": 12
        },
        50: {
            "PV_Power_In": 0,
            "Current_Bus": 4,
            "Voltage_Bus": 8,
            "Max_Power_Today": 12,
            "Yield_Diario": 20,
        },
        49: {
            "Current_Out": 24, 
            "Voltage_Out": 28
        },
        52: {
            "Current_R": 0,
            "Current_S": 4,
            "Current_T": 8,
            "Current_N": 12,
            "Current_L1N": 156,
            "Current_L2N": 160,
            "Current_L3N": 164,
            "Power_Sys": 172,
        },
    }

    db_sizes = {db: _db_size_from_offsets(list(offsets.values())) for db, offsets in tags.items()}

    server = snap7.server.Server()
    db_memory: dict[int, ctypes.Array] = {}

    # Estado para métricas (máximo e energia diária)
    max_power_today = 0.0
    yield_kwh = 0.0
    last_ts = time.monotonic()

    try:
        # Força CPU virtual em RUN (alguns clientes/driver são sensíveis a isso).
        server.set_cpu_status(8)  # 8 = RUN, 4 = STOP

        # Alguns drivers “antigos” esperam negociar PDU 480/960. O default do snap7 server pode vir 0.
        if args.pdu and args.pdu > 0:
            server.set_param(Parameter.PDURequest, int(args.pdu))

        try:
            server.start(args.port)
        except Exception as e:
            print(
                f"Falha ao iniciar servidor na porta {args.port}: {e}\n"
                "Dica: verifique se a porta está ocupada (Windows: `netstat -ano | findstr :102`).\n"
                "Se estiver ocupada, use `--port 1102` e ajuste a porta no driver do Elipse."
            )
            raise
        for db, size in sorted(db_sizes.items()):
            db_memory[db] = _register_db(server, db, size)

        print(f"Simulador de CLP Siemens S7 rodando em 0.0.0.0:{args.port}")
        print(f"PDURequest={args.pdu} | CPU=RUN")
        print("DBs publicados: " + ", ".join(f"DB{db}({size}B)" for db, size in sorted(db_sizes.items())))
        print("CTRL+C para parar.")

        cycle_s = max(0.05, args.cycle_ms / 1000.0)
        last_status_print = 0.0

        while True:
            now = time.monotonic()
            dt = max(0.0, now - last_ts)
            last_ts = now

            # --- Perfis de simulação (suaves e estáveis) ---
            # Potência FV (W): ciclo ~60s
            pv = 3500.0 * (math.sin(2.0 * math.pi * now / 60.0) * 0.5 + 0.5)
            pv = _clamp(pv, 0.0, 4500.0)

            # Barramento CC (V) e corrente (A)
            vbus = 620.0 + 10.0 * math.sin(2.0 * math.pi * now / 25.0)
            ibus = (pv / max(vbus, 1.0)) if pv > 10.0 else 0.0

            # Saída do inversor
            vout = 220.0 + 2.0 * math.sin(2.0 * math.pi * now / 20.0)
            iout = _clamp((pv / max(vout, 1.0)) / 3.0, 0.0, 25.0)

            # Bateria (valores típicos de 48V)
            vbat = 52.5 + 0.8 * math.sin(2.0 * math.pi * now / 40.0)
            load_w = 1200.0 + 300.0 * math.sin(2.0 * math.pi * now / 33.0)
            p_bat = pv - load_w  # positivo = carga, negativo = descarga
            ibat = _clamp(p_bat / max(vbat, 1.0), -80.0, 80.0)

            # "Carga_Bateria" (%): converge lentamente conforme tensão da bateria
            soc_speed = 0.02 * (cycle_s / 0.5)
            soc_target = _clamp((vbat - 48.0) / (55.0 - 48.0) * 100.0, 0.0, 100.0)
            try:
                soc_now = struct.unpack_from(">f", db_memory[51], tags[51]["Carga_Bateria"])[0]
                if math.isnan(soc_now) or math.isinf(soc_now):
                    soc_now = soc_target
            except Exception:
                soc_now = soc_target
            soc = soc_now + _clamp(soc_target - soc_now, -soc_speed, soc_speed)

            # Máximo e energia diária
            if pv > max_power_today:
                max_power_today = pv
            yield_kwh += (pv * dt) / 3_600_000.0  # W*s -> kWh

            # Correntes trifásicas do medidor (A)
            ir = 12.0 + 2.0 * math.sin(2.0 * math.pi * now / 17.0)
            is_ = 11.0 + 2.3 * math.sin(2.0 * math.pi * now / 19.0 + 1.0)
            it = 13.0 + 1.7 * math.sin(2.0 * math.pi * now / 23.0 + 2.0)
            in_ = _clamp(abs(ir + is_ + it) * 0.02, 0.0, 5.0)

            # Analizador CB (A) e potência do sistema (W)
            il1n = ir + 0.4
            il2n = is_ + 0.2
            il3n = it + 0.3
            psys = pv - _clamp(load_w, 0.0, 99999.0)

            # --- Escritas nas DBs (REAL big-endian) ---
            # DB51 - Bateria_BYD
            _write_real_be(db_memory[51], tags[51]["Current"], ibat)
            _write_real_be(db_memory[51], tags[51]["Voltage"], vbat)
            _write_real_be(db_memory[51], tags[51]["Carga_Bateria"], soc)

            # DB50 - Controlador_Carga
            _write_real_be(db_memory[50], tags[50]["PV_Power_In"], pv)
            _write_real_be(db_memory[50], tags[50]["Current_Bus"], ibus)
            _write_real_be(db_memory[50], tags[50]["Voltage_Bus"], vbus)
            _write_real_be(db_memory[50], tags[50]["Max_Power_Today"], max_power_today)
            _write_real_be(db_memory[50], tags[50]["Yield_Diario"], yield_kwh)

            # DB49 - Inversor_Multiplus
            _write_real_be(db_memory[49], tags[49]["Current_Out"], iout)
            _write_real_be(db_memory[49], tags[49]["Voltage_Out"], vout)

            # DB52 - Medidor_Schneider + Analizador_CB
            _write_real_be(db_memory[52], tags[52]["Current_R"], ir)
            _write_real_be(db_memory[52], tags[52]["Current_S"], is_)
            _write_real_be(db_memory[52], tags[52]["Current_T"], it)
            _write_real_be(db_memory[52], tags[52]["Current_N"], in_)
            _write_real_be(db_memory[52], tags[52]["Current_L1N"], il1n)
            _write_real_be(db_memory[52], tags[52]["Current_L2N"], il2n)
            _write_real_be(db_memory[52], tags[52]["Current_L3N"], il3n)
            _write_real_be(db_memory[52], tags[52]["Power_Sys"], psys)

            if args.log_events:
                # Atenção: isso pode gerar bastante saída, dependendo do polling do driver.
                while True:
                    event = server.pick_event()
                    if event is None:
                        break
                    print(server.event_text(event))

                # Status a cada ~2s (mostra se o driver conectou)
                if now - last_status_print > 2.0:
                    last_status_print = now
                    srv_status, cpu_status, clients = server.get_status()
                    print(f"STATUS: {srv_status}, {cpu_status}, clients={clients}")

            time.sleep(cycle_s)

    except KeyboardInterrupt:
        print("\nParando servidor...")
    except Exception as e:
        print(f"Erro: {e}")
    finally:
        try:
            server.stop()
        finally:
            server.destroy()


if __name__ == "__main__":
    main()
