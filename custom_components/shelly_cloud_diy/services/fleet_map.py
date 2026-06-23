"""Fleet-Map overlay for Shelly Cloud DIY (Stage 1).

A **read-only** overlay that, for every device the cloud account sees,

1. determines whether the same physical Shelly is *also* present locally in
   Home Assistant — matched purely by MAC. Two transports are covered:
     - **Wi-Fi** Shellys (cloud ``device_id`` == the Wi-Fi MAC in lowercase
       hex) → matched against any local device's ``CONNECTION_NETWORK_MAC``
       (typically the native ``shelly`` integration);
     - **Bluetooth/BLU** Shellys (cloud ``device_id`` == ``"XB"`` + the BLE
       MAC encoded as a decimal integer) → decoded back to the BLE MAC and
       matched against any local device's ``CONNECTION_BLUETOOTH`` (typically
       the ``bthome`` integration, which reads Shelly BLU broadcasts);
2. surfaces the cloud-side alias **without mutating local rows** (it only
   *suggests* a name; applying it is a manual, off-by-default, reversible
   action that never touches ``name_by_user``);
3. flags any device whose *control* path runs over the cloud, separating the
   unavoidable case (no local twin) from the operator-fixable case.

Local-first invariant: this module never calls a cloud *control* endpoint
and never sits in the control path. It reads the device/entity registries
plus the coordinator's already-fetched snapshot; the only write it can ever
perform is the cosmetic ``name`` of a matched local device, and only via the
explicit, dry-run-default service.

The pure functions (``_norm_mac``, ``_cloud_mac_key``, ``_index_local_by_mac``,
``match``, ``build_fleet_map``) are the reusable substrate Stage 2 (unified
``replace_device``) and Stage 3 (on-device config clone) build on.
"""
from __future__ import annotations

import hashlib
import logging
import re
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

# A cloud Wi-Fi device_id is the MAC as 12 lowercase hex chars, no separators.
_HEX12 = re.compile(r"^[0-9a-fA-F]{12}$")
# BLE/BLU cloud ids are "XB" + the BLE MAC encoded as a decimal integer.
_BLE_PREFIX = "XB"
_MAX_MAC = 1 << 48  # 48-bit address space


# ── MAC normalisation (single source of truth, used on BOTH sides) ──────


def _norm_mac(value: str) -> str:
    """Canonical comparison key for a MAC (Wi-Fi or BLE).

    ``dr.format_mac`` absorbs colon/case/separator drift; ``aabbccddeeff``
    and ``aa:bb:cc:dd:ee:ff`` both normalise to ``AABBCCDDEEFF``.
    """
    return dr.format_mac(value).replace(":", "").upper()


def _cloud_mac_key(cloud_id: str) -> str | None:
    """Map a cloud ``device_id`` to its normalised MAC key, or ``None``.

    - 12-hex Wi-Fi id → that MAC.
    - ``"XB"`` + decimal → the BLE MAC the decimal encodes. Shelly Cloud
      reports BLU devices with the BLE MAC as a decimal integer behind an
      ``XB`` prefix (e.g. ``XB281474976710655`` → ``FF:FF:FF:FF:FF:FF``),
      which is exactly the address the ``bthome`` / ``bluetooth``
      integrations key the same device by.
    - anything else (a genuinely synthetic id) → ``None`` (never matches).
    """
    if _HEX12.match(cloud_id):
        return _norm_mac(cloud_id)
    if cloud_id.startswith(_BLE_PREFIX):
        digits = cloud_id[len(_BLE_PREFIX):]
        if digits.isdigit():
            value = int(digits)
            if 0 < value < _MAX_MAC:
                return format(value, "012X")
    return None


def _fingerprint(value: str) -> str:
    """Stable, non-reversible short fingerprint of a cloud id's MAC key.

    Both sides of a match fingerprint identically, so a match stays
    verifiable in diagnostics without exposing the real MAC.
    """
    key = _cloud_mac_key(value) or _norm_mac(value)
    return hashlib.sha1(key.encode()).hexdigest()[:8]


# ── Local device indexing (by MAC, any integration) ─────────────────────


