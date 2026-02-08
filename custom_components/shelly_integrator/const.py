"""Constants for Shelly Integrator."""
from __future__ import annotations

import re
from typing import Any

from homeassistant.const import Platform

DOMAIN = "shelly_integrator"

# Integrator Tag (not secret - identifies this integration)
INTEGRATOR_TAG = "ITG_OSS"

# API Endpoints
API_GET_TOKEN = "https://api.shelly.cloud/integrator/get_access_token"
SHELLY_CONSENT_URL = "https://my.shelly.cloud/integrator.html"
WSS_PORT = 6113
WSS_PATH = "/shelly/wss/hk_sock"

# Config keys (TOKEN entered by user, stored securely by HA)
CONF_INTEGRATOR_TOKEN = "integrator_token"
CONF_LOCAL_GATEWAY_URL = "local_gateway_url"

# Token refresh interval (23 hours to be safe, token valid 24h)
TOKEN_REFRESH_INTERVAL = 23 * 60 * 60

# WebSocket reconnect – exponential backoff bounds (seconds)
WS_RECONNECT_MIN = 1
WS_RECONNECT_MAX = 60

# Historical data sync interval (daily = 24 hours)
HISTORICAL_SYNC_INTERVAL = 24 * 60 * 60

# Platforms
PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.COVER,
    Platform.LIGHT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Webhook
WEBHOOK_ID = "shelly_integrator_callback"

# Gen2/Gen3 detection pattern (shared across all platforms)
_GEN2_PATTERN = re.compile(r"switch:\d+|light:\d+|cover:\d+|input:\d+")


def is_gen2_status(status: dict[str, Any]) -> bool:
    """Check if a device status dict is from a Gen2/Gen3 (RPC) device."""
    return any(_GEN2_PATTERN.match(key) for key in status)
