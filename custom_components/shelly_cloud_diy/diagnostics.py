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
    CLOUD_CONTROL_DEFAULT,
    CONF_CLOUD_CONTROL,
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
    device_gen,
    is_gen2_status,
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
        CONF_CLOUD_CONTROL,
    }
)

# Redacted from the per-device raw status: network identifiers and
# human-set names that could carry another account's naming (shared
# devices). The technical control fields (mode, white, gain, brightness,
# rgb, output, …) are deliberately kept — they are the point of the dump.
DEVICE_TO_REDACT = {"name", "cloud_name", "ssid", "sta_ip", "ip", "mac"}

# Top-level status keys the coverage report never counts as a gap. Each was
# judged on its own; the list is short on purpose, because every key excluded
# here is a gap that can no longer be seen.
#
#   _updated, _dev_info   written by our own coordinator, not by the device
#   serial, ts            the payload's revision counter and its timestamp
#   id, code              the device's own component id and model code
#
# Deliberately NOT excluded, although both are tempting:
#
#   sys        carries uptime, RAM and filesystem headroom. The pending
#              firmware version became an entity, but that entity claims no
#              status key on purpose (see ShellyFirmwareUpdateBinarySensor):
#              this report judges a top-level block whole, and the three
#              readings above still surface nowhere.
#   reporter   the bridging gateway's RSSI, and on a BLU device the only
#              signal figure there is. Surfaced by the gateway signal sensor
#              now — but only while the gateway reports a usable reading, so
#              the key still has to be able to appear as a gap.
STRUCTURAL_STATUS_KEYS = frozenset(
    {"_updated", "_dev_info", "serial", "ts", "id", "code"}
)


def _cloud_control_diagnostics(
    options: dict[str, Any], coordinator: Any | None
) -> dict[str, Any]:
    """Explain the opt-in control channel, verdict by verdict.

    This block exists for one support question that has no other answer:
    *why does this device have no switch?* The channel refuses three
    different ways — the option is off, the relay never came up, or the
    relay will not route to that particular device — and from the outside
    all three look identical: a device with a read-only sensor and nothing
    to press.

    The same two rules as the block below hold, in the same order: nothing
    from ``entry.data`` is read (the OAuth token lives there, and a
    diagnostics file is something users paste into public issues), and
    device ids appear only as fleet-map fingerprints. The token's existence
    is not reported either — "is there a token" is answered well enough by
    whether the channel connected, and every extra sentence about a stored
    credential is one more thing to get wrong later.
    """
    # The coordinator's reading where there is one, so this block reports the
    # option as it is in force rather than as it is stored.
    enabled = getattr(coordinator, "cloud_control", None)
    if not isinstance(enabled, bool):
        enabled = bool(options.get(CONF_CLOUD_CONTROL, CLOUD_CONTROL_DEFAULT))
    report: dict[str, Any] = {"mode": "on" if enabled else "off"}
    if not enabled or coordinator is None:
        return report

    ownership = getattr(coordinator, "device_ownership", None)
    if not isinstance(ownership, dict):
        return {**report, "note": "coordinator carries no ownership verdicts"}
    unresolved = getattr(coordinator, "cloud_control_unclassified", None)
    unresolved = unresolved if isinstance(unresolved, (set, frozenset)) else set()

    verdicts = {
        fingerprint(device_id): getattr(verdict, "value", str(verdict))
        for device_id, verdict in ownership.items()
    }
    # Deliberately reported as their own value rather than folded in with the
    # definite ones: "we could not tell" is a state that resolves itself on
    # the next probe, and reading it as a verdict is the exact mistake the
    # coordinator refuses to make.
    verdicts.update({fingerprint(device_id): "unknown" for device_id in unresolved})

    return {
        **report,
        "connected": bool(getattr(coordinator, "cloud_control_connected", False)),
        "owned": sum(1 for v in verdicts.values() if v == "owned"),
        "not_routable": sum(1 for v in verdicts.values() if v == "not_routable"),
        "unclassified": len(unresolved),
        "verdicts": dict(sorted(verdicts.items())),
    }


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
        "cloud_control": _cloud_control_diagnostics(options, coordinator),
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
        "coverage": _coverage_diagnostics(
            coordinator, device_id, record.get("status") or {}
        ),
        "record": async_redact_data(record, DEVICE_TO_REDACT),
    }


