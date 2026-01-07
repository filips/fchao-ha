#!/usr/bin/env python3
"""
FCHAO Inverter RS485 to Home Assistant via MQTT
Reads inverter data and publishes to MQTT for Home Assistant auto-discovery
Uses asyncio event-driven architecture
"""

import asyncio
import json
import signal
import sys
import time
from typing import Callable, Awaitable

import aioserial
import aiomqtt

# Configuration
SERIAL_PORT = '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0'
SERIAL_BAUDRATE = 9600
QUERY_INTERVAL = 1.0
TIMEOUT_THRESHOLD = 10.0

MQTT_BROKER = 'localhost'
MQTT_PORT = 1883
MQTT_USERNAME = None
MQTT_PASSWORD = None
MQTT_CLIENT_ID = 'fchao_inverter'

# Home Assistant MQTT Discovery
DEVICE_NAME = 'FCHAO Inverter'
DEVICE_ID = 'fchao_inverter'
HA_DISCOVERY_PREFIX = 'homeassistant'
AVAILABILITY_TOPIC = f"{DEVICE_ID}/availability"

# Commands
COMMAND_QUERY = '010103'
COMMAND_ON = '0102040000'
COMMAND_OFF = '0102040100'


def parse_bcd_value(hex_str):
    """Convert BCD hex string to decimal integer"""
    try:
        return int(hex_str)
    except ValueError:
        return None


def parse_inverter_data(data):
    """Parse the inverter response packet (17 bytes)"""
    if len(data) != 17:
        return None

    hex_str = data.hex().upper()

    if not hex_str.startswith('AE01') or not hex_str.endswith('EE'):
        return None

    try:
        reserved_byte = int(hex_str[24:26], 16)
        alarm_byte = int(hex_str[26:28], 16)

        parsed = {
            'voltage_out': parse_bcd_value(hex_str[8:12]),
            'power': parse_bcd_value(hex_str[12:16]),
            'voltage_in': parse_bcd_value(hex_str[16:20]) / 10.0,
            'temperature': parse_bcd_value(hex_str[20:24]),
            'status': 'OFF' if reserved_byte & 0x01 else 'ON',
            'fan': 'ON' if alarm_byte & 0x40 else 'OFF'
        }

        return parsed
    except (ValueError, IndexError):
        return None


def build_packet(command_bytes: str) -> bytes:
    """Build command packet with checksum"""
    packet = bytearray.fromhex('AE')
    packet.extend(bytearray.fromhex(command_bytes))
    checksum_value = sum(packet[1:])
    packet.append(checksum_value & 0xFF)
    packet.append(0xEE)
    return bytes(packet)


class SerialManager:
    """Handles async serial communication with the inverter"""

    START_MARKER = 0xAE
    END_MARKER = 0xEE
    MAX_FRAME_SIZE = 100

    def __init__(self):
        self.serial: aioserial.AioSerial | None = None
        self._running = False
        self._data_callback: Callable[[dict], Awaitable[None]] | None = None

    def set_data_callback(self, callback: Callable[[dict], Awaitable[None]]):
        """Set callback for parsed data packets"""
        self._data_callback = callback

    async def connect(self):
        """Open serial port"""
        self.serial = aioserial.AioSerial(
            port=SERIAL_PORT,
            baudrate=SERIAL_BAUDRATE,
            bytesize=aioserial.EIGHTBITS,
            parity=aioserial.PARITY_NONE,
            stopbits=aioserial.STOPBITS_ONE,
            timeout=0.1,
        )
        self._running = True
        print("✓ Serial port opened")

    async def disconnect(self):
        """Close serial port"""
        self._running = False
        if self.serial and self.serial.is_open:
            self.serial.close()

    async def send_command(self, command: str):
        """Send command packet to inverter"""
        if self.serial and self.serial.is_open:
            packet = build_packet(command)
            await self.serial.write_async(packet)

    async def _read_frame(self) -> bytes | None:
        """Read a complete frame from AE to EE markers"""
        # Wait for start marker
        while self._running:
            byte = await asyncio.wait_for(
                self.serial.read_async(1),
                timeout=0.5
            )
            if byte and byte[0] == self.START_MARKER:
                break
        else:
            return None

        # Read until end marker
        frame = bytearray([self.START_MARKER])
        while self._running and len(frame) < self.MAX_FRAME_SIZE:
            byte = await asyncio.wait_for(
                self.serial.read_async(1),
                timeout=0.5
            )
            if not byte:
                continue
            frame.append(byte[0])
            if byte[0] == self.END_MARKER:
                return bytes(frame)

        return None

    async def read_loop(self):
        """Continuously read and parse serial frames"""
        while self._running:
            try:
                frame = await self._read_frame()
                if frame:
                    await self._process_frame(frame)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    print(f"Serial read error: {e}")
                    await asyncio.sleep(1)

    def _verify_checksum(self, frame: bytes) -> bool:
        """Verify frame checksum. Checksum is sum of payload bytes (between AE and checksum)."""
        if len(frame) < 4:  # AE + at least 1 payload byte + checksum + EE
            return False
        payload = frame[1:-2]  # Bytes between AE and checksum
        expected = sum(payload) & 0xFF
        actual = frame[-2]
        return expected == actual

    async def _process_frame(self, frame: bytes):
        """Process a complete frame"""
        if not self._verify_checksum(frame):
            print(f"  Checksum error: {frame.hex().upper()}")
            return

        if len(frame) == 17:
            parsed = parse_inverter_data(frame)
            if parsed and self._data_callback:
                await self._data_callback(parsed)
        elif len(frame) > 3:
            print(f"  Non-data packet ({len(frame)} bytes): {frame.hex().upper()}")


