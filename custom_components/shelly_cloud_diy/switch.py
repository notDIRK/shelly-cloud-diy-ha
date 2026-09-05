"""Switch platform for Shelly Cloud DIY.

Two kinds of switch live here, and they reach the device by different
roads. The hardware channels (Gen1 ``relays``, Gen2 ``switch:N``) go out
over the documented Cloud Control API, exactly as before. The virtual
booleans go over the undocumented cloud WebSocket relay — only when the
user opted into cloud control, and only on devices that relay said it will
route to.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_DEVICE_REMOVED
from .coordinator import ShellyCloudCoordinator, SIGNAL_NEW_DEVICE
from .entities.base import ShellyBaseEntity

_LOGGER = logging.getLogger(__name__)


def _controllable_boolean_keys(
    coordinator: ShellyCloudCoordinator,
    device_id: str,
) -> list[str]:
    """Virtual boolean keys this device may be given a control entity for.

    Both questions go to the coordinator, and both go through ``getattr``.
    That is not defensive clutter but the platform's half of the opt-in
    contract: anything that is not a coordinator running with cloud control
    switched on answers "none", and this platform then behaves exactly as it
    did before the feature existed. Which components are writable is the
    coordinator's knowledge — it is the side that has to send the command.
    """
    controllable = getattr(coordinator, "is_cloud_controllable", None)
    boolean_keys = getattr(coordinator, "cloud_control_boolean_keys", None)
    if not callable(controllable) or not callable(boolean_keys):
        return []
    return boolean_keys(device_id) if controllable(device_id) else []


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shelly Cloud DIY switches."""
    coordinator: ShellyCloudCoordinator = hass.data[DOMAIN][entry.entry_id]
    created_entities: set[str] = set()

    def create_switches(device_id: str) -> list[SwitchEntity]:
        """Create switch entities for a device."""
        entities: list[SwitchEntity] = []
        if not coordinator.is_enabled(device_id):
            return entities
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

        # Virtual booleans, over the opt-in cloud control channel only.
        for key in _controllable_boolean_keys(coordinator, device_id):
            unique_id = f"{device_id}_{key}_control"
            if unique_id not in created_entities:
                created_entities.add(unique_id)
                entities.append(
                    ShellyVirtualBooleanSwitch(coordinator, device_id, key)
                )

        if entities:
            _LOGGER.info("Created %d switches for %s", len(entities), device_id)

        return entities

    @callback
    def async_add_device(device_id: str) -> None:
        """Add entities for newly discovered device."""
        entities = create_switches(device_id)
        if entities:
            async_add_entities(entities)

    @callback
    def async_forget_device(device_id: str) -> None:
        """Forget a deleted device so a later rediscovery rebuilds it."""
        for k in [k for k in created_entities if k.startswith(device_id)]:
            created_entities.discard(k)

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
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_DEVICE_REMOVED, async_forget_device)
    )


class ShellySwitch(ShellyBaseEntity, SwitchEntity):
    """Shelly switch entity."""

    def __init__(
        self,
        coordinator: ShellyCloudCoordinator,
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

    def _component(self) -> dict[str, Any]:
        """Return this channel's raw status component (Gen1 or Gen2)."""
        status = self.device_status
        if self._is_gen2:
            return status.get(self._key, {})
        relays = status.get("relays", [])
        if len(relays) > self._channel:
            return relays[self._channel]
        return {}

    @property
    def is_on(self) -> bool | None:
        """Return true if switch is on."""
        component = self._apply_optimistic(self._component())
        if not component:
            return None
        key = "output" if self._is_gen2 else "ison"
        return component.get(key, False)

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
        """Send the appropriate command for Gen1 or Gen2 switch.
        
        NOTE: When using Shelly Cloud Integrator API, even Gen2 devices
        use CommandRequest (cmd: "relay") format, not JrpcRequest.
        JrpcRequest is only for advanced RPC methods like Thermostat.SetConfig.
        """
        # For cloud integrator API, use CommandRequest for all devices
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

        # Check for JRPC error response (Gen2/Gen3)
        jrpc_response = response.get("response", {})
        if "error" in jrpc_response:
            error = jrpc_response.get("error")
            if error == "UNAUTHORIZED":
                _LOGGER.error(
                    "Command UNAUTHORIZED - check logs for access "
                    "diagnostics. You may need to grant control "
                    "permissions at https://my.shelly.cloud/integrator.html"
                )
            else:
                _LOGGER.error("JRPC error: %s", error)
            return False

        # Check for CommandResponse (Gen1)
        data = response.get("data", {})
        if isinstance(data, dict) and "isok" in data:
            if not data["isok"]:
                _LOGGER.error("Command rejected: %s", data.get("res"))
                return False

        return True

    def _update_local_state(self, is_on: bool) -> None:
        """Record the commanded state optimistically until the cloud confirms."""
        key = "output" if self._is_gen2 else "ison"
        self._set_optimistic({key: is_on})


class ShellyVirtualBooleanSwitch(ShellyBaseEntity, SwitchEntity):
    """Writable Gen2/Gen3 virtual boolean (``boolean:<id>``), over the relay.

    Created **in addition** to the read-only binary sensor of the same
    component, never instead of it. Replacing it would strand every
    automation and dashboard card already pointing at that sensor, and would
    do it silently — the entity would simply go unavailable. For a feature
    whose first consumer is an irrigation controller, "silently stops
    working" is the exact failure this is designed against. The visible
    redundancy is the cheaper of the two.

    The state is read from the poll, like every other entity here. The relay
    only carries the command, and the command is confirmed by the next poll
    rather than by an optimistic write: a zone that accepted the command and
    did not move must look different from one that switched.
    """

    def __init__(
        self,
        coordinator: ShellyCloudCoordinator,
        device_id: str,
        component_key: str,
    ) -> None:
        """Initialise the writable virtual boolean."""
        super().__init__(coordinator, device_id, 0)
        self._component_key = component_key
        # NOT the binary sensor's ``…_value`` id. Reusing that one would move
        # an existing entity to another platform, which is a migration, and
        # this is not the change to smuggle one into.
        self._attr_unique_id = f"{device_id}_{component_key}_control"
        self._generic_name = f"Boolean {component_key.split(':', 1)[1]}"

    @property
    def name(self) -> str:
        """Resolved component name, or the generic fallback.

        The same lazily fetched v2 config the read-only sensor reads, so on
        an irrigation controller both entities carry the zone name the user
        typed rather than one of them showing ``boolean:203``.
        """
        return self.virtual_component_name(self._component_key) or self._generic_name

    @property
    def available(self) -> bool:
        """Available only while the channel that carries the command is up.

        The base class asks whether the *device* is reachable through the
        cloud, which is the right question for every entity that reads the
        poll. This one also writes, over a second connection that can be
        down on its own — and a switch that renders as operable while every
        press is guaranteed to throw is the polite version of the failure
        this feature exists to prevent.
        """
        return super().available and self.coordinator.cloud_control_connected

    @property
    def is_on(self) -> bool | None:
        """Return the component's current value as the poll last saw it."""
        component = self.device_status.get(self._component_key)
        if not isinstance(component, dict):
            return None
        value = component.get("value")
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Set the virtual boolean to true."""
        await self.coordinator.async_set_virtual_boolean(
            self._device_id, self._component_key, True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Set the virtual boolean to false."""
        await self.coordinator.async_set_virtual_boolean(
            self._device_id, self._component_key, False
        )
