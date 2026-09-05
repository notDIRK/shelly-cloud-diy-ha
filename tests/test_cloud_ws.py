"""Unit tests for the Shelly Cloud WebSocket relay transport.

The relay is a command channel for devices the documented HTTP API cannot
reach, and its first consumer switches irrigation valves. Three properties
matter more than any individual call:

1. **Failure is loud.** Every path raises a typed error. A caller must never be
   able to confuse "the zone switched" with "nobody answered".
2. **``WRONG_ID`` is a verdict, not a fault.** It is the measured signal for a
   device this session cannot route to — measured *with* a negative control,
   since deliberately malformed ids return the identical error. Treating it as a
   transport problem would put the connection into a reconnect loop over a
   perfectly healthy socket.
3. **The access token rides in the connect URL**, so a handshake failure hands
   ``aiohttp`` a full token-bearing URL that its own ``__str__`` will happily
   print at ERROR level. The tests reproduce exactly that and then search
   everything the code produced for the token.

Driven through the real transport with a hand-rolled fake socket: no network,
no running Home Assistant, and every credential a fake literal.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import aiohttp
import pytest
import yarl
from multidict import CIMultiDict, CIMultiDictProxy

from custom_components.shelly_cloud_diy.api import cloud_ws
from custom_components.shelly_cloud_diy.api.cloud_ws import (
    DeviceOwnership,
    ShellyCloudWebSocket,
    ShellyCloudWsAuthError,
    ShellyCloudWsCommandError,
    ShellyCloudWsNotConnectedError,
    ShellyCloudWsTimeoutError,
    ShellyCloudWsTransportError,
)

FAKE_TOKEN = "FAKE-ACCESS-TOKEN-do-not-log-55555555"  # noqa: S105
ACCOUNT_HOST = "shelly-42-eu.shelly.cloud"
SERVER_URI = f"https://{ACCOUNT_HOST}"
DEVICE_ID = "5432044e0001"

_REAL_SLEEP = asyncio.sleep
_CLOSE = object()


def _text_frame(payload: dict[str, Any]) -> SimpleNamespace:
    """One inbound TEXT message, shaped like ``aiohttp.WSMessage``."""
    return SimpleNamespace(
        type=aiohttp.WSMsgType.TEXT, data=json.dumps(payload), extra=None
    )


class _FakeWebSocket:
    """A socket that answers what a ``responder`` decides, and nothing else."""

    def __init__(
        self,
        responder=None,
        *,
        close_code: int | None = None,
        close_immediately: bool = False,
    ) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.close_code = close_code
        self._responder = responder
        self._queue: asyncio.Queue = asyncio.Queue()
        if close_immediately:
            self._queue.put_nowait(_CLOSE)

    async def send_json(self, frame: dict[str, Any]) -> None:
        self.sent.append(frame)
        if self._responder is None:
            return
        reply = self._responder(frame)
        if reply is not None:
            self._queue.put_nowait(_text_frame(reply))

    def push(self, frame: dict[str, Any]) -> None:
        """Deliver an unsolicited frame, the way a push-enabled relay would."""
        self._queue.put_nowait(_text_frame(frame))

    def __aiter__(self) -> _FakeWebSocket:
        return self

    async def __anext__(self) -> SimpleNamespace:
        message = await self._queue.get()
        if message is _CLOSE:
            raise StopAsyncIteration
        return message

    async def close(self) -> None:
        self.closed = True
        self._queue.put_nowait(_CLOSE)

    def exception(self) -> BaseException | None:
        return None


class _FakeSession:
    """Hands out sockets (or raises) and records every URL it was given."""

    def __init__(self, factory) -> None:
        self._factory = factory
        self.urls: list[str] = []

    async def ws_connect(self, url: str, **kwargs: Any):  # noqa: ANN401
        self.urls.append(url)
        result = self._factory(len(self.urls) - 1)
        if isinstance(result, BaseException):
            raise result
        return result


def _client(session: _FakeSession, **kwargs: Any) -> ShellyCloudWebSocket:
    async def _token() -> str:
        return FAKE_TOKEN

    return ShellyCloudWebSocket(session, SERVER_URI, _token, **kwargs)


def _answer(response: dict[str, Any]):
    """A responder that answers every request with ``response``."""

    def responder(frame: dict[str, Any]) -> dict[str, Any]:
        return {
            "event": "Shelly:JrpcResponse",
            "trid": frame["trid"],
            "deviceId": frame["deviceId"],
            "response": response,
        }

    return responder


def _handshake_error(url: str, status: int) -> aiohttp.WSServerHandshakeError:
    """The real aiohttp error — its ``__str__`` embeds the token-bearing URL."""
    parsed = yarl.URL(url)
    info = aiohttp.RequestInfo(
        url=parsed, method="GET", headers=CIMultiDictProxy(CIMultiDict()), real_url=parsed
    )
    return aiohttp.WSServerHandshakeError(
        info, (), status=status, message="Invalid response status"
    )


# ── The round trip ──────────────────────────────────────────────────────────


def test_a_command_round_trip_returns_the_rpc_result():
    """The generic relay call: a device's own RPC, sent through the cloud."""

    async def scenario():
        ws = _FakeWebSocket(_answer({"was_on": False}))
        client = _client(_FakeSession(lambda _: ws))
        await client.connect()
        result = await client.send_jrpc_request(
            DEVICE_ID, "Boolean.Set", {"id": 200, "value": True}
        )
        await client.disconnect()
        return ws.sent, result

    sent, result = asyncio.run(scenario())

    assert result == {"was_on": False}
    assert sent == [
        {
            "event": "Shelly:JrpcRequest",
            "trid": 1,
            "deviceId": DEVICE_ID,
            "method": "Boolean.Set",
            "params": {"id": 200, "value": True},
        }
    ]


