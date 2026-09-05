"""Shelly Cloud DIY integration for Home Assistant.

Entry point that:
- Builds the polling coordinator (its first refresh validates the
  stored ``auth_key`` and surfaces auth / transport errors back to HA).
- Forwards to entity platforms.
- Wires up the historical-data service (local-gateway flavour; unchanged
  from the pre-pivot code path).
- Provides ghost-entity purging on device removal from the HA UI.
- Brings up the opt-in cloud control channel (OAuth + the cloud
  WebSocket relay) when — and only when — the user asked for it.

Cloud control is a strictly additive second channel. The poll owns the
state of every device and is untouched by it; the relay only ever carries
commands the documented HTTP API has no route for. With the option off
none of it is constructed.
"""
from __future__ import annotations

import logging
from functools import partial

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .api.cloud_control import ShellyCloudControl
from .api.cloud_ws import ShellyCloudWebSocket
from .api.oauth import OAuthToken, ShellyTokenManager
from .const import (
    CONF_AUTH_KEY,
    CONF_CREATE_ALL_INITIALLY,
    CONF_ENABLED_DEVICES,
    CONF_OAUTH_TOKEN,
    CONF_SERVER_URI,
    DOMAIN,
    PLATFORMS,
    REAUTH_CLOUD_CONTROL,
    SIGNAL_DEVICE_REMOVED,
)
from .coordinator import ShellyCloudCoordinator
from .repair_issues import async_clear_entry_issues
from .services.fleet_map import async_handle_fleet_map
from .services.historical import HistoricalDataService
from .services.orphans import async_handle_detect_orphans
from .services.replace_device import async_handle_replace_device
from .utils.token_store import token_from_storage, token_to_storage

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

    # Opt-in, and inert unless opted in: with the option off nothing below
    # runs, so there is no OAuth round-trip, no second connection, no probe
    # and no control entity. The reading comes from the coordinator so the
    # option has one interpretation, not one per module.
    # See const.CONF_CLOUD_CONTROL.
    if coordinator.cloud_control:
        await _async_setup_cloud_control(hass, entry, coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Historical-data service (local-gateway CSV flow, unchanged from
    # pre-pivot). Kept here so existing users of the download service
    # retain that capability through the pivot.
    historical_service = HistoricalDataService(hass, coordinator, entry)
    hass.data[DOMAIN][f"{entry.entry_id}_historical"] = historical_service
    await _register_services(hass, historical_service)
    await historical_service.setup_auto_sync()

    # Snapshot of the options as loaded, so the update listener can tell an
    # options change (reload) from an ``entry.data`` write (do not reload).
    hass.data[DOMAIN][f"{entry.entry_id}_options"] = dict(entry.options)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Tear down a config entry cleanly.

    The cloud control channel is closed here rather than through
    ``entry.async_on_unload``, for the same reason the historical service is:
    Home Assistant runs the on-unload callbacks only when the platform unload
    *succeeded*. A failed unload would otherwise leave the relay socket, its
    reconnect ladder and the ownership loop running — and the next reload
    would build a second set on top of them.
    """
    historical: HistoricalDataService | None = hass.data[DOMAIN].pop(
        f"{entry.entry_id}_historical", None
    )
    if historical:
        historical.cancel_auto_sync()

    coordinator: ShellyCloudCoordinator | None = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator is not None:
        # Idempotent: with cloud control off there is nothing to close.
        await coordinator.async_disable_cloud_control()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        hass.data[DOMAIN].pop(f"{entry.entry_id}_options", None)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop repair issues belonging to a removed config entry.

    Reload and restart are deliberately NOT handled here. Every issue is
    ``is_persistent=False``, so it is gone after a restart and is re-derived
    within one poll (or one sync cycle) if it still holds. Clearing on unload
    would run on every options save — ``_async_options_updated`` reloads the
    entry — and deleting an issue discards the user's "Ignore", which the
    issue registry stores on the entry itself. That would produce exactly the
    loop the rate-limit card instructs: ignore it, raise the poll interval,
    save, reload, card returns un-ignored.
    """
    async_clear_entry_issues(hass, entry)


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when the user changes poll interval / gateway URL.

    Home Assistant fires this listener for a write to ``entry.data`` as well
    as to ``entry.options``, and a refreshed OAuth token is a write to
    ``entry.data``. Reloading for one would tear the poll down and rebuild
    every entity because a background token rotation happened, so a change
    that left the options identical is not a reason to reload.
    """
    previous = hass.data.get(DOMAIN, {}).get(f"{entry.entry_id}_options")
    if previous is not None and previous == dict(entry.options):
        _LOGGER.debug("Shelly Cloud DIY: entry data updated, options unchanged")
        return
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


# ── Cloud control (opt-in) ──────────────────────────────────────────────


@callback
def _start_cloud_control_reauth(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Ask the user to sign in to Shelly again — for the token, not the key.

    Both credentials fail into the same reauth flow, so the flow is told
    which one is being asked for. Without the marker the user would be shown
    the Authorization-cloud-key form for a rejected OAuth token and would
    quite reasonably paste a perfectly good key into it.

    Only the marker is passed. The flow reads nothing else out of it, and
    handing a token to a flow context is how a token ends up in a place
    nobody thought to check.
    """
    entry.async_start_reauth(hass, data={REAUTH_CLOUD_CONTROL: True})


async def _async_setup_cloud_control(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: ShellyCloudCoordinator
) -> None:
    """Bring the cloud control channel up, or explain why it stayed down.

    Never raises. The channel is an opt-in extra on top of an entry whose
    real job is the poll: a relay that is unreachable, or an account whose
    sign-in has expired, must cost the user their control entities and
    nothing else. Both cases are logged, both are visible in diagnostics,
    and an expired sign-in additionally asks for re-authentication.
    """
    token = token_from_storage(entry.data.get(CONF_OAUTH_TOKEN))
    if token is None:
        _LOGGER.warning(
            "Cloud control is switched on but this entry holds no Shelly "
            "sign-in; asking for one"
        )
        _start_cloud_control_reauth(hass, entry)
        return

    async def _persist_token(new_token: OAuthToken) -> None:
        """Store a refreshed token — but only when it is worth a write.

        Measured against a live account: the ``refresh_token`` does not
        rotate. So the durable half of the record is normally unchanged and
        the only thing a write would save is an access token that expires in
        twelve hours anyway, at the price of a config-entry write (and a
        listener wake-up) on every refresh, forever.
        """
        stored = token_from_storage(entry.data.get(CONF_OAUTH_TOKEN))
        if stored is None:
            # The entry holds no sign-in, and the only way it got that way is
            # that the user switched cloud control off — which deletes it.
            # A refresh still in flight on the closing connection must not
            # put it back.
            return
        if stored.refresh_token == new_token.refresh_token:
            return
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_OAUTH_TOKEN: token_to_storage(new_token)},
        )

    ws: ShellyCloudWebSocket | None = None
    try:
        # Inside the guard, not above it: reading the server URI can raise on
        # a hand-edited entry and building the transport rejects an unusable
        # host, and either escaping here would abort the entry setup — taking
        # the poll down over the optional half of the integration.
        server_uri = entry.data[CONF_SERVER_URI]
        session = async_get_clientsession(hass)
        tokens = ShellyTokenManager(
            session, server_uri, token=token, on_token_refreshed=_persist_token
        )
        ws = ShellyCloudWebSocket(
            session,
            server_uri,
            tokens.async_get_token,
            on_token_rejected=tokens.async_force_refresh,
            on_reauth=partial(_start_cloud_control_reauth, hass, entry),
        )
        await coordinator.async_enable_cloud_control(ws)
    except ConfigEntryAuthFailed:
        # Raised by the token manager when the stored sign-in is spent. It
        # would abort the whole entry setup if it were left to propagate,
        # taking the poll down over the optional half of the integration.
        _LOGGER.warning(
            "Cloud control: the stored Shelly sign-in was rejected; "
            "asking for a new one. Polling is unaffected"
        )
        if ws is not None:
            await ws.disconnect()
        _start_cloud_control_reauth(hass, entry)
    except Exception as err:  # noqa: BLE001 — see the docstring
        # Deliberately every remaining exception, not a list of the expected
        # ones: an optional channel that fails in an unforeseen way must
        # still not abort the entry setup and take the poll with it.
        #
        # Type name only, never ``err`` itself: an aiohttp error's own text
        # embeds the connect URL, and that URL carries the access token as a
        # query parameter.
        _LOGGER.error(
            "Cloud control could not connect (%s); control entities are not "
            "created this session. Polling is unaffected",
            type(err).__name__,
        )
        if ws is not None:
            await ws.disconnect()


