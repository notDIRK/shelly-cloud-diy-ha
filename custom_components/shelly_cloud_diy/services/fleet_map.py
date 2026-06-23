"""Fleet-Map overlay for Shelly Cloud DIY (Stage 1).

A **read-only** overlay that, for every device the cloud account sees,

1. determines whether the same physical Shelly is *also* present via Home
   Assistant Core's native (local/LAN) ``shelly`` integration — a trivial
   MAC join (``dr.format_mac(cloud_id) == dr.format_mac(native_mac)``);
2. surfaces the cloud-side alias **without mutating native rows** (it only
   *suggests* a name for native devices; applying it is a manual,
   off-by-default, reversible action);
3. flags any device whose *control* path runs over the cloud, separating the
   unavoidable case (no local twin — e.g. a shared/remote Shelly) from the
   operator-fixable case (a local twin exists but an automation drives the
   cloud entity).

Local-first invariant: this module never calls a cloud *control* endpoint
and never sits in the control path. It reads the device/entity registries
plus the coordinator's already-fetched snapshot, and the **only** write it
can ever perform is the cosmetic ``name`` of a *native* device — and that
only via the explicit, dry-run-default ``fleet_map`` service, never on the
poll hot path, never ``name_by_user``.

The pure functions (``_norm_mac``, ``_index_native_shelly``, ``match``,
``build_fleet_map``) are the reusable substrate Stage 2 (unified
``replace_device``) and Stage 3 (on-device config clone) build on.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from homeassistant.components import persistent_notification
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import (
    CONTROL_DOMAINS,
    DOMAIN,
    NATIVE_CONTROL_DOMAINS,
    NATIVE_SHELLY_DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall

    from ..coordinator import ShellyCloudCoordinator

_LOGGER = logging.getLogger(__name__)


# ── MAC normalisation (single source of truth, used on BOTH sides) ──────


def _norm_mac(value: str) -> str:
    """Canonical comparison key for a cloud ``device_id`` or a native MAC.

    ``dr.format_mac`` absorbs colon/case/separator drift; cloud ids
    (``aabbccddeeff``) and native ``CONNECTION_NETWORK_MAC``
    (``aa:bb:cc:dd:ee:ff``) both normalise to ``AABBCCDDEEFF``. A synthetic,
    non-MAC cloud id (e.g. a BLE-gateway ``XB…``) passes through unchanged
    and simply never matches a real MAC.
    """
    return dr.format_mac(value).replace(":", "").upper()


def _fingerprint(value: str) -> str:
    """Stable, non-reversible short fingerprint of a normalised MAC/id.

    Both sides of a match fingerprint identically (they normalise to the
    same value), so a match stays verifiable in diagnostics without
    exposing the real MAC.
    """
    return hashlib.sha1(_norm_mac(value).encode()).hexdigest()[:8]


# ── Native-shelly indexing ──────────────────────────────────────────────


def _main_mac(device: dr.DeviceEntry) -> str | None:
    """Return the device's ``CONNECTION_NETWORK_MAC``, if any."""
    for conn_type, conn_value in device.connections:
        if conn_type == dr.CONNECTION_NETWORK_MAC:
            return conn_value
    return None


def _mac_from_identifiers(device: dr.DeviceEntry) -> str | None:
    """Fallback: recover a parent MAC from a native sub-device identifier.

    Native sub-devices are keyed ``("shelly", "{mac}-{key}")``. Used only
    when a device has no MAC connection (rare — the main device always
    carries one), wrapped by the caller in try/except.
    """
    for ident_domain, ident in device.identifiers:
        if ident_domain == NATIVE_SHELLY_DOMAIN and "-" in ident:
            return ident.split("-", 1)[0]
    return None


