"""Unit tests for the welded-contact detector ("Relay fault" sensor).

The contract these pin comes from a run against genuinely failing hardware
(Shelly 1PM Mini Gen3, 2026-08-17): the device reported ``output: false``
while its own meter reported 85.2 W, continuously, and the load could not be
switched off in software at all. Two observations from that run drive most of
the tests below:

* one turn_off produced ``apower = 0`` for ~45 s while the load was
  demonstrably still running — so a single agreeing sample proves nothing and
  must not retract a standing verdict;
* the energy counter stood still for 80 s and then jumped by 1.0 Wh — so it
  resolves minutes, not seconds, and is deliberately not part of the verdict.

Driven through the real production code with the same lightweight fake
coordinator the other coordinator tests use.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.shelly_cloud_diy import coordinator as coordinator_mod
from custom_components.shelly_cloud_diy.binary_sensor import (
    ShellyRelayFaultBinarySensor,
    _create_relay_fault_sensors,
)
from custom_components.shelly_cloud_diy.const import CONF_RELAY_FAULT_DETECTION
from custom_components.shelly_cloud_diy.coordinator import ShellyCloudCoordinator
from custom_components.shelly_cloud_diy.relay_fault import (
    STUCK_CLEAR_SECONDS,
    STUCK_MIN_POWER_W,
    STUCK_MIN_SECONDS,
    STUCK_MIN_STREAK,
    RelayReading,
    iter_relay_readings,
    relay_clear_verdict,
    relay_fault_verdict,
)
from custom_components.shelly_cloud_diy.repair_issues import (
    format_relay_fault_list,
)

DEVICE = "5432044e9768"
STUCK_W = 85.2  # the wattage the real failing unit reported


@pytest.fixture(autouse=True)
def _inert_repair_issues(monkeypatch):
    """Neutralise the repair-issue wrappers except the one under test.

    ``_evaluate_relay_faults`` ends by upserting a repair card, which reaches
    into the real issue registry; the stub ``hass`` here has none. The relay
    wrapper is replaced by a recorder rather than a no-op so the tests can
    assert what the card was told.
    """
    monkeypatch.setattr(
        coordinator_mod, "async_manage_rate_limit_issue",
        lambda hass, entry, *, active: None,
    )
    monkeypatch.setattr(
        coordinator_mod, "async_manage_missing_devices_issue",
        lambda hass, entry, *, active, missing, names: None,
    )
    # Every device here is enabled (``create_all_initially``) so that the
    # relay evaluation sees it at all, which also makes the poll dispatch
    # SIGNAL_NEW_DEVICE into a Home Assistant that does not exist.
    monkeypatch.setattr(
        coordinator_mod, "async_dispatcher_send", lambda *args, **kwargs: None
    )


# ── Payload shapes: what can be judged at all ─────────────────────────


def _gen2(output: bool, apower: float, channel: int = 0) -> dict[str, Any]:
    return {
        f"switch:{channel}": {"id": channel, "output": output, "apower": apower},
        "cloud": {"connected": True},
    }


def test_a_metered_gen2_switch_is_readable() -> None:
    assert iter_relay_readings(_gen2(False, STUCK_W)) == [
        RelayReading(channel=0, output=False, power=STUCK_W)
    ]


def test_a_switch_without_metering_is_not_judged() -> None:
    """A Shelly Plus 1 has a relay but nothing to contradict it with, so it
    must produce no reading rather than a reading of zero — the latter would
    render a "Relay fault" sensor that can never be anything but off."""
    assert iter_relay_readings({"switch:0": {"id": 0, "output": False}}) == []


def test_clamp_metering_is_not_judged() -> None:
    """An EM measures a circuit, not the contact next to it. A disagreement
    there says nothing about any relay, so the device must stay out."""
    status = {"em:0": {"total_act_power": 812.0}, "cloud": {"connected": True}}
    assert iter_relay_readings(status) == []


def test_both_channels_of_a_two_channel_device_are_read() -> None:
    status = {**_gen2(False, STUCK_W, 0), **_gen2(True, 12.0, 1)}
    assert [r.channel for r in iter_relay_readings(status)] == [0, 1]


def test_gen1_relay_and_meter_pair_up_by_index() -> None:
    status = {
        "relays": [{"ison": False}, {"ison": True}],
        "meters": [{"power": STUCK_W}, {"power": 0.0}],
    }
    assert iter_relay_readings(status) == [
        RelayReading(0, False, STUCK_W),
        RelayReading(1, True, 0.0),
    ]


def test_gen1_roller_mode_is_skipped_entirely() -> None:
    """In roller mode the two relays drive motor directions and the meter
    reads the motor. "Relay off, power flowing" is then simply a moving
    shutter — the single most obvious false positive available."""
    status = {
        "rollers": [{"state": "open"}],
        "relays": [{"ison": False}],
        "meters": [{"power": 120.0}],
    }
    assert iter_relay_readings(status) == []


def test_a_boolean_apower_is_not_mistaken_for_a_wattage() -> None:
    status = {"switch:0": {"output": False, "apower": True}}
    assert iter_relay_readings(status) == []


def test_a_non_dict_status_is_survivable() -> None:
    assert iter_relay_readings(None) == []  # type: ignore[arg-type]


# ── The disagreement itself ───────────────────────────────────────────


def test_trickle_current_is_not_a_fault() -> None:
    """Snubbers, LED drivers and meter noise put a couple of watts on an open
    contact. Anything under the floor must be silence, not a warning."""
    assert not RelayReading(0, False, STUCK_MIN_POWER_W - 0.1).disagrees


def test_a_real_load_on_an_open_relay_is_a_fault() -> None:
    assert RelayReading(0, False, STUCK_W).disagrees


def test_a_closed_relay_drawing_power_is_the_normal_case() -> None:
    assert not RelayReading(0, True, STUCK_W).disagrees


# ── The gates ─────────────────────────────────────────────────────────


def test_neither_gate_alone_is_enough() -> None:
    assert not relay_fault_verdict(STUCK_MIN_STREAK, 0.0, STUCK_MIN_SECONDS - 1)
    assert not relay_fault_verdict(STUCK_MIN_STREAK - 1, 0.0, STUCK_MIN_SECONDS + 1)
    assert relay_fault_verdict(STUCK_MIN_STREAK, 0.0, STUCK_MIN_SECONDS)


def test_clearing_is_slower_than_raising() -> None:
    """The asymmetry is the point: the hardware run produced a 45 s window of
    agreeing samples in the middle of a live fault."""
    assert STUCK_CLEAR_SECONDS > STUCK_MIN_SECONDS
    assert not relay_clear_verdict(0.0, STUCK_CLEAR_SECONDS - 1)
    assert relay_clear_verdict(0.0, STUCK_CLEAR_SECONDS)
    assert not relay_clear_verdict(None, 10_000.0)


# ── Driven through the coordinator ────────────────────────────────────


class _FakeApi:
    def __init__(self, devices_status: dict[str, Any]) -> None:
        self.devices_status = devices_status

    async def get_all_status(self) -> dict[str, Any]:
        return {"devices_status": self.devices_status}


class _StubHass:
    def async_create_task(self, coro: Any) -> None:
        coro.close()


def _coordinator(devices_status: dict[str, Any], **options: Any) -> ShellyCloudCoordinator:
    """A coordinator with only the polling wiring in place.

    ``__init__`` wants a HomeAssistant instance and a ConfigEntry; none of the
    relay-fault logic does. ``test_coordinator_init.py`` is the guard that
    keeps this shortcut from hiding a missing assignment.
    """
    coord = object.__new__(ShellyCloudCoordinator)
    coord._api = _FakeApi(devices_status)
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
    # The real one reaches into the HA device registry for a fallback label.
    # Its behaviour belongs to the missing-devices card and is covered there.
    coord._display_names = lambda ids: {
        d: coord.device_names[d] for d in ids if d in coord.device_names
    }
    return coord


class _CardRecorder:
    """Captures what the repair card was last told."""

    def __init__(self) -> None:
        self.calls: list[tuple[bool, set, dict]] = []

    def __call__(self, hass, entry, *, active, faults, names) -> None:
        self.calls.append((active, set(faults), dict(names)))

    @property
    def last_active(self) -> bool:
        return self.calls[-1][0]


@pytest.fixture
def card(monkeypatch) -> _CardRecorder:
    recorder = _CardRecorder()
    monkeypatch.setattr(
        coordinator_mod, "async_manage_relay_fault_issue", recorder
    )
    return recorder


def _poll(coord: ShellyCloudCoordinator, status: dict[str, Any], at: float) -> None:
    """Run one full poll with ``time.monotonic`` pinned to ``at``.

    Patched rather than slept through: the gates are measured in minutes and
    the clear in five of them, so a real-time test would take longer than the
    rest of the suite combined.
    """
    coord._api.devices_status = {DEVICE: status}
    original = time.monotonic
    time.monotonic = lambda: at  # type: ignore[assignment]
    try:
        asyncio.run(coord._async_update_data())
    finally:
        time.monotonic = original  # type: ignore[assignment]


def _drive(coord, status, *, start=0.0, polls=30, step=5.0) -> None:
    for i in range(polls):
        # Vary the fingerprint so the device counts as checking in.
        fresh = {**status, "serial": int(start) + i, "_updated": f"t{i}"}
        _poll(coord, fresh, start + i * step)


def test_a_healthy_device_is_never_accused(card) -> None:
    coord = _coordinator({})
    _drive(coord, _gen2(False, 0.0))
    assert coord.relay_faults == set()
    assert card.last_active is False


def test_a_load_running_through_an_open_relay_is_reported(card) -> None:
    """The office-light case, replayed: relay reports off, meter reports 85 W,
    and it never stops."""
    coord = _coordinator({})
    _drive(coord, _gen2(False, STUCK_W))
    assert coord.relay_faults == {(DEVICE, 0)}
    assert card.last_active is True
    assert coord.has_relay_fault(DEVICE, 0)


def test_nothing_is_said_before_both_gates_are_served(card) -> None:
    """Two minutes of disagreement is the floor. At the 5 s default interval
    that is ~24 polls, so five polls in the detector must still be silent —
    this is what keeps the post-command settling window out of the verdict."""
    coord = _coordinator({})
    _drive(coord, _gen2(False, STUCK_W), polls=STUCK_MIN_STREAK + 1, step=5.0)
    assert coord.relay_faults == set()
    assert card.last_active is False


def test_one_agreeing_sample_does_not_retract_a_standing_warning(card) -> None:
    """The measured failure mode: 45 s of ``apower = 0`` in the middle of a
    live fault. Retracting on that would have announced all-clear precisely
    while the contact was welded."""
    coord = _coordinator({})
    _drive(coord, _gen2(False, STUCK_W))
    assert coord.relay_faults == {(DEVICE, 0)}

    # Nine agreeing polls — the full 45 s window the hardware produced.
    _drive(coord, _gen2(False, 0.0), start=1000.0, polls=9, step=5.0)
    assert coord.relay_faults == {(DEVICE, 0)}, "warning retracted too early"


def test_a_repaired_actuator_clears_the_warning_on_its_own(card) -> None:
    coord = _coordinator({})
    _drive(coord, _gen2(False, STUCK_W))
    assert coord.relay_faults == {(DEVICE, 0)}

    _drive(coord, _gen2(False, 0.0), start=1000.0, polls=80, step=5.0)
    assert coord.relay_faults == set()
    assert card.last_active is False


def test_a_frozen_device_cannot_earn_a_new_accusation(card) -> None:
    """A device that stops reporting keeps re-serving its last payload. If
    polls counted instead of check-ins, one stale snapshot repeated often
    enough would manufacture a fault out of nothing — three hours of it
    here, against gates measured in minutes."""
    coord = _coordinator({})
    stuck = _gen2(False, STUCK_W)
    # Same fingerprint every time: the device is not checking in.
    for i in range(200):
        _poll(coord, {**stuck, "serial": 1, "_updated": "frozen"}, i * 60.0)
    assert coord.relay_faults == set()


def test_a_frozen_agreeing_payload_cannot_clear_a_warning(card) -> None:
    """The same rule in the other direction. A device that goes quiet while
    its last payload happened to read zero watts must not talk itself out of
    a standing warning by having that payload re-served."""
    coord = _coordinator({})
    _drive(coord, _gen2(False, STUCK_W))
    assert coord.relay_faults == {(DEVICE, 0)}

    healthy = _gen2(False, 0.0)
    for i in range(200):
        _poll(coord, {**healthy, "serial": 7, "_updated": "frozen"},
              10_000.0 + i * 60.0)
    assert coord.relay_faults == {(DEVICE, 0)}


def test_a_standing_warning_survives_the_device_going_quiet(card) -> None:
    """The other half of the same rule: a device that dies with a welded
    contact is still welded shut, so the verdict is retained."""
    coord = _coordinator({})
    _drive(coord, _gen2(False, STUCK_W))
    assert coord.relay_faults == {(DEVICE, 0)}

    for i in range(200):
        _poll(coord, {**_gen2(False, STUCK_W), "serial": 1, "_updated": "frozen"},
              10_000.0 + i * 60.0)
    assert coord.relay_faults == {(DEVICE, 0)}


def test_the_option_switches_the_detector_off_and_drops_its_verdicts(card) -> None:
    coord = _coordinator({}, **{CONF_RELAY_FAULT_DETECTION: False})
    _drive(coord, _gen2(False, STUCK_W))
    assert coord.relay_faults == set()
    assert card.last_active is False


def test_channels_are_judged_independently(card) -> None:
    """A 2PM can have one welded contact and one healthy one; accusing the
    device as a whole would send the user to the wrong terminal."""
    coord = _coordinator({})
    _drive(coord, {**_gen2(False, STUCK_W, 0), **_gen2(False, 0.0, 1)})
    assert coord.relay_faults == {(DEVICE, 0)}


# ── The entity ────────────────────────────────────────────────────────


def test_one_sensor_is_built_per_metered_channel() -> None:
    coord = _coordinator({})
    created: set[str] = set()
    status = {**_gen2(False, 0.0, 0), **_gen2(False, 0.0, 1)}
    entities = _create_relay_fault_sensors(DEVICE, status, created, coord)
    assert [e.unique_id for e in entities] == [
        f"{DEVICE}_relay_fault_0",
        f"{DEVICE}_relay_fault_1",
    ]
    assert [e.name for e in entities] == ["Relay fault", "Relay fault 2"]


def test_a_rediscovered_device_does_not_get_a_second_set() -> None:
    coord = _coordinator({})
    created: set[str] = set()
    status = _gen2(False, 0.0)
    assert _create_relay_fault_sensors(DEVICE, status, created, coord)
    assert _create_relay_fault_sensors(DEVICE, status, created, coord) == []


def test_the_sensor_reports_the_coordinator_verdict() -> None:
    coord = _coordinator({})
    coord.devices = {DEVICE: {"status": _gen2(False, STUCK_W), "online": True}}
    sensor = ShellyRelayFaultBinarySensor(coord, DEVICE, 0)
    assert sensor.is_on is False
    coord.relay_faults.add((DEVICE, 0))
    assert sensor.is_on is True


def test_the_sensor_carries_the_wattage_behind_the_verdict() -> None:
    """Without the number the warning is unarguable in both directions."""
    coord = _coordinator({})
    coord.devices = {DEVICE: {"status": _gen2(False, STUCK_W), "online": True}}
    sensor = ShellyRelayFaultBinarySensor(coord, DEVICE, 0)
    assert sensor.extra_state_attributes == {"power_while_off_w": STUCK_W}


def test_no_wattage_is_shown_while_the_relay_is_closed() -> None:
    """With the contact closed the reading is ordinary consumption, and
    showing it would invite comparison against a threshold it has nothing to
    do with."""
    coord = _coordinator({})
    coord.devices = {DEVICE: {"status": _gen2(True, STUCK_W), "online": True}}
    sensor = ShellyRelayFaultBinarySensor(coord, DEVICE, 0)
    assert sensor.extra_state_attributes is None


# ── The card's rendering ──────────────────────────────────────────────


def test_the_card_names_the_device_and_omits_a_pointless_channel() -> None:
    text = format_relay_fault_list({(DEVICE, 0)}, {DEVICE: "Büro Licht"})
    assert text == f"Büro Licht ({DEVICE})"


def test_the_card_spells_out_the_channel_from_the_second_one() -> None:
    text = format_relay_fault_list({(DEVICE, 1)}, {})
    assert text == f"{DEVICE} channel 2"


def test_the_card_stays_bounded_on_a_fleet_wide_condition() -> None:
    faults = {(f"dev{i:02d}", 0) for i in range(12)}
    text = format_relay_fault_list(faults, {})
    assert text.endswith("… (+7)")