def _entity_source_keys(entity: Any, status: dict[str, Any]) -> set[str]:
    """Return the top-level status keys one entity reads.

    Every entity stores the key it was built from, but under a different
    attribute name per class — ``_component_key`` on the RPC entities,
    ``_status_key`` on the Block and BLE ones — and the Gen1 entities whose
    reading is a bare top-level flag (``motion``, ``flood``) keep it in
    ``_attr_key`` with no container at all. Reading all three and keeping
    only what is actually a key of this payload covers every shape without
    the report having to know the class hierarchy.
    """
    keys: set[str] = set()
    for attribute in ("_component_key", "_status_key", "_attr_key"):
        value = getattr(entity, attribute, None)
        if isinstance(value, str) and value in status:
            keys.add(value)
    return keys


def _coverage_diagnostics(
    coordinator: Any, device_id: str, status: dict[str, Any]
) -> dict[str, Any]:
    """Report which parts of this device's payload produce no entity.

    Derived from the builders, not from the description tables. That
    distinction is the whole point: all four dead-description bugs (#38,
    #41, #42 and the Wi-Fi RSSI) were entries that existed in a table while
    no builder ever looked them up, so a table-derived report would have
    called every one of them "covered" and found nothing.

    So the real creation functions run here, against a throwaway ``created``
    set, and the answer is assembled from the entities they actually return.
    The two device-wide binary sensors (Reporting, Relay fault) are left out
    because they derive from no single status key and would only blur the
    picture. The firmware flag is left out for the neighbouring reason: it
    reads one field of ``sys`` and claiming the whole block would hide the
    three readings in it that still produce nothing.

    Key NAMES only, never values — this block sits next to a redacted dump
    and must not become a way around it.

    Never raises. A user downloads this from the device page to attach to a
    bug report; a coverage report that cannot be produced is worth far less
    than the raw status next to it, so any failure degrades to an ``error``
    string and the rest of the file survives.
    """
    try:
        if not status:
            return {"note": "no status in the current snapshot"}

        # Imported here rather than at module scope: the platforms pull in
        # the whole entity stack, and a diagnostics module has no business
        # making that a hard import for every start.
        from . import binary_sensor as binary_sensor_platform
        from . import sensor as sensor_platform

        # The same dispatch the two platforms make, so the report is judged
        # against the builders that would really have run for this device.
        # Labels are fixed here rather than read off the function, so a
        # failure names the builder that was meant to run.
        gen = device_gen(status)
        if gen == "GBLE":
            builders = (
                ("sensor.ble", sensor_platform._create_ble_sensors),
                (
                    "binary_sensor.ble",
                    binary_sensor_platform._create_ble_binary_sensors,
                ),
            )
        elif is_gen2_status(status):
            builders = (
                ("sensor.rpc", sensor_platform._create_rpc_sensors),
                ("binary_sensor.rpc", binary_sensor_platform._create_rpc_sensors),
            )
        else:
            builders = (
                ("sensor.block", sensor_platform._create_block_sensors),
                ("binary_sensor.block", binary_sensor_platform._create_block_sensors),
            )

        covered: set[str] = set()
        entity_count = 0
        builder_errors: list[str] = []

        for label, build in builders:
            # A fresh throwaway set per builder, because that is what
            # production does: the sensor and binary-sensor platforms each
            # keep their own. Sharing one here could let a unique-id collision
            # between the two swallow an entity and invent a gap.
            try:
                entities = build(device_id, status, set(), coordinator)
            except Exception as err:  # noqa: BLE001 - see docstring
                builder_errors.append(f"{label}: {type(err).__name__}")
                continue
            entity_count += len(entities)
            for entity in entities:
                covered |= _entity_source_keys(entity, status)

        uncovered = sorted(
            key
            for key in status
            if key not in covered and key not in STRUCTURAL_STATUS_KEYS
        )

        report: dict[str, Any] = {
            "generation": gen,
            "entities_built": entity_count,
            "covered_keys": sorted(covered),
            "uncovered_keys": uncovered,
            "uncovered_count": len(uncovered),
            "ignored_keys": sorted(
                key for key in status if key in STRUCTURAL_STATUS_KEYS
            ),
        }
        if builder_errors:
            report["builder_errors"] = builder_errors
        return report
    except Exception as err:  # noqa: BLE001 - see docstring
        return {"error": f"{type(err).__name__}: {err}"}


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
