"""Unit tests for the repair-issue verdicts (rate limit, missing devices, history import).

Shelly Cloud signals its 1 request/second rate limit as HTTP **401 with
``max_req`` in the body**, not HTTP 429 — the same status code a rejected
auth_key produces. Making that distinction legible in the UI is the whole
point of the ``rate_limited`` issue (see issue #6), so nothing in these
tests or the strings they guard may key off 429.

These tests drive only the pure helpers, never the ``async_manage_*``
registry wrappers (those assert a running event loop), so they run
identically against any HA version.
"""
from __future__ import annotations

import asyncio
import json
import os
import re

import pytest

from homeassistant.config_entries import ConfigEntryState

from custom_components.shelly_cloud_diy import coordinator as coordinator_mod
from custom_components.shelly_cloud_diy.services import (
    historical as historical_mod,
)
from custom_components.shelly_cloud_diy.services.historical import (
    HistoricalDataService,
)
from custom_components.shelly_cloud_diy.coordinator import (
    ShellyCloudCoordinator,
)
from custom_components.shelly_cloud_diy.repair_issues import (
    HISTORY_IMPORT_MIN_FAILURES,
    HISTORY_IMPORT_RETRY_S,
    MISSING_DEVICE_MAX_LISTED,
    MISSING_DEVICE_MAX_NAME_LEN,
    MISSING_DEVICE_MIN_SECONDS,
    MISSING_DEVICE_MIN_STREAK,
    RATE_LIMIT_MIN_SECONDS,
    RATE_LIMIT_MIN_STREAK,
    compute_missing_devices,
    format_device_list,
    history_import_verdict,
    is_mass_absence,
    issue_id,
    missing_devices_verdict,
    rate_limit_verdict,
)

_COMPONENT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "custom_components",
    "shelly_cloud_diy",
)

_EXPECTED_ISSUE_KEYS = {
    "rate_limited",
    "missing_devices",
    "history_import_failed",
    "relay_fault",
}

# Everything the async_manage_* wrappers actually supply as placeholders.
_SUPPLIED_PLACEHOLDERS = {"entry_title", "count", "device_ids", "devices"}


# ── Part A: issue id scoping ──────────────────────────────────────────


def test_issue_id_shape() -> None:
    """The id is the kind suffixed with the config entry id."""
    assert issue_id("rate_limited", "abc") == "rate_limited_abc"


def test_issue_id_is_entry_scoped() -> None:
    """Two accounts must not clobber each other's card.

    The issue registry keys on ``(domain, issue_id)`` alone, so an
    unsuffixed id would let a second config entry overwrite the first.
    """
    assert issue_id("rate_limited", "entry_one") != issue_id(
        "rate_limited", "entry_two"
    )


# ── Part B: rate_limit_verdict ────────────────────────────────────────


@pytest.mark.parametrize(
    ("streak", "elapsed", "expected"),
    [
        (4, 300.0, False),      # count gate
        (5, 119.0, False),      # duration gate
        (5, 120.0, True),       # boundary inclusive
        (50, 600.0, True),
    ],
)
def test_rate_limit_verdict(streak: int, elapsed: float, expected: bool) -> None:
    """Both gates must hold before a sustained rate limit is reported."""
    now = 10_000.0
    assert rate_limit_verdict(streak, now - elapsed, now) is expected


def test_rate_limit_verdict_no_streak_start() -> None:
    """A cold coordinator must not raise, and must not report."""
    assert rate_limit_verdict(0, None, 1234.0) is False


def test_rate_limit_gates_match_constants() -> None:
    """The documented policy is what the constants actually encode."""
    assert RATE_LIMIT_MIN_STREAK == 5
    assert RATE_LIMIT_MIN_SECONDS == 120.0


# ── Part C: compute_missing_devices ───────────────────────────────────


