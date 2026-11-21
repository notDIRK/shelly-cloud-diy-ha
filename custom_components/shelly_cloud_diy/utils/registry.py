"""Device-registry lookups that work on every supported Home Assistant.

HA 2026.10 deprecated ``DeviceRegistry.async_get_device`` (removed in
2027.8.0): identifiers are unique per config entry now, not globally, so a
lookup has to say which entry it means. The replacement,
``async_get_device_by_identifier``, does not exist on older releases, and the
minimum this integration supported at the time was 2025.1.4.

HA 2026.11 went further: a device belongs to exactly one config entry
(``DeviceEntry.config_entry_id``), so ``config_entries`` and
``primary_config_entry`` are deprecated (removed in 2027.10.0), and
``DeviceRegistry.devices`` is iterated for its entries instead of being used
as a mapping (removed in 2027.9.0). The helpers below give every call site
one answer on both sides of that change. (#69)
"""
from __future__ import annotations

from collections.abc import Mapping

from homeassistant.helpers import device_registry as dr

from ..const import DOMAIN

_MISSING = object()


def iter_devices(dev_reg: dr.DeviceRegistry) -> list[dr.DeviceEntry]:
    """Return every device entry in the registry.

    Before HA 2026.11 ``devices`` is a mapping and iterating it yields device
    ids; from 2026.11 iterating yields the entries and mapping access is
    deprecated. A list is returned so callers may mutate the registry while
    walking the result.
    """
    devices = dev_reg.devices
    if isinstance(devices, Mapping):
        return list(devices.values())
    return list(devices)


def device_config_entry_ids(device: dr.DeviceEntry) -> set[str]:
    """Return the ids of the config entries a device belongs to."""
    entry_id = getattr(device, "config_entry_id", _MISSING)
    if entry_id is _MISSING:
        # Before HA 2026.11: a device could belong to several entries.
        return set(device.config_entries)
    return {entry_id} if entry_id else set()


def device_primary_config_entry(device: dr.DeviceEntry) -> str | None:
    """Return the config entry that owns a device, ``None`` if unknown."""
    entry_id = getattr(device, "config_entry_id", _MISSING)
    if entry_id is _MISSING:
        return getattr(device, "primary_config_entry", None)
    return entry_id or None


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
