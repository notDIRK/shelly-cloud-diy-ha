"""Unit tests for wiring the cloud control channel into Home Assistant.

The transport has its own tests (``test_oauth.py``, ``test_cloud_ws.py``).
These are about the four promises the *wiring* makes, because each one is a
promise a user can only check by trusting it:

1. **Off means off.** With the option unset — which is every existing install
   — nothing is constructed: no sign-in, no second connection, no probe, no
   entity. The existing suite is the other half of that proof; it passes
   unmodified.
2. **A shared device offers no control entity**, and neither does one the
   relay could not classify. "We could not tell" is re-asked, never promoted
   to a verdict.
3. **A failed command is loud.** Every failure raises; a success asks the
   poll what happened rather than asserting it.
4. **No credential reaches a log line or a diagnostics dump.**

Everything runs against a fake relay and hand-built coordinators, the way the
rest of this suite does: no network, no running Home Assistant, and every
credential an obvious fake.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging
import textwrap
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.exceptions import HomeAssistantError

import custom_components.shelly_cloud_diy as integration
from custom_components.shelly_cloud_diy import config_flow, diagnostics
from custom_components.shelly_cloud_diy import switch as switch_platform
from custom_components.shelly_cloud_diy.api.cloud_ws import (
    DeviceOwnership,
    ShellyCloudWsAuthError,
    ShellyCloudWsCommandError,
    ShellyCloudWsTimeoutError,
)
from custom_components.shelly_cloud_diy.api.oauth import OAuthToken
from custom_components.shelly_cloud_diy import coordinator as coordinator_module
from custom_components.shelly_cloud_diy.const import (
    CLOUD_CONTROL_DEFAULT,
    CONF_CLOUD_CONTROL,
    CONF_OAUTH_TOKEN,
    SIGNAL_NEW_DEVICE,
)
from custom_components.shelly_cloud_diy.coordinator import ShellyCloudCoordinator
from custom_components.shelly_cloud_diy.utils.token_store import (
    token_from_storage,
    token_to_storage,
)

# Obvious fakes. Nothing here is, or resembles, a real credential.
FAKE_ACCESS = "FAKE-ACCESS-TOKEN-never-in-a-log-1234"  # noqa: S105
FAKE_REFRESH = "FAKE-REFRESH-TOKEN-never-in-a-log-987"  # noqa: S105
FAKE_PASSWORD = "correct-horse-battery-staple-FAKE"  # noqa: S105
FAKE_EMAIL = "nobody@example.invalid"

OWNED_ID = "5432044e0001"
SHARED_ID = "5432044e0002"
MURKY_ID = "5432044e0003"
PLAIN_ID = "5432044e0004"

SERVER_URI = "https://shelly-42-eu.shelly.cloud"

# A Gen2 status carrying one virtual boolean (an irrigation zone) …
ZONE_STATUS: dict[str, Any] = {
    "sys": {"mac": "AABBCC"},
    "switch:0": {"output": False},
    "boolean:200": {"value": False},
    "boolean:201": {"value": True},
}
# … and one carrying none, which can never gain a control entity.
PLAIN_STATUS: dict[str, Any] = {"sys": {"mac": "DDEEFF"}, "switch:0": {"output": True}}


class _FakeRelay:
    """Stands in for ``ShellyCloudWebSocket``: records, answers, never talks."""

    host = "shelly-42-eu.shelly.cloud"

    def __init__(
        self,
        verdicts: dict[str, Any] | None = None,
        *,
        command_error: Exception | None = None,
        connected: bool = True,
    ) -> None:
        self._verdicts = verdicts or {}
        self._command_error = command_error
        self._connected = connected
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.probes: list[str] = []
        self.sent: list[tuple[str, str, dict]] = []

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self.connect_calls += 1

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._connected = False

    async def async_classify_ownership(self, device_id: str) -> DeviceOwnership:
        self.probes.append(device_id)
        answer = self._verdicts.get(device_id, DeviceOwnership.UNKNOWN)
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def send_jrpc_request(
        self, device_id: str, method: str, params: dict | None = None, **_: Any
    ) -> dict:
        self.sent.append((device_id, method, params or {}))
        if self._command_error is not None:
            raise self._command_error
        return {}


def _coordinator(
    devices: dict[str, dict[str, Any]] | None = None,
    *,
    options: dict[str, Any] | None = None,
    ws: _FakeRelay | None = None,
    real_tasks: bool = False,
) -> ShellyCloudCoordinator:
    """Build a coordinator by hand, as the rest of this suite does.

    Constructing the real one drags in Home Assistant's frame-reporting
    machinery; ``test_coordinator_init.py`` is the guard that ``__init__``
    really assigns what the poll path reads.
    """
    tasks: list[Any] = []

    def _create_task(hass: Any, coro: Any, name: str) -> Any:
        """Stand in for ``ConfigEntry.async_create_background_task``.

        The re-probe loop is an ENTRY background task, so Home Assistant
        cancels it with the entry. By default the fake only records the name
        and closes the coroutine; ``real_tasks`` runs it for the tests that
        are about the loop itself.
        """
        tasks.append(name)
        if real_tasks:
            return asyncio.get_running_loop().create_task(coro)
        coro.close()
        return SimpleNamespace(done=lambda: True, cancel=lambda: None)

    coordinator = object.__new__(ShellyCloudCoordinator)
    coordinator._entry = SimpleNamespace(
        entry_id="e1",
        options=dict(options or {}),
        data={},
        async_create_background_task=_create_task,
    )
    coordinator.devices = devices if devices is not None else {}
    coordinator.device_names = {}
    coordinator.virtual_configs = {}
    coordinator.device_ownership = {}
    coordinator._ownership_unresolved = set()
    coordinator._ownership_task = None
    coordinator._cloud_ws = ws
    coordinator.last_update_success = True
    coordinator.data = coordinator.devices

    coordinator.hass = SimpleNamespace(data={"shelly_cloud_diy": {"e1": coordinator}})
    coordinator.background_tasks = tasks

    refreshes: list[int] = []
    coordinator.refreshes = refreshes

    async def _request_refresh() -> None:
        refreshes.append(1)

    coordinator.async_request_refresh = _request_refresh
    return coordinator


def _devices(*ids: str, status: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        device_id: {"status": dict(status or ZONE_STATUS), "online": True}
        for device_id in ids
    }


class _Entry:
    """Stands in for a ConfigEntry that holds both credentials."""

    def __init__(self, options: dict[str, Any]) -> None:
        self.entry_id = "e1"
        self.data = {
            "auth_key": "NOTAREALKEY-shouldneverappear",
            "server_uri": SERVER_URI,
            CONF_OAUTH_TOKEN: {
                "access_token": FAKE_ACCESS,
                "refresh_token": FAKE_REFRESH,
                "expires_at": 1.0,
            },
        }
        self.options = options


# ── 1. Off means off ──────────────────────────────────────────────────


def test_cloud_control_is_off_unless_the_user_asks_for_it() -> None:
    assert _coordinator().cloud_control is False
    assert _coordinator(options={CONF_CLOUD_CONTROL: True}).cloud_control is True


def test_nothing_is_controllable_without_a_transport() -> None:
    """Even a stored verdict is inert while no relay is connected."""
    coordinator = _coordinator(_devices(OWNED_ID))
    coordinator.device_ownership[OWNED_ID] = DeviceOwnership.OWNED
    assert coordinator.is_cloud_controllable(OWNED_ID) is False


def test_setup_reaches_cloud_control_only_from_inside_the_option_check() -> None:
    """A static guard, for a failure a behavioural test cannot see.

    The promise "with the option off, nothing happens" is positional: it holds
    because one call sits inside one ``if``. A rebase that lifts the call out
    of the block would still import, still pass every other test, and would
    quietly sign every existing install in to Shelly Cloud.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(integration.async_setup_entry)))

    def _calls(node: ast.AST) -> list[ast.Call]:
        return [
            n
            for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and getattr(n.func, "id", "") == "_async_setup_cloud_control"
        ]

    assert len(_calls(tree)) == 1, "exactly one entry point into cloud control"
    guarded = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and "cloud_control" in ast.dump(node.test)
        and _calls(node)
    ]
    assert guarded, "the call must sit behind the cloud-control check"
    # Polarity, not just position: a negated test would still be a test.
    for node in guarded:
        assert not isinstance(node.test, ast.UnaryOp), "the guard must not be negated"
        assert not [n for n in ast.walk(node.test) if isinstance(n, ast.Not)]
    # And the option it reads defaults to off.
    assert CLOUD_CONTROL_DEFAULT is False


