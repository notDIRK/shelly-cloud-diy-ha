"""Unit tests for deep-sleep device availability (issue #13).

A battery device (Shelly H&T Gen3 and friends) is awake for a few seconds per
``sys.wakeup_period`` and asleep the rest of the time. Shelly Cloud keeps
serving the last snapshot such a device pushed, and that snapshot is captured
seconds after boot — before the device's cloud session is established — so it
carries ``cloud.connected: false`` permanently while the readings inside it
are current. Deriving availability from that flag made every H&T unavailable
forever after an OTA update changed the firmware's boot timing.

These tests pin the replacement contract: sleeping devices stay available
while they keep checking in, and go unavailable once they stop. They exercise
the real production code — ``sleep_period_s``, ``checkin_marker``,
``ShellyCloudCoordinator._evaluate_sleep_state``, the record written by
``_async_update_data`` and ``ShellyBaseEntity.available`` — with a lightweight
fake coordinator and a fake API, so no running Home Assistant instance is
required and they behave identically against any HA version.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.shelly_cloud_diy.coordinator import (
    SLEEP_ASSUMED_PERIOD_S,
    SLEEP_STALE_CAP_S,
    SLEEP_STALE_FLOOR_S,
    SLEEP_STALE_MULTIPLIER,
    ShellyCloudCoordinator,
    checkin_marker,
    sleep_period_s,
)
from custom_components.shelly_cloud_diy.entities.base import ShellyBaseEntity

DEVICE_ID = "907069591dc4"
MAINS_ID = "aabbccddeeff"

# Window the H&T's 7200 s wakeup period produces: 15840 s, above the 4 h floor.
HT_WINDOW = 7200 * SLEEP_STALE_MULTIPLIER

# Redacted status of the H&T Gen3 from issue #13, trimmed to the fields the
# availability path reads. Note the combination that caused the bug: every
# transport flag false, ``uptime: 4`` right after a deep-sleep wake, and
# perfectly current readings in the same payload.
HT_GEN3_STATUS: dict[str, Any] = {
    "id": DEVICE_ID,
    "code": "S3SN-0U12A",
    "cloud": {"connected": False},
    "ws": {"connected": False},
    "mqtt": {"connected": False},
    "wifi": {"status": "got ip", "rssi": -33},
    "temperature:0": {"id": 0, "tC": 24.6, "tF": 76.4},
    "humidity:0": {"id": 0, "rh": 43},
    "devicepower:0": {"battery": {"V": 6.12, "percent": 100}, "external": {"present": False}},
    "sys": {
        "unixtime": 1785659343,
        "uptime": 4,
        "wakeup_period": 7200,
        "wakeup_reason": {"boot": "deepsleep_wake", "cause": "periodic"},
    },
    "ts": 1785589264.33,
    "serial": 1785659343,
    "_updated": "2026-08-02 08:29:04",
    "_sleeping": True,
}

# Redacted status of the H&T Gen3 from issue #32: batteries removed, running
# off USB-C — and *still* waking out of deep sleep every 600 s, which is why
# the cached snapshot keeps reporting a dead cloud session for a live device.
USB_SLEEPER_STATUS: dict[str, Any] = {
    "id": "84fce63f8338",
    "code": "S3SN-0U12A",
    "cloud": {"connected": False},
    "ws": {"connected": False},
    "mqtt": {"connected": False},
    "wifi": {"status": "got ip", "rssi": -51},
    "temperature:0": {"id": 0, "tC": 25.8, "tF": 78.5},
    "humidity:0": {"id": 0, "rh": 43.7},
    "devicepower:0": {
        "id": 0,
        "battery": {"V": 0, "percent": 0},
        "external": {"present": True},
    },
    "sys": {
        "unixtime": 1786993324,
        "uptime": 5,
        "wakeup_period": 600,
        "wakeup_reason": {"boot": "deepsleep_wake", "cause": "status_update"},
    },
    "serial": 1786993324,
    "_updated": "2026-08-17 19:02:05",
    "_sleeping": True,
}

# The counter-case #13 asked for: same hardware on permanent power, awake for
# hours, only the wakeup_period left over from its battery days. Its transport
# flag means something again, so it must not be excused by the sleep window.
AWAKE_ON_USB_STATUS: dict[str, Any] = {
    **USB_SLEEPER_STATUS,
    "sys": {
        "unixtime": 1786993324,
        "uptime": 43200,
        "wakeup_period": 600,
        "wakeup_reason": {"boot": "poweron", "cause": None},
    },
}
del AWAKE_ON_USB_STATUS["_sleeping"]

# What a mains-powered device looks like while genuinely disconnected: no
# wakeup period, no sleeping marker. Must stay unavailable.
MAINS_OFFLINE_STATUS: dict[str, Any] = {
    "id": MAINS_ID,
    "code": "SNSW-001X16EU",
    "cloud": {"connected": False},
    "switch:0": {"id": 0, "output": False},
    "sys": {"unixtime": 1785659343, "uptime": 98123},
    "_updated": "2026-08-02 08:29:04",
}


def _pushed(status: dict[str, Any], unixtime: int = 1785666543) -> dict[str, Any]:
    """Return ``status`` as it looks after the device pushed a new snapshot."""
    return {
        **status,
        "sys": {**status["sys"], "unixtime": unixtime, "uptime": 3},
        "serial": unixtime,
        "_updated": "2026-08-02 10:29:03",
    }


def _coordinator() -> ShellyCloudCoordinator:
    """A coordinator with only the sleep bookkeeping wired up.

    ``__init__`` needs a HomeAssistant instance and a config entry; the sleep
    evaluation needs neither, so bypass it rather than mock all of HA.
    """
    coord = object.__new__(ShellyCloudCoordinator)
    coord._sleep_seen = {}
    return coord


class _FakeCoordinator:
    """Minimal coordinator: the base entity only reads ``.devices[id]``."""

    def __init__(self, device_id: str, entry: dict[str, Any]) -> None:
        self.devices = {device_id: entry}
        self.data = self.devices
        self.last_update_success = True


def _entity(**entry: Any) -> ShellyBaseEntity:
    """Build a real base entity over a device record with the given keys."""
    record: dict[str, Any] = {
        "status": HT_GEN3_STATUS,
        "device_code": "S3SN-0U12A",
        "name": None,
        **entry,
    }
    return ShellyBaseEntity(_FakeCoordinator(DEVICE_ID, record), DEVICE_ID)


# ── sleep_period_s ────────────────────────────────────────────────────


def test_sleep_period_reads_wakeup_period() -> None:
    assert sleep_period_s(HT_GEN3_STATUS) == 7200


def test_sleep_period_zero_for_mains_device() -> None:
    assert sleep_period_s(MAINS_OFFLINE_STATUS) == 0


def test_sleep_period_falls_back_for_sleeping_marker_only() -> None:
    """Gen1 battery devices carry no wakeup_period, only the cloud marker."""
    status = {"cloud": {"connected": False}, "_sleeping": True}
    assert sleep_period_s(status) == SLEEP_ASSUMED_PERIOD_S


def test_externally_powered_device_that_stopped_sleeping_is_not_sleeping() -> None:
    """USB power *and* no sign of sleep — the transport flag is honest again."""
    assert sleep_period_s(AWAKE_ON_USB_STATUS) == 0


def test_externally_powered_device_that_keeps_sleeping_still_sleeps() -> None:
    """Issue #32: an H&T Gen3 on USB-C keeps its wakeup schedule.

    The reporter pulled the batteries and ran the device off USB-C. It still
    woke every 600 s, so the cached snapshot still carried
    ``cloud.connected: false`` — and treating external power as proof of
    being awake flapped every entity of that device between available and
    unavailable roughly every six minutes.
    """
    assert sleep_period_s(USB_SLEEPER_STATUS) == 600


def test_deep_sleep_wake_alone_proves_sleep_on_external_power() -> None:
    """Without the cloud marker, a fresh deep-sleep boot is evidence enough."""
    status = {**USB_SLEEPER_STATUS}
    del status["_sleeping"]
    assert sleep_period_s(status) == 600


def test_stale_deep_sleep_wake_expires_once_uptime_outgrows_the_period() -> None:
    """A device that woke and then stayed awake must not look asleep forever."""
    status = {**USB_SLEEPER_STATUS}
    del status["_sleeping"]
    status["sys"] = {**status["sys"], "uptime": 601}
    assert sleep_period_s(status) == 0


@pytest.mark.parametrize("uptime", [None, "5", True, -1, {"s": 5}])
def test_unusable_uptime_is_not_read_as_a_fresh_wake(uptime: Any) -> None:
    """Only a real, plausible uptime may keep a mains-fed device sleeping."""
    status = {**USB_SLEEPER_STATUS}
    del status["_sleeping"]
    status["sys"] = {**status["sys"], "uptime": uptime}
    assert sleep_period_s(status) == 0


@pytest.mark.parametrize(
    "period",
    [0, -1, None, "7200", True, False, {"secs": 7200}],
)
def test_sleep_period_rejects_unusable_wakeup_values(period: Any) -> None:
    """A bogus wakeup_period must not be mistaken for a sleeping device.

    ``True`` is the interesting one: it is an ``int`` subclass in Python and
    would otherwise pass as a 1-second period.
    """
    status = {"sys": {"wakeup_period": period}}
    assert sleep_period_s(status) == 0


def test_sleep_period_survives_garbage_status() -> None:
    assert sleep_period_s({}) == 0
    assert sleep_period_s({"sys": "not-a-dict"}) == 0
    assert sleep_period_s({"devicepower:0": "not-a-dict", "_sleeping": True}) == (
        SLEEP_ASSUMED_PERIOD_S
    )
    assert sleep_period_s(None) == 0  # type: ignore[arg-type]


# ── checkin_marker ────────────────────────────────────────────────────


def test_checkin_marker_ignores_fields_that_are_not_check_ins() -> None:
    """Only a real push must move the fingerprint — not a changed reading.

    Shelly Cloud re-serves the cached snapshot verbatim between wakes, so a
    marker that keyed off measurement values would never settle.
    """
    same_device_new_reading = {**HT_GEN3_STATUS, "temperature:0": {"id": 0, "tC": 25.9}}
    assert checkin_marker(same_device_new_reading) == checkin_marker(HT_GEN3_STATUS)


def test_checkin_marker_changes_when_the_device_pushes_again() -> None:
    assert checkin_marker(_pushed(HT_GEN3_STATUS)) != checkin_marker(HT_GEN3_STATUS)


def test_checkin_marker_survives_garbage_status() -> None:
    assert checkin_marker({"sys": "not-a-dict"}) == (None, None, None, None, None)
    assert checkin_marker(None) == ()  # type: ignore[arg-type]


# ── _evaluate_sleep_state ─────────────────────────────────────────────


def test_first_sight_of_a_sleeping_device_starts_the_window() -> None:
    """No history is not evidence of death — and an HA restart clears it."""
    coord = _coordinator()
    sleeping, stale_at = coord._evaluate_sleep_state(DEVICE_ID, HT_GEN3_STATUS, 1000.0)
    assert sleeping is True
    assert stale_at == pytest.approx(1000.0 + HT_WINDOW)


def test_deadline_does_not_move_while_the_device_stays_silent() -> None:
    """Re-polling the same cached snapshot must not extend the window."""
    coord = _coordinator()
    _, first = coord._evaluate_sleep_state(DEVICE_ID, HT_GEN3_STATUS, 1000.0)

    for offset in (5.0, 3600.0, HT_WINDOW - 1.0, HT_WINDOW + 1.0):
        sleeping, stale_at = coord._evaluate_sleep_state(
            DEVICE_ID, HT_GEN3_STATUS, 1000.0 + offset
        )
        assert sleeping is True
        assert stale_at == first, offset


def test_a_new_check_in_resets_the_window() -> None:
    coord = _coordinator()
    coord._evaluate_sleep_state(DEVICE_ID, HT_GEN3_STATUS, 1000.0)

    # Device checks in just before it would have expired…
    checkin_at = 1000.0 + HT_WINDOW - 10.0
    _, stale_at = coord._evaluate_sleep_state(
        DEVICE_ID, _pushed(HT_GEN3_STATUS), checkin_at
    )
    # …so the window now runs from there, not from the original sighting.
    assert stale_at == pytest.approx(checkin_at + HT_WINDOW)


def test_window_is_never_shorter_than_the_floor() -> None:
    """A 10-minute wakeup period must not produce a 22-minute window."""
    coord = _coordinator()
    status = {**HT_GEN3_STATUS, "sys": {**HT_GEN3_STATUS["sys"], "wakeup_period": 600}}

    _, stale_at = coord._evaluate_sleep_state(DEVICE_ID, status, 0.0)
    assert stale_at == pytest.approx(SLEEP_STALE_FLOOR_S)


def test_window_is_capped() -> None:
    """An absurd wakeup period must not keep a dead device alive forever."""
    coord = _coordinator()
    status = {
        **HT_GEN3_STATUS,
        "sys": {**HT_GEN3_STATUS["sys"], "wakeup_period": 30 * 24 * 3600},
    }

    _, stale_at = coord._evaluate_sleep_state(DEVICE_ID, status, 0.0)
    assert stale_at == pytest.approx(SLEEP_STALE_CAP_S)


def test_mains_device_is_never_treated_as_sleeping() -> None:
    coord = _coordinator()
    assert coord._evaluate_sleep_state(MAINS_ID, MAINS_OFFLINE_STATUS, 0.0) == (
        False,
        None,
    )
    assert MAINS_ID not in coord._sleep_seen


def test_history_is_dropped_when_a_device_stops_sleeping() -> None:
    """A battery device that stopped sleeping must not keep stale history."""
    coord = _coordinator()
    coord._evaluate_sleep_state(DEVICE_ID, HT_GEN3_STATUS, 0.0)
    assert DEVICE_ID in coord._sleep_seen

    assert coord._evaluate_sleep_state(DEVICE_ID, AWAKE_ON_USB_STATUS, 1.0) == (
        False,
        None,
    )
    assert DEVICE_ID not in coord._sleep_seen


def test_usb_powered_sleeper_keeps_its_check_in_window() -> None:
    """Issue #32: the window survives the switch to external power."""
    coord = _coordinator()
    sleeping, stale_at = coord._evaluate_sleep_state(DEVICE_ID, USB_SLEEPER_STATUS, 0.0)
    assert sleeping is True
    # 600 s period is below the floor, so the floor decides the window.
    assert stale_at == SLEEP_STALE_FLOOR_S
    assert DEVICE_ID in coord._sleep_seen


