# FCHAO Off-grid inverter HA MQTT Bridge

This repository contains code to communicate with an FCHAO Off-grid inverter through the built-in RS485 port. I have personally used a 1500W model, but it should work with all of the 1200-3500W units in the same series.

Thanks to https://github.com/taste66/FCHAO-Inverter for reverse engineering the RS485 commands for data readout. I did some further experimentation to find two commands that can disable/enable the AC output.

It will expose the sensor readings to MQTT, along with the proper metadata required to get Home Assistant to display the entities.

## ESPHome component

An ESPHome external component is provided under [`esphome/components/fchao_inverter`](esphome/components/fchao_inverter) as an alternative to the MQTT bridge. It talks to the inverter over UART (TTL-level RS485 adapter) and exposes the same readings as native ESPHome entities that are auto-discovered by Home Assistant via the ESPHome API.

Minimal config snippet:

```yaml
external_components:
  - source:
      type: git
      url: https://github.com/filips/fchao-ha
      ref: master
    components: [fchao_inverter]

uart:
  tx_pin: GPIO17
  rx_pin: GPIO16
  baud_rate: 9600

fchao_inverter:
  update_interval: 1s
  voltage_out:
    name: "AC Output Voltage"
  voltage_in:
    name: "DC Input Voltage"
  power:
    name: "Output Power"
  temperature:
    name: "Temperature"
  fan:
    name: "Fan"
  status:
    name: "Status"
  output_switch:
    name: "AC Output"
```

### Options

- `update_interval` — polling interval for querying the inverter. Defaults to `1s`.
- `device_id` — optional sub-device ID. When set, all child entities are grouped under that sub-device in Home Assistant. Each entity can override this by setting its own `device_id`.
- `voltage_out` / `voltage_in` / `power` / `temperature` — sensors.
- `fan` / `status` — binary sensors.
- `output_switch` — switch that toggles the inverter's AC output via the RS485 command.
- `relay_switch` — optional reference to an external `switch` (e.g. a GPIO relay) that physically powers the inverter. When defined, the component treats the inverter as unavailable while the relay is off and avoids spamming the UART with queries.