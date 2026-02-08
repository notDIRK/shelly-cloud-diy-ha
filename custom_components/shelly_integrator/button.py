"""Button platform for Shelly Integrator.

Provides action buttons for Shelly devices:
- Gas Self-Test (Shelly Gas)
- Mute Gas Alarm (Shelly Gas)
- Unmute Gas Alarm (Shelly Gas)
"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
    """Set up Shelly Integrator buttons."""
    coordinator: ShellyIntegratorCoordinator = (
        hass.data[DOMAIN][entry.entry_id]
    )
    created_entities: set[str] = set()

    def create_buttons(device_id: str) -> list[ButtonEntity]:
        """Create button entities for a device."""
        entities: list[ButtonEntity] = []
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

        if entities:
            _LOGGER.info("Created %d buttons for %s", len(entities), device_id)

        return entities

    @callback
    def async_add_device(device_id: str) -> None:
        """Add entities for newly discovered device."""
        stale = [k for k in created_entities if k.startswith(device_id)]
        for k in stale:
            created_entities.discard(k)
        entities = create_buttons(device_id)
        if entities:
            async_add_entities(entities)

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


class ShellyGasSelfTestButton(ShellyBaseEntity, ButtonEntity):
    """Button to trigger gas sensor self-test."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:test-tube"

    def __init__(
        self,
        coordinator: ShellyIntegratorCoordinator,
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
        coordinator: ShellyIntegratorCoordinator,
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
        coordinator: ShellyIntegratorCoordinator,
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
