"""Binary sensor platform for Shelly Cloud DIY."""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_DEVICE_REMOVED, device_gen, is_gen2_status
from .coordinator import ShellyCloudCoordinator, SIGNAL_NEW_DEVICE
from .entities.base import ShellyBaseEntity
from .entities.descriptions import (
    BLE_BINARY_SENSORS,
    BLOCK_BINARY_SENSORS,
    RPC_BINARY_SENSORS,
    BleBinarySensorDescription,
    BlockBinarySensorDescription,
    RpcBinarySensorDescription,
)
from .relay_fault import iter_relay_readings

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Shelly Cloud DIY binary sensors."""
    coordinator: ShellyCloudCoordinator = hass.data[DOMAIN][entry.entry_id]
    created_entities: set[str] = set()
    # Tracked separately from ``created_entities`` because the reporting sensor
    # derives nothing from the device status: it exists for every device, even
    # one whose status we cannot read, so it has no per-component key to live
    # under. Both sets are forgotten together when the device is deleted.
    reporting_created: set[str] = set()

    def create_binary_sensors(device_id: str) -> list[BinarySensorEntity]:
        """Create binary sensor entities for a device."""
        entities: list[BinarySensorEntity] = []
        if not coordinator.is_enabled(device_id):
            return entities
        device_data = coordinator.devices.get(device_id, {})
        status = device_data.get("status", {})

        # Offline detection is device-wide, not component-derived, so it is
        # created before the status check below — a device whose status we
        # cannot read yet is exactly one worth watching.
        entities.extend(
            _create_reporting_sensor(device_id, reporting_created, coordinator)
        )

        if not status:
            return entities

        entities.extend(
            _create_relay_fault_sensors(
                device_id, status, created_entities, coordinator
            )
        )

        gen = device_gen(status)
        if gen == "GBLE":
            entities.extend(_create_ble_binary_sensors(
                device_id, status, created_entities, coordinator
            ))
        elif is_gen2_status(status):
            entities.extend(_create_rpc_sensors(
                device_id, status, created_entities, coordinator
            ))
        else:
            entities.extend(_create_block_sensors(
                device_id, status, created_entities, coordinator
            ))

        if entities:
            _LOGGER.info("Created %d binary sensors for %s", len(entities), device_id)

        return entities

    @callback
    def async_add_device(device_id: str) -> None:
        """Add entities for newly discovered device."""
        entities = create_binary_sensors(device_id)
        if entities:
            async_add_entities(entities)

    @callback
    def async_forget_device(device_id: str) -> None:
        """Forget a deleted device so a later rediscovery rebuilds it."""
        for tracked in (created_entities, reporting_created):
            for k in [k for k in tracked if k.startswith(device_id)]:
                tracked.discard(k)

    # Add existing devices
    entities: list[BinarySensorEntity] = []
    for device_id in list(coordinator.devices.keys()):
        entities.extend(create_binary_sensors(device_id))

    if entities:
        async_add_entities(entities)

    # Listen for new devices
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, async_add_device)
    )
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_DEVICE_REMOVED, async_forget_device)
    )


def _create_reporting_sensor(
    device_id: str,
    created: set[str],
    coordinator: ShellyCloudCoordinator,
) -> list[BinarySensorEntity]:
    """Create the device-wide "Reporting" sensor, once per device.

    Unlike the other builders this one derives nothing from the status: every
    device gets exactly one, including devices whose status is empty or whose
    generation we cannot classify. Those are if anything the ones most worth
    watching.
    """
    uid = f"{device_id}_reporting"
    if uid in created:
        return []
    created.add(uid)
    return [ShellyReportingBinarySensor(coordinator, device_id)]


class ShellyReportingBinarySensor(ShellyBaseEntity, BinarySensorEntity):
    """Whether the device is still reporting to Shelly Cloud.

    This is the honest counterpart to the ``Cloud`` binary sensor above.
    That one mirrors ``cloud.connected``, which the cloud caches: measured on
    a live account, it read *connected* for a device that had been physically
    unplugged 13 minutes earlier, and for every device on the account
    including BLE beacons silent for three days. It is a transport flag the
    cloud never revises, not a liveness signal.

    This sensor instead keys off the only thing the cloud cannot fake: that
    the device pushed a new snapshot. The coordinator fingerprints each
    payload and stamps its own monotonic clock when the fingerprint changes,
    then compares that against a per-device staleness window.

    ``on`` (connected) therefore means "checked in recently enough", and
    ``off`` means "silent for longer than this device ever normally is" —
    which is what a power cut looks like from the cloud's side.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = "Reporting"

    def __init__(
        self, coordinator: ShellyCloudCoordinator, device_id: str
    ) -> None:
        """Initialize the reporting sensor."""
        super().__init__(coordinator, device_id, 0)
        self._attr_unique_id = f"{device_id}_reporting"

    @property
    def available(self) -> bool:
        """Return if the verdict itself can be trusted.

        Deliberately *not* the base class's availability, which keys off the
        device being present and online — this entity exists to report
        precisely the case where it is not, and an unavailable entity says
        nothing.

        It does go unavailable when our own polling breaks: during a cloud
        outage or an auth failure we have no evidence about any device, and
        silently reporting the whole fleet as dead would be worse than
        admitting we cannot tell.
        """
        return (
            self.coordinator.last_update_success
            and self._device_id in self.coordinator.checkins
        )

    @property
    def is_on(self) -> bool | None:
        """Return True while the device is still checking in."""
        return self.coordinator.is_reporting(self._device_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the window this verdict was made against.

        Worth surfacing because it is per-device and adapts: a quiet device
        earns a wider window than the configured default, and without this
        the user would have no way to see why one device tolerates far more
        silence than another.
        """
        record = self.coordinator.checkins.get(self._device_id)
        if record is None:
            return None
        return {"stale_after_s": int(record.stale_after_s)}


def _create_relay_fault_sensors(
    device_id: str,
    status: dict[str, Any],
    created: set[str],
    coordinator: ShellyCloudCoordinator,
) -> list[BinarySensorEntity]:
    """Create one "Relay fault" sensor per metered switching channel.

    Built from the status rather than from the generation, because the
    thing that decides whether the question is answerable is not what kind
    of device this is but whether the payload carries a relay *and* the
    meter for that same relay — which is exactly what
    :func:`iter_relay_readings` looks for.

    Created even while the detector is switched off, so that turning the
    option back on does not require a reload to get the entities back. The
    verdict is then simply always ``off``.
    """
    entities: list[BinarySensorEntity] = []
    for reading in iter_relay_readings(status):
        uid = f"{device_id}_relay_fault_{reading.channel}"
        if uid in created:
            continue
        created.add(uid)
        entities.append(
            ShellyRelayFaultBinarySensor(coordinator, device_id, reading.channel)
        )
    return entities


class ShellyRelayFaultBinarySensor(ShellyBaseEntity, BinarySensorEntity):
    """Whether a switching channel's contact has stopped opening.

    ``on`` means the device reported its relay as *open* while its own
    meter reported a real load flowing through it, for longer than any
    post-command settling could explain. In practice that is a welded
    contact: the actuator still accepts commands and still reports ``off``,
    but the load never switches off. See ``relay_fault.py`` for the
    thresholds and the hardware run they came from.

    Availability is the ordinary device availability on purpose. While the
    device is unreachable nobody can observe current flowing through it, so
    claiming a live verdict would be an assertion about a device we cannot
    see. A verdict already raised is retained and reappears with the
    device.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ShellyCloudCoordinator,
        device_id: str,
        channel: int,
    ) -> None:
        """Initialize the relay fault sensor."""
        super().__init__(coordinator, device_id, channel)
        self._attr_unique_id = f"{device_id}_relay_fault_{channel}"
        self._attr_name = (
            "Relay fault" if channel == 0 else f"Relay fault {channel + 1}"
        )

    @property
    def is_on(self) -> bool:
        """Return True while this channel is judged to be stuck closed."""
        return self.coordinator.has_relay_fault(self._device_id, self._channel)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the measured load behind the verdict.

        Only while the channel reports *off*: with the relay closed the
        number is just the ordinary consumption and would invite the reader
        to compare it against a threshold it has nothing to do with.
        """
        power = self.coordinator.relay_fault_power(self._device_id, self._channel)
        if power is None:
            return None
        return {"power_while_off_w": power}


def _create_block_sensors(
    device_id: str,
    status: dict[str, Any],
    created: set[str],
    coordinator: ShellyCloudCoordinator,
) -> list[BinarySensorEntity]:
    """Create Gen1 Block binary sensors."""
    entities: list[BinarySensorEntity] = []

    # Inputs
    for idx, inp in enumerate(status.get("inputs", [])):
        if "input" in inp:
            desc = BLOCK_BINARY_SENSORS.get("input")
            if desc:
                uid = f"{device_id}_input_{idx}"
                if uid not in created:
                    created.add(uid)
                    entities.append(BlockBinarySensor(
                        coordinator, device_id, desc, idx, "inputs", "input"
                    ))

    # Motion
    if "motion" in status:
        desc = BLOCK_BINARY_SENSORS.get("motion")
        if desc:
            uid = f"{device_id}_motion"
            if uid not in created:
                created.add(uid)
                entities.append(BlockBinarySensor(
                    coordinator, device_id, desc, 0, None, "motion"
                ))

    # Door/Window
    sensor = status.get("sensor", {})
    if sensor and "state" in sensor:
        desc = BLOCK_BINARY_SENSORS.get("sensor_state")
        if desc:
            uid = f"{device_id}_door"
            if uid not in created:
                created.add(uid)
                entities.append(BlockBinarySensor(
                    coordinator, device_id, desc, 0, "sensor", "state"
                ))

    # Flood / Smoke — Gen1 leaf sensors carry a bare top-level flag:
    # Shelly Flood (``SHWT-1``) reports ``"flood": false`` and Shelly Smoke
    # (``SHSM-01``) reports ``"smoke": false``, both alongside ``is_valid``
    # and a ``tmp`` block (vendor Gen1 API docs, /status). Both descriptions
    # have existed since the first release but were never wired to a status
    # key, so these devices arrived with their diagnostics and without the one
    # reading they exist for — the same gap the Gen4 flood had in #41.
    for flag in ("flood", "smoke"):
        if flag in status:
            desc = BLOCK_BINARY_SENSORS.get(flag)
            if desc:
                uid = f"{device_id}_{flag}"
                if uid not in created:
                    created.add(uid)
                    entities.append(BlockBinarySensor(
                        coordinator, device_id, desc, 0, None, flag
                    ))

    # Gas alarm
    gas = status.get("gas_sensor", {})
    if gas and "alarm_state" in gas:
        desc = BLOCK_BINARY_SENSORS.get("gas_alarm")
        if desc:
            uid = f"{device_id}_gas_alarm"
            if uid not in created:
                created.add(uid)
                entities.append(BlockBinarySensor(
                    coordinator, device_id, desc, 0, "gas_sensor", "alarm_state"
                ))

    return entities


def _create_rpc_sensors(
    device_id: str,
    status: dict[str, Any],
    created: set[str],
    coordinator: ShellyCloudCoordinator,
) -> list[BinarySensorEntity]:
    """Create Gen2/Gen3 RPC binary sensors."""
    entities: list[BinarySensorEntity] = []

    # Inputs
    for key in status:
        if match := re.match(r"input:(\d+)", key):
            idx = int(match.group(1))
            if "state" in status[key]:
                desc = RPC_BINARY_SENSORS.get("input")
                if desc:
                    uid = f"{device_id}_input_{idx}"
                    if uid not in created:
                        created.add(uid)
                        entities.append(RpcBinarySensor(
                            coordinator, device_id, desc, idx, key, "state"
                        ))

    # Flood alarms (for example Shelly Flood Gen4 / S4SN-0071Z)
    for key, payload in status.items():
        if match := re.fullmatch(r"flood:(\d+)", key):
            idx = int(match.group(1))
            if isinstance(payload, dict) and "alarm" in payload:
                desc = RPC_BINARY_SENSORS.get("flood")
                if desc:
                    uid = f"{device_id}_{key}_alarm"
                    if uid not in created:
                        created.add(uid)
                        entities.append(RpcBinarySensor(
                            coordinator, device_id, desc, idx, key, "alarm"
                        ))

    # Cloud
    if "connected" in status.get("cloud", {}):
        desc = RPC_BINARY_SENSORS.get("cloud")
        if desc:
            uid = f"{device_id}_cloud"
            if uid not in created:
                created.add(uid)
                entities.append(RpcBinarySensor(
                    coordinator, device_id, desc, 0, "cloud", "connected"
                ))

    # Virtual boolean components (READ-ONLY) — Gen2/Gen3 ``boolean:<id>.value``.
    # Exposed read-only for the same reason as the virtual sensors in sensor.py:
    # the cloud status carries no component config (writable-view / name) and the
    # Cloud Control API has no virtual-component write method. (#9)
    for key in status:
        if match := re.match(r"boolean:(\d+)", key):
            idx = int(match.group(1))
            data = status[key]
            if isinstance(data, dict) and "value" in data:
                uid = f"{device_id}_{key}_value"
                if uid not in created:
                    created.add(uid)
                    entities.append(RpcVirtualBinarySensor(
                        coordinator, device_id, idx, key
                    ))

    return entities


class RpcVirtualBinarySensor(ShellyBaseEntity, BinarySensorEntity):
    """Read-only Gen2/Gen3 virtual boolean component (``boolean:<id>``).

    Mirrors the live ``value`` as a plain binary sensor. The user-set name is
    fetched lazily via the v2 settings endpoint and cached on the coordinator;
    until it arrives (or if it is absent) the entity falls back to a generic
    name (e.g. "Boolean 200"). Read-only: the cloud status has no writable-view
    flag and the Cloud Control API exposes no ``Boolean.Set``. (#9)
    """

    def __init__(
        self,
        coordinator: ShellyCloudCoordinator,
        device_id: str,
        comp_id: int,
        component_key: str,
    ) -> None:
        """Initialize the virtual boolean binary sensor."""
        super().__init__(coordinator, device_id, 0)
        self._component_key = component_key
        self._attr_unique_id = f"{device_id}_{component_key}_value"
        # Generic fallback; the real name is a PROPERTY because the v2 config
        # arrives via a background task after the entity is created. (#9)
        self._generic_name = f"Boolean {comp_id}"

    @property
    def name(self) -> str:
        """Return the resolved component name, or the generic fallback.

        On irrigation controllers this is the zone name from ``service:0``;
        otherwise the name set in the Shelly app. (#20)
        """
        return self.virtual_component_name(self._component_key) or self._generic_name

    @property
    def is_on(self) -> bool | None:
        """Return the virtual boolean's current value."""
        component = self.device_status.get(self._component_key)
        if not isinstance(component, dict):
            return None
        value = component.get("value")
        if value is None:
            return None
        return bool(value)


def _create_ble_binary_sensors(
    device_id: str,
    status: dict[str, Any],
    created: set[str],
    coordinator: ShellyCloudCoordinator,
) -> list[BinarySensorEntity]:
    """Create binary sensors for BLE / Shelly-BLU-Gateway-bridged devices.

    Mirrors ``_create_ble_sensors`` in ``sensor.py``: iterate every
    ``<type>:<channel>`` status key and look up the BLE_BINARY_SENSORS
    table. Unknown types are skipped so we do not invent entities that
    will always be ``unknown``.
    """
    entities: list[BinarySensorEntity] = []

    for key, payload in status.items():
        if not isinstance(payload, dict):
            continue
        if ":" not in key:
            continue
        sensor_type, _, channel_s = key.partition(":")
        if not channel_s.isdigit():
            continue
        channel = int(channel_s)

        desc = BLE_BINARY_SENSORS.get(sensor_type)
        if desc is None:
            continue
        if desc.value_field not in payload:
            continue

        uid = f"{device_id}_ble_{sensor_type}_{channel}"
        if uid in created:
            continue
        created.add(uid)
        entities.append(
            BleBinarySensor(
                coordinator=coordinator,
                device_id=device_id,
                description=desc,
                sensor_type=sensor_type,
                channel=channel,
            )
        )

    return entities


class BleBinarySensor(ShellyBaseEntity, BinarySensorEntity):
    """BLE / Shelly-BLU-Gateway-bridged binary sensor.

    Reads ``<sensor_type>:<channel>``-shaped status keys (e.g.
    ``moisture_alarm:0``) and interprets the ``value_field`` payload as
    a boolean.
    """

    def __init__(
        self,
        *,
        coordinator: ShellyCloudCoordinator,
        device_id: str,
        description: BleBinarySensorDescription,
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

    @property
    def is_on(self) -> bool | None:
        """Return true if the BLE binary sensor is tripped."""
        payload = self.device_status.get(self._status_key)
        if not isinstance(payload, dict):
            return None
        value = payload.get(self._description.value_field)
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value > 0
        return None


class BlockBinarySensor(ShellyBaseEntity, BinarySensorEntity):
    """Gen1 Block binary sensor."""

    def __init__(
        self,
        coordinator: ShellyCloudCoordinator,
        device_id: str,
        description: BlockBinarySensorDescription,
        channel: int,
        status_key: str | None,
        attr_key: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, channel)
        self._description = description
        self._status_key = status_key
        self._attr_key = attr_key

        self._attr_unique_id = f"{device_id}_{description.key}_{channel}"
        name = description.name or "Binary Sensor"
        self._attr_name = name if channel == 0 else f"{name} {channel + 1}"

        if description.device_class:
            self._attr_device_class = description.device_class
        if description.entity_category:
            self._attr_entity_category = description.entity_category
        if description.icon:
            self._attr_icon = description.icon

    @property
    def is_on(self) -> bool | None:
        """Return true if sensor is on."""
        status = self.device_status

        if self._status_key is None:
            value = status.get(self._attr_key)
        else:
            container = status.get(self._status_key)
            if container is None:
                return None
            if isinstance(container, list):
                if self._channel >= len(container):
                    return None
                container = container[self._channel]
            value = container.get(self._attr_key) if isinstance(container, dict) else None

        if value is None:
            return None

        if self._description.value_fn:
            return self._description.value_fn(value)

        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value > 0

        return None


class RpcBinarySensor(ShellyBaseEntity, BinarySensorEntity):
    """Gen2/Gen3 RPC binary sensor."""

    def __init__(
        self,
        coordinator: ShellyCloudCoordinator,
        device_id: str,
        description: RpcBinarySensorDescription,
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
        name = description.name or "Binary Sensor"
        self._attr_name = name if channel == 0 else f"{name} {channel + 1}"

        if description.device_class:
            self._attr_device_class = description.device_class
        if description.entity_category:
            self._attr_entity_category = description.entity_category
        self._attr_entity_registry_enabled_default = (
            description.entity_registry_enabled_default
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if sensor is on."""
        component = self.device_status.get(self._component_key)
        if component is None:
            return None

        value = component.get(self._attr_key)
        if value is None:
            return None

        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value > 0

        return None
