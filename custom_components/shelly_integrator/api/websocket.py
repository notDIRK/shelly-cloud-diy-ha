"""WebSocket connection manager for Shelly Cloud.

Handles WebSocket connections, reconnection, and message routing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import TYPE_CHECKING, Any, Callable

import aiohttp

from ..const import WSS_PORT, WSS_PATH, WS_RECONNECT_MIN, WS_RECONNECT_MAX

if TYPE_CHECKING:
    from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)


class ShellyWebSocket:
    """Manages WebSocket connections to Shelly Cloud servers."""

    def __init__(
        self,
        session: ClientSession,
        jwt_token_provider: Callable[[], str],
        message_handler: Callable[[dict, str], Any],
        on_connected: Callable[[str], Any] | None = None,
    ) -> None:
        """Initialize WebSocket manager.

        Args:
            session: aiohttp client session
            jwt_token_provider: Callable that returns current JWT token
            message_handler: Async callback for incoming messages (message, host)
            on_connected: Async callback when connection is established (host)
        """
        self._session = session
        self._get_jwt_token = jwt_token_provider
        self._message_handler = message_handler
        self._on_connected = on_connected
        self._connections: dict[str, aiohttp.ClientWebSocketResponse] = {}
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._message_id = 0
        self._pending_responses: dict[int, asyncio.Future] = {}

    @property
    def connected_hosts(self) -> set[str]:
        """Return set of connected hosts."""
        return set(self._connections.keys())

    def is_connected(self, host: str) -> bool:
        """Check if connected to a specific host."""
        return host in self._connections

    async def connect(self, host: str) -> None:
        """Connect to a Shelly Cloud WebSocket server.

        Args:
            host: Server hostname (e.g., shelly-187-eu.shelly.cloud)
        """
        if host in self._connections:
            _LOGGER.debug("Already connected to %s", host)
            return

        self._running = True
        task = asyncio.create_task(self._connection_loop(host))
        self._tasks.append(task)
        _LOGGER.info("Started connection task for %s", host)

    async def disconnect(self, host: str) -> None:
        """Disconnect from a specific host."""
        ws = self._connections.pop(host, None)
        if ws:
            await ws.close()
            _LOGGER.info("Disconnected from %s", host)

    async def disconnect_all(self) -> None:
        """Close all connections and stop tasks."""
        self._running = False

        # Cancel pending responses
        for future in self._pending_responses.values():
            if not future.done():
                future.cancel()
        self._pending_responses.clear()

        # Close WebSocket connections
        for ws in list(self._connections.values()):
            await ws.close()
        self._connections.clear()

        # Cancel tasks
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

        _LOGGER.info("All WebSocket connections closed")

    async def reconnect_all(self) -> None:
        """Reconnect to all hosts (e.g., after token refresh)."""
        hosts = list(self._connections.keys())
        for host in hosts:
            ws = self._connections.get(host)
            if ws:
                await ws.close()
        # Connection loops will auto-reconnect

    async def send_command(
        self,
        host: str,
        device_id: str,
        cmd: str,
        channel: int,
        action: str,
        params: dict | None = None,
        timeout: float = 10.0,
    ) -> dict | None:
        """Send a command to a device via WebSocket.

        Args:
            host: WebSocket server host
            device_id: Target device ID
            cmd: Command type (relay, light, roller)
            channel: Device channel
            action: Action to perform (on, off, toggle, open, close, stop)
            params: Additional command parameters
            timeout: Response timeout in seconds

        Returns:
            Command response dict, or None on failure
        """
        ws = self._connections.get(host)
        if not ws:
            _LOGGER.error("No WebSocket connection to %s", host)
            return None

        self._message_id += 1
        trid = self._message_id

        # Build command parameters
        cmd_params = {"id": channel}
        if cmd == "roller":
            cmd_params["go"] = action
        elif cmd == "roller_to_pos":
            pass  # Only id + extra params (pos, rel, slat_pos, slat_rel)
        else:
            cmd_params["turn"] = action
        # Merge extra params (brightness, pos, etc.) for ALL commands
        if params:
            cmd_params.update(params)

        command = {
            "event": "Shelly:CommandRequest",
            "trid": trid,
            "deviceId": device_id,
            "data": {
                "cmd": cmd,
                "params": cmd_params,
            }
        }

        _LOGGER.debug("Sending command: %s", command)

        future: asyncio.Future = asyncio.Future()
        self._pending_responses[trid] = future

        try:
            await ws.send_json(command)
            result = await asyncio.wait_for(future, timeout=timeout)
            _LOGGER.debug("Command response: %s", result)
            return result
        except asyncio.TimeoutError:
            _LOGGER.warning("Command timeout for %s", device_id)
            self._pending_responses.pop(trid, None)
            return None
        except Exception as err:
            _LOGGER.error("Command error: %s", err)
            self._pending_responses.pop(trid, None)
            return None

    async def send_jrpc_request(
        self,
        host: str,
        device_id: str,
        method: str,
        params: dict | None = None,
        timeout: float = 10.0,
    ) -> dict | None:
        """Send a JRPC request to a Gen2/Gen3 device.

        Gen2/Gen3 devices use ``Shelly:JrpcRequest`` for RPC methods
        such as Switch.Set, Light.Set, Cover.Open, etc.

        Args:
            host: WebSocket server host
            device_id: Target device ID
            method: RPC method name
            params: Method parameters
            timeout: Response timeout in seconds

        Returns:
            JRPC response dict, or None on failure
        """
        ws = self._connections.get(host)
        if not ws:
            _LOGGER.error("No WebSocket connection to %s", host)
            return None

        self._message_id += 1
        trid = self._message_id

        command = {
            "event": "Shelly:JrpcRequest",
            "trid": trid,
            "deviceId": device_id,
            "method": method,
            "params": params or {},
        }

        _LOGGER.debug("Sending JRPC request: %s", command)

        future: asyncio.Future = asyncio.Future()
        self._pending_responses[trid] = future

        try:
            await ws.send_json(command)
            result = await asyncio.wait_for(future, timeout=timeout)
            _LOGGER.debug("JRPC response: %s", result)

            # Log additional details if UNAUTHORIZED error received
            if result.get("response", {}).get("error") == "UNAUTHORIZED":
                _LOGGER.error(
                    "JRPC UNAUTHORIZED: device=%s, method=%s, "
                    "full_response=%s",
                    device_id, method, result
                )

            return result
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "JRPC timeout for %s method %s", device_id, method
            )
            self._pending_responses.pop(trid, None)
            return None
        except Exception as err:
            _LOGGER.error("JRPC error: %s", err)
            self._pending_responses.pop(trid, None)
            return None

    async def send_action_request(
        self,
        host: str,
        action: str,
        device_id: str,
    ) -> None:
        """Send an Integrator:ActionRequest (DeviceVerify, DeviceGetSettings).

        Args:
            host: WebSocket server host
            action: Action name (DeviceVerify, DeviceGetSettings)
            device_id: Target device ID
        """
        ws = self._connections.get(host)
        if not ws:
            _LOGGER.error("No WebSocket connection to %s", host)
            return

        self._message_id += 1
        command = {
            "event": "Integrator:ActionRequest",
            "trid": self._message_id,
            "data": {
                "action": action,
                "deviceId": device_id,
            }
        }

        _LOGGER.debug("Sending action request: %s", command)
        await ws.send_json(command)

    async def _connection_loop(self, host: str) -> None:
        """Connection loop with exponential-backoff reconnection."""
        backoff = WS_RECONNECT_MIN
        while self._running:
            try:
                await self._connect_and_listen(host)
                # Successful session – reset backoff for next drop
                backoff = WS_RECONNECT_MIN
            except Exception as err:
                _LOGGER.error("WebSocket error for %s: %s", host, err)

            if self._running:
                # Add up-to-10% jitter so many HA instances do not all
                # reconnect in lock-step after a Shelly Cloud outage.
                jitter = random.uniform(0, backoff * 0.1)
                sleep_for = backoff + jitter
                _LOGGER.info(
                    "Reconnecting to %s in %.1fs", host, sleep_for
                )
                await asyncio.sleep(sleep_for)
                backoff = min(backoff * 2, WS_RECONNECT_MAX)

    async def _connect_and_listen(self, host: str) -> None:
        """Establish connection and listen for messages."""
        jwt_token = self._get_jwt_token()
        url = f"wss://{host}:{WSS_PORT}{WSS_PATH}?t={jwt_token}"

        _LOGGER.info("Connecting to WebSocket: %s", host)

        async with self._session.ws_connect(url, ssl=True) as ws:
            self._connections[host] = ws
            _LOGGER.info("WebSocket connected to %s", host)

            # Notify coordinator that connection is established
            if self._on_connected:
                try:
                    await self._on_connected(host)
                except Exception as err:
                    _LOGGER.error("on_connected callback failed: %s", err)

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_message(msg.data, host)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.error("WebSocket error: %s", ws.exception())
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    _LOGGER.warning("WebSocket closed by server")
                    break

            self._connections.pop(host, None)

    async def _handle_message(self, data: str, host: str) -> None:
        """Handle incoming WebSocket message."""
        try:
            message = json.loads(data)
            _LOGGER.debug("Received from %s: %s", host, message)

            # Handle command / JRPC responses internally
            event = message.get("event")
            if event in ("Shelly:CommandResponse", "Shelly:JrpcResponse"):
                trid = message.get("trid")
                if trid and trid in self._pending_responses:
                    future = self._pending_responses.pop(trid)
                    if not future.done():
                        future.set_result(message)
                return

            # Delegate other messages to handler
            await self._message_handler(message, host)

        except json.JSONDecodeError as err:
            _LOGGER.error("Failed to parse message: %s", err)
