"""Entity descriptions for Shelly Integrator.

Based on the official Home Assistant Shelly integration patterns.
See: https://github.com/home-assistant/core/blob/dev/homeassistant/components/shelly/sensor.py

Uses aioshelly library for device model definitions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Final

from aioshelly.const import MODEL_NAMES

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfReactivePower,
    UnitOfTemperature,
)


def get_model_name(device_code: str) -> str:
    """Get friendly model name from device code.
    
    Uses MODEL_NAMES from aioshelly library which contains all official
    Shelly device model codes and their friendly names.
    """
    return MODEL_NAMES.get(device_code, device_code)


# ============================================================================
# Sensor Descriptions (Gen1 Block devices)
# ============================================================================
@dataclass(frozen=True, kw_only=True)
class BlockSensorDescription:
    """Describe a Gen1 Block sensor."""
    
    key: str  # Status key path like "emeter|power" or "sensor|concentration"
    name: str | None = None
    native_unit_of_measurement: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    entity_category: EntityCategory | None = None
    icon: str | None = None
    suggested_display_precision: int | None = None
    value_fn: Callable[[Any], Any] | None = None  # Transform value
    available_fn: Callable[[dict], bool] | None = None  # Check availability


# Gen1 Block sensors - based on official HA implementation
BLOCK_SENSORS: Final[dict[tuple[str, str], BlockSensorDescription]] = {
    # Battery
    ("device", "battery"): BlockSensorDescription(
        key="device|battery",
        name="Battery",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Device temperature
    ("device", "deviceTemp"): BlockSensorDescription(
        key="device|deviceTemp",
        name="Device Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
    ),
    # Emeter sensors (Shelly EM, 3EM)
    ("emeter", "power"): BlockSensorDescription(
        key="emeter|power",
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ("emeter", "voltage"): BlockSensorDescription(
        key="emeter|voltage",
        name="Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ("emeter", "current"): BlockSensorDescription(
        key="emeter|current",
        name="Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ("emeter", "energy"): BlockSensorDescription(
        key="emeter|energy",
        name="Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
    ),
    ("emeter", "energyReturned"): BlockSensorDescription(
        key="emeter|energyReturned",
        name="Energy Returned",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
    ),
    ("emeter", "powerFactor"): BlockSensorDescription(
        key="emeter|powerFactor",
        name="Power Factor",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    ("emeter", "reactive"): BlockSensorDescription(
        key="emeter|reactive",
        name="Reactive Power",
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        device_class=SensorDeviceClass.REACTIVE_POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # Relay/device power and energy
    ("relay", "power"): BlockSensorDescription(
        key="relay|power",
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ("relay", "energy"): BlockSensorDescription(
        key="relay|energy",
        name="Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda value: value / 60,  # Convert Watt-minutes to Wh
        suggested_display_precision=2,
    ),
    ("device", "power"): BlockSensorDescription(
        key="device|power",
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ("device", "energy"): BlockSensorDescription(
        key="device|energy",
        name="Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda value: value / 60,  # Convert Watt-minutes to Wh
        suggested_display_precision=2,
    ),
    # Light power and energy
    ("light", "power"): BlockSensorDescription(
        key="light|power",
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ("light", "energy"): BlockSensorDescription(
        key="light|energy",
        name="Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda value: value / 60,
        suggested_display_precision=2,
    ),
    # Roller power and energy
    ("roller", "rollerPower"): BlockSensorDescription(
        key="roller|rollerPower",
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ("roller", "rollerEnergy"): BlockSensorDescription(
        key="roller|rollerEnergy",
        name="Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda value: value / 60,
        suggested_display_precision=2,
    ),
    # Gas sensor (Shelly Gas)
    ("sensor", "concentration"): BlockSensorDescription(
        key="sensor|concentration",
        name="Gas Concentration",
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:molecule",
    ),
    ("sensor", "sensorOp"): BlockSensorDescription(
        key="sensor|sensorOp",
        name="Sensor Operation",
        device_class=SensorDeviceClass.ENUM,
        icon="mdi:gas-cylinder",
    ),
    ("sensor", "gas"): BlockSensorDescription(
        key="sensor|gas",
        name="Gas Detected",
        device_class=SensorDeviceClass.ENUM,
        icon="mdi:alarm-light",
    ),
    ("sensor", "selfTest"): BlockSensorDescription(
        key="sensor|selfTest",
        name="Self Test",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Temperature sensors
    ("sensor", "temp"): BlockSensorDescription(
        key="sensor|temp",
        name="Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ("sensor", "extTemp"): BlockSensorDescription(
        key="sensor|extTemp",
        name="External Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    # Humidity sensor
    ("sensor", "humidity"): BlockSensorDescription(
        key="sensor|humidity",
        name="Humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    # Illuminance sensor
    ("sensor", "luminosity"): BlockSensorDescription(
        key="sensor|luminosity",
        name="Illuminance",
        native_unit_of_measurement="lx",
        device_class=SensorDeviceClass.ILLUMINANCE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # Tilt sensor
    ("sensor", "tilt"): BlockSensorDescription(
        key="sensor|tilt",
        name="Tilt",
        native_unit_of_measurement="°",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # Valve status
    ("valve", "valve"): BlockSensorDescription(
        key="valve|valve",
        name="Valve Status",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # ADC (Shelly UNI)
    ("adc", "adc"): BlockSensorDescription(
        key="adc|adc",
        name="ADC",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    # RSSI
    ("wifi_sta", "rssi"): BlockSensorDescription(
        key="wifi_sta|rssi",
        name="RSSI",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
}


# ============================================================================
# Sensor Descriptions (Gen2/Gen3 RPC devices)
# ============================================================================
@dataclass(frozen=True, kw_only=True)
class RpcSensorDescription:
    """Describe a Gen2/Gen3 RPC sensor."""
    
    key: str  # Component key like "switch", "em", "temperature"
    sub_key: str  # Status sub-key like "apower", "voltage", "tC"
    name: str | None = None
    native_unit_of_measurement: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    entity_category: EntityCategory | None = None
    icon: str | None = None
    suggested_display_precision: int | None = None
    value_fn: Callable[[Any], Any] | None = None


# Gen2/Gen3 RPC sensors - based on official HA implementation
RPC_SENSORS: Final[dict[str, RpcSensorDescription]] = {
    # Switch power/energy/voltage/current
    "switch_power": RpcSensorDescription(
        key="switch",
        sub_key="apower",
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "switch_voltage": RpcSensorDescription(
        key="switch",
        sub_key="voltage",
        name="Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    "switch_current": RpcSensorDescription(
        key="switch",
        sub_key="current",
        name="Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "switch_energy": RpcSensorDescription(
        key="switch",
        sub_key="aenergy",
        name="Energy",
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda status: status.get("total"),
        suggested_display_precision=2,
    ),
    "switch_temperature": RpcSensorDescription(
        key="switch",
        sub_key="temperature",
        name="Device Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.get("tC"),
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=1,
    ),
    # Temperature sensor
    "temperature": RpcSensorDescription(
        key="temperature",
        sub_key="tC",
        name="Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    # Humidity sensor
    "humidity": RpcSensorDescription(
        key="humidity",
        sub_key="rh",
        name="Humidity",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    # Illuminance
    "illuminance": RpcSensorDescription(
        key="illuminance",
        sub_key="lux",
        name="Illuminance",
        native_unit_of_measurement="lx",
        device_class=SensorDeviceClass.ILLUMINANCE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # Battery
    "battery": RpcSensorDescription(
        key="devicepower",
        sub_key="battery",
        name="Battery",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.get("percent"),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # RSSI
    "rssi": RpcSensorDescription(
        key="wifi",
        sub_key="rssi",
        name="RSSI",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
}


# ============================================================================
# Binary Sensor Descriptions
# ============================================================================
@dataclass(frozen=True, kw_only=True)
class BlockBinarySensorDescription:
    """Describe a Gen1 Block binary sensor."""
    
    key: str
    name: str | None = None
    device_class: BinarySensorDeviceClass | None = None
    entity_category: EntityCategory | None = None
    icon: str | None = None
    value_fn: Callable[[Any], bool] | None = None


BLOCK_BINARY_SENSORS: Final[dict[str, BlockBinarySensorDescription]] = {
    # Input
    "input": BlockBinarySensorDescription(
        key="input",
        name="Input",
        device_class=BinarySensorDeviceClass.POWER,
    ),
    # Motion
    "motion": BlockBinarySensorDescription(
        key="motion",
        name="Motion",
        device_class=BinarySensorDeviceClass.MOTION,
    ),
    # Door/Window
    "sensor_state": BlockBinarySensorDescription(
        key="sensor|state",
        name="Door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda value: value == "open",
    ),
    # Flood
    "flood": BlockBinarySensorDescription(
        key="flood",
        name="Flood",
        device_class=BinarySensorDeviceClass.MOISTURE,
    ),
    # Smoke
    "smoke": BlockBinarySensorDescription(
        key="smoke",
        name="Smoke",
        device_class=BinarySensorDeviceClass.SMOKE,
    ),
    # Gas alarm (binary version)
    "gas_alarm": BlockBinarySensorDescription(
        key="gas_sensor|alarm_state",
        name="Gas Alarm",
        device_class=BinarySensorDeviceClass.GAS,
        value_fn=lambda value: value not in ("none", "unknown"),
        icon="mdi:alarm-light",
    ),
    # Overtemperature
    "overtemperature": BlockBinarySensorDescription(
        key="overtemperature",
        name="Overtemperature",
        device_class=BinarySensorDeviceClass.HEAT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Overpower
    "overpower": BlockBinarySensorDescription(
        key="overpower",
        name="Overpower",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # Vibration
    "vibration": BlockBinarySensorDescription(
        key="vibration",
        name="Vibration",
        device_class=BinarySensorDeviceClass.VIBRATION,
    ),
}


@dataclass(frozen=True, kw_only=True)
class RpcBinarySensorDescription:
    """Describe a Gen2/Gen3 RPC binary sensor."""
    
    key: str
    sub_key: str
    name: str | None = None
    device_class: BinarySensorDeviceClass | None = None
    entity_category: EntityCategory | None = None


RPC_BINARY_SENSORS: Final[dict[str, RpcBinarySensorDescription]] = {
    "input": RpcBinarySensorDescription(
        key="input",
        sub_key="state",
        name="Input",
        device_class=BinarySensorDeviceClass.POWER,
    ),
    "cloud": RpcBinarySensorDescription(
        key="cloud",
        sub_key="connected",
        name="Cloud",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
}
