"""Consent URL handling for Shelly Integrator.

Handles building consent URLs and parsing webhook payloads.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote_plus

from ..const import SHELLY_CONSENT_URL

_LOGGER = logging.getLogger(__name__)


@dataclass
class WebhookPayload:
    """Parsed webhook payload from Shelly Cloud."""

    device_id: str
    action: str
    host: str | None = None
    name: str | None = None
    device_type: str | None = None
    device_code: str | None = None
    access_groups: str = "00"


def build_consent_url(
    integrator_tag: str,
    ha_external_url: str,
    webhook_id: str,
) -> str:
    """Build Shelly consent URL for device authorization.

    Args:
        integrator_tag: Integrator identification tag
        ha_external_url: Home Assistant external URL
        webhook_id: Webhook ID for callback

    Returns:
        Complete consent URL
    """
    webhook_url = f"{ha_external_url}/api/webhook/{webhook_id}"
    encoded_callback = quote_plus(webhook_url)
    return f"{SHELLY_CONSENT_URL}?itg={integrator_tag}&cb={encoded_callback}"


def parse_webhook_payload(data: dict) -> WebhookPayload | None:
    """Parse webhook payload from Shelly Cloud.

    Shelly Cloud sends ONE request PER DEVICE with format:
    {
        "userId": 123,
        "deviceId": "abc123",
        "deviceType": "SHPLG-1",
        "deviceCode": "shellyplug",
        "accessGroups": "01",
        "action": "add" | "remove",
        "host": "shelly-187-eu.shelly.cloud",
        "name": ["Device Name"]
    }

    Args:
        data: Raw webhook data dictionary

    Returns:
        Parsed WebhookPayload or None if invalid
    """
    device_id = data.get("deviceId")
    if not device_id:
        _LOGGER.error("Missing deviceId in webhook data: %s", data)
        return None

    action = data.get("action", "add")
    host = data.get("host")
    device_type = data.get("deviceType")
    device_code = data.get("deviceCode")
    access_groups = data.get("accessGroups", "00")

    # Name is an array in the payload
    name_list = data.get("name", [])
    name = name_list[0] if name_list else None

    return WebhookPayload(
        device_id=device_id,
        action=action,
        host=host,
        name=name,
        device_type=device_type,
        device_code=device_code,
        access_groups=access_groups,
    )
