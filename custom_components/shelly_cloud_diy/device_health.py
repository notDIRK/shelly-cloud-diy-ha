"""Threshold checks on the status snapshot we already poll ("Doctor").

Every input below is a field the coordinator receives on each ordinary
``/device/all_status`` poll and today throws away. Reading it costs **no
extra request**, no extra credential and nothing from Shelly's 1 req/s
budget — the only thing this module adds is an interpretation.

The thresholds were chosen against a real 64-device account
(``.planning/messungen/snapshot.json``, 2026-09-04), which is also why they
are not hypothetical: that sample already contains a device at -82 dBm, a
switch running at 78.5 °C and a component reporting ``errors: ["read"]``.

**Which devices are judged, and why not the rest**

* **Gen2+/RPC devices** get the full set. Every device-wide input (Wi-Fi
  signal, free RAM, free filesystem, pending restart, pending firmware) is
  present on 35/35 of them in the sample; component temperature and
  component errors exist only where the device has such a component.
* **BLE/gateway-bridged devices** (``_dev_info.gen == "GBLE"``) get exactly
  one check: the RSSI their bridging gateway reports for them. They carry
  none of the Gen2 fields, and judging a device against a field it does not
  have would turn "unknown" into "unhealthy" — the one mistake a health
  check must never make. Their ``devicepower:0.battery.V`` and ``.low`` are
  ``null`` on 28/29 and 29/29 respectively, so neither is usable; the
  battery *percent* is already a first-class entity and is not duplicated
  here as a finding.
* **Gen1 devices are skipped entirely.** No Gen1 device appears in any
  payload ever recorded from this account, so every Gen1 threshold would
  rest on vendor documentation rather than on something measured — the
  mistake that produced issue #32. A check nobody can verify is worse than
  no check.

**Own heat only.** The temperature check reads a component's *nested*
temperature (``switch:0.temperature.tC``) and deliberately ignores a
standalone ``temperature:<id>`` component: that one is an external probe on
a Shelly Add-on, measuring the world rather than the device. A probe in a
boiler flow pipe or a sauna is above 70 °C by design and the payload offers
no way to tell it apart from an overheating device. The check still has an
off switch in the options, the same escape hatch the relay-fault detector
offers for two-way circuits.

Kept free of Home Assistant imports apart from the shared generation
detection in ``const`` — duplicating that regex to buy import purity would
mean two definitions of "is this a Gen2 device" that can drift apart.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .const import device_gen

# ── Wi-Fi signal ──────────────────────────────────────────────────────
#
# -70 dBm is the point where a 2.4 GHz link stops having headroom: it still
# works, but retransmissions climb and a microwave oven or a neighbouring
# AP is enough to drop it. -85 dBm is close to the noise floor, where the
# link survives only because Shelly traffic is tiny and infrequent.
# Measured range in the sample: -82 … -31 dBm, so the warning line already
# fires on real hardware while the error line does not.
RSSI_WARNING_DBM = -70
RSSI_ERROR_DBM = -85

# ── Device temperature ────────────────────────────────────────────────
#
# Shelly rates its Gen2 hardware for 40 °C ambient and its own components
# well above 70 °C, so 70 is not "about to fail" — it is "this is running
# hotter than the rest of the fleet and that is usually an installation
# problem", typically a device buried in an insulated wall box next to a
# dimmer. 85 °C is where the device's own overtemperature protection starts
# to become relevant. Measured range: 36.6 … 78.5 °C.
TEMPERATURE_WARNING_C = 70.0
TEMPERATURE_ERROR_C = 85.0

# ── Free memory and free filesystem ───────────────────────────────────
#
# Expressed as a fraction of the device's own total, because a Gen2 device
# has ~256 KB of RAM and a Gen3 more: an absolute byte floor would mean a
# different thing on each. Below 20 % free a device can still run but has
# no room for a script, an OTA image or a burst of MQTT; below 10 % Shelly
# firmware starts refusing work and rebooting. The whole sample sits
# between 30.4 % and 54.2 % free RAM and 25.0 % and 50.4 % free filesystem,
# so neither line fires on healthy hardware.
FREE_WARNING_FRACTION = 0.20
FREE_ERROR_FRACTION = 0.10

# ── The two gates before anything is announced ────────────────────────
#
# Mirrors the relay-fault detector and the rate-limit gate: a streak AND a
# wall-clock floor, both of which have to hold.
#
# The count is in CHECK-INS, not polls — three times the device itself said
# so, not three times we asked. A device that freezes keeps re-serving its
# last payload for as long as the cloud caches it, so counting polls would
# let a single snapshot be repeated into a verdict.
#
# Three rather than the relay detector's five, because the evidence here is
# not a contradiction that needs corroborating: a temperature reading is
# self-contained, and the cost of being wrong is a card, not an accusation
# that someone's actuator is defective. 300 s rather than 120 s for the
# opposite reason — none of these findings is urgent, and the one thing
# that would ruin the card is firing it at a device that is briefly warm
# after a firmware flash or briefly short of RAM while a script starts. At
# the 5 s default poll the streak is served in ~15 s, so the time floor is
# the binding gate on every device that reports faster than once every
# 100 s.
HEALTH_MIN_STREAK = 3
HEALTH_MIN_SECONDS = 300.0

# Severities. Deliberately a plain string rather than an enum: these travel
# into a repair-card placeholder and into diagnostics, and both want text.
SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ERROR = "error"

CHECK_WIFI = "wifi_signal"
CHECK_TEMPERATURE = "temperature"
CHECK_RAM = "ram"
CHECK_FILESYSTEM = "filesystem"
CHECK_RESTART = "restart_required"
CHECK_COMPONENT_ERROR = "component_error"
CHECK_FIRMWARE = "firmware_update"

# Short English labels for the aggregated repair card. They are rendered
# inside a translated description as a placeholder, so they stay terse and
# recognisable rather than trying to be a sentence.
CHECK_LABELS: dict[str, str] = {
    CHECK_WIFI: "Wi-Fi signal",
    CHECK_TEMPERATURE: "Temperature",
    CHECK_RAM: "Free memory",
    CHECK_FILESYSTEM: "Free storage",
    CHECK_RESTART: "Restart pending",
    CHECK_COMPONENT_ERROR: "Component error",
    CHECK_FIRMWARE: "Firmware update",
}

# ``<type>:<id>`` — the shape of every Gen2 component key. Anything without
# the numeric suffix (``sys``, ``wifi``, ``cloud``, ``ws``) is a singleton
# block, not a component, and is read by name where it is wanted.
_COMPONENT_KEY_RE = re.compile(r"^([a-z_]+):(\d+)$")


@dataclass(frozen=True, order=True)
class HealthFinding:
    """One threshold crossing on one device.

    ``component`` is the status key the finding came from — a real
    component (``switch:0``, ``temperature:100``) or the device-wide block
    it was read out of (``sys``, ``wifi``, ``reporter``). ``detail`` carries the measured value, because the
    number is the whole argument: "this device is warm" is an opinion,
    "78.5 °C" is something the user can act on or dismiss.
    """

    check: str
    component: str
    severity: str
    detail: str

    @property
    def label(self) -> str:
        """Human name of the check, for the aggregated card."""
        return CHECK_LABELS.get(self.check, self.check)


def _as_number(value: Any) -> float | None:
    """Return ``value`` as a float, or None if it is not a usable number.

    ``bool`` is rejected explicitly: it is a subclass of ``int`` in Python,
    and a device that reports ``tC: true`` would otherwise be measured at
    one degree.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _rssi_finding(component: str, value: Any) -> HealthFinding | None:
    """Judge one RSSI reading, whether Wi-Fi or gateway-reported."""
    rssi = _as_number(value)
    # A real RSSI is negative. Zero (and anything above it) is what a device
    # reports when it has no measurement, not a perfect link, so it must not
    # be read as "excellent" and must not be read as "unhealthy" either.
    if rssi is None or rssi >= 0:
        return None
    if rssi <= RSSI_ERROR_DBM:
        severity = SEVERITY_ERROR
    elif rssi <= RSSI_WARNING_DBM:
        severity = SEVERITY_WARNING
    else:
        return None
    return HealthFinding(CHECK_WIFI, component, severity, f"{rssi:.0f} dBm")