def _main_mac(device: dr.DeviceEntry) -> str | None:
    """Return the device's ``CONNECTION_NETWORK_MAC`` (Wi-Fi), if any."""
    for conn_type, conn_value in device.connections:
        if conn_type == dr.CONNECTION_NETWORK_MAC:
            return conn_value
    return None


def _device_ids_with_entities(ent_reg: er.EntityRegistry) -> set[str]:
    """Set of HA device_ids that own at least one entity."""
    return {
        entity.device_id
        for entity in ent_reg.entities.values()
        if entity.device_id is not None
    }


def _index_local_by_mac(
    dev_reg: dr.DeviceRegistry,
    with_entities: set[str],
) -> dict[str, dr.DeviceEntry]:
    """Map ``_norm_mac`` → local ``DeviceEntry`` across all integrations.

    Indexes both Wi-Fi (``CONNECTION_NETWORK_MAC``) and Bluetooth
    (``CONNECTION_BLUETOOTH``) connections, so a Shelly that lives locally as
    a native ``shelly`` device *or* as a ``bthome`` BLU device is found.
    Matching is by exact MAC, so cross-integration false positives are
    impossible (a MAC identifies one physical radio).

    On a MAC collision (e.g. the same BLU device exists both as a configured
    ``bthome`` device *and* as a bare ``bluetooth`` discovery shell with no
    entities — common when stray BLE devices merely advertise themselves),
    the device that actually owns entities wins, so a real integration is
    preferred over a passive discovery record.
    """
    index: dict[str, dr.DeviceEntry] = {}
    for device in dev_reg.devices.values():
        keys = {
            _norm_mac(value)
            for conn_type, value in device.connections
            if conn_type in (dr.CONNECTION_NETWORK_MAC, dr.CONNECTION_BLUETOOTH)
        }
        if not keys:
            continue
        device_has_entities = device.id in with_entities
        for key in keys:
            existing = index.get(key)
            if existing is None:
                index[key] = device
            elif device_has_entities and existing.id not in with_entities:
                index[key] = device  # prefer the integrated device
    return index


def _device_domain(hass: HomeAssistant, device: dr.DeviceEntry) -> str | None:
    """Best-effort owning integration domain of a device entry."""
    entry_id = getattr(device, "primary_config_entry", None)
    candidates = [entry_id] if entry_id else []
    candidates += [e for e in device.config_entries if e != entry_id]
    for candidate in candidates:
        entry = hass.config_entries.async_get_entry(candidate)
        if entry is not None:
            return entry.domain
    return None


def match(cloud_id: str, local_index: dict[str, dr.DeviceEntry]) -> str | None:
    """Return the local HA ``device_id`` for ``cloud_id`` (or ``None``)."""
    key = _cloud_mac_key(cloud_id)
    if key is None:
        return None
    device = local_index.get(key)
    return device.id if device is not None else None


# ── Cloud-device universe (offline-inclusive) ───────────────────────────


@dataclass(frozen=True)
class CloudDevice:
    """One cloud-account device: its (eventually-consistent) name + reachability."""

    name: str | None
    online: bool


def _fold_channel_id(cloud_id: str) -> str:
    """Fold a per-channel sub-entry id into its parent device id.

    The account device list (``/interface/device/list``) contains per-channel
    sub-entries with ids like ``<parentid>_<digits>`` (a channel of a
    multi-channel device, NOT a separate device). Strip a trailing
    ``_<digits>`` suffix to recover the parent id; any other id is returned
    unchanged. A real Wi-Fi MAC (12 hex) and an ``XB<decimal>`` id never
    contain ``_``, so only true channel sub-entries are folded.

    Examples::

        _fold_channel_id("aabbccddeeff_1")     == "aabbccddeeff"
        _fold_channel_id("XB281474976710655_3") == "XB281474976710655"
        _fold_channel_id("aabbccddeeff")       == "aabbccddeeff"
        _fold_channel_id("EMERGENCY-PVE4")     == "EMERGENCY-PVE4"
    """
    m = re.match(r"^(.+)_\d+$", cloud_id)
    return m.group(1) if m else cloud_id


