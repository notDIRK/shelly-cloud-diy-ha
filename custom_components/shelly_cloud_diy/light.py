"""Light platform for Shelly Cloud DIY."""
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
from .coordinator import ShellyCloudCoordinator, SIGNAL_NEW_DEVICE
from .entities.base import ShellyBaseEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shelly Cloud DIY lights."""
    coordinator: ShellyCloudCoordinator = hass.data[DOMAIN][entry.entry_id]
    created_entities: set[str] = set()

    def create_lights(device_id: str) -> list[LightEntity]:
        """Create light entities for a device."""
        entities: list[LightEntity] = []
        if not coordinator.is_enabled(device_id):
            return entities
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
        coordinator: ShellyCloudCoordinator,
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

    def _component(self) -> dict[str, Any]:
        """Return this channel's raw status component (Gen1 or Gen2)."""
        status = self.device_status
        if self._is_gen2:
            return status.get(self._key, {})
        lights = status.get("lights", [])
        if len(lights) > self._channel:
            return lights[self._channel]
        return {}

    @property
    def is_on(self) -> bool | None:
        """Return true if light is on."""
        component = self._apply_optimistic(self._component())
        if not component:
            return None
        key = "output" if self._is_gen2 else "ison"
        return component.get(key, False)

    @property
    def brightness(self) -> int | None:
        """Return brightness level (0-255)."""
        component = self._apply_optimistic(self._component())
        if not component:
            return None
        if component.get("mode") == "color":
            # RGBW2 color mode has no `brightness` field. The visible level
            # is the white LED (0-255, same scale as HA) when the channel is
            # white-dominant, otherwise the RGB `gain` (0-100). (#6)
            if component.get("white", 0) > self._rgb_max(component):
                return component.get("white", 0)
            return int(component.get("gain", 0) * 255 / 100)
        return int(component.get("brightness", 0) * 255 / 100)

    @staticmethod
    def _rgb_max(component: dict[str, Any]) -> int:
        """Largest of the RGB channels (0-255), for white-vs-colour dominance."""
        return max(
            component.get("red", 0),
            component.get("green", 0),
            component.get("blue", 0),
        )

    def _brightness_fields(self, brightness: int) -> dict[str, Any]:
        """Map an HA brightness (0-255) to the Shelly field(s) that carry it.

        RGBW2 color mode drives the white LED (0-255, same scale as HA) when
        the channel is white-dominant, else the RGB `gain` (0-100). White /
        Gen2 channels use plain `brightness` (0-100). Choosing the field from
        the live channel state is what makes a white-driven RGBW2 actually
        dim instead of only ever touching an inactive channel. (#6)
        """
        component = self._component()
        if component.get("mode") == "color":
            if component.get("white", 0) > self._rgb_max(component):
                return {"white": brightness}
            return {"gain": int(brightness * 100 / 255)}
        return {"brightness": int(brightness * 100 / 255)}

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
        """Send the appropriate command for Gen1 or Gen2 light.

        NOTE: When using Shelly Cloud Integrator API, even Gen2 devices
        use CommandRequest (cmd: "light") format, not JrpcRequest.
        """
        # Build a single action dict. The coordinator's _light_kwargs()
        # maps {"on": ...} -> turn and passes the brightness field(s)
        # through; there is no separate `params` argument. The right field
        # (white / gain / brightness) is chosen from the channel state. (#6)
        action: dict[str, Any] = {"on": on}
        if brightness is not None:
            action.update(self._brightness_fields(brightness))
        return await self.coordinator.send_command(
            device_id=self._device_id,
            cmd="light",
            channel=self._channel,
            action=action,
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
        """Record the commanded state optimistically until the cloud confirms."""
        key = "output" if self._is_gen2 else "ison"
        values: dict[str, Any] = {key: is_on}
        if brightness is not None:
            # Same field mapping the command used, so the optimistic overlay
            # and the brightness reader agree on white/gain/brightness. (#6)
            values.update(self._brightness_fields(brightness))
        self._set_optimistic(values)
