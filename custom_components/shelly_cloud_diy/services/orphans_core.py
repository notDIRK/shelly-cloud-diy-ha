"""Pure decision core for the "devices no longer in your account" detector.

This module deliberately has **zero** Home Assistant imports and no relative
imports — only the standard library (``dataclasses``, ``re``, ``typing``). It
is the trustworthy, standalone-testable heart of the detector: the handler in
``orphans.py`` distils Home Assistant registry/coordinator state down to the
primitives these functions take, so every risky judgement (is a device really
gone? is the account inventory trustworthy? should anything be deleted?) lives
here where it can be unit-tested without a running Home Assistant.

The central safety rule this module encodes: membership in the account is
decided from the **alias-independent** account inventory (every device id the
account lists, renamed or not), never from a name/alias map (which silently
omits un-aliased devices — an offline, never-renamed device would then look
"gone"). ``classify`` only ever moves a device into ``candidates`` when it is
absent from that authoritative inventory *and* from every trusted sibling
account's inventory.

The word "orphan" is used freely in code and comments here; it must NEVER
reach a user-visible string (see ``build_report`` — its output is checked for
that substring in the tests).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence


@dataclass(frozen=True)
class DeviceView:
    """One of *our* HA devices, distilled to primitives for the pure core."""

    ha_device_id: str
    cloud_id: str  # already folded to the parent device id
    name: str
    area: Optional[str]
    entity_count: int


@dataclass(frozen=True)
class Classification:
    """The three buckets every managed device sorts into."""

    healthy: list[DeviceView]  # present in own or a trusted sibling account
    curated_off: list[DeviceView]  # in account, but not on the enabled list
    candidates: list[DeviceView]  # gone from own AND every sibling account


def fold_channel_id(cloud_id: str) -> str:
    """Fold a per-channel sub-entry id into its parent device id.

    The account device list contains per-channel sub-entries with ids like
    ``<parentid>_<digits>`` (a channel of a multi-channel device, NOT a
    separate device). Strip a trailing ``_<digits>`` suffix to recover the
    parent id; any other id is returned unchanged. A real Wi-Fi MAC (12 hex)
    and an ``XB<decimal>`` id never contain ``_``, so only true channel
    sub-entries are folded.

    Duplicated on purpose from ``fleet_map._fold_channel_id`` to keep this
    module free of any Home Assistant / relative import and standalone-testable.
    """
    m = re.match(r"^(.+)_\d+$", cloud_id)
    return m.group(1) if m else cloud_id


def assess_trust(
    inv_ids: frozenset[str],
    isok: bool,
    well_formed: bool,
    raw_count: int,
    online_ids: frozenset[str],
    our_ha_count: int,
) -> tuple[bool, Optional[str]]:
    """Decide whether the fetched account inventory is trustworthy enough to
    base deletions on.

    ``inv_ids`` and ``online_ids`` are **folded** frozensets. The inventory is
    trusted ONLY IF every one of these holds:

    - ``isok`` is truthy (the API call itself reported success);
    - ``well_formed`` (the response actually carried a non-empty device map);
    - ``online_ids <= inv_ids`` (every device we are *live-polling right now*
      is present in the inventory — if a live device is missing, the inventory
      is demonstrably incomplete and cannot be used to prove absence);
    - ``raw_count >= our_ha_count`` (the inventory lists at least as many
      devices as we materialised — a truncated response that lists fewer
      devices than HA manages is a red flag).

    Returns ``(True, None)`` when trusted, else ``(False, <reason-key>)`` with a
    short, localisation-ready reason key.
    """
    if not isok:
        return (False, "api_not_ok")
    if not well_formed:
        return (False, "empty_response")
    if not online_ids <= inv_ids:
        return (False, "live_devices_missing")
    if raw_count < our_ha_count:
        return (False, "inventory_smaller_than_ha")
    return (True, None)


def classify(
    devices: Sequence[DeviceView],
    inv_ids: frozenset[str],
    enabled_ids: Optional[frozenset[str]],
    sibling_inv_ids: frozenset[str],
) -> Classification:
    """Sort managed devices into healthy / curated-off / candidate buckets.

    ``enabled_ids`` is a **folded** frozenset, or ``None`` for create-all mode
    (no curated list → nothing is ever "curated off"; everything materialised
    counts as enabled).

    For each device (its ``cloud_id`` is already folded):

    - present in the account inventory → ``healthy`` — UNLESS a curated list is
      in use and the id is not on it, in which case ``curated_off`` (still in
      the account, just deselected: it must NEVER be a deletion candidate);
    - absent from own inventory but present in a **trusted** sibling account's
      inventory → ``healthy`` (a shared / re-scoped-token device);
    - absent from both → ``candidate`` (a genuine "no longer in any account").
    """
    healthy: list[DeviceView] = []
    curated_off: list[DeviceView] = []
    candidates: list[DeviceView] = []
    for d in devices:
        if d.cloud_id in inv_ids:
            if enabled_ids is not None and d.cloud_id not in enabled_ids:
                curated_off.append(d)
            else:
                healthy.append(d)
        elif d.cloud_id in sibling_inv_ids:
            healthy.append(d)
        else:
            candidates.append(d)
    return Classification(
        healthy=healthy, curated_off=curated_off, candidates=candidates
    )


def apply_mass_floor(
    candidate_count: int,
    total_devices: int,
    floor_abs: int = 5,
    floor_frac: float = 0.25,
) -> tuple[bool, Optional[str]]:
    """Guard against a whole account transiently looking "gone".

    A single API hiccup that returns a plausible-but-wrong inventory could make
    a large share of devices look absent. If the candidate count exceeds
    ``max(floor_abs, floor_frac * total_devices)`` we refuse to treat this run
    as actionable and force the caller into a report-only, degraded state.

    Returns ``(True, None)`` when the count is within the floor, else
    ``(False, "mass_flag")``.
    """
    threshold = max(floor_abs, floor_frac * total_devices)
    if candidate_count > threshold:
        return (False, "mass_flag")
    return (True, None)


def select_actionable(
    deletable_ids: frozenset[str],
    user_target_ids: Optional[frozenset[str]],
) -> frozenset[str]:
    """Bind the deletion set to the user's consent.

    When the user supplied a ``devices`` target list, only the intersection of
    that list with the independently-derived deletable set is actionable (a
    device the user pointed at that is NOT independently deletable is excluded;
    a deletable device the user did not point at is likewise excluded). With no
    target list, every deletable id is actionable.
    """
    if user_target_ids is None:
        return deletable_ids
    return deletable_ids & user_target_ids


def sibling_inventory_union(
    entries: Sequence[tuple[bool, frozenset[str]]], index: int
) -> frozenset[str]:
    """Union of the folded inventories of every *other* **trusted** entry.

    ``entries`` is a sequence of ``(trusted, inv_folded)`` pairs — one per loaded
    account, in the same order the handler holds them. The sibling union for the
    account at ``index`` is the union of ``inv_folded`` across all the *other*
    accounts whose own inventory was itself trusted. An untrusted sibling
    contributes NOTHING: a device may only be rescued from candidacy by an
    account whose inventory we independently believe is complete.
    """
    out: frozenset[str] = frozenset()
    for i, (trusted, inv_folded) in enumerate(entries):
        if i != index and trusted:
            out = out | inv_folded
    return out


def decide_entry(
    views: Sequence[DeviceView],
    inv_ids: frozenset[str],
    enabled_ids: Optional[frozenset[str]],
    sibling_inv_ids: frozenset[str],
    trusted: bool,
    reason: Optional[str],
    floor_abs: int = 5,
    floor_frac: float = 0.25,
) -> tuple[Classification, bool, Optional[str], list[DeviceView]]:
    """Pure per-account decision pipeline: classify → mass-floor → eligibility.

    This is the exact wiring the handler applies, hoisted here so it is unit
    testable without a running Home Assistant. Given the classified buckets and
    the incoming ``assess_trust`` verdict:

    - if the mass-absence floor trips, the entry is forced ``trusted=False`` with
      reason ``"mass_flag"`` (a whole-account glitch must never be actioned);
    - ``eligible`` is the candidate list **only** when the entry is trusted after
      the mass-floor check, otherwise the empty list.

    Returns ``(classification, trusted_final, reason_final, eligible)``.
    """
    cls = classify(views, inv_ids, enabled_ids, sibling_inv_ids)
    ok, mass_reason = apply_mass_floor(
        len(cls.candidates), len(views), floor_abs=floor_abs, floor_frac=floor_frac
    )
    trusted_final = trusted
    reason_final = reason
    if not ok:
        trusted_final = False
        reason_final = mass_reason
    eligible = list(cls.candidates) if trusted_final else []
    return cls, trusted_final, reason_final, eligible


# ── Localised report strings ─────────────────────────────────────────────

_STRINGS: dict[str, dict[str, object]] = {
    "en": {
        "lead": (
            "Checked {total} device(s) managed by this integration against "
            "your live Shelly account inventory."
        ),
        "eligible_title": "No longer in your account — eligible for removal:",
        "curated_title": (
            "Still in your account but not on your enabled list — will NOT "
            "be removed:"
        ),
        "none": "  (none)",
        "degraded_title": (
            "Could not safely check removals right now — nothing is listed "
            "for removal:"
        ),
        "entities": "entities",
        "reason_api_not_ok": "the account inventory request did not succeed",
        "reason_empty_response": "the account returned no device inventory",
        "reason_live_devices_missing": (
            "live devices were missing from the returned inventory "
            "(it looks incomplete)"
        ),
        "reason_inventory_smaller_than_ha": (
            "the inventory listed fewer devices than Home Assistant manages "
            "(it looks incomplete)"
        ),
        "reason_mass_flag": (
            "an unusually large share of devices looked absent "
            "(likely a temporary account or API glitch)"
        ),
        "footer": [
            "What removing does:",
            "  It detaches the device and its entities from Home Assistant. "
            "It will NOT",
            "  come back automatically — re-add it in the integration options "
            "if the",
            "  hardware returns.",
            "  Back up Home Assistant before removing anything.",
            "  To remove a single device by hand, open its device page and use "
            "the",
            "  Delete button there.",
            "  To let this service remove the device(s) listed above, run it "
            "again",
            "  with Dry run turned off and Remove turned on.",
        ],
    },
    "de": {
        "lead": (
            "{total} von dieser Integration verwaltete(s) Gerät(e) mit dem "
            "aktuellen Bestand deines Shelly-Kontos abgeglichen."
        ),
        "eligible_title": "Nicht mehr in deinem Konto — zum Entfernen vorgemerkt:",
        "curated_title": (
            "Noch in deinem Konto, aber nicht auf deiner Aktiv-Liste — wird "
            "NICHT entfernt:"
        ),
        "none": "  (keine)",
        "degraded_title": (
            "Der Abgleich konnte gerade nicht sicher durchgeführt werden — es "
            "wird nichts zum Entfernen aufgeführt:"
        ),
        "entities": "Entitäten",
        "reason_api_not_ok": "die Bestandsabfrage des Kontos war nicht erfolgreich",
        "reason_empty_response": "das Konto lieferte keinen Gerätebestand",
        "reason_live_devices_missing": (
            "aktive Geräte fehlten im gelieferten Bestand (er wirkt "
            "unvollständig)"
        ),
        "reason_inventory_smaller_than_ha": (
            "der Bestand enthielt weniger Geräte, als Home Assistant verwaltet "
            "(er wirkt unvollständig)"
        ),
        "reason_mass_flag": (
            "ungewöhnlich viele Geräte wirkten abwesend (vermutlich eine "
            "vorübergehende Konto- oder API-Störung)"
        ),
        "footer": [
            "Was das Entfernen bewirkt:",
            "  Es löst das Gerät und seine Entitäten von Home Assistant. Es "
            "kommt NICHT",
            "  automatisch zurück — füge es in den Integrationsoptionen wieder "
            "hinzu,",
            "  falls die Hardware zurückkehrt.",
            "  Sichere Home Assistant, bevor du etwas entfernst.",
            "  Um ein einzelnes Gerät von Hand zu entfernen, öffne seine "
            "Geräteseite und",
            "  nutze dort die Schaltfläche zum Löschen.",
            "  Damit dieser Dienst die oben aufgeführten Geräte entfernt, "
            "führe ihn erneut",
            "  aus — mit ausgeschaltetem Trockenlauf und eingeschaltetem "
            "Entfernen.",
        ],
    },
}


def _format_rows(rows: Iterable[Mapping[str, object]], entities_word: str) -> list[str]:
    """Render report rows: name + area primary, cloud_id parenthetical, count,
    then the device-page deep link on its own line."""
    lines: list[str] = []
    for row in rows:
        name = str(row.get("name") or row.get("cloud_id") or "")
        area = row.get("area")
        head = name
        if area:
            head += f" — {area}"
        head += f" ({row.get('cloud_id')})"
        count = row.get("entity_count", 0)
        lines.append(f"  • {head} — {count} {entities_word}")
        lines.append(f"      /config/devices/device/{row.get('ha_device_id')}")
    return lines


def build_report(
    *,
    language: str,
    entry_label: Optional[str],
    trusted: bool,
    degraded_reason: Optional[str],
    eligible: Sequence[Mapping[str, object]],
    newly_missing: Sequence[Mapping[str, object]] = (),
    curated_off: Sequence[Mapping[str, object]],
    healthy_count: int,
    total_count: int,
) -> str:
    """Build one account's human-readable report block (pure).

    ``eligible`` / ``curated_off`` are lists of small language-neutral row
    dicts: ``{"name", "area", "cloud_id", "entity_count", "ha_device_id"}``.
    Section titles, the degraded banner, the reason text and the footer are
    localised from ``_STRINGS`` keyed by ``language`` (falling back to English).

    ``newly_missing`` is accepted for forward compatibility with a future
    temporal-confirmation pass; v1 does not render it separately.
    """
    s = _STRINGS.get(language, _STRINGS["en"])
    entities_word = str(s["entities"])
    lines: list[str] = []

    if entry_label:
        lines.append(f"[{entry_label}]")
    lines.append(str(s["lead"]).format(total=total_count))
    lines.append("")

    if trusted:
        lines.append(str(s["eligible_title"]))
        if eligible:
            lines.extend(_format_rows(eligible, entities_word))
        else:
            lines.append(str(s["none"]))
    else:
        lines.append(str(s["degraded_title"]))
        reason_text = s.get(f"reason_{degraded_reason}", degraded_reason or "")
        lines.append(f"  {reason_text}")
    lines.append("")

    lines.append(str(s["curated_title"]))
    if curated_off:
        lines.extend(_format_rows(curated_off, entities_word))
    else:
        lines.append(str(s["none"]))
    lines.append("")

    footer = s["footer"]
    if isinstance(footer, list):
        lines.extend(str(line) for line in footer)

    return "\n".join(lines)
