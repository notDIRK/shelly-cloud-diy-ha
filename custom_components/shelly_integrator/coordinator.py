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
from homeassistant.helpers import device_registry as dr

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
        self._pending_verifications: set[str] = set()
        self._settings_requested: set[str] = set()

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

        _LOGGER.debug(
            "Persisting %d known devices",
            len(self._device_host_map),
        )
        new_data = {
            **self._entry.data,
            CONF_KNOWN_DEVICES: dict(self._device_host_map),
        }
        self.hass.config_entries.async_update_entry(
            self._entry, data=new_data
        )

    async def _on_ws_connected(self, host: str) -> None:
        """Handle WebSocket connection established.

        Request status for all known devices on this host.
        """
        _LOGGER.info(
            "WebSocket connected to %s, requesting status",
            host,
        )

        devices_on_host = [
            did for did, h in self._device_host_map.items()
            if h == host
        ]

        if not devices_on_host:
            _LOGGER.debug("No known devices on host %s", host)
            return

        # Track which devices we are waiting for so
        # _devices_ready fires only after ALL respond.
        self._pending_verifications.update(devices_on_host)

        _LOGGER.info(
            "Requesting status for %d devices on %s",
            len(devices_on_host),
            host,
        )
        for device_id in devices_on_host:
            await self._websocket.send_action_request(
                host, "DeviceVerify", device_id
            )

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

    def _resolve_pending(self, device_id: str) -> None:
        """Mark a device verification as complete.

        Sets ``_devices_ready`` once every pending device has
        responded (OK, UNAUTHORIZED, etc.).
        """
        self._pending_verifications.discard(device_id)
        if (
            not self._pending_verifications
            and not self._devices_ready.is_set()
        ):
            _LOGGER.info("All pending device verifications complete")
            self._devices_ready.set()

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

    def _validate_device_access(self, device_id: str, operation: str = "control") -> bool:
        """Validate device access permissions (for debugging UNAUTHORIZED errors).
        
        Args:
            device_id: Device ID
            operation: Operation type (e.g., "control", "read")
        
        Returns:
            True if access is granted, False otherwise
        """
        device_data = self.devices.get(device_id)
        
        if not device_data:
            _LOGGER.error(
                "Device %s not found in coordinator devices. "
                "Available devices: %s",
                device_id, list(self.devices.keys())
            )
            return False
        
        access_groups = device_data.get("access_groups", "00")
        
        # Check if control bit is set (first bit = 0x01)
        try:
            has_control = int(access_groups, 16) & 0x01
        except (ValueError, TypeError):
            _LOGGER.error(
                "Invalid accessGroups value for device %s: %s",
                device_id, access_groups
            )
            return False
        
        # Always log at ERROR level for UNAUTHORIZED diagnostics
        _LOGGER.error(
            "UNAUTHORIZED Diagnostics for device %s: "
            "accessGroups='%s', has_control=%s, device_online=%s, "
            "device_code=%s, device_type=%s",
            device_id, access_groups, bool(has_control),
            device_data.get("online", False),
            device_data.get("device_code", "unknown"),
            device_data.get("device_type", "unknown")
        )
        
        # Log full device data for debugging
        _LOGGER.error(
            "Full device data for %s: %s",
            device_id, device_data
        )
        
        if not has_control:
            _LOGGER.error(
                "Device %s has READ-ONLY access (accessGroups=%s). "
                "Grant control permissions at "
                "https://my.shelly.cloud/integrator.html",
                device_id, access_groups
            )
            return False
        
        _LOGGER.error(
            "Device %s HAS control permission (accessGroups=%s) but still "
            "UNAUTHORIZED. Possible causes: JWT token expired, device "
            "removed from integration, or cloud API issue.",
            device_id, access_groups
        )
        
        return True

    async def send_jrpc_command(
        self,
        device_id: str,
        method: str,
        params: dict | None = None,
        timeout: float = 10.0,
    ) -> dict | None:
        """Send a JRPC command to a Gen2/Gen3 device.

        Uses ``Shelly:JrpcRequest`` which is required for Gen2/Gen3
        RPC methods (Switch.Set, Light.Set, Cover.Open, etc.).

        Args:
            device_id: Device ID
            method: RPC method name (e.g. Switch.Set, Light.Set)
            params: Method parameters
            timeout: Response timeout

        Returns:
            JRPC response or None
        """
        host = self._device_host_map.get(device_id)
        if not host:
            _LOGGER.error("No host for device %s", device_id)
            return None

        response = await self._websocket.send_jrpc_request(
            host=host,
            device_id=device_id,
            method=method,
            params=params,
            timeout=timeout,
        )
        
        # Validate device access if UNAUTHORIZED error is received
        if response and response.get("response", {}).get("error") == "UNAUTHORIZED":
            _LOGGER.warning(
                "UNAUTHORIZED error for device %s method %s - running diagnostics",
                device_id, method
            )
            self._validate_device_access(device_id)
        
        return response

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
            self._resolve_pending(device_id)
            return

        if result == "OK" and device_id:
            # A device is "new" if it was never seen OR if it was
            # only a restore-stub (no device_code yet).  Stubs are
            # created by _restore_known_devices with just
            # {"online": False}.
            existing = self.devices.get(device_id, {})
            is_new = not existing.get("device_code")

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

            if device_status:
                # DeviceVerify response — has full status data.
                # Replace device data entirely (Shelly is source of
                # truth).  Preserve only ``settings`` which arrives
                # via a separate Shelly:Settings event.
                prev_settings = existing.get("settings")

                self.devices[device_id] = {
                    "status": device_status,
                    "device_type": device_type,
                    "device_code": device_code,
                    "name": device_name or existing.get("name"),
                    "access_groups": access_groups,
                    "online": True,
                    "settings": prev_settings,
                }

                _LOGGER.info(
                    "Device verified: %s (name=%s, type=%s)",
                    device_id,
                    device_name or existing.get("name"),
                    device_type,
                )
            else:
                # DeviceGetSettings response — no status data.
                # Merge into existing device data without
                # overwriting status or online flag.
                if device_name:
                    existing["name"] = device_name
                if device_code:
                    existing["device_code"] = device_code
                if device_type:
                    existing["device_type"] = device_type

                self.devices[device_id] = existing

                _LOGGER.info(
                    "Device settings updated: %s (name=%s)",
                    device_id, device_name,
                )

            self.async_set_updated_data(self.devices)

            # Signal ready only when ALL pending devices have
            # been verified (or timed out).
            self._resolve_pending(device_id)

            # Request settings so hostname becomes available
            # (needed for CSV fetch in historical sync).
            # Guard with _settings_requested to prevent an infinite
            # loop: DeviceGetSettings response is also an
            # ActionResponse with result=OK, which would re-trigger
            # this block if device_name is still None.
            if not device_name and device_id not in self._settings_requested:
                self._settings_requested.add(device_id)
                await self._websocket.send_action_request(
                    host, "DeviceGetSettings", device_id
                )

            if is_new:
                # Persist newly discovered device
                await self._persist_known_devices()
                async_dispatcher_send(
                    self.hass, SIGNAL_NEW_DEVICE, device_id
                )

    async def _handle_status_change(self, message: dict, host: str) -> None:
        """Handle Shelly:StatusOnChange event.

        StatusOnChange may deliver only the CHANGED portion of the
        device status (especially for Gen2 RPC devices).  We must
        merge the incoming fields into the existing status so that
        unchanged keys (e.g. gas_sensor, concentration) are preserved.
        """
        device_id = message.get("deviceId")
        new_status = message.get("status", {})

        if not device_id:
            return

        is_new = device_id not in self.devices
        self._device_host_map[device_id] = host

        # Merge incoming status into existing status instead of
        # replacing.  Shallow merge at the top level preserves keys
        # not present in the partial update.
        existing = self.devices.get(device_id, {})
        existing_status = existing.get("status", {})
        merged_status = {**existing_status, **new_status}

        self.devices[device_id] = {
            **existing,
            "status": merged_status,
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

    async def async_remove_device(
        self,
        device_id: str,
        remove_from_registry: bool = False,
    ) -> None:
        """Remove a device from coordinator and persistent storage.

        Args:
            device_id: Shelly Cloud device ID
            remove_from_registry: If True, also remove from HA
                device registry.  Set to False when called from
                ``async_remove_config_entry_device`` (HA handles
                the registry removal itself after we return True).
                Set to True for webhook-triggered removals where
                HA is not involved.
        """
        self._device_host_map.pop(device_id, None)
        self.devices.pop(device_id, None)
        self._settings_requested.discard(device_id)
        await self._persist_known_devices()

        if remove_from_registry:
            dev_reg = dr.async_get(self.hass)
            device = dev_reg.async_get_device(
                identifiers={(DOMAIN, device_id)}
            )
            if device:
                dev_reg.async_remove_device(device.id)

        self.async_set_updated_data(self.devices)
        _LOGGER.info("Device removed: %s", device_id)

    async def async_close(self) -> None:
        """Close all connections."""
        if self._token_refresh_unsub:
            self._token_refresh_unsub()
            self._token_refresh_unsub = None

        await self._websocket.disconnect_all()
