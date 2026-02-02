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

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shelly Integrator switches."""
    coordinator: ShellyIntegratorCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Track which devices already have entities
    known_devices: set[str] = set()

    def _create_switches(device_id: str) -> list[ShellyIntegratorSwitch]:
        """Create switch entities for a device."""
        entities = []
        device_data = coordinator.devices.get(device_id, {})
        status = device_data.get("status", {})
        device_type = device_data.get("device_type", "")

        # Gen1 format: relays array
        relays = status.get("relays", [])
        if relays:
            for idx, _ in enumerate(relays):
                entities.append(
                    ShellyIntegratorSwitch(
                        coordinator=coordinator,
                        device_id=device_id,
                        channel=idx,
                    )
                )
            return entities

        # Gen2 format: switch:0, switch:1, etc.
        for key in status:
            if key.startswith("switch:"):
                try:
                    channel = int(key.split(":")[1])
                    entities.append(
                        ShellyIntegratorSwitch(
                            coordinator=coordinator,
                            device_id=device_id,
                            channel=channel,
                        )
                    )
                except (ValueError, IndexError):
                    pass

        # If no switches found but device type suggests it has relay, create default
        if not entities and ("1pm" in device_type.lower() or "1" in device_type.lower() or "plug" in device_type.lower()):
            _LOGGER.info("Creating default switch for device %s (type: %s)", device_id, device_type)
            entities.append(
                ShellyIntegratorSwitch(
                    coordinator=coordinator,
                    device_id=device_id,
                    channel=0,
                )
            )

        return entities

    @callback
    def async_add_device(device_id: str) -> None:
        """Add entities for a newly discovered device."""
        if device_id in known_devices:
            return

        known_devices.add(device_id)
        entities = _create_switches(device_id)
        if entities:
            async_add_entities(entities)

    # Add existing devices
    for device_id in coordinator.devices:
        known_devices.add(device_id)

    entities = []
    for device_id in known_devices:
        entities.extend(_create_switches(device_id))

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
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._channel = channel
        self._attr_unique_id = f"{device_id}_switch_{channel}"
        # Entity name is relative to device, so just "Switch" or "Switch 1" for multi-channel
        self._attr_name = "Switch" if channel == 0 else f"Switch {channel + 1}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        device_data = self.coordinator.devices.get(self._device_id, {})
        device_type = device_data.get("device_type", "")
        device_code = device_data.get("device_code", "")
        device_name = device_data.get("name")
        status = device_data.get("status", {})

        # Try multiple sources for device name
        if not device_name:
            # Gen2: name in sys.device
            sys_info = status.get("sys", {})
            device_name = sys_info.get("device", {}).get("name")

        # Fall back to device type + short ID
        if not device_name:
            short_id = self._device_id[-6:] if len(self._device_id) > 6 else self._device_id
            model_name = device_type or device_code or "Shelly"
            device_name = f"{model_name} {short_id}"

        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=device_name,
            manufacturer="Shelly",
            model=device_type or device_code or "Unknown",
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        device = self.coordinator.devices.get(self._device_id, {})
        status = device.get("status", {})

        # Gen1 format: relays array
        relays = status.get("relays", [])
        if relays and len(relays) > self._channel:
            return relays[self._channel].get("ison", False)

        # Gen2 format: switch:N
        switch_key = f"switch:{self._channel}"
        if switch_key in status:
            return status[switch_key].get("output", False)

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
        relays = status.get("relays", [])

        if len(relays) > self._channel:
            relays[self._channel]["ison"] = is_on
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
