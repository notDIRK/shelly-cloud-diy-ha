"""Unit tests for Shelly BLU TRV support behind a BLU Gateway Gen3 (#48).

Every status shape here is copied from a real, sanitised Cloud diagnostics
extract contributed by @gerok1984: a Shelly BLU Gateway Gen3
(``S3GW-1DBT001``) with two paired BLU TRVs, read by this integration at
version 0.12.0 on Home Assistant 2026.9.3. Nothing in this file is invented
from documentation — that distinction is the whole point of #48, which
required a real snapshot per entry before anything was built.

Two facts from that snapshot drive the design:

1. The gateway carries each valve as ``blutrv:<id>`` in the 200+ range, the
   same range the virtual components use, with the live thermostat state
   flat in the component (``current_C``, ``target_C``, ``pos``, ``battery``,
   ``rssi``, ``connected``, ``paired``).
2. A write goes to **the gateway**, addressed to the valve by component id —
   ``BluTrv.Call`` wrapping ``TRV.SetTarget`` — not to the valve's own
   device id, which the cloud relay does not route to.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.exceptions import HomeAssistantError

from custom_components.shelly_cloud_diy import climate as climate_platform
from custom_components.shelly_cloud_diy import diagnostics
from custom_components.shelly_cloud_diy.const import is_gen2_status
from custom_components.shelly_cloud_diy.coordinator import ShellyCloudCoordinator
from custom_components.shelly_cloud_diy.sensor import (
    _create_rpc_sensors as create_rpc_sensors,
)

GATEWAY_ID = "34987a1b2c3d"


def _valve(
    component_id: int,
    *,
    current_c: float,
    target_c: float,
    pos: int = 0,
    connected: bool = True,
    paired: bool = True,
    battery: int = 100,
    rssi: int = -59,
) -> dict[str, Any]:
    """One ``blutrv:<id>`` component, shaped exactly like the real one."""
    return {
        "last_updated_ts": 1790455431,
        "packet_id": 101,
        "rssi": rssi,
        "id": component_id,
        "target_C": target_c,
        "current_C": current_c,
        "pos": pos,
        "connected": connected,
        "battery": battery,
        "key": False,
        "paired": paired,
        "rpc": True,
        "rsv": 39,
        "fw_ver": "v1.5.0",
        "_attrs": {"model_id": 8},
    }


# The gateway snapshot, trimmed to the keys that matter and otherwise
# verbatim: two valves, their remote status blocks and their remote info.
GATEWAY_STATUS: dict[str, Any] = {
    "sys": {"mac": "34987A1B2C3D"},
    "wifi": {"rssi": -48},
    "blutrv:200": _valve(200, current_c=24.3, target_c=10),
    "blutrv:201": _valve(201, current_c=24.4, target_c=10, rssi=-61),
    "blutrv_rstatus:200": {
        "v": 39,
        "ts": 1790381728,
        "status": {
            "sys": {"uptime": 307017, "ram_free": 7984},
            "temperature:0": {"id": 0, "tC": 22.11, "tF": 71.8, "errors": ["disabled"]},
            "trv:0": {
                "id": 0,
                "pos": 0,
                "steps": 248126,
                "current_C": 24.3,
                "target_C": 10,
                "schedule_rev": 0,
                "errors": [],
            },
        },
    },
    "blutrv_rinfo:200": {
        "v": 39,
        "device_info": {"app": "BluTRV", "model": "BluTRV", "ver": "1.5.0"},
    },
    "vcomps": ["blutrv:200", "blutrv:201"],
}


class _FakeRelay:
    """Records what would have gone over the cloud relay."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.sent: list[tuple[str, str, dict]] = []

    async def send_jrpc_request(
        self, device_id: str, method: str, params: dict | None = None, **_: Any
    ) -> dict:
        self.sent.append((device_id, method, params or {}))
        if self._error is not None:
            raise self._error
        return {}


class _FakeCoordinator:
    """The slice of the coordinator a climate entity actually reads."""

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

    # Borrowed unchanged from the real coordinator: the write path is the
    # part worth testing, so it must be the shipped one, not a stand-in.
    async_set_blutrv_target = ShellyCloudCoordinator.async_set_blutrv_target


