"""Unit tests for the Gen1 Shelly TRV, read-only (#62).

The status below is the payload @zbcsaba reported from two real ``SHTRV-01``
valves on firmware v2.2.4. Only the fields quoted in the issue are here.
"""
from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.sensor import SensorDeviceClass

from custom_components.shelly_cloud_diy import binary_sensor as binary_sensor_platform
from custom_components.shelly_cloud_diy import climate as climate_platform
from custom_components.shelly_cloud_diy import diagnostics
from custom_components.shelly_cloud_diy import sensor as sensor_platform
from custom_components.shelly_cloud_diy.climate import ShellyGen1TrvClimate
from custom_components.shelly_cloud_diy.const import is_gen2_status

DEVICE_ID = "aabbccddeeff"


class _FakeCoordinator:
    """Minimal coordinator: entities only read ``.devices[id]['status']``."""

    def __init__(self, status: dict[str, Any]) -> None:
        self.devices = {
            DEVICE_ID: {"status": status, "device_code": "SHTRV-01", "online": True}
        }
        self.data = self.devices
        self.last_update_success = True

    def is_enabled(self, device_id: str) -> bool:
        return True


def _status(**thermostat: Any) -> dict[str, Any]:
    return {
        "thermostats": [
            {
                "pos": 4.7,
                "target_t": {"enabled": True, "value": 18.3, "units": "C"},
                "tmp": {"value": 18.4, "units": "C", "is_valid": True},
                "schedule": False,
                "boost_minutes": 0,
                "window_open": False,
                **thermostat,
            }
        ],
        "bat": {"value": 100, "voltage": 4.118},
        "charger": False,
        "calibrated": True,
        "wifi_sta": {"connected": True, "rssi": -60},
    }


def _climate(status: dict[str, Any]) -> ShellyGen1TrvClimate:
    entities = climate_platform._create_climate_entities(
        DEVICE_ID, status, set(), _FakeCoordinator(status)
    )
    assert len(entities) == 1
    assert isinstance(entities[0], ShellyGen1TrvClimate)
    return entities[0]


def _sensors(status: dict[str, Any]) -> dict[str, Any]:
    entities = sensor_platform._create_block_sensors(
        DEVICE_ID, status, set(), _FakeCoordinator(status)
    )
    return {entity.name: entity for entity in entities}


def test_stays_on_the_block_path() -> None:
    assert is_gen2_status(_status()) is False


def test_climate_reads_the_reported_payload() -> None:
    climate = _climate(_status())
    assert climate.current_temperature == 18.4
    assert climate.target_temperature == 18.3
    assert climate.hvac_mode == HVACMode.HEAT
    assert climate.hvac_action == HVACAction.HEATING
    assert climate.unique_id == f"{DEVICE_ID}_thermostat_0"


def test_climate_is_read_only() -> None:
    assert _climate(_status()).supported_features == ClimateEntityFeature(0)


def test_closed_valve_is_idle() -> None:
    assert _climate(_status(pos=0)).hvac_action == HVACAction.IDLE


def test_disabled_control_is_off() -> None:
    climate = _climate(
        _status(target_t={"enabled": False, "value": 18.3, "units": "C"})
    )
    assert climate.hvac_mode == HVACMode.OFF
    assert climate.hvac_action == HVACAction.OFF


def test_invalid_room_temperature_is_no_reading() -> None:
    climate = _climate(
        _status(tmp={"value": 18.4, "units": "C", "is_valid": False})
    )
    assert climate.current_temperature is None


@pytest.mark.parametrize("value", [None, "18", True, -999])
def test_unusable_values_are_no_reading(value: Any) -> None:
    climate = _climate(_status(tmp={"value": value, "units": "C", "is_valid": True}))
    assert climate.current_temperature is None


def test_fahrenheit_is_converted() -> None:
    climate = _climate(_status(tmp={"value": 68, "units": "F", "is_valid": True}))
    assert climate.current_temperature == pytest.approx(20.0)


def test_no_thermostats_means_no_climate_entity() -> None:
    status = _status()
    del status["thermostats"]
    assert not climate_platform._create_climate_entities(
        DEVICE_ID, status, set(), _FakeCoordinator(status)
    )


def test_sensors_read_battery_and_valve_position() -> None:
    sensors = _sensors(_status())
    assert sensors["Battery"].native_value == 100
    assert sensors["Battery"].device_class == SensorDeviceClass.BATTERY
    assert sensors["Valve Position"].native_value == 4.7


@pytest.mark.parametrize("pos", [-1, 101, "4.7", True])
def test_impossible_valve_position_is_no_reading(pos: Any) -> None:
    assert _sensors(_status(pos=pos))["Valve Position"].native_value is None


def test_window_sensor_follows_the_flag() -> None:
    for flag in (False, True):
        status = _status(window_open=flag)
        entities = binary_sensor_platform._create_block_sensors(
            DEVICE_ID, status, set(), _FakeCoordinator(status)
        )
        assert len(entities) == 1
        assert entities[0].device_class == BinarySensorDeviceClass.WINDOW
        assert entities[0].is_on is flag


def test_rebuilding_adds_no_duplicates() -> None:
    status = _status()
    coordinator = _FakeCoordinator(status)
    for build in (
        climate_platform._create_climate_entities,
        sensor_platform._create_block_sensors,
        binary_sensor_platform._create_block_sensors,
    ):
        created: set[str] = set()
        assert build(DEVICE_ID, status, created, coordinator)
        assert not build(DEVICE_ID, status, created, coordinator)


def test_coverage_report_counts_the_new_keys() -> None:
    status = _status()
    report = diagnostics._coverage_diagnostics(
        _FakeCoordinator(status), DEVICE_ID, status
    )
    assert "builder_errors" not in report
    assert {"thermostats", "bat", "wifi_sta"} <= set(report["covered_keys"])
    assert "thermostats" not in report["uncovered_keys"]
