"""Shelly Cloud WebSocket relay — a command channel, and nothing else.

The cloud WebSocket is not a status feed for this integration. It is a generic
JSON-RPC relay: whatever RPC a Gen2+ device understands locally can be sent to
it through the cloud, which is the only way to reach components the documented
HTTP Cloud Control API has no route for at all (measured 2026-08-09: a
``Boolean.Set`` on a virtual component succeeded over the relay while every
documented HTTP route answered 404 — with a negative control, a known-good
``set/switch``, answering 400, so those 404s are genuinely "no such route").

**What this module deliberately does not do: push.** The transport it is
harvested from also handled ``Shelly:StatusOnChange`` frames and fed them to the
coordinator. All of that is removed, and the file was renamed away from
``websocket.py`` so the removal is not quietly undone:

* The offline detector, the relay-fault detector and the health checks are
  hardware-confirmed against the poll. The freshness fields ``checkin_marker()``
  reads are **not present in the WS frame**, so feeding relay frames into the
  coordinator would corrupt exactly the bookkeeping those three are built on.
* Push works only for devices the account owns, so it can never replace the
  poll anyway. Control can ship now; push can be added later as a strictly
  additive layer.

Unsolicited frames are therefore read off the socket and dropped. The poll
remains the single source of state.

Ownership
---------
The relay refuses to route to devices the session does not own, answering
``WRONG_ID``. That is measured, and it is measured *with* a negative control:
deliberately malformed device ids return the identical error, which refutes the
"you formatted the id wrongly" reading. It is the only known runtime ownership
signal — no field in any cloud payload carries ownership — so
:meth:`ShellyCloudWebSocket.async_classify_ownership` sends one cheap
``Shelly.GetDeviceInfo`` and reads the answer. ``WRONG_ID`` is a verdict about a
device, not a fault in the transport, and is never treated as one.

Failure is loud
---------------
Every call raises on failure instead of returning ``None``. The first consumer
is an irrigation controller, where the realistic failure is *the zone did not
switch* — a silently swallowed error there breaks a watering schedule and looks
like success. The exceptions subclass
:class:`~homeassistant.exceptions.HomeAssistantError` so they surface in the UI
and in automation traces, and the repo's :class:`ShellyCloudError` so existing
handlers keep working.

Credential handling
-------------------
The access token rides in the connect URL as ``?t=<token>``, which makes this
module the most leak-prone place in the integration:

* A failed handshake raises ``aiohttp.WSServerHandshakeError`` whose ``__str__``
  embeds ``request_info.url`` — the **full** URL, token included — and ERROR
  logging is on by default. Every connect failure is therefore re-raised with
  the query string stripped, and no aiohttp error object is ever interpolated
  into a message or a log record; only its type name is.
* Inbound frames are logged at DEBUG only, through :func:`_redact`.
* The one transmission site is marked ``# CREDENTIAL:`` below, matching the
  convention ``api/cloud_control.py`` uses for the auth_key
  (see ``docs/AUTH_KEY.md``).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import time
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import aiohttp
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .cloud_control import ShellyCloudError

if TYPE_CHECKING:
    from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)

# Wire facts, verified live: the relay listens on 6113 and this path, and takes
# the access token as a query parameter.
_WSS_PORT = 6113
_WSS_PATH = "/shelly/wss/hk_sock"

# Reconnect ladder. Bounded on purpose — an unbounded backoff means a socket
# that never comes back after a long cloud outage.
_RECONNECT_MIN_S = 1.0
_RECONNECT_MAX_S = 60.0

# Up to 10 % jitter, so many Home Assistant instances do not reconnect in
# lock-step after a Shelly Cloud outage and re-create the outage themselves.
_RECONNECT_JITTER = 0.1

# A session has to last this long before it counts as "worked" and resets the
# ladder. Without the threshold, a relay that accepts a connection and drops it
# again immediately keeps the backoff pinned at its minimum — the ladder would
# switch itself off in exactly the situation it exists for.
_BACKOFF_RESET_AFTER_S = 60.0

# aiohttp ping interval; the relay drops idle sockets silently otherwise, and a
# command against a half-dead socket would wait for its full timeout.
_HEARTBEAT_S = 30

_DEFAULT_REQUEST_TIMEOUT_S = 10.0
_DEFAULT_CONNECT_TIMEOUT_S = 30.0

# Relay-level error strings (they are the relay's own, not a device's).
_ERROR_WRONG_ID = "WRONG_ID"
_ERROR_UNAUTHORIZED = "UNAUTHORIZED"

# WS close code the relay uses for a broken/expired session token.
_CLOSE_CODE_TOKEN_BROKEN = 4401

# An error code goes into exception messages, so it is capped rather than
# trusted: it arrives from the wire.
_MAX_ERROR_CODE_LEN = 64

# Keys whose values must be scrubbed before a frame reaches a log line.
_REDACT_KEYS = frozenset(
    {"token", "access_token", "refresh_token", "code", "t", "auth_key", "password"}
)


def _redact(value: Any) -> Any:
    """Return a copy of ``value`` with known-sensitive values masked.

    Log-only; the original frame is never mutated. Matching is by key name,
    so the key set has to be exhaustive on every credential-bearing name that
    can appear in a frame — which is why it also covers names this transport
    does not itself send.
    """
    if isinstance(value, dict):
        return {k: ("***" if k in _REDACT_KEYS else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


class ShellyCloudWsError(ShellyCloudError, HomeAssistantError):
    """A cloud-relay call failed.

    Doubly based on purpose: ``HomeAssistantError`` so a failed command shows up
    where the user acts (the UI, an automation trace) instead of only in the
    log, ``ShellyCloudError`` so the existing ``except`` clauses in this
    integration keep catching it.
    """


class ShellyCloudWsNotConnectedError(ShellyCloudWsError):
    """No relay connection was open when the call was made."""


class ShellyCloudWsTransportError(ShellyCloudWsError):
    """Connect, handshake or send failed at the network level."""


class ShellyCloudWsTimeoutError(ShellyCloudWsError):
    """The relay accepted the request and never answered it.

    Distinct from a transport error because the command may well have been
    executed — "no answer" is not "did not happen", and a caller must not
    report either outcome as certain.
    """


class ShellyCloudWsAuthError(ShellyCloudWsError):
    """The relay rejected the session token.

    Never retried in place: a rejected token stays rejected, so the only useful
    responses are a fresh token or asking the user to sign in again.
    """


class ShellyCloudWsCommandError(ShellyCloudWsError):
    """The relay answered with an error for this device and method.

    ``code`` is the relay's own short error string (e.g. ``WRONG_ID``), kept as
    an attribute so callers can branch on it without parsing the message.
    """

    def __init__(self, message: str, *, code: str) -> None:
        """Store the relay's error code alongside the readable message."""
        super().__init__(message)
        self.code = code