def _climates(
    status: dict[str, Any] | None = None, **kwargs: Any
) -> tuple[_FakeCoordinator, list[Any]]:
    coordinator = _FakeCoordinator(status or GATEWAY_STATUS, **kwargs)
    entities = climate_platform._create_climate_entities(
        GATEWAY_ID, status or GATEWAY_STATUS, set(), coordinator
    )
    return coordinator, entities


# ── 1. The gateway must be recognised at all ──────────────────────────


def test_a_gateway_whose_only_rpc_component_is_a_valve_reads_as_gen2() -> None:
    """A BLU Gateway Gen3 has no relay, no light, no meter — only valves.

    Without ``blutrv`` in the Gen2 pattern the whole device would be handed
    to the Gen1 builders, which is the second half of the bug #47 turned up.
    """
    assert is_gen2_status({"sys": {}, "blutrv:200": _valve(200, current_c=20, target_c=20)})


def test_the_remote_blocks_alone_do_not_make_a_device_gen2() -> None:
    """Negative control, one variable: the key name, nothing else.

    ``blutrv_rstatus:`` and ``blutrv_rinfo:`` start with the same six
    characters. A pattern that matched them would classify a Gen1 device
    carrying an unrelated key as Gen2.
    """
    assert not is_gen2_status({"sys": {}, "blutrv_rstatus:200": {"v": 1}})
    assert not is_gen2_status({"sys": {}, "blutrv_rinfo:200": {"v": 1}})


# ── 2. One entity per valve ───────────────────────────────────────────


def test_each_valve_becomes_one_climate_entity() -> None:
    _, entities = _climates()
    assert [e.unique_id for e in entities] == [
        f"{GATEWAY_ID}_blutrv:200_climate",
        f"{GATEWAY_ID}_blutrv:201_climate",
    ]


def test_the_first_valve_is_unnumbered_and_the_second_is_not() -> None:
    """House naming: channel 1 carries no suffix, later ones do."""
    _, entities = _climates()
    assert [e.name for e in entities] == ["BLU TRV", "BLU TRV 2"]


def test_a_gateway_with_no_valves_creates_nothing() -> None:
    _, entities = _climates({"sys": {}, "wifi": {"rssi": -48}})
    assert entities == []


# ── 3. What the entity reads ──────────────────────────────────────────


def test_the_entity_reads_the_valve_temperatures() -> None:
    _, entities = _climates()
    first = entities[0]
    assert first.current_temperature == 24.3
    assert first.target_temperature == 10
    assert first.hvac_mode is HVACMode.HEAT


def test_a_closed_valve_is_idle_and_an_open_one_is_heating() -> None:
    _, closed = _climates()
    assert closed[0].hvac_action is HVACAction.IDLE

    status = dict(GATEWAY_STATUS)
    status["blutrv:200"] = _valve(200, current_c=24.3, target_c=26, pos=42)
    _, open_valve = _climates(status)
    assert open_valve[0].hvac_action is HVACAction.HEATING


def test_a_valve_out_of_radio_contact_is_unavailable() -> None:
    """``connected`` is the valve's own radio link to the gateway.

    The gateway stays online and keeps serving the last values, so without
    this check a valve that dropped off would keep reporting a stale
    temperature as if it were live.
    """
    status = dict(GATEWAY_STATUS)
    status["blutrv:200"] = _valve(200, current_c=24.3, target_c=10, connected=False)
    _, entities = _climates(status)
    assert entities[0].available is False


def test_an_unpaired_component_is_unavailable() -> None:
    status = dict(GATEWAY_STATUS)
    status["blutrv:200"] = _valve(200, current_c=24.3, target_c=10, paired=False)
    _, entities = _climates(status)
    assert entities[0].available is False


# ── 4. Writing, and refusing to write ─────────────────────────────────


def test_no_target_can_be_set_without_the_cloud_relay() -> None:
    """Off means off: the read-only entity stays, the control does not."""
    _, entities = _climates(connected=False)
    assert entities[0].supported_features == ClimateEntityFeature(0)


def test_no_target_can_be_set_on_a_device_the_relay_will_not_route_to() -> None:
    _, entities = _climates(controllable=False)
    assert entities[0].supported_features == ClimateEntityFeature(0)


def test_the_control_is_offered_once_the_relay_will_route() -> None:
    _, entities = _climates()
    assert entities[0].supported_features == ClimateEntityFeature.TARGET_TEMPERATURE


