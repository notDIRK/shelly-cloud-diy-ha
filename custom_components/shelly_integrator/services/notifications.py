"""Notification service for Shelly Integrator.

Handles persistent notifications for user communication.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.persistent_notification import (
    async_create as notify_create,
    async_dismiss as notify_dismiss,
)

from ..const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

NOTIFICATION_ID_SETUP = f"{DOMAIN}_setup"
NOTIFICATION_ID_DEVICE_ADDED = f"{DOMAIN}_device_added"
NOTIFICATION_ID_HISTORICAL_SUCCESS = f"{DOMAIN}_historical_success"
NOTIFICATION_ID_HISTORICAL_ERROR = f"{DOMAIN}_historical_error"


class NotificationService:
    """Service for managing persistent notifications."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize notification service.

        Args:
            hass: Home Assistant instance
        """
        self._hass = hass

    def show_setup_notification(self, consent_url: str) -> None:
        """Show setup notification with consent URL.

        Args:
            consent_url: URL for device consent
        """
        message = (
            "## Add Your Shelly Devices\n\n"
            "To connect your Shelly devices, click the link below:\n\n"
            f"**[Grant Device Access]({consent_url})**\n\n"
            "You will be asked to log into your Shelly Cloud account and select "
            "which devices to share with Home Assistant.\n\n"
            "---\n"
            "*This notification will remain until you dismiss it. "
            "You can use the link again anytime to add more devices.*"
        )

        notify_create(
            self._hass,
            message=message,
            title="Shelly Integrator Setup",
            notification_id=NOTIFICATION_ID_SETUP,
        )
        _LOGGER.debug("Setup notification created")

    def dismiss_setup_notification(self) -> None:
        """Dismiss setup notification."""
        notify_dismiss(self._hass, NOTIFICATION_ID_SETUP)

    def show_device_added(self, device_name: str) -> None:
        """Show device added notification.

        Args:
            device_name: Name of added device
        """
        notify_create(
            self._hass,
            message=f"Device '{device_name}' added from Shelly Cloud.",
            title="Shelly Device Added",
            notification_id=NOTIFICATION_ID_DEVICE_ADDED,
        )

    def show_historical_success(self, statistic_ids: list[str]) -> None:
        """Show historical data import success.

        Args:
            statistic_ids: List of imported statistic IDs
        """
        stats_list = "\n".join(f"- `{s}`" for s in statistic_ids)
        notify_create(
            self._hass,
            message=(
                f"Historical energy data imported successfully!\n\n"
                f"**Statistics updated:**\n{stats_list}\n\n"
                f"The data is now available in the Energy Dashboard."
            ),
            title="Shelly Historical Data Imported",
            notification_id=NOTIFICATION_ID_HISTORICAL_SUCCESS,
        )

    def show_historical_error(self, message: str) -> None:
        """Show historical data error.

        Args:
            message: Error message
        """
        notify_create(
            self._hass,
            message=message,
            title="Historical Data Conversion Failed",
            notification_id=NOTIFICATION_ID_HISTORICAL_ERROR,
        )

    def show_gateway_url_missing(self) -> None:
        """Show notification for missing gateway URL."""
        self.show_historical_error(
            "No gateway URL configured. Go to Settings → Integrations → "
            "Shelly Integrator → Configure to set the Local Gateway URL."
        )
