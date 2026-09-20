"""Unit tests for the Gen2+ smoke alarm binary sensor (issue #47).

A Shelly Plus Smoke reports its detection as an RPC component,
``smoke:<id> = {"alarm": bool, ...}`` — the same shape the Gen4 flood sensor
uses. Before this, the RPC creation loop knew ``flood:<id>`` and not
``smoke:<id>``, so the detector arrived in Home Assistant with its battery,
firmware, reporting and Wi-Fi diagnostics and **without the one reading a smoke
detector exists for**.

That is the third report of this exact shape (#41 Gen4 flood, #42 Gen1
flood/smoke, #47 Gen2+ smoke), so the builder is now a table and these tests
guard both rows of it: adding the fourth must not silently drop the first.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.shelly_cloud_diy.binary_sensor import (
    RpcBinarySensor,
    _create_rpc_sensors,
)
from custom_components.shelly_cloud_diy.const import is_gen2_status

DEVICE_ID = "a0dd6cffee02"


class _FakeCoordinator:
    """Minimal coordinator: entities only read ``.devices[id]['status']``."""

    def __init__(self, device_id: str, status: dict[str, Any]) -> None:
        self.devices = {
            device_id: {
                "status": status,
                "device_code": "SNSN-0031Z",
                "online": True,
            }
        }
        self.data = self.devices
        self.last_update_success = True


def _smoke_status(alarm: Any = False) -> dict[str, Any]:
    """The shape a Gen2+ smoke detector reports (issue #47)."""
    return {
        "sys": {"mac": "A0DD6CFFEE02"},
        "cloud": {"connected": True},
        "smoke:0": {"id": 0, "alarm": alarm, "mute": False},
        "devicepower:0": {"battery": {"V": 3.0, "percent": 92}},
    }


def _build(status: dict[str, Any], created: set[str] | None = None):
    coord = _FakeCoordinator(DEVICE_ID, status)
    entities = _create_rpc_sensors(
        DEVICE_ID, status, set() if created is None else created, coord
    )
    return entities, {e.unique_id: e for e in entities}


def test_a_smoke_detector_is_recognised_as_gen2():
    """Detection must not lean on the incidental ``devicepower:0``."""
    assert is_gen2_status({"smoke:0": {"alarm": False}}) is True
    assert is_gen2_status(_smoke_status()) is True


def test_the_smoke_alarm_entity_is_created():
    """The reported symptom: battery and diagnostics arrived, the alarm did not."""
    entities, by_uid = _build(_smoke_status())

    uid = f"{DEVICE_ID}_smoke:0_alarm"
    assert uid in by_uid, [e.unique_id for e in entities]

    entity = by_uid[uid]
    assert isinstance(entity, RpcBinarySensor)
    assert entity.device_class is BinarySensorDeviceClass.SMOKE
    assert entity.name == "Smoke"
    assert entity.entity_registry_enabled_default is True


def test_the_alarm_value_is_mirrored():
    _, clear = _build(_smoke_status(alarm=False))
    assert clear[f"{DEVICE_ID}_smoke:0_alarm"].is_on is False

    _, alarming = _build(_smoke_status(alarm=True))
    assert alarming[f"{DEVICE_ID}_smoke:0_alarm"].is_on is True


def test_a_component_without_a_reading_creates_nothing():
    """An entity that could only ever be ``None`` is worse than none."""
    status = _smoke_status()
    status["smoke:0"] = {"id": 0, "mute": False}
    _, by_uid = _build(status)
    assert f"{DEVICE_ID}_smoke:0_alarm" not in by_uid


def test_several_detectors_on_one_device_each_get_an_entity():
    status = _smoke_status()
    status["smoke:1"] = {"id": 1, "alarm": True}
    _, by_uid = _build(status)

    assert by_uid[f"{DEVICE_ID}_smoke:0_alarm"].is_on is False
    assert by_uid[f"{DEVICE_ID}_smoke:1_alarm"].is_on is True


def test_flood_and_smoke_on_one_device_do_not_shadow_each_other():
    """The builder is a table now; one row must not consume the other's keys."""
    status = _smoke_status(alarm=True)
    status["flood:0"] = {"id": 0, "alarm": False}

    _, by_uid = _build(status)

    assert by_uid[f"{DEVICE_ID}_smoke:0_alarm"].device_class is BinarySensorDeviceClass.SMOKE
    assert by_uid[f"{DEVICE_ID}_flood:0_alarm"].device_class is BinarySensorDeviceClass.MOISTURE
