"""Unit tests for read-only virtual components (issue #9, part 2).

Confirmed cloud-status shapes (from @LEDuser's diagnostics): virtual components
surface their live value under ``<type>:<id>.value`` — ``boolean:200 {"value":
false}``, ``number:200 {"value": 24}``, ``enum:200 {"value": "cool"}``,
``text:200 {"value": "SCRIPT READY"}``. ``button:200 {}`` has no value (it is an
action) and must NOT create an entity. These tests drive the real RPC creation
loops with a fake coordinator — no running HA required.
"""
from __future__ import annotations

from typing import Any

from custom_components.shelly_cloud_diy.binary_sensor import (
    RpcVirtualBinarySensor,
    _create_rpc_sensors as create_rpc_binary_sensors,
)
from custom_components.shelly_cloud_diy.const import is_gen2_status
from custom_components.shelly_cloud_diy.sensor import (
    RpcVirtualSensor,
    _create_rpc_sensors as create_rpc_sensors,
)

DEVICE_ID = "wall01display"


class _FakeCoordinator:
    def __init__(self, device_id: str, status: dict[str, Any]) -> None:
        self.devices = {device_id: {"status": status, "device_code": "SAWD-0A1XX10EU1", "online": True}}
        self.data = self.devices
        self.last_update_success = True


# The exact status LEDuser reported (device 1), plus sys so it reads as Gen2.
_VCOMP_STATUS: dict[str, Any] = {
    "sys": {"mac": "AABBCC"},
    "boolean:200": {"value": False},
    "boolean:201": {"value": True},
    "number:200": {"value": 24},
    "number:201": {"value": 26.1},
    "number:202": {"value": 52},
    "enum:200": {"value": "cool"},
    "enum:201": {"value": "auto"},
    "enum:202": {"value": "middle"},
    "text:200": {"value": "SCRIPT READY"},
    "vcomps": [
        "boolean:200", "boolean:201", "enum:200", "enum:201", "enum:202",
        "number:200", "number:201", "number:202", "text:200",
    ],
}


def _build(status: dict[str, Any]):
    coord = _FakeCoordinator(DEVICE_ID, status)
    sensors = create_rpc_sensors(DEVICE_ID, status, set(), coord)
    binaries = create_rpc_binary_sensors(DEVICE_ID, status, set(), coord)
    by_uid = {e.unique_id: e for e in [*sensors, *binaries]}
    return sensors, binaries, by_uid


def test_gen2_detection_includes_virtual_types():
    assert is_gen2_status({"number:200": {"value": 1}}) is True
    assert is_gen2_status({"boolean:200": {"value": True}}) is True
    assert is_gen2_status({"text:200": {"value": "x"}}) is True


def test_number_enum_text_become_readonly_sensors():
    sensors, _b, by_uid = _build(_VCOMP_STATUS)
    virtual = [e for e in sensors if isinstance(e, RpcVirtualSensor)]
    assert len(virtual) == 7  # number(3) + enum(3) + text(1)

    n = by_uid[f"{DEVICE_ID}_number:201_value"]
    assert n._attr_name == "Number 201"
    assert n.native_value == 26.1

    e = by_uid[f"{DEVICE_ID}_enum:200_value"]
    assert e._attr_name == "Enum 200"
    assert e.native_value == "cool"

    t = by_uid[f"{DEVICE_ID}_text:200_value"]
    assert t._attr_name == "Text 200"
    assert t.native_value == "SCRIPT READY"
    # Read-only virtual sensors carry no wrong statistics metadata.
    assert n.state_class is None and n.device_class is None


def test_boolean_becomes_readonly_binary_sensor():
    _s, binaries, by_uid = _build(_VCOMP_STATUS)
    virtual = [e for e in binaries if isinstance(e, RpcVirtualBinarySensor)]
    assert len(virtual) == 2
    assert by_uid[f"{DEVICE_ID}_boolean:200_value"]._attr_name == "Boolean 200"
    assert by_uid[f"{DEVICE_ID}_boolean:200_value"].is_on is False
    assert by_uid[f"{DEVICE_ID}_boolean:201_value"].is_on is True


def test_button_creates_no_entity():
    """A virtual Button has no value (it is an action) — nothing to expose."""
    status = {"sys": {"mac": "x"}, "button:200": {}, "v_eve:200": {"ev": "", "ttl": -1}}
    sensors, binaries, _ = _build(status)
    assert not any(isinstance(e, RpcVirtualSensor) for e in sensors)
    assert not any(isinstance(e, RpcVirtualBinarySensor) for e in binaries)


def test_presence_gated_missing_value_creates_nothing():
    status = {"sys": {"mac": "x"}, "number:200": {}, "boolean:200": {}}
    sensors, binaries, _ = _build(status)
    assert not any(isinstance(e, RpcVirtualSensor) for e in sensors)
    assert not any(isinstance(e, RpcVirtualBinarySensor) for e in binaries)


def test_unique_ids_are_distinct():
    _s, _b, by_uid = _build(_VCOMP_STATUS)
    vuids = [u for u in by_uid if u.endswith("_value")]
    assert len(vuids) == len(set(vuids)) == 9  # 7 sensors + 2 booleans


def test_boolean_numeric_coercion():
    status = {"sys": {"mac": "x"}, "boolean:200": {"value": 1}, "boolean:201": {"value": 0}}
    _s, _b, by_uid = _build(status)
    assert by_uid[f"{DEVICE_ID}_boolean:200_value"].is_on is True
    assert by_uid[f"{DEVICE_ID}_boolean:201_value"].is_on is False