# ── _async_update_data: the record the entities actually read ─────────


class _FakeApi:
    """Stands in for ShellyCloudControl: serves a canned all-status payload."""

    def __init__(self, devices_status: dict[str, Any]) -> None:
        self._devices_status = devices_status
        self.name_lookups: list[list[str]] = []

    async def get_all_status(self) -> dict[str, Any]:
        return {"devices_status": self._devices_status}


class _StubHass:
    """Captures background tasks instead of running them."""

    def __init__(self) -> None:
        self.tasks: list[Any] = []

    def async_create_task(self, coro: Any) -> None:
        self.tasks.append(coro)
        coro.close()  # never awaited — keep asyncio from warning about it


@pytest.fixture(autouse=True)
def _inert_repair_issues(monkeypatch):
    """Neutralise the repair-issue wrappers for this module.

    A successful poll ends by re-evaluating the repair cards, which reaches
    into the real issue registry. These tests drive the poll against a stub
    ``hass`` that has no registry and are about sleep bookkeeping only, so
    the wrappers are stubbed out — the same split ``test_repair_issues.py``
    uses. Their behaviour is covered there and in the live harness.
    """
    from custom_components.shelly_cloud_diy import coordinator as coordinator_mod

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
    monkeypatch.setattr(
        coordinator_mod, "async_manage_device_health_issue",
        lambda hass, entry, *, active, findings, names: None,
    )


