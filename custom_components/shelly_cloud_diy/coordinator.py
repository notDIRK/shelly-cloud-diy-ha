"""DataUpdateCoordinator for Shelly Cloud DIY.

Polls the Shelly Cloud Control API (``POST /device/all_status``) at a
configurable interval and exposes the full fleet state to the entity
platforms through ``coordinator.devices``.

The coordinator also provides :meth:`send_command` as a thin, platform-
facing adapter around the Cloud Control API's command endpoints so that
platform files (``switch.py``, ``light.py``, ``cover.py``) do not need to
know about the raw HTTP shape.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api.cloud_control import (
    ShellyCloudAuthError,
    ShellyCloudControl,
    ShellyCloudError,
)
from .const import (
    CONF_POLL_INTERVAL,
    DOMAIN,
    POLL_INTERVAL_DEFAULT,
    SIGNAL_NEW_DEVICE,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


class ShellyCloudCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Poll the Shelly Cloud Control API and publish device state to HA.

    ``self.devices`` is the authoritative device snapshot. Each entry has
    the shape::

        {
            "status": <full status dict, including _dev_info>,
            "online": bool,
            "device_code": str,
            "name": str | None,
        }

    The platform files (``switch.py``, ``sensor.py``, …) read from this
    structure; ``entities/base.py`` provides the shared access helpers.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: ShellyCloudControl,
    ) -> None:
        """Initialise the coordinator.

        The poll interval is taken from ``entry.options`` so that the user
        can change it at runtime via the options flow without reinstalling.
        """
        interval_s = int(
            entry.options.get(CONF_POLL_INTERVAL, POLL_INTERVAL_DEFAULT)
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval_s),
        )

        self._entry = entry
        self._api = api
        self.devices: dict[str, dict[str, Any]] = {}
        self._known_device_ids: set[str] = set()

    # ── Properties platform code may inspect ──────────────────────────

    @property
    def api(self) -> ShellyCloudControl:
        """Expose the API client for platform-level calls if needed."""
        return self._api

    # ── Polling ───────────────────────────────────────────────────────

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Fetch the full device snapshot from Shelly Cloud.

        Runs every ``update_interval`` seconds. A single HTTP request
        retrieves the state of every device the account can see.
        """
        try:
            data = await self._api.get_all_status()
        except ShellyCloudAuthError as err:
            # Surfaces as "repair me" in HA → user must re-enter auth_key
            raise ConfigEntryAuthFailed(str(err)) from err
        except ShellyCloudError as err:
            raise UpdateFailed(f"Shelly Cloud poll failed: {err}") from err

        devices_status = data.get("devices_status") or {}
        if not isinstance(devices_status, dict):
            raise UpdateFailed(
                f"Unexpected devices_status shape: {type(devices_status)}"
            )

        new_devices: dict[str, dict[str, Any]] = {}
        for device_id, status in devices_status.items():
            if not isinstance(status, dict):
                continue
            dev_info = status.get("_dev_info", {}) if isinstance(status, dict) else {}
            new_devices[device_id] = {
                "status": status,
                "online": bool(dev_info.get("online", False)),
                "device_code": dev_info.get("code") or "",
                # /device/all_status does not return the user-set name;
                # stays None and the base-entity falls back to the model name.
                "name": None,
            }

        # Fire SIGNAL_NEW_DEVICE for devices we hadn't seen on previous polls
        # so platform async_setup_entry handlers can register new entities.
        newly_seen = set(new_devices) - self._known_device_ids
        if newly_seen:
            _LOGGER.info("Cloud Control API: discovered %d new device(s)", len(newly_seen))
        for device_id in newly_seen:
            async_dispatcher_send(self.hass, SIGNAL_NEW_DEVICE, device_id)
        self._known_device_ids = set(new_devices)

        self.devices = new_devices
        return new_devices

    # ── Command dispatch (compat shim for platform files) ─────────────

    async def send_command(
        self,
        device_id: str,
        cmd: str,
        channel: int = 0,
        action: Any = "toggle",
    ) -> dict[str, Any] | None:
        """Send a device command and return a response the platforms expect.

        This is a compatibility shim over :class:`ShellyCloudControl`. The
        pre-pivot platform code called ``send_command(cmd="relay", …)`` and
        expected a dict shaped like ``{"data": {"isok": bool}}``. We keep
        that contract so platform files do not all need to be rewritten
        during M1.

        Args:
            device_id: Shelly Cloud device id.
            cmd: One of ``"relay"``, ``"light"``, ``"roller"``.
            channel: Channel index on the device.
            action: For ``"relay"``: ``"on"`` / ``"off"`` / ``"toggle"``.
                For ``"light"``: either the same strings or a dict with
                keys like ``{"on": bool, "brightness": int}``.
                For ``"roller"``: ``"open"`` / ``"close"`` / ``"stop"`` or
                an int position 0..100.

        Returns:
            ``{"data": {…}}`` on success, or ``None`` on failure (error logged).
        """
        try:
            if cmd == "relay":
                turn = self._normalise_turn(action)
                if turn is None:
                    _LOGGER.error("Invalid relay action: %r", action)
                    return None
                data = await self._api.relay_control(
                    device_id, turn, channel=channel
                )

            elif cmd == "light":
                kwargs = self._light_kwargs(action)
                data = await self._api.light_control(
                    device_id, channel=channel, **kwargs
                )

            elif cmd == "roller":
                if isinstance(action, int):
                    data = await self._api.roller_control(
                        device_id, channel=channel, go_to_pos=action
                    )
                elif isinstance(action, str):
                    data = await self._api.roller_control(
                        device_id, channel=channel, direction=action
                    )
                else:
                    _LOGGER.error("Invalid roller action: %r", action)
                    return None

            else:
                _LOGGER.error("Unknown command cmd=%r", cmd)
                return None

        except ShellyCloudAuthError as err:
            _LOGGER.error("Auth rejected while sending %s to %s: %s", cmd, device_id, err)
            raise ConfigEntryAuthFailed(str(err)) from err
        except ShellyCloudError as err:
            _LOGGER.error("Command %s for %s failed: %s", cmd, device_id, err)
            return None

        # Schedule a fresh poll so the UI reflects the change within one
        # request rather than waiting for the next polling tick.
        self.hass.async_create_task(self.async_request_refresh())
        return {"data": data}

    @staticmethod
    def _normalise_turn(action: Any) -> str | None:
        """Coerce various platform-supplied action shapes to a turn string."""
        if isinstance(action, str) and action in ("on", "off", "toggle"):
            return action
        if action is True:
            return "on"
        if action is False:
            return "off"
        return None

    @classmethod
    def _light_kwargs(cls, action: Any) -> dict[str, Any]:
        """Translate a platform-supplied light action to light_control kwargs."""
        if isinstance(action, str):
            turn = cls._normalise_turn(action)
            return {"turn": turn} if turn else {}
        if isinstance(action, dict):
            kw: dict[str, Any] = {}
            if "on" in action:
                kw["turn"] = "on" if action["on"] else "off"
            elif "turn" in action:
                kw["turn"] = cls._normalise_turn(action["turn"])
            for key in ("brightness", "white", "temp", "red", "green", "blue"):
                if key in action and action[key] is not None:
                    kw[key] = action[key]
            return kw
        return {}
