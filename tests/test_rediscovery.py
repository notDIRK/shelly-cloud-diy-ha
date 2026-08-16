"""Unit tests for device discovery and rediscovery.

Two behaviours meet here, and getting either one alone wrong is a bug that
only shows up on a live account:

1. ``_async_update_data`` must replace ``self.devices`` BEFORE it fires
   ``SIGNAL_NEW_DEVICE``. The platform builders read the device's status out
   of ``coordinator.devices``; dispatching first meant a device seen for the
   first time was looked up in the *previous* snapshot, where it does not
   exist. The builders then saw an empty status, created no component
   entities, and the device never got a second chance because
   ``_known_device_ids`` had already been updated.

2. A rediscovered device must NOT have its entity bookkeeping reset.
   ``/device/all_status`` omits healthy devices spontaneously (see the
   debounce note in ``_async_update_data``), so ``SIGNAL_NEW_DEVICE`` fires
   for a perfectly healthy device as a matter of routine. Rebuilding its
   entities then collides with the ones still live — Home Assistant logs
   "does not generate unique IDs" and drops the duplicate. Only an actual
   deletion from the UI may reset that bookkeeping, which is what
   ``SIGNAL_DEVICE_REMOVED`` is for.

The two pull in opposite directions: fixing (1) on its own is what makes (2)
bite, because the clearing that used to be harmless suddenly has a populated
snapshot to work with.

Driven through the real production code with a lightweight fake coordinator,
so no running Home Assistant instance is required.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from custom_components.shelly_cloud_diy import binary_sensor as binary_sensor_mod
from custom_components.shelly_cloud_diy import coordinator as coordinator_mod
from custom_components.shelly_cloud_diy.const import (
    DOMAIN,
    SIGNAL_DEVICE_REMOVED,
    SIGNAL_NEW_DEVICE,
)
from custom_components.shelly_cloud_diy.coordinator import ShellyCloudCoordinator

DEVICE_ID = "ecda3bc59ec8"
OTHER_ID = "d0ef76c7a454"


def _gen3_status(updated: str = "2026-08-16 14:20:06") -> dict[str, Any]:
    """A Gen3 mains device with an input and a cloud block, so the RPC
    builder has something to build (one input sensor + one cloud sensor)."""
    return {
        "_updated": updated,
        "sys": {"mac": DEVICE_ID.upper(), "uptime": 545909},
        "cloud": {"connected": True},
        "input:0": {"id": 0, "state": False},
        "switch:0": {"id": 0, "output": True, "apower": 12.3},
    }


class _FakeApi:
    def __init__(self, devices_status: dict[str, Any]) -> None:
        self._devices_status = devices_status

    async def get_all_status(self) -> dict[str, Any]:
        return {"devices_status": self._devices_status}


class _StubHass:
    """Enough of HomeAssistant for the poll path; ``data`` carries the
    coordinator the way the real integration hands it to a platform."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def async_create_task(self, coro: Any) -> None:
        coro.close()


def _coordinator(devices_status: dict[str, Any]) -> ShellyCloudCoordinator:
    """A coordinator whose real ``_async_update_data`` can be driven.

    ``__init__`` wants a HomeAssistant instance and a ConfigEntry; the poll
    path does not, so bypass it rather than mock all of HA.
    """
    coord = object.__new__(ShellyCloudCoordinator)
    coord._api = _FakeApi(devices_status)
    coord.hass = _StubHass()
    # ``create_all_initially`` so every discovered device is dispatched —
    # otherwise ``is_enabled`` gates the signal we are testing.
    coord._entry = SimpleNamespace(options={"create_all_initially": True})
    coord.devices = {}
    coord._known_device_ids = set()
    coord.device_names = {}
    coord._names_attempted = set()
    coord._name_lookup_in_flight = False
    coord.virtual_configs = {}
    coord._vcomp_config_in_flight = False
    coord._sleep_seen = {}
    coord.checkins = {}
    return coord


def _setup_binary_sensor_platform(
    coord: ShellyCloudCoordinator, monkeypatch: Any
) -> tuple[list[Any], dict[str, Any]]:
    """Run the real ``binary_sensor.async_setup_entry`` and hand back the
    entities it added plus the dispatcher callbacks it registered."""
    added: list[Any] = []
    callbacks: dict[str, Any] = {}

    def fake_connect(hass: Any, signal: str, target: Any) -> Any:
        callbacks[signal] = target
        return lambda: None

    monkeypatch.setattr(binary_sensor_mod, "async_dispatcher_connect", fake_connect)

    entry = SimpleNamespace(entry_id="entry", async_on_unload=lambda _cb: None)
    coord.hass.data = {DOMAIN: {"entry": coord}}

    asyncio.run(
        binary_sensor_mod.async_setup_entry(
            coord.hass, entry, lambda entities: added.extend(entities)
        )
    )
    return added, callbacks


