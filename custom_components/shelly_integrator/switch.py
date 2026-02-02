"""Switch platform for Shelly Integrator."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
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
    """Set up Shelly Integrator switches."""
    coordinator: ShellyIntegratorCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Track which entities have been created (by unique_id)
    created_entities: set[str] = set()

    def _create_switches(device_id: str) -> list[ShellyIntegratorSwitch]:
        """Create switch entities for a device based on its status."""
        entities: list[ShellyIntegratorSwitch] = []
        device_data = coordinator.devices.get(device_id, {})
        status = device_data.get("status", {})
        device_code = device_data.get("device_code", "")
        
        if not status:
            _LOGGER.debug("No status data for device %s, skipping switch creation", device_id)
            return entities

        # Discover all possible entities
        discovered = discover_entities(status, device_code)
        
        # Filter for switches only
        for entity_def in discovered:
            if entity_def.entity_type != EntityType.SWITCH:
                continue
            
            unique_id = f"{device_id}_switch_{entity_def.channel}"
            if unique_id in created_entities:
                continue
            
            created_entities.add(unique_id)
            entities.append(
                ShellyIntegratorSwitch(
                    coordinator=coordinator,
                    device_id=device_id,
                    channel=entity_def.channel,
                    key=entity_def.key,
                )
            )
        
        if entities:
            _LOGGER.info("Creating %d switch entities for device %s", len(entities), device_id)
        
        return entities

    @callback
    def async_add_device(device_id: str) -> None:
        """Add entities for a newly discovered device."""
        entities = _create_switches(device_id)
        if entities:
            async_add_entities(entities)

    # Add existing devices
    entities: list[ShellyIntegratorSwitch] = []
    for device_id in list(coordinator.devices.keys()):
        entities.extend(_create_switches(device_id))

    if entities:
        async_add_entities(entities)

    # Listen for new devices
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, async_add_device)
    )


class ShellyIntegratorSwitch(CoordinatorEntity[ShellyIntegratorCoordinator], SwitchEntity):
    """Representation of a Shelly relay switch."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ShellyIntegratorCoordinator,
        device_id: str,
        channel: int,
        key: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._channel = channel
        self._key = key  # e.g., "relays.0" or "switch:0"
        self._is_gen2 = key.startswith("switch:")
        
        self._attr_unique_id = f"{device_id}_switch_{channel}"
        # Entity name is relative to device
        self._attr_name = "Switch" if channel == 0 else f"Switch {channel + 1}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        device_data = self.coordinator.devices.get(self._device_id, {})
        status = device_data.get("status", {})
        
        # Try to get friendly name
        name = device_data.get("name")
        if not name:
            # Gen2: name in sys.device
            sys_info = status.get("sys", {}).get("device", {})
            name = sys_info.get("name")
        if not name:
            # Gen1: name from settings
            settings = device_data.get("settings", {})
            name = settings.get("name")
        if not name:
            # Gen1: device from getinfo
            getinfo = status.get("getinfo", {}).get("fw_info", {})
            name = getinfo.get("device")
        
        if not name:
            # Fallback to device code + short ID
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
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        device = self.coordinator.devices.get(self._device_id, {})
        status = device.get("status", {})

        if self._is_gen2:
            # Gen2 format: switch:N
            switch_data = status.get(self._key, {})
            return switch_data.get("output", False)
        else:
            # Gen1 format: relays array
            relays = status.get("relays", [])
            if len(relays) > self._channel:
                return relays[self._channel].get("ison", False)

        return None

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        device = self.coordinator.devices.get(self._device_id, {})
        return device.get("online", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self.coordinator.send_command(
            device_id=self._device_id,
            cmd="relay",
            channel=self._channel,
            action="on",
        )
        # Optimistic update
        self._update_local_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self.coordinator.send_command(
            device_id=self._device_id,
            cmd="relay",
            channel=self._channel,
            action="off",
        )
        # Optimistic update
        self._update_local_state(False)

    def _update_local_state(self, is_on: bool) -> None:
        """Update local state optimistically."""
        device = self.coordinator.devices.get(self._device_id, {})
        status = device.get("status", {})

        if self._is_gen2:
            # Gen2 format
            if self._key in status:
                status[self._key]["output"] = is_on
        else:
            # Gen1 format
            relays = status.get("relays", [])
            if len(relays) > self._channel:
                relays[self._channel]["ison"] = is_on
        
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
