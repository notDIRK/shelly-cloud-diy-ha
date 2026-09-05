"""Unit tests for the Gen2+ "Firmware update" binary sensor.

``sys.available_updates`` is present on 35 of 35 Gen2+ devices in the recorded
account snapshot, and non-empty on 24 of them (18× 2.0.0, 5× 1.7.5, one beta).
Nothing in the integration surfaced it: the health check that reads the same
field is opt-in and off by default, precisely because a repair card lit on two
thirds of a fleet is wallpaper. An entity does not have that problem, which is
why this one exists and is created by default — see the class docstring before
"harmonising" the two.

The tests drive the real creation loop (``_create_rpc_sensors``) and the real
entity with a lightweight fake coordinator, so they need no running HA.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import EntityCategory

from custom_components.shelly_cloud_diy.binary_sensor import (
    ShellyFirmwareUpdateBinarySensor,
    _create_ble_binary_sensors,
    _create_rpc_sensors,
)

DEVICE_ID = "a0dd6cffee01"
UID = f"{DEVICE_ID}_firmware_update"


class _FakeCoordinator:
    """Minimal coordinator: entities only read ``.devices[id]['status']``."""

    def __init__(self, device_id: str, status: dict[str, Any]) -> None:
        self.devices = {
            device_id: {
                "status": status,
                "device_code": "SNSW-001X16EU",
                "online": True,
            }
        }
        self.data = self.devices
        self.last_update_success = True


def _gen2_status(updates: Any = "absent") -> dict[str, Any]:
    """A Gen2 payload, optionally carrying ``sys.available_updates``."""
    sys_block: dict[str, Any] = {
        "mac": "A0DD6CFFEE01",
        "uptime": 545909,
        "restart_required": False,
    }
    if updates != "absent":
        sys_block["available_updates"] = updates
    return {
        "switch:0": {"id": 0, "output": True, "apower": 12.3},
        "cloud": {"connected": True},
        "wifi": {"rssi": -55},
        "sys": sys_block,
    }


def _build(status: dict[str, Any], created: set[str] | None = None):
    coord = _FakeCoordinator(DEVICE_ID, status)
    entities = _create_rpc_sensors(
        DEVICE_ID, status, set() if created is None else created, coord
    )
    return entities, {e.unique_id: e for e in entities}


def test_a_pending_stable_update_reads_on() -> None:
    """The 18 devices offering 2.0.0 in the snapshot are the main case."""
    _, by_uid = _build(_gen2_status({"stable": {"version": "2.0.0"}}))

    sensor = by_uid[UID]
    assert isinstance(sensor, ShellyFirmwareUpdateBinarySensor)
    assert sensor.is_on is True
    assert sensor.device_class is BinarySensorDeviceClass.UPDATE
    assert sensor.entity_category is EntityCategory.DIAGNOSTIC


def test_the_offered_version_is_the_useful_part() -> None:
    """Bare "an update exists" cannot be acted on; a version number can."""
    _, by_uid = _build(_gen2_status({"stable": {"version": "1.7.5"}}))

    assert by_uid[UID].extra_state_attributes == {"stable_version": "1.7.5"}


def test_an_up_to_date_device_still_gets_the_entity() -> None:
    """``{}`` is a reading, not a missing field.

    11 of the 35 measured devices report it, and it means "current". Skipping
    them would leave a fleet where only the devices in trouble have the
    entity — so the one device that goes quiet looks identical to a healthy
    one.
    """
    _, by_uid = _build(_gen2_status({}))

    sensor = by_uid[UID]
    assert sensor.is_on is False
    assert sensor.extra_state_attributes is None


def test_a_beta_offer_is_reported_but_does_not_read_on() -> None:
    """One device in the snapshot offers only a beta.

    A default-on flag must not nudge anyone into installing a beta build, so
    the verdict stays ``off`` — while the version is still surfaced, because
    dropping the reading entirely would be its own kind of dishonesty.
    """
    _, by_uid = _build(_gen2_status({"beta": {"version": "2.0.0-beta3"}}))

    sensor = by_uid[UID]
    assert sensor.is_on is False
    assert sensor.extra_state_attributes == {"beta_version": "2.0.0-beta3"}


def test_both_channels_are_reported_together() -> None:
    """A device offering both must not lose one of the two versions."""
    _, by_uid = _build(
        _gen2_status(
            {"stable": {"version": "2.0.0"}, "beta": {"version": "2.1.0-beta1"}}
        )
    )

    sensor = by_uid[UID]
    assert sensor.is_on is True
    assert sensor.extra_state_attributes == {
        "stable_version": "2.0.0",
        "beta_version": "2.1.0-beta1",
    }


def test_no_entity_when_the_device_does_not_report_the_field() -> None:
    """No field, no entity — an always-unknown flag helps nobody."""
    for updates in ("absent", None, "yes", []):
        _, by_uid = _build(_gen2_status(updates))
        assert UID not in by_uid, f"entity built for available_updates={updates!r}"


def test_no_entity_without_a_sys_block_at_all() -> None:
    """Some payloads carry no ``sys``; that must not raise or invent one."""
    status = _gen2_status()
    del status["sys"]

    _, by_uid = _build(status)
    assert UID not in by_uid


def test_a_malformed_channel_entry_reads_off_rather_than_raising() -> None:
    """The cloud's shapes are not a contract; a surprise must not crash HA."""
    status = _gen2_status({"stable": "2.0.0"})
    _, by_uid = _build(status)

    sensor = by_uid[UID]
    assert sensor.is_on is False
    assert sensor.extra_state_attributes is None


