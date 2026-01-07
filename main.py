#!/usr/bin/env python3
"""
FCHAO Inverter RS485 to Home Assistant via MQTT
Reads inverter data and publishes to MQTT for Home Assistant auto-discovery
"""

import serial
import time
import json
import sys
from datetime import datetime

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("Error: paho-mqtt library not found")
    print("Install with: pip3 install paho-mqtt")
    sys.exit(1)

# Configuration
SERIAL_PORT = '/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0'
SERIAL_BAUDRATE = 9600
QUERY_INTERVAL = 1.0  # Query every 2 seconds
TIMEOUT_THRESHOLD = 10.0  # Mark unavailable after 10 seconds without response

MQTT_BROKER = 'localhost'  # Change to your MQTT broker IP
MQTT_PORT = 1883
MQTT_USERNAME = None  # Set if authentication required
MQTT_PASSWORD = None  # Set if authentication required
MQTT_CLIENT_ID = 'fchao_inverter'

# Home Assistant MQTT Discovery
DEVICE_NAME = 'FCHAO Inverter'
DEVICE_ID = 'fchao_inverter'
HA_DISCOVERY_PREFIX = 'homeassistant'
AVAILABILITY_TOPIC = f"{DEVICE_ID}/availability"

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
        reserved_byte = int(hex_str[24:26], 16)  # Byte 12 (position 24-26 in hex string)
        alarm_byte = int(hex_str[26:28], 16) # Byte 13 (position 26-28 in hex string)
        
        parsed = {
            'voltage_out': parse_bcd_value(hex_str[8:12]),  # AC Volts
            'power': parse_bcd_value(hex_str[12:16]),  # Watts
            'voltage_in': parse_bcd_value(hex_str[16:20]) / 10.0,  # DC Volts
            'temperature': parse_bcd_value(hex_str[20:24]),  # Celsius
            'status': 'OFF' if reserved_byte & 0x01 else 'ON',  # 0x06 = OFF, other = ON
            'fan': 'ON' if alarm_byte & 0x40 else 'OFF'
        }

        return parsed
    except (ValueError, IndexError):
        return None

def send_query(ser, command_bytes='010103'):
    """Send query command to inverter"""
    packet = bytearray.fromhex('AE')
    try:
        packet.extend(bytearray.fromhex(command_bytes))
    except ValueError:
        return False
    
    checksum_value = sum(packet[1:])
    packet.append(checksum_value & 0xFF)
    packet.append(0xEE)
    
    ser.write(packet)
    ser.flush()
    return True

class MQTTPublisher:
    def __init__(self, serial_port):
        self.client = mqtt.Client(client_id=MQTT_CLIENT_ID)
        self.serial_port = serial_port
        
        if MQTT_USERNAME and MQTT_PASSWORD:
            self.client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.connected = False
        
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"✓ Connected to MQTT broker at {MQTT_BROKER}:{MQTT_PORT}")
            self.connected = True
            self.publish_discovery()
            # Subscribe to switch command topic
            self.client.subscribe(f"{DEVICE_ID}/switch/set")
            print(f"✓ Subscribed to switch commands\n")
        else:
            print(f"✗ Failed to connect to MQTT broker, return code {rc}")
            self.connected = False
    
    def on_disconnect(self, client, userdata, rc):
        print("Disconnected from MQTT broker")
        self.connected = False
    
    def on_message(self, client, userdata, msg):
        """Handle incoming MQTT messages (switch commands)"""
        if msg.topic == f"{DEVICE_ID}/switch/set":
            payload = msg.payload.decode().upper()
            
            if payload == "ON":
                print(f"→ Turning inverter ON")
                send_query(self.serial_port, '0102040000')
            elif payload == "OFF":
                print(f"→ Turning inverter OFF")
                send_query(self.serial_port, '0102040100')
    
    def connect(self):
        try:
            self.client.connect(MQTT_BROKER, MQTT_PORT, 60)
            self.client.loop_start()
            
            # Wait for connection
            timeout = 10
            start = time.time()
            while not self.connected and (time.time() - start) < timeout:
                time.sleep(0.1)
            
            if not self.connected:
                print("✗ Failed to connect to MQTT broker within timeout")
                return False
            return True
        except Exception as e:
            print(f"✗ MQTT connection error: {e}")
            return False
    
    def publish_discovery(self):
        """Publish Home Assistant MQTT discovery messages"""
        # Sensor entities
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

            self.client.publish(topic, json.dumps(config), retain=True)

        # Publish switch discovery for ON/OFF control
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

                # Publish binary sensor for fan status
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
        self.client.publish(fan_topic, json.dumps(fan_config), retain=True)
        
        self.client.publish(switch_topic, json.dumps(switch_config), retain=True)
        print(f"✓ Published Home Assistant discovery messages")
        
        # Set initial availability to online
        self.publish_availability(True)
    
    def publish_state(self, data):
        """Publish sensor state data"""
        if not self.connected:
            return False
        
        topic = f"{DEVICE_ID}/state"
        payload = json.dumps(data)
        
        result = self.client.publish(topic, payload)
        return result.rc == mqtt.MQTT_ERR_SUCCESS
    
    def publish_availability(self, available):
        """Publish availability status"""
        if not self.connected:
            return False
        
        payload = "online" if available else "offline"
        result = self.client.publish(AVAILABILITY_TOPIC, payload, retain=True)
        return result.rc == mqtt.MQTT_ERR_SUCCESS
    
    def disconnect(self):
        self.client.loop_stop()
        self.client.disconnect()