def _index_native_shelly(
    hass: HomeAssistant, dev_reg: dr.DeviceRegistry
) -> dict[str, dr.DeviceEntry]:
    """Map ``_norm_mac`` → native ``shelly`` **main** ``DeviceEntry``.

    Two passes so a main device (which carries ``CONNECTION_NETWORK_MAC``)
    always wins over its sub-devices (which only carry the identifier
    fallback) for a given MAC key.
    """
    native_entry_ids = {
        entry.entry_id
        for entry in hass.config_entries.async_entries(NATIVE_SHELLY_DOMAIN)
    }
    index: dict[str, dr.DeviceEntry] = {}
    if not native_entry_ids:
        return index

    native_devices = [
        device
        for device in dev_reg.devices.values()
        if device.config_entries & native_entry_ids
    ]

    # Pass 1: devices with a real MAC connection (the main devices).
    for device in native_devices:
        mac = _main_mac(device)
        if mac:
            index.setdefault(_norm_mac(mac), device)

    # Pass 2: identifier fallback, only for MACs not already indexed.
    for device in native_devices:
        if _main_mac(device) is not None:
            continue
        try:
            mac = _mac_from_identifiers(device)
        except (ValueError, AttributeError):  # pragma: no cover - defensive
            mac = None
        if mac:
            index.setdefault(_norm_mac(mac), device)

    _LOGGER.debug("Fleet-Map: indexed %d native shelly device(s)", len(index))
    return index


def match(cloud_id: str, native_index: dict[str, dr.DeviceEntry]) -> str | None:
    """Return the native HA ``device_id`` for ``cloud_id`` (or ``None``)."""
    device = native_index.get(_norm_mac(cloud_id))
    return device.id if device is not None else None


# ── Fleet map ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FleetEntry:
    """One cloud device and its (optional) local/our HA twins."""

    cloud_id: str  # lowercase hex; the join key — always present
    cloud_name: str | None  # eventually-consistent; NEVER part of matching
    native_ha_device_id: str | None
    our_ha_device_id: str | None
    has_cloud_control: bool  # we expose switch/light/cover for it
    has_local_control: bool  # native exposes switch/light/cover/climate


@dataclass(frozen=True)
class FleetMap:
    """The full read-only join for one config entry."""

    entries: list[FleetEntry] = field(default_factory=list)
    # Native main devices with no cloud counterpart (id, display name).
    local_only: list[tuple[str, str | None]] = field(default_factory=list)


def _device_has_control(
    ent_reg: er.EntityRegistry,
    ha_device_id: str | None,
    domains: frozenset[str],
) -> bool:
    """True if the HA device exposes ≥1 entity in ``domains``."""
    if ha_device_id is None:
        return False
    for ent in er.async_entries_for_device(
        ent_reg, ha_device_id, include_disabled_entities=True
    ):
        if ent.entity_id.split(".", 1)[0] in domains:
            return True
    return False


def build_fleet_map(
    hass: HomeAssistant,
    coordinator: ShellyCloudCoordinator,
    dev_reg: dr.DeviceRegistry,
    ent_reg: er.EntityRegistry,
) -> FleetMap:
    """Compute the cloud↔local join — decoupled from names (MAC only)."""
    native_index = _index_native_shelly(hass, dev_reg)
    entries: list[FleetEntry] = []
    matched_native_ids: set[str] = set()

    for cloud_id in coordinator.devices:
        native_id = match(cloud_id, native_index)
        if native_id is not None:
            matched_native_ids.add(native_id)

        our_device = dev_reg.async_get_device(identifiers={(DOMAIN, cloud_id)})
        our_id = our_device.id if our_device is not None else None

        entries.append(
            FleetEntry(
                cloud_id=cloud_id,
                # report-only, eventually-consistent — never a match input
                cloud_name=coordinator.device_names.get(cloud_id),
                native_ha_device_id=native_id,
                our_ha_device_id=our_id,
                has_cloud_control=_device_has_control(
                    ent_reg, our_id, CONTROL_DOMAINS
                ),
                has_local_control=_device_has_control(
                    ent_reg, native_id, NATIVE_CONTROL_DOMAINS
                ),
            )
        )

    local_only: list[tuple[str, str | None]] = []
    for device in native_index.values():
        if device.id not in matched_native_ids:
            local_only.append((device.id, device.name_by_user or device.name))

    return FleetMap(entries=entries, local_only=local_only)


# ── Native name suggestions (compute only; apply is manual) ─────────────


@dataclass(frozen=True)
class NameSuggestion:
    """An advisory rename for a matched native device."""

    native_ha_device_id: str
    cloud_id: str
    current_name: str | None
    suggested_name: str


