"""Diagnostics for Shelly Cloud DIY.

Exposes the Stage 1 Fleet-Map table (cloud↔local MAC join, name
suggestions, resilience classification) as machine-readable diagnostics.
MACs and cloud device ids are reduced to a stable, non-reversible
fingerprint so a match stays verifiable without leaking the raw MAC; the
cloud-side alias is redacted because shared devices may carry another
account's naming.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .services.fleet_map import compute_fleet, gather_cloud_devices, to_diagnostics

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceEntry

TO_REDACT = {"cloud_name"}

# Redacted from the per-device raw status: network identifiers and
# human-set names that could carry another account's naming (shared
# devices). The technical control fields (mode, white, gain, brightness,
# rgb, output, …) are deliberately kept — they are the point of the dump.
DEVICE_TO_REDACT = {"name", "cloud_name", "ssid", "sta_ip", "ip", "mac"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None or not getattr(
        coordinator, "last_update_success", False
    ):
        return {"fleet_map": None, "note": "coordinator not ready"}

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    cloud_devices = await gather_cloud_devices(coordinator)
    fleet, suggestions, resilience = compute_fleet(
        hass, cloud_devices, dev_reg, ent_reg
    )
    return {
        "fleet_map": async_redact_data(
            to_diagnostics(fleet, suggestions, resilience), TO_REDACT
        )
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

    record = coordinator.devices.get(device_id)
    if record is None:
        return {
            "device_id": device_id,
            "note": "device not in the current coordinator snapshot",
        }

    return {
        "device_id": device_id,
        "record": async_redact_data(record, DEVICE_TO_REDACT),
    }
