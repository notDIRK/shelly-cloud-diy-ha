"""Shelly Integrator integration for Home Assistant.

This is the main entry point that orchestrates:
- Integration setup and teardown
- Service registration
- Webhook registration
"""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import get_url
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.components.webhook import (
    async_register as webhook_register,
    async_unregister as webhook_unregister,
)

from .const import (
    DOMAIN,
    INTEGRATOR_TAG,
    PLATFORMS,
    CONF_INTEGRATOR_TOKEN,
    WEBHOOK_ID,
)
from .coordinator import ShellyIntegratorCoordinator
from .api.auth import ShellyAuth
from .core.consent import build_consent_url
from .services.notifications import NotificationService
from .services.webhook import WebhookHandler
from .services.historical import HistoricalDataService

_LOGGER = logging.getLogger(__name__)

# Default EU server for auto-connect
DEFAULT_SERVER = "shelly-187-eu.shelly.cloud"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Shelly Integrator from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    session = async_get_clientsession(hass)
    token = entry.data[CONF_INTEGRATOR_TOKEN]

    # Initialize authentication
    auth = ShellyAuth(session, INTEGRATOR_TAG, token)

    try:
        jwt_token = await auth.get_jwt_token()
    except Exception as err:
        raise ConfigEntryNotReady(f"Failed to get JWT token: {err}") from err

    # Create coordinator
    coordinator = ShellyIntegratorCoordinator(
        hass=hass,
        session=session,
        tag=INTEGRATOR_TAG,
        token=token,
        jwt_token=jwt_token,
        entry=entry,
    )

    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up webhook
    webhook_handler = WebhookHandler(hass, coordinator)
    webhook_register(
        hass,
        DOMAIN,
        "Shelly Integrator Callback",
        WEBHOOK_ID,
        lambda h, w, r: webhook_handler.handle_request(r),
    )
    hass.data[DOMAIN][f"{entry.entry_id}_webhook"] = WEBHOOK_ID

    # Auto-connect to default server and wait for devices
    _LOGGER.info("Connecting to default server: %s", DEFAULT_SERVER)
    await coordinator.connect_to_host(DEFAULT_SERVER)

    # Wait for known devices to be verified before setting up platforms
    await coordinator.async_wait_for_devices(timeout=5.0)

    # Purge ghost entities left over from previously deleted devices
    # so they get recreated fresh with current naming format
    _purge_deleted_entities(hass, entry)

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Show consent notification
    notifications = NotificationService(hass)
    try:
        ha_url = get_url(hass, prefer_external=True)
        consent_url = build_consent_url(INTEGRATOR_TAG, ha_url, WEBHOOK_ID)
        notifications.show_setup_notification(consent_url)
        _LOGGER.info("Consent URL: %s", consent_url)
    except Exception as err:
        _LOGGER.warning("Could not create consent notification: %s", err)

    # Set up historical data service (single instance for both
    # manual service calls and automatic sync)
    historical_service = HistoricalDataService(hass, coordinator, entry)
    hass.data[DOMAIN][f"{entry.entry_id}_historical"] = historical_service

    await _register_services(hass, entry, historical_service)
    await historical_service.setup_auto_sync()

    # Options update listener
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


# ── Deleted-entity purge ─────────────────────────────────────
#
# When a device is deleted from the HA UI, HA moves its entities
# to ``deleted_entities`` in the entity registry.  If the same
# device is later re-added, HA matches on ``unique_id`` and
# restores the OLD entity_id – even if the naming format has
# changed.
#
# To guarantee a completely fresh start after deletion we purge
# those ghost records:
#   1. On every startup (catches anything left from previous runs)
#   2. Shortly after a device is deleted from the UI (so an
#      immediate re-add within the same session also starts fresh)


