"""Constants for Shelly Cloud DIY."""
from __future__ import annotations

import re
from typing import Any

from homeassistant.const import Platform, UnitOfTemperature

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

# ── Offline detection ("Reporting" binary sensor) ──────────────────
#
# How long a mains-powered device may stay silent before its "Reporting"
# binary sensor drops to *disconnected*. This is only the BASE window: the
# coordinator widens it per device to cover the slowest cadence it has
# actually observed, so a naturally quiet device does not flap.
#
# The default is deliberately generous. Measured on a 64-device account
# (2026-08-16): 34 of 35 mains Shellys reported within 70 s — but a Plus
# RGBW PM sitting idle went 25 minutes between reports, with nothing wrong.
# A 3-minute default would have produced a permanent false alarm on it.
# Users who care about fast detection on a metering device (freezer plug,
# …) can tighten this; the per-device widening keeps the quiet devices sane.
CONF_OFFLINE_AFTER = "offline_after_minutes"
OFFLINE_AFTER_DEFAULT = 30
OFFLINE_AFTER_MIN = 2
OFFLINE_AFTER_MAX = 24 * 60

# ── Relay fault detection ("Relay fault" binary sensor) ────────────
#
# Watch every metered switching channel for the contradiction "relay
# reports open, meter reports a load" — a welded contact. On by default:
# the thresholds in ``relay_fault.py`` are deliberately conservative, and
# an actuator that can no longer switch off is worth an unsolicited word.
#
# The escape hatch exists for wiring the detector cannot see. A Shelly
# sitting in one leg of a two-way circuit can have current pushed through
# its meter by the *other* switch while its own relay is genuinely open,
# which looks identical from the payload. Whoever wired that knows; the
# integration cannot.
CONF_RELAY_FAULT_DETECTION = "relay_fault_detection"
RELAY_FAULT_DETECTION_DEFAULT = True

# ── Device health checks ("Doctor") ────────────────────────────────
#
# Threshold checks on fields the poll already returns: Wi-Fi signal,
# device temperature, free RAM and filesystem, a pending restart and any
# component reporting its own errors. No extra request, no extra
# credential — see ``device_health.py`` for the thresholds and for why
# Gen1 devices are not judged at all.
#
# On by default. Only a component's own heat is judged, never an external
# add-on probe — a sensor in a boiler flow pipe is hot by design and the
# payload cannot be told apart from an overheating device. The off switch
# stays for the cases no payload can reveal, the way the relay detector
# keeps one for two-way circuits.
CONF_DEVICE_HEALTH_DETECTION = "device_health_detection"
DEVICE_HEALTH_DETECTION_DEFAULT = True

# Firmware findings are OFF by default, and that is a measurement, not a
# preference: 24 of the 35 Gen2 devices on the account this was developed
# against had an update pending. A card that is permanently lit on two
# thirds of a fleet teaches the user to ignore the card — including the
# time it is reporting a device cooking at 85 °C.
CONF_DEVICE_HEALTH_FIRMWARE = "device_health_firmware"
DEVICE_HEALTH_FIRMWARE_DEFAULT = False

# ── Cloud control for owned devices (opt-in, off by default) ───────
#
# Sending a command to a virtual component means leaving the documented
# Cloud Control API: every documented HTTP route answers 404 for it
# (measured 2026-08-09, with a known-good ``set/switch`` answering 400 as
# the negative control), while the cloud WebSocket relay carries the
# device's own ``Boolean.Set`` and succeeds. That relay is undocumented,
# Shelly support declared it unsupported on 2026-07-27, it needs the
# account password once to mint an OAuth token, and it refuses to route to
# devices the account does not own.
#
# Four reasons for an option — and for it defaulting to OFF, unlike the
# relay-fault and health detectors, which only interpret data the poll
# already returns. This one adds a credential, a second connection and a
# channel that can be withdrawn without notice. A user has to be able to
# say no to that, and saying nothing has to mean no.
CONF_CLOUD_CONTROL = "cloud_control"
CLOUD_CONTROL_DEFAULT = False

# ``entry.data`` key holding the OAuth token minted from that sign-in.
# It lives beside the ``auth_key`` (the poll keeps using that one, and only
# that one) and never in ``entry.options``: options are handed around by
# the options flow, echoed into log lines on a reload and read by the
# diagnostics block, none of which may ever see a token.
#
# The password itself is stored NOWHERE. It is hashed at the flow boundary
# by ``oauth.sha1_password`` and the digest dies with the login request, so
# a dead refresh token genuinely means "ask the human" — see
# ``ShellyTokenManager``.
CONF_OAUTH_TOKEN = "oauth_token"  # noqa: S105 — a key name, not a secret

