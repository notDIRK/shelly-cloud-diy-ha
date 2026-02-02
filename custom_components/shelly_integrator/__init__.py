"""Shelly Integrator integration for Home Assistant."""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote_plus

import aiohttp
from aiohttp import web
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import get_url
from homeassistant.components.webhook import (
    async_register as webhook_register,
    async_unregister as webhook_unregister,
)
from homeassistant.components.persistent_notification import (
    async_create as notify_create,
    async_dismiss as notify_dismiss,
)

from .const import (
    DOMAIN,
    PLATFORMS,
    API_GET_TOKEN,
    INTEGRATOR_TAG,
    CONF_INTEGRATOR_TOKEN,
    WEBHOOK_ID,
    SHELLY_CONSENT_URL,
)
from .coordinator import ShellyIntegratorCoordinator

_LOGGER = logging.getLogger(__name__)

NOTIFICATION_ID = f"{DOMAIN}_setup"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Shelly Integrator from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    token = entry.data[CONF_INTEGRATOR_TOKEN]

    session = async_get_clientsession(hass)

    # Get JWT token
    try:
        jwt_token = await get_jwt_token(session, INTEGRATOR_TAG, token)
    except Exception as err:
        raise ConfigEntryNotReady(f"Failed to get JWT token: {err}") from err

    # Create coordinator
    coordinator = ShellyIntegratorCoordinator(
        hass=hass,
        session=session,
        tag=INTEGRATOR_TAG,
        token=token,
        jwt_token=jwt_token,
    )

    # Start WebSocket connection
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Register webhook for user consent callback
    webhook_id = f"{WEBHOOK_ID}_{entry.entry_id}"
    webhook_register(
        hass,
        DOMAIN,
        "Shelly Integrator Callback",
        webhook_id,
        lambda hass, webhook_id, request: handle_webhook(hass, entry.entry_id, request),
    )

    # Store webhook ID for unload
    hass.data[DOMAIN][f"{entry.entry_id}_webhook"] = webhook_id

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Create persistent notification with consent URL
    await _create_consent_notification(hass, webhook_id)

    return True


async def _create_consent_notification(hass: HomeAssistant, webhook_id: str) -> None:
    """Create a persistent notification with the device consent URL."""
    try:
        # Get the external URL of Home Assistant
        ha_url = get_url(hass, prefer_external=True)
        webhook_url = f"{ha_url}/api/webhook/{webhook_id}"
        encoded_callback = quote_plus(webhook_url)

        consent_url = f"{SHELLY_CONSENT_URL}?itg={INTEGRATOR_TAG}&cb={encoded_callback}"

        message = (
            "## Add Your Shelly Devices\n\n"
            "To connect your Shelly devices, click the link below to grant access:\n\n"
            f"**[Grant Device Access]({consent_url})**\n\n"
            "You will be asked to log into your Shelly Cloud account and select "
            "which devices to share with Home Assistant.\n\n"
            "---\n"
            "*This notification will remain until you dismiss it. "
            "You can use the link again anytime to add more devices.*"
        )

        notify_create(
            hass,
            message=message,
            title="Shelly Integrator Setup",
            notification_id=NOTIFICATION_ID,
        )

        _LOGGER.info("Consent URL: %s", consent_url)

    except Exception as err:
        _LOGGER.warning("Could not create consent notification: %s", err)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: ShellyIntegratorCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Dismiss notification
    notify_dismiss(hass, NOTIFICATION_ID)

    # Unregister webhook
    webhook_id = hass.data[DOMAIN].pop(f"{entry.entry_id}_webhook", None)
    if webhook_id:
        webhook_unregister(hass, webhook_id)

    # Close WebSocket connection
    await coordinator.async_close()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def handle_webhook(
    hass: HomeAssistant, entry_id: str, request: web.Request
) -> web.Response:
    """Handle webhook callback from Shelly Cloud user consent."""
    try:
        data = await request.json()
        _LOGGER.debug("Received webhook data: %s", data)

        # Expected format from Shelly consent callback:
        # {
        #   "devices": [{"id": "...", "server": "..."}],
        #   "user_api_url": "..."
        # }

        coordinator: ShellyIntegratorCoordinator = hass.data[DOMAIN].get(entry_id)
        if not coordinator:
            _LOGGER.error("Coordinator not found for entry %s", entry_id)
            return web.Response(status=404)

        devices = data.get("devices", [])
        hosts_to_connect: set[str] = set()

        for device in devices:
            device_id = device.get("id")
            server = device.get("server")

            if device_id and server:
                coordinator._device_host_map[device_id] = server
                hosts_to_connect.add(server)
                _LOGGER.info("Device %s granted on server %s", device_id, server)

        # Connect to new hosts
        for host in hosts_to_connect:
            if host not in coordinator.hosts:
                await coordinator.connect_to_host(host)

        # Show success notification
        if devices:
            device_count = len(devices)
            notify_create(
                hass,
                message=f"Successfully added {device_count} device(s) from Shelly Cloud. "
                        "They will appear shortly in your devices list.",
                title="Shelly Devices Added",
                notification_id=f"{DOMAIN}_devices_added",
            )

        return web.Response(text="OK", status=200)

    except Exception as err:
        _LOGGER.error("Webhook error: %s", err)
        return web.Response(status=500)


async def get_jwt_token(
    session: aiohttp.ClientSession,
    tag: str,
    token: str,
) -> str:
    """Get JWT token from Shelly Cloud API."""
    async with session.post(
        API_GET_TOKEN,
        data={"itg": tag, "token": token},
    ) as response:
        response.raise_for_status()
        data = await response.json()

        if not data.get("isok"):
            raise ValueError(f"API error: {data}")

        return data["data"]