def test_a_coordinator_without_cloud_control_builds_no_control_switch() -> None:
    """The platform's half of the contract, exercised through the real setup."""
    coordinator = _coordinator(_devices(OWNED_ID))
    added = _run_switch_setup(coordinator)

    assert [e.unique_id for e in added] == [f"{OWNED_ID}_switch_0"]


def test_a_bare_stub_coordinator_is_treated_as_switched_off() -> None:
    """Anything that is not a coordinator with the feature answers "none"."""
    assert switch_platform._controllable_boolean_keys(SimpleNamespace(), OWNED_ID) == []


# ── 2. Ownership: probe once, cache, never harden a non-answer ────────


def _enable(coordinator: ShellyCloudCoordinator, ws: _FakeRelay) -> None:
    asyncio.run(coordinator.async_enable_cloud_control(ws))


def test_an_owned_device_gets_a_switch_beside_its_read_only_sensor() -> None:
    coordinator = _coordinator(
        _devices(OWNED_ID), options={CONF_CLOUD_CONTROL: True}
    )
    _enable(coordinator, _FakeRelay({OWNED_ID: DeviceOwnership.OWNED}))

    added = _run_switch_setup(coordinator)
    unique_ids = {e.unique_id for e in added}

    assert f"{OWNED_ID}_boolean:200_control" in unique_ids
    assert f"{OWNED_ID}_boolean:201_control" in unique_ids
    # The relay switch is untouched, and so is the binary sensor's unique id:
    # the control entity is additive, never a rename of an existing one.
    assert f"{OWNED_ID}_switch_0" in unique_ids
    assert f"{OWNED_ID}_boolean:200_value" not in unique_ids


