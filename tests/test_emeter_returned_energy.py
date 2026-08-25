"""Unit tests for Gen1 energy-meter counters (``emeters[].total_returned``).

Reported as issue #38 (2026-08-25): on a Shelly 3EM the "energy returned"
sensors never appeared. Cause: ``_create_block_sensors`` instantiated only
``total`` (consumption); the ``("emeter", "energyReturned")`` description had
existed since the initial port but was never wired to a status key, so anyone
feeding PV back into the grid saw one half of the meter.

These tests drive the real Block creation loop with a fake coordinator — no
running HA required.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy

from custom_components.shelly_cloud_diy.sensor import (
    BlockSensor,
    _create_block_sensors as create_block_sensors,
)

DEVICE_ID = "3em0a1b2c3d4"


class _FakeCoordinator:
    def __init__(self, device_id: str, status: dict[str, Any]) -> None:
        self.devices = {
            device_id: {"status": status, "device_code": "SHEM-3", "online": True}
        }
        self.data = self.devices
        self.last_update_success = True


# Shape of a Gen1 Shelly 3EM: three clamp channels, each with its own pair of
# cumulative counters. Phase B is exporting (negative power, rising
# ``total_returned``), which is exactly the case the missing entity hid.
_3EM_STATUS: dict[str, Any] = {
    "mac": "AABBCCDDEEFF",
    "emeters": [
        {
            "power": 250.7,
            "pf": 0.88,
            "current": 1.09,
            "voltage": 230.1,
            "is_valid": True,
            "total": 12345.6,
            "total_returned": 0.0,
        },
        {
            "power": -410.2,
            "pf": -0.96,
            "current": 1.79,
            "voltage": 229.8,
            "is_valid": True,
            "total": 500.0,
            "total_returned": 4200.5,
        },
        {
            "power": 12.0,
            "pf": 0.5,
            "current": 0.1,
            "voltage": 230.0,
            "is_valid": True,
            "total": 77.7,
            "total_returned": 1.25,
        },
    ],
}


def _build(status: dict[str, Any]):
    coord = _FakeCoordinator(DEVICE_ID, status)
    sensors = create_block_sensors(DEVICE_ID, status, set(), coord)
    return sensors, {e.unique_id: e for e in sensors}


def test_every_phase_gets_a_returned_energy_entity():
    _, by_uid = _build(_3EM_STATUS)
    for idx in range(3):
        assert f"{DEVICE_ID}_emeter|energyReturned_{idx}" in by_uid
        # The consumption counter must survive the change.
        assert f"{DEVICE_ID}_emeter|energy_{idx}" in by_uid


def test_returned_energy_reads_the_right_phase():
    _, by_uid = _build(_3EM_STATUS)
    assert by_uid[f"{DEVICE_ID}_emeter|energyReturned_0"].native_value == 0.0
    assert by_uid[f"{DEVICE_ID}_emeter|energyReturned_1"].native_value == 4200.5
    assert by_uid[f"{DEVICE_ID}_emeter|energyReturned_2"].native_value == 1.25
    # Consumption still points at ``total``, not at the new key.
    assert by_uid[f"{DEVICE_ID}_emeter|energy_1"].native_value == 500.0


def test_returned_energy_is_a_statistics_grade_energy_sensor():
    _, by_uid = _build(_3EM_STATUS)
    entity = by_uid[f"{DEVICE_ID}_emeter|energyReturned_0"]
    assert isinstance(entity, BlockSensor)
    assert entity.device_class is SensorDeviceClass.ENERGY
    assert entity.state_class is SensorStateClass.TOTAL_INCREASING
    assert entity.native_unit_of_measurement == UnitOfEnergy.WATT_HOUR


def test_names_follow_the_existing_channel_convention():
    _, by_uid = _build(_3EM_STATUS)
    assert by_uid[f"{DEVICE_ID}_emeter|energyReturned_0"].name == "Energy Returned"
    assert by_uid[f"{DEVICE_ID}_emeter|energyReturned_1"].name == "Energy Returned 2"
    assert by_uid[f"{DEVICE_ID}_emeter|energyReturned_2"].name == "Energy Returned 3"


def test_meter_without_the_key_creates_nothing_extra():
    """A Gen1 EM firmware that omits ``total_returned`` must not gain a
    permanently ``None`` entity."""
    status = {"emeters": [{"power": 1.0, "voltage": 230.0, "total": 10.0}]}
    _, by_uid = _build(status)
    assert f"{DEVICE_ID}_emeter|energy_0" in by_uid
    assert f"{DEVICE_ID}_emeter|energyReturned_0" not in by_uid


def test_created_set_prevents_duplicates_across_calls():
    coord = _FakeCoordinator(DEVICE_ID, _3EM_STATUS)
    created: set[str] = set()
    first = create_block_sensors(DEVICE_ID, _3EM_STATUS, created, coord)
    second = create_block_sensors(DEVICE_ID, _3EM_STATUS, created, coord)
    assert any("energyReturned" in e.unique_id for e in first)
    assert second == []
