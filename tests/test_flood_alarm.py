"""Unit tests for the Gen2+ flood alarm binary sensor (issue #41, PR #40).

A Shelly Flood S Gen4 (S4SN-0071Z) reports its wet/dry state as a first-class
RPC component, ``flood:<id> = {"alarm": bool, ...}``. Before PR #40 the RPC
creation loop knew nothing about it, so the device surfaced only its battery
and reporting diagnostics — the one reading it exists for was missing.

These tests drive the real creation loop (``_create_rpc_sensors``) and the real
``RpcBinarySensor`` value logic with a lightweight fake coordinator, so they run
identically against any HA version.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.shelly_cloud_diy.binary_sensor import (
    RpcBinarySensor,
    _create_rpc_sensors,
)
from custom_components.shelly_cloud_diy.const import is_gen2_status

DEVICE_ID = "a0dd6cffee01"


class _FakeCoordinator:
    """Minimal coordinator: entities only read ``.devices[id]['status']``."""

    def __init__(self, device_id: str, status: dict[str, Any]) -> None:
        self.devices = {
            device_id: {
                "status": status,
                "device_code": "S4SN-0071Z",
                "online": True,
            }
        }
        self.data = self.devices
        self.last_update_success = True


def _flood_status(alarm: Any = False) -> dict[str, Any]:
    """The shape reported for a Flood S Gen4 (issue #41)."""
    return {
        "sys": {"mac": "A0DD6CFFEE01"},
        "cloud": {"connected": True},
        "flood:0": {"id": 0, "alarm": alarm, "mute": False},
        "devicepower:0": {"battery": {"V": 5.9, "percent": 100}},
    }


def _build(status: dict[str, Any], created: set[str] | None = None):
    coord = _FakeCoordinator(DEVICE_ID, status)
    entities = _create_rpc_sensors(
        DEVICE_ID, status, set() if created is None else created, coord
    )
    return entities, {e.unique_id: e for e in entities}


def test_flood_device_is_routed_to_the_rpc_builders():
    """Detection must not lean on the incidental ``devicepower:0``."""
    assert is_gen2_status({"flood:0": {"alarm": False}}) is True
    assert is_gen2_status(_flood_status()) is True


def test_flood_alarm_entity_is_created():
    entities, by_uid = _build(_flood_status())

    uid = f"{DEVICE_ID}_flood:0_alarm"
    assert uid in by_uid, [e.unique_id for e in entities]

    entity = by_uid[uid]
    assert isinstance(entity, RpcBinarySensor)
    assert entity.device_class is BinarySensorDeviceClass.MOISTURE
    assert entity.name == "Flood"
    assert entity.entity_registry_enabled_default is True


def test_alarm_value_is_mirrored():
    _, dry = _build(_flood_status(alarm=False))
    assert dry[f"{DEVICE_ID}_flood:0_alarm"].is_on is False

    _, wet = _build(_flood_status(alarm=True))
    assert wet[f"{DEVICE_ID}_flood:0_alarm"].is_on is True


def test_component_without_alarm_creates_nothing():
    """No reading yet — an entity that could only ever be ``None``."""
    status = _flood_status()
    status["flood:0"] = {"id": 0, "mute": False}
    _, by_uid = _build(status)
    assert f"{DEVICE_ID}_flood:0_alarm" not in by_uid


def test_second_channel_gets_an_indexed_name():
    status = _flood_status()
    status["flood:1"] = {"id": 1, "alarm": True}
    _, by_uid = _build(status)

    assert by_uid[f"{DEVICE_ID}_flood:0_alarm"].name == "Flood"
    assert by_uid[f"{DEVICE_ID}_flood:1_alarm"].name == "Flood 2"


def test_already_created_uid_is_not_duplicated():
    seen = {f"{DEVICE_ID}_flood:0_alarm"}
    entities, _ = _build(_flood_status(), created=seen)
    assert all(e.unique_id != f"{DEVICE_ID}_flood:0_alarm" for e in entities)


def test_unindexed_flood_key_is_ignored():
    """The Gen1 top-level ``flood`` flag must not reach the RPC builder."""
    _, by_uid = _build({"sys": {}, "flood": True, "switch:0": {"output": False}})
    assert not any("flood" in uid for uid in by_uid)
