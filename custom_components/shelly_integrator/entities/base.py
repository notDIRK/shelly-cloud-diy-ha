"""Base entity class for Shelly Integrator.

Provides shared functionality for all Shelly entities.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ..const import DOMAIN, is_gen2_status
from .descriptions import get_model_name

if TYPE_CHECKING:
    from ..coordinator import ShellyIntegratorCoordinator

_LOGGER = logging.getLogger(__name__)


class ShellyBaseEntity(CoordinatorEntity["ShellyIntegratorCoordinator"]):
    """Base class for Shelly entities.

    Provides:
    - Shared device_info property
    - Availability based on online status
    - Common initialization
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ShellyIntegratorCoordinator,
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

        Always appends the last 6 digits of the device ID in parentheses
        so that multiple devices of the same model are distinguishable in
        both the UI and the auto-generated entity IDs.

        Examples:
            "Shelly Plus 1 (991792)"
                -> switch.shelly_plus_1_991792_switch
            "Kitchen Light (366571)"
                -> switch.kitchen_light_366571_switch

        Priority for base name:
        1. Name from coordinator device data (user-set)
        2. Name from Gen2 sys.device.name
        3. Name from Gen1 getinfo.fw_info.device
        4. Model name from device code
        5. Fallback: "Shelly"
        """
        suffix = self._device_id[-6:]

        # Priority 1: Stored name (user-set in Shelly Cloud)
        name = device_data.get("name")
        if name:
            return f"{name} ({suffix})"

        # Priority 2: Gen2 name
        if self.is_gen2:
            sys_info = status.get("sys", {}).get("device", {})
            name = sys_info.get("name")
            if name:
                return f"{name} ({suffix})"

        # Priority 3: Gen1 name
        getinfo = status.get("getinfo", {}).get("fw_info", {})
        name = getinfo.get("device")
        if name:
            return f"{name} ({suffix})"

        # Priority 4: Model name
        device_code = device_data.get("device_code", "")
        if device_code:
            return f"{get_model_name(device_code)} ({suffix})"

        # Priority 5: Fallback
        return f"Shelly ({suffix})"

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.device_data.get("online", False)