def _temperature_findings(status: dict[str, Any]) -> list[HealthFinding]:
    """Every component that reports its OWN heat and runs hot.

    Only the nested shape counts — ``switch:0.temperature.tC``,
    ``light:0.temperature.tC`` — because that is a component measuring
    itself. A standalone ``temperature:<id>`` component is the opposite: it
    is an *external* probe on a Shelly Add-on, measuring the world. A sensor
    in a boiler flow pipe or a sauna sits above 70 °C by design, and the
    payload gives us nothing to tell that apart from an overheating device.

    That exclusion costs nothing measurable: in the 64-device sample both
    hot readings (71.3 °C and 78.5 °C) come from ``switch:0``, and every
    standalone ``temperature:<id>`` present is either a BLU ambient sensor
    around 25 °C or an add-on channel with ``errors: ["read"]`` — which is
    still judged, by the component-error check where it belongs.
    """
    findings: list[HealthFinding] = []
    for key, payload in status.items():
        match = _COMPONENT_KEY_RE.match(key) if isinstance(key, str) else None
        if match is None or not isinstance(payload, dict):
            continue
        if match.group(1) == "temperature":
            continue
        nested = payload.get("temperature")
        raw = nested.get("tC") if isinstance(nested, dict) else None
        celsius = _as_number(raw)
        if celsius is None:
            continue
        if celsius >= TEMPERATURE_ERROR_C:
            severity = SEVERITY_ERROR
        elif celsius >= TEMPERATURE_WARNING_C:
            severity = SEVERITY_WARNING
        else:
            continue
        findings.append(
            HealthFinding(
                CHECK_TEMPERATURE, key, severity, f"{celsius:.1f} °C"
            )
        )
    return findings


