"""Entity factory for auto-discovering entities from Shelly status data."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

_LOGGER = logging.getLogger(__name__)


class EntityType(Enum):
    """Entity types that can be created."""
    SWITCH = "switch"
    LIGHT = "light"
    COVER = "cover"
    BINARY_SENSOR = "binary_sensor"
    SENSOR = "sensor"


@dataclass
class EntityDefinition:
    """Definition of an entity to create."""
    entity_type: EntityType
    key: str  # Status key (e.g., "switch:0", "emeters.0")
    channel: int
    sensor_type: str | None = None  # For sensors: power, energy, voltage, temperature, etc.
    name_suffix: str | None = None  # e.g., "Power", "Temperature"
    device_class: str | None = None
    unit: str | None = None
    icon: str | None = None


def discover_entities(status: dict[str, Any], device_code: str = "") -> list[EntityDefinition]:
    """Discover all possible entities from device status data.
    
    Args:
        status: Device status dictionary from Shelly Cloud
        device_code: Device code like "SHEM", "SNSW-001X16EU", etc.
    
    Returns:
        List of EntityDefinition objects for entities that should be created
    """
    entities: list[EntityDefinition] = []
    
    # Gen2 devices - switch:N pattern
    for key, value in status.items():
        if match := re.match(r"switch:(\d+)", key):
            channel = int(match.group(1))
            entities.append(EntityDefinition(
                entity_type=EntityType.SWITCH,
                key=key,
                channel=channel,
            ))
            # Gen2 switches often have temperature
            if isinstance(value, dict) and "temperature" in value:
                entities.append(EntityDefinition(
                    entity_type=EntityType.SENSOR,
                    key=f"{key}.temperature",
                    channel=channel,
                    sensor_type="temperature",
                    name_suffix="Temperature",
                    device_class="temperature",
                    unit="°C",
                ))
    
    # Gen2 devices - light:N pattern
    for key, value in status.items():
        if match := re.match(r"light:(\d+)", key):
            channel = int(match.group(1))
            entities.append(EntityDefinition(
                entity_type=EntityType.LIGHT,
                key=key,
                channel=channel,
            ))
    
    # Gen2 devices - cover:N pattern (roller shutters)
    for key, value in status.items():
        if match := re.match(r"cover:(\d+)", key):
            channel = int(match.group(1))
            entities.append(EntityDefinition(
                entity_type=EntityType.COVER,
                key=key,
                channel=channel,
            ))
    
    # Gen2 devices - input:N pattern
    for key, value in status.items():
        if match := re.match(r"input:(\d+)", key):
            channel = int(match.group(1))
            entities.append(EntityDefinition(
                entity_type=EntityType.BINARY_SENSOR,
                key=key,
                channel=channel,
                name_suffix="Input",
                device_class="power",  # Generic, could be button
            ))
    
    # Gen1 devices - relays array
    relays = status.get("relays", [])
    for idx, relay in enumerate(relays):
        # Check if it's a light or switch based on device code
        is_light = device_code in ("SHBDUO-1", "SHVIN-1", "SHCB-1", "SHDM-1", "SHDM-2")
        entities.append(EntityDefinition(
            entity_type=EntityType.LIGHT if is_light else EntityType.SWITCH,
            key=f"relays.{idx}",
            channel=idx,
        ))
    
    # Gen1 devices - lights array (dimmers, bulbs)
    lights = status.get("lights", [])
    for idx, light in enumerate(lights):
        entities.append(EntityDefinition(
            entity_type=EntityType.LIGHT,
            key=f"lights.{idx}",
            channel=idx,
        ))
    
    # Gen1 devices - rollers array
    rollers = status.get("rollers", [])
    for idx, roller in enumerate(rollers):
        entities.append(EntityDefinition(
            entity_type=EntityType.COVER,
            key=f"rollers.{idx}",
            channel=idx,
        ))
    
    # Gen1 devices - meters array (power monitoring)
    meters = status.get("meters", [])
    for idx, meter in enumerate(meters):
        entities.append(EntityDefinition(
            entity_type=EntityType.SENSOR,
            key=f"meters.{idx}.power",
            channel=idx,
            sensor_type="power",
            name_suffix="Power" if len(meters) == 1 else f"Power {idx + 1}",
            device_class="power",
            unit="W",
        ))
        if "total" in meter:
            entities.append(EntityDefinition(
                entity_type=EntityType.SENSOR,
                key=f"meters.{idx}.total",
                channel=idx,
                sensor_type="energy",
                name_suffix="Energy" if len(meters) == 1 else f"Energy {idx + 1}",
                device_class="energy",
                unit="Wh",
            ))
    
    # Gen1 devices - emeters array (energy meters like Shelly EM)
    emeters = status.get("emeters", [])
    for idx, emeter in enumerate(emeters):
        suffix = "" if len(emeters) == 1 else f" {idx + 1}"
        entities.append(EntityDefinition(
            entity_type=EntityType.SENSOR,
            key=f"emeters.{idx}.power",
            channel=idx,
            sensor_type="power",
            name_suffix=f"Power{suffix}",
            device_class="power",
            unit="W",
        ))
        entities.append(EntityDefinition(
            entity_type=EntityType.SENSOR,
            key=f"emeters.{idx}.voltage",
            channel=idx,
            sensor_type="voltage",
            name_suffix=f"Voltage{suffix}",
            device_class="voltage",
            unit="V",
        ))
        entities.append(EntityDefinition(
            entity_type=EntityType.SENSOR,
            key=f"emeters.{idx}.total",
            channel=idx,
            sensor_type="energy",
            name_suffix=f"Energy{suffix}",
            device_class="energy",
            unit="Wh",
        ))
        if "reactive" in emeter:
            entities.append(EntityDefinition(
                entity_type=EntityType.SENSOR,
                key=f"emeters.{idx}.reactive",
                channel=idx,
                sensor_type="reactive_power",
                name_suffix=f"Reactive Power{suffix}",
                device_class="reactive_power",
                unit="var",
            ))
        if "pf" in emeter:
            entities.append(EntityDefinition(
                entity_type=EntityType.SENSOR,
                key=f"emeters.{idx}.pf",
                channel=idx,
                sensor_type="power_factor",
                name_suffix=f"Power Factor{suffix}",
                device_class="power_factor",
                unit=None,
            ))
        if "current" in emeter:
            entities.append(EntityDefinition(
                entity_type=EntityType.SENSOR,
                key=f"emeters.{idx}.current",
                channel=idx,
                sensor_type="current",
                name_suffix=f"Current{suffix}",
                device_class="current",
                unit="A",
            ))
        if "total_returned" in emeter:
            entities.append(EntityDefinition(
                entity_type=EntityType.SENSOR,
                key=f"emeters.{idx}.total_returned",
                channel=idx,
                sensor_type="energy_returned",
                name_suffix=f"Energy Returned{suffix}",
                device_class="energy",
                unit="Wh",
            ))
    
    # Gas sensor (Shelly Gas)
    if "gas_sensor" in status:
        gas = status["gas_sensor"]
        entities.append(EntityDefinition(
            entity_type=EntityType.SENSOR,
            key="gas_sensor.sensor_state",
            channel=0,
            sensor_type="gas_state",
            name_suffix="Gas State",
            icon="mdi:gas-cylinder",
        ))
        entities.append(EntityDefinition(
            entity_type=EntityType.SENSOR,
            key="gas_sensor.alarm_state",
            channel=0,
            sensor_type="gas_alarm",
            name_suffix="Gas Alarm",
            icon="mdi:alarm-light",
        ))
    
    if "concentration" in status:
        entities.append(EntityDefinition(
            entity_type=EntityType.SENSOR,
            key="concentration.ppm",
            channel=0,
            sensor_type="gas_concentration",
            name_suffix="Gas Concentration",
            device_class="gas",
            unit="ppm",
            icon="mdi:molecule",
        ))
    
    # Temperature sensors (various devices)
    if "temperature" in status:
        temp = status["temperature"]
        if isinstance(temp, dict) and "tC" in temp:
            entities.append(EntityDefinition(
                entity_type=EntityType.SENSOR,
                key="temperature.tC",
                channel=0,
                sensor_type="temperature",
                name_suffix="Temperature",
                device_class="temperature",
                unit="°C",
            ))
    
    # External temperature sensors (Shelly 1PM, Shelly Uni, etc.)
    ext_temps = status.get("ext_temperature", {})
    for idx, temp in ext_temps.items():
        if isinstance(temp, dict) and "tC" in temp:
            entities.append(EntityDefinition(
                entity_type=EntityType.SENSOR,
                key=f"ext_temperature.{idx}.tC",
                channel=int(idx),
                sensor_type="temperature",
                name_suffix=f"External Temperature {int(idx) + 1}",
                device_class="temperature",
                unit="°C",
            ))
    
    # Humidity sensors (Shelly H&T)
    if "humidity" in status:
        hum = status["humidity"]
        if isinstance(hum, dict) and "value" in hum:
            entities.append(EntityDefinition(
                entity_type=EntityType.SENSOR,
                key="humidity.value",
                channel=0,
                sensor_type="humidity",
                name_suffix="Humidity",
                device_class="humidity",
                unit="%",
            ))
    
    # Battery (Shelly H&T, Door/Window, etc.)
    if "bat" in status:
        bat = status["bat"]
        if isinstance(bat, dict) and "value" in bat:
            entities.append(EntityDefinition(
                entity_type=EntityType.SENSOR,
                key="bat.value",
                channel=0,
                sensor_type="battery",
                name_suffix="Battery",
                device_class="battery",
                unit="%",
            ))
    
    # Illuminance (Shelly Motion)
    if "lux" in status:
        lux = status["lux"]
        if isinstance(lux, dict) and "value" in lux:
            entities.append(EntityDefinition(
                entity_type=EntityType.SENSOR,
                key="lux.value",
                channel=0,
                sensor_type="illuminance",
                name_suffix="Illuminance",
                device_class="illuminance",
                unit="lx",
            ))
    
    # Motion sensor (Shelly Motion)
    if "motion" in status:
        entities.append(EntityDefinition(
            entity_type=EntityType.BINARY_SENSOR,
            key="motion",
            channel=0,
            name_suffix="Motion",
            device_class="motion",
        ))
    
    # Door/Window sensor
    if "sensor" in status:
        sensor = status["sensor"]
        if isinstance(sensor, dict) and "state" in sensor:
            entities.append(EntityDefinition(
                entity_type=EntityType.BINARY_SENSOR,
                key="sensor.state",
                channel=0,
                name_suffix="Door",
                device_class="door",
            ))
    
    # Flood sensor (Shelly Flood)
    if "flood" in status:
        entities.append(EntityDefinition(
            entity_type=EntityType.BINARY_SENSOR,
            key="flood",
            channel=0,
            name_suffix="Flood",
            device_class="moisture",
        ))
    
    # Smoke sensor (Shelly Smoke)
    if "smoke" in status:
        entities.append(EntityDefinition(
            entity_type=EntityType.BINARY_SENSOR,
            key="smoke",
            channel=0,
            name_suffix="Smoke",
            device_class="smoke",
        ))
    
    # Inputs (Gen1 - button presses, etc.)
    inputs = status.get("inputs", [])
    for idx, inp in enumerate(inputs):
        entities.append(EntityDefinition(
            entity_type=EntityType.BINARY_SENSOR,
            key=f"inputs.{idx}.input",
            channel=idx,
            name_suffix=f"Input {idx + 1}" if len(inputs) > 1 else "Input",
            device_class="power",
        ))
    
    # Overtemperature / Overpower (protection sensors)
    if "overtemperature" in status:
        entities.append(EntityDefinition(
            entity_type=EntityType.BINARY_SENSOR,
            key="overtemperature",
            channel=0,
            name_suffix="Overtemperature",
            device_class="heat",
        ))
    
    if "overpower" in status:
        entities.append(EntityDefinition(
            entity_type=EntityType.BINARY_SENSOR,
            key="overpower",
            channel=0,
            name_suffix="Overpower",
            device_class="problem",
        ))
    
    _LOGGER.debug("Discovered %d entities from status", len(entities))
    return entities


def get_status_value(status: dict[str, Any], key: str) -> Any:
    """Get a value from status using dot notation key.
    
    Args:
        status: Device status dictionary
        key: Key path like "emeters.0.power" or "switch:0.output"
    
    Returns:
        The value at the key path, or None if not found
    """
    parts = key.split(".")
    value = status
    
    for part in parts:
        if value is None:
            return None
        
        # Handle array index
        if part.isdigit():
            idx = int(part)
            if isinstance(value, list) and idx < len(value):
                value = value[idx]
            else:
                return None
        # Handle dict key (including "switch:0" style keys)
        elif isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    
    return value