def test_a_device_the_relay_refuses_gets_no_control_switch() -> None:
    coordinator = _coordinator(
        _devices(SHARED_ID), options={CONF_CLOUD_CONTROL: True}
    )
    _enable(coordinator, _FakeRelay({SHARED_ID: DeviceOwnership.NOT_ROUTABLE}))

    assert coordinator.device_ownership[SHARED_ID] is DeviceOwnership.NOT_ROUTABLE
    assert [e.unique_id for e in _run_switch_setup(coordinator)] == [
        f"{SHARED_ID}_switch_0"
    ]


def test_an_unclassified_device_gets_no_switch_and_no_verdict() -> None:
    coordinator = _coordinator(
        _devices(MURKY_ID), options={CONF_CLOUD_CONTROL: True}
    )
    _enable(coordinator, _FakeRelay({MURKY_ID: DeviceOwnership.UNKNOWN}))

    assert MURKY_ID not in coordinator.device_ownership, "UNKNOWN is not a verdict"
    assert MURKY_ID in coordinator._ownership_unresolved
    assert coordinator.is_cloud_controllable(MURKY_ID) is False
    assert [e.unique_id for e in _run_switch_setup(coordinator)] == [
        f"{MURKY_ID}_switch_0"
    ]


def test_a_definite_verdict_is_never_probed_twice() -> None:
    coordinator = _coordinator(
        _devices(OWNED_ID, SHARED_ID), options={CONF_CLOUD_CONTROL: True}
    )
    relay = _FakeRelay(
        {OWNED_ID: DeviceOwnership.OWNED, SHARED_ID: DeviceOwnership.NOT_ROUTABLE}
    )
    _enable(coordinator, relay)
    asyncio.run(coordinator._async_probe_ownership([OWNED_ID, SHARED_ID]))

    assert sorted(relay.probes) == [OWNED_ID, SHARED_ID]


def test_an_unclassified_device_is_asked_again_and_can_become_owned() -> None:
    """The point of not storing UNKNOWN: the next answer is allowed to differ."""
    coordinator = _coordinator(
        _devices(MURKY_ID), options={CONF_CLOUD_CONTROL: True}
    )
    _enable(coordinator, _FakeRelay({MURKY_ID: DeviceOwnership.UNKNOWN}))

    coordinator._cloud_ws = _FakeRelay({MURKY_ID: DeviceOwnership.OWNED})
    newly_owned = asyncio.run(coordinator._async_probe_ownership([MURKY_ID]))

    assert newly_owned == {MURKY_ID}
    assert coordinator.device_ownership[MURKY_ID] is DeviceOwnership.OWNED
    assert coordinator._ownership_unresolved == set()


def test_only_gen2_devices_carrying_a_virtual_boolean_are_probed() -> None:
    """One relay round-trip is cheap; sixty at every startup are not.

    A device without a virtual boolean can never gain a control entity, so a
    probe would buy a diagnostics line and nothing else.
    """
    devices = {
        OWNED_ID: {"status": dict(ZONE_STATUS)},
        PLAIN_ID: {"status": dict(PLAIN_STATUS)},
        "blegateway01": {
            "status": {"_dev_info": {"gen": "GBLE"}, "temperature:0": {"tC": 20}}
        },
        "gen1device01": {"status": {"relays": [{"ison": True}]}},
    }
    coordinator = _coordinator(devices, options={CONF_CLOUD_CONTROL: True})

    assert coordinator._control_candidates() == [OWNED_ID]


def test_a_device_the_user_unticked_is_not_probed() -> None:
    coordinator = _coordinator(
        _devices(OWNED_ID, SHARED_ID),
        options={CONF_CLOUD_CONTROL: True, "enabled_devices": [OWNED_ID]},
    )
    assert coordinator._control_candidates() == [OWNED_ID]


def test_a_rejected_session_leaves_the_remaining_devices_unclassified() -> None:
    """The session died, which says nothing about any device."""
    coordinator = _coordinator(
        _devices(OWNED_ID, SHARED_ID, MURKY_ID), options={CONF_CLOUD_CONTROL: True}
    )
    coordinator._cloud_ws = _FakeRelay(
        {
            OWNED_ID: DeviceOwnership.OWNED,
            SHARED_ID: ShellyCloudWsAuthError("session rejected"),
        }
    )
    asyncio.run(
        coordinator._async_probe_ownership([OWNED_ID, SHARED_ID, MURKY_ID])
    )

    # What was answered before the session died stands; the rest is left
    # unresolved rather than judged on a failure that is not theirs.
    assert coordinator.device_ownership == {OWNED_ID: DeviceOwnership.OWNED}
    assert coordinator._ownership_unresolved == {SHARED_ID, MURKY_ID}


def test_the_classification_loop_runs_for_the_session() -> None:
    """It does not stop at "everything classified for now".

    The account is not a closed set: the cloud drops devices from a poll on
    its own, and accounts gain devices. A loop that exited would leave those
    with no verdict, no switch, and nothing in diagnostics explaining it —
    until someone reloaded the entry.
    """
    resolved = _coordinator(_devices(OWNED_ID), options={CONF_CLOUD_CONTROL: True})
    _enable(resolved, _FakeRelay({OWNED_ID: DeviceOwnership.OWNED}))
    assert len(resolved.background_tasks) == 1

    murky = _coordinator(_devices(MURKY_ID), options={CONF_CLOUD_CONTROL: True})
    _enable(murky, _FakeRelay({MURKY_ID: DeviceOwnership.UNKNOWN}))
    assert len(murky.background_tasks) == 1


