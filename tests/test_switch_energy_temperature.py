"""Switch channels must expose their energy counter and device temperature.

Contributed as PR #24 by @walnuss0815, who noticed the entities were missing on
his hardware. The cause was not a missing description — ``switch_energy`` and
``switch_temperature`` had existed all along, including the ``value_fn``s that
unpack the nested shapes. They were simply never listed in the creation loop in
``sensor.py``, so they could never fire.

That is a silent failure mode: a description can sit in the table looking
correct forever while nothing ever builds an entity from it. Hence these tests —
they assert on the entities the loop actually produces, so dropping an entry
from that list breaks a test instead of quietly costing users their sensors.

The nested shapes are the other half. A switch reports ``aenergy`` as
``{"total": ..., "by_minute": [...], "minute_ts": ...}`` and ``temperature`` as
``{"tC": ..., "tF": ...}``; a naive handler would surface the dict itself.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.shelly_cloud_diy.sensor import (
    _create_rpc_sensors as create_rpc_sensors,
)

DEVICE_ID = "plus1pm4f5a6b"


class _FakeCoordinator:
    def __init__(self, device_id: str, status: dict[str, Any]) -> None:
        self.devices = {
            device_id: {
                "status": status,
                "device_code": "SNSW-001P16EU",
                "online": True,
            }
        }
        self.data = self.devices
        self.last_update_success = True


# A Shelly Plus 1PM: relay plus metering, including the two fields at issue.
_SWITCH_STATUS: dict[str, Any] = {
    "cloud": {"connected": True},
    "sys": {"mac": "AABBCCDDEEFF"},
    "switch:0": {
        "id": 0,
        "output": True,
        "apower": 61.4,
        "voltage": 232.1,
        "current": 0.271,
        "aenergy": {"total": 4321.7, "by_minute": [0.0], "minute_ts": 1755000000},
        "temperature": {"tC": 42.3, "tF": 108.1},
    },
}


def _build(status: dict[str, Any]):
    coord = _FakeCoordinator(DEVICE_ID, status)
    sensors = create_rpc_sensors(DEVICE_ID, status, set(), coord)
    return {s.unique_id: s for s in sensors}


def test_energy_and_temperature_entities_are_created():
    """The regression PR #24 fixed: both were absent from the creation loop."""
    by_uid = _build(_SWITCH_STATUS)
    assert f"{DEVICE_ID}_switch:0_aenergy" in by_uid
    assert f"{DEVICE_ID}_switch:0_temperature" in by_uid


def test_nested_values_are_unpacked_not_surfaced_as_dicts():
    """``aenergy.total`` and ``temperature.tC``, not the containing dicts."""
    by_uid = _build(_SWITCH_STATUS)
    assert by_uid[f"{DEVICE_ID}_switch:0_aenergy"].native_value == 4321.7
    assert by_uid[f"{DEVICE_ID}_switch:0_temperature"].native_value == 42.3


def test_energy_counter_is_dashboard_compatible():
    """TOTAL_INCREASING + ENERGY is what the HA energy dashboard requires."""
    energy = _build(_SWITCH_STATUS)[f"{DEVICE_ID}_switch:0_aenergy"]
    assert energy.device_class == SensorDeviceClass.ENERGY
    assert energy.state_class == SensorStateClass.TOTAL_INCREASING


def test_temperature_is_diagnostic_and_measured():
    temp = _build(_SWITCH_STATUS)[f"{DEVICE_ID}_switch:0_temperature"]
    assert temp.device_class == SensorDeviceClass.TEMPERATURE
    assert temp.state_class == SensorStateClass.MEASUREMENT


def test_the_previously_working_sensors_still_work():
    """Power, voltage and current must not have been disturbed."""
    by_uid = _build(_SWITCH_STATUS)
    assert by_uid[f"{DEVICE_ID}_switch:0_apower"].native_value == 61.4
    assert by_uid[f"{DEVICE_ID}_switch:0_voltage"].native_value == 232.1
    assert by_uid[f"{DEVICE_ID}_switch:0_current"].native_value == 0.271


def test_relayless_switch_creates_nothing_extra():
    """A switch without metering must not gain empty energy/temperature entities."""
    status = {
        "sys": {"mac": "AABBCCDDEEFF"},
        "switch:0": {"id": 0, "output": False},
    }
    by_uid = _build(status)
    assert f"{DEVICE_ID}_switch:0_aenergy" not in by_uid
    assert f"{DEVICE_ID}_switch:0_temperature" not in by_uid


def test_covers_and_lights_get_the_same_treatment():
    """The loop covers switch|light|cover — a metered cover must benefit too."""
    status = {
        "sys": {"mac": "AABBCCDDEEFF"},
        "cover:0": {
            "id": 0,
            "apower": 12.0,
            "aenergy": {"total": 99.5},
            "temperature": {"tC": 31.0},
        },
    }
    by_uid = _build(status)
    assert by_uid[f"{DEVICE_ID}_cover:0_aenergy"].native_value == 99.5
    assert by_uid[f"{DEVICE_ID}_cover:0_temperature"].native_value == 31.0