# ── 1. The dispatch order ─────────────────────────────────────────────


def test_a_new_device_is_dispatched_against_the_snapshot_it_appeared_in(
    monkeypatch: Any,
) -> None:
    """The bug this pins: the signal used to fire while ``coordinator.devices``
    still held the previous poll, so the builders looked the brand-new device
    up in a snapshot that could not contain it."""
    coord = _coordinator({DEVICE_ID: _gen3_status()})
    seen_at_dispatch: dict[str, Any] = {}

    def record(hass: Any, signal: str, device_id: str) -> None:
        seen_at_dispatch[device_id] = coord.devices.get(device_id, {}).get("status", {})

    monkeypatch.setattr(coordinator_mod, "async_dispatcher_send", record)
    asyncio.run(coord._async_update_data())

    assert DEVICE_ID in seen_at_dispatch, "no signal fired for the new device"
    assert seen_at_dispatch[DEVICE_ID].get("input:0") == {"id": 0, "state": False}


def test_a_device_appearing_mid_run_gets_its_component_entities(
    monkeypatch: Any,
) -> None:
    """End-to-end over the real platform: a device that shows up while the
    integration is already running must get its component entities, not just
    the device-wide reporting sensor."""
    devices_status: dict[str, Any] = {}
    coord = _coordinator(devices_status)
    added, callbacks = _setup_binary_sensor_platform(coord, monkeypatch)
    assert added == [], "no devices yet, so nothing to build"

    # Wire the coordinator's signal to the platform listener, as HA would.
    monkeypatch.setattr(
        coordinator_mod,
        "async_dispatcher_send",
        lambda hass, signal, device_id: callbacks[signal](device_id),
    )

    devices_status[DEVICE_ID] = _gen3_status()
    asyncio.run(coord._async_update_data())

    uids = {e.unique_id for e in added}
    assert f"{DEVICE_ID}_reporting" in uids
    assert f"{DEVICE_ID}_input:0_state" in uids, "component entities were not built"
    assert f"{DEVICE_ID}_cloud_connected" in uids


# ── 2. Rediscovery must not rebuild what is still live ────────────────


def test_rediscovery_does_not_rebuild_entities_that_still_exist(
    monkeypatch: Any,
) -> None:
    """``/device/all_status`` drops healthy devices on its own, so this path
    runs in normal operation. Rebuilding here is what produced the duplicate
    unique-ID errors."""
    coord = _coordinator({DEVICE_ID: _gen3_status()})
    coord.devices = {DEVICE_ID: {"status": _gen3_status(), "online": True}}
    added, callbacks = _setup_binary_sensor_platform(coord, monkeypatch)

    first_round = {e.unique_id for e in added}
    assert f"{DEVICE_ID}_input:0_state" in first_round

    added.clear()
    callbacks[SIGNAL_NEW_DEVICE](DEVICE_ID)
    assert added == [], "a rediscovered device must not be rebuilt"


def test_rediscovery_of_one_device_leaves_the_others_alone(
    monkeypatch: Any,
) -> None:
    """The bookkeeping is keyed by device, so a signal for one device must
    not resurrect or duplicate anything belonging to another."""
    coord = _coordinator({})
    coord.devices = {
        DEVICE_ID: {"status": _gen3_status(), "online": True},
        OTHER_ID: {"status": _gen3_status(), "online": True},
    }
    added, callbacks = _setup_binary_sensor_platform(coord, monkeypatch)
    assert {e.unique_id for e in added} >= {
        f"{DEVICE_ID}_input:0_state",
        f"{OTHER_ID}_input:0_state",
    }

    added.clear()
    callbacks[SIGNAL_DEVICE_REMOVED](DEVICE_ID)
    callbacks[SIGNAL_NEW_DEVICE](OTHER_ID)
    assert added == [], "the untouched device was rebuilt"


# ── 3. …but a real deletion must reset it ─────────────────────────────


def test_a_deleted_device_is_rebuilt_when_it_is_rediscovered(
    monkeypatch: Any,
) -> None:
    """Deleting a device from the UI purges its entity-registry entries, so
    the platform must forget it too — otherwise a later rediscovery finds
    everything "already created" and silently produces nothing."""
    coord = _coordinator({})
    coord.devices = {DEVICE_ID: {"status": _gen3_status(), "online": True}}
    added, callbacks = _setup_binary_sensor_platform(coord, monkeypatch)
    original = {e.unique_id for e in added}
    assert f"{DEVICE_ID}_reporting" in original

    added.clear()
    callbacks[SIGNAL_DEVICE_REMOVED](DEVICE_ID)
    callbacks[SIGNAL_NEW_DEVICE](DEVICE_ID)

    assert {e.unique_id for e in added} == original