# Marker put into the reauth flow's data when it is the OAuth token that
# was rejected rather than the ``auth_key``. Without it the reauth form
# would ask for the wrong credential — both failures land in the same flow.
REAUTH_CLOUD_CONTROL = "cloud_control_reauth"

# How often the ownership re-probe wakes while any enabled device is still
# unclassified. Long, because the pass exists for one situation only: the
# relay was unreachable (or answered something unfamiliar) when a device
# was first asked about, so no verdict could be formed. The task exits as
# soon as every enabled device has one, so a healthy account pays nothing
# beyond the single probe pass at setup.
OWNERSHIP_REPROBE_INTERVAL_S = 15 * 60

# ── Historical sync (unchanged from pre-pivot) ─────────────────────

HISTORICAL_SYNC_INTERVAL = 24 * 60 * 60  # daily

# ── Dispatcher signals ─────────────────────────────────────────────

SIGNAL_NEW_DEVICE = f"{DOMAIN}_new_device"

# Fired when the user deletes a single device from the HA UI. Platforms use
# it to forget which entities they already built for that device, so that a
# later rediscovery starts from scratch. Rediscovery on its own must NOT
# reset that bookkeeping: ``/device/all_status`` omits devices spontaneously,
# so a healthy device is "rediscovered" routinely and rebuilding its entities
# would collide with the ones still live.
SIGNAL_DEVICE_REMOVED = f"{DOMAIN}_device_removed"

# ── Fleet-Map overlay (Stage 1) ────────────────────────────────────

# Domain of Home Assistant Core's native (local/LAN) Shelly integration.
# The Fleet-Map overlay joins our cloud devices to native ones purely by
# MAC, so we only need the domain string — never a Python import of the
# native integration's internals.
NATIVE_SHELLY_DOMAIN = "shelly"

# Entity domains that constitute *control* (as opposed to read-only
# sensing). Used by the resilience check to tell a controllable device
# from a sensor-only one (e.g. a shared WS90 weather station).
#
# ``climate`` was for a long time native-only, because the cloud path had no
# way to reach a thermostat. The BLU TRV support added in #48 changed that:
# a valve behind a BLU Gateway Gen3 is set over the cloud relay, so a climate
# entity of ours is real control and must count as such on both sides.
CONTROL_DOMAINS = frozenset({"switch", "light", "cover", "climate"})
NATIVE_CONTROL_DOMAINS = CONTROL_DOMAINS

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
    Platform.CLIMATE,
    Platform.COVER,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]

# ── Device-generation detection ────────────────────────────────────

# Gen2/Gen3 RPC devices expose keys like ``switch:0``, ``light:0``, etc.
# Battery sensor devices (e.g. Shelly H&T Gen3) expose only sensor
# components such as ``temperature:0``, ``humidity:0`` and ``devicepower:0``.
# Gen1 devices use legacy keys like ``relays``, ``meters``. BLE devices
# reported through Shelly BLU Gateway use the same ``humidity:0`` /
# ``temperature:0`` shape but are distinguished by ``_dev_info.gen == "GBLE"``
# (checked first in ``device_gen`` before this structural fallback).
#
# This inference is not a nicety: Shelly Cloud sends ``_dev_info`` only for
# BLE-bridged devices, so for every real Gen2/Gen3 Shelly this pattern is the
# *only* thing that routes the device to the RPC entity builders instead of
# the Gen1 block ones — and the block builders find nothing in an RPC status.
#
# The metering components are therefore listed explicitly. A relay-less
# meter (Shelly Pro 3EM) carries nothing but ``em:0`` / ``emdata:0`` and its
# device temperature, so it used to be recognised purely by the incidental
# ``temperature:0``; the 2-channel Pro EM-50 got by on its ``switch:0``.
# Neither is something to rely on, and a pure ``pm1`` meter (PM Mini Gen3)
# has neither to fall back on.
#
# ``cloud`` and ``sys`` were listed here but are *not* components: they carry
# no ``:<id>`` suffix, so those alternatives could never match and are gone.
# They must not be re-added with an optional index either — a Gen1 status has
# a bare ``cloud`` key too, which would classify every Gen1 device as Gen2.
#
# ``blutrv`` is the same case as ``smoke`` in #47: a Shelly BLU Gateway Gen3
# has no relay, no light and no meter — its only RPC components are the
# valves it carries, so without this alternative the whole gateway would be
# handed to the Gen1 builders. It matches ``blutrv:200`` and deliberately not
# ``blutrv_rstatus:200`` / ``blutrv_rinfo:200``, which are per-valve mirrors
# of the child's own status and info rather than components of the gateway.
_GEN2_PATTERN = re.compile(
    r"(switch|light|cover|input|temperature|humidity|flood|smoke|blutrv"
    r"|devicepower|voltmeter|em1data|em1|emdata|em|pm1"
    r"|boolean|number|enum|text|button):\d+"
)