def suggest_native_names(
    fleet: FleetMap,
    dev_reg: dr.DeviceRegistry,
) -> list[NameSuggestion]:
    """Compute advisory native renames. Writes nothing."""
    suggestions: list[NameSuggestion] = []
    for entry in fleet.entries:
        if entry.native_ha_device_id is None or not entry.cloud_name:
            continue
        native = dev_reg.async_get(entry.native_ha_device_id)
        # Never touch a device the user has renamed themselves.
        if native is None or native.name_by_user is not None:
            continue
        if native.name == entry.cloud_name:
            continue
        suggestions.append(
            NameSuggestion(
                native_ha_device_id=entry.native_ha_device_id,
                cloud_id=entry.cloud_id,
                current_name=native.name,
                suggested_name=entry.cloud_name,
            )
        )
    return suggestions


def apply_native_name_suggestions(
    dev_reg: dr.DeviceRegistry,
    suggestions: list[NameSuggestion],
) -> int:
    """Apply suggestions by writing native ``name`` (never ``name_by_user``).

    The sole native-write path. Operator-initiated, reversible, idempotent
    (a device with ``name_by_user`` set is always skipped).
    """
    applied = 0
    for suggestion in suggestions:
        native = dev_reg.async_get(suggestion.native_ha_device_id)
        if native is None or native.name_by_user is not None:
            continue
        if native.name == suggestion.suggested_name:
            continue
        dev_reg.async_update_device(
            suggestion.native_ha_device_id, name=suggestion.suggested_name
        )
        applied += 1
    return applied


# ── Resilience check ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResilienceHit:
    """A matched device whose cloud entity is used despite a local twin."""

    cloud_id: str
    cloud_entity_ids: list[str]


@dataclass(frozen=True)
class ResilienceReport:
    """Two resilience categories plus whether the automation scan ran."""

    # Authoritative: cloud control, no local twin (may be unavoidable).
    cloud_only_control: list[str]  # cloud_ids
    # Actionable, best-effort: local twin exists, automation uses cloud.
    cloud_control_despite_local_twin: list[ResilienceHit]
    automation_scan_ok: bool


def _cloud_control_entity_ids(
    ent_reg: er.EntityRegistry, our_ha_device_id: str
) -> list[str]:
    """Our control entity_ids (switch/light/cover) for a device."""
    return [
        ent.entity_id
        for ent in er.async_entries_for_device(
            ent_reg, our_ha_device_id, include_disabled_entities=True
        )
        if ent.entity_id.split(".", 1)[0] in CONTROL_DOMAINS
    ]


def _referenced_entity_ids(hass: HomeAssistant) -> set[str] | None:
    """Best-effort set of entity_ids referenced by automations/scripts.

    There is no stable public API for this, so it is wrapped defensively
    and returns ``None`` (→ scan unavailable) on any failure. Never
    authoritative.
    """
    refs: set[str] = set()
    try:
        for component_key in ("automation", "script"):
            component = hass.data.get(component_key)
            entities = getattr(component, "entities", None)
            if entities is None:
                continue
            for entity in entities:
                referenced = getattr(entity, "referenced_entities", None)
                if referenced:
                    refs |= set(referenced)
    except Exception:  # noqa: BLE001 - best-effort, must never raise
        _LOGGER.debug("Fleet-Map: automation/script scan unavailable", exc_info=True)
        return None
    return refs