def test_the_loop_reclassifies_and_tells_the_platforms(monkeypatch) -> None:
    """The loop itself, run for real rather than asserted about."""
    monkeypatch.setattr(coordinator_module, "OWNERSHIP_REPROBE_INTERVAL_S", 0.01)
    dispatched: list[tuple[str, str]] = []
    monkeypatch.setattr(
        coordinator_module,
        "async_dispatcher_send",
        lambda hass, signal, device_id: dispatched.append((signal, device_id)),
    )

    async def _scenario() -> ShellyCloudCoordinator:
        coordinator = _coordinator(
            _devices(MURKY_ID), options={CONF_CLOUD_CONTROL: True}, real_tasks=True
        )
        await coordinator.async_enable_cloud_control(
            _FakeRelay({MURKY_ID: DeviceOwnership.UNKNOWN})
        )
        assert coordinator.device_ownership == {}, "no verdict was formed"

        # The relay is back and now answers definitely.
        coordinator._cloud_ws = _FakeRelay({MURKY_ID: DeviceOwnership.OWNED})
        for _ in range(200):
            await asyncio.sleep(0.01)
            if coordinator.device_ownership:
                break
        await coordinator.async_disable_cloud_control()
        return coordinator

    coordinator = asyncio.run(_scenario())

    assert dispatched == [(SIGNAL_NEW_DEVICE, MURKY_ID)], (
        "the platform has to be told, or the switch is only built on a reload"
    )
    assert coordinator._ownership_task is None, "teardown cancels the loop"


def test_a_device_that_appears_after_setup_is_classified_too(monkeypatch) -> None:
    """The first snapshot is not a closed set — the cloud omits devices."""
    monkeypatch.setattr(coordinator_module, "OWNERSHIP_REPROBE_INTERVAL_S", 0.01)
    dispatched: list[tuple[str, str]] = []
    monkeypatch.setattr(
        coordinator_module,
        "async_dispatcher_send",
        lambda hass, signal, device_id: dispatched.append((signal, device_id)),
    )

    async def _scenario() -> ShellyCloudCoordinator:
        # Nothing to probe at setup: the device is not in the snapshot yet.
        coordinator = _coordinator(
            {}, options={CONF_CLOUD_CONTROL: True}, real_tasks=True
        )
        relay = _FakeRelay({OWNED_ID: DeviceOwnership.OWNED})
        await coordinator.async_enable_cloud_control(relay)
        assert relay.probes == []

        coordinator.devices.update(_devices(OWNED_ID))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if coordinator.device_ownership:
                break
        await coordinator.async_disable_cloud_control()
        return coordinator

    coordinator = asyncio.run(_scenario())

    assert dispatched == [(SIGNAL_NEW_DEVICE, OWNED_ID)]


# ── 3. Commands: never optimistic, never quiet ────────────────────────


def test_a_successful_command_asks_the_poll_for_the_truth() -> None:
    relay = _FakeRelay({OWNED_ID: DeviceOwnership.OWNED})
    coordinator = _coordinator(
        _devices(OWNED_ID), options={CONF_CLOUD_CONTROL: True}
    )
    _enable(coordinator, relay)

    asyncio.run(coordinator.async_set_virtual_boolean(OWNED_ID, "boolean:200", True))

    assert relay.sent == [
        (OWNED_ID, "Boolean.Set", {"id": 200, "value": True})
    ]
    assert coordinator.refreshes == [1], "the state comes from the poll, not from us"


def test_a_refused_command_raises() -> None:
    relay = _FakeRelay(
        {OWNED_ID: DeviceOwnership.OWNED},
        command_error=ShellyCloudWsCommandError("refused", code="WRONG_ID"),
    )
    coordinator = _coordinator(
        _devices(OWNED_ID), options={CONF_CLOUD_CONTROL: True}
    )
    _enable(coordinator, relay)

    with pytest.raises(ShellyCloudWsCommandError):
        asyncio.run(
            coordinator.async_set_virtual_boolean(OWNED_ID, "boolean:200", True)
        )
    assert coordinator.refreshes == [], "a failed command confirms nothing"


def test_an_unanswered_command_raises_rather_than_reporting_success() -> None:
    relay = _FakeRelay(
        {OWNED_ID: DeviceOwnership.OWNED},
        command_error=ShellyCloudWsTimeoutError("no answer"),
    )
    coordinator = _coordinator(
        _devices(OWNED_ID), options={CONF_CLOUD_CONTROL: True}
    )
    _enable(coordinator, relay)

    with pytest.raises(ShellyCloudWsTimeoutError):
        asyncio.run(
            coordinator.async_set_virtual_boolean(OWNED_ID, "boolean:200", False)
        )


def test_a_command_without_a_connection_raises() -> None:
    coordinator = _coordinator(_devices(OWNED_ID))
    with pytest.raises(HomeAssistantError):
        asyncio.run(
            coordinator.async_set_virtual_boolean(OWNED_ID, "boolean:200", True)
        )


def test_a_command_to_a_device_the_relay_will_not_route_to_raises() -> None:
    coordinator = _coordinator(
        _devices(SHARED_ID), options={CONF_CLOUD_CONTROL: True}
    )
    _enable(coordinator, _FakeRelay({SHARED_ID: DeviceOwnership.NOT_ROUTABLE}))

    with pytest.raises(HomeAssistantError):
        asyncio.run(
            coordinator.async_set_virtual_boolean(SHARED_ID, "boolean:200", True)
        )


