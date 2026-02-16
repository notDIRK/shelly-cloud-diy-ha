"""Light platform for Shelly Integrator."""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ShellyIntegratorCoordinator, SIGNAL_NEW_DEVICE
from .entities.base import ShellyBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shelly Integrator lights."""
    coordinator: ShellyIntegratorCoordinator = hass.data[DOMAIN][entry.entry_id]
    created_entities: set[str] = set()

    def create_lights(device_id: str) -> list[LightEntity]:
        """Create light entities for a device."""
        entities: list[LightEntity] = []
        device_data = coordinator.devices.get(device_id, {})
        status = device_data.get("status", {})

        if not status:
            return entities

        # Gen1: lights array
        for idx, _ in enumerate(status.get("lights", [])):
            unique_id = f"{device_id}_light_{idx}"
            if unique_id not in created_entities:
                created_entities.add(unique_id)
                entities.append(ShellyLight(
                    coordinator, device_id, idx, f"lights.{idx}", is_gen2=False
                ))

        # Gen2: light:N pattern
        for key in status:
            if match := re.match(r"light:(\d+)", key):
                idx = int(match.group(1))
                unique_id = f"{device_id}_light_{idx}"
                if unique_id not in created_entities:
                    created_entities.add(unique_id)
                    entities.append(ShellyLight(
                        coordinator, device_id, idx, key, is_gen2=True
                    ))

        if entities:
            _LOGGER.info("Created %d lights for %s", len(entities), device_id)

        return entities

    @callback
    def async_add_device(device_id: str) -> None:
        """Add entities for newly discovered device."""
        stale = [k for k in created_entities if k.startswith(device_id)]
        for k in stale:
            created_entities.discard(k)
        entities = create_lights(device_id)
        if entities:
            async_add_entities(entities)

    # Add existing devices
    entities: list[LightEntity] = []
    for device_id in list(coordinator.devices.keys()):
        entities.extend(create_lights(device_id))

    if entities:
        async_add_entities(entities)

    # Listen for new devices
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, async_add_device)
    )


class ShellyLight(ShellyBaseEntity, LightEntity):
    """Shelly light entity."""

    def __init__(
        self,
        coordinator: ShellyIntegratorCoordinator,
        device_id: str,
        channel: int,
        key: str,
        is_gen2: bool,
    ) -> None:
        """Initialize the light."""
        super().__init__(coordinator, device_id, channel)
        self._key = key
        self._is_gen2 = is_gen2
        self._attr_unique_id = f"{device_id}_light_{channel}"
        self._attr_name = "Light" if channel == 0 else f"Light {channel + 1}"
        self._attr_color_mode = ColorMode.BRIGHTNESS
        self._attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    @property
    def is_on(self) -> bool | None:
        """Return true if light is on."""
        status = self.device_status

        if self._is_gen2:
            return status.get(self._key, {}).get("output", False)
        else:
            lights = status.get("lights", [])
            if len(lights) > self._channel:
                return lights[self._channel].get("ison", False)

        return None

    @property
    def brightness(self) -> int | None:
        """Return brightness level (0-255)."""
        status = self.device_status

        if self._is_gen2:
            pct = status.get(self._key, {}).get("brightness", 0)
        else:
            lights = status.get("lights", [])
            if len(lights) > self._channel:
                pct = lights[self._channel].get("brightness", 0)
            else:
                pct = 0

        return int(pct * 255 / 100)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        response = await self._send_light_command(
            on=True, brightness=brightness,
        )
        if not self._is_command_ok(response):
            return
        self._update_local_state(True, brightness=brightness)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        response = await self._send_light_command(on=False)
        if not self._is_command_ok(response):
            return
        self._update_local_state(False)

    async def _send_light_command(
        self, on: bool, brightness: int | None = None
    ) -> dict | None:
        """Send the appropriate command for Gen1 or Gen2 light."""
        if self._is_gen2:
            params: dict[str, Any] = {"id": self._channel, "on": on}
            if brightness is not None:
                params["brightness"] = int(brightness * 100 / 255)
            return await self.coordinator.send_jrpc_command(
                device_id=self._device_id,
                method="Light.Set",
                params=params,
            )
        # Gen1: relay-style command with optional brightness param
        extra: dict[str, Any] | None = None
        if brightness is not None:
            extra = {"brightness": int(brightness * 100 / 255)}
        return await self.coordinator.send_command(
            device_id=self._device_id,
            cmd="light",
            channel=self._channel,
            action="on" if on else "off",
            params=extra,
        )

    @staticmethod
    def _is_command_ok(response: dict | None) -> bool:
        """Check if a command response indicates success."""
        if response is None:
            _LOGGER.warning("Light command failed: no response")
            return False

        # Check for JRPC error response (Gen2/Gen3)
        jrpc_response = response.get("response", {})
        if "error" in jrpc_response:
            error = jrpc_response.get("error")
            if error == "UNAUTHORIZED":
                _LOGGER.error(
                    "Light command UNAUTHORIZED - check logs for access "
                    "diagnostics. You may need to grant control "
                    "permissions at "
                    "https://my.shelly.cloud/integrator.html"
                )
            else:
                _LOGGER.error("Light JRPC error: %s", error)
            return False

        # Check for CommandResponse (Gen1)
        data = response.get("data", {})
        if isinstance(data, dict) and "isok" in data:
            if not data["isok"]:
                _LOGGER.error("Light command rejected: %s", data.get("res"))
                return False

        return True

    def _update_local_state(
        self, is_on: bool, brightness: int | None = None
    ) -> None:
        """Update local state optimistically."""
        status = self.device_status
        # Convert HA brightness (0-255) to Shelly percentage (0-100)
        pct = int(brightness * 100 / 255) if brightness is not None else None

        if self._is_gen2:
            if self._key in status:
                status[self._key]["output"] = is_on
                if pct is not None:
                    status[self._key]["brightness"] = pct
        else:
            lights = status.get("lights", [])
            if len(lights) > self._channel:
                lights[self._channel]["ison"] = is_on
                if pct is not None:
                    lights[self._channel]["brightness"] = pct

        self.async_write_ha_state()
