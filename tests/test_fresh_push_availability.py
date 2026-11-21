"""Availability of a mains device whose cached ``cloud.connected`` lies (#68).

A Shelly EM (Gen1) at a remote site kept pushing fresh readings to the cloud,
the Reporting sensor said *connected*, and yet every measurement entity was
unavailable: the snapshot carried ``cloud.connected: false``, and for a mains
device that flag alone decided availability.

The contract pinned here:

* a device we keep watching push new snapshots stays available, whatever the
  cached transport flag says;
* first sight is NOT a push — after an HA restart a dead device's last
  snapshot looks exactly like a live one, so it stays unavailable until the
  fingerprint actually changes;
* a device that stops pushing goes unavailable once its Reporting window
  runs out;
* ``cloud.connected: true`` keeps working exactly as before.

Driven through the real ``_async_update_data`` and ``ShellyBaseEntity``.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.shelly_cloud_diy import coordinator as coordinator_mod
from custom_components.shelly_cloud_diy.coordinator import ShellyCloudCoordinator
from custom_components.shelly_cloud_diy.entities.base import ShellyBaseEntity

EM_ID = "c45bbe6b1a2f"


@pytest.fixture(autouse=True)
def _inert_repair_issues(monkeypatch):
    """The stub ``hass`` has no issue registry; see test_offline_reporting."""
    for name, stub in {
        "async_manage_rate_limit_issue": lambda hass, entry, *, active: None,
        "async_manage_missing_devices_issue":
            lambda hass, entry, *, active, missing, names: None,
        "async_manage_relay_fault_issue":
            lambda hass, entry, *, active, faults, names: None,
        "async_manage_device_health_issue":
            lambda hass, entry, *, active, findings, names: None,
    }.items():
        monkeypatch.setattr(coordinator_mod, name, stub)


def _gen1_em(serial: int, *, connected: bool = False) -> dict[str, Any]:
    """A Gen1 Shelly EM as the cloud serves it, trimmed to what matters.

    Shape from the redacted diagnostic in #68: fresh ``emeters`` readings,
    Wi-Fi up, and ``cloud.connected: false`` in the same snapshot.
    """
    return {
        "cloud": {"enabled": True, "connected": connected},
        "wifi_sta": {"connected": True, "rssi": -25},
        "emeters": [
            {"power": 201.59, "voltage": 225.08, "pf": 0.9, "total": 1000.0,
             "total_returned": 0.0, "is_valid": True},
        ],
        "unixtime": 1795000000 + serial,
        "uptime": 864000 + serial,
        "serial": serial,
        "_updated": f"2026-11-20 06:{serial % 60:02d}:59",
    }


class _FakeApi:
    def __init__(self, devices_status: dict[str, Any]) -> None:
        self.devices_status = devices_status

    async def get_all_status(self) -> dict[str, Any]:
        return {"devices_status": self.devices_status}


class _StubHass:
    def async_create_task(self, coro: Any) -> None:
        coro.close()


def _coordinator() -> ShellyCloudCoordinator:
    coord = object.__new__(ShellyCloudCoordinator)
    coord._api = _FakeApi({})
    coord.hass = _StubHass()
    coord._entry = SimpleNamespace(options={"enabled_devices": []})
    coord.devices = {}
    coord._known_device_ids = set()
    coord.device_names = {}
    coord._names_attempted = set()
    coord._name_lookup_in_flight = False
    coord.virtual_configs = {}
    coord._vcomp_config_in_flight = False
    coord._sleep_seen = {}
    coord.checkins = {}
    coord._rate_limit_streak = 0
    coord._rate_limit_since = None
    coord._rate_limit_reported = False
    coord._missing_streak = {}
    coord._missing_since = {}
    coord._relay_fault_streak = {}
    coord._relay_fault_since = {}
    coord._relay_healthy_since = {}
    coord.relay_faults = set()
    coord._health_streak = {}
    coord._health_since = {}
    coord.device_health = {}
    return coord


def _poll(coord: ShellyCloudCoordinator, status: dict[str, Any] | None) -> None:
    coord._api.devices_status = {} if status is None else {EM_ID: status}
    coord.devices = asyncio.run(coord._async_update_data())


def _entity(coord: ShellyCloudCoordinator) -> ShellyBaseEntity:
    return ShellyBaseEntity(coord, EM_ID)


def test_the_cloud_really_reports_the_em_offline() -> None:
    """Calibration: the payload does produce ``online: False``."""
    coord = _coordinator()
    _poll(coord, _gen1_em(1))
    assert coord.devices[EM_ID]["online"] is False
    assert coord.devices[EM_ID]["sleeping"] is False


def test_first_sight_alone_does_not_make_it_available() -> None:
    """After a restart the snapshot may be the last one a dead device sent."""
    coord = _coordinator()
    _poll(coord, _gen1_em(1))
    assert coord.is_reporting(EM_ID) is True, "Reporting gives benefit of the doubt"
    assert _entity(coord).available is False


def test_a_device_seen_pushing_is_available_despite_the_flag() -> None:
    """The regression from #68."""
    coord = _coordinator()
    _poll(coord, _gen1_em(1))
    _poll(coord, _gen1_em(2))
    assert coord.devices[EM_ID]["online"] is False
    assert _entity(coord).available is True


def test_a_device_that_stops_pushing_goes_unavailable() -> None:
    coord = _coordinator()
    _poll(coord, _gen1_em(1))
    _poll(coord, _gen1_em(2))
    record = coord.checkins[EM_ID]
    record.last_checkin -= record.stale_after_s + 1.0
    _poll(coord, _gen1_em(2))  # same fingerprint: present but silent
    assert _entity(coord).available is False


def test_an_unchanged_snapshot_never_makes_it_available() -> None:
    coord = _coordinator()
    for _ in range(3):
        _poll(coord, _gen1_em(1))
    assert _entity(coord).available is False


def test_a_device_missing_from_the_poll_is_unavailable() -> None:
    coord = _coordinator()
    _poll(coord, _gen1_em(1))
    _poll(coord, _gen1_em(2))
    _poll(coord, None)
    assert _entity(coord).available is False


def test_connected_flag_true_still_means_available() -> None:
    coord = _coordinator()
    _poll(coord, _gen1_em(1, connected=True))
    assert _entity(coord).available is True


def test_a_coordinator_without_check_ins_keeps_the_old_contract() -> None:
    """Fakes and older call paths without ``has_fresh_push`` fail closed."""
    fake = SimpleNamespace(
        devices={EM_ID: {"online": False, "sleeping": False, "status": {}}},
        last_update_success=True,
    )
    fake.data = fake.devices
    assert ShellyBaseEntity(fake, EM_ID).available is False