def test_the_switch_entity_drives_the_coordinator_and_reads_the_poll() -> None:
    relay = _FakeRelay({OWNED_ID: DeviceOwnership.OWNED})
    coordinator = _coordinator(
        _devices(OWNED_ID), options={CONF_CLOUD_CONTROL: True}
    )
    _enable(coordinator, relay)

    entity = next(
        e
        for e in _run_switch_setup(coordinator)
        if e.unique_id == f"{OWNED_ID}_boolean:200_control"
    )
    assert entity.is_on is False

    asyncio.run(entity.async_turn_on())
    assert relay.sent == [(OWNED_ID, "Boolean.Set", {"id": 200, "value": True})]
    # Still off: nothing was written optimistically, the poll has not run yet.
    assert entity.is_on is False


def test_the_control_switch_goes_unavailable_when_the_channel_does() -> None:
    """A switch that renders as operable while every press throws is a lie."""
    relay = _FakeRelay({OWNED_ID: DeviceOwnership.OWNED})
    coordinator = _coordinator(
        _devices(OWNED_ID), options={CONF_CLOUD_CONTROL: True}
    )
    _enable(coordinator, relay)

    added = _run_switch_setup(coordinator)
    control = next(e for e in added if e.unique_id.endswith("_control"))
    relay_switch = next(e for e in added if e.unique_id.endswith("_switch_0"))
    assert control.available is True

    relay._connected = False
    assert control.available is False
    # The entity that runs over the documented HTTP path is unaffected.
    assert relay_switch.available is True


def test_the_switch_borrows_the_name_the_read_only_sensor_uses() -> None:
    coordinator = _coordinator(
        _devices(OWNED_ID), options={CONF_CLOUD_CONTROL: True}
    )
    coordinator.virtual_configs = {
        OWNED_ID: {
            "boolean:200": {"role": "zone0"},
            "service:0": {"zones": [{"name": "Vegetable bed"}]},
        }
    }
    _enable(coordinator, _FakeRelay({OWNED_ID: DeviceOwnership.OWNED}))

    entity = next(
        e
        for e in _run_switch_setup(coordinator)
        if e.unique_id == f"{OWNED_ID}_boolean:200_control"
    )
    assert entity.name == "Vegetable bed"


# ── 4. Teardown ───────────────────────────────────────────────────────


def test_disabling_closes_the_relay_and_drops_the_session_verdicts() -> None:
    relay = _FakeRelay({OWNED_ID: DeviceOwnership.OWNED})
    coordinator = _coordinator(
        _devices(OWNED_ID), options={CONF_CLOUD_CONTROL: True}
    )
    _enable(coordinator, relay)

    asyncio.run(coordinator.async_disable_cloud_control())

    assert relay.disconnect_calls == 1
    assert coordinator.device_ownership == {}
    assert coordinator._ownership_unresolved == set()
    assert coordinator._cloud_ws is None


# ── 4b. Setup: an optional channel must never cost the poll ───────────


def _setup_cloud_control(
    monkeypatch,
    entry: Any,
    coordinator: ShellyCloudCoordinator,
    *,
    failure: Exception | None = None,
) -> tuple[list[Any], _FakeRelay]:
    """Run the entry-level wiring with the transport faked out.

    Returns the re-authentication requests it made, and the relay it built,
    so a caller can check the socket was closed behind a failure.
    """
    relay = _FakeRelay()

    async def _enable(ws: Any) -> None:
        if failure is not None:
            raise failure
        coordinator._cloud_ws = ws

    coordinator.async_enable_cloud_control = _enable

    reauths: list[Any] = []
    monkeypatch.setattr(
        integration, "async_get_clientsession", lambda hass: SimpleNamespace()
    )
    monkeypatch.setattr(
        integration,
        "ShellyTokenManager",
        lambda *args, **kwargs: SimpleNamespace(
            async_get_token=None, async_force_refresh=None
        ),
    )
    monkeypatch.setattr(
        integration, "ShellyCloudWebSocket", lambda *args, **kwargs: relay
    )
    monkeypatch.setattr(
        integration,
        "_start_cloud_control_reauth",
        lambda hass, config_entry: reauths.append(config_entry),
    )

    asyncio.run(
        integration._async_setup_cloud_control(
            SimpleNamespace(), entry, coordinator
        )
    )
    return reauths, relay


def test_a_relay_that_will_not_connect_costs_the_switches_and_nothing_else(
    monkeypatch, caplog
) -> None:
    """Setup must survive it: the entry's real job is the poll."""
    entry = _Entry({CONF_CLOUD_CONTROL: True})
    coordinator = _coordinator(_devices(OWNED_ID), options=entry.options)

    with caplog.at_level(logging.DEBUG):
        reauths, relay = _setup_cloud_control(
            monkeypatch,
            entry,
            coordinator,
            failure=ShellyCloudWsTimeoutError("relay did not connect"),
        )

    assert reauths == [], "a dead relay is not a credential problem"
    assert coordinator._cloud_ws is None
    assert relay.disconnect_calls == 1, "a half-open socket must not be left behind"
    assert [e.unique_id for e in _run_switch_setup(coordinator)] == [
        f"{OWNED_ID}_switch_0"
    ]
    for secret in (FAKE_ACCESS, FAKE_REFRESH):
        assert secret not in caplog.text


