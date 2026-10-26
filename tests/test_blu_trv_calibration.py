"""Unit tests for starting a BLU TRV calibration and reading its outcome (#58).

Two documented facts drive this, both from Shelly's BLU TRV API docs:

1. ``TRV.Calibrate`` with ``{id: 0}`` starts the stepper-motor calibration
   and returns nothing useful — success or failure shows up in the status.
2. A failed run leaves ``not_calibrated`` in ``trv:0.errors``: "calibration
   did not succeed. The TRV will not function properly."

The call travels like the setpoint write of #48: ``BluTrv.Call`` to the
gateway, the valve named by component id. The error list may sit flat in
``blutrv:<id>`` or only in the gateway's mirror ``blutrv_rstatus:<id>``
(#55); both shapes are read, flat first.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from custom_components.shelly_cloud_diy import binary_sensor as binary_sensor_platform
from custom_components.shelly_cloud_diy import button as button_platform
from custom_components.shelly_cloud_diy import diagnostics
from custom_components.shelly_cloud_diy.const import BLUTRV_CALL_TIMEOUT_S
from custom_components.shelly_cloud_diy.coordinator import ShellyCloudCoordinator

GATEWAY_ID = "34987a1b2c3d"


def _valve(component_id: int, **extra: Any) -> dict[str, Any]:
    """``blutrv:<id>`` with the link fields both real shapes carry."""
    return {
        "id": component_id,
        "rssi": -59,
        "battery": 100,
        "connected": True,
        "paired": True,
        "fw_ver": "v1.5.0",
        **extra,
    }


def _rstatus(errors: Any = None) -> dict[str, Any]:
    trv: dict[str, Any] = {"id": 0, "pos": 0, "current_C": 22.7, "target_C": 4}
    if errors is not None:
        trv["errors"] = errors
    return {"v": 39, "status": {"trv:0": trv}}


def _gateway(
    *, flat_errors: Any = None, remote_errors: Any = None, rstatus: bool = True
) -> dict[str, Any]:
    """A BLU Gateway Gen3 with two valves; errors placed on the first one."""
    first = _valve(200)
    if flat_errors is not None:
        first["errors"] = flat_errors
    status: dict[str, Any] = {
        "sys": {"mac": "34987A1B2C3D"},
        "blutrv:200": first,
        "blutrv:201": _valve(201),
    }
    if rstatus:
        status["blutrv_rstatus:200"] = _rstatus(remote_errors)
    return status


class _FakeRelay:
    """Records what would have gone over the cloud relay."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.sent: list[tuple[str, str, dict]] = []
        self.timeouts: list[float | None] = []

    async def send_jrpc_request(
        self,
        device_id: str,
        method: str,
        params: dict | None = None,
        *,
        timeout: float | None = None,
    ) -> dict:
        self.sent.append((device_id, method, params or {}))
        self.timeouts.append(timeout)
        if self._error is not None:
            raise self._error
        return {}


class _FakeCoordinator:
    """The slice of the coordinator these entities read."""

    def __init__(
        self,
        status: dict[str, Any],
        *,
        controllable: bool = True,
        connected: bool = True,
        relay: _FakeRelay | None = None,
    ) -> None:
        self.devices = {GATEWAY_ID: {"status": status, "online": True}}
        self.data = self.devices
        self.last_update_success = True
        self.virtual_configs: dict[str, dict[str, dict]] = {}
        self.blu_trv_names: dict[str, dict[str, str]] = {}
        self._controllable = controllable
        self.cloud_control_connected = connected
        self._cloud_ws = relay
        self.refreshes = 0

    def is_enabled(self, device_id: str) -> bool:
        return True

    def is_cloud_controllable(self, device_id: str) -> bool:
        return self._controllable

    async def async_request_refresh(self) -> None:
        self.refreshes += 1

    # The shipped write path, not a stand-in: it is the part worth testing.
    async_calibrate_blutrv = ShellyCloudCoordinator.async_calibrate_blutrv


def _buttons(status: dict[str, Any] | None = None, **kwargs: Any):
    status = status if status is not None else _gateway()
    coordinator = _FakeCoordinator(status, **kwargs)
    return coordinator, button_platform._create_blutrv_buttons(
        GATEWAY_ID, status, set(), coordinator
    )


