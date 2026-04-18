"""Sensor platform for Shelly Cloud DIY."""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from homeassistant.const import PERCENTAGE, UnitOfElectricPotential, EntityCategory
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from .const import DOMAIN, device_gen, is_gen2_status
from .coordinator import ShellyCloudCoordinator, SIGNAL_NEW_DEVICE
from .entities.base import ShellyBaseEntity
from .entities.descriptions import (
    BLE_SENSORS,
    BLOCK_SENSORS,
    RPC_SENSORS,
    BleSensorDescription,
    BlockSensorDescription,
    RpcSensorDescription,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shelly Cloud DIY sensors."""
    coordinator: ShellyCloudCoordinator = hass.data[DOMAIN][entry.entry_id]
    created_sensors: set[str] = set()

    def create_sensors(device_id: str) -> list[SensorEntity]:
        """Create sensor entities for a device."""
        entities: list[SensorEntity] = []
        if not coordinator.is_enabled(device_id):
            return entities
        device_data = coordinator.devices.get(device_id, {})
        status = device_data.get("status", {})

        if not status:
            return entities

        gen = device_gen(status)
        if gen == "GBLE":
            entities.extend(_create_ble_sensors(
                device_id, status, created_sensors, coordinator
            ))
        elif is_gen2_status(status):
            entities.extend(_create_rpc_sensors(
                device_id, status, created_sensors, coordinator
            ))
        else:
            entities.extend(_create_block_sensors(
                device_id, status, created_sensors, coordinator
            ))

        if entities:
            _LOGGER.info("Created %d sensors for %s", len(entities), device_id)

        return entities

    @callback
    def async_add_device(device_id: str) -> None:
        """Add entities for newly discovered device."""
        # Clear stale tracking for this device so entities are
        # recreated after a delete-then-rediscover cycle.
        stale = [k for k in created_sensors if k.startswith(device_id)]
        for k in stale:
            created_sensors.discard(k)
        entities = create_sensors(device_id)
        if entities:
            async_add_entities(entities)

    # Add existing devices
    entities: list[SensorEntity] = []
    for device_id in list(coordinator.devices.keys()):
        entities.extend(create_sensors(device_id))

    if entities:
        async_add_entities(entities)

    # Listen for new devices
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, async_add_device)
    )


def _create_block_sensors(
    device_id: str,
    status: dict[str, Any],
    created: set[str],
    coordinator: ShellyCloudCoordinator,
) -> list[SensorEntity]:
    """Create Gen1 Block sensors."""
    entities: list[SensorEntity] = []

    # Emeters
    for idx, emeter in enumerate(status.get("emeters", [])):
        for attr, key in [
            ("power", ("emeter", "power")),
            ("voltage", ("emeter", "voltage")),
            ("current", ("emeter", "current")),
            ("pf", ("emeter", "powerFactor")),
        ]:
            if attr in emeter and key in BLOCK_SENSORS:
                desc = BLOCK_SENSORS[key]
                uid = f"{device_id}_{desc.key}_{idx}"
                if uid not in created:
                    created.add(uid)
                    entities.append(BlockSensor(
                        coordinator, device_id, desc, idx, "emeters", attr
                    ))

        if "total" in emeter:
            desc = BLOCK_SENSORS.get(("emeter", "energy"))
            if desc:
                uid = f"{device_id}_{desc.key}_{idx}"
                if uid not in created:
                    created.add(uid)
                    entities.append(BlockSensor(
                        coordinator, device_id, desc, idx, "emeters", "total"
                    ))

    # Meters
    for idx, meter in enumerate(status.get("meters", [])):
        if "power" in meter:
            desc = BLOCK_SENSORS.get(("relay", "power"))
            if desc:
                uid = f"{device_id}_meter_power_{idx}"
                if uid not in created:
                    created.add(uid)
                    entities.append(BlockSensor(
                        coordinator, device_id, desc, idx, "meters", "power"
                    ))

    # Gas sensor
    gas = status.get("gas_sensor", {})
    if gas and "sensor_state" in gas:
        desc = BLOCK_SENSORS.get(("sensor", "sensorOp"))
        if desc:
            uid = f"{device_id}_gas_sensor_state"
            if uid not in created:
                created.add(uid)
                entities.append(BlockSensor(
                    coordinator, device_id, desc, 0,
                    "gas_sensor", "sensor_state",
                ))

    # Concentration – always create the entity when data is
    # present; ``is_valid`` is used for availability, not entity
    # creation (sensor may still be warming up at start-up).
    conc = status.get("concentration", {})
    if conc and "ppm" in conc:
        desc = BLOCK_SENSORS.get(("sensor", "concentration"))
        if desc:
            uid = f"{device_id}_gas_concentration"
            if uid not in created:
                created.add(uid)
                entities.append(BlockSensor(
                    coordinator, device_id, desc, 0, "concentration", "ppm"
                ))

    # Temperature
    temp = status.get("tmp", {}) or status.get("temperature", {})
    if temp and "tC" in temp:
        desc = BLOCK_SENSORS.get(("sensor", "temp"))
        if desc:
            uid = f"{device_id}_temperature"
            if uid not in created:
                created.add(uid)
                key = "tmp" if "tmp" in status else "temperature"
                entities.append(BlockSensor(
                    coordinator, device_id, desc, 0, key, "tC"
                ))

    return entities


def _create_rpc_sensors(
    device_id: str,
    status: dict[str, Any],
    created: set[str],
    coordinator: ShellyCloudCoordinator,
) -> list[SensorEntity]:
    """Create Gen2/Gen3 RPC sensors."""
    entities: list[SensorEntity] = []

    for key in status:
        if match := re.match(r"(switch|light|cover):(\d+)", key):
            component = match.group(1)
            idx = int(match.group(2))
            data = status[key]

            for attr, desc_key in [
                ("apower", "switch_power"),
                ("voltage", "switch_voltage"),
                ("current", "switch_current"),
            ]:
                if attr in data:
                    desc = RPC_SENSORS.get(desc_key)
                    if desc:
                        uid = f"{device_id}_{component}_{idx}_{attr}"
                        if uid not in created:
                            created.add(uid)
                            entities.append(RpcSensor(
                                coordinator, device_id, desc, idx, key, attr
                            ))

    # Temperature sensors
    for key in status:
        if match := re.match(r"temperature:(\d+)", key):
            idx = int(match.group(1))
            desc = RPC_SENSORS.get("temperature")
            if desc:
                uid = f"{device_id}_temperature_{idx}"
                if uid not in created:
                    created.add(uid)
                    entities.append(RpcSensor(
                        coordinator, device_id, desc, idx, key, "tC"
                    ))

    return entities


class BlockSensor(ShellyBaseEntity, SensorEntity):
    """Gen1 Block sensor."""

    def __init__(
        self,
        coordinator: ShellyCloudCoordinator,
        device_id: str,
        description: BlockSensorDescription,
        channel: int,
        status_key: str,
        attr_key: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, channel)
        self._description = description
        self._status_key = status_key
        self._attr_key = attr_key

        self._attr_unique_id = f"{device_id}_{description.key}_{channel}"
        name = description.name or "Sensor"
        self._attr_name = name if channel == 0 else f"{name} {channel + 1}"

        if description.device_class:
            self._attr_device_class = description.device_class
        if description.state_class:
            self._attr_state_class = description.state_class
        if description.native_unit_of_measurement:
            self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        if description.entity_category:
            self._attr_entity_category = description.entity_category
        if description.icon:
            self._attr_icon = description.icon
        if description.suggested_display_precision is not None:
            self._attr_suggested_display_precision = description.suggested_display_precision

    @property
    def native_value(self) -> float | int | str | None:
        """Return sensor value."""
        status = self.device_status
        container = status.get(self._status_key)

        if container is None:
            return None

        if isinstance(container, list):
            if self._channel >= len(container):
                return None
            container = container[self._channel]

        value = container.get(self._attr_key) if isinstance(container, dict) else None

        if value is not None and self._description.value_fn:
            value = self._description.value_fn(value)

        return value


def _create_ble_sensors(
    device_id: str,
    status: dict[str, Any],
    created: set[str],
    coordinator: ShellyCloudCoordinator,
) -> list[SensorEntity]:
    """Create sensors for BLE / Shelly-BLU-Gateway-bridged devices.

    These devices report each reading under a ``<type>:<channel>`` key,
    e.g. ``humidity:0``, ``temperature:0``, ``speed:0`` (wind, channel 0),
    ``speed:1`` (gust, channel 1). We iterate every such key and look up
    the BLE_SENSORS description table. Unknown sensor types are skipped.
    ``devicepower:0`` is special-cased into a battery percentage sensor.
    """
    entities: list[SensorEntity] = []

    for key, payload in status.items():
        if not isinstance(payload, dict):
            continue
        if ":" not in key:
            continue
        sensor_type, _, channel_s = key.partition(":")
        if not channel_s.isdigit():
            continue
        channel = int(channel_s)

        desc = BLE_SENSORS.get(sensor_type)
        if desc is None:
            continue
        if desc.value_field not in payload:
            continue

        uid = f"{device_id}_ble_{sensor_type}_{channel}"
        if uid in created:
            continue
        created.add(uid)
        entities.append(
            BleSensor(
                coordinator=coordinator,
                device_id=device_id,
                description=desc,
                sensor_type=sensor_type,
                channel=channel,
            )
        )

    # devicepower:0 → battery percentage / voltage (special nested shape)
    dp = status.get("devicepower:0")
    if isinstance(dp, dict) and isinstance(dp.get("battery"), dict):
        battery = dp["battery"]
        if "percent" in battery:
            uid = f"{device_id}_ble_battery_percent"
            if uid not in created:
                created.add(uid)
                entities.append(
                    BleBatteryPercentSensor(
                        coordinator=coordinator, device_id=device_id
                    )
                )
        if "V" in battery:
            uid = f"{device_id}_ble_battery_voltage"
            if uid not in created:
                created.add(uid)
                entities.append(
                    BleBatteryVoltageSensor(
                        coordinator=coordinator, device_id=device_id
                    )
                )

    return entities


class RpcSensor(ShellyBaseEntity, SensorEntity):
    """Gen2/Gen3 RPC sensor."""

    def __init__(
        self,
        coordinator: ShellyCloudCoordinator,
        device_id: str,
        description: RpcSensorDescription,
        channel: int,
        component_key: str,
        attr_key: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, channel)
        self._description = description
        self._component_key = component_key
        self._attr_key = attr_key

        self._attr_unique_id = f"{device_id}_{component_key}_{attr_key}"
        name = description.name or "Sensor"
        self._attr_name = name if channel == 0 else f"{name} {channel + 1}"

        if description.device_class:
            self._attr_device_class = description.device_class
        if description.state_class:
            self._attr_state_class = description.state_class
        if description.native_unit_of_measurement:
            self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        if description.entity_category:
            self._attr_entity_category = description.entity_category
        if description.icon:
            self._attr_icon = description.icon
        if description.suggested_display_precision is not None:
            self._attr_suggested_display_precision = description.suggested_display_precision

    @property
    def native_value(self) -> float | int | str | None:
        """Return sensor value."""
        component = self.device_status.get(self._component_key)
        if component is None:
            return None

        value = component.get(self._attr_key)

        if value is not None and self._description.value_fn:
            value = self._description.value_fn(value)

        return value


class BleSensor(ShellyBaseEntity, SensorEntity):
    """BLE / Shelly-BLU-Gateway-bridged sensor.

    Reads a value from a ``<sensor_type>:<channel>`` status key using the
    metadata in :class:`BleSensorDescription` to shape the HA entity. One
    description applies to every channel of the same sensor type, so
    ``speed:0`` (wind) and ``speed:1`` (gust) share the "Wind Speed"
    description but each becomes its own entity with a unique channel.
    """

    def __init__(
        self,
        *,
        coordinator: ShellyCloudCoordinator,
        device_id: str,
        description: BleSensorDescription,
        sensor_type: str,
        channel: int,
    ) -> None:
        super().__init__(coordinator, device_id, channel)
        self._description = description
        self._sensor_type = sensor_type
        self._status_key = f"{sensor_type}:{channel}"

        self._attr_unique_id = f"{device_id}_ble_{sensor_type}_{channel}"
        base_name = description.name
        self._attr_name = base_name if channel == 0 else f"{base_name} {channel + 1}"

        if description.device_class:
            self._attr_device_class = description.device_class
        if description.state_class:
            self._attr_state_class = description.state_class
        if description.native_unit_of_measurement:
            self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        if description.entity_category:
            self._attr_entity_category = description.entity_category
        if description.icon:
            self._attr_icon = description.icon
        if description.suggested_display_precision is not None:
            self._attr_suggested_display_precision = description.suggested_display_precision

    @property
    def native_value(self) -> float | int | str | None:
        """Return the numeric reading for this BLE sensor channel."""
        payload = self.device_status.get(self._status_key)
        if not isinstance(payload, dict):
            return None
        return payload.get(self._description.value_field)


class BleBatteryPercentSensor(ShellyBaseEntity, SensorEntity):
    """Battery percentage reading from a BLE device's ``devicepower:0``."""

    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        *,
        coordinator: ShellyCloudCoordinator,
        device_id: str,
    ) -> None:
        super().__init__(coordinator, device_id, 0)
        self._attr_unique_id = f"{device_id}_ble_battery_percent"

    @property
    def native_value(self) -> float | int | None:
        dp = self.device_status.get("devicepower:0")
        if not isinstance(dp, dict):
            return None
        battery = dp.get("battery")
        if not isinstance(battery, dict):
            return None
        return battery.get("percent")


class BleBatteryVoltageSensor(ShellyBaseEntity, SensorEntity):
    """Battery voltage reading from a BLE device's ``devicepower:0``."""

    _attr_name = "Battery Voltage"
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        *,
        coordinator: ShellyCloudCoordinator,
        device_id: str,
    ) -> None:
        super().__init__(coordinator, device_id, 0)
        self._attr_unique_id = f"{device_id}_ble_battery_voltage"

    @property
    def native_value(self) -> float | None:
        dp = self.device_status.get("devicepower:0")
        if not isinstance(dp, dict):
            return None
        battery = dp.get("battery")
        if not isinstance(battery, dict):
            return None
        return battery.get("V")