def test_a_silent_relay_raises_instead_of_returning_none():
    """The harvested transport returned ``None`` here. That is the bug this slice fixes.

    "No answer" is the single most dangerous outcome for a valve: the caller
    must be told, not handed a falsy value that reads like a normal result.
    """

    async def scenario():
        client = _client(_FakeSession(lambda _: _FakeWebSocket(lambda frame: None)))
        await client.connect()
        try:
            with pytest.raises(ShellyCloudWsTimeoutError):
                await client.send_jrpc_request(DEVICE_ID, "Boolean.Set", timeout=0.05)
        finally:
            await client.disconnect()

    asyncio.run(scenario())


def test_sending_without_a_connection_raises():
    """A command issued before the transport is up is an error, not a no-op."""
    client = _client(_FakeSession(lambda _: _FakeWebSocket()))
    with pytest.raises(ShellyCloudWsNotConnectedError):
        asyncio.run(client.send_jrpc_request(DEVICE_ID, "Boolean.Set"))


def test_a_dropped_connection_fails_the_request_in_flight():
    """A lost answer must surface immediately, not after the full timeout.

    Waiting out the timeout would be survivable; silently resolving would not.
    """

    async def scenario():
        ws = _FakeWebSocket(lambda frame: None)
        client = _client(_FakeSession(lambda _: ws))
        await client.connect()
        pending = asyncio.create_task(
            client.send_jrpc_request(DEVICE_ID, "Boolean.Set", timeout=30)
        )
        await _REAL_SLEEP(0)
        await ws.close()
        with pytest.raises(ShellyCloudWsNotConnectedError):
            await asyncio.wait_for(pending, timeout=1)
        await client.disconnect()

    asyncio.run(scenario())


def test_a_failed_first_connection_is_reported_to_the_caller():
    """An opt-in control channel that cannot come up has to say so at setup."""

    async def scenario():
        session = _FakeSession(lambda _: aiohttp.ClientConnectorError(None, OSError("no route")))
        client = _client(session)
        with pytest.raises(ShellyCloudWsTransportError):
            await client.connect()
        assert client.connected is False
        # No background task is left retrying behind the caller's back.
        assert client._task is None  # noqa: SLF001

    asyncio.run(scenario())


# ── Ownership ───────────────────────────────────────────────────────────────


def test_an_answering_device_is_owned():
    async def scenario():
        client = _client(
            _FakeSession(lambda _: _FakeWebSocket(_answer({"id": DEVICE_ID, "gen": 3})))
        )
        await client.connect()
        verdict = await client.async_classify_ownership(DEVICE_ID)
        await client.disconnect()
        return verdict

    assert asyncio.run(scenario()) is DeviceOwnership.OWNED


