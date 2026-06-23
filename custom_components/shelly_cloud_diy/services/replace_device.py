"""Replace-device service for Shelly Cloud DIY.

Transplants a dead Shelly's Home Assistant identity onto a new Shelly of the
same model, so the user does not have to re-wire Home Assistant after a
hardware swap. Everything that referenced the old device keeps working:
``entity_id``\\ s, the HA ``device_id``, the device/entity name, area, labels,
long-term history, and every automation / dashboard / scene / template
reference.

Why this is a simple prefix rewrite for *this* integration: every entity
``unique_id`` is built as ``f"{device_id}_…"`` (see the platform files) and
every HA device is keyed solely by ``identifiers={(DOMAIN, device_id)}`` (no
MAC ``connections`` — see ``entities/base.py``). So a swap reduces to:

1. delete the new device's freshly-created (duplicate) entities + its HA device
   entry — this frees the ``{new_id}_…`` unique_ids and the ``(DOMAIN, new_id)``
   identifier so they can be claimed below;
2. re-point the *old* HA device entry's identifier to ``(DOMAIN, new_id)`` — the
   same HA ``device_id`` survives, so device-based automations and the device's
   area/label assignment keep working;
3. rewrite every old entity's unique_id prefix ``{old_id}_`` → ``{new_id}_`` —
   on reload the integration's ``async_get_or_create`` adopts the existing rows,
   preserving entity_id / name / area / history;
4. swap the device in the ``enabled_devices`` option (if a curated list is used);
5. reload the config entry so the new hardware binds to the adopted rows.

Modelled on Home Assistant core's ESPHome device-replacement repair flow
(core PR #142507), adapted to this integration's cloud device-id scheme.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components import persistent_notification
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import CONF_ENABLED_DEVICES, DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant, ServiceCall

_LOGGER = logging.getLogger(__name__)


def _shelly_id(device: dr.DeviceEntry) -> str | None:
    """Return the Shelly Cloud device id encoded in a HA device entry."""
    for domain, ident in device.identifiers:
        if domain == DOMAIN:
            return ident
    return None


def _resolve_entry(
    hass: HomeAssistant, old_dev: dr.DeviceEntry, new_dev: dr.DeviceEntry
) -> ConfigEntry | None:
    """Return the shared Shelly Cloud DIY config entry of both devices."""
    common = set(old_dev.config_entries) & set(new_dev.config_entries)
    for entry_id in common:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is not None and entry.domain == DOMAIN:
            return entry
    return None


def _purge_deleted_for_id(
    ent_reg: er.EntityRegistry, config_entry_id: str, shelly_id: str
) -> None:
    """Drop soft-deleted entity records that reference ``shelly_id``.

    Removing the new device's duplicate entities leaves ghost records in
    ``deleted_entities``; clearing them keeps a future re-add clean and stops
    them shadowing the adopted entities.
    """
    deleted = ent_reg.deleted_entities
    to_remove = [
        key
        for key, e in deleted.items()
        if e.config_entry_id == config_entry_id and shelly_id in (e.unique_id or "")
    ]
    for key in to_remove:
        deleted.pop(key, None)
    if to_remove:
        ent_reg.async_schedule_save()


async def async_handle_replace_device(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle the ``shelly_cloud_diy.replace_device`` service call."""
    old_ha_id: str = call.data["old_device"]
    new_ha_id: str = call.data["new_device"]
    dry_run: bool = bool(call.data.get("dry_run", False))
    force: bool = bool(call.data.get("force", False))

    if old_ha_id == new_ha_id:
        raise ServiceValidationError(
            "The old and new device must be different."
        )

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    old_dev = dev_reg.async_get(old_ha_id)
    new_dev = dev_reg.async_get(new_ha_id)
    if old_dev is None or new_dev is None:
        raise ServiceValidationError(
            "One of the selected devices no longer exists."
        )

    old_id = _shelly_id(old_dev)
    new_id = _shelly_id(new_dev)
    if not old_id or not new_id:
        raise ServiceValidationError(
            "Both devices must belong to the Shelly Cloud DIY integration."
        )

    entry = _resolve_entry(hass, old_dev, new_dev)
    if entry is None:
        raise ServiceValidationError(
            "Both devices must belong to the same Shelly Cloud DIY account."
        )

    # Model guard. Uses the HA device registry (not live cloud data) so it still
    # works when the old device is already offline / gone from the account.
    if not force and (old_dev.model or "") != (new_dev.model or ""):
        raise ServiceValidationError(
            f"Model mismatch: old device is '{old_dev.model}', new device is "
            f"'{new_dev.model}'. Re-check the selection, or pass force: true to "
            f"override (only do this if you know the channel layout matches)."
        )

    old_entities = er.async_entries_for_device(
        ent_reg, old_dev.id, include_disabled_entities=True
    )
    new_entities = er.async_entries_for_device(
        ent_reg, new_dev.id, include_disabled_entities=True
    )

    prefix_old = f"{old_id}_"
    prefix_new = f"{new_id}_"
    rewrites: list[tuple[str, str, str]] = []  # (entity_id, old_uid, new_uid)
    for ent in old_entities:
        if not ent.unique_id.startswith(prefix_old):
            continue
        new_uid = prefix_new + ent.unique_id[len(prefix_old):]
        if new_uid != ent.unique_id:
            rewrites.append((ent.entity_id, ent.unique_id, new_uid))

    if not rewrites:
        raise ServiceValidationError(
            "The old device has no entities to migrate — nothing to do."
        )

    plan_lines = [
        f"Replace Shelly device {old_id} -> {new_id}",
        f"  Old HA device: {old_dev.name_by_user or old_dev.name} ({old_dev.id})",
        f"  New HA device: {new_dev.name_by_user or new_dev.name} ({new_dev.id})",
        f"  Remove {len(new_entities)} duplicate entity(ies) from the new device",
        f"  Re-point HA device identifier to (DOMAIN, {new_id}); device_id preserved",
        f"  Rewrite {len(rewrites)} entity unique_id(s): {prefix_old}* -> {prefix_new}*",
    ]
    for _eid, ouid, nuid in rewrites:
        plan_lines.append(f"    {ouid} -> {nuid}")
    plan_text = "\n".join(plan_lines)

    _LOGGER.info("Shelly Cloud DIY replace_device plan:\n%s", plan_text)

    if dry_run:
        persistent_notification.async_create(
            hass,
            f"Dry run — nothing was changed.\n\n```\n{plan_text}\n```",
            title="Shelly Cloud DIY — replace device (dry run)",
            notification_id=f"{DOMAIN}_replace_dryrun",
        )
        return

    # ── Apply ─────────────────────────────────────────────────────────────
    # 1. Remove the new device's freshly-created duplicate entities (frees the
    #    {new_id}_… unique_ids the rewrite below claims).
    for ent in new_entities:
        ent_reg.async_remove(ent.entity_id)

    # 2. Remove the new HA device entry (frees the (DOMAIN, new_id) identifier).
    dev_reg.async_remove_device(new_dev.id)

    # 3. Re-point the OLD HA device entry onto the new hardware. Same device_id
    #    survives -> device-based automations and area/label keep working.
    #    name_by_user (a user rename) is left untouched by async_update_device.
    new_name = old_dev.name
    if old_dev.name and f"({old_id})" in old_dev.name:
        new_name = old_dev.name.replace(f"({old_id})", f"({new_id})")
    dev_reg.async_update_device(
        old_dev.id,
        new_identifiers={(DOMAIN, new_id)},
        name=new_name,
    )

    # 4. Rewrite every old entity's unique_id -> adopted on reload.
    for entity_id, _ouid, new_uid in rewrites:
        ent_reg.async_update_entity(entity_id, new_unique_id=new_uid)

    # Clean up ghost records for the new id left by step 1.
    _purge_deleted_for_id(ent_reg, entry.entry_id, new_id)

    # 5. Swap the device in the curated enabled list (if one is in use). This
    #    triggers the options-update listener, which reloads the entry — so we
    #    must NOT also reload explicitly below (double reload).
    reload_via_options = False
    opts = dict(entry.options)
    enabled = opts.get(CONF_ENABLED_DEVICES)
    if isinstance(enabled, list):
        new_list = [d for d in enabled if d != old_id]
        if new_id not in new_list:
            new_list.append(new_id)
        if new_list != enabled:
            opts[CONF_ENABLED_DEVICES] = new_list
            hass.config_entries.async_update_entry(entry, options=opts)
            reload_via_options = True

    _LOGGER.info(
        "Shelly Cloud DIY: replaced %s -> %s (%d entities re-pointed)",
        old_id,
        new_id,
        len(rewrites),
    )
    persistent_notification.async_create(
        hass,
        f"Replaced the Shelly device and preserved {len(rewrites)} entity(ies), "
        f"their history, and all automations / dashboards.\n\n```\n{plan_text}\n```",
        title="Shelly Cloud DIY — device replaced",
        notification_id=f"{DOMAIN}_replace_done",
    )

    # 6. Reload so the integration re-binds the new hardware to the adopted rows.
    if not reload_via_options:
        await hass.config_entries.async_reload(entry.entry_id)