def test_compute_missing_create_all_is_vacuous() -> None:
    """With create_all on, the enabled set IS the seen set.

    So the difference is vacuous BY DECISION, made explicit here rather
    than emerging by accident from whatever the caller passed as
    ``enabled``. The related ``enabled_ids`` fallback trap lives in the
    coordinator, not in this function — it is guarded separately by
    ``test_explicit_enabled_ids_ignores_the_all_devices_fallback``.
    """
    assert compute_missing_devices({"a", "b"}, {"a"}, True) == set()


def test_compute_missing_basic() -> None:
    assert compute_missing_devices({"a", "b", "c"}, {"a", "b"}, False) == {"c"}


def test_compute_missing_nothing_enabled() -> None:
    assert compute_missing_devices(set(), {"a", "b"}, False) == set()


def test_compute_missing_all_present() -> None:
    assert compute_missing_devices({"a"}, {"a", "b"}, False) == set()


# ── Part D: is_mass_absence / missing_devices_verdict ─────────────────


def _ids(count: int, prefix: str = "dev") -> set[str]:
    return {f"{prefix}{i:03d}" for i in range(count)}


def test_mass_absence_small_fraction() -> None:
    enabled = _ids(20)
    missing = set(sorted(enabled)[:2])
    assert is_mass_absence(missing, enabled) is False


def test_mass_absence_above_floor() -> None:
    enabled = _ids(20)
    missing = set(sorted(enabled)[:6])
    assert is_mass_absence(missing, enabled) is True


def test_mass_absence_total_wipeout() -> None:
    """Everything selected gone at once is an account/server condition."""
    enabled = _ids(4)
    assert is_mass_absence(set(enabled), enabled) is True


def test_mass_absence_single_of_four() -> None:
    enabled = _ids(4)
    missing = set(sorted(enabled)[:1])
    assert is_mass_absence(missing, enabled) is False


def test_mass_absence_empty_enabled() -> None:
    assert is_mass_absence(set(), set()) is False


def test_missing_verdict_nothing_missing() -> None:
    assert (
        missing_devices_verdict(9_999, 0.0, 999_999.0, set(), _ids(20)) is False
    )


@pytest.mark.parametrize(
    ("streak", "elapsed", "expected"),
    [
        (9, 90_000.0, False),       # count gate
        (10, 86_399.0, False),      # duration gate
        (10, 86_400.0, True),       # boundary inclusive
    ],
)
def test_missing_verdict_gates(
    streak: int, elapsed: float, expected: bool
) -> None:
    enabled = _ids(20)
    now = 1_000_000.0
    assert (
        missing_devices_verdict(
            streak, now - elapsed, now, {"dev000"}, enabled
        )
        is expected
    )


def test_missing_verdict_mass_absence_outranks_gates() -> None:
    """A fleet-wide disappearance is suppressed however long it lasts."""
    enabled = _ids(20)
    missing = set(sorted(enabled)[:6])
    now = 1_000_000.0
    assert (
        missing_devices_verdict(100, now - 200_000.0, now, missing, enabled)
        is False
    )


def test_missing_gates_match_constants() -> None:
    assert MISSING_DEVICE_MIN_STREAK == 10
    assert MISSING_DEVICE_MIN_SECONDS == 86400.0


# ── Part E: history_import_verdict ────────────────────────────────────


@pytest.mark.parametrize(
    ("failures", "url", "expected"),
    [
        (99, "", False),                          # never configured a gateway
        (0, "http://10.0.0.5:8080", False),        # fresh service never fires
        (1, "http://10.0.0.5:8080", False),
        (2, "http://10.0.0.5:8080", True),
        (HISTORY_IMPORT_MIN_FAILURES, "http://x", True),
    ],
)
def test_history_import_verdict(
    failures: int, url: str, expected: bool
) -> None:
    assert history_import_verdict(failures, url) is expected


# ── Part F: format_device_list ────────────────────────────────────────


def test_format_bare_id() -> None:
    assert format_device_list({"abc123"}, {}) == "abc123"


