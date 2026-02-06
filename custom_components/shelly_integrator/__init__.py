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
