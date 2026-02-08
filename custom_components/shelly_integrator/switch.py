"""Switch platform for Shelly Integrator."""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.switch import SwitchEntity
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
    """Set up Shelly Integrator switches."""
    coordinator: ShellyIntegratorCoordinator = hass.data[DOMAIN][entry.entry_id]
    created_entities: set[str] = set()

    def create_switches(device_id: str) -> list[SwitchEntity]:
        """Create switch entities for a device."""
        entities: list[SwitchEntity] = []
        device_data = coordinator.devices.get(device_id, {})
        status = device_data.get("status", {})

        if not status:
            return entities

        # Gen1: relays array
        for idx, _ in enumerate(status.get("relays", [])):
            unique_id = f"{device_id}_switch_{idx}"
            if unique_id not in created_entities:
                created_entities.add(unique_id)
                entities.append(ShellySwitch(
                    coordinator, device_id, idx, f"relays.{idx}", is_gen2=False
                ))

        # Gen2: switch:N pattern
        for key in status:
            if match := re.match(r"switch:(\d+)", key):
                idx = int(match.group(1))
                unique_id = f"{device_id}_switch_{idx}"
                if unique_id not in created_entities:
                    created_entities.add(unique_id)
                    entities.append(ShellySwitch(
                        coordinator, device_id, idx, key, is_gen2=True
                    ))

        if entities:
            _LOGGER.info("Created %d switches for %s", len(entities), device_id)

        return entities

    @callback
    def async_add_device(device_id: str) -> None:
        """Add entities for newly discovered device."""
        stale = [k for k in created_entities if k.startswith(device_id)]
        for k in stale:
            created_entities.discard(k)
        entities = create_switches(device_id)
        if entities:
            async_add_entities(entities)

    # Add existing devices
    entities: list[SwitchEntity] = []
    for device_id in list(coordinator.devices.keys()):
        entities.extend(create_switches(device_id))

    if entities:
        async_add_entities(entities)

    # Listen for new devices
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, async_add_device)
    )


class ShellySwitch(ShellyBaseEntity, SwitchEntity):
    """Shelly switch entity."""

    def __init__(
        self,
        coordinator: ShellyIntegratorCoordinator,
        device_id: str,
        channel: int,
        key: str,
        is_gen2: bool,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id, channel)
        self._key = key
        self._is_gen2 = is_gen2
        self._attr_unique_id = f"{device_id}_switch_{channel}"
        self._attr_name = "Switch" if channel == 0 else f"Switch {channel + 1}"

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        status = self.device_status

        if self._is_gen2:
            return status.get(self._key, {}).get("output", False)
        else:
            relays = status.get("relays", [])
            if len(relays) > self._channel:
                return relays[self._channel].get("ison", False)

        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        response = await self._send_switch_command(on=True)
        if not self._is_command_ok(response):
            return
        self._update_local_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        response = await self._send_switch_command(on=False)
        if not self._is_command_ok(response):
            return
        self._update_local_state(False)

    async def _send_switch_command(self, on: bool) -> dict | None:
        """Send the appropriate command for Gen1 or Gen2 switch."""
        if self._is_gen2:
            return await self.coordinator.send_jrpc_command(
                device_id=self._device_id,
                method="Switch.Set",
                params={"id": self._channel, "on": on},
            )
        return await self.coordinator.send_command(
            device_id=self._device_id,
            cmd="relay",
            channel=self._channel,
            action="on" if on else "off",
        )

    @staticmethod
    def _is_command_ok(response: dict | None) -> bool:
        """Check if a command response indicates success."""
        if response is None:
            _LOGGER.warning("Command failed: no response")
            return False
        # CommandResponse: data.isok, JrpcResponse: response.result
        data = response.get("data", {})
        if isinstance(data, dict) and "isok" in data:
            if not data["isok"]:
                _LOGGER.error("Command rejected: %s", data.get("res"))
                return False
        return True

    def _update_local_state(self, is_on: bool) -> None:
        """Update local state optimistically."""
        status = self.device_status

        if self._is_gen2:
            if self._key in status:
                status[self._key]["output"] = is_on
        else:
            relays = status.get("relays", [])
            if len(relays) > self._channel:
                relays[self._channel]["ison"] = is_on

        self.async_write_ha_state()
