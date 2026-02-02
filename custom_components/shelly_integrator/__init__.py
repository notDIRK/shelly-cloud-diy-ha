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
    auth = ShellyAuth(session, "ITG_OSS", token)

    try:
        jwt_token = await auth.get_jwt_token()
    except Exception as err:
        raise ConfigEntryNotReady(f"Failed to get JWT token: {err}") from err

    # Create coordinator
    coordinator = ShellyIntegratorCoordinator(
        hass=hass,
        session=session,
        tag="ITG_OSS",
        token=token,
        jwt_token=jwt_token,
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

    # Auto-connect to default server
    _LOGGER.info("Connecting to default server: %s", DEFAULT_SERVER)
    await coordinator.connect_to_host(DEFAULT_SERVER)

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Show consent notification
    notifications = NotificationService(hass)
    try:
        ha_url = get_url(hass, prefer_external=True)
        consent_url = build_consent_url("ITG_OSS", ha_url, WEBHOOK_ID)
        notifications.show_setup_notification(consent_url)
        _LOGGER.info("Consent URL: %s", consent_url)
    except Exception as err:
        _LOGGER.warning("Could not create consent notification: %s", err)

    # Set up services
    await _register_services(hass, entry, coordinator)

    # Set up historical data sync
    historical_service = HistoricalDataService(hass, coordinator, entry)
    await historical_service.setup_auto_sync()
    hass.data[DOMAIN][f"{entry.entry_id}_historical"] = historical_service

    # Options update listener
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    return True


async def _register_services(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: ShellyIntegratorCoordinator,
) -> None:
    """Register integration services."""
    historical_service = HistoricalDataService(hass, coordinator, entry)

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
    """Handle options update."""
    _LOGGER.info("Options updated for Shelly Integrator")


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
