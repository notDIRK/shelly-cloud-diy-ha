"""Binary sensor platform for Shelly Integrator."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ShellyIntegratorCoordinator, SIGNAL_NEW_DEVICE
from .entity_factory import EntityType, discover_entities, get_status_value

_LOGGER = logging.getLogger(__name__)

# Map device_class strings to BinarySensorDeviceClass
DEVICE_CLASS_MAP = {
    "door": BinarySensorDeviceClass.DOOR,
    "window": BinarySensorDeviceClass.WINDOW,
    "motion": BinarySensorDeviceClass.MOTION,
    "moisture": BinarySensorDeviceClass.MOISTURE,
    "smoke": BinarySensorDeviceClass.SMOKE,
    "heat": BinarySensorDeviceClass.HEAT,
    "problem": BinarySensorDeviceClass.PROBLEM,
    "power": BinarySensorDeviceClass.POWER,
    "vibration": BinarySensorDeviceClass.VIBRATION,
    "gas": BinarySensorDeviceClass.GAS,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shelly Integrator binary sensors."""
    coordinator: ShellyIntegratorCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Track which entities have been created (by unique_id)
    created_entities: set[str] = set()

    def _create_binary_sensors(device_id: str) -> list[BinarySensorEntity]:
        """Create binary sensor entities for a device based on its status."""
        entities: list[BinarySensorEntity] = []
        device_data = coordinator.devices.get(device_id, {})
        status = device_data.get("status", {})
        device_code = device_data.get("device_code", "")
        
        if not status:
            _LOGGER.debug("No status data for device %s, skipping binary sensor creation", device_id)
            return entities

        # Discover all possible entities
        discovered = discover_entities(status, device_code)
        
        # Filter for binary sensors only
        for entity_def in discovered:
            if entity_def.entity_type != EntityType.BINARY_SENSOR:
                continue
            
            unique_id = f"{device_id}_binary_{entity_def.key.replace('.', '_').replace(':', '_')}"
            if unique_id in created_entities:
                continue
            
            created_entities.add(unique_id)
            entities.append(
                ShellyBinarySensor(
                    coordinator=coordinator,
                    device_id=device_id,
                    key=entity_def.key,
                    channel=entity_def.channel,
                    name_suffix=entity_def.name_suffix or "Binary Sensor",
                    device_class=entity_def.device_class,
                )
            )
        
        if entities:
            _LOGGER.info("Creating %d binary sensor entities for device %s", len(entities), device_id)
        
        return entities

    @callback
    def async_add_device(device_id: str) -> None:
        """Add entities for a newly discovered device."""
        entities = _create_binary_sensors(device_id)
        if entities:
            async_add_entities(entities)

    # Add existing devices
    entities: list[BinarySensorEntity] = []
    for device_id in list(coordinator.devices.keys()):
        entities.extend(_create_binary_sensors(device_id))

    if entities:
        async_add_entities(entities)

    # Listen for new devices
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, async_add_device)
    )


class ShellyBinarySensor(CoordinatorEntity[ShellyIntegratorCoordinator], BinarySensorEntity):
    """Generic Shelly binary sensor entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ShellyIntegratorCoordinator,
        device_id: str,
        key: str,
        channel: int,
        name_suffix: str,
        device_class: str | None,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._key = key
        self._channel = channel
        
        self._attr_unique_id = f"{device_id}_binary_{key.replace('.', '_').replace(':', '_')}"
        self._attr_name = name_suffix
        
        if device_class and device_class in DEVICE_CLASS_MAP:
            self._attr_device_class = DEVICE_CLASS_MAP[device_class]

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        device_data = self.coordinator.devices.get(self._device_id, {})
        
        # Try to get friendly name
        name = device_data.get("name")
        if not name:
            status = device_data.get("status", {})
            # Gen2 device name
            sys_info = status.get("sys", {}).get("device", {})
            name = sys_info.get("name")
            if not name:
                # Gen1 device name from settings
                settings = device_data.get("settings", {})
                name = settings.get("name")
            if not name:
                # Gen1 device name from getinfo
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
    def available(self) -> bool:
        """Return if entity is available."""
        device = self.coordinator.devices.get(self._device_id, {})
        return device.get("online", False)

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        device = self.coordinator.devices.get(self._device_id, {})
        status = device.get("status", {})
        
        value = get_status_value(status, self._key)
        
        if value is None:
            return None
        
        # Handle different value types
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value > 0
        if isinstance(value, str):
            # For door/window sensors: "open" = True, "close" = False
            return value.lower() in ("open", "true", "on", "1")
        
        return None