def test_format_named_id() -> None:
    assert (
        format_device_list({"abc123"}, {"abc123": "Kitchen lamp"})
        == "Kitchen lamp (abc123)"
    )


def test_format_truncates_and_counts() -> None:
    """The card must never render an unbounded id blob."""
    missing = _ids(8)
    text = format_device_list(missing, {})
    assert text.count(",") == MISSING_DEVICE_MAX_LISTED  # 4 separators + tail
    assert text.endswith(", … (+3)")
    for did in sorted(missing)[:MISSING_DEVICE_MAX_LISTED]:
        assert did in text
    assert len(text) < 200


def test_format_is_bounded_even_with_long_names() -> None:
    """Names are user-supplied, so the bound must survive verbose ones.

    Five entries of "Wohnzimmer Deckenlampe hinten links (3494546e1f2a)"
    would blow past the limit MISSING_DEVICE_MAX_LISTED exists to enforce,
    and names are usually resolved BEFORE a device vanishes, so this is the
    case that actually occurs in production.
    """
    missing = _ids(8)
    names = {
        did: "Wohnzimmer Deckenlampe hinten links am Fenster"
        for did in missing
    }
    text = format_device_list(missing, names)
    assert len(text) < 200
    assert text.endswith(", … (+3)")
    # Every listed id is still identifiable despite the clamp.
    for did in sorted(missing)[:MISSING_DEVICE_MAX_LISTED]:
        assert f"({did})" in text


def test_format_clamps_a_single_long_name() -> None:
    text = format_device_list({"abc123"}, {"abc123": "x" * 100})
    assert text.endswith("(abc123)")
    assert len(text) < 100


def test_format_clamps_on_a_word_boundary() -> None:
    """A cut name must still read like a name.

    The live relay-fault card rendered Dirk's switch as
    "KG-SY-PSW-Licht-Buero-Di…", which reads as a rendering fault rather
    than a shortened label.
    """
    text = format_device_list(
        {"abc123"}, {"abc123": "KG-SY-PSW-Licht-Buero-Dirk"}
    )
    assert text == "KG-SY-PSW-Licht-Buero… (abc123)"


def test_format_clamp_falls_back_to_a_hard_cut() -> None:
    """No separator late enough in the budget means the hard cut stands."""
    text = format_device_list({"abc123"}, {"abc123": "Wohnzimmer" + "x" * 40})
    assert text.startswith("Wohnzimmerxxx")
    assert text.endswith("… (abc123)")


def test_format_clamp_never_exceeds_the_bound() -> None:
    """Whichever branch runs, the clamp still owes the caller its bound."""
    for name in (
        "KG-SY-PSW-Licht-Buero-Dirk",
        "Wohnzimmer Deckenlampe hinten links",
        "a" * 100,
        "-" * 30,
        "kurz-" + "y" * 30,
    ):
        text = format_device_list({"abc123"}, {"abc123": name})
        rendered = text.split(" (abc123)")[0]
        assert len(rendered) <= MISSING_DEVICE_MAX_NAME_LEN, name


def test_format_is_deterministic() -> None:
    missing = _ids(8)
    assert format_device_list(missing, {}) == format_device_list(missing, {})


# ── Part G: streak bookkeeping against the REAL coordinator ───────────
#
# These drive the ACTUAL ShellyCloudCoordinator methods, not a
# re-implementation of them. A hand-written mirror can only ever confirm
# that the mirror-writer understood their own code; it cannot catch a
# regression in the method it copies. The coordinator is built with
# ``__new__`` so no HA instance, config entry or DataUpdateCoordinator
# machinery is needed — only the handful of attributes these three methods
# actually touch. Everything that reaches out to HA (the issue-registry
# wrappers, the device registry, the clock) is swapped for a fake.


