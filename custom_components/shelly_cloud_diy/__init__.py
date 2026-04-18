"""Shelly Cloud DIY integration for Home Assistant.

Entry point that:
- Builds the polling coordinator (its first refresh validates the
  stored ``auth_key`` and surfaces auth / transport errors back to HA).
- Forwards to entity platforms.
- Wires up the historical-data service (local-gateway flavour; unchanged
  from the pre-pivot code path).
- Provides ghost-entity purging on device removal from the HA UI.

The pre-pivot Integrator-API machinery (JWT token refresh, WebSocket
client, consent-callback webhook) is intentionally absent here. Those
move to Milestone 2 when OAuth + WebSocket land.
"""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api.cloud_control import ShellyCloudControl
from .const import (
    CONF_AUTH_KEY,
    CONF_CREATE_ALL_INITIALLY,
    CONF_ENABLED_DEVICES,
    CONF_SERVER_URI,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import ShellyCloudCoordinator
from .services.historical import HistoricalDataService

_LOGGER = logging.getLogger(__name__)


# ── Setup / teardown ────────────────────────────────────────────────────


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Shelly Cloud DIY config entry."""
    hass.data.setdefault(DOMAIN, {})

    auth_key = entry.data.get(CONF_AUTH_KEY)
    server_uri = entry.data.get(CONF_SERVER_URI)
    if not auth_key or not server_uri:
        # Corrupt entry (e.g. hand-edited .storage) — force reauth.
        raise ConfigEntryAuthFailed("Missing auth_key or server_uri")

    _migrate_to_v0_4_0(hass, entry)

    session = async_get_clientsession(hass)
    api = ShellyCloudControl(session, server_uri, auth_key)

    coordinator = ShellyCloudCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    _LOGGER.info(
        "Shelly Cloud DIY: connected to %s, %d device(s) visible",
        server_uri,
        len(coordinator.devices),
    )

    # Purge ghost entity records left over from previously-deleted devices
    # so a later re-add produces fresh entity IDs (carried over from the
    # pre-pivot code — still useful because HA keeps deleted-entity
    # bookkeeping regardless of the underlying API).
    _purge_deleted_entities(hass, entry)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Historical-data service (local-gateway CSV flow, unchanged from
    # pre-pivot). Kept here so existing users of the download service
    # retain that capability through the pivot.
    historical_service = HistoricalDataService(hass, coordinator, entry)
    hass.data[DOMAIN][f"{entry.entry_id}_historical"] = historical_service
    await _register_services(hass, historical_service)
    await historical_service.setup_auto_sync()

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down a config entry cleanly."""
    historical: HistoricalDataService | None = hass.data[DOMAIN].pop(
        f"{entry.entry_id}_historical", None
    )
    if historical:
        historical.cancel_auto_sync()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when the user changes poll interval / gateway URL."""
    _LOGGER.info("Shelly Cloud DIY: options changed, reloading")
    await hass.config_entries.async_reload(entry.entry_id)


def _migrate_to_v0_4_0(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Preserve v0.3.x behaviour for entries upgraded to v0.4.0.

    Pre-v0.4.0 installs have no device-selection keys in ``entry.options``.
    To avoid silently losing every entity on upgrade, we force
    ``create_all_initially=True`` for any entry that carries neither
    ``CONF_CREATE_ALL_INITIALLY`` nor ``CONF_ENABLED_DEVICES``. The user
    can later opt into a curated subset via the options flow.
    """
    opts = dict(entry.options)
    if CONF_CREATE_ALL_INITIALLY in opts or CONF_ENABLED_DEVICES in opts:
        return
    opts[CONF_CREATE_ALL_INITIALLY] = True
    hass.config_entries.async_update_entry(entry, options=opts)
    _LOGGER.info(
        "Shelly Cloud DIY: migrated config entry to v0.4.0 — "
        "all devices remain enabled; use options flow to curate."
    )


# ── Service registration ────────────────────────────────────────────────


async def _register_services(
    hass: HomeAssistant, historical_service: HistoricalDataService
) -> None:
    """Register integration-wide services.

    Only ``download_and_convert_history`` is registered here — it operates
    against the local gateway URL and is unchanged across the pivot.
    """
    if not hass.services.has_service(DOMAIN, "download_and_convert_history"):
        hass.services.async_register(
            DOMAIN,
            "download_and_convert_history",
            historical_service.handle_service_call,
            schema=vol.Schema(
                {
                    vol.Optional("gateway_url"): cv.string,
                    vol.Optional("device_id"): cv.string,
                }
            ),
        )
        _LOGGER.info("Registered service: shelly_cloud_diy.download_and_convert_history")


# ── Device removal & ghost-entity purge ─────────────────────────────────


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow the user to delete an individual device from the HA UI.

    Removes the device's live entities and any ghost entries in the
    entity registry so a later re-add starts fresh.
    """
    device_id: str | None = None
    for identifier in device_entry.identifiers:
        if identifier[0] == DOMAIN:
            device_id = identifier[1]
            break

    if not device_id:
        return False

    _purge_device_entities(hass, config_entry.entry_id, device_entry.id, device_id)
    return True


def _purge_deleted_entities(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Drop ``deleted_entities`` records for this config entry."""
    ent_reg = er.async_get(hass)
    deleted = ent_reg.deleted_entities
    to_remove = [
        key for key, e in deleted.items() if e.config_entry_id == entry.entry_id
    ]
    if not to_remove:
        return
    for key in to_remove:
        deleted.pop(key, None)
    ent_reg.async_schedule_save()
    _LOGGER.info("Purged %d ghost entity records", len(to_remove))


def _purge_device_entities(
    hass: HomeAssistant,
    config_entry_id: str,
    ha_device_id: str,
    shelly_device_id: str,
) -> None:
    """Remove every entity of a device and purge ghost records."""
    ent_reg = er.async_get(hass)
    entities = er.async_entries_for_device(
        ent_reg, ha_device_id, include_disabled_entities=True
    )
    for entity in entities:
        ent_reg.async_remove(entity.entity_id)

    if not entities:
        return

    deleted = ent_reg.deleted_entities
    to_remove = [
        key for key, e in deleted.items()
        if e.config_entry_id == config_entry_id
        and shelly_device_id in (e.unique_id or "")
    ]
    if to_remove:
        for key in to_remove:
            deleted.pop(key, None)
        ent_reg.async_schedule_save()

    _LOGGER.info(
        "Removed %d entities and purged %d ghost records for device %s",
        len(entities),
        len(to_remove),
        shelly_device_id,
    )