def _free_finding(
    check: str, component: str, free: Any, size: Any
) -> HealthFinding | None:
    """Judge a free/total pair as a fraction of the device's own capacity."""
    free_value = _as_number(free)
    size_value = _as_number(size)
    if free_value is None or size_value is None or size_value <= 0:
        return None
    fraction = free_value / size_value
    if fraction < FREE_ERROR_FRACTION:
        severity = SEVERITY_ERROR
    elif fraction < FREE_WARNING_FRACTION:
        severity = SEVERITY_WARNING
    else:
        return None
    return HealthFinding(
        check, component, severity, f"{fraction * 100:.0f} % free"
    )


def _component_error_findings(status: dict[str, Any]) -> list[HealthFinding]:
    """Components that are telling us themselves that something is wrong.

    This is the one check with no threshold to argue about: the device has
    already made the judgement and put it in an ``errors`` list. Measured
    on real hardware as ``temperature:100 → ["read"]`` (an add-on probe
    that is wired but not answering) and ``light:1 → ["cal_abort:no_load"]``.
    """
    findings: list[HealthFinding] = []
    for key, payload in status.items():
        match = _COMPONENT_KEY_RE.match(key) if isinstance(key, str) else None
        if match is None or not isinstance(payload, dict):
            continue
        errors = payload.get("errors")
        if not isinstance(errors, list) or not errors:
            continue
        detail = ", ".join(str(err) for err in errors)
        findings.append(
            HealthFinding(CHECK_COMPONENT_ERROR, key, SEVERITY_ERROR, detail)
        )
    return findings


def _firmware_finding(sys_block: dict[str, Any]) -> HealthFinding | None:
    """Report a pending firmware update as information, never as a fault.

    24 of 35 Gen2 devices in the measured account had one pending. A finding
    that fires on two thirds of a fleet is wallpaper, so this one is behind
    its own opt-in and carries the lowest severity there is.
    """
    updates = sys_block.get("available_updates")
    if not isinstance(updates, dict) or not updates:
        return None
    versions = []
    for channel in ("stable", "beta"):
        entry = updates.get(channel)
        if isinstance(entry, dict) and entry.get("version"):
            versions.append(f"{channel} {entry['version']}")
    detail = ", ".join(versions) if versions else "available"
    return HealthFinding(CHECK_FIRMWARE, "sys", SEVERITY_INFO, detail)