# Shelly BLU TRV, carried by a BLU Gateway Gen3 as ``blutrv:<id>`` in the
# same 200+ range the virtual components use. Shared by the coordinator (the
# write path), the sensor platform (battery / signal) and the climate
# platform, so it lives here rather than being spelled three times.
BLUTRV_KEY_RE = re.compile(r"^blutrv:(\d+)$")

# The writable Gen2/Gen3 components behind the opt-in cloud relay, one regex
# each because every one of them is parsed for its id and then addressed by a
# different RPC method. Shared by the coordinator (which sends), the platforms
# (which build the entities) and the ownership probe (which decides whether a
# device is worth asking about), so they live here rather than in three files.
#
# ``script:<id>`` is the odd one out: it is not a virtual component at all, it
# is a real component of the device. It is listed here because it shares the
# one property that matters — the documented HTTP API cannot touch it and the
# relay can (measured 2026-09-27: ``Script.Start`` / ``Script.Stop``). (#48)
VIRTUAL_BOOLEAN_KEY_RE = re.compile(r"^boolean:(\d+)$")
VIRTUAL_NUMBER_KEY_RE = re.compile(r"^number:(\d+)$")
VIRTUAL_TEXT_KEY_RE = re.compile(r"^text:(\d+)$")
VIRTUAL_ENUM_KEY_RE = re.compile(r"^enum:(\d+)$")
SCRIPT_KEY_RE = re.compile(r"^script:(\d+)$")

# What a virtual number's range is when the v2 config has not arrived — or
# carries no ``min``/``max``, which is legal. Home Assistant's own default is
# 0–100, and a component holding 5000 would then be rejected by the entity
# before the write was ever attempted. Wide and honest beats narrow and wrong.
VIRTUAL_NUMBER_FALLBACK_MIN = -1e9
VIRTUAL_NUMBER_FALLBACK_MAX = 1e9

# A Shelly text component's own default cap, echoed by the device in
# ``max_len`` on the measured rig. Used only when the config is not (yet) here.
VIRTUAL_TEXT_FALLBACK_MAX_LEN = 255

# The valve's own setpoint range. Shelly's BLU TRV accepts 4–30 °C and
# answers anything outside it with an RPC error, so the write path stops
# there rather than spending a relay round trip to be told no.
BLUTRV_MIN_TEMP_C = 4.0
BLUTRV_MAX_TEMP_C = 30.0

# How long a call forwarded to a BLU TRV may take to answer. The gateway's
# docs say a ``BluTrv.Call`` response can take up to a minute, against the
# relay's default of ten seconds; a calibration alone runs up to ten. (#58)
BLUTRV_CALL_TIMEOUT_S = 60.0


# ── Physically impossible temperatures ─────────────────────────────
#
# Absolute zero, per unit. A reading below it cannot be a measurement, so it
# is a placeholder and is reported as "no reading" instead (#51).
#
# Measured: an ECOWITT WS90 behind a BLU gateway reported ``tC: -999`` twice,
# both times right after a Home Assistant restart (2026-10-06 12:07:41 and
# 2026-10-08 14:21:42 UTC), followed by a real value 5–10 s later. Passed
# through, that one poll landed in the history and pulled the hourly
# long-term statistics (min/mean) down to -999 for the whole hour.
#
# The rule is the physical limit, NOT a list of sentinels. -999 is the only
# placeholder ever observed; other values (-9999, 65535, …) are guesses and
# would be a hypothesis presented as a finding. The limit catches -999 and
# any other impossible value alike, and cannot reject a real reading.
#
# Strictly *below*: absolute zero itself is unattainable too, but nothing has
# ever reported it, and the line is the physical one rather than a margin.
_ABSOLUTE_ZERO: dict[str, float] = {
    UnitOfTemperature.CELSIUS: -273.15,
    UnitOfTemperature.FAHRENHEIT: -459.67,
    UnitOfTemperature.KELVIN: 0.0,
}


def plausible_temperature(
    value: Any, unit: str | None = UnitOfTemperature.CELSIUS
) -> Any:
    """Return ``value``, or ``None`` if it lies below absolute zero.

    Anything that is not a number — ``None``, a missing field — is returned
    unchanged: this judges physics, not shape. ``bool`` is not treated as a
    number, for the same reason as in ``device_health._as_number``. An
    unknown unit passes through, because there is no limit to judge it by.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    floor = _ABSOLUTE_ZERO.get(unit) if unit is not None else None
    if floor is not None and value < floor:
        return None
    return value


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