async def gather_cloud_devices(
    coordinator: ShellyCloudCoordinator,
) -> dict[str, CloudDevice]:
    """Return the FULL cloud-device universe ``id → CloudDevice``, offline-inclusive.

    ``coordinator.devices`` comes from ``/device/all_status``, which OMITS
    offline devices. This best-effort augments that set with the full account
    list (``/interface/device/list``), which DOES include offline devices —
    that augmentation is exactly what makes OFFLINE devices visible to the
    fleet map, central to the replace use case (a device you replace is
    dead/offline). Devices added from the full list are, by construction,
    offline (``online=False``); devices already in ``coordinator.devices``
    carry their real reachability flag.

    The augmentation is best-effort: on any failure it degrades silently to
    the all_status set (each with its real online flag) and never raises.
    """
    universe: dict[str, CloudDevice] = {
        cid: CloudDevice(
            name=coordinator.device_names.get(cid),
            online=bool(info.get("online")),
        )
        for cid, info in coordinator.devices.items()
    }
    try:
        names = await coordinator.api.get_device_names()
        for list_id, list_name in names.items():
            parent = _fold_channel_id(list_id)
            if parent in universe:
                # Never overwrite an existing universe entry, and never let a
                # channel child's name become the parent's name.
                continue
            # Only adopt a name if the parent is itself a list entry; a
            # channel child's name (<parent>_N) must not name the parent.
            # Anything new here is absent from all_status → offline.
            universe[parent] = CloudDevice(name=names.get(parent), online=False)
    except Exception:  # noqa: BLE001 - best-effort, must degrade, never raise
        _LOGGER.debug(
            "Fleet-Map: full device list unavailable; "
            "falling back to all_status set",
            exc_info=True,
        )
    return universe


# ── Fleet map ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FleetEntry:
    """One cloud device and its (optional) local/our HA twins."""

    cloud_id: str  # the join source — always present
    cloud_name: str | None  # eventually-consistent; NEVER part of matching
    online: bool  # cloud reachability (offline devices are still listed)
    local_ha_device_id: str | None  # matched local twin (any integration)
    local_domain: str | None  # owning integration of the twin (shelly/bthome/…)
    local_has_entities: bool  # False → a bare discovery shell, not integrated
    match_kind: str  # "wifi" | "ble" | "none"
    our_ha_device_id: str | None  # our own materialised device, if any
    has_cloud_control: bool  # we expose switch/light/cover for it
    has_local_control: bool  # the twin exposes switch/light/cover/climate


@dataclass(frozen=True)
class FleetMap:
    """The full read-only join for one config entry."""

    entries: list[FleetEntry] = field(default_factory=list)
    # Native shelly Wi-Fi devices with no cloud counterpart (id, name).
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


def _native_shelly_devices(
    hass: HomeAssistant, dev_reg: dr.DeviceRegistry
) -> list[dr.DeviceEntry]:
    """All native ``shelly`` main devices (those carrying a Wi-Fi MAC)."""
    native_entry_ids = {
        entry.entry_id
        for entry in hass.config_entries.async_entries(NATIVE_SHELLY_DOMAIN)
    }
    if not native_entry_ids:
        return []
    return [
        device
        for device in dev_reg.devices.values()
        if (device.config_entries & native_entry_ids)
        and _main_mac(device) is not None
    ]


