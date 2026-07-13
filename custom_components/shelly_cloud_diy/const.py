"""Constants for Shelly Cloud DIY."""
from __future__ import annotations

import re
from typing import Any

from homeassistant.const import Platform

DOMAIN = "shelly_cloud_diy"

# ── Config entry keys ──────────────────────────────────────────────

CONF_AUTH_KEY = "auth_key"
CONF_SERVER_URI = "server_uri"
CONF_POLL_INTERVAL = "poll_interval"
CONF_LOCAL_GATEWAY_URL = "local_gateway_url"

# ── Device selection (v0.4.0) ──────────────────────────────────────

# List of Shelly device_ids the user wants materialised as HA entities.
# Stored in ``entry.options``. Interaction with ``CONF_CREATE_ALL_INITIALLY``
# below:
#   - ``CONF_CREATE_ALL_INITIALLY`` = True  → all devices enabled, list ignored.
#   - ``CONF_CREATE_ALL_INITIALLY`` unset/False AND ``CONF_ENABLED_DEVICES``
#     present → only listed devices are enabled.
#   - Neither key present (pre-v0.4.0 entries before migration) → all enabled.
CONF_ENABLED_DEVICES = "enabled_devices"

# Boolean option: "create entities for every device the account can see".
# Set to True on upgrade from v0.3.x so existing installs keep all their
# entities, and whenever the user opts into the "all devices" checkbox in
# the config / options flow.
CONF_CREATE_ALL_INITIALLY = "create_all_initially"

# ── Polling configuration ──────────────────────────────────────────

# Shelly documents a 1 req/s rate limit per account. Default 5 s leaves
# four commands per second of headroom. The floor of 3 s keeps polling
# comfortably under the limit even with occasional retries; the ceiling
# of 60 s is for battery-sensitive setups.
POLL_INTERVAL_MIN = 3
POLL_INTERVAL_MAX = 60
POLL_INTERVAL_DEFAULT = 5

# ── Historical sync (unchanged from pre-pivot) ─────────────────────

HISTORICAL_SYNC_INTERVAL = 24 * 60 * 60  # daily

# ── Dispatcher signals ─────────────────────────────────────────────

SIGNAL_NEW_DEVICE = f"{DOMAIN}_new_device"

# ── Fleet-Map overlay (Stage 1) ────────────────────────────────────

# Domain of Home Assistant Core's native (local/LAN) Shelly integration.
# The Fleet-Map overlay joins our cloud devices to native ones purely by
# MAC, so we only need the domain string — never a Python import of the
# native integration's internals.
NATIVE_SHELLY_DOMAIN = "shelly"

# Entity domains that constitute *control* (as opposed to read-only
# sensing). Used by the resilience check to tell a controllable device
# from a sensor-only one (e.g. a shared WS90 weather station). ``climate``
# is control too, but the cloud path never exposes it, so it is only
# considered on the native side.
CONTROL_DOMAINS = frozenset({"switch", "light", "cover"})
NATIVE_CONTROL_DOMAINS = CONTROL_DOMAINS | frozenset({"climate"})

# ── "No longer in account" detector (detect_orphans) ───────────────
#
# Mass-absence guard: if more than ``max(ORPHAN_FLOOR_ABS,
# ORPHAN_FLOOR_FRAC * managed_devices)`` of our managed devices look absent
# from the account inventory in a single run, we treat the run as a likely
# transient account/API glitch and refuse to act (report-only, degraded).
ORPHAN_FLOOR_ABS = 5
ORPHAN_FLOOR_FRAC = 0.25

# ── Persistent storage keys ────────────────────────────────────────

# Map of Shelly device_id → hostname, kept in entry.data so platforms can
# resolve devices before the first successful poll (e.g. on HA restart).
CONF_KNOWN_DEVICES = "known_devices"

# ── Platforms we publish ───────────────────────────────────────────

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.COVER,
    Platform.LIGHT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# ── Device-generation detection ────────────────────────────────────

# Gen2/Gen3 RPC devices expose keys like ``switch:0``, ``light:0``, etc.
# Battery sensor devices (e.g. Shelly H&T Gen3) expose only sensor
# components such as ``temperature:0``, ``humidity:0`` and ``devicepower:0``.
# Gen1 devices use legacy keys like ``relays``, ``meters``. BLE devices
# reported through Shelly BLU Gateway use the same ``humidity:0`` /
# ``temperature:0`` shape but are distinguished by ``_dev_info.gen == "GBLE"``
# (checked first in ``device_gen`` before this structural fallback).
_GEN2_PATTERN = re.compile(
    r"(switch|light|cover|input|cloud|sys|temperature|humidity"
    r"|devicepower|voltmeter):\d+"
)


def is_gen2_status(status: dict[str, Any]) -> bool:
    """Return True if the status dict looks like a Gen2/Gen3 RPC device."""
    if not status:
        return False
    return any(_GEN2_PATTERN.match(key) for key in status)


def device_gen(status: dict[str, Any]) -> str:
    """Return ``"G1"`` / ``"G2"`` / ``"GBLE"`` based on ``_dev_info.gen``.

    Falls back to structural inference if ``_dev_info`` is missing.
    """
    dev_info = status.get("_dev_info") if isinstance(status, dict) else None
    if isinstance(dev_info, dict):
        gen = dev_info.get("gen")
        if isinstance(gen, str) and gen:
            return gen
    return "G2" if is_gen2_status(status) else "G1"
