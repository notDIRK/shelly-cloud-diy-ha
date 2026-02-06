"""Binary sensor platform for Shelly Integrator."""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, is_gen2_status
from .coordinator import ShellyIntegratorCoordinator, SIGNAL_NEW_DEVICE
from .entities.base import ShellyBaseEntity
from .entities.descriptions import (
    BLOCK_BINARY_SENSORS,
    RPC_BINARY_SENSORS,
    BlockBinarySensorDescription,
    RpcBinarySensorDescription,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shelly Integrator binary sensors."""
    coordinator: ShellyIntegratorCoordinator = hass.data[DOMAIN][entry.entry_id]
    created_entities: set[str] = set()

    def create_binary_sensors(device_id: str) -> list[BinarySensorEntity]:
        """Create binary sensor entities for a device."""
        entities: list[BinarySensorEntity] = []
        device_data = coordinator.devices.get(device_id, {})
        status = device_data.get("status", {})

        if not status:
            return entities

        if is_gen2_status(status):
            entities.extend(_create_rpc_sensors(
                device_id, status, created_entities, coordinator
            ))
        else:
            entities.extend(_create_block_sensors(
                device_id, status, created_entities, coordinator
            ))

        if entities:
            _LOGGER.info("Created %d binary sensors for %s", len(entities), device_id)

        return entities

    @callback
    def async_add_device(device_id: str) -> None:
        """Add entities for newly discovered device."""
        entities = create_binary_sensors(device_id)
        if entities:
            async_add_entities(entities)

    # Add existing devices
    entities: list[BinarySensorEntity] = []
    for device_id in list(coordinator.devices.keys()):
        entities.extend(create_binary_sensors(device_id))

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
) -> list[BinarySensorEntity]:
    """Create Gen1 Block binary sensors."""
    entities: list[BinarySensorEntity] = []

    # Inputs
    for idx, inp in enumerate(status.get("inputs", [])):
        if "input" in inp:
            desc = BLOCK_BINARY_SENSORS.get("input")
            if desc:
                uid = f"{device_id}_input_{idx}"
                if uid not in created:
                    created.add(uid)
                    entities.append(BlockBinarySensor(
                        coordinator, device_id, desc, idx, "inputs", "input"
                    ))

    # Motion
    if "motion" in status:
        desc = BLOCK_BINARY_SENSORS.get("motion")
        if desc:
            uid = f"{device_id}_motion"
            if uid not in created:
                created.add(uid)
                entities.append(BlockBinarySensor(
                    coordinator, device_id, desc, 0, None, "motion"
                ))

    # Door/Window
    sensor = status.get("sensor", {})
    if sensor and "state" in sensor:
        desc = BLOCK_BINARY_SENSORS.get("sensor_state")
        if desc:
            uid = f"{device_id}_door"
            if uid not in created:
                created.add(uid)
                entities.append(BlockBinarySensor(
                    coordinator, device_id, desc, 0, "sensor", "state"
                ))

    # Gas alarm
    gas = status.get("gas_sensor", {})
    if gas and "alarm_state" in gas:
        desc = BLOCK_BINARY_SENSORS.get("gas_alarm")
        if desc:
            uid = f"{device_id}_gas_alarm"
            if uid not in created:
                created.add(uid)
                entities.append(BlockBinarySensor(
                    coordinator, device_id, desc, 0, "gas_sensor", "alarm_state"
                ))

    return entities


def _create_rpc_sensors(
    device_id: str,
    status: dict[str, Any],
    created: set[str],
    coordinator: ShellyIntegratorCoordinator,
) -> list[BinarySensorEntity]:
    """Create Gen2/Gen3 RPC binary sensors."""
    entities: list[BinarySensorEntity] = []

    # Inputs
    for key in status:
        if match := re.match(r"input:(\d+)", key):
            idx = int(match.group(1))
            if "state" in status[key]:
                desc = RPC_BINARY_SENSORS.get("input")
                if desc:
                    uid = f"{device_id}_input_{idx}"
                    if uid not in created:
                        created.add(uid)
                        entities.append(RpcBinarySensor(
                            coordinator, device_id, desc, idx, key, "state"
                        ))

    # Cloud
    if "connected" in status.get("cloud", {}):
        desc = RPC_BINARY_SENSORS.get("cloud")
        if desc:
            uid = f"{device_id}_cloud"
            if uid not in created:
                created.add(uid)
                entities.append(RpcBinarySensor(
                    coordinator, device_id, desc, 0, "cloud", "connected"
                ))

    return entities


class BlockBinarySensor(ShellyBaseEntity, BinarySensorEntity):
    """Gen1 Block binary sensor."""

    def __init__(
        self,
        coordinator: ShellyIntegratorCoordinator,
        device_id: str,
        description: BlockBinarySensorDescription,
        channel: int,
        status_key: str | None,
        attr_key: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, channel)
        self._description = description
        self._status_key = status_key
        self._attr_key = attr_key

        self._attr_unique_id = f"{device_id}_{description.key}_{channel}"
        name = description.name or "Binary Sensor"
        self._attr_name = name if channel == 0 else f"{name} {channel + 1}"

        if description.device_class:
            self._attr_device_class = description.device_class
        if description.entity_category:
            self._attr_entity_category = description.entity_category
        if description.icon:
            self._attr_icon = description.icon

    @property
    def is_on(self) -> bool | None:
        """Return true if sensor is on."""
        status = self.device_status

        if self._status_key is None:
            value = status.get(self._attr_key)
        else:
            container = status.get(self._status_key)
            if container is None:
                return None
            if isinstance(container, list):
                if self._channel >= len(container):
                    return None
                container = container[self._channel]
            value = container.get(self._attr_key) if isinstance(container, dict) else None

        if value is None:
            return None

        if self._description.value_fn:
            return self._description.value_fn(value)

        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value > 0

        return None


class RpcBinarySensor(ShellyBaseEntity, BinarySensorEntity):
    """Gen2/Gen3 RPC binary sensor."""

    def __init__(
        self,
        coordinator: ShellyIntegratorCoordinator,
        device_id: str,
        description: RpcBinarySensorDescription,
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
        name = description.name or "Binary Sensor"
        self._attr_name = name if channel == 0 else f"{name} {channel + 1}"

        if description.device_class:
            self._attr_device_class = description.device_class
        if description.entity_category:
            self._attr_entity_category = description.entity_category

    @property
    def is_on(self) -> bool | None:
        """Return true if sensor is on."""
        component = self.device_status.get(self._component_key)
        if component is None:
            return None

        value = component.get(self._attr_key)
        if value is None:
            return None

        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value > 0

        return None