def build_fleet_map(
    hass: HomeAssistant,
    cloud_devices: dict[str, CloudDevice],
    dev_reg: dr.DeviceRegistry,
    ent_reg: er.EntityRegistry,
) -> FleetMap:
    """Compute the cloud↔local join — decoupled from names (MAC only).

    ``cloud_devices`` is the offline-inclusive ``id → CloudDevice`` universe
    (see ``gather_cloud_devices``). This function is PURE: no await, no
    coordinator reference.
    """
    with_entities = _device_ids_with_entities(ent_reg)
    local_index = _index_local_by_mac(dev_reg, with_entities)
    entries: list[FleetEntry] = []
    matched_local_ids: set[str] = set()

    for cloud_id, cloud in cloud_devices.items():
        cloud_name = cloud.name
        key = _cloud_mac_key(cloud_id)
        local_device = local_index.get(key) if key is not None else None
        local_id = local_device.id if local_device is not None else None
        if local_id is not None:
            matched_local_ids.add(local_id)
            match_kind = "ble" if cloud_id.startswith(_BLE_PREFIX) else "wifi"
        else:
            match_kind = "none"

        our_device = dev_reg.async_get_device(identifiers={(DOMAIN, cloud_id)})
        our_id = our_device.id if our_device is not None else None

        entries.append(
            FleetEntry(
                cloud_id=cloud_id,
                cloud_name=cloud_name,
                online=cloud.online,
                local_ha_device_id=local_id,
                local_domain=(
                    _device_domain(hass, local_device)
                    if local_device is not None
                    else None
                ),
                local_has_entities=local_id is not None and local_id in with_entities,
                match_kind=match_kind,
                our_ha_device_id=our_id,
                has_cloud_control=_device_has_control(
                    ent_reg, our_id, CONTROL_DOMAINS
                ),
                has_local_control=_device_has_control(
                    ent_reg, local_id, NATIVE_CONTROL_DOMAINS
                ),
            )
        )

    local_only: list[tuple[str, str | None]] = []
    for device in _native_shelly_devices(hass, dev_reg):
        if device.id not in matched_local_ids:
            local_only.append((device.id, device.name_by_user or device.name))

    return FleetMap(entries=entries, local_only=local_only)


# ── Local name suggestions (compute only; apply is manual) ──────────────


@dataclass(frozen=True)
class NameSuggestion:
    """An advisory rename for a matched local device."""

    local_ha_device_id: str
    cloud_id: str
    current_name: str | None
    suggested_name: str


def suggest_native_names(
    fleet: FleetMap,
    dev_reg: dr.DeviceRegistry,
) -> list[NameSuggestion]:
    """Compute advisory renames for matched local devices. Writes nothing."""
    suggestions: list[NameSuggestion] = []
    for entry in fleet.entries:
        if entry.local_ha_device_id is None or not entry.cloud_name:
            continue
        local = dev_reg.async_get(entry.local_ha_device_id)
        # Never touch a device the user has renamed themselves.
        if local is None or local.name_by_user is not None:
            continue
        if local.name == entry.cloud_name:
            continue
        suggestions.append(
            NameSuggestion(
                local_ha_device_id=entry.local_ha_device_id,
                cloud_id=entry.cloud_id,
                current_name=local.name,
                suggested_name=entry.cloud_name,
            )
        )
    return suggestions


