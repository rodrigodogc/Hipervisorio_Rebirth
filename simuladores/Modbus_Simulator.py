import asyncio
import random
import colorama
from colorama import Fore, Style
from pymodbus.server import ModbusTcpServer
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext, ModbusDeviceContext

SERVER_IP = "0.0.0.0"
SERVER_PORT = 502
UPDATE_INTERVAL = 0.5

async def updating_task(bloco_hr):
    """Task assíncrona para atualizar os valores dos registradores."""
    print(Fore.CYAN + "Task de atualização de dados iniciada.")
    
    while True:
        try:
            # Simula os dados
            tensao = int(random.uniform(218.0, 222.0) * 10)
            corrente = int(random.uniform(4.5, 8.0) * 10)
            temperatura = int(random.uniform(18.0, 25.0) * 10)
            umidade = int(random.uniform(40.0, 60.0) * 10)
            consumo = int(random.uniform(1000, 1800))
            
            valores = [tensao, corrente, temperatura, umidade, consumo]
            
            bloco_hr.setValues(1, valores)
            
            print(
                Fore.GREEN + f"[UPDATE] " +
                f"Tensão: {tensao/10:.1f}V | " +
                f"Corrente: {corrente/10:.1f}A | " +
                f"Temp: {temperatura/10:.1f}°C | " +
                f"Umidade: {umidade/10:.1f}% | " +
                f"Consumo: {consumo}W"
            )
            
            await asyncio.sleep(UPDATE_INTERVAL)

        except Exception as e:
            print(Fore.RED + f"Erro na task de atualização: {e}")
            await asyncio.sleep(UPDATE_INTERVAL)


async def main():
    """Configura e executa o servidor e a task de atualização."""
    
    print(Fore.YELLOW + Style.BRIGHT + "========================================")
    print(Fore.YELLOW + Style.BRIGHT + "  Simulador Modbus TCP (Slave)")
    print(Fore.YELLOW + Style.BRIGHT + "========================================")
    print(f"  {Style.DIM}Escutando em: {Fore.WHITE}{SERVER_IP}:{SERVER_PORT}")
    print(f"  {Style.DIM}Atualização:  {Fore.WHITE}A cada {UPDATE_INTERVAL} segundos")
    print(Style.RESET_ALL)
    
    bloco_hr = ModbusSequentialDataBlock(1, [0] * 5)
    
    device_context = ModbusDeviceContext(
        di=None,
        co=None,
        hr=bloco_hr,
        ir=None
    )

    server_context = ModbusServerContext(devices=device_context, single=True)
    
    server = ModbusTcpServer(
        context=server_context,
        address=(SERVER_IP, SERVER_PORT)
    )

    print(Fore.CYAN + "Iniciando servidor Modbus TCP (Slave)...")

    task_servidor = asyncio.create_task(server.serve_forever())
    
    task_atualizacao = asyncio.create_task(updating_task(bloco_hr))
    
    try:
        await asyncio.gather(task_servidor, task_atualizacao)
    finally:
        print(Fore.RED + "\nEncerrando servidor...")
        await server.shutdown()


if __name__ == "__main__":
    colorama.init(autoreset=True)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(Fore.MAGENTA + "\nServidor interrompido pelo usuário (Ctrl+C).")