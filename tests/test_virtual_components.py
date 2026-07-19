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
    def __init__(
        self,
        device_id: str,
        status: dict[str, Any],
        virtual_configs: dict[str, dict[str, dict]] | None = None,
    ) -> None:
        self.devices = {device_id: {"status": status, "device_code": "SAWD-0A1XX10EU1", "online": True}}
        self.data = self.devices
        self.last_update_success = True
        # Only set when a test wants to exercise config enrichment; leaving it
        # unset also exercises the ``getattr(..., None)`` None-safety path.
        if virtual_configs is not None:
            self.virtual_configs = virtual_configs


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


def _build(status: dict[str, Any], virtual_configs=None):
    coord = _FakeCoordinator(DEVICE_ID, status, virtual_configs)
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
    assert n.name == "Number 201"
    assert n.native_value == 26.1

    e = by_uid[f"{DEVICE_ID}_enum:200_value"]
    assert e.name == "Enum 200"
    assert e.native_value == "cool"

    t = by_uid[f"{DEVICE_ID}_text:200_value"]
    assert t.name == "Text 200"
    assert t.native_value == "SCRIPT READY"
    # Read-only virtual sensors carry no wrong statistics metadata.
    assert n.state_class is None and n.device_class is None


def test_boolean_becomes_readonly_binary_sensor():
    _s, binaries, by_uid = _build(_VCOMP_STATUS)
    virtual = [e for e in binaries if isinstance(e, RpcVirtualBinarySensor)]
    assert len(virtual) == 2
    assert by_uid[f"{DEVICE_ID}_boolean:200_value"].name == "Boolean 200"
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


# ── v2-config enrichment (issue #9, part 3) ─────────────────────────────────
# The v2 settings endpoint returns per-virtual-component config keyed exactly
# like the status keys. Config shape derived from the native HA Shelly
# integration (utils.get_virtual_component_unit / get_rpc_custom_name,
# entity.py enum options+titles), not live-confirmed on an account with
# virtual comps.
_VCOMP_CONFIG: dict[str, dict[str, dict]] = {
    DEVICE_ID: {
        "number:200": {"name": "Target Temp", "meta": {"ui": {"unit": "°C"}}},
        "number:201": {"name": None, "meta": {"ui": {}}},  # null name → generic
        "number:202": {"meta": {"ui": {"unit": ""}}},       # empty unit → None
        "enum:200": {
            "name": "HVAC Mode",
            "options": ["cool", "heat", "auto"],
            "meta": {"ui": {"titles": {"cool": "Cooling", "heat": "Heating"}}},
        },
        "text:200": {"name": "Script Status"},
        "boolean:200": {"name": "Away Flag"},
    }
}


def test_enriched_name_when_config_present():
    _s, binaries, by_uid = _build(_VCOMP_STATUS, _VCOMP_CONFIG)
    assert by_uid[f"{DEVICE_ID}_number:200_value"].name == "Target Temp"
    assert by_uid[f"{DEVICE_ID}_enum:200_value"].name == "HVAC Mode"
    assert by_uid[f"{DEVICE_ID}_text:200_value"].name == "Script Status"
    assert by_uid[f"{DEVICE_ID}_boolean:200_value"].name == "Away Flag"


def test_fallback_generic_name_when_config_absent():
    # No virtual_configs on the coordinator at all → generic names, no crash.
    _s, _b, by_uid = _build(_VCOMP_STATUS)
    assert by_uid[f"{DEVICE_ID}_number:200_value"].name == "Number 200"
    assert by_uid[f"{DEVICE_ID}_boolean:200_value"].name == "Boolean 200"
    # Null / missing name in config also falls back to generic.
    _s2, _b2, by_uid2 = _build(_VCOMP_STATUS, _VCOMP_CONFIG)
    assert by_uid2[f"{DEVICE_ID}_number:201_value"].name == "Number 201"


def test_number_unit_from_meta_ui_unit():
    _s, _b, by_uid = _build(_VCOMP_STATUS, _VCOMP_CONFIG)
    assert by_uid[f"{DEVICE_ID}_number:200_value"].native_unit_of_measurement == "°C"
    # Empty-string unit → None (no bogus unit).
    assert by_uid[f"{DEVICE_ID}_number:202_value"].native_unit_of_measurement is None
    # Enum/text carry no unit even with config present.
    assert by_uid[f"{DEVICE_ID}_enum:200_value"].native_unit_of_measurement is None


def test_enum_options_attribute():
    _s, _b, by_uid = _build(_VCOMP_STATUS, _VCOMP_CONFIG)
    attrs = by_uid[f"{DEVICE_ID}_enum:200_value"].extra_state_attributes
    assert attrs["options"] == ["cool", "heat", "auto"]
    assert attrs["titles"] == {"cool": "Cooling", "heat": "Heating"}
    # Enum with no config entry → no attributes, no crash.
    assert by_uid[f"{DEVICE_ID}_enum:201_value"].extra_state_attributes is None
    # A number is not an enum → never emits options attributes.
    assert by_uid[f"{DEVICE_ID}_number:200_value"].extra_state_attributes is None


def test_none_safety_partial_and_missing_config():
    # Coordinator has virtual_configs but not for this device / component.
    _s, _b, by_uid = _build(_VCOMP_STATUS, {"other-device": {"number:200": {"name": "X"}}})
    n = by_uid[f"{DEVICE_ID}_number:200_value"]
    assert n.name == "Number 200"
    assert n.native_unit_of_measurement is None
    # Malformed config sub-trees must not raise.
    broken = {DEVICE_ID: {
        "number:200": {"meta": "not-a-dict"},
        "enum:200": {"options": "not-a-list", "meta": {"ui": "nope"}},
    }}
    _s2, _b2, by_uid2 = _build(_VCOMP_STATUS, broken)
    assert by_uid2[f"{DEVICE_ID}_number:200_value"].native_unit_of_measurement is None
    assert by_uid2[f"{DEVICE_ID}_number:200_value"].name == "Number 200"
    assert by_uid2[f"{DEVICE_ID}_enum:200_value"].extra_state_attributes is None
