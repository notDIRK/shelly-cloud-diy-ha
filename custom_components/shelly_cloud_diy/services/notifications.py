"""Persistent-notification helpers for Shelly Cloud DIY.

Kept focused on historical-sync user feedback. Consent / setup notifications
from the pre-pivot Integrator-API flow are intentionally removed — the
Cloud Control API does not need a consent webhook step.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.persistent_notification import (
    async_create as notify_create,
)

from ..const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

NOTIFICATION_ID_HISTORICAL_SUCCESS = f"{DOMAIN}_historical_success"
NOTIFICATION_ID_HISTORICAL_ERROR = f"{DOMAIN}_historical_error"


class NotificationService:
    """Persistent notifications for the historical-data service."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def show_historical_success(self, statistic_ids: list[str]) -> None:
        stats_list = "\n".join(f"- `{s}`" for s in statistic_ids)
        notify_create(
            self._hass,
            message=(
                "Historical energy data imported successfully.\n\n"
                f"**Statistics updated:**\n{stats_list}\n\n"
                "The data is now available in the Energy Dashboard."
            ),
            title="Shelly historical data imported",
            notification_id=NOTIFICATION_ID_HISTORICAL_SUCCESS,
        )

    def show_historical_error(self, message: str) -> None:
        notify_create(
            self._hass,
            message=message,
            title="Shelly historical data import failed",
            notification_id=NOTIFICATION_ID_HISTORICAL_ERROR,
        )

    def show_gateway_url_missing(self) -> None:
        self.show_historical_error(
            "No gateway URL configured. Go to Settings → Devices & Services "
            "→ Shelly Cloud DIY → Configure to set the Local Gateway URL."
        )
