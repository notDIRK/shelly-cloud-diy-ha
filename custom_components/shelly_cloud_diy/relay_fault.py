"""Detection of a relay whose contact no longer opens ("stuck contact").

A switching Shelly reports what it *commanded* (``output``) and what it
*measures* (``apower``) in the same status payload. When the two disagree —
the relay says open, the meter says a load is running — the contact has
welded shut. The device keeps accepting commands and keeps reporting
``off``; only the meter gives it away.

Measured on real failing hardware (Shelly 1PM Mini Gen3, 2026-08-17). With
the load switched off through Home Assistant::

    A) no command, just observing   output=false   apower=85.2 W
    B) turn_on                      output=true    apower=85.2 W
    C) turn_off                     output=false   apower=85.2 W  (80 s)

The lamp never went dark and could not be switched off in software at all.
That run also produced the two design constraints encoded below:

* **The moment right after a command lies.** In one of two turn_off runs the
  device reported ``apower = 0`` for ~45 s while the load was demonstrably
  still running. A detector that trusts a single sample would therefore have
  announced "all clear" precisely while the fault was present, so the verdict
  needs both a streak and a wall-clock floor, and clearing needs its own,
  longer floor (see :data:`STUCK_CLEAR_SECONDS`).
* **The energy counter is not a fast second opinion.** Over the same run it
  stood still for 80 s and then jumped by 1.0 Wh at once, so it resolves
  minutes, not seconds. Only ``apower`` is read here.

Deliberately *no* learned per-device threshold, unlike the offline detector
in ``coordinator.py``. Learning what "off" normally draws would be poisoned
by exactly the situation this detects: a contact that is already welded when
Home Assistant starts would teach the device that 85 W is its idle draw, and
the fault would then be unreportable forever. A fixed floor cannot be taught
anything.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Below this, never accuse anything. An open relay is not necessarily a
# zero-watt reading: snubber/RC networks across the contact, electronic LED
# drivers and the meter's own noise floor all put a trickle on the line. The
# floor is set well above that band rather than at the smallest load we could
# theoretically catch — the cost of missing a 3 W night light is a missed
# nicety, the cost of a false accusation is a user pulling a working actuator
# out of a wall.
STUCK_MIN_POWER_W = 5.0

# Both gates must hold before the fault is announced, mirroring the
# rate-limit gate in ``repair_issues.py``.
#
# The count is in CHECK-INS, not polls — five times the device itself said
# so, not five times we asked. That distinction is the whole defence against
# stale data: a device that freezes keeps re-serving its last payload for as
# long as the cloud caches it, and counting polls would let one snapshot be
# repeated into a verdict. Counting check-ins makes the number mean five
# independent reports.
#
# Cost of that choice: on a device reporting once a minute the warning
# arrives after ~5 minutes rather than ~2. For "your actuator is broken"
# that is the right trade.
STUCK_MIN_STREAK = 5
STUCK_MIN_SECONDS = 120.0

# Clearing is deliberately slower than raising, because the failure mode is
# asymmetric: a single spurious ``apower = 0`` sample (which the hardware run
# produced) must not retract a standing warning about a welded contact, while
# a genuinely repaired or replaced actuator only costs the user five minutes
# of a stale card. Never make this shorter than the ~45 s blind window.
STUCK_CLEAR_SECONDS = 300.0

_SWITCH_KEY_RE = re.compile(r"^switch:(\d+)$")


@dataclass(frozen=True)
class RelayReading:
    """One switching channel's commanded state next to its measured load."""

    channel: int
    output: bool
    power: float

    @property
    def disagrees(self) -> bool:
        """True when the relay claims open while a real load is flowing."""
        return not self.output and self.power >= STUCK_MIN_POWER_W


def _as_power(value: Any) -> float | None:
    """Return ``value`` as watts, or None if it is not a usable number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def iter_relay_readings(status: dict[str, Any]) -> list[RelayReading]:
    """Return every channel of ``status`` that can be judged at all.

    Only channels that both switch a relay *and* meter that same relay's
    output qualify. That requirement is what keeps clamp-metering devices
    (Gen2 ``em:0``, Gen1 ``emeters``) out on its own: their measurement has
    no defined relationship to any contact in the same housing, so a
    disagreement there would mean nothing.
    """
    if not isinstance(status, dict):
        return []

    readings: list[RelayReading] = []

    # Gen2/Gen3 — ``switch:N`` carries both fields. A non-metering switch
    # (Shelly Plus 1) simply has no ``apower`` and drops out here.
    for key, payload in status.items():
        match = _SWITCH_KEY_RE.match(key) if isinstance(key, str) else None
        if match is None or not isinstance(payload, dict):
            continue
        output = payload.get("output")
        power = _as_power(payload.get("apower"))
        if not isinstance(output, bool) or power is None:
            continue
        readings.append(RelayReading(int(match.group(1)), output, power))

    if readings:
        return sorted(readings, key=lambda r: r.channel)

    # Gen1 — parallel ``relays`` / ``meters`` lists.
    #
    # Roller-shutter devices are skipped wholesale: in that mode the two
    # relays drive motor directions, and the meter reads the motor while the
    # shutter travels. "Relay off, power flowing" is then the normal picture
    # for a moving cover, not a fault.
    #
    # Unit-tested only — the account this was developed against has no Gen1
    # hardware, so unlike the Gen2 path above this shape has never been seen
    # in a live payload.
    if status.get("rollers"):
        return []
    relays = status.get("relays")
    meters = status.get("meters")
    if not isinstance(relays, list) or not isinstance(meters, list):
        return []
    for idx, relay in enumerate(relays):
        if idx >= len(meters):
            break
        meter = meters[idx]
        if not isinstance(relay, dict) or not isinstance(meter, dict):
            continue
        output = relay.get("ison")
        power = _as_power(meter.get("power"))
        if not isinstance(output, bool) or power is None:
            continue
        readings.append(RelayReading(idx, output, power))

    return readings


def relay_fault_verdict(
    streak: int, streak_started: float | None, now: float
) -> bool:
    """True when the disagreement has lasted long enough to report."""
    return (
        streak >= STUCK_MIN_STREAK
        and streak_started is not None
        and (now - streak_started) >= STUCK_MIN_SECONDS
    )


def relay_clear_verdict(healthy_since: float | None, now: float) -> bool:
    """True when a standing fault has been contradicted for long enough."""
    return (
        healthy_since is not None
        and (now - healthy_since) >= STUCK_CLEAR_SECONDS
    )