class MQTTManager:
    """Handles async MQTT communication"""

    def __init__(self):
        self._command_callback: Callable[[str], Awaitable[None]] | None = None
        self._running = False
        self._client: aiomqtt.Client | None = None
        self._publish_queue: asyncio.Queue = asyncio.Queue()

    def set_command_callback(self, callback: Callable[[str], Awaitable[None]]):
        """Set callback for incoming commands"""
        self._command_callback = callback

    async def run(self):
        """Main MQTT loop with auto-reconnect"""
        self._running = True

        while self._running:
            try:
                async with aiomqtt.Client(
                    hostname=MQTT_BROKER,
                    port=MQTT_PORT,
                    username=MQTT_USERNAME,
                    password=MQTT_PASSWORD,
                    identifier=MQTT_CLIENT_ID,
                ) as client:
                    self._client = client
                    print(f"✓ Connected to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}")

                    await self._publish_discovery()
                    await self.publish_availability(True)

                    # Subscribe to command topic
                    command_topic = f"{DEVICE_ID}/switch/set"
                    await client.subscribe(command_topic)
                    print(f"✓ Subscribed to switch commands\n")

                    # Run message listener and publish handler concurrently
                    await asyncio.gather(
                        self._message_loop(client, command_topic),
                        self._publish_loop(client),
                    )

            except aiomqtt.MqttError as e:
                self._client = None
                if self._running:
                    print(f"MQTT error: {e}. Reconnecting in 5s...")
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                break

        self._client = None

    async def _message_loop(self, client: aiomqtt.Client, command_topic: str):
        """Listen for incoming MQTT messages"""
        async for message in client.messages:
            if message.topic.matches(command_topic):
                payload = message.payload.decode().upper()
                if self._command_callback:
                    await self._command_callback(payload)

    async def _publish_loop(self, client: aiomqtt.Client):
        """Process publish queue"""
        while self._running:
            try:
                topic, payload, retain = await asyncio.wait_for(
                    self._publish_queue.get(),
                    timeout=1.0
                )
                await client.publish(topic, payload, retain=retain)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    async def publish_state(self, data: dict):
        """Queue state data for publishing"""
        topic = f"{DEVICE_ID}/state"
        payload = json.dumps(data)
        await self._publish_queue.put((topic, payload, False))

    async def publish_availability(self, available: bool):
        """Queue availability status for publishing"""
        payload = "online" if available else "offline"
        await self._publish_queue.put((AVAILABILITY_TOPIC, payload, True))

    async def _publish_discovery(self):
        """Publish Home Assistant discovery messages"""
        sensors = [
            {
                'name': 'AC Output Voltage',
                'key': 'voltage_out',
                'unit': 'V',
                'device_class': 'voltage',
                'icon': 'mdi:current-ac'
            },
            {
                'name': 'DC Input Voltage',
                'key': 'voltage_in',
                'unit': 'V',
                'device_class': 'voltage',
                'icon': 'mdi:current-dc'
            },
            {
                'name': 'Output Power',
                'key': 'power',
                'unit': 'W',
                'device_class': 'power',
                'icon': 'mdi:lightning-bolt'
            },
            {
                'name': 'Temperature',
                'key': 'temperature',
                'unit': '°C',
                'device_class': 'temperature',
                'icon': 'mdi:thermometer'
            }
        ]

        device_info = {
            'identifiers': [DEVICE_ID],
            'name': DEVICE_NAME,
            'model': 'FCHAO Inverter',
            'manufacturer': 'FCHAO'
        }

        # Publish sensor discoveries
        for sensor in sensors:
            topic = f"{HA_DISCOVERY_PREFIX}/sensor/{DEVICE_ID}/{sensor['key']}/config"
            config = {
                'name': sensor['name'],
                'unique_id': f"{DEVICE_ID}_{sensor['key']}",
                'state_topic': f"{DEVICE_ID}/state",
                'value_template': f"{{{{ value_json.{sensor['key']} }}}}",
                'unit_of_measurement': sensor['unit'],
                'device_class': sensor['device_class'],
                'icon': sensor['icon'],
                'availability_topic': AVAILABILITY_TOPIC,
                'payload_available': 'online',
                'payload_not_available': 'offline',
                'device': device_info
            }
            await self._client.publish(topic, json.dumps(config), retain=True)

        # Publish switch discovery
        switch_topic = f"{HA_DISCOVERY_PREFIX}/switch/{DEVICE_ID}/output/config"
        switch_config = {
            'name': "Output",
            'unique_id': f"{DEVICE_ID}_output_switch",
            'state_topic': f"{DEVICE_ID}/state",
            'value_template': "{{ value_json.status }}",
            'command_topic': f"{DEVICE_ID}/switch/set",
            'payload_on': 'ON',
            'payload_off': 'OFF',
            'state_on': 'ON',
            'state_off': 'OFF',
            'icon': 'mdi:power',
            'availability_topic': AVAILABILITY_TOPIC,
            'payload_available': 'online',
            'payload_not_available': 'offline',
            'device': device_info
        }
        await self._client.publish(switch_topic, json.dumps(switch_config), retain=True)

        # Publish fan binary sensor
        fan_topic = f"{HA_DISCOVERY_PREFIX}/binary_sensor/{DEVICE_ID}/fan/config"
        fan_config = {
            'name': "Fan",
            'unique_id': f"{DEVICE_ID}_fan",
            'state_topic': f"{DEVICE_ID}/state",
            'value_template': "{{ value_json.fan }}",
            'payload_on': 'ON',
            'payload_off': 'OFF',
            'device_class': 'running',
            'icon': 'mdi:fan',
            'availability_topic': AVAILABILITY_TOPIC,
            'payload_available': 'online',
            'payload_not_available': 'offline',
            'device': device_info
        }
        await self._client.publish(fan_topic, json.dumps(fan_config), retain=True)

        print("✓ Published Home Assistant discovery messages")