class _FakeIssueSink:
    """Model issue EXISTENCE, mirroring async_get_or_create semantics.

    ``async_create_issue`` is an upsert that only fires an event when a
    field actually changed, so re-asserting an unchanged issue must record
    nothing. ``async_delete_issue`` on an unknown id is a no-op.
    """

    def __init__(self) -> None:
        self.raised: dict[str, dict[str, str]] = {}
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def manage(
        self, iid: str, active: bool, placeholders: dict[str, str] | None = None
    ) -> None:
        placeholders = placeholders or {}
        if active:
            if self.raised.get(iid) == placeholders:
                return          # unchanged upsert — no event
            self.raised[iid] = placeholders
            self.calls.append(("create", iid, placeholders))
        elif iid in self.raised:
            del self.raised[iid]
            self.calls.append(("delete", iid, {}))


class _FakeClock:
    """Stand-in for the ``time`` module the coordinator calls into."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class _FakeDeviceRegistry:
    """A device registry that knows nothing, so labels fall back to ids."""

    def async_get_device(self, identifiers=None):  # noqa: ANN001
        return None


class _RealCoordinatorDriver:
    """Drive the real repair methods with every HA edge faked out."""

    def __init__(self, monkeypatch, enabled: set[str] | None = None) -> None:
        self.clock = _FakeClock()
        self.sink = _FakeIssueSink()
        self.enabled = enabled or set()

        coord = ShellyCloudCoordinator.__new__(ShellyCloudCoordinator)
        coord.hass = None
        coord._entry = None
        coord.device_names = {}
        coord._rate_limit_streak = 0
        coord._rate_limit_since = None
        coord._rate_limit_reported = False
        coord._missing_streak = {}
        coord._missing_since = {}
        coord._relay_fault_streak = {}
        coord._relay_fault_since = {}
        coord._relay_healthy_since = {}
        coord.relay_faults = set()
        self.coord = coord

        sink = self.sink
        monkeypatch.setattr(coordinator_mod, "time", self.clock)
        monkeypatch.setattr(
            coordinator_mod, "dr", type("_dr", (), {
                "async_get": staticmethod(lambda _hass: _FakeDeviceRegistry()),
            }),
        )
        monkeypatch.setattr(
            coordinator_mod,
            "async_manage_rate_limit_issue",
            lambda hass, entry, *, active: sink.manage("rate_limited", active),
        )
        monkeypatch.setattr(
            coordinator_mod,
            "async_manage_missing_devices_issue",
            lambda hass, entry, *, active, missing, names: sink.manage(
                "missing_devices",
                active,
                {
                    "count": str(len(missing)),
                    "device_ids": format_device_list(missing, names),
                },
            ),
        )
        # ``_explicit_enabled_ids`` reads entry.options through the
        # ``_options`` property; feed it the selection under test.
        monkeypatch.setattr(
            ShellyCloudCoordinator,
            "_options",
            property(lambda _self: {"enabled_devices": sorted(self.enabled)}),
        )
        monkeypatch.setattr(
            ShellyCloudCoordinator,
            "create_all_initially",
            property(lambda _self: False),
        )

    def advance(self, seconds: float) -> None:
        self.clock.now += seconds

    def note_rate_limited(self) -> None:
        self.coord._note_rate_limited()

    def note_not_rate_limited(self) -> None:
        self.coord._note_poll_not_rate_limited()

    def evaluate_missing(self, seen: set[str]) -> None:
        self.coord._evaluate_missing_devices(set(seen))


@pytest.fixture(name="coord")
def _coord(monkeypatch) -> _RealCoordinatorDriver:
    return _RealCoordinatorDriver(monkeypatch)


def test_explicit_enabled_ids_ignores_the_all_devices_fallback(
    monkeypatch,
) -> None:
    """The whole check dies silently if this is swapped for ``enabled_ids``.

    ``enabled_ids`` falls back to ``set(self.devices)`` in two branches, so
    the difference against the seen set would be vacuous and R2 would never
    fire again — with every test still green.
    """
    driver = _RealCoordinatorDriver(monkeypatch)
    coord = driver.coord

    monkeypatch.setattr(
        ShellyCloudCoordinator, "_options",
        property(lambda _s: {"enabled_devices": ["a", "b"]}),
    )
    assert coord._explicit_enabled_ids() == {"a", "b"}

    monkeypatch.setattr(
        ShellyCloudCoordinator, "_options", property(lambda _s: {}),
    )
    assert coord._explicit_enabled_ids() == set()

    monkeypatch.setattr(
        ShellyCloudCoordinator, "_options",
        property(lambda _s: {"enabled_devices": "not-a-list"}),
    )
    assert coord._explicit_enabled_ids() == set()

    monkeypatch.setattr(
        ShellyCloudCoordinator, "_options",
        property(lambda _s: {"enabled_devices": ["a", 7, None, "b"]}),
    )
    assert coord._explicit_enabled_ids() == {"a", "b"}


def test_self_healing_rate_limit_never_flaps(coord) -> None:
    """The ~1.5 s self-healing case must produce zero issue churn."""
    for _ in range(50):
        coord.note_rate_limited()
        coord.advance(1.5)
        coord.note_not_rate_limited()
        coord.advance(3.5)
    assert coord.sink.calls == []


def test_sustained_rate_limit_raises_once_then_clears(coord) -> None:
    for _ in range(5):
        coord.note_rate_limited()
        coord.advance(50.0)
    assert [c[0] for c in coord.sink.calls] == ["create"]

    for _ in range(5):
        coord.note_rate_limited()
        coord.advance(50.0)
    assert [c[0] for c in coord.sink.calls] == ["create"]

    coord.note_not_rate_limited()
    assert [c[0] for c in coord.sink.calls] == ["create", "delete"]


def test_non_rate_limit_failure_resets_streak(coord) -> None:
    """A different failure type falsifies the pure-rate-limit claim."""
    for _ in range(4):
        coord.note_rate_limited()
        coord.advance(50.0)
    coord.note_not_rate_limited()      # e.g. a transport error
    for _ in range(4):
        coord.note_rate_limited()
        coord.advance(50.0)
    assert coord.sink.calls == []


def test_missing_set_change_replaces_in_place(monkeypatch) -> None:
    """Replacing placeholders preserves the user's "Ignore".

    A delete-then-create would discard it, so the second event must be
    another ``create`` and there must be no ``delete`` in between.
    """
    enabled = _ids(20)
    coord = _RealCoordinatorDriver(monkeypatch, enabled)
    seen = set(enabled) - {"dev000"}
    for _ in range(MISSING_DEVICE_MIN_STREAK):
        coord.evaluate_missing(seen)
        coord.advance(MISSING_DEVICE_MIN_SECONDS / 2)
    assert [c[0] for c in coord.sink.calls] == ["create"]
    assert coord.sink.calls[-1][2]["device_ids"] == "dev000"
    # The count must describe the ids actually named, never a wider set.
    assert coord.sink.calls[-1][2]["count"] == "1"

    # A second device vanishes: same issue, new placeholders.
    seen2 = seen - {"dev001"}
    for _ in range(MISSING_DEVICE_MIN_STREAK):
        coord.evaluate_missing(seen2)
        coord.advance(MISSING_DEVICE_MIN_SECONDS / 2)
    assert [c[0] for c in coord.sink.calls] == ["create", "create"]
    assert coord.sink.calls[-1][2]["device_ids"] == "dev000, dev001"
    assert coord.sink.calls[-1][2]["count"] == "2"


def test_newly_vanished_device_serves_its_own_clock(monkeypatch) -> None:
    """A new absence does not inherit elapsed time from another device."""
    enabled = _ids(20)
    coord = _RealCoordinatorDriver(monkeypatch, enabled)
    seen = set(enabled) - {"dev000"}
    for _ in range(MISSING_DEVICE_MIN_STREAK):
        coord.evaluate_missing(seen)
        coord.advance(MISSING_DEVICE_MIN_SECONDS / 2)
    assert len(coord.sink.calls) == 1

    # dev000 returns and dev001 vanishes in the same poll. The card must
    # clear (its only id is back) and dev001 must NOT inherit the 24 h
    # dev000 had already served.
    seen2 = set(enabled) - {"dev001"}
    for _ in range(MISSING_DEVICE_MIN_STREAK):
        coord.evaluate_missing(seen2)
        coord.advance(1.0)
    assert [c[0] for c in coord.sink.calls] == ["create", "delete"]


def test_second_device_vanishing_does_not_hide_the_card(monkeypatch) -> None:
    """A WORSENING condition must never make the warning disappear.

    Per-set streak bookkeeping would restart the clock here and clear a
    card that had already earned its place for 24 h.
    """
    enabled = _ids(20)
    coord = _RealCoordinatorDriver(monkeypatch, enabled)
    seen = set(enabled) - {"dev000"}
    for _ in range(MISSING_DEVICE_MIN_STREAK):
        coord.evaluate_missing(seen)
        coord.advance(MISSING_DEVICE_MIN_SECONDS / 2)
    assert [c[0] for c in coord.sink.calls] == ["create"]

    # dev001 also vanishes; it has not served its own 24 h yet.
    seen2 = seen - {"dev001"}
    for _ in range(3):
        coord.evaluate_missing(seen2)
        coord.advance(60.0)
    # dev000's card stands, still naming only dev000, with no churn.
    assert [c[0] for c in coord.sink.calls] == ["create"]
    assert coord.sink.raised["missing_devices"]["device_ids"] == "dev000"


def test_transient_mass_outage_cannot_retract_an_earned_card(
    monkeypatch,
) -> None:
    """The mass-absence guard must never delete an already-earned card.

    Deleting discards the user's "Ignore" and the card returns un-ignored
    once the outage passes — the exact delete-then-create loop this design
    exists to prevent. The guard is therefore judged on the REPORTABLE set,
    not on the whole missing set: ids that have not served their own 24 h
    cannot vote a card off the board.
    """
    enabled = _ids(20)
    coord = _RealCoordinatorDriver(monkeypatch, enabled)
    seen = set(enabled) - {"dev000"}
    for _ in range(MISSING_DEVICE_MIN_STREAK):
        coord.evaluate_missing(seen)
        coord.advance(MISSING_DEVICE_MIN_SECONDS / 2)
    assert [c[0] for c in coord.sink.calls] == ["create"]

    # A router reboot takes six BLU children offline for a few polls: well
    # over the mass-absence floor, but none of them has served 24 h.
    outage = set(enabled) - {f"dev{i:03d}" for i in range(6)}
    for _ in range(3):
        coord.evaluate_missing(outage)
        coord.advance(60.0)
    assert [c[0] for c in coord.sink.calls] == ["create"]
    assert coord.sink.raised["missing_devices"]["device_ids"] == "dev000"

    # Outage over: still no churn, still the same single id.
    coord.evaluate_missing(seen)
    assert [c[0] for c in coord.sink.calls] == ["create"]


def test_account_wide_disappearance_is_suppressed(monkeypatch) -> None:
    """A genuine fleet-wide wipe-out still reaches the mass-absence guard.

    Every id vanishes at the same moment, so they clear their individual
    24 h gates together and land in ``reportable`` together — where the
    guard catches them and stays silent.
    """
    enabled = _ids(20)
    coord = _RealCoordinatorDriver(monkeypatch, enabled)
    for _ in range(MISSING_DEVICE_MIN_STREAK * 2):
        coord.evaluate_missing(set())
        coord.advance(MISSING_DEVICE_MIN_SECONDS / 2)
    assert coord.sink.calls == []


def test_device_reappearing_clears_the_issue(monkeypatch) -> None:
    enabled = _ids(20)
    coord = _RealCoordinatorDriver(monkeypatch, enabled)
    seen = set(enabled) - {"dev000"}
    for _ in range(MISSING_DEVICE_MIN_STREAK):
        coord.evaluate_missing(seen)
        coord.advance(MISSING_DEVICE_MIN_SECONDS / 2)
    coord.evaluate_missing(set(enabled))
    assert [c[0] for c in coord.sink.calls] == ["create", "delete"]


# ── Part J: historical-import accounting (the real state machine) ─────
#
# ``history_import_verdict`` is a pure function tested in Part E, but the
# state machine that FEEDS it — the reset arm, the increment, the one-shot
# retry, the unloaded-entry bail-out — is what actually decides whether a
# user sees a permanent unclearable card. Driven through the real method.


class _FakeEntry:
    def __init__(self, url: str) -> None:
        self.options = {"local_gateway_url": url}
        self.state = ConfigEntryState.LOADED
        self.entry_id = "entry1"
        self.title = "Shelly Cloud DIY"


class _HistoryDriver:
    """Drive the real ``_run_auto_sync`` accounting with HA faked out."""

    def __init__(
        self,
        monkeypatch,
        url: str = "http://10.0.0.5:8080",
        importable: bool = True,
    ) -> None:
        self.sink = _FakeIssueSink()
        self.scheduled: list[float] = []
        self.imported: list[str] = []
        self.entry = _FakeEntry(url)

        svc = HistoricalDataService.__new__(HistoricalDataService)
        svc._hass = None
        svc._entry = self.entry
        svc._sync_failures = 0
        svc._cancel_retry = None
        svc._retry_task = None
        svc._startup_task = None
        self.svc = svc

        svc._has_importable_em_devices = lambda: importable
        svc.sync_data = self._sync_data

        sink = self.sink
        monkeypatch.setattr(
            historical_mod,
            "async_manage_history_import_issue",
            lambda hass, entry, *, active: sink.manage(
                "history_import_failed", active
            ),
        )
        monkeypatch.setattr(
            historical_mod,
            "async_call_later",
            lambda hass, delay, action: (
                self.scheduled.append(delay) or (lambda: None)
            ),
        )

    async def _sync_data(self, _url):
        return list(self.imported)

    def run(self) -> None:
        asyncio.run(self.svc._run_auto_sync())


def test_unproductive_sync_raises_only_on_the_second_run(monkeypatch) -> None:
    d = _HistoryDriver(monkeypatch)
    d.run()
    assert d.svc._sync_failures == 1
    assert d.sink.calls == []
    # A 15-minute retry is armed so the second data point lands in the same
    # session instead of a day later.
    assert d.scheduled == [HISTORY_IMPORT_RETRY_S]

    d.run()
    assert [c[0] for c in d.sink.calls] == ["create"]


def test_productive_sync_clears_the_issue(monkeypatch) -> None:
    d = _HistoryDriver(monkeypatch)
    d.run()
    d.run()
    assert [c[0] for c in d.sink.calls] == ["create"]

    d.imported = ["sensor.shelly_em_energy"]
    d.run()
    assert [c[0] for c in d.sink.calls] == ["create", "delete"]
    assert d.svc._sync_failures == 0


def test_clearing_the_gateway_url_clears_the_issue(monkeypatch) -> None:
    """Switching the import off must not leave an unclearable card."""
    d = _HistoryDriver(monkeypatch)
    d.run()
    d.run()
    assert [c[0] for c in d.sink.calls] == ["create"]

    d.entry.options["local_gateway_url"] = ""
    d.run()
    assert [c[0] for c in d.sink.calls] == ["create", "delete"]


def test_nothing_importable_is_not_a_gateway_failure(monkeypatch) -> None:
    """An EM device that can never produce a statistic is not a failure.

    ``_find_em_devices`` scans the WHOLE cloud fleet, including devices the
    user opted out of, which the coordinator still polls but never
    materialises as entities. Counting those as failures would raise a
    permanent card telling the user to fix a gateway URL that is correct.
    """
    d = _HistoryDriver(monkeypatch, importable=False)
    for _ in range(5):
        d.run()
    assert d.svc._sync_failures == 0
    assert d.sink.calls == []


def test_unloaded_entry_neither_counts_nor_arms_a_timer(monkeypatch) -> None:
    """A sync outliving teardown must not schedule work on a dead entry.

    Arming the 15-minute retry against an unloaded entry would run a full
    gateway fetch and a statistics import a quarter of an hour after the
    entry was gone.
    """
    d = _HistoryDriver(monkeypatch)
    d.entry.state = ConfigEntryState.NOT_LOADED
    d.run()
    assert d.svc._sync_failures == 0
    assert d.scheduled == []
    assert d.sink.calls == []


# ── Part I: translation completeness (blocking-defect guard) ──────────


def _load(rel: str) -> dict:
    with open(os.path.join(_COMPONENT_DIR, rel), encoding="utf-8") as fh:
        return json.load(fh)


def _all_files() -> dict[str, dict]:
    return {
        "strings.json": _load("strings.json"),
        "en.json": _load(os.path.join("translations", "en.json")),
        "de.json": _load(os.path.join("translations", "de.json")),
    }


def test_every_file_has_the_same_issue_keys() -> None:
    for name, data in _all_files().items():
        assert "issues" in data, f"{name} has no issues block"
        assert set(data["issues"]) == _EXPECTED_ISSUE_KEYS, name


def test_every_issue_has_title_and_description_only() -> None:
    """No fix_flow: every issue is informational (is_fixable=False)."""
    for name, data in _all_files().items():
        for key, issue in data["issues"].items():
            assert set(issue) == {"title", "description"}, f"{name}:{key}"


def test_placeholders_match_between_languages_and_code() -> None:
    files = _all_files()
    for key in _EXPECTED_ISSUE_KEYS:
        tokens = {}
        for lang in ("en.json", "de.json"):
            issue = files[lang]["issues"][key]
            tokens[lang] = set(
                re.findall(r"\{(\w+)\}", issue["title"] + issue["description"])
            )
        assert tokens["en.json"] == tokens["de.json"], key
        assert tokens["en.json"] <= _SUPPLIED_PLACEHOLDERS, key


def test_no_out_of_scope_terms() -> None:
    """The OAuth/WebSocket realtime path is not shipped; 429 is wrong."""
    pattern = re.compile(r"(websocket|oauth|realtime|429)", re.IGNORECASE)
    for name, data in _all_files().items():
        for key, issue in data["issues"].items():
            for field, value in issue.items():
                assert not pattern.search(value), f"{name}:{key}:{field}"


def test_strings_and_en_are_identical() -> None:
    files = _all_files()
    assert files["strings.json"]["issues"] == files["en.json"]["issues"]


def test_german_glossary_discipline() -> None:
    """"abhaken" means the opposite of what we mean; "Konto" not "Account"."""
    de = _load(os.path.join("translations", "de.json"))["issues"]
    for key, issue in de.items():
        for field, value in issue.items():
            assert not re.search(r"abhak", value, re.IGNORECASE), (
                f"{key}:{field}"
            )
            assert "Account" not in value, f"{key}:{field}"


def test_german_is_actually_translated() -> None:
    """Every DE field must differ from its EN counterpart.

    A verbatim copy-paste of the English block into de.json passes every
    other Part I assertion — identical keys, identical placeholders, no
    "abhaken", no "Account" — so this is the one i18n regression the guard
    could not otherwise see. Incomplete i18n is a blocking defect here.
    """
    files = _all_files()
    en = files["en.json"]["issues"]
    de = files["de.json"]["issues"]
    for key in _EXPECTED_ISSUE_KEYS:
        for field in ("title", "description"):
            assert de[key][field] != en[key][field], f"{key}:{field} untranslated"