def _live_coordinator(devices_status: dict[str, Any]) -> ShellyCloudCoordinator:
    """A coordinator whose real ``_async_update_data`` can be driven."""
    coord = object.__new__(ShellyCloudCoordinator)
    coord._api = _FakeApi(devices_status)
    coord.hass = _StubHass()
    # No device is opted in, so the poll never dispatches SIGNAL_NEW_DEVICE
    # (which would need a real HA dispatcher).
    coord._entry = SimpleNamespace(options={"enabled_devices": []})
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
    coord._health_streak = {}
    coord._health_since = {}
    coord.device_health = {}
    return coord


def test_poll_writes_the_sleep_keys_into_the_device_record() -> None:
    """The seam the fix hangs on: without these keys the bug is back."""
    coord = _live_coordinator(
        {DEVICE_ID: HT_GEN3_STATUS, MAINS_ID: MAINS_OFFLINE_STATUS}
    )
    before = time.monotonic()
    devices = asyncio.run(coord._async_update_data())

    ht = devices[DEVICE_ID]
    assert ht["online"] is False, "the cloud really does report it as offline"
    assert ht["sleeping"] is True
    assert ht["sleep_stale_at"] >= before + HT_WINDOW

    mains = devices[MAINS_ID]
    assert mains["online"] is False
    assert mains["sleeping"] is False
    assert mains["sleep_stale_at"] is None


