"""Unit tests for single-phase energy-meter channels (``em1`` / ``em1data``).

Reported on the simon42 forum (2026-08-03): on a Shelly Pro EM-50 only the
relay came through, the energy measurement stayed invisible although the
diagnostics file clearly contained the values. Cause: the 2-channel meters
report per-channel ``em1:<id>`` / ``em1data:<id>`` components, while the
creation loops only knew the phase-prefixed ``em`` / ``emdata`` shape of the
Pro 3EM.

These tests drive the real RPC creation loop with a fake coordinator — no
running HA required.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.shelly_cloud_diy.sensor import (
    RpcSensor,
    _create_rpc_sensors as create_rpc_sensors,
)

DEVICE_ID = "proem50a1b2c3"


class _FakeCoordinator:
    def __init__(self, device_id: str, status: dict[str, Any]) -> None:
        self.devices = {device_id: {"status": status, "device_code": "SPEM-002CEBEU50", "online": True}}
        self.data = self.devices
        self.last_update_success = True


# Shape of a Pro EM-50: one relay plus two independent meter channels.
_EM50_STATUS: dict[str, Any] = {
    "sys": {"mac": "AABBCCDDEEFF"},
    "switch:0": {"id": 0, "output": False},
    "em1:0": {
        "id": 0,
        "current": 1.234,
        "voltage": 230.1,
        "act_power": 250.7,
        "aprt_power": 283.9,
        "pf": 0.88,
        "freq": 50.0,
        "calibration": "factory",
    },
    "em1:1": {
        "id": 1,
        "current": 0.5,
        "voltage": 229.8,
        "act_power": -110.2,
        "aprt_power": 115.0,
        "pf": 0.96,
        "freq": 50.0,
        "calibration": "factory",
    },
    "em1data:0": {"id": 0, "total_act_energy": 12345.6, "total_act_ret_energy": 78.9},
    "em1data:1": {"id": 1, "total_act_energy": 500.0, "total_act_ret_energy": 4200.5},
}


def _build(status: dict[str, Any]):
    coord = _FakeCoordinator(DEVICE_ID, status)
    sensors = create_rpc_sensors(DEVICE_ID, status, set(), coord)
    return sensors, {e.unique_id: e for e in sensors}


def test_both_channels_create_measurement_and_energy_entities():
    sensors, by_uid = _build(_EM50_STATUS)
    em1 = [e for e in sensors if isinstance(e, RpcSensor) and ":" in e.unique_id]
    # 6 measurements + 2 counters, per channel.
    assert len([u for u in by_uid if "_em1:0_" in u]) == 6
    assert len([u for u in by_uid if "_em1:1_" in u]) == 6
    assert len([u for u in by_uid if "_em1data:0_" in u]) == 2
    assert len([u for u in by_uid if "_em1data:1_" in u]) == 2
    assert em1  # sanity: entities were actually built


def test_values_are_read_from_the_right_channel():
    _s, by_uid = _build(_EM50_STATUS)
    assert by_uid[f"{DEVICE_ID}_em1:0_act_power"].native_value == 250.7
    assert by_uid[f"{DEVICE_ID}_em1:1_act_power"].native_value == -110.2
    assert by_uid[f"{DEVICE_ID}_em1:0_voltage"].native_value == 230.1
    assert by_uid[f"{DEVICE_ID}_em1data:1_total_act_ret_energy"].native_value == 4200.5


def test_channel_is_reflected_in_the_name():
    _s, by_uid = _build(_EM50_STATUS)
    assert by_uid[f"{DEVICE_ID}_em1:0_act_power"].name == "Active Power"
    assert by_uid[f"{DEVICE_ID}_em1:1_act_power"].name == "Active Power 2"
    assert by_uid[f"{DEVICE_ID}_em1data:0_total_act_energy"].name == "Energy"
    assert by_uid[f"{DEVICE_ID}_em1data:1_total_act_energy"].name == "Energy 2"


def test_energy_counters_carry_statistics_metadata():
    """Without these the counters never reach the HA energy dashboard."""
    _s, by_uid = _build(_EM50_STATUS)
    e = by_uid[f"{DEVICE_ID}_em1data:0_total_act_energy"]
    assert e.device_class is SensorDeviceClass.ENERGY
    assert e.state_class is SensorStateClass.TOTAL_INCREASING
    p = by_uid[f"{DEVICE_ID}_em1:0_act_power"]
    assert p.device_class is SensorDeviceClass.POWER
    assert p.state_class is SensorStateClass.MEASUREMENT


def test_unique_ids_are_distinct():
    sensors, by_uid = _build(_EM50_STATUS)
    assert len(by_uid) == len(sensors)


def test_null_and_missing_fields_are_skipped():
    status = {
        "sys": {"mac": "x"},
        "em1:0": {"id": 0, "act_power": 12.0, "voltage": None, "pf": None},
        "em1data:0": {"id": 0, "total_act_energy": 1.0},
    }
    _s, by_uid = _build(status)
    assert f"{DEVICE_ID}_em1:0_act_power" in by_uid
    assert f"{DEVICE_ID}_em1:0_voltage" not in by_uid
    assert f"{DEVICE_ID}_em1:0_pf" not in by_uid
    assert f"{DEVICE_ID}_em1:0_current" not in by_uid
    assert f"{DEVICE_ID}_em1data:0_total_act_ret_energy" not in by_uid


def test_malformed_component_does_not_raise():
    status = {"sys": {"mac": "x"}, "em1:0": "not-a-dict", "em1data:0": None}
    sensors, _ = _build(status)
    assert not [e for e in sensors if "em1" in (e.unique_id or "")]


def test_em1_does_not_collide_with_three_phase_em():
    """``em1``/``em1data`` and ``em``/``emdata`` must not match each other."""
    status = {
        "sys": {"mac": "x"},
        "em:0": {"a_act_power": 100.0, "total_act_power": 300.0},
        "emdata:0": {"total_act": 5000.0},
    }
    _s, by_uid = _build(status)
    assert f"{DEVICE_ID}_em:0_a_act_power" in by_uid
    assert f"{DEVICE_ID}_emdata:0_total_act" in by_uid
    assert not [u for u in by_uid if "em1" in u]