def scan_resilience(
    hass: HomeAssistant,
    fleet: FleetMap,
    ent_reg: er.EntityRegistry,
) -> ResilienceReport:
    """Classify control-path resilience risks."""
    cloud_only: list[str] = []
    for entry in fleet.entries:
        # Category 1 — controllable via cloud with no local twin control.
        if entry.has_cloud_control and not entry.has_local_control:
            cloud_only.append(entry.cloud_id)

    referenced = _referenced_entity_ids(hass)
    scan_ok = referenced is not None
    despite_twin: list[ResilienceHit] = []
    if scan_ok:
        for entry in fleet.entries:
            # Category 2 — a local twin exists AND both sides control AND an
            # automation/script points at our cloud control entity.
            if not (
                entry.native_ha_device_id is not None
                and entry.has_cloud_control
                and entry.has_local_control
                and entry.our_ha_device_id is not None
            ):
                continue
            cloud_entities = _cloud_control_entity_ids(
                ent_reg, entry.our_ha_device_id
            )
            hits = [eid for eid in cloud_entities if eid in referenced]
            if hits:
                despite_twin.append(
                    ResilienceHit(cloud_id=entry.cloud_id, cloud_entity_ids=hits)
                )

    return ResilienceReport(
        cloud_only_control=cloud_only,
        cloud_control_despite_local_twin=despite_twin,
        automation_scan_ok=scan_ok,
    )


# ── Reporting ───────────────────────────────────────────────────────────


def _label(entry: FleetEntry) -> str:
    return entry.cloud_name or entry.cloud_id


def format_report(
    fleet: FleetMap,
    suggestions: list[NameSuggestion],
    resilience: ResilienceReport,
    *,
    applied_names: int | None,
) -> str:
    """Human-readable report for the persistent notification."""
    matched = [e for e in fleet.entries if e.native_ha_device_id is not None]
    cloud_only_devices = [
        e for e in fleet.entries if e.native_ha_device_id is None
    ]

    lines: list[str] = []
    lines.append(
        f"Cloud devices: {len(fleet.entries)}  |  matched to a local "
        f"(native shelly) device: {len(matched)}  |  cloud-only: "
        f"{len(cloud_only_devices)}  |  local-only: {len(fleet.local_only)}"
    )
    lines.append("")

    lines.append("Matched (same physical Shelly on both integrations):")
    if matched:
        for e in matched:
            lines.append(
                f"  • {_label(e)} — local device {e.native_ha_device_id} "
                f"⇄ cloud overlay {e.our_ha_device_id or '(no entities)'}"
            )
        lines.append(
            "    ⚠ These pairs are the SAME hardware. Do NOT delete the local "
            "device card — that is your fast, offline-resilient control path."
        )
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("Cloud-only (no local twin — shared/remote, or local not set up):")
    for e in cloud_only_devices:
        lines.append(f"  • {_label(e)}")
    if not cloud_only_devices:
        lines.append("  (none)")
    lines.append("")

    lines.append("Local-only (native shelly device with no cloud counterpart):")
    for _id, name in fleet.local_only:
        lines.append(f"  • {name or _id}")
    if not fleet.local_only:
        lines.append("  (none)")
    lines.append("")

    lines.append("Suggested native names (cloud alias → local device):")
    if suggestions:
        for s in suggestions:
            lines.append(
                f"  • {s.current_name or s.native_ha_device_id} → "
                f"'{s.suggested_name}'"
            )
        if applied_names is None:
            lines.append(
                "    (advisory only — re-run with apply_native_name_suggestions: "
                "true, dry_run: false to apply; native 'name' may later be reset "
                "by the native integration, set name_by_user to make it stick.)"
            )
        else:
            lines.append(f"    Applied {applied_names} native name(s).")
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("Resilience — control-path risks:")
    lines.append(
        "  Cloud-only control (may be unavoidable for shared/remote devices):"
    )
    if resilience.cloud_only_control:
        for cid in resilience.cloud_only_control:
            label = next(
                (_label(e) for e in fleet.entries if e.cloud_id == cid), cid
            )
            lines.append(f"    • {label}")
    else:
        lines.append("    (none)")
    lines.append(
        "  Cloud control used despite a local twin (ACTIONABLE — point the "
        "automation at the local entity):"
    )
    if not resilience.automation_scan_ok:
        lines.append(
            "    (automation/script scan unavailable on this HA build — "
            "best-effort, skipped)"
        )
    elif resilience.cloud_control_despite_local_twin:
        for hit in resilience.cloud_control_despite_local_twin:
            label = next(
                (_label(e) for e in fleet.entries if e.cloud_id == hit.cloud_id),
                hit.cloud_id,
            )
            lines.append(f"    • {label}: {', '.join(hit.cloud_entity_ids)}")
    else:
        lines.append("    (none found — best-effort scan, may be incomplete)")

    return "\n".join(lines)