def main():
    print("=" * 60)
    print("FCHAO Inverter to Home Assistant via MQTT")
    print("=" * 60)
    print(f"Serial: {SERIAL_PORT} @ {SERIAL_BAUDRATE} baud")
    print(f"MQTT: {MQTT_BROKER}:{MQTT_PORT}")
    print(f"Query Interval: {QUERY_INTERVAL}s")
    print("=" * 60)
    print()
    
    # Initialize MQTT
    # Note: We'll pass the serial port to MQTT publisher after opening it
    mqtt_pub = None
    
    print()
    
    # Open serial port
    try:
        ser = serial.Serial(
            port=SERIAL_PORT,
            baudrate=SERIAL_BAUDRATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1
        )
        print("✓ Serial port opened\n")
    except serial.SerialException as e:
        print(f"✗ Failed to open serial port: {e}")
        return
    
    # Now initialize MQTT with serial port reference
    mqtt_pub = MQTTPublisher(ser)
    if not mqtt_pub.connect():
        print("✗ Exiting due to MQTT connection failure")
        ser.close()
        return
    
    buffer = bytearray()
    last_query_time = 0
    last_response_time = time.time()
    was_available = True
    
    try:
        while True:
            # Send query at regular intervals
            current_time = time.time()
            if current_time - last_query_time >= QUERY_INTERVAL:
                send_query(ser)
                last_query_time = current_time
            
            # Check for timeout
            time_since_response = current_time - last_response_time
            if time_since_response > TIMEOUT_THRESHOLD:
                if was_available:
                    print(f"\n⚠ No response for {time_since_response:.0f}s - marking unavailable")
                    mqtt_pub.publish_availability(False)
                    was_available = False
            
            # Read responses
            if ser.in_waiting > 0:
                chunk = ser.read(ser.in_waiting)
                buffer.extend(chunk)
                
                # Look for complete packets (AE...EE)
                while len(buffer) >= 3:
                    start_idx = buffer.find(b'\xAE')
                    
                    if start_idx == -1:
                        buffer.clear()
                        break
                    
                    if start_idx > 0:
                        buffer = buffer[start_idx:]
                    
                    end_idx = buffer.find(b'\xEE', 1)
                    
                    if end_idx == -1:
                        if len(buffer) > 100:
                            buffer = buffer[1:]
                        break
                    
                    packet = buffer[:end_idx + 1]
                    buffer = buffer[end_idx + 1:]
                    
                    # Parse and publish if it's data packet
                    if len(packet) == 17:
                        data = parse_inverter_data(packet)
                        if data:
                            # Update last response time
                            last_response_time = time.time()

                            # Restore availability BEFORE publishing state
                            if not was_available:
                                print(f"✓ Inverter responding again - marking available\n")
                                mqtt_pub.publish_availability(True)
                                was_available = True

                            mqtt_pub.publish_state(data)
                    else:
                        # Only show non-data packets if they're not just noise
                        if len(packet) > 3:
                            print(f"  Non-data packet ({len(packet)} bytes): {packet.hex().upper()}")
            
            time.sleep(0.1)
    
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        mqtt_pub.publish_availability(False)
    except Exception as e:
        print(f"\nError: {e}")
        mqtt_pub.publish_availability(False)
    finally:
        ser.close()
        mqtt_pub.disconnect()
        print("Disconnected")

if __name__ == "__main__":
    main()