def test_wrong_id_is_a_verdict_about_the_device_not_a_transport_fault():
    """``WRONG_ID`` means "this session cannot route there" — shared, or gone.

    The connection is healthy afterwards and nothing escalates: a refused route
    must not look like a broken socket, or a single shared device would put the
    whole transport into a reconnect loop.
    """

    async def scenario():
        client = _client(_FakeSession(lambda _: _FakeWebSocket(_answer({"error": "WRONG_ID"}))))
        await client.connect()
        verdict = await client.async_classify_ownership(DEVICE_ID)
        state = (client.connected, client.auth_failed)
        with pytest.raises(ShellyCloudWsCommandError) as excinfo:
            await client.send_jrpc_request(DEVICE_ID, "Boolean.Set", {"id": 200})
        await client.disconnect()
        return verdict, state, excinfo.value

    verdict, (connected, auth_failed), err = asyncio.run(scenario())

    assert verdict is DeviceOwnership.NOT_ROUTABLE
    assert connected is True
    assert auth_failed is False
    assert err.code == "WRONG_ID"
    # A command failure is still a failure — the classifier is what interprets
    # the code, the command path must not swallow it.
    assert not isinstance(err, ShellyCloudWsTimeoutError)


def test_an_unfamiliar_relay_error_is_unknown_rather_than_a_guess():
    """``UNKNOWN`` keeps a probe that could not decide from posing as one that did."""

    async def scenario():
        client = _client(
            _FakeSession(lambda _: _FakeWebSocket(_answer({"error": "BAD_REQUEST"})))
        )
        await client.connect()
        verdict = await client.async_classify_ownership(DEVICE_ID)
        await client.disconnect()
        return verdict

    assert asyncio.run(scenario()) is DeviceOwnership.UNKNOWN


def test_a_silent_relay_makes_ownership_unknown_not_not_routable():
    """A timeout says nothing about ownership, and must not be read as a refusal."""

    async def scenario():
        client = _client(
            _FakeSession(lambda _: _FakeWebSocket(lambda frame: None)),
            request_timeout_s=0.05,
        )
        await client.connect()
        verdict = await client.async_classify_ownership(DEVICE_ID)
        await client.disconnect()
        return verdict

    assert asyncio.run(scenario()) is DeviceOwnership.UNKNOWN


def test_the_ownership_probe_is_one_cheap_call():
    """One ``Shelly.GetDeviceInfo`` per device, per session — not per poll."""

    async def scenario():
        ws = _FakeWebSocket(_answer({"id": DEVICE_ID}))
        client = _client(_FakeSession(lambda _: ws))
        await client.connect()
        await client.async_classify_ownership(DEVICE_ID)
        await client.disconnect()
        return ws.sent

    sent = asyncio.run(scenario())
    assert [frame["method"] for frame in sent] == ["Shelly.GetDeviceInfo"]
    assert sent[0]["params"] == {}


# ── Authentication ──────────────────────────────────────────────────────────


def test_unauthorized_signals_reauth_and_does_not_retry():
    """A rejected session token stays rejected; only a human can fix it.

    So it raises, asks Home Assistant for re-authentication exactly once, and
    reconnects zero times — a retry loop here would hammer the account with a
    credential the relay has already refused.
    """
    reauths: list[int] = []

    async def scenario():
        client = _client(
            _FakeSession(lambda _: _FakeWebSocket(_answer({"error": "UNAUTHORIZED"}))),
            on_reauth=lambda: reauths.append(1),
        )
        await client.connect()
        with pytest.raises(ShellyCloudWsAuthError):
            await client.send_jrpc_request(DEVICE_ID, "Boolean.Set", {"id": 200})
        # Give any (unwanted) reconnect attempt room to happen.
        for _ in range(10):
            await _REAL_SLEEP(0)
        state = (client.auth_failed, len(client._session.urls))  # noqa: SLF001
        await client.disconnect()
        return state

    auth_failed, connect_attempts = asyncio.run(scenario())

    assert reauths == [1]
    assert auth_failed is True
    assert connect_attempts == 1


