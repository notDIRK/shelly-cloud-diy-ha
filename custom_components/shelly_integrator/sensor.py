"""Sensor platform for Shelly Integrator."""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, is_gen2_status
from .coordinator import ShellyIntegratorCoordinator, SIGNAL_NEW_DEVICE
from .entities.base import ShellyBaseEntity
from .entities.descriptions import (
    BLOCK_SENSORS,
    RPC_SENSORS,
    BlockSensorDescription,
    RpcSensorDescription,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shelly Integrator sensors."""
    coordinator: ShellyIntegratorCoordinator = hass.data[DOMAIN][entry.entry_id]
    created_sensors: set[str] = set()

    def create_sensors(device_id: str) -> list[SensorEntity]:
        """Create sensor entities for a device."""
        entities: list[SensorEntity] = []
        device_data = coordinator.devices.get(device_id, {})
        status = device_data.get("status", {})

        if not status:
            return entities

        if is_gen2_status(status):
            entities.extend(_create_rpc_sensors(
                device_id, status, created_sensors, coordinator
            ))
        else:
            entities.extend(_create_block_sensors(
                device_id, status, created_sensors, coordinator
            ))

        if entities:
            _LOGGER.info("Created %d sensors for %s", len(entities), device_id)

        return entities

    @callback
    def async_add_device(device_id: str) -> None:
        """Add entities for newly discovered device."""
        entities = create_sensors(device_id)
        if entities:
            async_add_entities(entities)

    # Add existing devices
    entities: list[SensorEntity] = []
    for device_id in list(coordinator.devices.keys()):
        entities.extend(create_sensors(device_id))

    if entities:
        async_add_entities(entities)

    # Listen for new devices
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, async_add_device)
    )


def _create_block_sensors(
    device_id: str,
    status: dict[str, Any],
    created: set[str],
    coordinator: ShellyIntegratorCoordinator,
) -> list[SensorEntity]:
    """Create Gen1 Block sensors."""
    entities: list[SensorEntity] = []

    # Emeters
    for idx, emeter in enumerate(status.get("emeters", [])):
        for attr, key in [
            ("power", ("emeter", "power")),
            ("voltage", ("emeter", "voltage")),
            ("current", ("emeter", "current")),
            ("pf", ("emeter", "powerFactor")),
        ]:
            if attr in emeter and key in BLOCK_SENSORS:
                desc = BLOCK_SENSORS[key]
                uid = f"{device_id}_{desc.key}_{idx}"
                if uid not in created:
                    created.add(uid)
                    entities.append(BlockSensor(
                        coordinator, device_id, desc, idx, "emeters", attr
                    ))

        if "total" in emeter:
            desc = BLOCK_SENSORS.get(("emeter", "energy"))
            if desc:
                uid = f"{device_id}_{desc.key}_{idx}"
                if uid not in created:
                    created.add(uid)
                    entities.append(BlockSensor(
                        coordinator, device_id, desc, idx, "emeters", "total"
                    ))

    # Meters
    for idx, meter in enumerate(status.get("meters", [])):
        if "power" in meter:
            desc = BLOCK_SENSORS.get(("relay", "power"))
            if desc:
                uid = f"{device_id}_meter_power_{idx}"
                if uid not in created:
                    created.add(uid)
                    entities.append(BlockSensor(
                        coordinator, device_id, desc, idx, "meters", "power"
                    ))

    # Gas sensor
    gas = status.get("gas_sensor", {})
    if gas and "sensor_state" in gas:
        desc = BLOCK_SENSORS.get(("sensor", "sensorOp"))
        if desc:
            uid = f"{device_id}_gas_sensor_state"
            if uid not in created:
                created.add(uid)
                entities.append(BlockSensor(
                    coordinator, device_id, desc, 0, "gas_sensor", "sensor_state"
                ))

    # Concentration
    conc = status.get("concentration", {})
    if conc and conc.get("is_valid"):
        desc = BLOCK_SENSORS.get(("sensor", "concentration"))
        if desc:
            uid = f"{device_id}_gas_concentration"
            if uid not in created:
                created.add(uid)
                entities.append(BlockSensor(
                    coordinator, device_id, desc, 0, "concentration", "ppm"
                ))

    # Temperature
    temp = status.get("tmp", {}) or status.get("temperature", {})
    if temp and "tC" in temp:
        desc = BLOCK_SENSORS.get(("sensor", "temp"))
        if desc:
            uid = f"{device_id}_temperature"
            if uid not in created:
                created.add(uid)
                key = "tmp" if "tmp" in status else "temperature"
                entities.append(BlockSensor(
                    coordinator, device_id, desc, 0, key, "tC"
                ))

    return entities


def _create_rpc_sensors(
    device_id: str,
    status: dict[str, Any],
    created: set[str],
    coordinator: ShellyIntegratorCoordinator,
) -> list[SensorEntity]:
    """Create Gen2/Gen3 RPC sensors."""
    entities: list[SensorEntity] = []

    for key in status:
        if match := re.match(r"(switch|light|cover):(\d+)", key):
            component = match.group(1)
            idx = int(match.group(2))
            data = status[key]

            for attr, desc_key in [
                ("apower", "switch_power"),
                ("voltage", "switch_voltage"),
                ("current", "switch_current"),
            ]:
                if attr in data:
                    desc = RPC_SENSORS.get(desc_key)
                    if desc:
                        uid = f"{device_id}_{component}_{idx}_{attr}"
                        if uid not in created:
                            created.add(uid)
                            entities.append(RpcSensor(
                                coordinator, device_id, desc, idx, key, attr
                            ))

    # Temperature sensors
    for key in status:
        if match := re.match(r"temperature:(\d+)", key):
            idx = int(match.group(1))
            desc = RPC_SENSORS.get("temperature")
            if desc:
                uid = f"{device_id}_temperature_{idx}"
                if uid not in created:
                    created.add(uid)
                    entities.append(RpcSensor(
                        coordinator, device_id, desc, idx, key, "tC"
                    ))

    return entities


class BlockSensor(ShellyBaseEntity, SensorEntity):
    """Gen1 Block sensor."""

    def __init__(
        self,
        coordinator: ShellyIntegratorCoordinator,
        device_id: str,
        description: BlockSensorDescription,
        channel: int,
        status_key: str,
        attr_key: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, channel)
        self._description = description
        self._status_key = status_key
        self._attr_key = attr_key

        self._attr_unique_id = f"{device_id}_{description.key}_{channel}"
        name = description.name or "Sensor"
        self._attr_name = name if channel == 0 else f"{name} {channel + 1}"

        if description.device_class:
            self._attr_device_class = description.device_class
        if description.state_class:
            self._attr_state_class = description.state_class
        if description.native_unit_of_measurement:
            self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        if description.entity_category:
            self._attr_entity_category = description.entity_category
        if description.icon:
            self._attr_icon = description.icon
        if description.suggested_display_precision is not None:
            self._attr_suggested_display_precision = description.suggested_display_precision

    @property
    def native_value(self) -> float | int | str | None:
        """Return sensor value."""
        status = self.device_status
        container = status.get(self._status_key)

        if container is None:
            return None

        if isinstance(container, list):
            if self._channel >= len(container):
                return None
            container = container[self._channel]

        value = container.get(self._attr_key) if isinstance(container, dict) else None

        if value is not None and self._description.value_fn:
            value = self._description.value_fn(value)

        return value


class RpcSensor(ShellyBaseEntity, SensorEntity):
    """Gen2/Gen3 RPC sensor."""

    def __init__(
        self,
        coordinator: ShellyIntegratorCoordinator,
        device_id: str,
        description: RpcSensorDescription,
        channel: int,
        component_key: str,
        attr_key: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, channel)
        self._description = description
        self._component_key = component_key
        self._attr_key = attr_key

        self._attr_unique_id = f"{device_id}_{component_key}_{attr_key}"
        name = description.name or "Sensor"
        self._attr_name = name if channel == 0 else f"{name} {channel + 1}"

        if description.device_class:
            self._attr_device_class = description.device_class
        if description.state_class:
            self._attr_state_class = description.state_class
        if description.native_unit_of_measurement:
            self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        if description.entity_category:
            self._attr_entity_category = description.entity_category
        if description.icon:
            self._attr_icon = description.icon
        if description.suggested_display_precision is not None:
            self._attr_suggested_display_precision = description.suggested_display_precision

    @property
    def native_value(self) -> float | int | str | None:
        """Return sensor value."""
        component = self.device_status.get(self._component_key)
        if component is None:
            return None

        value = component.get(self._attr_key)

        if value is not None and self._description.value_fn:
            value = self._description.value_fn(value)

        return value
