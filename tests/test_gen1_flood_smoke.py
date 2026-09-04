"""Unit tests for the Gen1 flood and smoke alarms.

Gen1 leaf sensors carry a bare top-level flag rather than a component. Per the
vendor Gen1 API docs, a Shelly Flood (``SHWT-1``) ``/status`` reads
``{"is_valid": true, "flood": false, "tmp": {...}, ...}`` and a Shelly Smoke
(``SHSM-01``) the same with ``"smoke"``.

Both descriptions existed in ``BLOCK_BINARY_SENSORS`` from the first release
but were never wired to a status key, so these devices arrived with their
diagnostics and no alarm entity — the Gen1 half of #41.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.shelly_cloud_diy.binary_sensor import (
    BlockBinarySensor,
    _create_block_sensors,
)
from custom_components.shelly_cloud_diy.const import is_gen2_status

DEVICE_ID = "3494546a1b2c"


class _FakeCoordinator:
    """Minimal coordinator: entities only read ``.devices[id]['status']``."""

    def __init__(self, device_id: str, status: dict[str, Any], code: str) -> None:
        self.devices = {
            device_id: {"status": status, "device_code": code, "online": True}
        }
        self.data = self.devices
        self.last_update_success = True


def _flood_status(flood: Any = False) -> dict[str, Any]:
    """Shelly Flood ``SHWT-1`` /status, as documented by Shelly."""
    return {
        "is_valid": True,
        "flood": flood,
        "tmp": {"value": 20, "units": "C", "tC": 20, "tF": 68, "is_valid": True},
        "bat": {"value": 88, "voltage": 2.9},
        "cloud": {"enabled": True, "connected": True},
        "wifi_sta": {"connected": True},
    }


def _smoke_status(smoke: Any = False) -> dict[str, Any]:
    """Shelly Smoke ``SHSM-01`` /status, as documented by Shelly."""
    status = _flood_status()
    del status["flood"]
    status["smoke"] = smoke
    return status


def _build(status: dict[str, Any], code: str = "SHWT-1", created: set[str] | None = None):
    coord = _FakeCoordinator(DEVICE_ID, status, code)
    entities = _create_block_sensors(
        DEVICE_ID, status, set() if created is None else created, coord
    )
    return entities, {e.unique_id: e for e in entities}


def test_gen1_leaf_sensors_stay_on_the_block_path():
    """Guard the routing: neither status may be read as Gen2."""
    assert is_gen2_status(_flood_status()) is False
    assert is_gen2_status(_smoke_status()) is False


def test_flood_alarm_entity_is_created():
    entities, by_uid = _build(_flood_status())

    uid = f"{DEVICE_ID}_flood_0"
    assert uid in by_uid, [e.unique_id for e in entities]

    entity = by_uid[uid]
    assert isinstance(entity, BlockBinarySensor)
    assert entity.device_class is BinarySensorDeviceClass.MOISTURE
    assert entity.name == "Flood"


def test_smoke_alarm_entity_is_created():
    _, by_uid = _build(_smoke_status(), code="SHSM-01")

    uid = f"{DEVICE_ID}_smoke_0"
    assert uid in by_uid
    assert by_uid[uid].device_class is BinarySensorDeviceClass.SMOKE
    assert by_uid[uid].name == "Smoke"


def test_alarm_values_are_mirrored():
    _, dry = _build(_flood_status(flood=False))
    assert dry[f"{DEVICE_ID}_flood_0"].is_on is False

    _, wet = _build(_flood_status(flood=True))
    assert wet[f"{DEVICE_ID}_flood_0"].is_on is True

    _, smoking = _build(_smoke_status(smoke=True), code="SHSM-01")
    assert smoking[f"{DEVICE_ID}_smoke_0"].is_on is True


def test_a_device_without_the_flag_gets_no_alarm_entity():
    """A Gen1 relay must not grow a flood or smoke sensor."""
    relay = {
        "relays": [{"ison": False}],
        "meters": [{"power": 0.0, "total": 12}],
        "cloud": {"enabled": True, "connected": True},
    }
    _, by_uid = _build(relay, code="SHSW-1")
    assert not any(uid.endswith(("_flood_0", "_smoke_0")) for uid in by_uid)


def test_already_created_uid_is_not_duplicated():
    seen = {f"{DEVICE_ID}_flood"}
    entities, _ = _build(_flood_status(), created=seen)
    assert all(e.unique_id != f"{DEVICE_ID}_flood_0" for e in entities)


def test_temperature_block_is_untouched():
    """The flag sits next to ``tmp`` — creating it must not shadow anything."""
    entities, _ = _build(_flood_status(flood=True))
    assert len(entities) == 1