def test_a_rejected_handshake_refreshes_the_token_before_retrying(monkeypatch):
    """A 4401-style rejection on a live connection is worth one fresh token.

    The refresh hook runs before the backoff sleep, so the next attempt carries
    the new token rather than replaying the rejected one.
    """
    refreshes: list[int] = []
    sleeps: list[float] = []

    async def _refresh() -> str:
        refreshes.append(1)
        return FAKE_TOKEN

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)
        await _REAL_SLEEP(0)

    def factory(attempt: int):
        if attempt == 0:
            return _FakeWebSocket(close_code=4401, close_immediately=True)
        return _FakeWebSocket(_answer({"ok": True}))

    async def scenario():
        client = _client(_FakeSession(factory), on_token_rejected=_refresh)
        await client.connect()
        for _ in range(20):
            await _REAL_SLEEP(0)
        attempts = len(client._session.urls)  # noqa: SLF001
        await client.disconnect()
        return attempts

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    attempts = asyncio.run(scenario())

    assert refreshes == [1]
    assert attempts >= 2


# ── Reconnect ladder ────────────────────────────────────────────────────────


def test_the_backoff_grows_and_stays_bounded(monkeypatch):
    """A flapping relay must be backed off from, and never backed off forever.

    A connection that dies straight away is a failing connection wearing a
    clean-close hat, so it does not reset the ladder — otherwise the backoff
    switches itself off in exactly the situation it exists for. The cap is what
    guarantees the socket still comes back after a long cloud outage.
    """
    sleeps: list[float] = []
    enough = asyncio.Event()

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) >= 12:
            enough.set()
        await _REAL_SLEEP(0)

    async def scenario():
        client = _client(
            _FakeSession(lambda _: _FakeWebSocket(close_immediately=True))
        )
        await client.connect()
        await asyncio.wait_for(enough.wait(), timeout=5)
        await client.disconnect()

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    asyncio.run(scenario())

    ceiling = cloud_ws._RECONNECT_MAX_S * (1 + cloud_ws._RECONNECT_JITTER)  # noqa: SLF001
    assert all(0 < delay <= ceiling for delay in sleeps)
    assert sleeps[0] < sleeps[3] < sleeps[6]  # the ladder actually climbs
    assert max(sleeps) >= cloud_ws._RECONNECT_MAX_S  # and reaches its cap  # noqa: SLF001
    assert sleeps[-1] <= ceiling  # and stops there


def test_jitter_keeps_instances_from_reconnecting_in_lock_step(monkeypatch):
    """Identical clients must not all come back in the same second."""
    sleeps: list[float] = []

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) >= 8:
            raise asyncio.CancelledError
        await _REAL_SLEEP(0)

    async def scenario():
        client = _client(_FakeSession(lambda _: _FakeWebSocket(close_immediately=True)))
        await client.connect()
        for _ in range(200):
            await _REAL_SLEEP(0)
            if len(sleeps) >= 8:
                break
        await client.disconnect()

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    asyncio.run(scenario())

    # Every delay carries a random component, so no two runs are identical and
    # the values are not the bare ladder steps.
    assert any(delay != round(delay) for delay in sleeps)


# ── Push stays deleted ──────────────────────────────────────────────────────


def test_unsolicited_frames_are_dropped():
    """The poll owns state. A push frame must change nothing here.

    The fields the freshness bookkeeping reads are not in a relay frame, so a
    transport that fed them to the coordinator would corrupt the three
    hardware-confirmed detectors built on the poll.
    """

    async def scenario():
        ws = _FakeWebSocket(_answer({"ok": True}))
        client = _client(_FakeSession(lambda _: ws))
        await client.connect()
        ws.push({"event": "Shelly:StatusOnChange", "status": {"switch:0": {"output": True}}})
        ws.push({"event": "Shelly:Online", "online": True})
        await _REAL_SLEEP(0)
        result = await client.send_jrpc_request(DEVICE_ID, "Shelly.GetDeviceInfo")
        connected = client.connected
        await client.disconnect()
        return result, connected

    result, connected = asyncio.run(scenario())
    assert result == {"ok": True}
    assert connected is True


def test_the_module_carries_no_push_machinery():
    """A structural guard, because this deletion is the point of the slice.

    The file was renamed away from ``websocket.py`` so nobody re-adds the status
    path by reflex; this test is the half of that rename which does not depend
    on anyone noticing the name.
    """
    source = Path(cloud_ws.__file__).read_text(encoding="utf-8")
    for banned in (
        "StatusOnChangeRequest",
        "_request_status_after_connect",
        "message_handler",
        "Shelly:CommandRequest",
    ):
        assert banned not in source, f"push-era construct is back: {banned}"


