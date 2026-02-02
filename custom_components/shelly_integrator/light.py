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
from .entity_factory import EntityType, discover_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shelly Integrator lights."""
    coordinator: ShellyIntegratorCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Track which entities have been created (by unique_id)
    created_entities: set[str] = set()

    def _create_lights(device_id: str) -> list[ShellyIntegratorLight]:
        """Create light entities for a device based on its status."""
        entities: list[ShellyIntegratorLight] = []
        device_data = coordinator.devices.get(device_id, {})
        status = device_data.get("status", {})
        device_code = device_data.get("device_code", "")
        
        if not status:
            _LOGGER.debug("No status data for device %s, skipping light creation", device_id)
            return entities

        # Discover all possible entities
        discovered = discover_entities(status, device_code)
        
        # Filter for lights only
        for entity_def in discovered:
            if entity_def.entity_type != EntityType.LIGHT:
                continue
            
            unique_id = f"{device_id}_light_{entity_def.channel}"
            if unique_id in created_entities:
                continue
            
            created_entities.add(unique_id)
            entities.append(
                ShellyIntegratorLight(
                    coordinator=coordinator,
                    device_id=device_id,
                    channel=entity_def.channel,
                    key=entity_def.key,
                )
            )
        
        if entities:
            _LOGGER.info("Creating %d light entities for device %s", len(entities), device_id)
        
        return entities

    @callback
    def async_add_device(device_id: str) -> None:
        """Add entities for a newly discovered device."""
        entities = _create_lights(device_id)
        if entities:
            async_add_entities(entities)

    # Add existing devices
    entities: list[ShellyIntegratorLight] = []
    for device_id in list(coordinator.devices.keys()):
        entities.extend(_create_lights(device_id))

    if entities:
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
        key: str,
    ) -> None:
        """Initialize the light."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._channel = channel
        self._key = key  # e.g., "lights.0" or "light:0"
        self._is_gen2 = key.startswith("light:")
        
        self._attr_unique_id = f"{device_id}_light_{channel}"
        self._attr_name = "Light" if channel == 0 else f"Light {channel + 1}"

        # Determine supported color modes based on device capabilities
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}
        self._attr_color_mode = ColorMode.BRIGHTNESS

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        device_data = self.coordinator.devices.get(self._device_id, {})
        status = device_data.get("status", {})
        
        # Try to get friendly name
        name = device_data.get("name")
        if not name:
            sys_info = status.get("sys", {}).get("device", {})
            name = sys_info.get("name")
        if not name:
            settings = device_data.get("settings", {})
            name = settings.get("name")
        if not name:
            getinfo = status.get("getinfo", {}).get("fw_info", {})
            name = getinfo.get("device")
        
        if not name:
            device_code = device_data.get("device_code", "")
            short_id = self._device_id[-6:] if len(self._device_id) > 6 else self._device_id
            name = f"Shelly {device_code or ''} {short_id}".strip()

        model = device_data.get("device_code") or device_data.get("device_type") or "Unknown"

        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=name,
            manufacturer="Shelly",
            model=model,
        )

    @property
    def _light_data(self) -> dict[str, Any]:
        """Get current light data from coordinator."""
        device = self.coordinator.devices.get(self._device_id, {})
        status = device.get("status", {})

        if self._is_gen2:
            # Gen2 format: light:N
            return status.get(self._key, {})
        else:
            # Gen1 format: lights array
            lights = status.get("lights", [])
            if len(lights) > self._channel:
                return lights[self._channel]
        return {}

    @property
    def is_on(self) -> bool | None:
        """Return true if light is on."""
        data = self._light_data
        if self._is_gen2:
            return data.get("output", False)
        return data.get("ison", False)

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
        # Note: Shelly Integrator API doesn't support brightness in CommandRequest
        # For now, just turn on - brightness needs JRPC method
        await self.coordinator.send_command(
            device_id=self._device_id,
            cmd="light",
            channel=self._channel,
            action="on",
        )
        # Optimistic update
        self._update_local_state(True, None)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        await self.coordinator.send_command(
            device_id=self._device_id,
            cmd="light",
            channel=self._channel,
            action="off",
        )
        # Optimistic update
        self._update_local_state(False, None)

    def _update_local_state(self, is_on: bool, brightness: int | None) -> None:
        """Update local state optimistically."""
        device = self.coordinator.devices.get(self._device_id, {})
        status = device.get("status", {})

        if self._is_gen2:
            if self._key in status:
                status[self._key]["output"] = is_on
                if brightness is not None:
                    status[self._key]["brightness"] = brightness
        else:
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