class DeviceOwnership(StrEnum):
    """What the relay says about routing to one device.

    ``NOT_ROUTABLE`` is deliberately not called "shared": the relay refuses a
    device that was never on the account and one that was removed from it in
    exactly the same way, so the honest statement is about routing, not about
    who owns what.
    """

    OWNED = "owned"
    NOT_ROUTABLE = "not_routable"
    UNKNOWN = "unknown"


def _relay_error_code(error: Any) -> str:
    """Reduce a relay error field to one short, comparable code string.

    The relay answers a refused route with a bare string (``"WRONG_ID"``,
    measured). A device-side JSON-RPC error can instead arrive as
    ``{"code": …, "message": …}``. Both are reduced to a single short string, so
    the ownership classifier compares one thing and nothing unbounded from the
    wire reaches an exception message.
    """
    if isinstance(error, str):
        return error.strip()[:_MAX_ERROR_CODE_LEN]
    if isinstance(error, dict):
        for key in ("error", "message", "code"):
            value = error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:_MAX_ERROR_CODE_LEN]
            if isinstance(value, int):
                return str(value)
    return "UNKNOWN"


class ShellyCloudWebSocket:
    """One reconnecting relay connection for one Shelly account.

    Bound to a single host, unlike the transport this was harvested from, which
    carried a ``host`` argument on every call and kept a map of connections. The
    Cloud Control API gives an account exactly one server URI (the login code's
    own ``user_api_url`` claim names it), and the relay on that host answers for
    every device id the account lists — including the ones it refuses to route
    to, which is how ``WRONG_ID`` was measured in the first place. A per-call
    host was an artefact of the Integrator-era client; keeping it would only
    invite callers to invent a second connection that has nothing to talk to.
    """

    def __init__(
        self,
        session: ClientSession,
        server_uri: str,
        token_provider: Callable[[], Awaitable[str]],
        *,
        on_token_rejected: Callable[[], Awaitable[str]] | None = None,
        on_reauth: Callable[[], None] | None = None,
        request_timeout_s: float = _DEFAULT_REQUEST_TIMEOUT_S,
        connect_timeout_s: float = _DEFAULT_CONNECT_TIMEOUT_S,
    ) -> None:
        """Prepare the transport. Nothing connects until :meth:`connect`.

        Args:
            session: shared aiohttp session.
            server_uri: the account's server URI or bare host.
            token_provider: **async** callable returning the current access
                token. Awaited on every connect attempt, so a reconnect after a
                long outage carries a current token rather than the one the
                socket was originally opened with.
            on_token_rejected: awaited when the relay rejects the session token
                on a live connection, to mint a fresh one before the next
                attempt. May raise ``ConfigEntryAuthFailed`` to say the
                credentials are genuinely gone.
            on_reauth: called when re-authentication needs the user. The wiring
                layer binds this to ``entry.async_start_reauth``; a
                ``ConfigEntryAuthFailed`` raised inside this module's background
                task would be swallowed by asyncio and reach nobody.
            request_timeout_s: how long one JSON-RPC round trip may take.
            connect_timeout_s: how long :meth:`connect` waits for the first
                connection attempt to resolve.
        """
        self._session = session
        self._host = self._normalise_host(server_uri)
        self._token_provider = token_provider
        self._on_token_rejected = on_token_rejected
        self._on_reauth = on_reauth
        self._request_timeout_s = request_timeout_s
        self._connect_timeout_s = connect_timeout_s

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task | None = None
        self._running = False
        self._auth_failed = False
        self._message_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._first_attempt: asyncio.Future | None = None

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def host(self) -> str:
        """The relay host this transport talks to."""
        return self._host

    @property
    def connected(self) -> bool:
        """True while a relay connection is open."""
        return self._ws is not None and not self._ws.closed

    @property
    def auth_failed(self) -> bool:
        """True once the relay's rejection has been escalated to reauth."""
        return self._auth_failed

    @staticmethod
    def _normalise_host(server_uri: str) -> str:
        """Reduce a server URI to the bare host the relay is addressed by.

        Accepts what the config entry already stores, in any of the shapes the
        Shelly app shows it in (``shelly-42-eu.shelly.cloud``,
        ``https://shelly-42-eu.shelly.cloud/``), because asking the caller to
        normalise it is how a scheme ends up inside a ``wss://`` URL.
        """
        raw = server_uri.strip()
        if not raw:
            raise ValueError("server_uri must not be empty")
        raw = raw.split("://", 1)[-1]
        raw = raw.split("/", 1)[0]
        return raw.split(":", 1)[0]

    # ── Connection lifecycle ────────────────────────────────────────────

    async def connect(self) -> None:
        """Open the relay connection and keep it open.

        Returns once the first connection is established, and raises if that
        first attempt fails — an opt-in control channel that cannot come up
        should say so at setup rather than sit in a silent retry loop while its
        entities pretend to work. Drops *after* a working connection are a
        different matter and are healed by the reconnect ladder.
        """
        if self._task is not None and not self._task.done():
            return
        self._running = True
        self._auth_failed = False
        self._first_attempt = asyncio.get_running_loop().create_future()
        self._task = asyncio.create_task(self._connection_loop())
        try:
            await asyncio.wait_for(
                asyncio.shield(self._first_attempt), self._connect_timeout_s
            )
        except asyncio.TimeoutError as err:
            await self.disconnect()
            raise ShellyCloudWsTimeoutError(
                f"Cloud relay {self._host} did not connect within "
                f"{self._connect_timeout_s:.0f}s"
            ) from err
        except BaseException:
            # The loop has already stopped itself on a first-attempt failure;
            # await the task so no exception is left orphaned in the loop.
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        """Close the connection, stop reconnecting, and fail anything in flight."""
        self._running = False
        ws, self._ws = self._ws, None
        if ws is not None and not ws.closed:
            await ws.close()
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            # The loop is being torn down; whatever it was doing is moot, and a
            # failure raised here would mask the caller's own reason for
            # disconnecting.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        first, self._first_attempt = self._first_attempt, None
        if first is not None:
            if not first.done():
                first.cancel()
            elif not first.cancelled():
                # Retrieve any stored exception. Nobody is waiting for it any
                # more, and an unretrieved one is reported by asyncio at
                # garbage-collection time — in a log the user reads.
                first.exception()
        self._fail_pending("the relay connection was closed")
        _LOGGER.debug("Cloud relay connection to %s closed", self._host)

    async def _connection_loop(self) -> None:
        """Reconnect with bounded exponential backoff plus jitter."""
        backoff = _RECONNECT_MIN_S
        while self._running:
            started = time.monotonic()
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                raise
            except ShellyCloudWsAuthError as err:
                if self._fail_first_attempt(err):
                    # A token minted seconds ago was refused; looping on it
                    # would only repeat the refusal.
                    break
                if not await self._handle_token_rejected():
                    break
            except ShellyCloudWsError as err:
                _LOGGER.error(
                    "Cloud relay error for %s: %s", self._host, type(err).__name__
                )
                if self._fail_first_attempt(err):
                    break
            except Exception as err:  # noqa: BLE001
                # Never ``str(err)`` — a raw aiohttp error embeds the
                # ``?t=<token>`` URL.
                _LOGGER.error(
                    "Unexpected cloud relay failure for %s: %s",
                    self._host,
                    type(err).__name__,
                )
                if self._fail_first_attempt(err):
                    break
            else:
                if time.monotonic() - started >= _BACKOFF_RESET_AFTER_S:
                    # A connection that actually worked resets the ladder, so a
                    # long-lived session is not punished for an old outage.
                    backoff = _RECONNECT_MIN_S

            if not self._running:
                break
            sleep_for = backoff + random.uniform(0, backoff * _RECONNECT_JITTER)
            _LOGGER.info("Reconnecting to cloud relay %s in %.1fs", self._host, sleep_for)
            await asyncio.sleep(sleep_for)
            backoff = min(backoff * 2, _RECONNECT_MAX_S)

    async def _connect_and_listen(self) -> None:
        """Open one connection and read from it until it closes.

        Raises:
            ShellyCloudWsAuthError: handshake 401/403, or a 4401 close.
            ShellyCloudWsTransportError: any other connect failure. Neither
                message ever carries the token-bearing URL.
        """
        token = await self._token_provider()
        # CREDENTIAL (sent 1/1): the OAuth access token, as the ``t`` query
        # parameter of the relay URL. Destination is the account's own host,
        # normalised once in ``_normalise_host``. This URL must never be
        # logged, echoed into an exception, or put in diagnostics.
        url = f"wss://{self._host}:{_WSS_PORT}{_WSS_PATH}?t={token}"
        safe_url = url.split("?", 1)[0]

        _LOGGER.info("Connecting to cloud relay %s", self._host)
        try:
            ws = await self._session.ws_connect(
                url, ssl=True, heartbeat=_HEARTBEAT_S
            )
        except aiohttp.WSServerHandshakeError as err:
            status = getattr(err, "status", None)
            if status in (401, 403):
                raise ShellyCloudWsAuthError(
                    f"Cloud relay rejected the session at {safe_url} (HTTP {status})"
                ) from None
            raise ShellyCloudWsTransportError(
                f"Cloud relay handshake failed at {safe_url} (HTTP {status})"
            ) from None
        except asyncio.TimeoutError:
            raise ShellyCloudWsTransportError(
                f"Timeout connecting to cloud relay {self._host}"
            ) from None
        except aiohttp.ClientError as err:
            # ``from None`` and the type name only: the aiohttp error carries
            # the token-bearing URL in both its message and its context.
            raise ShellyCloudWsTransportError(
                f"Cloud relay connect failed to {self._host} ({type(err).__name__})"
            ) from None

        self._ws = ws
        self._resolve_first_attempt()
        close_reason = ""
        try:
            _LOGGER.info("Cloud relay connected to %s", self._host)
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self._handle_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.error(
                        "Cloud relay transport error for %s: %s",
                        self._host,
                        type(ws.exception()).__name__,
                    )
                    break
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                ):
                    close_reason = str(msg.extra or "")
                    break
        finally:
            self._ws = None
            # Anything in flight is now unanswerable. Failing it here turns a
            # dropped connection into an immediate, honest error instead of a
            # command that waits out its whole timeout.
            self._fail_pending("the relay connection dropped")

        if ws.close_code == _CLOSE_CODE_TOKEN_BROKEN or "Token-Broken" in close_reason:
            raise ShellyCloudWsAuthError(
                f"Cloud relay {self._host} closed the session as unauthenticated"
            )

    async def _handle_token_rejected(self) -> bool:
        """Refresh the rejected token. Returns False if the loop must stop."""
        if self._on_token_rejected is None:
            self._signal_reauth()
            return False
        try:
            await self._on_token_rejected()
        except ConfigEntryAuthFailed:
            _LOGGER.error(
                "Cloud relay authentication for %s failed permanently", self._host
            )
            self._signal_reauth()
            return False
        except Exception as err:  # noqa: BLE001 — type name only, never a token
            _LOGGER.error(
                "Refreshing the rejected cloud relay token failed: %s",
                type(err).__name__,
            )
        return True

    def _signal_reauth(self) -> None:
        """Ask Home Assistant to re-authenticate the entry, exactly once."""
        self._auth_failed = True
        self._running = False
        if self._on_reauth is None:
            return
        try:
            self._on_reauth()
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Starting re-authentication failed: %s", type(err).__name__)

    # ── First-attempt bookkeeping ───────────────────────────────────────

    def _resolve_first_attempt(self) -> None:
        """Let a waiting :meth:`connect` return once a connection is up."""
        if self._first_attempt is not None and not self._first_attempt.done():
            self._first_attempt.set_result(None)

    def _fail_first_attempt(self, err: BaseException) -> bool:
        """Report a failure to a waiting :meth:`connect`.

        Returns True when this was the first attempt, which is the loop's signal
        to stop: the caller is being told, so a background retry would only
        duplicate work the caller is about to decide on.
        """
        if self._first_attempt is None or self._first_attempt.done():
            return False
        self._first_attempt.set_exception(err)
        self._running = False
        return True

    # ── Requests ────────────────────────────────────────────────────────

    async def send_jrpc_request(
        self,
        device_id: str,
        method: str,
        params: dict | None = None,
        *,
        timeout: float | None = None,
    ) -> dict:
        """Send one JSON-RPC call over the relay and return its result.

        This is the whole point of the module: ``method``/``params`` are the
        device's own local RPC, so anything a Gen2+ device understands can be
        reached — including the virtual components the HTTP API has no route
        for.

        Returns:
            The relay's ``response`` object, i.e. the RPC result.

        Raises:
            ShellyCloudWsNotConnectedError: no connection is open.
            ShellyCloudWsTimeoutError: no answer within the timeout.
            ShellyCloudWsAuthError: the relay rejected the session token.
            ShellyCloudWsCommandError: the relay refused the call (``WRONG_ID``
                for a device it will not route to, or a device-side error).
            ShellyCloudWsTransportError: the send itself failed.

        It never returns ``None`` for any of those. The first consumer switches
        water valves, and a caller that cannot tell "switched" from "did not
        switch" is worse than one that fails.
        """
        ws = self._ws
        if ws is None or ws.closed:
            raise ShellyCloudWsNotConnectedError(
                f"No cloud relay connection to {self._host} for {method}"
            )

        self._message_id += 1
        trid = self._message_id
        frame = {
            "event": "Shelly:JrpcRequest",
            "trid": trid,
            "deviceId": device_id,
            "method": method,
            "params": params or {},
        }
        _LOGGER.debug("Cloud relay request: %s", _redact(frame))

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[trid] = future
        try:
            try:
                await ws.send_json(frame)
            except (aiohttp.ClientError, ConnectionError, RuntimeError) as err:
                raise ShellyCloudWsTransportError(
                    f"Sending {method} to {device_id} failed "
                    f"({type(err).__name__})"
                ) from None
            try:
                message = await asyncio.wait_for(
                    future, timeout if timeout is not None else self._request_timeout_s
                )
            except asyncio.TimeoutError as err:
                raise ShellyCloudWsTimeoutError(
                    f"No cloud relay answer for {method} on {device_id}"
                ) from err
        finally:
            self._pending.pop(trid, None)

        response = message.get("response")
        if not isinstance(response, dict):
            raise ShellyCloudWsCommandError(
                f"Malformed cloud relay answer for {method} on {device_id}",
                code="UNKNOWN",
            )

        error = response.get("error")
        if error is not None:
            code = _relay_error_code(error)
            if code == _ERROR_UNAUTHORIZED:
                # Log the marker, never the frame: the relay may echo session
                # material on this path. No retry — see ShellyCloudWsAuthError.
                _LOGGER.error(
                    "Cloud relay reported UNAUTHORIZED: device=%s method=%s",
                    device_id,
                    method,
                )
                self._signal_reauth()
                # A session the relay refuses is worth nothing, and leaving it
                # open would let further commands queue against a dead one.
                with contextlib.suppress(Exception):
                    await ws.close()
                raise ShellyCloudWsAuthError(
                    f"Cloud relay rejected the session for {method} on {device_id}"
                )
            raise ShellyCloudWsCommandError(
                f"Cloud relay refused {method} on {device_id}: {code}", code=code
            )

        _LOGGER.debug(
            "Cloud relay answered %s for %s with keys %s",
            method,
            device_id,
            sorted(response),
        )
        return response

    async def async_classify_ownership(self, device_id: str) -> DeviceOwnership:
        """Ask the relay whether it will route to ``device_id`` at all.

        Sends one ``Shelly.GetDeviceInfo`` — the cheapest call that proves
        routing — and reads the answer:

        * an answer ⇒ :attr:`DeviceOwnership.OWNED`;
        * ``WRONG_ID`` ⇒ :attr:`DeviceOwnership.NOT_ROUTABLE`, the measured
          signal for a device the session does not own (shared, or gone);
        * anything else ⇒ :attr:`DeviceOwnership.UNKNOWN`.

        ``UNKNOWN`` exists so a timeout or an unfamiliar relay error is never
        laundered into a verdict about the device. A caller may cache the two
        definite answers for the session; it must not cache ``UNKNOWN`` as if it
        were one, which is also why no cache lives in here — the lifetime of the
        answer belongs to whoever holds the device list.

        A rejected session token propagates as
        :class:`ShellyCloudWsAuthError` instead of being reported as a device
        verdict: it says nothing about the device, and classifying a whole
        account as "unknown" would hide that the session is simply dead.
        """
        try:
            await self.send_jrpc_request(device_id, "Shelly.GetDeviceInfo")
        except ShellyCloudWsAuthError:
            raise
        except ShellyCloudWsCommandError as err:
            if err.code == _ERROR_WRONG_ID:
                _LOGGER.debug(
                    "Cloud relay will not route to %s (%s)", device_id, err.code
                )
                return DeviceOwnership.NOT_ROUTABLE
            _LOGGER.debug(
                "Cloud relay gave an unclassifiable answer for %s: %s",
                device_id,
                err.code,
            )
            return DeviceOwnership.UNKNOWN
        except ShellyCloudWsError as err:
            _LOGGER.debug(
                "Ownership probe for %s could not complete: %s",
                device_id,
                type(err).__name__,
            )
            return DeviceOwnership.UNKNOWN
        return DeviceOwnership.OWNED

    # ── Inbound frames ──────────────────────────────────────────────────

    def _handle_message(self, data: str) -> None:
        """Route one inbound frame to its waiting request, or drop it."""
        try:
            message = json.loads(data)
        except json.JSONDecodeError:
            _LOGGER.debug("Unparseable frame from cloud relay %s", self._host)
            return
        if not isinstance(message, dict):
            _LOGGER.debug("Unexpected frame shape from cloud relay %s", self._host)
            return

        # Correlate on ``trid`` alone, not on the event name: the round trip was
        # measured by transaction id, and the response event name was not.
        trid = message.get("trid")
        future = self._pending.get(trid) if isinstance(trid, int) else None
        if future is not None:
            if not future.done():
                future.set_result(message)
            return

        # Everything else is push, and this transport does not consume push —
        # see the module docstring. Dropping it is the design, not a gap.
        _LOGGER.debug("Ignoring unsolicited relay frame: %s", _redact(message))

    def _fail_pending(self, reason: str) -> None:
        """Fail every in-flight request; a lost answer must never look like one."""
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(
                    ShellyCloudWsNotConnectedError(
                        f"Cloud relay request to {self._host} was lost: {reason}"
                    )
                )

    def __repr__(self) -> str:
        """Host and state only — the token this object connects with is not printable."""
        return (
            f"ShellyCloudWebSocket(host={self._host!r}, connected={self.connected}, "
            f"auth_failed={self._auth_failed})"
        )