# ── Secrecy ─────────────────────────────────────────────────────────────────


def test_the_token_never_reaches_a_log_record_or_a_repr(caplog):
    """The whole leak surface in one test.

    A handshake failure is the dangerous case: ``aiohttp``'s own error message
    contains the full ``?t=<token>`` URL, ERROR logging is on by default, and
    one careless ``%s`` would put an account credential into everybody's log.
    """
    caplog.set_level(logging.DEBUG)
    url = f"wss://{ACCOUNT_HOST}:6113/shelly/wss/hk_sock?t={FAKE_TOKEN}"
    assert FAKE_TOKEN in str(_handshake_error(url, 500))  # the trap is real

    async def scenario():
        # A working session first, so the DEBUG frame dumps are exercised too.
        ws = _FakeWebSocket(_answer({"ok": True}))
        client = _client(_FakeSession(lambda _: ws))
        await client.connect()
        await client.send_jrpc_request(DEVICE_ID, "Boolean.Set", {"id": 200})
        ws.push({"event": "Shelly:StatusOnChange", "t": FAKE_TOKEN})
        await _REAL_SLEEP(0)
        rendering = repr(client)
        await client.disconnect()

        # Then a handshake rejection, whose exception embeds the token URL.
        failing = _client(_FakeSession(lambda _: _handshake_error(url, 500)))
        with pytest.raises(ShellyCloudWsTransportError) as excinfo:
            await failing.connect()
        return rendering, str(excinfo.value)

    client_repr, error_message = asyncio.run(scenario())

    formatter = logging.Formatter("%(levelname)s %(name)s %(message)s")
    rendered = "\n".join(formatter.format(record) for record in caplog.records)
    assert rendered  # something must have been logged for the search to mean anything
    assert FAKE_TOKEN not in rendered
    assert FAKE_TOKEN not in client_repr
    assert FAKE_TOKEN not in error_message
    assert "?t=" not in error_message
    assert ACCOUNT_HOST in error_message  # still says which host failed


def test_an_auth_rejected_handshake_is_typed_and_sanitised():
    """A 401 handshake is an auth failure, and still carries no token."""
    url = f"wss://{ACCOUNT_HOST}:6113/shelly/wss/hk_sock?t={FAKE_TOKEN}"

    async def scenario():
        client = _client(_FakeSession(lambda _: _handshake_error(url, 401)))
        with pytest.raises(ShellyCloudWsAuthError) as excinfo:
            await client.connect()
        return str(excinfo.value)

    message = asyncio.run(scenario())
    assert FAKE_TOKEN not in message
    assert "401" in message


def test_redaction_covers_nested_frames():
    """``_redact`` is what stands between a DEBUG dump and a leaked token."""
    frame = {
        "event": "Shelly:JrpcRequest",
        "params": {"token": FAKE_TOKEN, "nested": [{"access_token": FAKE_TOKEN}]},
        "deviceId": DEVICE_ID,
    }
    rendered = json.dumps(cloud_ws._redact(frame))  # noqa: SLF001
    assert FAKE_TOKEN not in rendered
    assert DEVICE_ID in rendered  # a device id is not a secret; the frame stays useful
    assert frame["params"]["token"] == FAKE_TOKEN  # and the original is untouched


# ── Small pure helpers ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "server_uri",
    [ACCOUNT_HOST, f"https://{ACCOUNT_HOST}", f"https://{ACCOUNT_HOST}/", f"{ACCOUNT_HOST}:443"],
)
def test_the_host_is_normalised_from_whatever_the_entry_stored(server_uri):
    """The entry holds whatever the Shelly app showed the user."""
    assert ShellyCloudWebSocket._normalise_host(server_uri) == ACCOUNT_HOST  # noqa: SLF001


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("WRONG_ID", "WRONG_ID"),
        ({"message": "WRONG_ID"}, "WRONG_ID"),
        ({"code": -114}, "-114"),
        (["nonsense"], "UNKNOWN"),
        ("x" * 200, "x" * 64),
    ],
)
def test_relay_error_codes_are_reduced_to_something_short(error, expected):
    """The code goes into an exception message, so it is capped, not trusted."""
    assert cloud_ws._relay_error_code(error) == expected  # noqa: SLF001