def _calibration_sensors(status: dict[str, Any]) -> list[Any]:
    coordinator = _FakeCoordinator(status)
    entities = binary_sensor_platform._create_rpc_sensors(
        GATEWAY_ID, status, set(), coordinator
    )
    return [e for e in entities if e.unique_id.endswith("_calibration")]


# ── 1. The call ───────────────────────────────────────────────────────


def test_pressing_calibrate_wraps_trv_calibrate_for_the_gateway() -> None:
    """The documented call, asserted literally — ids, method and params."""
    relay = _FakeRelay()
    coordinator, buttons = _buttons(relay=relay)
    asyncio.run(buttons[1].async_press())

    assert relay.sent == [
        (
            GATEWAY_ID,
            "BluTrv.Call",
            {"id": 201, "method": "TRV.Calibrate", "params": {"id": 0}},
        )
    ]
    # A forwarded call may take up to a minute to answer (gateway docs).
    assert relay.timeouts == [BLUTRV_CALL_TIMEOUT_S]
    # The outcome is asked of the poll, never assumed.
    assert coordinator.refreshes == 1


def test_calibrate_refuses_without_the_cloud_relay() -> None:
    _, buttons = _buttons(relay=None)
    with pytest.raises(HomeAssistantError):
        asyncio.run(buttons[0].async_press())


def test_calibrate_refuses_a_gateway_the_relay_will_not_route_to() -> None:
    relay = _FakeRelay()
    _, buttons = _buttons(relay=relay, controllable=False)
    with pytest.raises(HomeAssistantError):
        asyncio.run(buttons[0].async_press())
    assert relay.sent == []


def test_calibrate_refuses_a_key_that_is_not_a_valve() -> None:
    relay = _FakeRelay()
    coordinator = _FakeCoordinator(_gateway(), relay=relay)
    with pytest.raises(HomeAssistantError):
        asyncio.run(coordinator.async_calibrate_blutrv(GATEWAY_ID, "blutrv_rstatus:200"))
    assert relay.sent == []


def test_a_refused_calibration_is_loud() -> None:
    relay = _FakeRelay(error=HomeAssistantError("relay said no"))
    _, buttons = _buttons(relay=relay)
    with pytest.raises(HomeAssistantError):
        asyncio.run(buttons[0].async_press())


# ── 2. The button ─────────────────────────────────────────────────────


def test_each_valve_gets_one_config_button_on_the_gateway_device() -> None:
    _, buttons = _buttons()
    assert [b.unique_id for b in buttons] == [
        f"{GATEWAY_ID}_blutrv:200_calibrate",
        f"{GATEWAY_ID}_blutrv:201_calibrate",
    ]
    assert all(b.entity_category is EntityCategory.CONFIG for b in buttons)
    # Same device as the climate entity: the gateway's.
    assert all(b._device_id == GATEWAY_ID for b in buttons)


def test_the_button_is_available_once_the_relay_will_route() -> None:
    _, buttons = _buttons()
    assert buttons[0].available is True


def test_the_button_is_unavailable_without_cloud_control() -> None:
    _, buttons = _buttons(connected=False)
    assert buttons[0].available is False


def test_the_button_is_unavailable_on_a_gateway_the_relay_will_not_route_to() -> None:
    _, buttons = _buttons(controllable=False)
    assert buttons[0].available is False


def test_the_button_is_unavailable_for_a_valve_off_the_radio_link() -> None:
    status = _gateway()
    status["blutrv:200"]["connected"] = False
    _, buttons = _buttons(status)
    assert buttons[0].available is False
    assert buttons[1].available is True


def test_a_gateway_without_valves_gets_no_button() -> None:
    _, buttons = _buttons({"sys": {}})
    assert buttons == []


# ── 3. The problem sensor ─────────────────────────────────────────────


def test_each_valve_gets_one_diagnostic_problem_sensor() -> None:
    sensors = _calibration_sensors(_gateway())
    assert [s.unique_id for s in sensors] == [
        f"{GATEWAY_ID}_blutrv:200_calibration",
        f"{GATEWAY_ID}_blutrv:201_calibration",
    ]
    assert all(s.device_class is BinarySensorDeviceClass.PROBLEM for s in sensors)
    assert all(s.entity_category is EntityCategory.DIAGNOSTIC for s in sensors)


