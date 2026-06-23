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
from .services.fleet_map import compute_fleet, to_diagnostics

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

TO_REDACT = {"cloud_name"}


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
    fleet, suggestions, resilience = compute_fleet(
        hass, coordinator, dev_reg, ent_reg
    )
    return {
        "fleet_map": async_redact_data(
            to_diagnostics(fleet, suggestions, resilience), TO_REDACT
        )
    }
