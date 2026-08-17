"""Unit tests for offline detection — the "Reporting" binary sensor.

Why this feature exists at all, measured against a live 64-device account on
2026-08-16: every flag the cloud offers for "is this device alive" is a cached
lie. ``cloud.connected`` still read ``true`` thirteen minutes after a device
had been physically unplugged, and read ``true`` for all 35 mains devices on
the account — including one that had not reported for a quarter of an hour.
``_dev_info.online`` read ``true`` for all 29 BLE devices, one of which had
been silent for three days. Disappearing from ``/device/all_status`` does
eventually happen, but takes up to ten minutes and the endpoint also omits
devices spontaneously.

The one trustworthy fact is that the device pushed a new snapshot, which
``checkin_marker`` fingerprints. These tests pin the contract built on it:

* a device counts as reporting while it keeps checking in;
* the window is per-device, because normal cadences differ by orders of
  magnitude (a metering Mini 1PM reports every 60 s, an idle Plus RGBW PM went
  25 minutes, a BLE beacon three days — all healthy);
* a single missing poll is not an outage (the endpoint drops devices);
* an outage must never be *learned* as a device's normal cadence;
* and when our own polling breaks we say "unknown", never "everything died".

Driven through the real production code with a lightweight fake coordinator,
so no running Home Assistant instance is required.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.shelly_cloud_diy import coordinator as coordinator_mod
from custom_components.shelly_cloud_diy.binary_sensor import (
    ShellyReportingBinarySensor,
    _create_reporting_sensor,
)
from custom_components.shelly_cloud_diy.const import OFFLINE_AFTER_DEFAULT
from custom_components.shelly_cloud_diy.coordinator import (
    REPORT_GAP_MARGIN,
    REPORT_LEARN_CAP_S,
    REPORT_STALE_BLE_S,
    REPORT_STALE_CAP_S,
    REPORT_UNLEARNED_FLOOR_S,
    CheckinRecord,
    ShellyCloudCoordinator,
    sleep_window_s,
)


@pytest.fixture(autouse=True)
def _inert_repair_issues(monkeypatch):
    """Neutralise the repair-issue wrappers for this module.

    A successful poll ends by re-evaluating the repair cards, which reaches
    into the real issue registry. These tests drive the poll against a stub
    ``hass`` that has no registry, so the wrappers are stubbed out — the same
    split ``test_sleeping_availability.py`` uses. Their behaviour is covered
    in ``test_repair_issues.py`` and in the live harness.

    Deliberately per-module rather than in ``conftest.py``: an autouse
    fixture there would also disarm the tests that exist to exercise these
    very wrappers.
    """
    monkeypatch.setattr(
        coordinator_mod, "async_manage_rate_limit_issue",
        lambda hass, entry, *, active: None,
    )
    monkeypatch.setattr(
        coordinator_mod, "async_manage_missing_devices_issue",
        lambda hass, entry, *, active, missing, names: None,
    )
    monkeypatch.setattr(
        coordinator_mod, "async_manage_relay_fault_issue",
        lambda hass, entry, *, active, faults, names: None,
    )

MAINS_ID = "ecda3bc59ec8"
BLE_ID = "XB106582483818186"
DEFAULT_WINDOW_S = OFFLINE_AFTER_DEFAULT * 60


# ── Fixtures modelled on real payloads ────────────────────────────────

def _mains_status(unixtime: int = 1786890000, updated: str = "2026-08-16 14:20:06") -> dict[str, Any]:
    """A Gen3 mains device, exactly as the cloud serves it — including the
    ``cloud.connected: true`` that stays true long after the device is dead."""
    return {
        "id": MAINS_ID,
        "code": "S3SW-001P8EU",
        "cloud": {"connected": True},
        "switch:0": {"id": 0, "output": False, "apower": 0.0},
        "sys": {"unixtime": unixtime, "uptime": 545909},
        "serial": 1,
        "_updated": updated,
    }


def _ble_status() -> dict[str, Any]:
    """A BLU beacon bridged through a gateway: silent for days, yet online."""
    return {
        "motion:0": {"id": 0, "motion": False},
        "devicepower:0": {"id": 0, "battery": {"percent": 100}},
        "_updated": "2026-08-15 19:47:35",
        "serial": 27020,
        "_dev_info": {"id": BLE_ID, "gen": "GBLE", "online": True},
    }


def _pushed(status: dict[str, Any], unixtime: int) -> dict[str, Any]:
    """``status`` as it looks once the device has pushed a fresh snapshot."""
    return {
        **status,
        "sys": {**status.get("sys", {}), "unixtime": unixtime},
        "serial": unixtime,
        "_updated": f"2026-08-16 14:{unixtime % 60:02d}:06",
    }


class _FakeApi:
    """Serves a canned all-status payload, swappable between polls."""

    def __init__(self, devices_status: dict[str, Any]) -> None:
        self.devices_status = devices_status

    async def get_all_status(self) -> dict[str, Any]:
        return {"devices_status": self.devices_status}


class _StubHass:
    def async_create_task(self, coro: Any) -> None:
        coro.close()


def _coordinator(devices_status: dict[str, Any] | None = None, **options: Any) -> ShellyCloudCoordinator:
    """A coordinator with only the offline-detection wiring in place.

    ``__init__`` wants a HomeAssistant instance and a ConfigEntry; none of the
    check-in logic does, so bypass it rather than mock all of HA.
    """
    coord = object.__new__(ShellyCloudCoordinator)
    coord._api = _FakeApi(devices_status or {})
    coord.hass = _StubHass()
    coord._entry = SimpleNamespace(options={"enabled_devices": [], **options})
    coord.devices = {}
    coord._known_device_ids = set()
    coord.device_names = {}
    coord._names_attempted = set()
    coord._name_lookup_in_flight = False
    coord.virtual_configs = {}
    coord._vcomp_config_in_flight = False
    coord._sleep_seen = {}
    coord.checkins = {}
    # Repair bookkeeping — set here because the harness bypasses __init__.
    coord._rate_limit_streak = 0
    coord._rate_limit_since = None
    coord._rate_limit_reported = False
    coord._missing_streak = {}
    coord._missing_since = {}
    coord._relay_fault_streak = {}
    coord._relay_fault_since = {}
    coord._relay_healthy_since = {}
    coord.relay_faults = set()
    return coord


# ── CheckinRecord: the window arithmetic ──────────────────────────────


def test_a_device_whose_cadence_is_unknown_gets_the_benefit_of_the_doubt() -> None:
    """Learned cadences live in memory only, so every restart starts blind.
    Without this floor, a user who tightened the window for their metering
    plug would get a false alarm on their quiet devices after every restart."""
    record = CheckinRecord(marker=(1,), last_checkin=0.0, base_window_s=300.0)
    assert record.stale_after_s == REPORT_UNLEARNED_FLOOR_S


def test_the_configured_window_takes_over_once_the_cadence_is_known() -> None:
    record = CheckinRecord(
        marker=(1,), last_checkin=0.0, base_window_s=1800.0, widest_gap_s=60.0
    )
    assert record.stale_after_s == 1800.0


def test_a_slow_device_earns_a_wider_window_than_configured() -> None:
    """The Plus RGBW PM case: 25 min between reports, nothing wrong with it."""
    record = CheckinRecord(
        marker=(1,), last_checkin=0.0, base_window_s=300.0, widest_gap_s=1500.0
    )
    assert record.stale_after_s == 1500.0 * REPORT_GAP_MARGIN
    # ...and it is still considered alive well past the configured 5 minutes.
    assert record.is_reporting(now=1200.0) is True


def test_the_window_never_shrinks_below_the_configured_base() -> None:
    """A chatty device must not get a hair-trigger window from a tiny gap."""
    record = CheckinRecord(
        marker=(1,), last_checkin=0.0, base_window_s=1800.0, widest_gap_s=60.0
    )
    assert record.stale_after_s == 1800.0


def test_the_window_is_capped_however_wide_the_gap() -> None:
    record = CheckinRecord(
        marker=(1,), last_checkin=0.0, base_window_s=0.0, widest_gap_s=10**9
    )
    assert record.stale_after_s == REPORT_STALE_CAP_S


def test_silence_past_the_window_is_an_outage() -> None:
    record = CheckinRecord(
        marker=(1,), last_checkin=0.0, base_window_s=600.0, widest_gap_s=60.0
    )
    assert record.is_reporting(now=599.0) is True
    assert record.is_reporting(now=601.0) is False


# ── _record_checkin: what one poll does to the record ─────────────────


def test_first_sight_counts_as_a_check_in() -> None:
    """Otherwise every device would be 'offline' for one window after each
    restart, which is the loudest possible way to be wrong."""
    coord = _coordinator()
    coord._record_checkin(MAINS_ID, _mains_status(), now=1000.0)

    record = coord.checkins[MAINS_ID]
    assert record.last_checkin == 1000.0
    assert record.is_reporting(now=1000.0) is True


def test_an_unchanged_payload_does_not_refresh_the_check_in() -> None:
    """The whole point: the cloud re-serving a cached snapshot is not a sign
    of life, so the window has to keep running underneath it."""
    coord = _coordinator()
    status = _mains_status()
    coord._record_checkin(MAINS_ID, status, now=1000.0)
    coord._record_checkin(MAINS_ID, dict(status), now=2000.0)

    assert coord.checkins[MAINS_ID].last_checkin == 1000.0


def test_a_new_payload_refreshes_the_check_in_and_teaches_the_cadence() -> None:
    coord = _coordinator()
    coord._record_checkin(MAINS_ID, _mains_status(), now=1000.0)
    coord._record_checkin(MAINS_ID, _pushed(_mains_status(), 42), now=1090.0)

    record = coord.checkins[MAINS_ID]
    assert record.last_checkin == 1090.0
    assert record.widest_gap_s == 90.0


def test_only_the_widest_gap_is_kept() -> None:
    coord = _coordinator()
    coord._record_checkin(MAINS_ID, _mains_status(), now=0.0)
    coord._record_checkin(MAINS_ID, _pushed(_mains_status(), 11), now=300.0)
    coord._record_checkin(MAINS_ID, _pushed(_mains_status(), 22), now=360.0)

    assert coord.checkins[MAINS_ID].widest_gap_s == 300.0


def test_a_gap_that_spans_an_absence_is_not_learned() -> None:
    """The regression that would silently disable the feature: if a real
    outage taught the device its own downtime as a normal cadence, the window
    would widen after every outage until none could ever be detected again."""
    coord = _coordinator()
    coord._record_checkin(MAINS_ID, _mains_status(), now=0.0)
    coord.checkins[MAINS_ID].absent = True  # vanished from all_status
    coord._record_checkin(MAINS_ID, _pushed(_mains_status(), 33), now=7200.0)

    record = coord.checkins[MAINS_ID]
    assert record.widest_gap_s == 0.0, "an outage is not a cadence"
    assert record.last_checkin == 7200.0, "but it is still a check-in"
    assert record.absent is False


def test_an_implausibly_long_gap_is_not_learned_either() -> None:
    """Belt and braces for a device that stayed listed while being dead."""
    coord = _coordinator()
    coord._record_checkin(MAINS_ID, _mains_status(), now=0.0)
    coord._record_checkin(
        MAINS_ID, _pushed(_mains_status(), 44), now=REPORT_LEARN_CAP_S + 1
    )
    assert coord.checkins[MAINS_ID].widest_gap_s == 0.0


# ── Per-class base windows ────────────────────────────────────────────


def test_a_mains_device_uses_the_configured_window() -> None:
    coord = _coordinator()
    coord._record_checkin(MAINS_ID, _mains_status(), now=0.0)
    assert coord.checkins[MAINS_ID].base_window_s == DEFAULT_WINDOW_S


def test_the_configured_window_is_honoured() -> None:
    coord = _coordinator(offline_after_minutes=5)
    coord._record_checkin(MAINS_ID, _mains_status(), now=0.0)
    assert coord.checkins[MAINS_ID].base_window_s == 300.0


def test_a_nonsense_option_falls_back_to_the_default() -> None:
    coord = _coordinator(offline_after_minutes="not-a-number")
    assert coord.offline_after_s == DEFAULT_WINDOW_S


def test_a_ble_beacon_gets_its_own_generous_window() -> None:
    """Measured median silence 19 minutes, maximum three days — and it cannot
    run a heartbeat to do better, so the mains window would be pure noise."""
    coord = _coordinator()
    coord._record_checkin(BLE_ID, _ble_status(), now=0.0)
    assert coord.checkins[BLE_ID].base_window_s == REPORT_STALE_BLE_S


def test_a_sleeping_device_reuses_its_deep_sleep_window() -> None:
    """So offline detection and availability (#13) cannot disagree about what
    'still checking in' means for a battery device.

    The *length* of the window, not the deadline: the deadline shrinks as the
    device stays silent, and this record measures from its own check-in stamp,
    so pairing the two would compound into half the intended tolerance.
    """
    status = {
        "id": "907069591dc4",
        "code": "S3SN-0U12A",
        "cloud": {"connected": False},
        "temperature:0": {"tC": 21.4},
        "devicepower:0": {"battery": {"percent": 92}},
        "sys": {"wakeup_period": 7200, "unixtime": 1786890000, "uptime": 3},
        "_updated": "2026-08-16 14:20:06",
    }
    coord = _coordinator()
    coord._record_checkin("907069591dc4", status, now=1000.0)

    expected = sleep_window_s(status)
    assert expected > 0, "fixture really is a deep-sleep device"
    assert coord.checkins["907069591dc4"].base_window_s == expected

    # A deadline would move with the clock; a window must not.
    later = _coordinator()
    later._record_checkin("907069591dc4", status, now=999_000.0)
    assert later.checkins["907069591dc4"].base_window_s == expected


# ── Full poll: absence handling ───────────────────────────────────────


def test_a_device_missing_from_the_poll_keeps_its_record() -> None:
    """It has to: the record is the only thing that can still report the
    outage once the device is gone from the payload entirely."""
    api_payload = {MAINS_ID: _mains_status()}
    coord = _coordinator(api_payload)
    asyncio.run(coord._async_update_data())

    coord._api.devices_status = {}
    asyncio.run(coord._async_update_data())

    assert MAINS_ID not in coord.devices
    assert MAINS_ID in coord.checkins
    assert coord.checkins[MAINS_ID].absent is True


def test_one_missing_poll_is_not_an_outage() -> None:
    """``/device/all_status`` omits devices spontaneously — that is the whole
    reason the verdict is time-based rather than presence-based."""
    coord = _coordinator({MAINS_ID: _mains_status()})
    asyncio.run(coord._async_update_data())

    coord._api.devices_status = {}
    asyncio.run(coord._async_update_data())

    assert coord.is_reporting(MAINS_ID) is True


def test_sustained_absence_eventually_reads_as_an_outage() -> None:
    coord = _coordinator({MAINS_ID: _mains_status()})
    asyncio.run(coord._async_update_data())

    coord._api.devices_status = {}
    asyncio.run(coord._async_update_data())
    record = coord.checkins[MAINS_ID]
    record.widest_gap_s = 60.0  # cadence already known, so the base applies
    # Rewind the check-in past the window rather than sleeping through it.
    record.last_checkin -= DEFAULT_WINDOW_S + 1

    assert coord.is_reporting(MAINS_ID) is False


def test_a_still_listed_but_silent_device_reads_as_an_outage() -> None:
    """The measured failure mode: the cloud keeps serving the device, keeps
    claiming ``cloud.connected: true``, and only the frozen payload gives it
    away."""
    coord = _coordinator({MAINS_ID: _mains_status()})
    asyncio.run(coord._async_update_data())
    asyncio.run(coord._async_update_data())  # same cached snapshot again

    record = coord.checkins[MAINS_ID]
    record.widest_gap_s = 60.0  # cadence already known, so the base applies
    record.last_checkin -= DEFAULT_WINDOW_S + 1

    assert coord.devices[MAINS_ID]["status"]["cloud"]["connected"] is True
    assert coord.is_reporting(MAINS_ID) is False


def test_an_unknown_device_has_no_verdict() -> None:
    assert _coordinator().is_reporting("never-seen") is None


# ── The entity ────────────────────────────────────────────────────────


class _EntityCoordinator:
    """Minimal coordinator surface the reporting entity actually reads."""

    def __init__(
        self,
        checkins: dict[str, CheckinRecord],
        *,
        devices: dict[str, Any] | None = None,
        last_update_success: bool = True,
    ) -> None:
        self.checkins = checkins
        self.devices = devices if devices is not None else {}
        self.data = self.devices
        self.last_update_success = last_update_success

    def is_reporting(self, device_id: str) -> bool | None:
        record = self.checkins.get(device_id)
        return None if record is None else record.is_reporting(time.monotonic())


def _entity(
    *,
    gone_for: float = 0.0,
    window: float = DEFAULT_WINDOW_S,
    learned_gap: float = 60.0,
    **kwargs: Any,
) -> ShellyReportingBinarySensor:
    record = CheckinRecord(
        marker=(1,),
        last_checkin=time.monotonic() - gone_for,
        base_window_s=window,
        widest_gap_s=learned_gap,
    )
    return ShellyReportingBinarySensor(
        _EntityCoordinator({MAINS_ID: record}, **kwargs), MAINS_ID
    )


def test_the_entity_is_on_while_the_device_reports() -> None:
    assert _entity(gone_for=60.0).is_on is True


def test_the_entity_is_off_once_the_device_goes_silent() -> None:
    assert _entity(gone_for=DEFAULT_WINDOW_S + 60).is_on is False


def test_a_device_of_unknown_cadence_is_not_declared_dead_at_the_base_window() -> None:
    """Same grace as after a restart, seen from the entity side."""
    entity = _entity(gone_for=DEFAULT_WINDOW_S + 60, learned_gap=0.0)
    assert entity.is_on is True


def test_the_entity_stays_available_when_the_device_is_gone() -> None:
    """The crux. The base class would make this unavailable — it keys off the
    device being present and online — and an unavailable entity cannot report
    the outage it exists to report."""
    entity = _entity(gone_for=DEFAULT_WINDOW_S + 60)
    assert entity.coordinator.devices == {}, "device absent from the poll"
    assert entity.available is True
    assert entity.is_on is False


def test_a_broken_poll_makes_the_verdict_unavailable_not_negative() -> None:
    """During a cloud outage we know nothing about any device; declaring the
    whole fleet dead would be worse than admitting we cannot tell."""
    entity = _entity(gone_for=60.0, last_update_success=False)
    assert entity.available is False


def test_an_entity_for_an_unseen_device_is_unavailable() -> None:
    entity = ShellyReportingBinarySensor(_EntityCoordinator({}), MAINS_ID)
    assert entity.available is False
    assert entity.is_on is None


def test_the_entity_publishes_the_window_it_judged_against() -> None:
    """Per-device and adaptive, so without it there is no way to see why one
    device tolerates far more silence than another."""
    entity = _entity(window=1800.0)
    assert entity.extra_state_attributes == {"stale_after_s": 1800}


def test_the_entity_has_no_attributes_for_an_unseen_device() -> None:
    entity = ShellyReportingBinarySensor(_EntityCoordinator({}), MAINS_ID)
    assert entity.extra_state_attributes is None


# ── Entity creation ───────────────────────────────────────────────────


def test_every_device_gets_exactly_one_reporting_sensor() -> None:
    coordinator = _EntityCoordinator({})
    created: set[str] = set()

    first = _create_reporting_sensor(MAINS_ID, created, coordinator)
    second = _create_reporting_sensor(MAINS_ID, created, coordinator)

    assert len(first) == 1
    assert first[0].unique_id == f"{MAINS_ID}_reporting"
    assert second == [], "re-running discovery must not duplicate it"


def test_a_device_with_no_readable_status_still_gets_one() -> None:
    """It is created ahead of the status check on purpose: a device we cannot
    classify is if anything more worth watching, not less."""
    assert _create_reporting_sensor(BLE_ID, set(), _EntityCoordinator({})) != []


# ── Rediscovery: the flap the cloud produces on its own ───────────────


class _SetupCoordinator(_EntityCoordinator):
    """Adds the bits ``async_setup_entry`` touches on top of the entity stub."""

    def is_enabled(self, device_id: str) -> bool:
        return True


def test_a_rediscovered_device_does_not_get_a_second_reporting_sensor(monkeypatch) -> None:
    """``/device/all_status`` drops devices on its own, so a device is
    rediscovered routinely. ``async_add_device`` deliberately forgets a
    rediscovered device's component entities so they can be rebuilt from a
    fresher status — but the reporting sensor derives nothing from status, and
    re-adding it makes Home Assistant log a duplicate-unique-ID error.

    Found by a live run against a real Home Assistant, not by the unit tests:
    the coordinator dispatches ``SIGNAL_NEW_DEVICE`` *before* it commits the
    new snapshot, so the rediscovery path sees an empty status and every
    status-derived builder bows out — leaving this one to collide alone.
    """
    from custom_components.shelly_cloud_diy import binary_sensor as mod

    record = CheckinRecord(marker=(1,), last_checkin=time.monotonic(),
                           base_window_s=DEFAULT_WINDOW_S, widest_gap_s=60.0)
    coordinator = _SetupCoordinator({MAINS_ID: record}, devices={MAINS_ID: {"status": {}}})

    handlers: list[Any] = []
    monkeypatch.setattr(
        mod, "async_dispatcher_connect",
        lambda hass, signal, cb: handlers.append(cb) or (lambda: None),
    )

    added: list[Any] = []
    hass = SimpleNamespace(data={"shelly_cloud_diy": {"e1": coordinator}})
    entry = SimpleNamespace(entry_id="e1", async_on_unload=lambda cb: None)

    asyncio.run(mod.async_setup_entry(hass, entry, lambda ents: added.extend(ents)))
    first = [e for e in added if e.unique_id.endswith("_reporting")]
    assert len(first) == 1, "one reporting sensor at setup"

    # The device drops out of a poll and comes back — the coordinator fires
    # SIGNAL_NEW_DEVICE again.
    handlers[0](MAINS_ID)

    again = [e for e in added if e.unique_id.endswith("_reporting")]
    assert len(again) == 1, "rediscovery must not add a second one"
