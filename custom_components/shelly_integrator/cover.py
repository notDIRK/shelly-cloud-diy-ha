"""Cover platform for Shelly Integrator."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
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
    """Set up Shelly Integrator covers."""
    coordinator: ShellyIntegratorCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Track which entities have been created (by unique_id)
    created_entities: set[str] = set()

    def _create_covers(device_id: str) -> list[ShellyIntegratorCover]:
        """Create cover entities for a device based on its status."""
        entities: list[ShellyIntegratorCover] = []
        device_data = coordinator.devices.get(device_id, {})
        status = device_data.get("status", {})
        device_code = device_data.get("device_code", "")
        
        if not status:
            _LOGGER.debug("No status data for device %s, skipping cover creation", device_id)
            return entities

        # Discover all possible entities
        discovered = discover_entities(status, device_code)
        
        # Filter for covers only
        for entity_def in discovered:
            if entity_def.entity_type != EntityType.COVER:
                continue
            
            unique_id = f"{device_id}_cover_{entity_def.channel}"
            if unique_id in created_entities:
                continue
            
            created_entities.add(unique_id)
            entities.append(
                ShellyIntegratorCover(
                    coordinator=coordinator,
                    device_id=device_id,
                    channel=entity_def.channel,
                    key=entity_def.key,
                )
            )
        
        if entities:
            _LOGGER.info("Creating %d cover entities for device %s", len(entities), device_id)
        
        return entities

    @callback
    def async_add_device(device_id: str) -> None:
        """Add entities for a newly discovered device."""
        entities = _create_covers(device_id)
        if entities:
            async_add_entities(entities)

    # Add existing devices
    entities: list[ShellyIntegratorCover] = []
    for device_id in list(coordinator.devices.keys()):
        entities.extend(_create_covers(device_id))

    if entities:
        async_add_entities(entities)

    # Listen for new devices
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, async_add_device)
    )


class ShellyIntegratorCover(CoordinatorEntity[ShellyIntegratorCoordinator], CoverEntity):
    """Representation of a Shelly roller shutter / cover."""

    _attr_has_entity_name = True
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
    ) -> None:
        """Initialize the cover."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._channel = channel
        self._key = key  # e.g., "rollers.0" or "cover:0"
        self._is_gen2 = key.startswith("cover:")
        
        self._attr_unique_id = f"{device_id}_cover_{channel}"
        self._attr_name = "Cover" if channel == 0 else f"Cover {channel + 1}"

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
    def available(self) -> bool:
        """Return if entity is available."""
        device = self.coordinator.devices.get(self._device_id, {})
        return device.get("online", False)

    @property
    def current_cover_position(self) -> int | None:
        """Return current position of cover (0-100, 100 is fully open)."""
        device = self.coordinator.devices.get(self._device_id, {})
        status = device.get("status", {})

        if self._is_gen2:
            # Gen2 format: cover:N
            cover_data = status.get(self._key, {})
            pos = cover_data.get("current_pos")
            if pos is not None:
                return pos
        else:
            # Gen1 format: rollers array
            rollers = status.get("rollers", [])
            if len(rollers) > self._channel:
                pos = rollers[self._channel].get("current_pos")
                if pos is not None:
                    return pos

        return None

    @property
    def is_closed(self) -> bool | None:
        """Return if the cover is closed."""
        position = self.current_cover_position
        if position is not None:
            return position == 0
        return None

    @property
    def is_opening(self) -> bool:
        """Return if the cover is opening."""
        device = self.coordinator.devices.get(self._device_id, {})
        status = device.get("status", {})

        if self._is_gen2:
            cover_data = status.get(self._key, {})
            return cover_data.get("state") == "opening"
        else:
            rollers = status.get("rollers", [])
            if len(rollers) > self._channel:
                return rollers[self._channel].get("state") == "open"
        return False

    @property
    def is_closing(self) -> bool:
        """Return if the cover is closing."""
        device = self.coordinator.devices.get(self._device_id, {})
        status = device.get("status", {})

        if self._is_gen2:
            cover_data = status.get(self._key, {})
            return cover_data.get("state") == "closing"
        else:
            rollers = status.get("rollers", [])
            if len(rollers) > self._channel:
                return rollers[self._channel].get("state") == "close"
        return False

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        await self.coordinator.send_command(
            device_id=self._device_id,
            cmd="roller",
            channel=self._channel,
            action="open",
        )

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        await self.coordinator.send_command(
            device_id=self._device_id,
            cmd="roller",
            channel=self._channel,
            action="close",
        )

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        await self.coordinator.send_command(
            device_id=self._device_id,
            cmd="roller",
            channel=self._channel,
            action="stop",
        )

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        position = kwargs.get("position")
        if position is not None:
            await self.coordinator.send_command(
                device_id=self._device_id,
                cmd="roller",
                channel=self._channel,
                action="to_pos",
                params={"pos": position},
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