def test_setting_a_target_addresses_the_gateway_and_wraps_the_valve_call() -> None:
    """The one measured fact about the write path, asserted literally."""
    relay = _FakeRelay()
    coordinator, entities = _climates(relay=relay)
    asyncio.run(entities[1].async_set_temperature(**{ATTR_TEMPERATURE: 21.5}))

    assert relay.sent == [
        (
            GATEWAY_ID,
            "BluTrv.Call",
            {
                "id": 201,
                "method": "TRV.SetTarget",
                "params": {"id": 0, "target_C": 21.5},
            },
        )
    ]
    # Confirmation is asked of the poll, never assumed.
    assert coordinator.refreshes == 1


def test_a_refused_command_is_loud() -> None:
    relay = _FakeRelay(error=HomeAssistantError("relay said no"))
    _, entities = _climates(relay=relay)
    with pytest.raises(HomeAssistantError):
        asyncio.run(entities[0].async_set_temperature(**{ATTR_TEMPERATURE: 21.0}))


def test_a_target_outside_the_valve_range_is_refused_before_it_is_sent() -> None:
    relay = _FakeRelay()
    _, entities = _climates(relay=relay)
    with pytest.raises(HomeAssistantError):
        asyncio.run(entities[0].async_set_temperature(**{ATTR_TEMPERATURE: 45.0}))
    assert relay.sent == []


def test_a_write_without_a_relay_raises_rather_than_passing_silently() -> None:
    _, entities = _climates(relay=None)
    with pytest.raises(HomeAssistantError):
        asyncio.run(entities[0].async_set_temperature(**{ATTR_TEMPERATURE: 21.0}))


# ── 5. The valve's own diagnostics ────────────────────────────────────


def test_each_valve_gets_its_battery_and_signal_sensors() -> None:
    """A BLU TRV runs on batteries; its level is in the same component."""
    coordinator = _FakeCoordinator(GATEWAY_STATUS)
    sensors = create_rpc_sensors(GATEWAY_ID, GATEWAY_STATUS, set(), coordinator)
    by_uid = {s.unique_id: s for s in sensors}

    assert by_uid[f"{GATEWAY_ID}_blutrv:200_battery"].native_value == 100
    assert by_uid[f"{GATEWAY_ID}_blutrv:200_rssi"].native_value == -59
    assert by_uid[f"{GATEWAY_ID}_blutrv:201_rssi"].native_value == -61


# ── 6. The gap #48 was opened for is actually closed ──────────────────


def test_the_valve_components_no_longer_report_as_uncovered() -> None:
    """The coverage report is what found this gap; it must now agree.

    Derived from the builders, so this fails if an entity stops being
    created — the guard that #41/#42/#47 each needed and did not have.
    """
    coordinator = _FakeCoordinator(GATEWAY_STATUS)
    report = diagnostics._coverage_diagnostics(
        coordinator, GATEWAY_ID, GATEWAY_STATUS
    )
    assert "blutrv:200" not in report["uncovered_keys"]
    assert "blutrv:201" not in report["uncovered_keys"]


# ── 7. The remote-status-only shape (#55) ─────────────────────────────
#
# A second BLU Gateway Gen3 (valve on firmware v1.5.0) carries none of
# ``current_C`` / ``target_C`` / ``pos`` in ``blutrv:<id>``; the valve's state
# is only in the gateway's mirror of its own status. The values below are the
# reporter's, verbatim.


def _rstatus(
    *,
    current_c: Any = 22.74,
    target_c: Any = 4,
    pos: Any = 0,
    t_c: Any = 22.89,
    t_errors: list[str] | None = None,
) -> dict[str, Any]:
    temperature: dict[str, Any] = {"id": 0, "tC": t_c}
    if t_errors is not None:
        temperature["errors"] = t_errors
    trv: dict[str, Any] = {"id": 0}
    for field, value in (("current_C", current_c), ("target_C", target_c), ("pos", pos)):
        if value is not None:
            trv[field] = value
    return {"v": 39, "status": {"temperature:0": temperature, "trv:0": trv}}


def _bare_valve(component_id: int = 200) -> dict[str, Any]:
    """``blutrv:<id>`` as #55 has it: link and battery, no thermostat state."""
    valve = _valve(component_id, current_c=0, target_c=0)
    for field in ("current_C", "target_C", "pos"):
        del valve[field]
    return valve


