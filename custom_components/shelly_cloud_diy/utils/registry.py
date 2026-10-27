"""Device-registry lookups that work on every supported Home Assistant.

HA 2026.10 deprecated ``DeviceRegistry.async_get_device`` (removed in
2027.8.0): identifiers are unique per config entry now, not globally, so a
lookup has to say which entry it means. The replacement,
``async_get_device_by_identifier``, does not exist on older releases, and the
minimum this integration supports is 2025.1.4.
"""
from __future__ import annotations

from homeassistant.helpers import device_registry as dr

from ..const import DOMAIN


def async_get_own_device(
    dev_reg: dr.DeviceRegistry, device_id: str, config_entry_id: str
) -> dr.DeviceEntry | None:
    """Return this integration's device for a Shelly id, owned by the entry.

    Uses the per-entry lookup where HA has it. Older releases only know the
    global lookup; there an identifier can only belong to one device anyway.
    """
    identifier = (DOMAIN, device_id)
    by_identifier = getattr(dev_reg, "async_get_device_by_identifier", None)
    if by_identifier is not None:
        return by_identifier(identifier, config_entry_id)
    return dev_reg.async_get_device(identifiers={identifier})
