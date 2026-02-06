"""DataUpdateCoordinator for Shelly Integrator.

This is a thin orchestrator that coordinates:
- API layer (auth, websocket)
- Device registry
- Home Assistant update coordinator
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Callable

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .api.auth import ShellyAuth
from .api.websocket import ShellyWebSocket
from .const import DOMAIN, TOKEN_REFRESH_INTERVAL

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

SIGNAL_NEW_DEVICE = f"{DOMAIN}_new_device"
CONF_KNOWN_DEVICES = "known_devices"


class ShellyIntegratorCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for Shelly Integrator.

    Orchestrates WebSocket connections, device discovery, and state updates.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        tag: str,
        token: str,
        jwt_token: str,
        entry: "ConfigEntry",
    ) -> None:
        """Initialize the coordinator.

        Args:
            hass: Home Assistant instance
            session: aiohttp client session
            tag: Integrator tag
            token: User's integrator token
            jwt_token: Initial JWT token
            entry: Config entry (for persistent storage)
        """
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )

        self._entry = entry

        # API layer
        self._auth = ShellyAuth(session, tag, token, jwt_token=jwt_token)

        self._websocket = ShellyWebSocket(
            session=session,
            jwt_token_provider=lambda: self._auth.jwt_token,
            message_handler=self._handle_ws_message,
            on_connected=self._on_ws_connected,
        )

        # Device state
        self.devices: dict[str, Any] = {}
        self._device_host_map: dict[str, str] = {}
        self._token_refresh_unsub: Callable[[], None] | None = None
        self._devices_ready = asyncio.Event()

        # Restore known devices from persistent storage
        self._restore_known_devices()

    @property
    def hosts(self) -> set[str]:
        """Return connected hosts."""
        return self._websocket.connected_hosts

    def _restore_known_devices(self) -> None:
        """Restore known devices from persistent storage."""
        known = self._entry.data.get(CONF_KNOWN_DEVICES, {})
        if known:
            _LOGGER.info("Restoring %d known devices from storage", len(known))
            for device_id, host in known.items():
                self._device_host_map[device_id] = host
                # Initialize empty device entry
                self.devices[device_id] = {"online": False}

    async def _persist_known_devices(self) -> None:
        """Persist known devices to storage."""
        if not self._device_host_map:
            return

        current_known = self._entry.data.get(CONF_KNOWN_DEVICES, {})
        if current_known == self._device_host_map:
            return  # No changes

        _LOGGER.debug("Persisting %d known devices", len(self._device_host_map))
        new_data = {**self._entry.data, CONF_KNOWN_DEVICES: dict(self._device_host_map)}
        self.hass.config_entries.async_update_entry(self._entry, data=new_data)

    async def _on_ws_connected(self, host: str) -> None:
        """Handle WebSocket connection established.

        Request status for all known devices on this host.
        """
        _LOGGER.info("WebSocket connected to %s, requesting device status", host)

        devices_on_host = [
            did for did, h in self._device_host_map.items() if h == host
        ]

        if not devices_on_host:
            _LOGGER.debug("No known devices on host %s", host)
            return

        _LOGGER.info("Requesting status for %d devices on %s", len(devices_on_host), host)
        for device_id in devices_on_host:
            await self._websocket.send_action_request(host, "DeviceVerify", device_id)
            # Small delay to avoid flooding
            await asyncio.sleep(0.1)

    async def _async_update_data(self) -> dict[str, Any]:
        """Return current device data (fallback polling)."""
        return self.devices

    async def async_wait_for_devices(self, timeout: float = 5.0) -> bool:
        """Wait for devices to be verified after connection.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if devices are ready, False if timeout
        """
        if not self._device_host_map:
            # No known devices to wait for
            return True

        try:
            await asyncio.wait_for(self._devices_ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout waiting for device verification")
            return False

    async def async_config_entry_first_refresh(self) -> None:
        """Start connections on first refresh."""
        # Schedule token refresh
        self._token_refresh_unsub = async_track_time_interval(
            self.hass,
            self._async_refresh_token,
            timedelta(seconds=TOKEN_REFRESH_INTERVAL),
        )

        await super().async_config_entry_first_refresh()

    async def _async_refresh_token(self, _now=None) -> None:
        """Refresh JWT token and reconnect WebSocket."""
        try:
            await self._auth.refresh_token()
            await self._websocket.reconnect_all()
        except Exception as err:
            _LOGGER.error("Token refresh failed: %s", err)

    async def connect_to_host(self, host: str) -> None:
        """Connect to a Shelly Cloud WebSocket server.

        Device status requests are handled by _on_ws_connected callback
        once the connection is actually established.
        """
        await self._websocket.connect(host)

    async def send_command(
        self,
        device_id: str,
        cmd: str,
        channel: int = 0,
        action: str = "toggle",
        params: dict | None = None,
        timeout: float = 10.0,
    ) -> dict | None:
        """Send command to a device.

        Args:
            device_id: Device ID
            cmd: Command type (relay, light, roller)
            channel: Device channel
            action: Action (on, off, toggle, open, close, stop)
            params: Additional parameters
            timeout: Response timeout

        Returns:
            Command response or None
        """
        host = self._device_host_map.get(device_id)
        if not host:
            _LOGGER.error("No host for device %s", device_id)
            return None

        return await self._websocket.send_command(
            host=host,
            device_id=device_id,
            cmd=cmd,
            channel=channel,
            action=action,
            params=params,
            timeout=timeout,
        )

    async def _handle_ws_message(self, message: dict, host: str) -> None:
        """Handle incoming WebSocket message.

        Routes messages to appropriate handlers.
        """
        event = message.get("event")

        if event == "Integrator:ActionResponse":
            await self._handle_action_response(message, host)
        elif event == "Shelly:StatusOnChange":
            await self._handle_status_change(message, host)
        elif event == "Shelly:Online":
            await self._handle_online(message, host)
        elif event == "Shelly:Settings":
            await self._handle_settings(message, host)
        elif event == "Error":
            _LOGGER.error("Server error: %s", message.get("message"))
        elif event:
            _LOGGER.debug("Unhandled event: %s", event)

    async def _handle_action_response(self, message: dict, host: str) -> None:
        """Handle Integrator:ActionResponse."""
        data = message.get("data", {})
        result = data.get("result")
        device_id = data.get("deviceId")

        if result == "WRONG_HOST":
            correct_host = data.get("host")
            if correct_host and device_id:
                _LOGGER.info(
                    "Device %s on different host: %s", device_id, correct_host
                )
                self._device_host_map[device_id] = correct_host
                if correct_host not in self.hosts:
                    await self.connect_to_host(correct_host)
            return

        if result == "UNAUTHORIZED":
            _LOGGER.error("Unauthorized: %s", device_id)
            return

        if result == "OK" and device_id:
            is_new = device_id not in self.devices
            self._device_host_map[device_id] = host

            # Extract device info
            device_type = data.get("deviceType")
            device_code = data.get("deviceCode")
            device_status = data.get("deviceStatus", {})
            device_settings = data.get("deviceSettings", {})
            access_groups = data.get("accessGroups", "00")

            # Get name from settings
            device_name = None
            if device_settings:
                device_name = device_settings.get("name")
                if not device_name and "device" in device_settings:
                    dev = device_settings.get("device", {})
                    device_name = dev.get("hostname")

            # Update device
            if device_id in self.devices:
                device = self.devices[device_id]
                if device_type:
                    device["device_type"] = device_type
                if device_code:
                    device["device_code"] = device_code
                if device_name:
                    device["name"] = device_name
                if device_status:
                    device["status"] = device_status
                    device["online"] = True
                if access_groups:
                    device["access_groups"] = access_groups
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
                "Device verified: %s (name=%s, type=%s)",
                device_id, device_name, device_type
            )

            self.async_set_updated_data(self.devices)

            # Signal that at least one device is ready
            if not self._devices_ready.is_set():
                self._devices_ready.set()

            if is_new:
                # Persist newly discovered device
                await self._persist_known_devices()
                async_dispatcher_send(self.hass, SIGNAL_NEW_DEVICE, device_id)

    async def _handle_status_change(self, message: dict, host: str) -> None:
        """Handle Shelly:StatusOnChange event."""
        device_id = message.get("deviceId")
        status = message.get("status", {})

        if not device_id:
            return

        is_new = device_id not in self.devices
        self._device_host_map[device_id] = host

        # Update device
        self.devices[device_id] = {
            **self.devices.get(device_id, {}),
            "status": status,
            "online": True,
        }

        _LOGGER.debug("Status changed: %s", device_id)
        self.async_set_updated_data(self.devices)

        if is_new:
            # Persist newly discovered device
            await self._persist_known_devices()
            # Request settings for new device
            await self._websocket.send_action_request(
                host, "DeviceGetSettings", device_id
            )
            async_dispatcher_send(self.hass, SIGNAL_NEW_DEVICE, device_id)

    async def _handle_online(self, message: dict, host: str) -> None:
        """Handle Shelly:Online event."""
        device_id = message.get("deviceId")
        online = message.get("online", 0) == 1

        if not device_id:
            return

        is_new = device_id not in self.devices
        self._device_host_map[device_id] = host
        self.devices.setdefault(device_id, {})["online"] = online

        _LOGGER.debug("Online status: %s = %s", device_id, online)
        self.async_set_updated_data(self.devices)

        if is_new and online:
            async_dispatcher_send(self.hass, SIGNAL_NEW_DEVICE, device_id)

    async def _handle_settings(self, message: dict, host: str) -> None:
        """Handle Shelly:Settings event."""
        device_id = message.get("deviceId")
        settings = message.get("settings", {})

        if device_id and device_id in self.devices:
            self._device_host_map[device_id] = host
            self.devices[device_id]["settings"] = settings
            _LOGGER.debug("Settings updated: %s", device_id)
            self.async_set_updated_data(self.devices)

    async def async_close(self) -> None:
        """Close all connections."""
        if self._token_refresh_unsub:
            self._token_refresh_unsub()
            self._token_refresh_unsub = None

        await self._websocket.disconnect_all()
