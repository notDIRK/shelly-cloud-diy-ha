"""Repair issues raised by the Shelly Cloud DIY polling path.

**Why this module is not called ``repairs.py``:** ``repairs.py`` is a
reserved Home Assistant platform filename. On HA 2025.1.4 — this repo's
stated minimum — ``components/repairs/issue_handler.py`` calls
``async_process_repairs_platforms(hass)``, which eagerly scans *every*
loaded integration that ships a ``repairs.py`` as soon as a user clicks
"Fix" on any integration's repair, and raises
``HomeAssistantError("Invalid repairs platform …")`` for any module that
does not expose ``async_create_fix_flow``. Every issue here is
informational (``is_fixable=False``) and therefore has no fix flow, so a
module named ``repairs.py`` would be a landmine for unrelated
integrations on that version. Newer HA uses lazy platform loading and
would be safe, but the two-venv test runner must pass on both. Using a
non-reserved filename sidesteps the platform contract entirely at zero
cost; if a real fix flow is ever wanted, add a proper ``repairs.py``
exposing ``async_create_fix_flow`` at that point.

All four issues are informational and non-persistent. None of them can
be resolved by an in-process button press — a sustained rate limit, a
device that vanished from the Shelly account, an unreachable local
gateway and a welded relay contact are all resolved outside Home
Assistant — so a confirm-only Fix button would lie to the user and the
card would simply reappear on the next poll. Every condition is
re-derived from live polling (or the next sync cycle) after a restart,
so nothing needs to be persisted.

Issue ids are suffixed with the config entry id: the issue registry keys
on ``(domain, issue_id)`` alone, so a second Shelly account would
otherwise clobber the first account's card.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, ORPHAN_FLOOR_ABS, ORPHAN_FLOOR_FRAC

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


# Both gates must hold. At the 5 s default poll interval the TIME gate is the
# binding one: 120 s is ~19 consecutive failed polls, since each failed poll
# also burns the 1.5 s in-call retry (_RATE_LIMIT_BACKOFF_S). At the 60 s
# maximum the COUNT gate binds: 5 polls is ~5 minutes. Either way the user
# must be losing every poll for at least two minutes before we say anything —
# long enough to outlast someone browsing the Shelly mobile app.
RATE_LIMIT_MIN_STREAK = 5
RATE_LIMIT_MIN_SECONDS = 120.0

# 24 hours, not minutes. A device that was genuinely sold or factory-reset is
# still gone tomorrow, so nothing here is urgent — and the entire value of this
# repair is that it is RIGHT. Ten minutes would have accused every Shelly BLU
# owner of selling hardware whenever the mains Shelly bridging it went down for
# a firmware update or a router reboot: BLU children appear in the cloud
# snapshot only while their gateway is bridging them. In practice the time gate
# always binds (24 h is >= 1440 polls even at the 60 s maximum interval); the
# streak gate is a cheap floor that keeps the verdict honest if the poll
# interval ever grows.
MISSING_DEVICE_MIN_STREAK = 10
MISSING_DEVICE_MIN_SECONDS = 86400.0

# At most this many device ids are listed in the repair card; the rest are
# summarised as a count so the card cannot render a 130-character id blob.
MISSING_DEVICE_MAX_LISTED = 5

# A resolved device name is user-supplied and can be arbitrarily long, so it
# is clamped before rendering: five entries of "Wohnzimmer Deckenlampe hinten
# (3494546e1f2a)" would otherwise blow past the bound MISSING_DEVICE_MAX_LISTED
# exists to enforce.
MISSING_DEVICE_MAX_NAME_LEN = 24

# When the clamp has to cut, it prefers the last word boundary at or after
# this offset, so "KG-SY-PSW-Licht-Buero-Dirk" ends on "…-Buero…" instead of
# mid-word "…-Di…". Below the floor the boundary is too early to be worth the
# characters it costs, and the hard cut is used instead.
MISSING_DEVICE_NAME_MIN_KEEP = 16

# Two unproductive syncs. Because the daily interval is 24 h and this counter
# is instance state on a service that async_setup_entry rebuilds on every
# reload, a naive "wait for the next daily run" would mean the repair never
# fires for exactly the population that needs it — someone actively editing
# their gateway URL, which reloads the entry each time. historical.py therefore
# schedules ONE 15-minute retry after the first unproductive run, so the second
# data point arrives inside the same session.
HISTORY_IMPORT_MIN_FAILURES = 2

# Delay of that one-shot retry. Exists purely so the second data point lands
# inside the same Home Assistant session rather than a day later.
HISTORY_IMPORT_RETRY_S = 900.0

# Same bound and the same reason as MISSING_DEVICE_MAX_LISTED: a fleet-wide
# condition must not render a card made of a hundred device ids.
RELAY_FAULT_MAX_LISTED = 5

ISSUE_RATE_LIMITED = "rate_limited"
ISSUE_MISSING_DEVICES = "missing_devices"
ISSUE_HISTORY_IMPORT_FAILED = "history_import_failed"
ISSUE_RELAY_FAULT = "relay_fault"


# ── Pure verdict helpers (no hass — unit-testable) ────────────────────


def issue_id(kind: str, entry_id: str) -> str:
    """Return the registry-unique id for ``kind`` on one config entry."""
    return f"{kind}_{entry_id}"


def rate_limit_verdict(
    streak: int, streak_started: float | None, now: float
) -> bool:
    """True when the rate limit has been sustained long enough to report."""
    return (
        streak >= RATE_LIMIT_MIN_STREAK
        and streak_started is not None
        and (now - streak_started) >= RATE_LIMIT_MIN_SECONDS
    )


def compute_missing_devices(
    enabled: set[str], seen: set[str], create_all: bool
) -> set[str]:
    """Return explicitly-enabled ids the cloud did not report.

    Returns an empty set when ``create_all`` is on: the enabled set is then
    DEFINED as the seen set, so the difference would be vacuous.
    """
    if create_all:
        return set()
    return enabled - seen


def is_mass_absence(missing: set[str], enabled: set[str]) -> bool:
    """True if so much of the selection is absent that the account or the API
    is the more likely explanation than N individual devices being sold.

    Mirrors the guard detect_orphans already applies (const.ORPHAN_FLOOR_*):
    this repair must not be less conservative than the pull-based service it
    tells the user to run. A total wipe-out counts too — if literally every
    selected device is gone, that is an account/server-side condition, not a
    fleet-wide garage sale. Deliberate trade: a user with a single enabled
    device that really was sold is never told. At 24 h of absence the false
    accusation is the worse error.
    """
    if not enabled or not missing:
        return False
    if missing >= enabled:          # everything selected has vanished
        return True
    return len(missing) > max(
        ORPHAN_FLOOR_ABS, int(ORPHAN_FLOOR_FRAC * len(enabled))
    )


def missing_devices_verdict(
    streak: int,
    streak_started: float | None,
    now: float,
    missing: set[str],
    enabled: set[str],
) -> bool:
    """True when the same ids have been absent long enough to report."""
    if not missing or is_mass_absence(missing, enabled):
        return False
    return (
        streak >= MISSING_DEVICE_MIN_STREAK
        and streak_started is not None
        and (now - streak_started) >= MISSING_DEVICE_MIN_SECONDS
    )


def history_import_verdict(
    consecutive_failures: int, gateway_url: str
) -> bool:
    """True when the historical import has been unproductive repeatedly.

    Users who never configured a gateway URL are never told about it.
    """
    return bool(gateway_url) and (
        consecutive_failures >= HISTORY_IMPORT_MIN_FAILURES
    )


def _clamp_name(name: str) -> str:
    """Trim a device name to keep the rendered card bounded.

    The cut lands on a word boundary where one sits late enough in the
    budget to be worth it, because the truncated half of a name is what the
    reader uses to recognise their device: a live card read
    "KG-SY-PSW-Licht-Buero-Di…", which looks like a rendering bug rather
    than a shortened name. Names without a usable separator still get the
    hard cut — the bound, not the prettiness, is what this function owes.
    """
    if len(name) <= MISSING_DEVICE_MAX_NAME_LEN:
        return name
    head = name[: MISSING_DEVICE_MAX_NAME_LEN - 1]
    cut = max(head.rfind(" "), head.rfind("-"), head.rfind("_"))
    if cut >= MISSING_DEVICE_NAME_MIN_KEEP:
        head = head[:cut]
    return head.rstrip(" -_") + "…"


def format_device_list(missing: set[str], names: dict[str, str]) -> str:
    """Render the missing ids for the card, readable and bounded.

    A bare ``3494546e1f2a`` is not shown anywhere in the HA UI, so where a
    label was resolved before the device vanished (a cloud alias, or the HA
    device-registry row that outlives it) we render ``Name (id)``. Falls
    back to the bare id. Names are clamped so the whole string stays
    bounded no matter what the user called their device.
    """
    ordered = sorted(missing)
    shown = ordered[:MISSING_DEVICE_MAX_LISTED]
    parts = [
        f"{_clamp_name(names[d])} ({d})" if names.get(d) else d for d in shown
    ]
    text = ", ".join(parts)
    if len(ordered) > len(shown):
        text += f", … (+{len(ordered) - len(shown)})"
    return text


# ── Registry wrappers ─────────────────────────────────────────────────
#
# Each takes the caller's already-computed ``active`` verdict and creates OR
# deletes in the same call. Existence is read from the issue registry, never
# tracked by the caller, so the two can never desync.


@callback
def async_manage_rate_limit_issue(
    hass: HomeAssistant, entry: ConfigEntry, *, active: bool
) -> None:
    """Create or clear the sustained-rate-limit issue for ``entry``."""
    iid = issue_id(ISSUE_RATE_LIMITED, entry.entry_id)
    if active:
        ir.async_create_issue(
            hass,
            DOMAIN,
            iid,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_RATE_LIMITED,
            translation_placeholders={"entry_title": entry.title},
        )
    else:
        # A no-op when the id was never raised, so no existence probe.
        ir.async_delete_issue(hass, DOMAIN, iid)


@callback
def async_manage_missing_devices_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    active: bool,
    missing: set[str],
    names: dict[str, str],
) -> None:
    """Create or clear the missing-devices issue for ``entry``.

    When the missing set changes while the issue is raised, just call this
    again with the new set: ``async_get_or_create`` replaces the
    placeholders in place and, unlike delete-then-create, PRESERVES the
    user's "Ignore" (which the registry stores on the issue itself). Never
    add a delete-then-create here.
    """
    iid = issue_id(ISSUE_MISSING_DEVICES, entry.entry_id)
    if active:
        ir.async_create_issue(
            hass,
            DOMAIN,
            iid,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_MISSING_DEVICES,
            translation_placeholders={
                "entry_title": entry.title,
                "count": str(len(missing)),
                "device_ids": format_device_list(missing, names),
            },
        )
    else:
        # A no-op when the id was never raised, so no existence probe.
        ir.async_delete_issue(hass, DOMAIN, iid)


def format_relay_fault_list(
    faults: set[tuple[str, int]], names: dict[str, str]
) -> str:
    """Render the affected switching channels for the card.

    The channel is only spelled out from the second one onwards: on a
    single-channel device "channel 1" is noise, on a 2PM it is the whole
    point of the line.
    """
    parts = []
    for device_id, channel in sorted(faults)[:RELAY_FAULT_MAX_LISTED]:
        label = (
            f"{_clamp_name(names[device_id])} ({device_id})"
            if names.get(device_id)
            else device_id
        )
        parts.append(label if channel == 0 else f"{label} channel {channel + 1}")
    text = ", ".join(parts)
    if len(faults) > len(parts):
        text += f", … (+{len(faults) - len(parts)})"
    return text


@callback
def async_manage_relay_fault_issue(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    active: bool,
    faults: set[tuple[str, int]],
    names: dict[str, str],
) -> None:
    """Create or clear the stuck-contact issue for ``entry``.

    Upserts rather than delete-then-create for the same reason as the
    missing-devices card: a second failing actuator must not silently
    un-ignore the first one.
    """
    iid = issue_id(ISSUE_RELAY_FAULT, entry.entry_id)
    if active:
        ir.async_create_issue(
            hass,
            DOMAIN,
            iid,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_RELAY_FAULT,
            translation_placeholders={
                "entry_title": entry.title,
                "count": str(len(faults)),
                "devices": format_relay_fault_list(faults, names),
            },
        )
    else:
        # A no-op when the id was never raised, so no existence probe.
        ir.async_delete_issue(hass, DOMAIN, iid)


@callback
def async_manage_history_import_issue(
    hass: HomeAssistant, entry: ConfigEntry, *, active: bool
) -> None:
    """Create or clear the historical-import issue for ``entry``."""
    iid = issue_id(ISSUE_HISTORY_IMPORT_FAILED, entry.entry_id)
    if active:
        ir.async_create_issue(
            hass,
            DOMAIN,
            iid,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_HISTORY_IMPORT_FAILED,
            translation_placeholders={"entry_title": entry.title},
        )
    else:
        # A no-op when the id was never raised, so no existence probe.
        ir.async_delete_issue(hass, DOMAIN, iid)


@callback
def async_clear_entry_issues(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Delete every issue this integration may hold for ``entry``."""
    for kind in (
        ISSUE_RATE_LIMITED,
        ISSUE_MISSING_DEVICES,
        ISSUE_HISTORY_IMPORT_FAILED,
        ISSUE_RELAY_FAULT,
    ):
        # ``async_delete_issue`` on a non-existent id is an explicit no-op.
        ir.async_delete_issue(hass, DOMAIN, issue_id(kind, entry.entry_id))
