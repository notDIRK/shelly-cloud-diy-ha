"""The Cloud diagnostic binary sensor is disabled by default (issue #13 follow-up).

``cloud.connected`` is served from the cached cloud snapshot. On a deep-sleep
device that snapshot is captured seconds after wake, before the cloud session is
established, so the flag reads false permanently while the device is
demonstrably reaching the cloud — the reporter on #13 saw exactly that after the
sensors themselves were fixed. HA core's native Shelly integration ships the
same sensor with ``entity_registry_enabled_default=False``; these tests pin that
we match it, and that no other binary sensor was swept up in the change.
"""
from __future__ import annotations

from typing import Any

from custom_components.shelly_cloud_diy.binary_sensor import (
    _create_rpc_sensors as create_rpc_binary_sensors,
)
from custom_components.shelly_cloud_diy.entities.descriptions import (
    RPC_BINARY_SENSORS,
)

DEVICE_ID = "aabbccddeeff"


class _FakeCoordinator:
    def __init__(self, device_id: str, status: dict[str, Any]) -> None:
        self.devices = {
            device_id: {
                "status": status,
                "device_code": "S3SN-0U12A",
                "online": True,
            }
        }
        self.data = self.devices
        self.last_update_success = True


# A deep-sleep H&T as it actually arrives: cloud reports disconnected in the
# same record that carries current readings.
_SLEEPING_STATUS: dict[str, Any] = {
    "sys": {"mac": "AABBCCDDEEFF", "wakeup_period": 7200},
    "cloud": {"connected": False},
    "input:0": {"state": True},
}


def _build() -> dict[str, Any]:
    coordinator = _FakeCoordinator(DEVICE_ID, _SLEEPING_STATUS)
    entities = create_rpc_binary_sensors(
        DEVICE_ID, _SLEEPING_STATUS, set(), coordinator
    )
    return {entity.unique_id: entity for entity in entities}


def test_cloud_sensor_is_still_created() -> None:
    """The entity must keep existing — disabled, not removed.

    Dropping it would strand the registry entries of every existing install.
    """
    assert f"{DEVICE_ID}_cloud_connected" in _build()


def test_cloud_sensor_disabled_by_default() -> None:
    """It must not be enabled on a fresh install."""
    entity = _build()[f"{DEVICE_ID}_cloud_connected"]
    assert entity.entity_registry_enabled_default is False


def test_cloud_sensor_still_reports_the_raw_flag() -> None:
    """Disabling the default must not change what the sensor says when enabled.

    The value is honest; it is only a poor signal for a sleeping device.
    """
    entity = _build()[f"{DEVICE_ID}_cloud_connected"]
    assert entity.is_on is False


def test_other_binary_sensors_stay_enabled() -> None:
    """Only the cloud sensor changed — the input sensor is user-facing."""
    entity = _build()[f"{DEVICE_ID}_input:0_state"]
    assert entity.entity_registry_enabled_default is True


def test_descriptions_default_to_enabled() -> None:
    """The new dataclass field must not silently disable anything else."""
    disabled = {
        key
        for key, desc in RPC_BINARY_SENSORS.items()
        if not desc.entity_registry_enabled_default
    }
    assert disabled == {"cloud"}