def _rstatus_only(**rstatus: Any) -> dict[str, Any]:
    return {
        "sys": {"mac": "34987A1B2C3D"},
        "blutrv:200": _bare_valve(),
        "blutrv_rstatus:200": _rstatus(**rstatus),
        "bthomesensor:202": {"id": 202, "value": 4},
        "bthomesensor:203": {"id": 203, "value": 22.7},
    }


def test_the_flat_shape_reads_exactly_as_before() -> None:
    """#48 unchanged: the flat fields win over its rstatus block."""
    _, entities = _climates()
    assert entities[0].current_temperature == 24.3
    assert entities[0].target_temperature == 10
    assert entities[0].extra_state_attributes["valve_position"] == 0


def test_the_remote_status_alone_supplies_temperatures_and_position() -> None:
    _, (entity,) = _climates(_rstatus_only(pos=35))
    assert entity.current_temperature == 22.74
    assert entity.target_temperature == 4
    assert entity.hvac_action is HVACAction.HEATING
    assert entity.extra_state_attributes["valve_position"] == 35


def test_the_flat_value_wins_when_both_are_present() -> None:
    """Negative control, one variable: the flat fields are added back."""
    status = _rstatus_only(pos=35)
    status["blutrv:200"] = _valve(200, current_c=21.0, target_c=19.5, pos=0)
    _, (entity,) = _climates(status)
    assert entity.current_temperature == 21.0
    assert entity.target_temperature == 19.5
    assert entity.hvac_action is HVACAction.IDLE


def test_a_flat_placeholder_falls_through_to_the_remote_reading() -> None:
    status = _rstatus_only()
    status["blutrv:200"] = _valve(200, current_c=-999, target_c=-999)
    _, (entity,) = _climates(status)
    assert entity.current_temperature == 22.74
    assert entity.target_temperature == 4


def test_a_remote_placeholder_is_no_reading() -> None:
    _, (entity,) = _climates(_rstatus_only(current_c=-999, target_c=-999, t_c=-999))
    assert entity.current_temperature is None
    assert entity.target_temperature is None


def test_a_boolean_is_not_a_temperature_or_a_position() -> None:
    _, (entity,) = _climates(
        _rstatus_only(current_c=True, target_c=True, pos=True, t_c=True)
    )
    assert entity.current_temperature is None
    assert entity.target_temperature is None
    assert entity.hvac_action is HVACAction.IDLE
    assert "valve_position" not in entity.extra_state_attributes


def test_the_internal_sensor_is_the_last_resort_for_the_room_temperature() -> None:
    _, (entity,) = _climates(_rstatus_only(current_c=None))
    assert entity.current_temperature == 22.89
    # Only the room temperature has that fallback; a setpoint does not.
    _, (entity,) = _climates(_rstatus_only(target_c=None))
    assert entity.target_temperature is None


def test_a_flagged_internal_sensor_is_not_used() -> None:
    """#48's ``temperature:0`` says ``disabled`` and reads two degrees off."""
    _, (entity,) = _climates(_rstatus_only(current_c=None, t_errors=["disabled"]))
    assert entity.current_temperature is None


def test_the_remote_status_of_another_valve_is_not_borrowed() -> None:
    status = _rstatus_only()
    status["blutrv_rstatus:201"] = status.pop("blutrv_rstatus:200")
    _, (entity,) = _climates(status)
    assert entity.current_temperature is None
    assert entity.target_temperature is None


def test_bthome_sensors_are_never_read_as_the_valve() -> None:
    """Their link to a valve is not provable from the payload (#55)."""
    status = _rstatus_only()
    del status["blutrv_rstatus:200"]
    _, (entity,) = _climates(status)
    assert entity.current_temperature is None
    assert entity.target_temperature is None


def test_the_remote_status_counts_as_covered_only_while_it_is_read() -> None:
    status = _rstatus_only()
    report = diagnostics._coverage_diagnostics(
        _FakeCoordinator(status), GATEWAY_ID, status
    )
    assert "blutrv_rstatus:200" in report["covered_keys"]
    assert "bthomesensor:203" in report["uncovered_keys"]

    # #48: the block is there, but the flat fields supply everything the
    # climate entity reads. (Since #58 the calibration sensor reads the
    # block's ``trv:0.errors``, so the report as a whole now calls it
    # covered — asserted in test_blu_trv_calibration.py. The climate
    # entity's own claim is what this guards.)
    _, entities = _climates()
    assert entities[0]._fallback_source_keys == set()
