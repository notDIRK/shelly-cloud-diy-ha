"""Button platform for Shelly Cloud DIY.

Provides action buttons for Shelly devices:
- Gas Self-Test (Shelly Gas)
- Mute Gas Alarm (Shelly Gas)
- Unmute Gas Alarm (Shelly Gas)
- Calibrate (Shelly BLU TRV behind a BLU Gateway Gen3, over cloud control)
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_DEVICE_REMOVED
from .coordinator import ShellyCloudCoordinator, SIGNAL_NEW_DEVICE
from .entities.base import ShellyBaseEntity
from .entities.blutrv import BluTrvPartMixin, blutrv_keys

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shelly Cloud DIY buttons."""
    coordinator: ShellyCloudCoordinator = (
        hass.data[DOMAIN][entry.entry_id]
    )
    created_entities: set[str] = set()

    def create_buttons(device_id: str) -> list[ButtonEntity]:
        """Create button entities for a device."""
        entities: list[ButtonEntity] = []
        if not coordinator.is_enabled(device_id):
            return entities
        device_data = coordinator.devices.get(device_id, {})
        status = device_data.get("status", {})

        if not status:
            return entities

        # Gas device buttons (Shelly Gas has gas_sensor in status)
        gas = status.get("gas_sensor", {})
        if gas:
            # Self-test button
            uid = f"{device_id}_gas_self_test"
            if uid not in created_entities:
                created_entities.add(uid)
                entities.append(ShellyGasSelfTestButton(
                    coordinator, device_id,
                ))

            # Mute button
            uid = f"{device_id}_gas_mute"
            if uid not in created_entities:
                created_entities.add(uid)
                entities.append(ShellyGasMuteButton(
                    coordinator, device_id,
                ))

            # Unmute button
            uid = f"{device_id}_gas_unmute"
            if uid not in created_entities:
                created_entities.add(uid)
                entities.append(ShellyGasUnmuteButton(
                    coordinator, device_id,
                ))

        entities.extend(
            _create_blutrv_buttons(device_id, status, created_entities, coordinator)
        )

        if entities:
            _LOGGER.info("Created %d buttons for %s", len(entities), device_id)

        return entities

    @callback
    def async_add_device(device_id: str) -> None:
        """Add entities for newly discovered device."""
        entities = create_buttons(device_id)
        if entities:
            async_add_entities(entities)

    @callback
    def async_forget_device(device_id: str) -> None:
        """Forget a deleted device so a later rediscovery rebuilds it."""
        for k in [k for k in created_entities if k.startswith(device_id)]:
            created_entities.discard(k)

    # Add existing devices
    entities: list[ButtonEntity] = []
    for device_id in list(coordinator.devices.keys()):
        entities.extend(create_buttons(device_id))

    if entities:
        async_add_entities(entities)

    # Listen for new devices
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, async_add_device)
    )
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_DEVICE_REMOVED, async_forget_device)
    )


def _create_blutrv_buttons(
    device_id: str,
    status: dict[str, Any],
    created: set[str],
    coordinator: ShellyCloudCoordinator,
) -> list[ButtonEntity]:
    """One Calibrate button per BLU TRV the gateway carries (#58).

    Built for every valve, with or without cloud control: the button is
    unavailable while the relay cannot carry the call, which tells the user
    why it does nothing — a button that only exists sometimes does not.
    """
    entities: list[ButtonEntity] = []
    for display_index, key in enumerate(blutrv_keys(status)):
        uid = f"{device_id}_{key}_calibrate"
        if uid in created:
            continue
        created.add(uid)
        entities.append(
            ShellyBluTrvCalibrateButton(coordinator, device_id, key, display_index)
        )
    return entities


class ShellyGasSelfTestButton(ShellyBaseEntity, ButtonEntity):
    """Button to trigger gas sensor self-test."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:test-tube"

    def __init__(
        self,
        coordinator: ShellyCloudCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, device_id, 0)
        self._attr_unique_id = f"{device_id}_gas_self_test"
        self._attr_name = "Self-Test"

    async def async_press(self) -> None:
        """Trigger gas sensor self-test."""
        _LOGGER.info("Triggering gas self-test for %s", self._device_id)
        response = await self.coordinator.send_command(
            device_id=self._device_id,
            cmd="self_test",
            channel=0,
            action="start",
        )
        if response is None:
            _LOGGER.warning(
                "Gas self-test command got no response for %s",
                self._device_id,
            )


class ShellyGasMuteButton(ShellyBaseEntity, ButtonEntity):
    """Button to mute gas alarm."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:volume-off"

    def __init__(
        self,
        coordinator: ShellyCloudCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, device_id, 0)
        self._attr_unique_id = f"{device_id}_gas_mute"
        self._attr_name = "Mute Alarm"

    async def async_press(self) -> None:
        """Mute the gas alarm."""
        _LOGGER.info("Muting gas alarm for %s", self._device_id)
        await self.coordinator.send_command(
            device_id=self._device_id,
            cmd="relay",
            channel=0,
            action="on",
        )


class ShellyGasUnmuteButton(ShellyBaseEntity, ButtonEntity):
    """Button to unmute gas alarm."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:volume-high"

    def __init__(
        self,
        coordinator: ShellyCloudCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator, device_id, 0)
        self._attr_unique_id = f"{device_id}_gas_unmute"
        self._attr_name = "Unmute Alarm"

    async def async_press(self) -> None:
        """Unmute the gas alarm."""
        _LOGGER.info("Unmuting gas alarm for %s", self._device_id)
        await self.coordinator.send_command(
            device_id=self._device_id,
            cmd="relay",
            channel=0,
            action="off",
        )


class ShellyBluTrvCalibrateButton(BluTrvPartMixin, ShellyBaseEntity, ButtonEntity):
    """Start a BLU TRV's stepper-motor calibration, through its gateway.

    The outcome is not known when the press returns: the valve reports a
    failed run as ``not_calibrated`` in its status, which the calibration
    problem sensor next to this button picks up from the poll. (#58)
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:tune-vertical"
    _attr_translation_key = "blutrv_calibrate"
    _default_name_template = "Calibrate {valve}"

    def __init__(
        self,
        coordinator: ShellyCloudCoordinator,
        device_id: str,
        component_key: str,
        display_index: int,
    ) -> None:
        """Initialize the button on the gateway's device, as the climate is."""
        super().__init__(coordinator, device_id, display_index)
        self._init_valve(component_key, display_index)
        self._attr_unique_id = f"{device_id}_{component_key}_calibrate"

    @property
    def available(self) -> bool:
        """Available while the valve is on the gateway and the relay will route.

        The same condition under which the climate entity offers its
        setpoint: cloud control connected, and the relay willing to carry
        commands to this gateway.
        """
        return (
            self._valve_present()
            and self.coordinator.cloud_control_connected
            and self.coordinator.is_cloud_controllable(self._device_id)
            and super().available
        )

    async def async_press(self) -> None:
        """Start the calibration; failures raise, nothing is assumed."""
        _LOGGER.info(
            "Starting BLU TRV calibration for %s on %s",
            self._component_key,
            self._device_id,
        )
        await self.coordinator.async_calibrate_blutrv(
            self._device_id, self._component_key
        )
