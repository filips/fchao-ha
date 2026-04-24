import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import uart, sensor, binary_sensor, switch
from esphome.const import (
    CONF_ID,
    CONF_DEVICE_ID,
    DEVICE_CLASS_VOLTAGE,
    DEVICE_CLASS_POWER,
    DEVICE_CLASS_TEMPERATURE,
    STATE_CLASS_MEASUREMENT,
    UNIT_VOLT,
    UNIT_WATT,
    UNIT_CELSIUS,
)

CODEOWNERS = ["@fchao-ha"]
DEPENDENCIES = ["uart"]
AUTO_LOAD = ["sensor", "binary_sensor", "switch"]

CONF_VOLTAGE_OUT = "voltage_out"
CONF_VOLTAGE_IN = "voltage_in"
CONF_POWER = "power"
CONF_TEMPERATURE = "temperature"
CONF_FAN = "fan"
CONF_STATUS = "status"
CONF_OUTPUT_SWITCH = "output_switch"
CONF_RELAY_SWITCH = "relay_switch"

ENTITY_CONFIG_KEYS = (
    CONF_VOLTAGE_OUT,
    CONF_VOLTAGE_IN,
    CONF_POWER,
    CONF_TEMPERATURE,
    CONF_FAN,
    CONF_STATUS,
    CONF_OUTPUT_SWITCH,
)


def _device_id(value):
    from esphome.core.config import Device

    return cv.use_id(Device)(value)


def _inherit_device_id(config):
    if CONF_DEVICE_ID not in config:
        return config
    device_id = config[CONF_DEVICE_ID]
    for key in ENTITY_CONFIG_KEYS:
        entity = config.get(key)
        if isinstance(entity, dict):
            entity.setdefault(CONF_DEVICE_ID, device_id)
    return config


fchao_inverter_ns = cg.esphome_ns.namespace("fchao_inverter")
FchaoInverter = fchao_inverter_ns.class_(
    "FchaoInverter", cg.PollingComponent, uart.UARTDevice
)
FchaoInverterSwitch = fchao_inverter_ns.class_(
    "FchaoInverterSwitch", switch.Switch, cg.Parented.template(FchaoInverter)
)

CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(FchaoInverter),
            cv.Optional(CONF_DEVICE_ID): _device_id,
            cv.Optional(CONF_VOLTAGE_OUT): sensor.sensor_schema(
                unit_of_measurement=UNIT_VOLT,
                device_class=DEVICE_CLASS_VOLTAGE,
                state_class=STATE_CLASS_MEASUREMENT,
                accuracy_decimals=0,
                icon="mdi:current-ac",
            ),
            cv.Optional(CONF_VOLTAGE_IN): sensor.sensor_schema(
                unit_of_measurement=UNIT_VOLT,
                device_class=DEVICE_CLASS_VOLTAGE,
                state_class=STATE_CLASS_MEASUREMENT,
                accuracy_decimals=1,
                icon="mdi:current-dc",
            ),
            cv.Optional(CONF_POWER): sensor.sensor_schema(
                unit_of_measurement=UNIT_WATT,
                device_class=DEVICE_CLASS_POWER,
                state_class=STATE_CLASS_MEASUREMENT,
                accuracy_decimals=0,
                icon="mdi:lightning-bolt",
            ),
            cv.Optional(CONF_TEMPERATURE): sensor.sensor_schema(
                unit_of_measurement=UNIT_CELSIUS,
                device_class=DEVICE_CLASS_TEMPERATURE,
                state_class=STATE_CLASS_MEASUREMENT,
                accuracy_decimals=0,
                icon="mdi:thermometer",
            ),
            cv.Optional(CONF_FAN): binary_sensor.binary_sensor_schema(
                device_class="running",
                icon="mdi:fan",
            ),
            cv.Optional(CONF_STATUS): binary_sensor.binary_sensor_schema(
                device_class="power",
                icon="mdi:power",
            ),
            cv.Optional(CONF_OUTPUT_SWITCH): switch.switch_schema(
                FchaoInverterSwitch,
                icon="mdi:power",
            ),
            cv.Optional(CONF_RELAY_SWITCH): cv.use_id(switch.Switch),
        }
    )
    .extend(cv.polling_component_schema("1s"))
    .extend(uart.UART_DEVICE_SCHEMA),
    _inherit_device_id,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await uart.register_uart_device(var, config)

    if CONF_VOLTAGE_OUT in config:
        s = await sensor.new_sensor(config[CONF_VOLTAGE_OUT])
        cg.add(var.set_voltage_out_sensor(s))

    if CONF_VOLTAGE_IN in config:
        s = await sensor.new_sensor(config[CONF_VOLTAGE_IN])
        cg.add(var.set_voltage_in_sensor(s))

    if CONF_POWER in config:
        s = await sensor.new_sensor(config[CONF_POWER])
        cg.add(var.set_power_sensor(s))

    if CONF_TEMPERATURE in config:
        s = await sensor.new_sensor(config[CONF_TEMPERATURE])
        cg.add(var.set_temperature_sensor(s))

    if CONF_FAN in config:
        s = await binary_sensor.new_binary_sensor(config[CONF_FAN])
        cg.add(var.set_fan_binary_sensor(s))

    if CONF_STATUS in config:
        s = await binary_sensor.new_binary_sensor(config[CONF_STATUS])
        cg.add(var.set_status_binary_sensor(s))

    if CONF_OUTPUT_SWITCH in config:
        s = await switch.new_switch(config[CONF_OUTPUT_SWITCH])
        await cg.register_parented(s, config[CONF_ID])
        cg.add(var.set_output_switch(s))

    if CONF_RELAY_SWITCH in config:
        s = await cg.get_variable(config[CONF_RELAY_SWITCH])
        cg.add(var.set_relay_switch(s))
