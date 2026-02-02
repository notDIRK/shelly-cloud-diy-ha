"""DataUpdateCoordinator for Shelly Integrator."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any, Callable

import aiohttp
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DOMAIN,
    API_GET_TOKEN,
    WSS_PORT,
    WSS_PATH,
    TOKEN_REFRESH_INTERVAL,
    WS_RECONNECT_DELAY,
)

_LOGGER = logging.getLogger(__name__)

# Signal for new device discovery
SIGNAL_NEW_DEVICE = f"{DOMAIN}_new_device"


class ShellyIntegratorCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for Shelly Integrator WebSocket connection."""

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        tag: str,
        token: str,
        jwt_token: str,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),  # Fallback polling
        )
        self.session = session
        self.tag = tag
        self.token = token
        self.jwt_token = jwt_token
        self.devices: dict[str, Any] = {}
        self.hosts: set[str] = set()
        self._ws_connections: dict[str, aiohttp.ClientWebSocketResponse] = {}
        self._ws_tasks: list[asyncio.Task] = []
        self._running = False
        self._message_id = 0
        self._pending_commands: dict[int, asyncio.Future] = {}
        self._token_refresh_unsub: Callable[[], None] | None = None
        self._device_host_map: dict[str, str] = {}  # device_id -> host

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from WebSocket (fallback if WS disconnected)."""
        return self.devices

    async def async_config_entry_first_refresh(self) -> None:
        """Start WebSocket connections on first refresh."""
        self._running = True

        # Schedule token refresh
        self._token_refresh_unsub = async_track_time_interval(
            self.hass,
            self._async_refresh_token,
            timedelta(seconds=TOKEN_REFRESH_INTERVAL),
        )

        # Note: In production, hosts come from device permission grant callback
        # For now, we start with default server
        await super().async_config_entry_first_refresh()

    async def _async_refresh_token(self, _now=None) -> None:
        """Refresh JWT token periodically."""
        try:
            await self.refresh_token()
            # Reconnect WebSocket with new token
            for host in list(self.hosts):
                if host in self._ws_connections:
                    ws = self._ws_connections[host]
                    await ws.close()
        except Exception as err:
            _LOGGER.error("Failed to refresh token: %s", err)

    async def connect_to_host(self, host: str) -> None:
        """Connect to a Shelly Cloud WebSocket server."""
        if host in self._ws_connections:
            return

        self.hosts.add(host)
        task = asyncio.create_task(self._ws_loop(host))
        self._ws_tasks.append(task)

    async def _ws_loop(self, host: str) -> None:
        """WebSocket connection loop with reconnection."""
        while self._running:
            try:
                await self._connect_websocket(host)
            except Exception as err:
                _LOGGER.error("WebSocket error for %s: %s", host, err)

            if self._running:
                _LOGGER.info("Reconnecting to %s in %ds", host, WS_RECONNECT_DELAY)
                await asyncio.sleep(WS_RECONNECT_DELAY)

    async def _connect_websocket(self, host: str) -> None:
        """Establish WebSocket connection and handle messages."""
        url = f"wss://{host}:{WSS_PORT}{WSS_PATH}?t={self.jwt_token}"

        _LOGGER.info("Connecting to WebSocket: %s", host)

        async with self.session.ws_connect(url, ssl=True) as ws:
            self._ws_connections[host] = ws
            _LOGGER.info("WebSocket connected to %s", host)

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_message(msg.data, host)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.error("WebSocket error: %s", ws.exception())
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    _LOGGER.warning("WebSocket closed")
                    break

            self._ws_connections.pop(host, None)

    async def _handle_message(self, data: str, host: str) -> None:
        """Handle incoming WebSocket message."""
        try:
            message = json.loads(data)
            _LOGGER.debug("Received: %s", message)

            # Handle command responses
            if "id" in message and message["id"] in self._pending_commands:
                future = self._pending_commands.pop(message["id"])
                if not future.done():
                    future.set_result(message)
                return

            # Handle different message types
            event = message.get("event")

            if event == "Shelly:StatusOnChange":
                await self._handle_status_change(message, host)
            elif event == "Shelly:Online":
                await self._handle_online(message, host)
            elif event == "Shelly:Offline":
                await self._handle_offline(message, host)
            elif event == "Shelly:NotifyFullStatus":
                await self._handle_full_status(message, host)
            else:
                _LOGGER.debug("Unknown event: %s", event)

        except json.JSONDecodeError as err:
            _LOGGER.error("Failed to parse message: %s", err)

    async def _handle_status_change(self, message: dict, host: str) -> None:
        """Handle device status change event."""
        device_id = message.get("device")
        status = message.get("status", {})

        if device_id:
            is_new = device_id not in self.devices
            self._device_host_map[device_id] = host
            self.devices[device_id] = {
                **self.devices.get(device_id, {}),
                "status": status,
                "online": True,
            }
            self.async_set_updated_data(self.devices)

            if is_new:
                async_dispatcher_send(self.hass, SIGNAL_NEW_DEVICE, device_id)

    async def _handle_online(self, message: dict, host: str) -> None:
        """Handle device online event."""
        device_id = message.get("device")
        if device_id:
            is_new = device_id not in self.devices
            self._device_host_map[device_id] = host
            self.devices.setdefault(device_id, {})["online"] = True
            self.async_set_updated_data(self.devices)

            if is_new:
                async_dispatcher_send(self.hass, SIGNAL_NEW_DEVICE, device_id)

    async def _handle_offline(self, message: dict, host: str) -> None:
        """Handle device offline event."""
        device_id = message.get("device")
        if device_id:
            self.devices.setdefault(device_id, {})["online"] = False
            self.async_set_updated_data(self.devices)

    async def _handle_full_status(self, message: dict, host: str) -> None:
        """Handle full device status event (initial connection)."""
        device_id = message.get("device")
        status = message.get("status", {})
        device_info = message.get("device_info", {})

        if device_id:
            is_new = device_id not in self.devices
            self._device_host_map[device_id] = host
            self.devices[device_id] = {
                "status": status,
                "device_info": device_info,
                "online": True,
            }
            self.async_set_updated_data(self.devices)

            if is_new:
                async_dispatcher_send(self.hass, SIGNAL_NEW_DEVICE, device_id)

    def get_host_for_device(self, device_id: str) -> str | None:
        """Get the WebSocket host for a device."""
        return self._device_host_map.get(device_id)

    async def send_command(
        self,
        device_id: str,
        method: str,
        params: dict | None = None,
        timeout: float = 10.0,
    ) -> dict | None:
        """Send command via WebSocket and wait for response."""
        host = self._device_host_map.get(device_id)
        if not host:
            _LOGGER.error("No host mapping for device %s", device_id)
            return None

        ws = self._ws_connections.get(host)
        if not ws:
            _LOGGER.error("No WebSocket connection to %s", host)
            return None

        self._message_id += 1
        msg_id = self._message_id

        command = {
            "id": msg_id,
            "device": device_id,
            "method": method,
        }
        if params:
            command["params"] = params

        # Create future for response
        future: asyncio.Future = asyncio.Future()
        self._pending_commands[msg_id] = future

        try:
            await ws.send_json(command)
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            _LOGGER.warning("Command timeout for %s: %s", device_id, method)
            self._pending_commands.pop(msg_id, None)
            return None
        except Exception as err:
            _LOGGER.error("Command error: %s", err)
            self._pending_commands.pop(msg_id, None)
            return None

    async def refresh_token(self) -> None:
        """Refresh JWT token."""
        async with self.session.post(
            API_GET_TOKEN,
            data={"itg": self.tag, "token": self.token},
        ) as response:
            response.raise_for_status()
            data = await response.json()
            self.jwt_token = data["data"]
            _LOGGER.info("JWT token refreshed")

    async def async_close(self) -> None:
        """Close all connections."""
        self._running = False

        # Cancel token refresh
        if self._token_refresh_unsub:
            self._token_refresh_unsub()
            self._token_refresh_unsub = None

        # Cancel pending commands
        for future in self._pending_commands.values():
            if not future.done():
                future.cancel()
        self._pending_commands.clear()

        for ws in self._ws_connections.values():
            await ws.close()

        for task in self._ws_tasks:
            task.cancel()

        self._ws_connections.clear()
        self._ws_tasks.clear()
