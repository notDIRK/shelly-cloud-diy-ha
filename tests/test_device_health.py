"""Unit tests for the threshold health checks ("Doctor").

Every threshold pinned here was chosen against a real 64-device account
(``.planning/messungen/snapshot.json``, gitignored). Cross-checked against
that payload while this was written, the evaluator finds 19 findings on 14
devices with firmware off — an -82 dBm link, a switch at 78.5 °C, five dead
add-on probes reporting ``errors: ["read"]`` — and **no** finding on any of
the 29 BLE devices other than the gateway RSSI. Those numbers are what the
cases below encode in a form the suite can keep honest.

The coordinator-level tests drive the real production path with the same
lightweight fake coordinator the relay-fault tests use.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.shelly_cloud_diy import coordinator as coordinator_mod
from custom_components.shelly_cloud_diy.const import (
    CONF_DEVICE_HEALTH_DETECTION,
    CONF_DEVICE_HEALTH_FIRMWARE,
    DEVICE_HEALTH_FIRMWARE_DEFAULT,
    DOMAIN,
)
from custom_components.shelly_cloud_diy.coordinator import ShellyCloudCoordinator
from custom_components.shelly_cloud_diy.device_health import (
    CHECK_COMPONENT_ERROR,
    CHECK_FILESYSTEM,
    CHECK_FIRMWARE,
    CHECK_RAM,
    CHECK_RESTART,
    CHECK_TEMPERATURE,
    CHECK_WIFI,
    FREE_ERROR_FRACTION,
    FREE_WARNING_FRACTION,
    HEALTH_MIN_SECONDS,
    HEALTH_MIN_STREAK,
    RSSI_ERROR_DBM,
    RSSI_WARNING_DBM,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    TEMPERATURE_ERROR_C,
    TEMPERATURE_WARNING_C,
    HealthFinding,
    evaluate_device_health,
    health_verdict,
    summarise_findings,
)
from custom_components.shelly_cloud_diy.repair_issues import (
    ISSUE_DEVICE_HEALTH,
    issue_id,
)

DEVICE = "84fce63b699c"
OTHER = "d0ef76c69dd8"
BLU = "XB106582483818186"

# The measured healthy baseline of the account, so that a payload built from
# these defaults produces exactly zero findings and any test that wants one
# has to say so explicitly.
RAM_SIZE = 262108
RAM_FREE = 81072          # 30.9 % free
FS_SIZE = 917504
FS_FREE = 458752          # 50.0 % free


def _gen2(
    *,
    rssi: int = -59,
    tc: float | None = 63.36,
    ram_free: int = RAM_FREE,
    fs_free: int = FS_FREE,
    restart: bool = False,
    updates: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A Gen2 status shaped like the real ones, healthy unless told otherwise."""
    switch: dict[str, Any] = {"id": 0, "output": False, "apower": 0.0}
    if tc is not None:
        switch["temperature"] = {"tC": tc, "tF": tc * 1.8 + 32}
    status: dict[str, Any] = {
        "wifi": {"sta_ip": "192.168.1.5", "status": "got ip", "rssi": rssi},
        "cloud": {"connected": True},
        "switch:0": switch,
        "sys": {
            "ram_size": RAM_SIZE,
            "ram_free": ram_free,
            "fs_size": FS_SIZE,
            "fs_free": fs_free,
            "restart_required": restart,
            "available_updates": updates if updates is not None else {},
            "uptime": 7815949,
        },
    }
    if extra:
        status.update(extra)
    return status


