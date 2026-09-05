"""Unit tests for the BLU gateway signal sensor (``reporter.rssi``).

A Shelly BLU device has no IP link, so it has no radio figure of its own.
What the cloud carries instead is ``reporter.rssi`` — how well the bridging
gateway hears the beacon. It is present on 29 of 29 gateway-bridged devices
in the recorded account snapshot (-43 … -84 dBm), the health checks have been
reading it since they were written, and no entity ever surfaced it: the same
gap ``wifi.rssi`` had on the Gen2 side.

These tests drive the real creation loop (``_create_ble_sensors``) and the
real entity with a lightweight fake coordinator, so they run identically
against any HA version and need no running Home Assistant.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import EntityCategory

from custom_components.shelly_cloud_diy.const import device_gen
from custom_components.shelly_cloud_diy.sensor import (
    BleReporterSignalSensor,
    _create_ble_sensors,
    _usable_rssi,
)

DEVICE_ID = "XB106582483818186"
GATEWAY_ID = "146221729481748"
UID = f"{DEVICE_ID}_ble_reporter_rssi"


class _FakeCoordinator:
    """Minimal coordinator: entities only read ``.devices[id]['status']``."""

    def __init__(self, device_id: str, status: dict[str, Any]) -> None:
        self.devices = {
            device_id: {
                "status": status,
                "device_code": "SBHT-003C",
                "online": True,
            }
        }
        self.data = self.devices
        self.last_update_success = True


def _blu_status(reporter: Any = "default") -> dict[str, Any]:
    """The shape a BLU H&T arrives in, gateway block included."""
    status: dict[str, Any] = {
        "_dev_info": {"gen": "GBLE"},
        "temperature:0": {"id": 0, "tC": 21.4},
        "humidity:0": {"id": 0, "rh": 48.0},
        "devicepower:0": {"id": 0, "battery": {"percent": 100, "V": None}},
    }
    if reporter == "default":
        reporter = {"id": GATEWAY_ID, "rssi": -59, "inrange": False}
    if reporter is not None:
        status["reporter"] = reporter
    return status


def _build(status: dict[str, Any], created: set[str] | None = None):
    coord = _FakeCoordinator(DEVICE_ID, status)
    entities = _create_ble_sensors(
        DEVICE_ID, status, set() if created is None else created, coord
    )
    return entities, {e.unique_id: e for e in entities}


def test_blu_payload_is_classified_as_a_bridged_device() -> None:
    """Guards the branch: only the BLE builder ever sees ``reporter``."""
    assert device_gen(_blu_status()) == "GBLE"


def test_gateway_signal_sensor_is_created_and_reads_the_gateway_view() -> None:
    """The reading the 29 measured BLU devices all carry becomes an entity."""
    _, by_uid = _build(_blu_status())

    sensor = by_uid[UID]
    assert isinstance(sensor, BleReporterSignalSensor)
    assert sensor.native_value == -59
    assert sensor.native_unit_of_measurement == "dBm"
    assert sensor.device_class is SensorDeviceClass.SIGNAL_STRENGTH
    assert sensor.state_class is SensorStateClass.MEASUREMENT
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC


def test_the_name_says_the_reading_belongs_to_the_gateway() -> None:
    """A BLU sensor has no radio of its own; the name must not imply one."""
    _, by_uid = _build(_blu_status())

    assert "gateway" in by_uid[UID].name.lower()


def test_the_relaying_gateway_is_exposed_as_an_attribute() -> None:
    """One gateway relays for many devices, so the reading needs a receiver."""
    _, by_uid = _build(_blu_status())

    assert by_uid[UID].extra_state_attributes == {"gateway_id": GATEWAY_ID}


def test_no_gateway_id_means_no_attributes_rather_than_a_null_one() -> None:
    """An attribute reading ``None`` would look like a named gateway."""
    _, by_uid = _build(_blu_status({"rssi": -70}))

    assert by_uid[UID].extra_state_attributes is None


def test_no_entity_without_a_usable_reading() -> None:
    """The gate is the reading, not the key.

    The block exists on a gateway that has not heard the beacon yet, and an
    entity that can only ever say "unknown" — or claim a perfect 0 dBm — is
    worse than no entity.
    """
    for reporter in (
        None,
        {},
        {"id": GATEWAY_ID},
        {"id": GATEWAY_ID, "rssi": None},
        {"id": GATEWAY_ID, "rssi": 0},
        {"id": GATEWAY_ID, "rssi": 7},
        {"id": GATEWAY_ID, "rssi": "strong"},
        {"id": GATEWAY_ID, "rssi": True},
    ):
        _, by_uid = _build(_blu_status(reporter))
        assert UID not in by_uid, f"entity built for reporter={reporter!r}"


def test_a_device_without_the_block_still_gets_its_other_sensors() -> None:
    """Absence of ``reporter`` must cost exactly one entity, not the rest."""
    entities, by_uid = _build(_blu_status(None))

    assert UID not in by_uid
    assert f"{DEVICE_ID}_ble_temperature_0" in by_uid
    assert f"{DEVICE_ID}_ble_battery_percent" in by_uid


def test_a_reading_that_stops_being_usable_reads_unknown() -> None:
    """The live value applies the same filter the builder gated on."""
    status = _blu_status()
    _, by_uid = _build(status)
    sensor = by_uid[UID]

    status["reporter"]["rssi"] = 0
    assert sensor.native_value is None

    del status["reporter"]
    assert sensor.native_value is None
    assert sensor.extra_state_attributes is None


def test_the_sensor_is_created_once_per_device() -> None:
    """The ``created`` set is what stops a rediscovery duplicating entities."""
    created: set[str] = set()
    _build(_blu_status(), created)
    _, by_uid = _build(_blu_status(), created)

    assert UID not in by_uid


def test_the_coverage_report_can_see_which_key_this_entity_reads() -> None:
    """``_status_key`` is the only way the diagnostics report learns this.

    Without it ``reporter`` would keep being listed as an uncovered key even
    though it now produces an entity — the mirror image of the bug the report
    exists to find.
    """
    _, by_uid = _build(_blu_status())

    assert by_uid[UID]._status_key == "reporter"


def test_usable_rssi_rejects_everything_that_is_not_a_measurement() -> None:
    """The shared filter, pinned on its own."""
    assert _usable_rssi(-59) == -59
    assert _usable_rssi(-43.5) == -43.5
    assert _usable_rssi(0) is None
    assert _usable_rssi(12) is None
    assert _usable_rssi(None) is None
    assert _usable_rssi("-59") is None
    # A bool is an int in Python; a payload of ``rssi: true`` is malformed,
    # not a one-decibel link.
    assert _usable_rssi(True) is None
    assert _usable_rssi(False) is None