# ── Service registration ────────────────────────────────────────────────


async def _register_services(
    hass: HomeAssistant, historical_service: HistoricalDataService
) -> None:
    """Register integration-wide services.

    ``download_and_convert_history`` operates against the local gateway URL and
    is unchanged across the pivot. ``replace_device`` transplants a dead
    Shelly's HA identity onto a new unit of the same model; it is account-
    agnostic (it resolves the config entry from the selected devices), so it is
    registered once with a module-level handler bound to ``hass``.
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

    if not hass.services.has_service(DOMAIN, "replace_device"):
        hass.services.async_register(
            DOMAIN,
            "replace_device",
            partial(async_handle_replace_device, hass),
            schema=vol.Schema(
                {
                    vol.Required("old_device"): cv.string,
                    vol.Required("new_device"): cv.string,
                    vol.Optional("dry_run", default=False): cv.boolean,
                    vol.Optional("force", default=False): cv.boolean,
                }
            ),
        )
        _LOGGER.info("Registered service: shelly_cloud_diy.replace_device")

    if not hass.services.has_service(DOMAIN, "fleet_map"):
        hass.services.async_register(
            DOMAIN,
            "fleet_map",
            partial(async_handle_fleet_map, hass),
            schema=vol.Schema(
                {
                    vol.Optional("dry_run", default=True): cv.boolean,
                    vol.Optional(
                        "apply_native_name_suggestions", default=False
                    ): cv.boolean,
                }
            ),
        )
        _LOGGER.info("Registered service: shelly_cloud_diy.fleet_map")

    if not hass.services.has_service(DOMAIN, "detect_orphans"):
        hass.services.async_register(
            DOMAIN,
            "detect_orphans",
            partial(async_handle_detect_orphans, hass),
            schema=vol.Schema(
                {
                    vol.Optional("dry_run", default=True): cv.boolean,
                    vol.Optional("remove", default=False): cv.boolean,
                    vol.Optional("devices"): vol.All(cv.ensure_list, [cv.string]),
                }
            ),
        )
        _LOGGER.info("Registered service: shelly_cloud_diy.detect_orphans")


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
    # Tell the platforms to forget their entity bookkeeping for this device.
    # Without it they would still consider every entity "already created" and
    # a later rediscovery would silently produce nothing.
    async_dispatcher_send(hass, SIGNAL_DEVICE_REMOVED, device_id)
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