def evaluate_device_health(
    status: dict[str, Any], *, include_firmware: bool = False
) -> list[HealthFinding]:
    """Return every threshold crossing in one device's status snapshot.

    Pure: same snapshot in, same findings out, no clock and no state. The
    decision about whether a finding has lasted long enough to be worth
    telling anyone about belongs to the caller (see the gates in
    ``coordinator._evaluate_device_health``), because that decision needs a
    history this function deliberately does not have.
    """
    if not isinstance(status, dict) or not status:
        return []

    gen = device_gen(status)

    if gen == "GBLE":
        # The reduced set, and it really is one check. ``reporter.rssi`` is
        # the gateway's view of the beacon, so a finding here means "the
        # bridge barely hears this sensor", which is the one actionable
        # thing about a BLU device we can see from the cloud at all.
        reporter = status.get("reporter")
        if not isinstance(reporter, dict):
            return []
        finding = _rssi_finding("reporter", reporter.get("rssi"))
        return [finding] if finding else []

    if gen != "G2":
        # Gen1 (and anything unrecognised). See the module docstring: no
        # measured payload exists, so there is nothing honest to check.
        return []

    findings: list[HealthFinding] = []

    wifi = status.get("wifi")
    if isinstance(wifi, dict):
        if finding := _rssi_finding("wifi", wifi.get("rssi")):
            findings.append(finding)

    findings.extend(_temperature_findings(status))
    findings.extend(_component_error_findings(status))

    sys_block = status.get("sys")
    if isinstance(sys_block, dict):
        if finding := _free_finding(
            CHECK_RAM, "sys", sys_block.get("ram_free"), sys_block.get("ram_size")
        ):
            findings.append(finding)
        if finding := _free_finding(
            CHECK_FILESYSTEM, "sys",
            sys_block.get("fs_free"), sys_block.get("fs_size"),
        ):
            findings.append(finding)
        if sys_block.get("restart_required"):
            # Never observed true in the measured sample — coded from the
            # field's presence (35/35) and documented as untested rather
            # than claimed as verified.
            findings.append(
                HealthFinding(
                    CHECK_RESTART, "sys", SEVERITY_ERROR,
                    "configuration change not applied yet",
                )
            )
        if include_firmware and (finding := _firmware_finding(sys_block)):
            findings.append(finding)

    return sorted(findings)


def health_verdict(
    streak: int, first_seen: float | None, now: float
) -> bool:
    """True when one finding has persisted long enough to be worth saying.

    There is no matching ``health_clear_verdict``. Clearing is immediate on
    the first check-in that no longer carries the finding, which is
    deliberately *not* slower than raising: a transient 71 °C reading that
    falls back to 60 °C has stopped being true, and the relay detector's
    asymmetry exists only because a welded contact can produce an innocent
    sample mid-fault. Nothing measured here can.
    """
    return (
        streak >= HEALTH_MIN_STREAK
        and first_seen is not None
        and (now - first_seen) >= HEALTH_MIN_SECONDS
    )


def finding_key(device_id: str, finding: HealthFinding) -> tuple[str, str, str]:
    """Identity of a finding across polls, for the streak bookkeeping.

    The severity and the measured value are deliberately NOT part of it: a
    device drifting from -71 to -73 dBm is the same finding getting slightly
    worse, and restarting its clock on every fluctuation would mean a
    genuinely borderline link could never complete the streak.
    """
    return (device_id, finding.check, finding.component)


def summarise_findings(
    findings: dict[str, tuple[HealthFinding, ...]]
) -> str:
    """Render "which kinds of finding, how many" for the aggregated card.

    Bounded by construction — there are seven checks — so unlike the device
    list this needs no truncation. Counts findings, not devices: one device
    with five dead add-on probes is five component errors, and collapsing
    that to "1" would understate what the user is looking at.
    """
    counts: dict[str, int] = {}
    for device_findings in findings.values():
        for finding in device_findings:
            counts[finding.check] = counts.get(finding.check, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ", ".join(
        f"{CHECK_LABELS.get(check, check)} ({count})" for check, count in ordered
    )
