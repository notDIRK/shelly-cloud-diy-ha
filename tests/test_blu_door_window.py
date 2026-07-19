"""Unit tests for Shelly BLU Door/Window entities (issue #9, part 1).

A BLU Door/Window sensor bridged via a BLU gateway (``_dev_info.gen == "GBLE"``)
reports ``window:0 = {"open": bool}`` and ``tilt:0 = {"angle": deg}`` in the
cloud status snapshot. These tests exercise the real production code paths
(``_create_ble_sensors`` / ``_create_ble_binary_sensors`` and the ``BleSensor`` /
``BleBinarySensor`` value logic) with a lightweight fake coordinator — no running
Home Assistant instance required, so they run identically against any HA version.
"""
from __future__ import annotations

from typing import Any

import pytest

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorStateClass

from custom_components.shelly_cloud_diy.binary_sensor import (
    BleBinarySensor,
    _create_ble_binary_sensors,
)
from custom_components.shelly_cloud_diy.sensor import BleSensor, _create_ble_sensors

DEVICE_ID = "abc123def456"


class _FakeCoordinator:
    """Minimal coordinator: entities only read ``.devices[id]['status']``."""

    def __init__(self, device_id: str, status: dict[str, Any]) -> None:
        self.devices = {
            device_id: {
                "status": status,
                "device_code": "SBDW-002C",
                "online": True,
            }
        }
        self.data = self.devices
        self.last_update_success = True


def _build(status: dict[str, Any]):
    """Run both BLE creation loops against a status and index by unique_id."""
    coord = _FakeCoordinator(DEVICE_ID, status)
    sensors = _create_ble_sensors(DEVICE_ID, status, set(), coord)
    binaries = _create_ble_binary_sensors(DEVICE_ID, status, set(), coord)
    by_uid = {e.unique_id: e for e in [*sensors, *binaries]}
    return sensors, binaries, by_uid


def _full_blu_dw_status(open_state: Any = True, angle: Any = 37) -> dict[str, Any]:
    return {
        "window:0": {"open": open_state},
        "tilt:0": {"angle": angle},
        "illuminance:0": {"lux": 12},
        "devicepower:0": {"battery": {"percent": 88, "V": 3.0}},
        "_dev_info": {"gen": "GBLE"},
    }


# ── Part A: the two new entities appear, with correct metadata ──────────────


def test_window_binary_sensor_created_with_opening_class():
    _s, _b, by_uid = _build(_full_blu_dw_status())
    win = by_uid[f"{DEVICE_ID}_ble_window_0"]
    assert isinstance(win, BleBinarySensor)
    assert win.device_class == BinarySensorDeviceClass.OPENING
    assert win._attr_name == "Door/Window"
    assert win.is_on is True


def test_tilt_sensor_created_with_degrees_and_no_device_class():
    _s, _b, by_uid = _build(_full_blu_dw_status(angle=37))
    tilt = by_uid[f"{DEVICE_ID}_ble_tilt_0"]
    assert isinstance(tilt, BleSensor)
    assert tilt.native_value == 37
    assert tilt.native_unit_of_measurement == "°"
    assert tilt.state_class == SensorStateClass.MEASUREMENT
    assert tilt.device_class is None  # HA has no device_class for a raw angle
    assert tilt._attr_name == "Tilt"


def test_no_regression_battery_and_illuminance_still_created():
    _s, _b, by_uid = _build(_full_blu_dw_status())
    assert f"{DEVICE_ID}_ble_illuminance_0" in by_uid
    assert f"{DEVICE_ID}_ble_battery_percent" in by_uid
    assert f"{DEVICE_ID}_ble_battery_voltage" in by_uid


# ── open-state coercion (bool AND numeric 0/1) ──────────────────────────────


@pytest.mark.parametrize(
    "open_state,expected",
    [(True, True), (False, False), (1, True), (0, False)],
)
def test_window_open_coercion(open_state, expected):
    _s, _b, by_uid = _build(_full_blu_dw_status(open_state=open_state))
    assert by_uid[f"{DEVICE_ID}_ble_window_0"].is_on is expected


# ── null / missing handling ─────────────────────────────────────────────────


def test_tilt_null_angle_returns_none():
    _s, _b, by_uid = _build(_full_blu_dw_status(angle=None))
    assert by_uid[f"{DEVICE_ID}_ble_tilt_0"].native_value is None


def test_absent_fields_create_no_entities():
    """Presence-gated: empty component dicts must not spawn phantom entities."""
    status = {"window:0": {}, "tilt:0": {}, "_dev_info": {"gen": "GBLE"}}
    sensors, binaries, _ = _build(status)
    assert not any(e.unique_id.endswith("_ble_tilt_0") for e in sensors)
    assert not any(e.unique_id.endswith("_ble_window_0") for e in binaries)


def test_unrelated_key_does_not_create_window_or_tilt():
    status = {"illuminance:0": {"lux": 5}, "_dev_info": {"gen": "GBLE"}}
    sensors, binaries, _ = _build(status)
    assert not any("_ble_window_" in e.unique_id for e in binaries)
    assert not any("_ble_tilt_" in e.unique_id for e in sensors)
    # illuminance itself is unaffected
    assert any(e.unique_id.endswith("_ble_illuminance_0") for e in sensors)


# ── multi-channel naming / uniqueness ───────────────────────────────────────


def test_multichannel_window_and_tilt():
    status = {
        "window:0": {"open": True},
        "window:1": {"open": False},
        "tilt:0": {"angle": 5},
        "tilt:1": {"angle": 90},
        "_dev_info": {"gen": "GBLE"},
    }
    _s, _b, by_uid = _build(status)
    assert by_uid[f"{DEVICE_ID}_ble_window_1"]._attr_name == "Door/Window 2"
    assert by_uid[f"{DEVICE_ID}_ble_tilt_1"]._attr_name == "Tilt 2"
    assert by_uid[f"{DEVICE_ID}_ble_window_1"].is_on is False
    assert by_uid[f"{DEVICE_ID}_ble_tilt_1"].native_value == 90
    # all four unique_ids are distinct
    assert len({
        f"{DEVICE_ID}_ble_window_0",
        f"{DEVICE_ID}_ble_window_1",
        f"{DEVICE_ID}_ble_tilt_0",
        f"{DEVICE_ID}_ble_tilt_1",
    } & set(by_uid)) == 4
