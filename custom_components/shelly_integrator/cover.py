"""Cover platform for Shelly Integrator."""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
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
    """Set up Shelly Integrator covers."""
    coordinator: ShellyIntegratorCoordinator = hass.data[DOMAIN][entry.entry_id]
    created_entities: set[str] = set()

    def create_covers(device_id: str) -> list[CoverEntity]:
        """Create cover entities for a device."""
        entities: list[CoverEntity] = []
        device_data = coordinator.devices.get(device_id, {})
        status = device_data.get("status", {})

        if not status:
            return entities

        # Gen1: rollers array
        for idx, _ in enumerate(status.get("rollers", [])):
            uid = f"{device_id}_cover_{idx}"
            if uid not in created_entities:
                created_entities.add(uid)
                entities.append(ShellyCover(
                    coordinator, device_id, idx, f"rollers.{idx}", is_gen2=False
                ))

        # Gen2: cover:N pattern
        for key in status:
            if match := re.match(r"cover:(\d+)", key):
                idx = int(match.group(1))
                uid = f"{device_id}_cover_{idx}"
                if uid not in created_entities:
                    created_entities.add(uid)
                    entities.append(ShellyCover(
                        coordinator, device_id, idx, key, is_gen2=True
                    ))

        if entities:
            _LOGGER.info("Created %d covers for %s", len(entities), device_id)

        return entities

    @callback
    def async_add_device(device_id: str) -> None:
        """Add entities for newly discovered device."""
        stale = [k for k in created_entities if k.startswith(device_id)]
        for k in stale:
            created_entities.discard(k)
        entities = create_covers(device_id)
        if entities:
            async_add_entities(entities)

    # Add existing devices
    entities: list[CoverEntity] = []
    for device_id in list(coordinator.devices.keys()):
        entities.extend(create_covers(device_id))

    if entities:
        async_add_entities(entities)

    # Listen for new devices
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, async_add_device)
    )


class ShellyCover(ShellyBaseEntity, CoverEntity):
    """Shelly cover entity."""

    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    def __init__(
        self,
        coordinator: ShellyIntegratorCoordinator,
        device_id: str,
        channel: int,
        key: str,
        is_gen2: bool,
    ) -> None:
        """Initialize the cover."""
        super().__init__(coordinator, device_id, channel)
        self._key = key
        self._is_gen2 = is_gen2
        self._attr_unique_id = f"{device_id}_cover_{channel}"
        self._attr_name = "Cover" if channel == 0 else f"Cover {channel + 1}"

    @property
    def current_cover_position(self) -> int | None:
        """Return current position (0-100, 100 is fully open)."""
        status = self.device_status

        if self._is_gen2:
            return status.get(self._key, {}).get("current_pos")
        else:
            rollers = status.get("rollers", [])
            if len(rollers) > self._channel:
                return rollers[self._channel].get("current_pos")

        return None

    @property
    def is_closed(self) -> bool | None:
        """Return if cover is closed."""
        position = self.current_cover_position
        if position is not None:
            return position == 0
        return None

    @property
    def is_opening(self) -> bool:
        """Return if cover is opening."""
        status = self.device_status

        if self._is_gen2:
            return status.get(self._key, {}).get("state") == "opening"
        else:
            rollers = status.get("rollers", [])
            if len(rollers) > self._channel:
                return rollers[self._channel].get("state") == "open"
        return False

    @property
    def is_closing(self) -> bool:
        """Return if cover is closing."""
        status = self.device_status

        if self._is_gen2:
            return status.get(self._key, {}).get("state") == "closing"
        else:
            rollers = status.get("rollers", [])
            if len(rollers) > self._channel:
                return rollers[self._channel].get("state") == "close"
        return False

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        if self._is_gen2:
            response = await self.coordinator.send_jrpc_command(
                device_id=self._device_id,
                method="Cover.Open",
                params={"id": self._channel},
            )
            self._check_response(response, "open")
        else:
            await self.coordinator.send_command(
                device_id=self._device_id,
                cmd="roller",
                channel=self._channel,
                action="open",
            )

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        if self._is_gen2:
            response = await self.coordinator.send_jrpc_command(
                device_id=self._device_id,
                method="Cover.Close",
                params={"id": self._channel},
            )
            self._check_response(response, "close")
        else:
            await self.coordinator.send_command(
                device_id=self._device_id,
                cmd="roller",
                channel=self._channel,
                action="close",
            )

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        if self._is_gen2:
            response = await self.coordinator.send_jrpc_command(
                device_id=self._device_id,
                method="Cover.Stop",
                params={"id": self._channel},
            )
            self._check_response(response, "stop")
        else:
            await self.coordinator.send_command(
                device_id=self._device_id,
                cmd="roller",
                channel=self._channel,
                action="stop",
            )

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move cover to position."""
        position = kwargs.get("position")
        if position is None:
            return
        if self._is_gen2:
            response = await self.coordinator.send_jrpc_command(
                device_id=self._device_id,
                method="Cover.GoToPosition",
                params={"id": self._channel, "pos": position},
            )
            self._check_response(response, f"set position to {position}")
        else:
            # API uses dedicated "roller_to_pos" command for positioning
            await self.coordinator.send_command(
                device_id=self._device_id,
                cmd="roller_to_pos",
                channel=self._channel,
                action="to_pos",
                params={"pos": position},
            )

    def _check_response(self, response: dict | None, action: str) -> None:
        """Check JRPC response for errors and log if needed."""
        if response is None:
            _LOGGER.warning("Cover %s failed: no response", action)
            return

        # Check for JRPC error response
        jrpc_response = response.get("response", {})
        if "error" in jrpc_response:
            error = jrpc_response.get("error")
            if error == "UNAUTHORIZED":
                _LOGGER.error(
                    "Cover %s UNAUTHORIZED - check logs for access "
                    "diagnostics. You may need to grant control "
                    "permissions at "
                    "https://my.shelly.cloud/integrator.html",
                    action
                )
            else:
                _LOGGER.error("Cover %s JRPC error: %s", action, error)
