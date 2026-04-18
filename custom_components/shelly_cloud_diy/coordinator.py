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

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
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
    CONF_CREATE_ALL_INITIALLY,
    CONF_ENABLED_DEVICES,
    CONF_POLL_INTERVAL,
    DOMAIN,
    POLL_INTERVAL_DEFAULT,
    SIGNAL_NEW_DEVICE,
)

# Gap between the v1 poll completing and the v2 name lookup firing, so we
# stay under the 1 req/s per-account rate limit that both endpoints share.
_V2_NAME_LOOKUP_GAP_S = 1.2

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
        # Cache of device_id → user-set name fetched from the v2 API. Names
        # are resolved lazily after the first successful poll and whenever
        # new devices appear; we never re-fetch already-known names (they
        # change rarely and cost rate-limit budget).
        self.device_names: dict[str, str] = {}
        # Populated by ``_refresh_device_names`` when it schedules itself.
        self._name_lookup_in_flight = False

    # ── Properties platform code may inspect ──────────────────────────

    @property
    def api(self) -> ShellyCloudControl:
        """Expose the API client for platform-level calls if needed."""
        return self._api

    # ── Per-device opt-in gate (v0.4.0) ───────────────────────────────

    @property
    def _options(self) -> dict[str, Any]:
        return dict(self._entry.options)

    @property
    def create_all_initially(self) -> bool:
        """Whether every account-visible device should be materialised.

        Set to ``True`` for v0.3.x upgraders (via the migration in
        ``async_setup_entry``) and for users who tick "create entities for
        all devices" during setup. Users can later untick this in the
        options flow and switch to a curated subset.
        """
        return bool(self._options.get(CONF_CREATE_ALL_INITIALLY, False))

    @property
    def enabled_ids(self) -> set[str]:
        """Return the set of device_ids that should produce HA entities.

        Semantics:
        - ``create_all_initially=True`` → all devices (returns the full
          set of currently-known ids).
        - otherwise, ``enabled_devices`` list → that set.
        - neither present (shouldn't happen post-migration but guarded for
          safety) → all devices, same as ``create_all_initially``.
        """
        opts = self._options
        if opts.get(CONF_CREATE_ALL_INITIALLY):
            return set(self.devices.keys())
        raw = opts.get(CONF_ENABLED_DEVICES)
        if isinstance(raw, list):
            return {d for d in raw if isinstance(d, str)}
        # No explicit selection — fall back to all (greenfield safety net).
        return set(self.devices.keys())

    def is_enabled(self, device_id: str) -> bool:
        """Return True if ``device_id`` should be materialised as entities."""
        opts = self._options
        if opts.get(CONF_CREATE_ALL_INITIALLY):
            return True
        raw = opts.get(CONF_ENABLED_DEVICES)
        if isinstance(raw, list):
            return device_id in raw
        return True

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
            # Shelly Cloud puts metadata under ``_dev_info`` only for BLE
            # gateway-bridged devices (``gen == "GBLE"``); for Gen2/Gen3
            # Shelly devices the fields live at the top level of the status
            # (``code``, ``id``, ``cloud.connected``). Probe both.
            dev_info = status.get("_dev_info") if isinstance(status, dict) else None
            if not isinstance(dev_info, dict):
                dev_info = {}

            code = dev_info.get("code") or status.get("code") or ""

            if "online" in dev_info:
                online = bool(dev_info.get("online"))
            else:
                cloud = status.get("cloud")
                online = bool(cloud.get("connected")) if isinstance(cloud, dict) else False

            new_devices[device_id] = {
                "status": status,
                "online": online,
                "device_code": code,
                # Seed with whatever we already resolved via the v2 name
                # lookup; stays None until that lookup succeeds.
                "name": self.device_names.get(device_id),
            }

        # Fire SIGNAL_NEW_DEVICE only for devices the user has actually
        # enabled — if they've opted out of a device we still poll it (so
        # commands and data stay consistent) but we never materialise
        # entities for it.
        newly_seen = set(new_devices) - self._known_device_ids
        if newly_seen:
            _LOGGER.info("Cloud Control API: discovered %d new device(s)", len(newly_seen))
        for device_id in newly_seen:
            if self.is_enabled(device_id):
                async_dispatcher_send(self.hass, SIGNAL_NEW_DEVICE, device_id)
        self._known_device_ids = set(new_devices)

        self.devices = new_devices

        # Schedule a v2 name lookup for any device we haven't resolved yet,
        # but only for devices currently online (v2 returns no settings
        # for offline devices so the call is wasted on them).
        unresolved = [
            did for did, info in new_devices.items()
            if did not in self.device_names and info.get("online")
        ]
        if unresolved and not self._name_lookup_in_flight:
            self._name_lookup_in_flight = True
            self.hass.async_create_task(self._refresh_device_names(unresolved))

        return new_devices

    async def _refresh_device_names(self, ids: list[str]) -> None:
        """Fetch user-set names for ``ids`` via the v2 API and cache them.

        Runs as a background task after ``_async_update_data`` completes so
        it does not delay the coordinator's next tick. Waits
        ``_V2_NAME_LOOKUP_GAP_S`` to stay under the shared 1 req/s rate
        limit, then batches every missing id into a single v2 request.
        Failures are logged at debug level — a missing name is not worth
        bubbling up as an UpdateFailed.
        """
        try:
            await asyncio.sleep(_V2_NAME_LOOKUP_GAP_S)
            names = await self._api.get_device_names(ids)
        except ShellyCloudAuthError:
            _LOGGER.debug("v2 name lookup rejected auth_key — skipping")
            return
        except ShellyCloudError as err:
            _LOGGER.debug("v2 name lookup failed: %s", err)
            return
        finally:
            self._name_lookup_in_flight = False

        if not names:
            return

        self.device_names.update(names)
        for did, name in names.items():
            entry = self.devices.get(did)
            if entry is not None:
                entry["name"] = name
        _LOGGER.info("Resolved %d device name(s) via v2 API", len(names))

        # Push the resolved names into the HA device registry so existing
        # DeviceEntry rows (created at integration setup with a fallback
        # "Shelly <model> (<id>)" label) get renamed on the spot. HA only
        # reads ``DeviceInfo.name`` on the first registration; later changes
        # via ``device_info`` are ignored, so without this explicit update
        # the v2 names would never surface in the UI.
        #
        # ``async_update_device(name=…)`` only writes the technical ``name``
        # field, never ``name_by_user`` — so any user who renamed a device
        # in the HA UI keeps their override intact.
        dev_reg = dr.async_get(self.hass)
        updated_in_registry = 0
        for did, resolved in names.items():
            formatted = f"{resolved} ({did})"
            device_entry = dev_reg.async_get_device(
                identifiers={(DOMAIN, did)}
            )
            if device_entry is None:
                # Entity creation hasn't happened yet (device not enabled
                # in options, or first poll still propagating). Name will
                # be picked up naturally when the entity registers.
                continue
            if device_entry.name == formatted:
                continue
            dev_reg.async_update_device(device_entry.id, name=formatted)
            updated_in_registry += 1
        if updated_in_registry:
            _LOGGER.info(
                "Updated %d device name(s) in HA device registry",
                updated_in_registry,
            )

        # Push updated device_info to platforms without waiting for next poll.
        self.async_update_listeners()

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