def test_a_spent_sign_in_asks_the_user_instead_of_failing_the_entry(
    monkeypatch,
) -> None:
    from homeassistant.exceptions import ConfigEntryAuthFailed

    entry = _Entry({CONF_CLOUD_CONTROL: True})
    coordinator = _coordinator(_devices(OWNED_ID), options=entry.options)

    reauths, relay = _setup_cloud_control(
        monkeypatch,
        entry,
        coordinator,
        failure=ConfigEntryAuthFailed("no refresh token"),
    )

    assert reauths == [entry]
    assert relay.disconnect_calls == 1


def test_an_entry_with_no_stored_sign_in_asks_for_one_without_connecting(
    monkeypatch,
) -> None:
    entry = _Entry({CONF_CLOUD_CONTROL: True})
    del entry.data[CONF_OAUTH_TOKEN]
    coordinator = _coordinator(_devices(OWNED_ID), options=entry.options)

    reauths, relay = _setup_cloud_control(monkeypatch, entry, coordinator)

    assert reauths == [entry]
    assert coordinator._cloud_ws is None
    assert relay.connect_calls == 0, "nothing is contacted without a sign-in"


# ── 4c. Entry lifecycle: reloads, token storage, reauth routing ──────


def _flow_hass(entry: Any) -> Any:
    """A hass stub an options flow can read its entry through."""
    updates: list[dict[str, Any]] = []

    def _update(config_entry: Any, **kwargs: Any) -> None:
        if "data" in kwargs:
            config_entry.data = dict(kwargs["data"])
        updates.append(kwargs)

    reloads: list[str] = []
    return SimpleNamespace(
        config_entries=SimpleNamespace(
            async_get_known_entry=lambda entry_id: entry,
            async_get_entry=lambda entry_id: entry,
            async_update_entry=_update,
            async_schedule_reload=reloads.append,
        ),
        updates=updates,
        reloads=reloads,
    )


def _options_flow(entry: Any) -> Any:
    flow = config_flow.ShellyCloudDiyOptionsFlow()
    flow.hass = _flow_hass(entry)
    flow.handler = entry.entry_id
    return flow


def test_a_token_refresh_does_not_reload_the_entry() -> None:
    """A rotated token is a write to entry.data, not a reason to reload.

    Home Assistant fires the update listener for both, and reloading would
    tear the poll down and rebuild every entity because a background refresh
    happened.
    """
    reloads: list[str] = []
    entry = _Entry({CONF_CLOUD_CONTROL: True})
    hass = SimpleNamespace(
        data={
            "shelly_cloud_diy": {
                f"{entry.entry_id}_options": dict(entry.options),
            }
        },
        config_entries=SimpleNamespace(
            async_reload=_recorder(reloads),
        ),
    )

    asyncio.run(integration._async_options_updated(hass, entry))
    assert reloads == []

    # An actual options change still reloads.
    entry.options = {CONF_CLOUD_CONTROL: False}
    asyncio.run(integration._async_options_updated(hass, entry))
    assert reloads == [entry.entry_id]


def _recorder(sink: list[str]) -> Any:
    async def _record(entry_id: str) -> None:
        sink.append(entry_id)

    return _record


def test_a_refresh_never_writes_a_token_back_into_an_entry_that_has_none(
    monkeypatch,
) -> None:
    """The user switched cloud control off; a refresh in flight must not undo it."""
    entry = _Entry({CONF_CLOUD_CONTROL: True})
    coordinator = _coordinator(_devices(OWNED_ID), options=entry.options)
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        integration, "async_get_clientsession", lambda hass: SimpleNamespace()
    )
    monkeypatch.setattr(
        integration, "ShellyCloudWebSocket", lambda *a, **k: _FakeRelay()
    )
    monkeypatch.setattr(
        integration,
        "ShellyTokenManager",
        lambda *args, **kwargs: captured.setdefault(
            "manager", SimpleNamespace(**kwargs, async_get_token=None,
                                       async_force_refresh=None)
        ),
    )
    monkeypatch.setattr(
        integration, "_start_cloud_control_reauth", lambda hass, e: None
    )

    updates: list[Any] = []
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_update_entry=lambda e, **kw: updates.append(kw)
        )
    )

    async def _enable(ws: Any) -> None:
        coordinator._cloud_ws = ws

    coordinator.async_enable_cloud_control = _enable
    asyncio.run(integration._async_setup_cloud_control(hass, entry, coordinator))

    persist = captured["manager"].on_token_refreshed
    rotated = OAuthToken(
        access_token=FAKE_ACCESS, expires_at=99.0, refresh_token="ROTATED-FAKE"
    )

    # A rotation while the token is still stored is written through …
    asyncio.run(persist(rotated))
    assert len(updates) == 1

    # … but once the user has deleted it, nothing puts it back.
    del entry.data[CONF_OAUTH_TOKEN]
    asyncio.run(persist(rotated))
    assert len(updates) == 1


