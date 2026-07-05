"""Detect (and optionally remove) Home Assistant devices this integration
materialised whose hardware is no longer in the Shelly account.

A device can leave a Shelly account for good reasons — it was sold, factory
reset, or deleted in the Shelly app. When that happens the Home Assistant
device this integration created lingers as a dead card. This service reconciles
*our* HA devices against the account's alias-independent inventory and reports
the ones that are genuinely gone. It is **inform-only by default**: it writes a
report to notifications and changes nothing until the user explicitly opts into
removal (Dry run off + Remove on), which is previewed first and is honestly
disclosed as not automatically reversible.

All of the risky judgement lives in the pure, HA-free ``orphans_core`` module
(membership, inventory-trust, mass-absence guard, consent binding); this handler
only marshals Home Assistant registry/coordinator state into those functions and
applies their verdicts. The safety-critical invariant — decide absence from the
authoritative account inventory, never from an alias/name map — is enforced by
``ShellyCloudControl.get_account_inventory`` + ``orphans_core.classify``.

Scope firewall (hard): only devices carrying ``(DOMAIN, cloud_id)`` are ever
inspected or touched; load state / ``ConfigEntryState`` is never a discriminator
(a not-loaded account's hardware is still in-account); disabled devices are
skipped; there is no cross-integration registry sweep.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..api.cloud_control import AccountInventory, ShellyCloudError
from ..const import (
    CONF_ENABLED_DEVICES,
    DOMAIN,
    ORPHAN_FLOOR_ABS,
    ORPHAN_FLOOR_FRAC,
)
from ..services.replace_device import _shelly_id
from .orphans_core import (
    DeviceView,
    assess_trust,
    build_report,
    decide_entry,
    fold_channel_id,
    select_actionable,
    sibling_inventory_union,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall

    from ..coordinator import ShellyCloudCoordinator

_LOGGER = logging.getLogger(__name__)

# Human-readable skip reasons for the removal-summary notification. Kept short
# and free of the word "orphan" (user-visible).
_SKIP_REASONS = {
    "account_check_unavailable": (
        "account check was not conclusive — nothing removed for this account"
    ),
    "device_gone": "the device was already removed",
    "disabled": "the device is disabled",
    "reappeared": "the device re-appeared in the account",
}


def _row(view: DeviceView) -> dict[str, Any]:
    """Distil a DeviceView into a language-neutral report row dict."""
    return {
        "name": view.name,
        "area": view.area,
        "cloud_id": view.cloud_id,
        "entity_count": view.entity_count,
        "ha_device_id": view.ha_device_id,
    }


async def async_handle_detect_orphans(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle the ``shelly_cloud_diy.detect_orphans`` service call."""
    dry_run: bool = bool(call.data.get("dry_run", True))
    remove: bool = bool(call.data.get("remove", False))
    user_targets: list[str] | None = call.data.get("devices")

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    area_reg = ar.async_get(hass)

    # ── STEP 0 — require at least one loaded, freshly-polled account ────────
    loaded: list[tuple[ConfigEntry, ShellyCloudCoordinator]] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if coordinator is not None and getattr(
            coordinator, "last_update_success", False
        ):
            loaded.append((entry, coordinator))

    if not loaded:
        raise ServiceValidationError(
            "No Shelly Cloud DIY account is loaded yet — wait for the first "
            "successful poll and try again."
        )

    def _area_name(area_id: str | None) -> str | None:
        if not area_id:
            return None
        area = area_reg.async_get_area(area_id)
        return area.name if area is not None else area_id

    # ── Per-account: fresh authoritative inventory + our device views ───────
    per_entry: list[dict[str, Any]] = []
    for entry, coord in loaded:
        try:
            inv = await coord.api.get_account_inventory()
        except ShellyCloudError as err:
            # Degrade gracefully: an unreadable inventory must never be treated
            # as "everything is gone". Synthesize a not-ok inventory so
            # assess_trust returns api_not_ok and nothing becomes eligible.
            _LOGGER.warning(
                "Shelly Cloud DIY detect_orphans: inventory fetch failed for "
                "%s (%s) — reporting this account as degraded",
                entry.title,
                err,
            )
            inv = AccountInventory(
                ids=frozenset(), raw_count=0, isok=False, well_formed=False
            )

        inv_folded = frozenset(fold_channel_id(i) for i in inv.ids)
        online_folded = frozenset(
            fold_channel_id(cid) for cid in coord.devices
        )

        views: list[DeviceView] = []
        for device in dev_reg.devices.values():
            shelly_id = _shelly_id(device)
            if shelly_id is None:
                continue
            if entry.entry_id not in device.config_entries:
                continue
            if device.disabled_by is not None:
                continue
            entity_count = len(
                er.async_entries_for_device(
                    ent_reg, device.id, include_disabled_entities=True
                )
            )
            views.append(
                DeviceView(
                    ha_device_id=device.id,
                    cloud_id=fold_channel_id(shelly_id),
                    name=device.name_by_user or device.name or shelly_id,
                    area=_area_name(device.area_id),
                    entity_count=entity_count,
                )
            )

        our_ha_count = len(views)
        # Compare FOLDED inventory size against our FOLDED HA parent-device
        # count. ``inv.raw_count`` counts unfolded per-channel sub-entries and
        # would be inflated for any multi-channel fleet, neutering the
        # "inventory smaller than HA" guard; folding both sides keeps them in
        # the same unit so a truncated inventory that drops a real device still
        # trips the guard.
        trusted, reason = assess_trust(
            inv_folded,
            inv.isok,
            inv.well_formed,
            len(inv_folded),
            online_folded,
            our_ha_count,
        )

        # Curated list (folded), or None for create-all mode.
        if coord.create_all_initially:
            enabled_ids: frozenset[str] | None = None
        else:
            raw = entry.options.get(CONF_ENABLED_DEVICES)
            enabled_ids = (
                frozenset(
                    fold_channel_id(x) for x in raw if isinstance(x, str)
                )
                if isinstance(raw, list)
                else None
            )

        per_entry.append(
            {
                "entry": entry,
                "inv_folded": inv_folded,
                "views": views,
                "trusted": trusted,  # assess_trust verdict (used for siblings)
                "reason": reason,
                "enabled_ids": enabled_ids,
            }
        )

    # ── Sibling inventories (only trusted siblings count) ───────────────────
    trust_inv_pairs = [(pe["trusted"], pe["inv_folded"]) for pe in per_entry]
    for i, pe in enumerate(per_entry):
        pe["sibling_inv_ids"] = sibling_inventory_union(trust_inv_pairs, i)

    # ── Classify + mass-absence guard + build reports ───────────────────────
    reports: list[str] = []
    for pe in per_entry:
        cls, trusted_final, reason_final, eligible = decide_entry(
            pe["views"],
            pe["inv_folded"],
            pe["enabled_ids"],
            pe["sibling_inv_ids"],
            pe["trusted"],
            pe["reason"],
            floor_abs=ORPHAN_FLOOR_ABS,
            floor_frac=ORPHAN_FLOOR_FRAC,
        )
        pe["classification"] = cls
        pe["trusted_final"] = trusted_final
        pe["reason_final"] = reason_final
        pe["eligible"] = eligible

        reports.append(
            build_report(
                language=hass.config.language,
                entry_label=pe["entry"].title,
                trusted=trusted_final,
                degraded_reason=reason_final,
                eligible=[_row(v) for v in eligible],
                curated_off=[_row(v) for v in cls.curated_off],
                healthy_count=len(cls.healthy),
                total_count=len(pe["views"]),
            )
        )

    report_text = "\n\n".join(reports)
    _LOGGER.info("Shelly Cloud DIY detect_orphans:\n%s", report_text)

    persistent_notification.async_create(
        hass,
        f"```\n{report_text}\n```",
        title="Shelly Cloud DIY — devices no longer in your account",
        notification_id=f"{DOMAIN}_orphans",
    )

    if not (remove and not dry_run):
        return

    # ── Removal safety gate: every account must have been checked ───────────
    # The sibling-rescue in classify() can only vouch for a device via accounts
    # that were actually polled this run. If ANY non-disabled account was
    # dropped from ``loaded`` (coordinator down, auth blip, SETUP_RETRY, first
    # poll not yet succeeded), the sibling union is partial: a device still
    # reachable via that missing account could be misclassified as a candidate
    # and wrongly deleted. Reporting tolerates a partial view; deletion must
    # not. Refuse the whole removal run until every account can be checked.
    active_entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.disabled_by is None
    ]
    if len(loaded) < len(active_entries):
        _LOGGER.warning(
            "Shelly Cloud DIY detect_orphans: removal refused — %d of %d "
            "active account(s) could not be checked this run; the account "
            "cross-check would be incomplete",
            len(active_entries) - len(loaded),
            len(active_entries),
        )
        persistent_notification.async_create(
            hass,
            (
                "```\n"
                "Nothing was removed.\n\n"
                "Not all of your Shelly Cloud DIY accounts could be checked "
                "right now, so the cross-account safety check would be "
                "incomplete. A device that is still present via another "
                "account could be misjudged as gone.\n\n"
                "Wait until every account has polled successfully, then run "
                "the service again with Dry run off and Remove on.\n"
                "```"
            ),
            title="Shelly Cloud DIY — removal skipped",
            notification_id=f"{DOMAIN}_orphans_removed",
        )
        return

    # ── Removal (opt-in): detach eligible devices from trusted accounts ─────
    user_target_folded: frozenset[str] | None = None
    if user_targets:
        targets: set[str] = set()
        for ha_id in user_targets:
            device = dev_reg.async_get(ha_id)
            if device is None:
                continue
            shelly_id = _shelly_id(device)
            if shelly_id is not None:
                targets.add(fold_channel_id(shelly_id))
        user_target_folded = frozenset(targets)

    removed: list[DeviceView] = []
    skipped: list[tuple[DeviceView, str]] = []

    for pe in per_entry:
        entry = pe["entry"]
        # Degraded / untrusted accounts remove NOTHING and say so.
        if not pe["trusted_final"]:
            for view in pe["classification"].candidates:
                skipped.append((view, "account_check_unavailable"))
            continue

        by_cloud = {v.cloud_id: v for v in pe["eligible"]}
        actionable = select_actionable(
            frozenset(by_cloud), user_target_folded
        )
        inv_folded = pe["inv_folded"]
        stripped: set[str] = set()

        for cloud_id in actionable:
            view = by_cloud.get(cloud_id)
            if view is None:
                continue
            # Re-assert everything immediately before acting.
            device = dev_reg.async_get(view.ha_device_id)
            if device is None:
                skipped.append((view, "device_gone"))
                continue
            if device.disabled_by is not None:
                skipped.append((view, "disabled"))
                continue
            shelly_id = _shelly_id(device)
            if shelly_id is None or fold_channel_id(shelly_id) in inv_folded:
                skipped.append((view, "reappeared"))
                continue

            # 1. Remove the device's live entities.
            for ent in er.async_entries_for_device(
                ent_reg, device.id, include_disabled_entities=True
            ):
                ent_reg.async_remove(ent.entity_id)
            # 2. Detach from this config entry. Single-owner → HA removes and
            #    tombstones the device; we deliberately do NOT purge the
            #    deleted-entity records, so a future re-add keeps entity identity.
            dev_reg.async_update_device(
                device.id, remove_config_entry_id=entry.entry_id
            )
            removed.append(view)
            if pe["enabled_ids"] is not None:
                stripped.add(cloud_id)

        # 3. Strip removed ids from the curated list once (its options update
        #    reloads the entry). Create-all mode needs no strip and no reload.
        if pe["enabled_ids"] is not None and stripped:
            opts = dict(entry.options)
            enabled = opts.get(CONF_ENABLED_DEVICES)
            if isinstance(enabled, list):
                new_list = [
                    x for x in enabled if fold_channel_id(x) not in stripped
                ]
                if new_list != enabled:
                    opts[CONF_ENABLED_DEVICES] = new_list
                    hass.config_entries.async_update_entry(entry, options=opts)

    _write_removal_notification(hass, removed, skipped)


def _write_removal_notification(
    hass: HomeAssistant,
    removed: list[DeviceView],
    skipped: list[tuple[DeviceView, str]],
) -> None:
    """Second notification summarising what was removed and what was skipped."""
    lines: list[str] = []
    lines.append(f"Removed {len(removed)} device(s) no longer in your account:")
    if removed:
        for view in removed:
            lines.append(f"  • {view.name} ({view.cloud_id})")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append(f"Skipped {len(skipped)} device(s):")
    if skipped:
        for view, reason in skipped:
            why = _SKIP_REASONS.get(reason, reason)
            lines.append(f"  • {view.name} ({view.cloud_id}) — {why}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append(
        "Removal detaches each device and its entities from Home Assistant. "
        "They will NOT come back automatically — re-add the hardware in the "
        "integration options if it returns."
    )
    body = "\n".join(lines)
    _LOGGER.info("Shelly Cloud DIY detect_orphans removal:\n%s", body)
    persistent_notification.async_create(
        hass,
        f"```\n{body}\n```",
        title="Shelly Cloud DIY — devices removed",
        notification_id=f"{DOMAIN}_orphans_removed",
    )
