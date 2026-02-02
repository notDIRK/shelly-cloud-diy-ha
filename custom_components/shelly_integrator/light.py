"""Light platform for Shelly Integrator."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
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
    """Set up Shelly Integrator lights."""
    coordinator: ShellyIntegratorCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Track which devices already have entities
    known_devices: set[str] = set()

    def _create_lights(device_id: str) -> list[ShellyIntegratorLight]:
        """Create light entities for a device."""
        entities = []
        device_data = coordinator.devices.get(device_id, {})
        status = device_data.get("status", {})
        lights = status.get("lights", [])

        for idx, light_data in enumerate(lights):
            entities.append(
                ShellyIntegratorLight(
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
        entities = _create_lights(device_id)
        if entities:
            async_add_entities(entities)

    # Add existing devices
    for device_id in coordinator.devices:
        known_devices.add(device_id)

    entities = []
    for device_id in known_devices:
        entities.extend(_create_lights(device_id))

    async_add_entities(entities)

    # Listen for new devices
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, async_add_device)
    )


class ShellyIntegratorLight(CoordinatorEntity[ShellyIntegratorCoordinator], LightEntity):
    """Representation of a Shelly light."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ShellyIntegratorCoordinator,
        device_id: str,
        channel: int,
    ) -> None:
        """Initialize the light."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._channel = channel
        self._attr_unique_id = f"{device_id}_light_{channel}"
        self._attr_name = f"Light {channel}"

        # Determine supported color modes based on device capabilities
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        self._attr_color_mode = ColorMode.BRIGHTNESS

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
    def _light_data(self) -> dict[str, Any]:
        """Get current light data from coordinator."""
        device = self.coordinator.devices.get(self._device_id, {})
        status = device.get("status", {})
        lights = status.get("lights", [])

        if len(lights) > self._channel:
            return lights[self._channel]
        return {}

    @property
    def is_on(self) -> bool | None:
        """Return true if light is on."""
        return self._light_data.get("ison", False)

    @property
    def brightness(self) -> int | None:
        """Return the brightness of the light (0-255)."""
        # Shelly uses 0-100, HA uses 0-255
        shelly_brightness = self._light_data.get("brightness", 0)
        return round(shelly_brightness * 255 / 100)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        device = self.coordinator.devices.get(self._device_id, {})
        return device.get("online", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        params: dict[str, Any] = {"id": self._channel, "on": True}

        if ATTR_BRIGHTNESS in kwargs:
            # Convert HA brightness (0-255) to Shelly (0-100)
            params["brightness"] = round(kwargs[ATTR_BRIGHTNESS] * 100 / 255)

        await self.coordinator.send_command(
            device_id=self._device_id,
            method="light",
            params=params,
        )
        # Optimistic update
        self._update_local_state(True, params.get("brightness"))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self.coordinator.send_command(
            device_id=self._device_id,
            method="light",
            params={"id": self._channel, "on": False},
        )
        # Optimistic update
        self._update_local_state(False, None)

    def _update_local_state(self, is_on: bool, brightness: int | None) -> None:
        """Update local state optimistically."""
        device = self.coordinator.devices.get(self._device_id, {})
        status = device.get("status", {})
        lights = status.get("lights", [])

        if len(lights) > self._channel:
            lights[self._channel]["ison"] = is_on
            if brightness is not None:
                lights[self._channel]["brightness"] = brightness
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
