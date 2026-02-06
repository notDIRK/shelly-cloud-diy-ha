"""Webhook handler for Shelly Integrator.

Handles webhook callbacks from Shelly Cloud for device authorization.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiohttp import web

from ..core.consent import parse_webhook_payload
from .notifications import NotificationService

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from ..coordinator import ShellyIntegratorCoordinator

_LOGGER = logging.getLogger(__name__)


class WebhookHandler:
    """Handles Shelly Cloud webhook callbacks."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: ShellyIntegratorCoordinator,
    ) -> None:
        """Initialize webhook handler.

        Args:
            hass: Home Assistant instance
            coordinator: Shelly Integrator coordinator
        """
        self._hass = hass
        self._coordinator = coordinator
        self._notifications = NotificationService(hass)

    async def handle_request(self, request: web.Request) -> web.Response:
        """Handle incoming webhook request.

        Args:
            request: aiohttp web request

        Returns:
            Web response
        """
        try:
            data = await request.json()
            _LOGGER.info("Webhook received: %s", data)

            payload = parse_webhook_payload(data)
            if not payload:
                return web.Response(status=400)

            if payload.action == "add":
                await self._handle_device_add(payload)
            elif payload.action == "remove":
                await self._handle_device_remove(payload)

            return web.Response(text="OK", status=200)

        except Exception as err:
            _LOGGER.error("Webhook error: %s", err)
            return web.Response(status=500)

    async def _handle_device_add(self, payload) -> None:
        """Handle device add action.

        Args:
            payload: Parsed webhook payload
        """
        _LOGGER.info(
            "Device granted: id=%s, host=%s, type=%s, code=%s, name=%s",
            payload.device_id,
            payload.host,
            payload.device_type,
            payload.device_code,
            payload.name,
        )

        # Store device info in coordinator
        if payload.host:
            host_map = self._coordinator._device_host_map
            host_map[payload.device_id] = payload.host

        # Pre-populate device data
        if payload.device_id not in self._coordinator.devices:
            self._coordinator.devices[payload.device_id] = {}

        device = self._coordinator.devices[payload.device_id]
        if payload.name:
            device["name"] = payload.name
        if payload.device_type:
            device["device_type"] = payload.device_type
        if payload.device_code:
            device["device_code"] = payload.device_code

        # Connect to host if not already connected
        if payload.host and payload.host not in self._coordinator.hosts:
            _LOGGER.info("Connecting to new host: %s", payload.host)
            await self._coordinator.connect_to_host(payload.host)

        # Show notification
        name = payload.name or payload.device_id
        self._notifications.show_device_added(name)

    async def _handle_device_remove(self, payload) -> None:
        """Handle device remove action (consent revoked in Shelly Cloud).

        Args:
            payload: Parsed webhook payload
        """
        _LOGGER.info("Device consent revoked: id=%s", payload.device_id)
        await self._coordinator.async_remove_device(payload.device_id)
