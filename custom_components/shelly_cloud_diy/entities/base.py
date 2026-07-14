"""Base entity class for Shelly Cloud DIY.

Provides shared functionality for all Shelly entities.
"""
from __future__ import annotations

import logging
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
        """Return if entity is available."""
        return self.device_data.get("online", False)