@pytest.mark.parametrize("where", ["flat", "remote"])
def test_not_calibrated_turns_the_sensor_on(where: str) -> None:
    key = "flat_errors" if where == "flat" else "remote_errors"
    sensor = _calibration_sensors(_gateway(**{key: ["not_calibrated"]}))[0]
    assert sensor.is_on is True


@pytest.mark.parametrize("where", ["flat", "remote"])
def test_an_empty_or_unrelated_error_list_is_off(where: str) -> None:
    key = "flat_errors" if where == "flat" else "remote_errors"
    assert _calibration_sensors(_gateway(**{key: []}))[0].is_on is False
    assert _calibration_sensors(_gateway(**{key: ["low_battery"]}))[0].is_on is False


def test_no_error_list_anywhere_is_unknown_not_calibrated() -> None:
    """Negative control, one variable: the list is removed, nothing else."""
    sensors = _calibration_sensors(_gateway())
    assert sensors[0].is_on is None
    # The second valve has no rstatus block at all.
    assert sensors[1].is_on is None
    assert _calibration_sensors(_gateway(rstatus=False))[0].is_on is None


def test_the_flat_list_wins_over_the_remote_one() -> None:
    sensor = _calibration_sensors(
        _gateway(flat_errors=[], remote_errors=["not_calibrated"])
    )[0]
    assert sensor.is_on is False


def test_the_sensor_reads_without_cloud_control() -> None:
    status = _gateway(remote_errors=["not_calibrated"])
    coordinator = _FakeCoordinator(status, connected=False, controllable=False)
    entities = binary_sensor_platform._create_rpc_sensors(
        GATEWAY_ID, status, set(), coordinator
    )
    sensor = next(e for e in entities if e.unique_id.endswith("blutrv:200_calibration"))
    assert sensor.available is True
    assert sensor.is_on is True


def test_the_sensor_is_unavailable_for_a_valve_off_the_radio_link() -> None:
    status = _gateway(remote_errors=["not_calibrated"])
    status["blutrv:200"]["paired"] = False
    assert _calibration_sensors(status)[0].available is False


# ── 4. Names ──────────────────────────────────────────────────────────


def _attach_platform(entity: Any, translations: dict[str, str], english: dict[str, str]) -> None:
    platform = SimpleNamespace(
        platform_name="shelly_cloud_diy",
        domain="button",
        platform_translations=translations,
        object_id_platform_translations=english,
    )
    entity.platform = platform
    entity.platform_data = platform


def test_names_carry_the_valve_position_or_alias() -> None:
    coordinator, buttons = _buttons()
    assert [b.name for b in buttons] == ["Calibrate BLU TRV", "Calibrate BLU TRV 2"]
    coordinator.blu_trv_names = {GATEWAY_ID: {"blutrv:200": "Bad"}}
    assert buttons[0].name == "Calibrate Bad"


def test_the_translated_name_is_used_and_the_entity_id_stays_english() -> None:
    _, buttons = _buttons()
    key = "component.shelly_cloud_diy.entity.button.blutrv_calibrate.name"
    _attach_platform(
        buttons[0], {key: "{valve} kalibrieren"}, {key: "Calibrate {valve}"}
    )
    assert buttons[0].name == "BLU TRV kalibrieren"
    assert buttons[0].suggested_object_id == "Calibrate BLU TRV"


# ── 5. The coverage report stays honest ───────────────────────────────


def test_the_mirror_counts_as_covered_only_while_the_error_list_comes_from_it() -> None:
    status = _gateway(remote_errors=["not_calibrated"])
    report = diagnostics._coverage_diagnostics(
        _FakeCoordinator(status), GATEWAY_ID, status
    )
    assert "blutrv_rstatus:200" in report["covered_keys"]

    # Negative control, one variable: the flat component now answers.
    status = _gateway(flat_errors=[], remote_errors=["not_calibrated"])
    sensor = _calibration_sensors(status)[0]
    assert sensor._fallback_source_keys == set()