def to_diagnostics(
    fleet: FleetMap,
    suggestions: list[NameSuggestion],
    resilience: ResilienceReport,
) -> dict:
    """Machine-readable Fleet-Map table for diagnostics (MACs fingerprinted)."""
    suggested_for = {s.cloud_id: s.suggested_name for s in suggestions}
    cloud_only_set = set(resilience.cloud_only_control)
    despite_twin_set = {
        hit.cloud_id for hit in resilience.cloud_control_despite_local_twin
    }
    return {
        "summary": {
            "cloud_devices": len(fleet.entries),
            "matched": sum(
                1 for e in fleet.entries if e.native_ha_device_id is not None
            ),
            "cloud_only": sum(
                1 for e in fleet.entries if e.native_ha_device_id is None
            ),
            "local_only": len(fleet.local_only),
            "automation_scan_ok": resilience.automation_scan_ok,
        },
        "entries": [
            {
                "fingerprint": _fingerprint(e.cloud_id),
                "cloud_name": e.cloud_name,
                "matched_native": e.native_ha_device_id is not None,
                "has_our_entities": e.our_ha_device_id is not None,
                "has_cloud_control": e.has_cloud_control,
                "has_local_control": e.has_local_control,
                "cloud_only_control": e.cloud_id in cloud_only_set,
                "cloud_control_despite_local_twin": e.cloud_id in despite_twin_set,
                "name_suggestion": suggested_for.get(e.cloud_id),
            }
            for e in fleet.entries
        ],
    }


# ── Service handler ─────────────────────────────────────────────────────


def compute_fleet(
    hass: HomeAssistant,
    coordinator: ShellyCloudCoordinator,
    dev_reg: dr.DeviceRegistry,
    ent_reg: er.EntityRegistry,
) -> tuple[FleetMap, list[NameSuggestion], ResilienceReport]:
    """Run the full read-only computation for one coordinator."""
    fleet = build_fleet_map(hass, coordinator, dev_reg, ent_reg)
    suggestions = suggest_native_names(fleet, dev_reg)
    resilience = scan_resilience(hass, fleet, ent_reg)
    return fleet, suggestions, resilience


async def async_handle_fleet_map(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle the ``shelly_cloud_diy.fleet_map`` service call.

    Read-only by default. With ``apply_native_name_suggestions: true`` and
    ``dry_run: false`` it performs the single, manual, reversible native
    ``name`` write for matched main devices.
    """
    dry_run: bool = bool(call.data.get("dry_run", True))
    apply_names: bool = bool(call.data.get("apply_native_name_suggestions", False))

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    entries = hass.config_entries.async_entries(DOMAIN)
    coordinators: list[ShellyCloudCoordinator] = []
    for entry in entries:
        coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if coordinator is not None and getattr(
            coordinator, "last_update_success", False
        ):
            coordinators.append(coordinator)

    if not coordinators:
        raise ServiceValidationError(
            "No Shelly Cloud DIY account is loaded yet — wait for the first "
            "successful poll and try again."
        )

    reports: list[str] = []
    total_applied = 0
    for coordinator in coordinators:
        fleet, suggestions, resilience = compute_fleet(
            hass, coordinator, dev_reg, ent_reg
        )

        applied: int | None = None
        if apply_names and not dry_run:
            applied = apply_native_name_suggestions(dev_reg, suggestions)
            total_applied += applied

        reports.append(
            format_report(
                fleet, suggestions, resilience, applied_names=applied
            )
        )

    report_text = "\n\n".join(reports)
    _LOGGER.info("Shelly Cloud DIY fleet_map:\n%s", report_text)

    if dry_run or not apply_names:
        title = "Shelly Cloud DIY — Fleet map"
        notification_id = f"{DOMAIN}_fleet_map"
    else:
        title = f"Shelly Cloud DIY — Fleet map ({total_applied} name(s) applied)"
        notification_id = f"{DOMAIN}_fleet_map_applied"

    persistent_notification.async_create(
        hass,
        f"```\n{report_text}\n```",
        title=title,
        notification_id=notification_id,
    )
