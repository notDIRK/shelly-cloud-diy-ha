"""Diagnostics for Shelly Cloud DIY.

Exposes the Stage 1 Fleet-Map table (cloud↔local MAC join, name
suggestions, resilience classification) as machine-readable diagnostics.
MACs and cloud device ids are reduced to a stable, non-reversible
fingerprint so a match stays verifiable without leaking the raw MAC; the
cloud-side alias is redacted because shared devices may carry another
account's naming.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_CREATE_ALL_INITIALLY,
    CONF_ENABLED_DEVICES,
    CONF_LOCAL_GATEWAY_URL,
    CONF_OFFLINE_AFTER,
    CONF_POLL_INTERVAL,
    CONF_RELAY_FAULT_DETECTION,
    DOMAIN,
    OFFLINE_AFTER_DEFAULT,
    POLL_INTERVAL_DEFAULT,
    RELAY_FAULT_DETECTION_DEFAULT,
)
from .coordinator import sleep_period_s
from .services.fleet_map import (
    fingerprint,
    compute_fleet,
    gather_cloud_devices,
    to_diagnostics,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry

TO_REDACT = {"cloud_name"}

# Options this module reports explicitly. Anything else the entry carries is
# listed BY NAME ONLY (never by value) so a future option cannot start
# leaking through diagnostics just because nobody updated this file.
KNOWN_OPTION_KEYS = frozenset(
    {
        CONF_POLL_INTERVAL,
        CONF_OFFLINE_AFTER,
        CONF_CREATE_ALL_INITIALLY,
        CONF_ENABLED_DEVICES,
        CONF_LOCAL_GATEWAY_URL,
        CONF_RELAY_FAULT_DETECTION,
    }
)

# Redacted from the per-device raw status: network identifiers and
# human-set names that could carry another account's naming (shared
# devices). The technical control fields (mode, white, gain, brightness,
# rgb, output, …) are deliberately kept — they are the point of the dump.
DEVICE_TO_REDACT = {"name", "cloud_name", "ssid", "sta_ip", "ip", "mac"}


def _config_diagnostics(
    entry: ConfigEntry, coordinator: Any | None
) -> dict[str, Any]:
    """Return what the integration is actually configured to do.

    Exists because the fleet map answers "what is out there" but not "why
    does this device have no entities" — and that question is settled by the
    options, not by the cloud. Reading them used to mean opening
    ``.storage/core.config_entries`` on the user's machine.

    Two rules hold here, in this order:

    * ``entry.data`` is never touched. The ``auth_key`` lives there, and a
      diagnostics file is something users paste into public issues.
    * device ids are fingerprinted with the same helper the fleet map uses,
      so an enabled device can still be matched to its fleet-map row without
      the raw MAC appearing anywhere.

    Values are the *effective* ones wherever the coordinator can supply them
    (its properties apply the defaults), because an option that is stored but
    not in force explains nothing.
    """
    options = dict(entry.options)

    raw_selection = options.get(CONF_ENABLED_DEVICES)
    selection = (
        [d for d in raw_selection if isinstance(d, str)]
        if isinstance(raw_selection, list)
        else None
    )
    create_all = (
        coordinator.create_all_initially
        if coordinator is not None
        else bool(options.get(CONF_CREATE_ALL_INITIALLY, False))
    )

    devices: dict[str, Any]
    if coordinator is None:
        devices = {"note": "coordinator not ready"}
    else:
        snapshot = set(coordinator.devices)
        enabled = {d for d in snapshot if coordinator.is_enabled(d)}
        devices = {
            "in_snapshot": len(snapshot),
            "enabled": len(enabled),
            # The support answer in one number: devices the cloud serves us
            # that produce no entities because the options gate them out.
            "gated_out": len(snapshot - enabled),
        }

    gateway = options.get(CONF_LOCAL_GATEWAY_URL)

    return {
        "options": {
            "poll_interval_s": options.get(
                CONF_POLL_INTERVAL, POLL_INTERVAL_DEFAULT
            ),
            "offline_after_s": (
                coordinator.offline_after_s
                if coordinator is not None
                else options.get(CONF_OFFLINE_AFTER, OFFLINE_AFTER_DEFAULT) * 60
            ),
            "relay_fault_detection": (
                coordinator.relay_fault_detection
                if coordinator is not None
                else bool(
                    options.get(
                        CONF_RELAY_FAULT_DETECTION, RELAY_FAULT_DETECTION_DEFAULT
                    )
                )
            ),
            "create_all_initially": create_all,
            # A URL can carry an internal hostname, so only its presence is
            # reported — that is all the answer "is the local gateway wired
            # up?" needs.
            "local_gateway_url_set": bool(
                isinstance(gateway, str) and gateway.strip()
            ),
            "enabled_devices": {
                "mode": "all" if create_all else "selection",
                "selected": len(selection) if selection is not None else None,
                "fingerprints": (
                    sorted(fingerprint(d) for d in selection)
                    if selection is not None and not create_all
                    else None
                ),
            },
            "other_option_keys": sorted(set(options) - KNOWN_OPTION_KEYS),
        },
        "devices": devices,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    ready = coordinator is not None and getattr(
        coordinator, "last_update_success", False
    )
    # The configuration is reported either way: an entry that never came up
    # is exactly when knowing what it was told to do matters most.
    config = _config_diagnostics(entry, coordinator if ready else None)

    if not ready:
        return {"fleet_map": None, "note": "coordinator not ready", **config}

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    cloud_devices = await gather_cloud_devices(coordinator)
    fleet, suggestions, resilience = compute_fleet(
        hass, cloud_devices, dev_reg, ent_reg
    )
    return {
        "fleet_map": async_redact_data(
            to_diagnostics(fleet, suggestions, resilience), TO_REDACT
        ),
        **config,
    }


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
) -> dict[str, Any]:
    """Return the raw Shelly Cloud status snapshot for a single device.

    The coordinator builds every entity's state from this record, so it is
    the ground truth when a device's behaviour needs the exact field values
    to reproduce (e.g. RGBW2 white/color dimming). Downloadable per device
    via Settings -> device -> Download diagnostics.
    """
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        return {"note": "coordinator not ready"}

    device_id = next(
        (ident[1] for ident in device.identifiers if ident[0] == DOMAIN),
        None,
    )
    if device_id is None:
        return {"note": "no Shelly Cloud DIY identifier on this device"}

    coordinator_health = {
        "last_update_success": getattr(
            coordinator, "last_update_success", None
        ),
        "last_error": (
            str(coordinator.last_exception)
            if getattr(coordinator, "last_exception", None)
            else None
        ),
    }

    record = coordinator.devices.get(device_id)
    if record is None:
        return {
            "device_id": device_id,
            "coordinator": coordinator_health,
            "note": "device not in the current coordinator snapshot",
            # Included here in particular: a device missing from the poll is
            # exactly the case the reporting verdict exists to explain.
            "reporting": _reporting_diagnostics(coordinator, device_id),
        }

    return {
        "device_id": device_id,
        "coordinator": coordinator_health,
        # Transparency: exactly which keys are stripped from `record` below,
        # so anyone attaching this to a bug report knows what was withheld.
        "redacted_keys": sorted(DEVICE_TO_REDACT),
        "sleep": _sleep_diagnostics(record),
        "reporting": _reporting_diagnostics(coordinator, device_id),
        "record": async_redact_data(record, DEVICE_TO_REDACT),
    }


def _reporting_diagnostics(
    coordinator: Any, device_id: str
) -> dict[str, Any] | None:
    """Explain the "Reporting" verdict in numbers a reader can check.

    The record holds monotonic timestamps, which say nothing in a dump. What
    a bug report needs is: how long has this device actually been silent, how
    much silence is it allowed, and is the allowance the configured one or a
    wider one this device earned by being naturally quiet.
    """
    checkins = getattr(coordinator, "checkins", None)
    if not isinstance(checkins, dict):
        return None
    checkin = checkins.get(device_id)
    if checkin is None:
        return None

    return {
        "silent_for_s": round(time.monotonic() - checkin.last_checkin, 1),
        "stale_after_s": round(checkin.stale_after_s, 1),
        "base_window_s": round(checkin.base_window_s, 1),
        "widest_observed_gap_s": round(checkin.widest_gap_s, 1),
        "cadence_learned": checkin.widest_gap_s > 0,
        "missing_from_last_poll": checkin.absent,
        "reporting": checkin.is_reporting(time.monotonic()),
    }


def _sleep_diagnostics(record: dict[str, Any]) -> dict[str, Any] | None:
    """Explain the availability verdict for a deep-sleep battery device.

    The record carries ``sleep_stale_at`` as a raw monotonic timestamp, which
    means nothing in a dump. Resolving it to "seconds left before this device
    is considered gone" makes an availability report self-diagnosing. Returns
    ``None`` for mains devices, where the cloud's ``online`` flag decides. (#13)
    """
    if not record.get("sleeping"):
        return None

    stale_at = record.get("sleep_stale_at")
    return {
        "wakeup_period_s": sleep_period_s(record.get("status") or {}),
        "seconds_until_considered_gone": (
            round(stale_at - time.monotonic(), 1)
            if isinstance(stale_at, (int, float))
            else None
        ),
    }