def test_reauth_routes_to_the_credential_that_was_actually_rejected() -> None:
    """Both credentials fail into one flow, so the caller has to say which."""
    from custom_components.shelly_cloud_diy.const import REAUTH_CLOUD_CONTROL

    flow = config_flow.ShellyCloudDiyConfigFlow()
    entry = _Entry({CONF_CLOUD_CONTROL: True})
    flow.hass = _flow_hass(entry)
    flow.context = {"entry_id": entry.entry_id}

    cloud = asyncio.run(flow.async_step_reauth({REAUTH_CLOUD_CONTROL: True}))
    assert cloud["step_id"] == "reauth_cloud_control"

    key = asyncio.run(flow.async_step_reauth({}))
    assert key["step_id"] == "reauth_confirm"


def test_the_options_flow_stores_the_token_only_with_the_option(monkeypatch) -> None:
    """A flow abandoned after the sign-in must leave nothing behind."""
    entry = _Entry({})
    del entry.data[CONF_OAUTH_TOKEN]
    flow = _options_flow(entry)
    flow._pending_base_options = {CONF_CLOUD_CONTROL: True}

    monkeypatch.setattr(
        config_flow,
        "_async_sign_in",
        _fake_sign_in(
            OAuthToken(
                access_token=FAKE_ACCESS, expires_at=1.0, refresh_token=FAKE_REFRESH
            )
        ),
    )

    assert flow._needs_cloud_sign_in() is True
    asyncio.run(
        flow.async_step_cloud_control({"email": FAKE_EMAIL, "password": FAKE_PASSWORD})
    )
    # Signed in — and nothing written: the user could still close the dialog.
    assert CONF_OAUTH_TOKEN not in entry.data
    assert flow._needs_cloud_sign_in() is False

    flow._save({CONF_CLOUD_CONTROL: True})
    assert entry.data[CONF_OAUTH_TOKEN]["access_token"] == FAKE_ACCESS


def test_switching_the_option_off_deletes_the_stored_sign_in() -> None:
    entry = _Entry({CONF_CLOUD_CONTROL: True})
    flow = _options_flow(entry)

    flow._save({CONF_CLOUD_CONTROL: False})

    assert CONF_OAUTH_TOKEN not in entry.data
    assert entry.data["auth_key"], "the poll's own credential is untouched"


def test_skipping_the_device_step_keeps_the_device_selection() -> None:
    """Saving replaces the options wholesale, so the selection must be carried.

    Without this a cloud hiccup during an options save would drop the user's
    curated device list — and the coordinator's "no explicit selection"
    fallback would then materialise every device on the account.
    """
    entry = _Entry({CONF_CLOUD_CONTROL: False, "enabled_devices": [OWNED_ID]})
    flow = _options_flow(entry)
    flow._pending_base_options = {CONF_CLOUD_CONTROL: False}

    flow._carry_device_selection()

    assert flow._pending_base_options["enabled_devices"] == [OWNED_ID]


def _fake_sign_in(token: OAuthToken | None) -> Any:
    async def _sign_in(hass: Any, server_uri: str, user_input: dict) -> Any:
        return token, {}

    return _sign_in


# ── 5. Nothing secret escapes ─────────────────────────────────────────


def test_the_diagnostics_block_carries_no_token_and_no_raw_device_id() -> None:
    entry = _Entry({CONF_CLOUD_CONTROL: True, "create_all_initially": True})
    coordinator = _coordinator(
        _devices(OWNED_ID, SHARED_ID, MURKY_ID),
        options=entry.options,
        ws=_FakeRelay(),
    )
    coordinator.device_ownership = {
        OWNED_ID: DeviceOwnership.OWNED,
        SHARED_ID: DeviceOwnership.NOT_ROUTABLE,
    }
    coordinator._ownership_unresolved = {MURKY_ID}

    dumped = json.dumps(diagnostics._config_diagnostics(entry, coordinator))

    for secret in (FAKE_ACCESS, FAKE_REFRESH, "NOTAREALKEY-shouldneverappear"):
        assert secret not in dumped
    for device_id in (OWNED_ID, SHARED_ID, MURKY_ID):
        assert device_id not in dumped

    block = diagnostics._config_diagnostics(entry, coordinator)["cloud_control"]
    assert block["mode"] == "on"
    assert (block["owned"], block["not_routable"], block["unclassified"]) == (1, 1, 1)


def test_the_diagnostics_block_says_off_without_reading_anything_else() -> None:
    entry = _Entry({})
    block = diagnostics._config_diagnostics(entry, None)["cloud_control"]
    assert block == {"mode": "off"}


def test_signing_in_never_logs_the_password_or_its_digest(monkeypatch, caplog) -> None:
    """The one place a plaintext password exists, and the one that must not talk."""
    from custom_components.shelly_cloud_diy.api.oauth import (
        ShellyOAuthError,
        sha1_password,
    )

    seen: dict[str, Any] = {}

    async def _fake_login(session, server_uri, email, password_sha1):
        seen["email"] = email
        seen["digest"] = password_sha1
        raise ShellyOAuthError("OAuth request rejected (HTTP 401)")

    monkeypatch.setattr(config_flow, "login", _fake_login)
    monkeypatch.setattr(
        config_flow, "async_get_clientsession", lambda hass: SimpleNamespace()
    )

    with caplog.at_level(logging.DEBUG):
        token, errors = asyncio.run(
            config_flow._async_sign_in(
                SimpleNamespace(),
                SERVER_URI,
                {"email": FAKE_EMAIL, "password": FAKE_PASSWORD},
            )
        )

    assert token is None
    assert errors == {"base": "invalid_account"}
    # The plaintext never left the boundary, and neither half is in the log.
    assert seen["digest"] == sha1_password(FAKE_PASSWORD)
    assert FAKE_PASSWORD not in caplog.text
    assert seen["digest"] not in caplog.text
    assert FAKE_PASSWORD not in json.dumps(errors)