def test_poll_result_makes_a_sleeping_entity_available() -> None:
    """End to end: cloud payload → record → entity availability."""
    coord = _live_coordinator({DEVICE_ID: HT_GEN3_STATUS})
    devices = asyncio.run(coord._async_update_data())

    entity = ShellyBaseEntity(_FakeCoordinator(DEVICE_ID, devices[DEVICE_ID]), DEVICE_ID)
    assert entity.available is True


def test_name_lookup_covers_sleeping_devices_but_only_once() -> None:
    """Sleeping devices need the alias lookup — and must not re-fire per poll.

    The lookup shares the account's 1 req/s budget, so a device the user never
    renamed would otherwise burn one request on every single poll.
    """
    coord = _live_coordinator({DEVICE_ID: HT_GEN3_STATUS})

    asyncio.run(coord._async_update_data())
    assert len(coord.hass.tasks) == 1, "sleeping device must be looked up"

    # Simulate the lookup completing without finding an alias for it.
    coord._name_lookup_in_flight = False
    coord._names_attempted.add(DEVICE_ID)

    asyncio.run(coord._async_update_data())
    assert len(coord.hass.tasks) == 1, "nameless device must not be re-fetched"


def test_check_in_history_survives_a_device_missing_from_one_poll() -> None:
    """/device/all_status intermittently omits devices; that must not reset
    the window, or a flapping device could never go stale."""
    coord = _live_coordinator({DEVICE_ID: HT_GEN3_STATUS})
    devices = asyncio.run(coord._async_update_data())
    first_deadline = devices[DEVICE_ID]["sleep_stale_at"]

    coord._api._devices_status = {}  # device omitted from this poll
    asyncio.run(coord._async_update_data())
    assert DEVICE_ID in coord._sleep_seen

    coord._api._devices_status = {DEVICE_ID: HT_GEN3_STATUS}  # …and back
    devices = asyncio.run(coord._async_update_data())
    assert devices[DEVICE_ID]["sleep_stale_at"] == first_deadline