class InverterApp:
    """Main application coordinator"""

    def __init__(self):
        self.serial_manager = SerialManager()
        self.mqtt_manager = MQTTManager()

        self._last_response_time = 0.0
        self._is_available = True
        self._running = False
        self._tasks: list[asyncio.Task] = []

        # Wire up callbacks
        self.serial_manager.set_data_callback(self._on_inverter_data)
        self.mqtt_manager.set_command_callback(self._on_mqtt_command)

    async def run(self):
        """Main application entry point"""
        self._running = True
        self._last_response_time = time.time()

        # Connect to serial
        await self.serial_manager.connect()

        # Create and run all tasks concurrently
        self._tasks = [
            asyncio.create_task(self.serial_manager.read_loop()),
            asyncio.create_task(self.mqtt_manager.run()),
            asyncio.create_task(self._query_loop()),
            asyncio.create_task(self._availability_monitor()),
        ]

        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            pass

    async def _query_loop(self):
        """Send periodic queries to inverter"""
        while self._running:
            await self.serial_manager.send_command(COMMAND_QUERY)
            await asyncio.sleep(QUERY_INTERVAL)

    async def _availability_monitor(self):
        """Monitor for timeout and update availability"""
        while self._running:
            elapsed = time.time() - self._last_response_time

            if elapsed > TIMEOUT_THRESHOLD and self._is_available:
                print(f"\n⚠ No response for {elapsed:.0f}s - marking unavailable")
                await self.mqtt_manager.publish_availability(False)
                self._is_available = False

            await asyncio.sleep(1)

    async def _on_inverter_data(self, data: dict):
        """Handle parsed inverter data"""
        self._last_response_time = time.time()

        # Restore availability if needed
        if not self._is_available:
            print("✓ Inverter responding again - marking available\n")
            await self.mqtt_manager.publish_availability(True)
            self._is_available = True

        await self.mqtt_manager.publish_state(data)

    async def _on_mqtt_command(self, command: str):
        """Handle MQTT switch commands"""
        if command == "ON":
            print("→ Turning inverter ON")
            await self.serial_manager.send_command(COMMAND_ON)
        elif command == "OFF":
            print("→ Turning inverter OFF")
            await self.serial_manager.send_command(COMMAND_OFF)

    async def shutdown(self):
        """Graceful shutdown"""
        print("\n\nShutting down...")
        self._running = False

        # Publish offline status
        await self.mqtt_manager.publish_availability(False)

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()

        # Wait for tasks to complete
        await asyncio.gather(*self._tasks, return_exceptions=True)

        # Close serial
        await self.serial_manager.disconnect()
        print("Disconnected")


async def main():
    print("=" * 60)
    print("FCHAO Inverter to Home Assistant via MQTT")
    print("=" * 60)
    print(f"Serial: {SERIAL_PORT} @ {SERIAL_BAUDRATE} baud")
    print(f"MQTT: {MQTT_BROKER}:{MQTT_PORT}")
    print(f"Query Interval: {QUERY_INTERVAL}s")
    print("=" * 60)
    print()

    app = InverterApp()

    # Setup signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()

    def signal_handler():
        asyncio.create_task(app.shutdown())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await app.run()
    except Exception as e:
        print(f"\nError: {e}")
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