def _gble(*, rssi: int = -59, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """A BLE/gateway-bridged status, in the shape the cloud really sends."""
    status: dict[str, Any] = {
        "devicepower:0": {
            "id": 0,
            "battery": {"V": None, "percent": 100, "low": None},
            "errors": [],
        },
        "temperature:0": {"id": 0, "tC": 21.4},
        "humidity:0": {"id": 0, "rh": 47.5},
        "reporter": {"id": "146221729481748", "rssi": rssi, "inrange": False},
        "_dev_info": {
            "id": BLU, "gen": "GBLE", "code": "SBHT-003C", "online": True,
        },
    }
    if extra:
        status.update(extra)
    return status


def _checks(findings: list[HealthFinding]) -> set[str]:
    return {f.check for f in findings}


def _one(findings: list[HealthFinding], check: str) -> HealthFinding:
    matching = [f for f in findings if f.check == check]
    assert len(matching) == 1, f"expected exactly one {check}, got {matching}"
    return matching[0]


# ── The baseline: a healthy device says nothing ───────────────────────


def test_a_healthy_gen2_device_produces_no_findings() -> None:
    """Built from the account's measured medians. If this ever starts
    producing a finding, a threshold has drifted into the normal range."""
    assert evaluate_device_health(_gen2()) == []


def test_an_empty_or_broken_status_is_survivable() -> None:
    assert evaluate_device_health({}) == []
    assert evaluate_device_health(None) == []  # type: ignore[arg-type]


# ── Wi-Fi signal ──────────────────────────────────────────────────────


def test_a_strong_link_is_not_a_finding() -> None:
    assert evaluate_device_health(_gen2(rssi=RSSI_WARNING_DBM + 1)) == []


def test_the_warning_line_is_inclusive() -> None:
    finding = _one(evaluate_device_health(_gen2(rssi=RSSI_WARNING_DBM)), CHECK_WIFI)
    assert finding.severity == SEVERITY_WARNING


def test_the_measured_worst_link_is_a_warning_not_an_error() -> None:
    """-82 dBm is the worst Wi-Fi RSSI in the real snapshot. It has to land
    between the two lines, or one of them is in the wrong place."""
    finding = _one(evaluate_device_health(_gen2(rssi=-82)), CHECK_WIFI)
    assert finding.severity == SEVERITY_WARNING
    assert finding.detail == "-82 dBm"


def test_the_error_line_is_inclusive() -> None:
    finding = _one(evaluate_device_health(_gen2(rssi=RSSI_ERROR_DBM)), CHECK_WIFI)
    assert finding.severity == SEVERITY_ERROR


def test_a_missing_rssi_reading_is_not_a_perfect_link() -> None:
    """Zero is what a device reports when it has no measurement. Read as a
    number it is the best possible signal, which is the wrong answer in the
    one direction that matters."""
    assert evaluate_device_health(_gen2(rssi=0)) == []
    assert evaluate_device_health(_gen2(rssi=None)) == []  # type: ignore[arg-type]


# ── Temperature ───────────────────────────────────────────────────────


def test_a_warm_but_normal_switch_is_not_a_finding() -> None:
    assert evaluate_device_health(_gen2(tc=TEMPERATURE_WARNING_C - 0.1)) == []


def test_the_measured_hottest_switch_crosses_the_warning_line() -> None:
    """78.5 °C is the hottest reading in the real snapshot."""
    finding = _one(evaluate_device_health(_gen2(tc=78.5)), CHECK_TEMPERATURE)
    assert finding.severity == SEVERITY_WARNING
    assert finding.component == "switch:0"
    assert finding.detail == "78.5 °C"


def test_the_temperature_error_line_is_inclusive() -> None:
    finding = _one(
        evaluate_device_health(_gen2(tc=TEMPERATURE_ERROR_C)), CHECK_TEMPERATURE
    )
    assert finding.severity == SEVERITY_ERROR


def test_a_standalone_temperature_component_is_never_device_temperature() -> None:
    """An add-on probe measures the world, not the device.

    A sensor in a boiler flow pipe or a sauna is above 70 °C by design, and
    the payload gives us nothing to tell that apart from an overheating
    device — so a standalone ``temperature:<id>`` must produce no
    temperature finding at all, however hot it reads. In the 64-device
    sample both genuinely hot readings come from ``switch:0``, so this
    exclusion costs nothing measured.
    """
    status = _gen2(extra={"temperature:100": {"id": 100, "tC": 91.0}})
    assert evaluate_device_health(status) == []


def test_an_add_on_probe_is_still_judged_on_its_own_error() -> None:
    """Excluding the reading must not exclude the component: the real
    payload carries ``temperature:100 → errors: ["read"]``, which is the
    device reporting a broken probe and belongs in the card."""
    status = _gen2(
        extra={"temperature:100": {"id": 100, "tC": 91.0, "errors": ["read"]}}
    )
    findings = evaluate_device_health(status)
    assert [f.check for f in findings] == [CHECK_COMPONENT_ERROR]


def test_a_null_temperature_is_not_measured_as_zero() -> None:
    status = _gen2(extra={"temperature:100": {"id": 100, "tC": None}})
    assert evaluate_device_health(status) == []


# ── Free memory and free filesystem ───────────────────────────────────


def test_free_memory_just_above_the_line_is_silence() -> None:
    ram = int(RAM_SIZE * FREE_WARNING_FRACTION) + 1
    assert evaluate_device_health(_gen2(ram_free=ram)) == []


def test_free_memory_at_the_line_warns() -> None:
    ram = int(RAM_SIZE * FREE_WARNING_FRACTION) - 1
    finding = _one(evaluate_device_health(_gen2(ram_free=ram)), CHECK_RAM)
    assert finding.severity == SEVERITY_WARNING


def test_free_memory_below_the_error_line_is_an_error() -> None:
    ram = int(RAM_SIZE * FREE_ERROR_FRACTION) - 1
    finding = _one(evaluate_device_health(_gen2(ram_free=ram)), CHECK_RAM)
    assert finding.severity == SEVERITY_ERROR


def test_free_storage_uses_the_same_two_lines() -> None:
    warn = _one(
        evaluate_device_health(
            _gen2(fs_free=int(FS_SIZE * FREE_WARNING_FRACTION) - 1)
        ),
        CHECK_FILESYSTEM,
    )
    err = _one(
        evaluate_device_health(
            _gen2(fs_free=int(FS_SIZE * FREE_ERROR_FRACTION) - 1)
        ),
        CHECK_FILESYSTEM,
    )
    assert (warn.severity, err.severity) == (SEVERITY_WARNING, SEVERITY_ERROR)


def test_a_device_reporting_no_capacity_is_not_judged() -> None:
    """A zero total would make every device 0 % free — an integration-wide
    false alarm out of one missing field."""
    status = _gen2()
    status["sys"]["ram_size"] = 0
    status["sys"].pop("fs_size")
    assert evaluate_device_health(status) == []


# ── Restart pending and component errors ──────────────────────────────


def test_a_pending_restart_is_reported() -> None:
    finding = _one(evaluate_device_health(_gen2(restart=True)), CHECK_RESTART)
    assert finding.severity == SEVERITY_ERROR


def test_a_component_reporting_its_own_error_is_believed() -> None:
    """The one check with no threshold to argue about: measured on real
    hardware as ``temperature:100 -> ["read"]``."""
    status = _gen2(
        extra={"temperature:100": {"id": 100, "tC": None, "errors": ["read"]}}
    )
    finding = _one(evaluate_device_health(status), CHECK_COMPONENT_ERROR)
    assert (finding.component, finding.detail) == ("temperature:100", "read")


def test_an_empty_error_list_is_not_an_error() -> None:
    status = _gen2(extra={"temperature:100": {"id": 100, "tC": 20.0, "errors": []}})
    assert evaluate_device_health(status) == []


def test_every_failing_component_is_its_own_finding() -> None:
    """One device in the snapshot has five dead add-on probes. Collapsing
    them to one would hide how much of the add-on is gone."""
    status = _gen2(
        extra={
            f"temperature:{i}": {"id": i, "errors": ["read"]}
            for i in range(100, 105)
        }
    )
    assert len(evaluate_device_health(status)) == 5


# ── Firmware: measured, and therefore opt-in ──────────────────────────


UPDATES = {"stable": {"version": "2.0.0"}}


def test_a_pending_firmware_update_is_silent_by_default() -> None:
    """24 of 35 devices on the measured account had one pending. Default-on
    would mean a permanently lit card that teaches the user to ignore it."""
    assert DEVICE_HEALTH_FIRMWARE_DEFAULT is False
    assert evaluate_device_health(_gen2(updates=UPDATES)) == []


def test_a_pending_firmware_update_is_information_when_asked_for() -> None:
    findings = evaluate_device_health(
        _gen2(updates=UPDATES), include_firmware=True
    )
    finding = _one(findings, CHECK_FIRMWARE)
    assert finding.severity == SEVERITY_INFO
    assert finding.detail == "stable 2.0.0"


def test_a_device_on_the_current_firmware_says_nothing_either_way() -> None:
    assert evaluate_device_health(_gen2(updates={}), include_firmware=True) == []


# ── BLE/gateway-bridged devices: the reduced set ──────────────────────


def test_a_ble_device_is_judged_on_the_gateway_signal() -> None:
    finding = _one(evaluate_device_health(_gble(rssi=-84)), CHECK_WIFI)
    assert (finding.component, finding.severity) == ("reporter", SEVERITY_WARNING)


def test_a_well_heard_ble_device_produces_nothing() -> None:
    assert evaluate_device_health(_gble()) == []


def test_a_ble_device_is_never_judged_against_gen2_fields() -> None:
    """The payload here would trip the Gen2 checks if the reduced set were
    not enforced: a component error and no ``sys`` block at all. "Unknown"
    must never read as "unhealthy"."""
    status = _gble(
        extra={
            "temperature:0": {"id": 0, "tC": 95.0},
            "devicepower:0": {
                "id": 0,
                "battery": {"V": None, "percent": 3, "low": None},
                "errors": ["broken"],
            },
        }
    )
    assert evaluate_device_health(status, include_firmware=True) == []


def test_a_ble_device_without_a_reporter_block_is_not_judged() -> None:
    status = _gble()
    status.pop("reporter")
    assert evaluate_device_health(status) == []


# ── Gen1: deliberately not judged at all ──────────────────────────────


def test_a_gen1_device_is_skipped_entirely() -> None:
    """No Gen1 device exists in any payload ever recorded from this account,
    so every Gen1 threshold would be a guess dressed up as a measurement —
    the mistake behind issue #32. The payload below would trip three checks
    if the Gen2 rules were applied to it by accident."""
    status = {
        "relays": [{"ison": False}],
        "meters": [{"power": 0.0}],
        "wifi_sta": {"connected": True, "rssi": -91},
        "temperature": 96.0,
        "update": {"has_update": True},
        "ram_free": 100,
        "ram_total": 50000,
    }
    assert evaluate_device_health(status, include_firmware=True) == []


# ── The two gates ─────────────────────────────────────────────────────


def test_neither_gate_alone_is_enough() -> None:
    assert not health_verdict(HEALTH_MIN_STREAK, 0.0, HEALTH_MIN_SECONDS - 1)
    assert not health_verdict(HEALTH_MIN_STREAK - 1, 0.0, HEALTH_MIN_SECONDS + 1)
    assert health_verdict(HEALTH_MIN_STREAK, 0.0, HEALTH_MIN_SECONDS)


def test_a_finding_with_no_start_time_is_never_confirmed() -> None:
    assert not health_verdict(HEALTH_MIN_STREAK, None, 10_000.0)


# ── The aggregated summary ────────────────────────────────────────────


def test_the_summary_counts_findings_not_devices() -> None:
    findings = {
        DEVICE: (
            HealthFinding(CHECK_COMPONENT_ERROR, "temperature:100", "error", "read"),
            HealthFinding(CHECK_COMPONENT_ERROR, "temperature:101", "error", "read"),
            HealthFinding(CHECK_WIFI, "wifi", "warning", "-81 dBm"),
        ),
        OTHER: (HealthFinding(CHECK_WIFI, "wifi", "warning", "-76 dBm"),),
    }
    # Ordered by count, then by check id — so a tie renders deterministically
    # rather than however the last poll happened to iterate.
    assert summarise_findings(findings) == "Component error (2), Wi-Fi signal (2)"


def test_an_empty_summary_is_empty_rather_than_a_stray_separator() -> None:
    assert summarise_findings({}) == ""


# ── Driven through the coordinator ────────────────────────────────────


class _FakeApi:
    def __init__(self, devices_status: dict[str, Any]) -> None:
        self.devices_status = devices_status

    async def get_all_status(self) -> dict[str, Any]:
        return {"devices_status": self.devices_status}


class _StubHass:
    def async_create_task(self, coro: Any) -> None:
        coro.close()


@pytest.fixture(autouse=True)
def _inert_repair_issues(monkeypatch):
    """Silence every repair wrapper except the one under test.

    The stub ``hass`` below has no issue registry, and the poll dispatches
    ``SIGNAL_NEW_DEVICE`` into a Home Assistant that does not exist.
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
    monkeypatch.setattr(
        coordinator_mod, "async_dispatcher_send", lambda *args, **kwargs: None
    )


class _CardRecorder:
    """Captures what the aggregated card was last told."""

    def __init__(self) -> None:
        self.calls: list[tuple[bool, dict, dict]] = []

    def __call__(self, hass, entry, *, active, findings, names) -> None:
        self.calls.append((active, dict(findings), dict(names)))

    @property
    def last_active(self) -> bool:
        return self.calls[-1][0]

    @property
    def last_findings(self) -> dict:
        return self.calls[-1][1]


@pytest.fixture
def card(monkeypatch) -> _CardRecorder:
    recorder = _CardRecorder()
    monkeypatch.setattr(
        coordinator_mod, "async_manage_device_health_issue", recorder
    )
    return recorder


def _coordinator(**options: Any) -> ShellyCloudCoordinator:
    """A coordinator with only the polling wiring in place.

    ``__init__`` wants a HomeAssistant instance and a ConfigEntry; none of
    the health logic does. ``test_coordinator_init.py`` is the guard that
    keeps this shortcut from hiding a missing assignment.
    """
    coord = object.__new__(ShellyCloudCoordinator)
    coord._api = _FakeApi({})
    coord.hass = _StubHass()
    coord._entry = SimpleNamespace(
        entry_id="entry", title="Shelly Cloud DIY",
        options={"enabled_devices": [], "create_all_initially": True, **options},
    )
    coord.devices = {}
    coord._known_device_ids = set()
    coord.device_names = {}
    coord._names_attempted = set()
    coord._name_lookup_in_flight = False
    coord.virtual_configs = {}
    coord._vcomp_config_in_flight = False
    coord._sleep_seen = {}
    coord.checkins = {}
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
    coord._display_names = lambda ids: {
        d: coord.device_names[d] for d in ids if d in coord.device_names
    }
    return coord


def _poll(coord: ShellyCloudCoordinator, statuses: dict[str, Any], at: float) -> None:
    """Run one full poll with ``time.monotonic`` pinned to ``at``.

    Patched rather than slept through: the time gate is five minutes, and a
    real-time test would outlast the rest of the suite several times over.
    """
    coord._api.devices_status = statuses
    original = time.monotonic
    time.monotonic = lambda: at  # type: ignore[assignment]
    try:
        asyncio.run(coord._async_update_data())
    finally:
        time.monotonic = original  # type: ignore[assignment]


def _drive(
    coord: ShellyCloudCoordinator,
    statuses: dict[str, Any],
    *,
    start: float = 0.0,
    polls: int = 8,
    step: float = 60.0,
) -> None:
    """Poll ``polls`` times, each with a fresh fingerprint so every device
    counts as having genuinely checked in."""
    for i in range(polls):
        fresh = {
            did: {**status, "serial": int(start) + i, "_updated": f"t{start}-{i}"}
            for did, status in statuses.items()
        }
        _poll(coord, fresh, start + i * step)


def test_a_healthy_fleet_never_raises_the_card(card) -> None:
    coord = _coordinator()
    _drive(coord, {DEVICE: _gen2(), BLU: _gble()})
    assert coord.device_health == {}
    assert card.last_active is False


def test_a_sustained_finding_is_reported(card) -> None:
    coord = _coordinator()
    _drive(coord, {DEVICE: _gen2(rssi=-82)})
    assert list(coord.device_health) == [DEVICE]
    assert _checks(list(coord.device_health[DEVICE])) == {CHECK_WIFI}
    assert card.last_active is True


def test_nothing_is_said_before_both_gates_are_served(card) -> None:
    """Three check-ins inside a minute serve the streak but not the clock:
    a device is allowed to be briefly warm after a firmware flash without
    the user hearing about it."""
    coord = _coordinator()
    _drive(coord, {DEVICE: _gen2(rssi=-82)}, polls=HEALTH_MIN_STREAK + 1, step=10.0)
    assert coord.device_health == {}
    assert card.last_active is False


def test_the_clock_alone_is_not_enough_either(card) -> None:
    """Two check-ins ten minutes apart clear the time gate and still must
    not produce a verdict — one report either side of a long gap is not a
    sustained condition."""
    coord = _coordinator()
    _drive(coord, {DEVICE: _gen2(rssi=-82)}, polls=HEALTH_MIN_STREAK - 1, step=600.0)
    assert coord.device_health == {}


def test_a_finding_clears_on_the_first_contradicting_checkin(card) -> None:
    """Deliberately NOT delayed the way the relay detector's clear is. A
    device now reporting 60 °C is not hiding a hot chip, so holding the
    finding open would just be showing something that stopped being true."""
    coord = _coordinator()
    _drive(coord, {DEVICE: _gen2(tc=78.5)})
    assert coord.device_health[DEVICE]

    _drive(coord, {DEVICE: _gen2(tc=60.0)}, start=1000.0, polls=1)
    assert coord.device_health == {}
    assert card.last_active is False


def test_a_flapping_value_has_to_serve_both_gates_again(card) -> None:
    """One clean check-in wipes the history, so a value that crosses the
    line every other poll can never accumulate a streak."""
    coord = _coordinator()
    for i in range(20):
        rssi = -82 if i % 2 else -50
        _drive(
            coord, {DEVICE: _gen2(rssi=rssi)},
            start=float(i * 100), polls=1,
        )
    assert coord.device_health == {}


def test_a_frozen_device_cannot_earn_a_finding(card) -> None:
    """A device that stops reporting keeps re-serving its last payload. If
    polls counted instead of check-ins, one stale snapshot repeated for
    three hours would manufacture a verdict against gates measured in
    minutes."""
    coord = _coordinator()
    hot = _gen2(tc=90.0)
    for i in range(200):
        _poll(coord, {DEVICE: {**hot, "serial": 1, "_updated": "frozen"}}, i * 60.0)
    assert coord.device_health == {}


def test_a_standing_finding_survives_the_device_going_quiet(card) -> None:
    """A device that drops off the cloud at 90 °C is not cooler for having
    stopped talking about it."""
    coord = _coordinator()
    _drive(coord, {DEVICE: _gen2(tc=90.0)})
    assert coord.device_health[DEVICE]

    for i in range(50):
        _poll(
            coord,
            {DEVICE: {**_gen2(tc=90.0), "serial": 9, "_updated": "frozen"}},
            10_000.0 + i * 60.0,
        )
    assert list(coord.device_health) == [DEVICE]


def test_two_unhealthy_devices_share_one_card(card) -> None:
    """The measured account would have produced 24 firmware cards alone. One
    card per device is what makes a repair panel unreadable, so the manager
    is called once with every device on it."""
    coord = _coordinator()
    _drive(
        coord,
        {
            DEVICE: _gen2(rssi=-82),
            OTHER: _gen2(tc=90.0),
            BLU: _gble(),
        },
    )
    assert set(coord.device_health) == {DEVICE, OTHER}
    assert card.last_active is True
    assert set(card.last_findings) == {DEVICE, OTHER}


def test_several_findings_on_one_device_are_all_carried(card) -> None:
    coord = _coordinator()
    _drive(
        coord,
        {DEVICE: _gen2(rssi=-86, tc=90.0, ram_free=100, restart=True)},
    )
    assert _checks(list(coord.device_health[DEVICE])) == {
        CHECK_WIFI, CHECK_TEMPERATURE, CHECK_RAM, CHECK_RESTART
    }


def test_firmware_findings_stay_out_unless_the_option_is_set(card) -> None:
    coord = _coordinator()
    _drive(coord, {DEVICE: _gen2(updates=UPDATES)})
    assert coord.device_health == {}
    assert card.last_active is False


def test_firmware_findings_appear_once_the_option_is_set(card) -> None:
    coord = _coordinator(**{CONF_DEVICE_HEALTH_FIRMWARE: True})
    _drive(coord, {DEVICE: _gen2(updates=UPDATES)})
    assert _checks(list(coord.device_health[DEVICE])) == {CHECK_FIRMWARE}


def test_the_option_switches_the_check_off_and_drops_its_verdicts(card) -> None:
    coord = _coordinator(**{CONF_DEVICE_HEALTH_DETECTION: False})
    _drive(coord, {DEVICE: _gen2(rssi=-86, tc=90.0)})
    assert coord.device_health == {}
    assert card.last_active is False


def test_switching_the_check_off_clears_a_standing_card(card) -> None:
    """The user unticking the option must take the card with it, not leave
    a verdict nothing will ever re-evaluate."""
    coord = _coordinator()
    _drive(coord, {DEVICE: _gen2(rssi=-86)})
    assert coord.device_health[DEVICE]

    coord._entry.options = {
        **coord._entry.options, CONF_DEVICE_HEALTH_DETECTION: False,
    }
    _drive(coord, {DEVICE: _gen2(rssi=-86)}, start=5000.0, polls=1)
    assert coord.device_health == {}
    assert coord._health_streak == {}
    assert card.last_active is False


def test_a_device_the_user_unticks_stops_being_reported(card) -> None:
    """The finding is about hardware Home Assistant no longer looks at."""
    coord = _coordinator()
    _drive(coord, {DEVICE: _gen2(rssi=-86)})
    assert coord.device_health[DEVICE]

    coord._entry.options = {
        **coord._entry.options,
        "create_all_initially": False,
        "enabled_devices": [OTHER],
    }
    _drive(coord, {DEVICE: _gen2(rssi=-86)}, start=5000.0, polls=1)
    assert coord.device_health == {}
    assert card.last_active is False


# ── The card is cleaned up with the entry ─────────────────────────────


def test_removing_the_entry_deletes_the_health_card(monkeypatch) -> None:
    """A kind missing from ``async_clear_entry_issues`` outlives the entry
    that raised it, and nothing ever comes back to remove it."""
    from custom_components.shelly_cloud_diy import repair_issues

    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr(
        repair_issues.ir, "async_delete_issue",
        lambda hass, domain, iid: deleted.append((domain, iid)),
    )
    repair_issues.async_clear_entry_issues(
        None, SimpleNamespace(entry_id="entry", title="Shelly Cloud DIY")
    )
    assert (DOMAIN, issue_id(ISSUE_DEVICE_HEALTH, "entry")) in deleted