def test_the_verdict_follows_the_payload() -> None:
    """A device that gets flashed must clear the flag on the next poll."""
    status = _gen2_status({"stable": {"version": "2.0.0"}})
    _, by_uid = _build(status)
    sensor = by_uid[UID]
    assert sensor.is_on is True

    status["sys"]["available_updates"] = {}
    assert sensor.is_on is False
    assert sensor.extra_state_attributes is None


def test_the_sensor_is_created_once_per_device() -> None:
    """The ``created`` set is what stops a rediscovery duplicating entities."""
    created: set[str] = set()
    _build(_gen2_status({}), created)
    _, by_uid = _build(_gen2_status({}), created)

    assert UID not in by_uid


def test_it_is_registered_enabled_unlike_the_repair_card() -> None:
    """Pins the decision the class docstring argues for.

    The repair card over the same field defaults OFF because 24 of 35 devices
    would light it permanently. The entity defaults ON because it never
    interrupts anyone. If someone ever "harmonises" the two, this fails.
    """
    _, by_uid = _build(_gen2_status({"stable": {"version": "2.0.0"}}))

    assert by_uid[UID].entity_registry_enabled_default is True


def test_it_claims_no_status_key_so_sys_stays_a_reported_gap() -> None:
    """Deliberate: the coverage report works at top-level-key granularity.

    Claiming ``sys`` here would mark the whole block covered while uptime,
    free RAM and free filesystem still produce no entity — three real gaps
    traded away for one entity's mention.
    """
    _, by_uid = _build(_gen2_status({"stable": {"version": "2.0.0"}}))
    sensor = by_uid[UID]

    for attribute in ("_status_key", "_component_key", "_attr_key"):
        assert not hasattr(sensor, attribute), attribute


def test_a_bridged_device_gets_no_firmware_flag() -> None:
    """BLU devices take the other branch and carry no ``sys`` block."""
    status = {
        "_dev_info": {"gen": "GBLE"},
        "temperature:0": {"id": 0, "tC": 21.4},
        "reporter": {"id": "146221729481748", "rssi": -71},
    }
    coord = _FakeCoordinator(DEVICE_ID, status)

    built = _create_ble_binary_sensors(DEVICE_ID, status, set(), coord)

    assert not [e for e in built if e.unique_id == UID]