def test_a_stored_token_survives_a_round_trip_and_a_broken_one_does_not() -> None:
    token = OAuthToken(
        access_token=FAKE_ACCESS, expires_at=123.5, refresh_token=FAKE_REFRESH
    )
    restored = token_from_storage(token_to_storage(token))

    assert restored == token
    # A record whose expiry is unreadable means "refresh at once", never
    # "valid forever".
    assert token_from_storage({"access_token": FAKE_ACCESS}).expires_at == 0.0
    assert token_from_storage({"refresh_token": FAKE_REFRESH}) is None
    assert token_from_storage(None) is None
    # And the repr of what we store still refuses to print the token.
    assert FAKE_ACCESS not in repr(restored)


# ── helpers ───────────────────────────────────────────────────────────


def _run_switch_setup(coordinator: ShellyCloudCoordinator) -> list[Any]:
    """Run the real switch platform setup against a hand-built coordinator.

    The dispatcher is stubbed out for the same reason the other platform
    tests stub it: connecting a signal needs a running Home Assistant, and
    what is under test here is which entities the builder returns.
    """
    added: list[Any] = []
    hass = SimpleNamespace(data={"shelly_cloud_diy": {"e1": coordinator}})
    entry = SimpleNamespace(entry_id="e1", async_on_unload=lambda cb: None)

    original = switch_platform.async_dispatcher_connect
    switch_platform.async_dispatcher_connect = lambda *args, **kwargs: (lambda: None)
    try:
        asyncio.run(
            switch_platform.async_setup_entry(
                hass, entry, lambda ents: added.extend(ents)
            )
        )
    finally:
        switch_platform.async_dispatcher_connect = original
    return added


# ── Regressions found in review, after the feature was already written ──


def test_a_failed_setup_cannot_strand_the_relay() -> None:
    """Two teardown paths, because neither one covers both failures.

    Home Assistant never calls a component's ``async_unload_entry`` for an
    entry that is not LOADED — ``ConfigEntry.async_unload`` short-circuits —
    so a setup that raises after the channel is up would leave the socket,
    its reconnect ladder and the ownership loop running forever, refreshing
    a token for an entry that never came up. The on-unload callback covers
    that for the ``ConfigEntry*`` failures; it does NOT cover the generic
    exception branch, which is the only one Home Assistant does not process
    on-unload callbacks for. Hence both, and hence a static test: this is a
    positional property no behavioural test of a working setup can see.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(integration.async_setup_entry)))
    dumped = ast.dump(tree)

    assert "async_on_unload" in dumped and "async_disable_cloud_control" in dumped, (
        "setup must register the channel teardown as an on-unload callback"
    )

    handlers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        and "async_disable_cloud_control" in ast.dump(node)
    ]
    assert handlers, (
        "the rest of setup must be wrapped so the generic-exception branch, "
        "where Home Assistant runs no on-unload callbacks, still closes the "
        "channel"
    )
    # The handler must re-raise: swallowing here would report a broken entry
    # as set up.
    assert any(
        isinstance(node, ast.Raise) and node.exc is None
        for handler in handlers
        for node in ast.walk(handler)
    ), "the handler must re-raise, not swallow the setup failure"


def test_a_fresh_sign_in_with_unchanged_options_still_reloads() -> None:
    """Writing a token that nothing acts on is the same as not writing it.

    Reachable, not theoretical: when a token is rejected the entry keeps
    ``cloud_control: True`` with no usable credential, and a user who signs
    in again through Options rather than the repair card changes no option at
    all. Neither write reaches a listener then — the data write is correctly
    ignored because the options did not change, and Home Assistant fires no
    listener for an options write that changes nothing — so without an
    explicit reload the entry would sit with a valid token and a dead channel
    until the next restart.
    """
    entry = _Entry({CONF_CLOUD_CONTROL: True})
    del entry.data[CONF_OAUTH_TOKEN]  # the state a rejected session leaves
    flow = _options_flow(entry)
    flow._pending_token = OAuthToken(
        access_token=FAKE_ACCESS, expires_at=9e9, refresh_token=FAKE_REFRESH
    )

    flow._save(dict(entry.options))

    assert CONF_OAUTH_TOKEN in entry.data, "the sign-in must be stored"
    assert flow.hass.reloads == [entry.entry_id], (
        "a stored sign-in that nothing reloads leaves the channel dead"
    )


def test_an_options_change_is_not_reloaded_twice() -> None:
    """The explicit reload is only for the case no listener will cover."""
    entry = _Entry({CONF_CLOUD_CONTROL: False})
    flow = _options_flow(entry)
    flow._pending_token = OAuthToken(
        access_token=FAKE_ACCESS, expires_at=9e9, refresh_token=FAKE_REFRESH
    )

    # Options genuinely change, so the ordinary listener path reloads.
    flow._save({CONF_CLOUD_CONTROL: True})

    assert flow.hass.reloads == [], "the update listener already covers this"
