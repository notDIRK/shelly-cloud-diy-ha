"""Base entity class for Shelly Cloud DIY.

Provides shared functionality for all Shelly entities.
"""
from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN, is_gen2_status
from .descriptions import get_model_name

if TYPE_CHECKING:
    from ..coordinator import ShellyCloudCoordinator

_LOGGER = logging.getLogger(__name__)

# How long an optimistic value overrides cloud reads after a command. The
# Shelly Cloud can take several seconds to propagate a just-issued change
# (notably on RGBW2 channels); without this a lagging poll reverts the
# optimistic state, causing a visible flicker / slider snap-back. The override
# is cleared early as soon as the cloud confirms the value. (#6)
OPTIMISTIC_GRACE_S = 10.0

# Irrigation controllers (FRANKEVER FK-06X and friends) expose each zone as a
# virtual boolean whose ``role`` is ``zone0``…``zoneN``. Those components carry
# no useful ``name`` of their own — the name the user typed lives in the
# device's ``service:0`` block, indexed by the zone number. Mirrors the native
# HA Shelly integration (``utils.py::get_irrigation_zone_id``). (#20)
_ZONE_ROLE_RE = re.compile(r"^zone(\d+)$")
_SERVICE_CONFIG_KEY = "service:0"


class ShellyBaseEntity(CoordinatorEntity["ShellyCloudCoordinator"]):
    """Base class for Shelly entities.

    Provides:
    - Shared device_info property
    - Availability based on online status
    - Common initialization
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ShellyCloudCoordinator,
        device_id: str,
        channel: int = 0,
    ) -> None:
        """Initialize the base entity.

        Args:
            coordinator: Data update coordinator
            device_id: Shelly Cloud device ID
            channel: Device channel (0-indexed)
        """
        super().__init__(coordinator)
        self._device_id = device_id
        self._channel = channel
        # Optimistic-state override: intended component fields that win over
        # cloud reads until confirmed or the grace window expires. (#6)
        self._optimistic: dict[str, Any] = {}
        self._optimistic_deadline = 0.0

    def _set_optimistic(self, values: dict[str, Any]) -> None:
        """Record an optimistic state and refresh the entity immediately.

        ``values`` are raw status-component fields (e.g. ``{"output": True,
        "brightness": 50}`` for Gen2, ``{"ison": True}`` for Gen1) so they
        overlay cleanly onto the component dict a reader passes to
        :meth:`_apply_optimistic`. (#6)
        """
        self._optimistic = dict(values)
        self._optimistic_deadline = time.monotonic() + OPTIMISTIC_GRACE_S
        self.async_write_ha_state()

    def _apply_optimistic(self, component: dict[str, Any]) -> dict[str, Any]:
        """Overlay any active optimistic values onto a cloud component dict.

        The overlay holds until the grace window expires or the cloud confirms
        every optimistic field — whichever comes first. So a poll that lands
        before the Shelly Cloud has propagated a just-issued command cannot
        revert the entity, but a genuinely different cloud state wins once
        propagation catches up. (#6)
        """
        if not self._optimistic:
            return component
        if time.monotonic() >= self._optimistic_deadline:
            self._optimistic = {}
            return component
        if all(component.get(k) == v for k, v in self._optimistic.items()):
            # Cloud caught up — drop the override so it stays authoritative.
            self._optimistic = {}
            return component
        return {**component, **self._optimistic}

    @property
    def device_data(self) -> dict[str, Any]:
        """Get device data from coordinator."""
        return self.coordinator.devices.get(self._device_id, {})

    @property
    def device_status(self) -> dict[str, Any]:
        """Get device status from coordinator."""
        return self.device_data.get("status", {})

    def virtual_component_config(self, component_key: str) -> dict[str, Any] | None:
        """Return the cached v2 config for a virtual component, or None.

        The coordinator fetches per-component config (name / unit / enum
        options) lazily in the background via the v2 settings endpoint and
        caches it under ``coordinator.virtual_configs[device_id][key]``. This
        helper reads that cache with full None-safety: a missing cache
        (e.g. before the background fetch completes, or a coordinator without
        the attribute at all) returns ``None`` so callers fall back to their
        generic behaviour. (#9)
        """
        configs = getattr(self.coordinator, "virtual_configs", None)
        if not isinstance(configs, dict):
            return None
        device_configs = configs.get(self._device_id)
        if not isinstance(device_configs, dict):
            return None
        config = device_configs.get(component_key)
        return config if isinstance(config, dict) else None

    def virtual_component_name(self, component_key: str) -> str | None:
        """Return the display name for a virtual component, or None.

        Two sources, checked in the order the native HA Shelly integration
        uses:

        1. **Irrigation zones** — a component whose ``role`` is ``zone<N>``
           has no meaningful ``name`` of its own; the zone name the user typed
           lives in ``service:0.zones[N].name``. (#20)
        2. The component's own ``name``, set in the Shelly app. (#9)

        Returns ``None`` when neither is available — before the background v2
        config fetch completes, or when the cloud simply does not carry these
        fields — so callers keep their generic fallback name.
        """
        config = self.virtual_component_config(component_key)
        if not isinstance(config, dict):
            return None

        if zone_name := self._irrigation_zone_name(config):
            return zone_name

        name = config.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return None

    def _irrigation_zone_name(self, config: dict[str, Any]) -> str | None:
        """Resolve a zone name from the device's ``service:0`` block, or None.

        Every step is guarded: the ``role`` field is not guaranteed to survive
        the cloud's ``settings`` view, and a controller may report fewer zone
        entries than it has zone components.
        """
        role = config.get("role")
        if not isinstance(role, str):
            return None
        match = _ZONE_ROLE_RE.match(role)
        if not match:
            return None

        service = self.virtual_component_config(_SERVICE_CONFIG_KEY)
        if not isinstance(service, dict):
            return None
        zones = service.get("zones")
        if not isinstance(zones, list):
            return None

        index = int(match.group(1))
        if index >= len(zones):
            return None
        zone = zones[index]
        if not isinstance(zone, dict):
            return None
        name = zone.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return None

    @property
    def is_gen2(self) -> bool:
        """Check if device is Gen2/Gen3."""
        return is_gen2_status(self.device_status)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for registry."""
        device_data = self.device_data
        device_code = device_data.get("device_code", "")
        status = device_data.get("status", {})

        # Get name from multiple sources
        name = self._get_device_name(device_data, status)

        # Get model name from device code
        model = get_model_name(device_code) if device_code else "Unknown"

        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=name,
            manufacturer="Shelly",
            model=model,
        )

    def _get_device_name(
        self,
        device_data: dict[str, Any],
        status: dict[str, Any],
    ) -> str:
        """Get device name from available sources.

        Appends the full device ID so that every device is
        uniquely identifiable in the UI and in entity IDs.

        Examples:
            "Shelly Plus 1 (53640421991792)"
                -> switch.shelly_plus_1_53640421991792_switch
            "Kitchen Light (80157669366571)"
                -> switch.kitchen_light_80157669366571_switch

        Priority for base name:
        1. Name from coordinator device data (user-set)
        2. Name from Gen2 sys.device.name
        3. Name from Gen1 getinfo.fw_info.device
        4. Model name from device code
        5. Fallback: "Shelly"
        """
        did = self._device_id

        # Priority 1: Stored name (user-set in Shelly Cloud)
        name = device_data.get("name")
        if name:
            return f"{name} ({did})"

        # Priority 2: Gen2 name
        if self.is_gen2:
            sys_info = status.get("sys", {}).get("device", {})
            name = sys_info.get("name")
            if name:
                return f"{name} ({did})"

        # Priority 3: Gen1 name
        getinfo = status.get("getinfo", {}).get("fw_info", {})
        name = getinfo.get("device")
        if name:
            return f"{name} ({did})"

        # Priority 4: Model name
        device_code = device_data.get("device_code", "")
        if device_code:
            return f"{get_model_name(device_code)} ({did})"

        # Priority 5: Fallback
        return f"Shelly ({did})"

    @property
    def available(self) -> bool:
        """Return if entity is available.

        Normally this is the cloud's transport flag. Deep-sleep battery
        devices are the exception: they are awake only a few seconds per
        wakeup period, and the snapshot Shelly Cloud caches for them is
        captured seconds after boot — before the cloud session is up — so
        their ``cloud.connected`` reads ``false`` permanently while the
        readings in that very snapshot are current. For those devices the
        coordinator stamps a deadline for the last check-in, and staying
        available means still being inside it. Same contract as the native
        Shelly integration in HA core. (#13)
        """
        device_data = self.device_data
        if device_data.get("online", False):
            return True
        if not device_data.get("sleeping"):
            return False
        stale_at = device_data.get("sleep_stale_at")
        if not isinstance(stale_at, (int, float)):
            return True
        return time.monotonic() < stale_at
