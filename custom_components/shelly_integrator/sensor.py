"""Sensor platform for Shelly Integrator."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ShellyIntegratorCoordinator, SIGNAL_NEW_DEVICE

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shelly Integrator sensors."""
    coordinator: ShellyIntegratorCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Track which devices already have entities
    known_devices: set[str] = set()

    def _create_sensors(device_id: str) -> list[SensorEntity]:
        """Create sensor entities for a device."""
        entities: list[SensorEntity] = []
        device_data = coordinator.devices.get(device_id, {})
        status = device_data.get("status", {})
        meters = status.get("meters", [])

        for idx, _ in enumerate(meters):
            entities.append(
                ShellyPowerSensor(
                    coordinator=coordinator,
                    device_id=device_id,
                    channel=idx,
                )
            )
            entities.append(
                ShellyEnergySensor(
                    coordinator=coordinator,
                    device_id=device_id,
                    channel=idx,
                )
            )
        return entities

    @callback
    def async_add_device(device_id: str) -> None:
        """Add entities for a newly discovered device."""
        if device_id in known_devices:
            return

        known_devices.add(device_id)
        entities = _create_sensors(device_id)
        if entities:
            async_add_entities(entities)

    # Add existing devices
    for device_id in coordinator.devices:
        known_devices.add(device_id)

    entities: list[SensorEntity] = []
    for device_id in known_devices:
        entities.extend(_create_sensors(device_id))

    async_add_entities(entities)

    # Listen for new devices
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, async_add_device)
    )


class ShellySensorBase(CoordinatorEntity[ShellyIntegratorCoordinator], SensorEntity):
    """Base class for Shelly sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ShellyIntegratorCoordinator,
        device_id: str,
        channel: int,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._channel = channel

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        device_data = self.coordinator.devices.get(self._device_id, {})
        device_info = device_data.get("device_info", {})

        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=device_info.get("name", f"Shelly {self._device_id}"),
            manufacturer="Shelly",
            model=device_info.get("model", "Unknown"),
            sw_version=device_info.get("fw_version"),
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        device = self.coordinator.devices.get(self._device_id, {})
        return device.get("online", False)


class ShellyPowerSensor(ShellySensorBase):
    """Representation of a Shelly power sensor."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(
        self,
        coordinator: ShellyIntegratorCoordinator,
        device_id: str,
        channel: int,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, channel)
        self._attr_unique_id = f"{device_id}_power_{channel}"
        self._attr_name = f"Power {channel}"

    @property
    def native_value(self) -> float | None:
        """Return the sensor value."""
        device = self.coordinator.devices.get(self._device_id, {})
        status = device.get("status", {})
        meters = status.get("meters", [])

        if len(meters) > self._channel:
            return meters[self._channel].get("power")

        return None


class ShellyEnergySensor(ShellySensorBase):
    """Representation of a Shelly energy sensor."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.WATT_HOUR

    def __init__(
        self,
        coordinator: ShellyIntegratorCoordinator,
        device_id: str,
        channel: int,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, channel)
        self._attr_unique_id = f"{device_id}_energy_{channel}"
        self._attr_name = f"Energy {channel}"

    @property
    def native_value(self) -> float | None:
        """Return the sensor value."""
        device = self.coordinator.devices.get(self._device_id, {})
        status = device.get("status", {})
        meters = status.get("meters", [])

        if len(meters) > self._channel:
            # Convert to Wh (API returns Watt-minutes)
            total = meters[self._channel].get("total", 0)
            return total / 60.0

        return None
