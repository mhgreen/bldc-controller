import json
from contextlib import asynccontextmanager

import asyncgpio as gpio
import paho.mqtt.client as mqtt
import trio
from loguru import logger
from serial.tools import list_ports
from trio import run
# from trio_paho_mqtt import AsyncClient
from trio_paho_mqtt import AsyncClient, mqtt_client
from trio_serial import SerialStream

from packages.shutdown_manager import ShutdownManager

async def serial_manager(shutdown_manager, receive_command):
    logger.debug(f'serial_manager starting')
    with shutdown_manager.cancel_on_shutdown():
        # async with SerialStream(
        #     port=mdc100_port,
        #     baudrate=38400,
        #     xonxoff=True
        # ) as serial:
        async with receive_command:
            async for command in receive_command:
                logger.debug(f'serial_manager got command: {command}')
    if shutdown_manager.shutting_down:
        logger.debug(f'serial_manager stopped')
        return


async def process_messages(mdc100_port, shutdown_manager, send_command):
    logger.debug(f'process_messages starting')
    with shutdown_manager.cancel_on_shutdown():
        async with SerialStream(
            port=mdc100_port,
            baudrate=38400,
            xonxoff=True
        ) as serial:
            async with mqtt_client('192.168.7.122', 1883, 60) as client:
                result = client.subscribe("mdc100", qos=1)
                logger.debug(f'result of client.subscribe: {result}')
                async with send_command:
                    async for message in client.messages():
                        logger.debug(f'message: {message.payload.decode()}')
                        json_dict = json.loads(message.payload.decode())
                        commands = [json_dict["cmd"].encode()+b'\r']
                        # Append a return code command to the request
                        commands.append(b'@0!\r')
                        logger.debug(f'command: {commands}')
                        # commands = [b'@0V*\r', b'@0!\r']
                        for command in commands:
                            await serial.send_all(command)
                            logger.debug(f'sent: {command.decode().rstrip()}')
                            await send_command.send(f'{command.decode().rstrip()}')
                            await trio.sleep(0.1)
                        with trio.move_on_after(0.1) as cancel_scope:
                            data = await serial.receive_some(70)
                            logger.debug(f'got: {data.decode()!r}')
                            logger.debug(f'with a length of {len(data)} bytes')
                            output_list = data.split(b'\r')
                            output_list = [element.decode('utf8')
                                           for element in output_list]
                            output_list = list(filter(None, output_list))
                            logger.debug(f'returned list: {output_list!r}')
                        if cancel_scope.cancelled_caught:
                            logger.debug(
                                f'serial read timed out after .1 seconds')
    if shutdown_manager.shutting_down:
        logger.debug(f'process_messaging stopped')
        return


async def quick_toggle(chip_id, pin):
    with gpio.Chip(label=chip_id) as chip:
        pin_out = chip.line(pin)
        with pin_out.open(direction=gpio.DIRECTION_OUTPUT) as current_pin:
            logger.debug(
                f'toggling control on MDC100 to set to prevent unexpected startup')
            current_pin.value = 0
            await trio.sleep(.05)
            current_pin.value = 1


@asynccontextmanager
async def open_nurseries():
    # Outer and inner nurseries from https://github.com/python-trio/trio/issues/569
    # Interesting example of using this technique: https://gist.github.com/miracle2k/8499df40a7b650198bbbc3038a6fb292
    async with trio.open_nursery() as daemon_nursery:
        try:
            async with trio.open_nursery() as normal_nursery:
                yield (normal_nursery, daemon_nursery)
        finally:
            daemon_nursery.cancel_scope.cancel()


async def main(mdc100_port):
    logger.info('trio-mqtt-serial started')
    await quick_toggle('pinctrl-bcm2711', pin=25)
    shutdown_manager = ShutdownManager()
    async with open_nurseries() as nurseries:
        nursery, daemon = nurseries
        daemon.start_soon(shutdown_manager._listen_for_shutdown_signals)
        send_command, receive_command = trio.open_memory_channel(0)
        async with send_command, receive_command:
            # mqtt_sync = mqtt.Client()
            # client = AsyncClient(mqtt_sync, nursery)
            # client.connect('192.168.7.122', 1883, 60)
            # client.subscribe("mdc100", qos=1)
            nursery.start_soon(process_messages, mdc100_port,
                               shutdown_manager, send_command.clone())
            nursery.start_soon(
                serial_manager, shutdown_manager, receive_command.clone())
    logger.info('trio-mqtt-serial stopped')


if __name__ == '__main__':
    # Don't start the loop unless the MDC100 is found
    ports = []
    mdc100_port = ''
    start = False
    for port, desc, hwid in sorted(list_ports.comports()):
        logger.debug(f'port: {port}, desc: {desc}, hwid: {hwid}')
        ports.append(desc)
        if (desc.find('MDC100') > 0):
            mdc100_port = port
            start = True
            logger.info(f'found MDC100 attached to {mdc100_port}')

    if (start == False):
        logger.error(f'No MDC100 unit found. Exiting.')
        logger.info(f'The following ports were found:')
        for port, desc, hwid in sorted(list_ports.comports()):
            logger.info(f'port: {port}, desc: {desc}, hwid: {hwid}')
    else:
        trio.run(main, mdc100_port)