def apply_native_name_suggestions(
    dev_reg: dr.DeviceRegistry,
    suggestions: list[NameSuggestion],
) -> int:
    """Apply suggestions by writing local ``name`` (never ``name_by_user``).

    The sole local-write path. Operator-initiated, reversible, idempotent
    (a device with ``name_by_user`` set is always skipped).
    """
    applied = 0
    for suggestion in suggestions:
        local = dev_reg.async_get(suggestion.local_ha_device_id)
        if local is None or local.name_by_user is not None:
            continue
        if local.name == suggestion.suggested_name:
            continue
        dev_reg.async_update_device(
            suggestion.local_ha_device_id, name=suggestion.suggested_name
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
                entry.local_ha_device_id is not None
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


def _label_status(entry: FleetEntry) -> str:
    """Device label with a trailing ``[OFFLINE]`` marker when unreachable."""
    base = _label(entry)
    return base if entry.online else f"{base} [OFFLINE]"


def format_report(
    fleet: FleetMap,
    suggestions: list[NameSuggestion],
    resilience: ResilienceReport,
    *,
    applied_names: int | None,
) -> str:
    """Human-readable report for the persistent notification."""
    matched_wifi = [
        e for e in fleet.entries
        if e.local_ha_device_id is not None and e.match_kind == "wifi"
    ]
    matched_ble = [
        e for e in fleet.entries
        if e.local_ha_device_id is not None and e.match_kind == "ble"
    ]
    cloud_only_devices = [
        e for e in fleet.entries if e.local_ha_device_id is None
    ]

    offline_count = sum(1 for e in fleet.entries if e.online is False)

    lines: list[str] = []
    lines.append(
        f"Cloud devices: {len(fleet.entries)}  |  matched locally: "
        f"{len(matched_wifi) + len(matched_ble)} "
        f"(Wi-Fi {len(matched_wifi)} + Bluetooth {len(matched_ble)})  |  "
        f"cloud-only: {len(cloud_only_devices)}  |  offline: {offline_count}  |  "
        f"local-only Shelly: {len(fleet.local_only)}"
    )
    lines.append("")

    lines.append("Matched via Wi-Fi (same physical Shelly, local control path):")
    if matched_wifi:
        for e in matched_wifi:
            lines.append(
                f"  • {_label_status(e)} — local {e.local_domain or '?'} device "
                f"{e.local_ha_device_id}"
            )
        lines.append(
            "    ⚠ Same hardware as your local device card. Do NOT delete the "
            "local card — that is your fast, offline-resilient control path."
        )
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append(
        "Matched via Bluetooth/BLU (local sensor twin, e.g. bthome):"
    )
    if matched_ble:
        for e in matched_ble:
            note = (
                "" if e.local_has_entities
                else "  [discovered only — not actually integrated]"
            )
            lines.append(
                f"  • {_label_status(e)} — local {e.local_domain or '?'} device "
                f"{e.local_ha_device_id}{note}"
            )
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("Cloud-only (no local twin — shared/remote, or not set up):")
    for e in cloud_only_devices:
        lines.append(f"  • {_label_status(e)}")
    if not cloud_only_devices:
        lines.append("  (none)")
    lines.append("")

    lines.append("Local-only (native Shelly device with no cloud counterpart):")
    for _id, name in fleet.local_only:
        lines.append(f"  • {name or _id}")
    if not fleet.local_only:
        lines.append("  (none)")
    lines.append("")

    lines.append("Suggested local names (cloud alias → local device):")
    if suggestions:
        for s in suggestions:
            lines.append(
                f"  • {s.current_name or s.local_ha_device_id} → "
                f"'{s.suggested_name}'"
            )
        if applied_names is None:
            lines.append(
                "    (advisory only — re-run with apply_native_name_suggestions: "
                "true, dry_run: false to apply; the local 'name' may later be "
                "reset by its integration, set name_by_user to make it stick.)"
            )
        else:
            lines.append(f"    Applied {applied_names} local name(s).")
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
            "matched_wifi": sum(
                1 for e in fleet.entries if e.match_kind == "wifi"
            ),
            "matched_ble": sum(
                1 for e in fleet.entries if e.match_kind == "ble"
            ),
            "cloud_only": sum(
                1 for e in fleet.entries if e.local_ha_device_id is None
            ),
            "offline": sum(1 for e in fleet.entries if e.online is False),
            "local_only": len(fleet.local_only),
            "automation_scan_ok": resilience.automation_scan_ok,
        },
        "entries": [
            {
                "fingerprint": _fingerprint(e.cloud_id),
                "cloud_name": e.cloud_name,
                "online": e.online,
                "match_kind": e.match_kind,
                "local_domain": e.local_domain,
                "local_has_entities": e.local_has_entities,
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
    cloud_devices: dict[str, CloudDevice],
    dev_reg: dr.DeviceRegistry,
    ent_reg: er.EntityRegistry,
) -> tuple[FleetMap, list[NameSuggestion], ResilienceReport]:
    """Run the full read-only computation for one cloud-device universe."""
    fleet = build_fleet_map(hass, cloud_devices, dev_reg, ent_reg)
    suggestions = suggest_native_names(fleet, dev_reg)
    resilience = scan_resilience(hass, fleet, ent_reg)
    return fleet, suggestions, resilience


async def async_handle_fleet_map(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle the ``shelly_cloud_diy.fleet_map`` service call.

    Read-only by default. With ``apply_native_name_suggestions: true`` and
    ``dry_run: false`` it performs the single, manual, reversible local
    ``name`` write for matched devices.
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
        cloud_devices = await gather_cloud_devices(coordinator)
        fleet, suggestions, resilience = compute_fleet(
            hass, cloud_devices, dev_reg, ent_reg
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