def _purge_deleted_entities(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Remove ghost entity records for this config entry.

    HA stores deleted entities keyed by (domain, platform,
    unique_id).  We iterate and drop every entry that belongs
    to our config entry so that re-added devices get fresh
    entity IDs.
    """
    ent_reg = er.async_get(hass)
    deleted = ent_reg.deleted_entities
    to_remove = [
        key for key, e in deleted.items()
        if e.config_entry_id == entry.entry_id
    ]
    if not to_remove:
        return

    for key in to_remove:
        deleted.pop(key, None)

    ent_reg.async_schedule_save()
    _LOGGER.info(
        "Purged %d ghost entity records", len(to_remove)
    )


def _purge_device_entities(
    hass: HomeAssistant,
    config_entry_id: str,
    ha_device_id: str,
    shelly_device_id: str,
) -> None:
    """Remove all entities for a device and purge their ghost records.

    Called synchronously (within the event loop) from
    ``async_remove_config_entry_device`` *before* returning True.
    This avoids any arbitrary sleep: we proactively remove the
    entities, then immediately clean up ``deleted_entities`` so
    a future re-add starts completely fresh.

    Args:
        hass: Home Assistant instance
        config_entry_id: Config entry ID
        ha_device_id: HA internal device ID (device_entry.id)
        shelly_device_id: Shelly Cloud device ID
    """
    ent_reg = er.async_get(hass)

    # Step 1: Remove all live entities for this device.
    # async_remove moves each entity into ``deleted_entities``.
    entities = er.async_entries_for_device(
        ent_reg, ha_device_id, include_disabled_entities=True
    )
    for entity in entities:
        ent_reg.async_remove(entity.entity_id)

    if not entities:
        return

    # Step 2: Purge the ghost records we just created so
    # re-adding the same device produces fresh entity IDs.
    deleted = ent_reg.deleted_entities
    to_remove = [
        key for key, e in deleted.items()
        if e.config_entry_id == config_entry_id
        and shelly_device_id in (e.unique_id or "")
    ]
    if not to_remove:
        return

    for key in to_remove:
        deleted.pop(key, None)

    ent_reg.async_schedule_save()
    _LOGGER.info(
        "Removed %d entities and purged ghost records "
        "for device %s",
        len(entities),
        shelly_device_id,
    )


# ── Service registration ─────────────────────────────────────

async def _register_services(
    hass: HomeAssistant,
    entry: ConfigEntry,
    historical_service: HistoricalDataService,
) -> None:
    """Register integration services."""
    if not hass.services.has_service(DOMAIN, "download_and_convert_history"):
        hass.services.async_register(
            DOMAIN,
            "download_and_convert_history",
            historical_service.handle_service_call,
            schema=vol.Schema({
                vol.Optional("gateway_url"): cv.string,
                vol.Optional("device_id"): cv.string,
            }),
        )
        _LOGGER.info("Registered service: shelly_integrator.download_and_convert_history")


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update by reloading the integration."""
    _LOGGER.info("Options updated, reloading Shelly Integrator")
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: ShellyIntegratorCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Dismiss notifications
    NotificationService(hass).dismiss_setup_notification()

    # Unregister webhook
    webhook_id = hass.data[DOMAIN].pop(f"{entry.entry_id}_webhook", None)
    if webhook_id:
        webhook_unregister(hass, webhook_id)

    # Cancel historical sync
    historical: HistoricalDataService | None = hass.data[DOMAIN].pop(
        f"{entry.entry_id}_historical", None
    )
    if historical:
        historical.cancel_auto_sync()

    # Close coordinator
    await coordinator.async_close()

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow user to delete a Shelly device from HA UI.

    Enables the "Delete" button on device pages.  When clicked:
    1. All entities for the device are removed and ghost
       records purged synchronously (no arbitrary sleep)
    2. Coordinator memory + persistent storage are cleaned
    3. We return True so HA removes the device entry
    """
    coordinator: ShellyIntegratorCoordinator = (
        hass.data[DOMAIN][config_entry.entry_id]
    )

    # Extract Shelly device ID from HA device identifiers
    device_id = None
    for identifier in device_entry.identifiers:
        if identifier[0] == DOMAIN:
            device_id = identifier[1]
            break

    if not device_id:
        return False

    # Proactively remove entities and purge ghost records
    # before HA's cascade removal — fully deterministic,
    # no sleep required.
    _purge_device_entities(
        hass,
        config_entry.entry_id,
        device_entry.id,
        device_id,
    )

    await coordinator.async_remove_device(device_id)
    return True