# ── ShellyBaseEntity.available ────────────────────────────────────────


def _future() -> float:
    return time.monotonic() + 3600.0


def _past() -> float:
    return time.monotonic() - 1.0


def test_available_when_cloud_reports_online() -> None:
    assert _entity(online=True, sleeping=False, sleep_stale_at=None).available is True


def test_online_short_circuits_regardless_of_sleep_state() -> None:
    """An awake battery device is available even past its staleness window."""
    assert _entity(online=True, sleeping=True, sleep_stale_at=_past()).available is True


def test_sleeping_and_fresh_is_available_despite_offline_cloud_flag() -> None:
    """The regression from #13: this used to report unavailable."""
    assert _entity(online=False, sleeping=True, sleep_stale_at=_future()).available is True


def test_sleeping_but_stale_is_unavailable() -> None:
    assert _entity(online=False, sleeping=True, sleep_stale_at=_past()).available is False


def test_offline_mains_device_stays_unavailable() -> None:
    assert _entity(online=False, sleeping=False, sleep_stale_at=None).available is False


def test_record_without_sleep_keys_behaves_as_before() -> None:
    """Records written before this change (or by other code paths) must not
    accidentally become available."""
    assert _entity(online=False).available is False
    assert _entity(online=True).available is True


def test_sleeping_without_a_deadline_is_available() -> None:
    """Defensive: a malformed deadline must fail open, not hide a live sensor."""
    assert _entity(online=False, sleeping=True).available is True
    assert _entity(online=False, sleeping=True, sleep_stale_at=None).available is True


def test_unknown_device_is_unavailable() -> None:
    """``device_data`` returns {} for a device that vanished from the poll."""
    entity = ShellyBaseEntity(_FakeCoordinator("someone-else", {}), DEVICE_ID)
    assert entity.available is False
