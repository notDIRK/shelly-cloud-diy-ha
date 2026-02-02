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

            # Request status for all devices we know about on this host
            await self._request_device_statuses(host)

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

    async def _request_device_settings(self, device_id: str, host: str) -> None:
        """Request device settings to get the device name."""
        try:
            self._message_id += 1
            trid = self._message_id

            ws = self._ws_connections.get(host)
            if ws:
                command = {
                    "event": "Integrator:ActionRequest",
                    "trid": trid,
                    "data": {
                        "action": "DeviceGetSettings",
                        "deviceId": device_id,
                    }
                }
                _LOGGER.info("Requesting settings for %s", device_id)
                await ws.send_json(command)
        except Exception as err:
            _LOGGER.error("Failed to request settings for %s: %s", device_id, err)

    async def _request_device_statuses(self, host: str) -> None:
        """Request status for all devices on this host using DeviceVerify action."""
        devices_on_host = [
            device_id for device_id, h in self._device_host_map.items() if h == host
        ]

        _LOGGER.info("Requesting status for %d devices on %s: %s", len(devices_on_host), host, devices_on_host)

        for device_id in devices_on_host:
            try:
                self._message_id += 1
                trid = self._message_id

                ws = self._ws_connections.get(host)
                if ws:
                    # Use Integrator:ActionRequest with DeviceVerify to get device status
                    command = {
                        "event": "Integrator:ActionRequest",
                        "trid": trid,
                        "data": {
                            "action": "DeviceVerify",
                            "deviceId": device_id,
                        }
                    }
                    _LOGGER.info("Sending DeviceVerify for %s: %s", device_id, command)
                    await ws.send_json(command)
            except Exception as err:
                _LOGGER.error("Failed to request status for %s: %s", device_id, err)

    async def _handle_message(self, data: str, host: str) -> None:
        """Handle incoming WebSocket message."""
        try:
            message = json.loads(data)
            _LOGGER.info("WebSocket received from %s: %s", host, message)

            event = message.get("event")

            # Handle command responses
            if event == "Shelly:CommandResponse":
                trid = message.get("trid")
                if trid and trid in self._pending_commands:
                    future = self._pending_commands.pop(trid)
                    if not future.done():
                        future.set_result(message)
                return

            # Handle DeviceVerify response
            if event == "Integrator:ActionResponse":
                await self._handle_action_response(message, host)
                return

            # Handle device events
            if event == "Shelly:StatusOnChange":
                await self._handle_status_change(message, host)
            elif event == "Shelly:Online":
                await self._handle_online(message, host)
            elif event == "Shelly:Settings":
                await self._handle_settings(message, host)
            elif event == "Error":
                _LOGGER.error("Error from server: %s", message.get("message"))
            elif event:
                _LOGGER.info("Unhandled event type: %s - %s", event, message)
            else:
                _LOGGER.info("Message without event: %s", message)

        except json.JSONDecodeError as err:
            _LOGGER.error("Failed to parse message: %s", err)

    async def _handle_action_response(self, message: dict, host: str) -> None:
        """Handle Integrator:ActionResponse (DeviceVerify/DeviceGetSettings response)."""
        data = message.get("data", {})
        result = data.get("result")
        device_id = data.get("deviceId")

        if result == "WRONG_HOST":
            # Device is on a different host
            correct_host = data.get("host")
            if correct_host and device_id:
                _LOGGER.info("Device %s is on different host: %s", device_id, correct_host)
                self._device_host_map[device_id] = correct_host
                if correct_host not in self.hosts:
                    await self.connect_to_host(correct_host)
            return

        if result == "UNAUTHORIZED":
            _LOGGER.error("Unauthorized access to device %s", device_id)
            return

        if result == "OK" and device_id:
            device_type = data.get("deviceType")
            device_code = data.get("deviceCode")
            device_status = data.get("deviceStatus", {})
            device_settings = data.get("deviceSettings", {})
            access_groups = data.get("accessGroups", "00")

            # Extract device name from settings
            device_name = None
            if device_settings:
                # Gen2: name in device settings
                device_name = device_settings.get("name")
                # Gen1: name might be in different location
                if not device_name and "device" in device_settings:
                    device_name = device_settings.get("device", {}).get("hostname")

                _LOGGER.info("Device %s settings received: name=%s", device_id, device_name)

            is_new = device_id not in self.devices
            self._device_host_map[device_id] = host

            # Update or create device entry
            if device_id in self.devices:
                # Update existing device with new info
                if device_type:
                    self.devices[device_id]["device_type"] = device_type
                if device_code:
                    self.devices[device_id]["device_code"] = device_code
                if device_name:
                    self.devices[device_id]["name"] = device_name
                if device_status:
                    self.devices[device_id]["status"] = device_status
                    self.devices[device_id]["online"] = True
                if access_groups:
                    self.devices[device_id]["access_groups"] = access_groups
            else:
                self.devices[device_id] = {
                    "status": device_status,
                    "device_type": device_type,
                    "device_code": device_code,
                    "name": device_name,
                    "access_groups": access_groups,
                    "online": bool(device_status),
                }

            _LOGGER.info(
                "Device %s verified: name=%s, type=%s, code=%s, online=%s",
                device_id, device_name, device_type, device_code, bool(device_status)
            )

            self.async_set_updated_data(self.devices)

            if is_new:
                _LOGGER.info("New device discovered: %s - dispatching signal", device_id)
                async_dispatcher_send(self.hass, SIGNAL_NEW_DEVICE, device_id)

    async def _handle_status_change(self, message: dict, host: str) -> None:
        """Handle Shelly:StatusOnChange event."""
        device_id = message.get("deviceId")
        status = message.get("status", {})

        if device_id:
            is_new = device_id not in self.devices
            self._device_host_map[device_id] = host

            # Extract device info from status
            device_name = None
            device_type = None

            # Gen2: name in sys.device.name or from getinfo
            if "sys" in status:
                sys_info = status.get("sys", {})
                device_info = sys_info.get("device", {})
                device_name = device_info.get("name")

            # Gen1: device info in getinfo
            if "getinfo" in status:
                fw_info = status.get("getinfo", {}).get("fw_info", {})
                device_type = fw_info.get("device", "").split("-")[0]  # e.g., "shellyem"

            # Get code/type from status
            device_code = status.get("code")  # Gen2 has this

            self.devices[device_id] = {
                **self.devices.get(device_id, {}),
                "status": status,
                "online": True,
            }

            # Store device name/type if found
            if device_name:
                self.devices[device_id]["name"] = device_name
            if device_type:
                self.devices[device_id]["device_type"] = device_type
            if device_code:
                self.devices[device_id]["device_code"] = device_code

            _LOGGER.info("Device %s status changed (name=%s, type=%s)", device_id, device_name, device_type or device_code)
            self.async_set_updated_data(self.devices)

            if is_new:
                # Request device settings to get the name
                await self._request_device_settings(device_id, host)
                async_dispatcher_send(self.hass, SIGNAL_NEW_DEVICE, device_id)

    async def _handle_online(self, message: dict, host: str) -> None:
        """Handle Shelly:Online event."""
        device_id = message.get("deviceId")
        online = message.get("online", 0)  # 1 for online, 0 for offline

        if device_id:
            is_new = device_id not in self.devices
            self._device_host_map[device_id] = host
            self.devices.setdefault(device_id, {})["online"] = online == 1
            _LOGGER.info("Device %s online status: %s", device_id, online == 1)
            self.async_set_updated_data(self.devices)

            if is_new and online == 1:
                async_dispatcher_send(self.hass, SIGNAL_NEW_DEVICE, device_id)

    async def _handle_settings(self, message: dict, host: str) -> None:
        """Handle Shelly:Settings event."""
        device_id = message.get("deviceId")
        settings = message.get("settings", {})

        if device_id:
            self._device_host_map[device_id] = host
            if device_id in self.devices:
                self.devices[device_id]["settings"] = settings
                _LOGGER.info("Device %s settings updated: %s", device_id, settings)
                self.async_set_updated_data(self.devices)

    def get_host_for_device(self, device_id: str) -> str | None:
        """Get the WebSocket host for a device."""
        return self._device_host_map.get(device_id)

    async def send_command(
        self,
        device_id: str,
        cmd: str,
        channel: int = 0,
        action: str = "toggle",
        params: dict | None = None,
        timeout: float = 10.0,
    ) -> dict | None:
        """Send command via WebSocket using Shelly:CommandRequest format.

        Args:
            device_id: Device ID
            cmd: Command type ("relay", "light", "roller")
            channel: Device channel (default 0)
            action: Action to perform ("on", "off", "toggle", "open", "close", "stop", "to_pos")
            params: Additional parameters (e.g., {"pos": 50} for roller position)
            timeout: Response timeout
        """
        host = self._device_host_map.get(device_id)
        if not host:
            _LOGGER.error("No host mapping for device %s", device_id)
            return None

        ws = self._ws_connections.get(host)
        if not ws:
            _LOGGER.error("No WebSocket connection to %s", host)
            return None

        self._message_id += 1
        trid = self._message_id

        # Build params based on command type
        cmd_params = {"id": channel}
        
        if cmd == "roller":
            # Roller commands: open, close, stop, to_pos
            cmd_params["go"] = action
            if params:
                cmd_params.update(params)
        else:
            # Relay/light commands: on, off, toggle
            cmd_params["turn"] = action

        # Build command in Shelly Integrator API format
        command = {
            "event": "Shelly:CommandRequest",
            "trid": trid,
            "deviceId": device_id,
            "data": {
                "cmd": cmd,
                "params": cmd_params,
            }
        }

        _LOGGER.info("Sending command to %s: %s", device_id, command)

        # Create future for response
        future: asyncio.Future = asyncio.Future()
        self._pending_commands[trid] = future

        try:
            await ws.send_json(command)
            result = await asyncio.wait_for(future, timeout=timeout)
            _LOGGER.info("Command response for %s: %s", device_id, result)
            return result
        except asyncio.TimeoutError:
            _LOGGER.warning("Command timeout for %s: %s %s", device_id, cmd, action)
            self._pending_commands.pop(trid, None)
            return None
        except Exception as err:
            _LOGGER.error("Command error: %s", err)
            self._pending_commands.pop(trid, None)
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
