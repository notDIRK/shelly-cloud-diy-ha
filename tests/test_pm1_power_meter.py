"""Unit tests for the power-meter component (``pm1:<id>``).

Used by the dedicated meters (Shelly PM Mini Gen3) and by metered channels of
other Gen3 devices. Two things make it unlike the energy meters, and both are
what these tests pin down:

* the field names differ — power is ``apower``, not ``act_power``;
* the energy counters are *nested* dicts (``aenergy.total`` /
  ``ret_aenergy.total``), not flat values, and there is no ``pm1data``
  component to hold them.

Shapes taken from the native HA Shelly integration (``sensor.py``, ``*_pm1``).
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.shelly_cloud_diy.const import is_gen2_status
from custom_components.shelly_cloud_diy.sensor import (
    _create_rpc_sensors as create_rpc_sensors,
)

DEVICE_ID = "pmmini3a1b2c3"


class _FakeCoordinator:
    def __init__(self, device_id: str, status: dict[str, Any]) -> None:
        self.devices = {device_id: {"status": status, "device_code": "S3PM-001PCEU16", "online": True}}
        self.data = self.devices
        self.last_update_success = True


# A PM Mini Gen3: a pure meter — no relay, no device temperature.
_PM_MINI_STATUS: dict[str, Any] = {
    "cloud": {"connected": True},
    "pm1:0": {
        "id": 0,
        "voltage": 234.7,
        "current": 0.322,
        "apower": 55.0,
        "freq": 50.0,
        "aenergy": {"total": 1234.5, "by_minute": [0.0], "minute_ts": 1754300000},
        "ret_aenergy": {"total": 12.0, "by_minute": [0.0], "minute_ts": 1754300000},
        "calibration": "factory",
    },
}


def _build(status: dict[str, Any]):
    coord = _FakeCoordinator(DEVICE_ID, status)
    sensors = create_rpc_sensors(DEVICE_ID, status, set(), coord)
    return sensors, {e.unique_id: e for e in sensors}


def test_pure_meter_is_recognised_as_gen2():
    """Without this the RPC builders never run and the device stays empty."""
    assert is_gen2_status(_PM_MINI_STATUS) is True
    assert is_gen2_status({"pm1:0": {}}) is True


def test_all_six_measurements_become_entities():
    sensors, by_uid = _build(_PM_MINI_STATUS)
    assert len(sensors) == 6
    for attr in ("apower", "voltage", "current", "freq", "aenergy", "ret_aenergy"):
        assert f"{DEVICE_ID}_pm1:0_{attr}" in by_uid


def test_flat_measurements_read_through():
    _s, by_uid = _build(_PM_MINI_STATUS)
    assert by_uid[f"{DEVICE_ID}_pm1:0_apower"].native_value == 55.0
    assert by_uid[f"{DEVICE_ID}_pm1:0_voltage"].native_value == 234.7
    assert by_uid[f"{DEVICE_ID}_pm1:0_current"].native_value == 0.322
    assert by_uid[f"{DEVICE_ID}_pm1:0_freq"].native_value == 50.0


def test_nested_energy_counters_are_unwrapped():
    """``aenergy`` is a dict — the entity must report ``total``, not the dict."""
    _s, by_uid = _build(_PM_MINI_STATUS)
    assert by_uid[f"{DEVICE_ID}_pm1:0_aenergy"].native_value == 1234.5
    assert by_uid[f"{DEVICE_ID}_pm1:0_ret_aenergy"].native_value == 12.0


def test_energy_counters_carry_statistics_metadata():
    _s, by_uid = _build(_PM_MINI_STATUS)
    e = by_uid[f"{DEVICE_ID}_pm1:0_aenergy"]
    assert e.device_class is SensorDeviceClass.ENERGY
    assert e.state_class is SensorStateClass.TOTAL_INCREASING
    p = by_uid[f"{DEVICE_ID}_pm1:0_apower"]
    assert p.device_class is SensorDeviceClass.POWER
    assert p.state_class is SensorStateClass.MEASUREMENT


def test_malformed_nested_counter_yields_none_not_a_crash():
    status = {"pm1:0": {"apower": 1.0, "aenergy": "not-a-dict", "ret_aenergy": {}}}
    _s, by_uid = _build(status)
    assert by_uid[f"{DEVICE_ID}_pm1:0_aenergy"].native_value is None
    assert by_uid[f"{DEVICE_ID}_pm1:0_ret_aenergy"].native_value is None


def test_second_channel_is_named_and_kept_apart():
    status = {
        "pm1:0": {"apower": 10.0},
        "pm1:1": {"apower": 20.0},
    }
    _s, by_uid = _build(status)
    assert by_uid[f"{DEVICE_ID}_pm1:0_apower"].name == "Power"
    assert by_uid[f"{DEVICE_ID}_pm1:1_apower"].name == "Power 2"
    assert by_uid[f"{DEVICE_ID}_pm1:1_apower"].native_value == 20.0


def test_missing_fields_are_skipped():
    status = {"pm1:0": {"apower": 5.0, "voltage": None}}
    sensors, by_uid = _build(status)
    assert len(sensors) == 1
    assert f"{DEVICE_ID}_pm1:0_voltage" not in by_uid


def test_pm1_does_not_collide_with_em1():
    """``pm1`` and ``em1`` must stay separate components."""
    status = {
        "pm1:0": {"apower": 5.0},
        "em1:0": {"act_power": 7.0},
    }
    _s, by_uid = _build(status)
    assert by_uid[f"{DEVICE_ID}_pm1:0_apower"].native_value == 5.0
    assert by_uid[f"{DEVICE_ID}_em1:0_act_power"].native_value == 7.0
