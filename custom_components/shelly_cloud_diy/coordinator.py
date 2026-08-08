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
import re
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api.cloud_control import (
    ShellyCloudAuthError,
    ShellyCloudControl,
    ShellyCloudError,
    ShellyCloudRateLimitError,
)
from .const import (
    CONF_CREATE_ALL_INITIALLY,
    CONF_ENABLED_DEVICES,
    CONF_POLL_INTERVAL,
    DOMAIN,
    POLL_INTERVAL_DEFAULT,
    SIGNAL_NEW_DEVICE,
)
from .repair_issues import (
    async_manage_missing_devices_issue,
    async_manage_rate_limit_issue,
    compute_missing_devices,
    is_mass_absence,
    missing_devices_verdict,
    rate_limit_verdict,
)

# Gap between the v1 poll completing and the v2 name lookup firing, so we
# stay under the 1 req/s per-account rate limit that both endpoints share.
_V2_NAME_LOOKUP_GAP_S = 1.2

# Status/config keys of Gen2/Gen3 virtual components (``number:200``, …). Used
# to decide which online devices need a one-time v2 config fetch so their
# read-only virtual entities can render real names/units/options. (#9)
_VIRTUAL_COMPONENT_KEY_RE = re.compile(r"^(number|enum|text|boolean):\d+$")

# ── Deep-sleep (battery) device freshness ─────────────────────────────
#
# Battery devices (H&T, Flood, Door/Window, …) are awake for a few seconds
# every ``sys.wakeup_period`` and spend the rest in deep sleep. Shelly Cloud
# keeps serving the last snapshot such a device pushed, and that snapshot is
# typically captured seconds after boot — before the device's cloud session
# is up. So ``cloud.connected`` is ``false`` in it and stays false forever,
# even though the readings it carries are current. Availability therefore
# cannot be derived from the transport flag for these devices; it has to come
# from "is the device still checking in?". (#13)
#
# Multiplier applied to ``wakeup_period`` to get the staleness window. Matches
# ``UPDATE_PERIOD_MULTIPLIER`` in HA core's native Shelly integration, so a
# device that misses a single check-in is still considered alive but one that
# stops reporting altogether goes unavailable.
SLEEP_STALE_MULTIPLIER = 2.2

# Period assumed for a device that reports itself sleeping but exposes no
# usable ``wakeup_period`` (Gen1 battery devices publish no such field).
SLEEP_ASSUMED_PERIOD_S = 2 * 3600

# Lower bound on the staleness window. Deliberately generous — briefly showing
# a dead device as available is far less harmful than flapping a working one.
SLEEP_STALE_FLOOR_S = 4 * 3600

# Hard ceiling on the staleness window, so a device configured with an absurd
# wakeup period cannot stay "available" indefinitely after it dies.
SLEEP_STALE_CAP_S = 24 * 3600

# Delay before the post-command status refresh (seconds). Long enough for the
# Shelly Cloud to propagate the new state (so the poll confirms rather than
# reverts the optimistic entity state) and to keep the command + its refresh
# from bursting past the 1 req/s budget. (#6)
POST_COMMAND_REFRESH_DELAY = 2.0

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


def sleep_period_s(status: dict[str, Any]) -> int:
    """Return the deep-sleep period of ``status`` in seconds, 0 if not sleeping.

    A non-zero result marks the device as a battery/deep-sleep device, which
    is what :attr:`ShellyBaseEntity.available` keys off. Gen2/Gen3 devices
    publish ``sys.wakeup_period``; devices that only carry the cloud's
    ``_sleeping`` marker fall back to :data:`SLEEP_ASSUMED_PERIOD_S`.

    A device running off external power is never treated as sleeping, even
    when it still carries a ``wakeup_period`` from its battery days — it is
    permanently connected, so the transport flag is meaningful again and an
    offline reading means the device really is gone. (#13)
    """
    if not isinstance(status, dict):
        return 0

    power = status.get("devicepower:0")
    external = power.get("external") if isinstance(power, dict) else None
    if isinstance(external, dict) and external.get("present") is True:
        return 0

    sys_block = status.get("sys")
    period = sys_block.get("wakeup_period") if isinstance(sys_block, dict) else None
    if isinstance(period, (int, float)) and not isinstance(period, bool) and period > 0:
        return int(period)
    return SLEEP_ASSUMED_PERIOD_S if status.get("_sleeping") is True else 0


def checkin_marker(status: dict[str, Any]) -> tuple[Any, ...]:
    """Return the fields that change whenever the device pushes a new snapshot.

    Used as a change-detection fingerprint rather than reading any of these
    as a clock: ``ts`` can lag the rest of the payload by hours, ``_updated``
    carries no timezone, and device clocks drift. Comparing the fingerprint
    against the previous poll and stamping *our own* monotonic time when it
    changes sidesteps all three. (#13)
    """
    if not isinstance(status, dict):
        return ()
    sys_block = status.get("sys") if isinstance(status.get("sys"), dict) else {}
    return (
        status.get("_updated"),
        status.get("serial"),
        sys_block.get("unixtime"),
        sys_block.get("uptime"),
        status.get("ts"),
    )


class ShellyCloudCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Poll the Shelly Cloud Control API and publish device state to HA.

    ``self.devices`` is the authoritative device snapshot. Each entry has
    the shape::

        {
            "status": <full status dict, including _dev_info>,
            "online": bool,
            "sleeping": bool,
            "sleep_stale_at": float | None,
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
        # Ids covered by a completed name lookup, including those the account
        # has no alias for — keeps a never-renamed device from re-triggering
        # the lookup on every poll. (#13)
        self._names_attempted: set[str] = set()
        # Populated by ``_refresh_device_names`` when it schedules itself.
        self._name_lookup_in_flight = False
        # Cache of device_id → {component_key → v2 config dict} for Gen2/Gen3
        # virtual components. Fetched lazily once per device (config changes
        # rarely) so the read-only virtual entities can show real names,
        # units and enum options instead of generic labels. (#9)
        self.virtual_configs: dict[str, dict[str, dict]] = {}
        # Populated by ``_refresh_virtual_configs`` when it schedules itself.
        self._vcomp_config_in_flight = False
        # device_id → (checkin fingerprint, monotonic timestamp of the poll
        # where that fingerprint first appeared). Drives the staleness window
        # for deep-sleep battery devices. (#13)
        self._sleep_seen: dict[str, tuple[tuple, float]] = {}

        # Repair bookkeeping. Streaks are counted here, never in the issue
        # registry, so a self-healing rate limit cannot flap an issue card.
        self._rate_limit_streak = 0
        self._rate_limit_since: float | None = None
        # Whether the WARNING has already been logged for the current
        # episode, so the log fires on the transition rather than on a
        # streak count the UI does not yet agree with.
        self._rate_limit_reported = False
        # Absence is tracked PER DEVICE, not per set: id -> consecutive
        # successful polls absent, and id -> monotonic first-absent time.
        # Set-level bookkeeping would restart every device's 24 h clock
        # whenever any one device joined or left the missing set, so a
        # WORSENING condition (a second device vanishing) would hide the
        # existing card for a full day and destroy the user's "Ignore".
        self._missing_streak: dict[str, int] = {}
        self._missing_since: dict[str, float] = {}

    # ── Deep-sleep freshness bookkeeping (#13) ────────────────────────

    def _evaluate_sleep_state(
        self, device_id: str, status: dict[str, Any], now: float
    ) -> tuple[bool, float | None]:
        """Return ``(sleeping, stale_at)`` for one device's status snapshot.

        ``sleeping`` marks a deep-sleep battery device — for those the cloud's
        transport flag is meaningless and availability is decided by whether
        the device is still checking in. ``stale_at`` is the monotonic
        deadline at which the last check-in stops counting; ``None`` for
        devices this does not apply to.

        Returning a deadline rather than a boolean matters when polling breaks:
        the entity keeps re-evaluating against its own clock, so a sleeping
        device eventually goes unavailable during a prolonged cloud outage
        instead of freezing on the last verdict. It also avoids tying
        availability to ``last_update_success``, which a single transient
        ``401 max_req`` would flip for the whole fleet at once.

        ``now`` is a monotonic timestamp supplied by the caller so the whole
        decision stays deterministic and testable.
        """
        period = sleep_period_s(status)
        if not period:
            # Mains device (or a battery device now on external power) — drop
            # any history so it cannot leak into a later evaluation, and leave
            # availability to ``online``.
            self._sleep_seen.pop(device_id, None)
            return False, None

        marker = checkin_marker(status)
        previous = self._sleep_seen.get(device_id)
        if previous is None or previous[0] != marker:
            # First sight, or the device pushed a new snapshot: (re)start the
            # window. First sight counts as a check-in — we have no evidence
            # the device is gone, and the alternative would make every battery
            # device unavailable for one window after every HA restart.
            self._sleep_seen[device_id] = (marker, now)
            last_seen = now
        else:
            last_seen = previous[1]

        window = min(
            max(period * SLEEP_STALE_MULTIPLIER, SLEEP_STALE_FLOOR_S),
            SLEEP_STALE_CAP_S,
        )
        return True, last_seen + window

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
        # Order matters: ShellyCloudRateLimitError and ShellyCloudAuthError
        # are siblings under ShellyCloudError, so both specific branches must
        # precede the generic one.
        try:
            data = await self._api.get_all_status()
        except ShellyCloudAuthError as err:
            # Surfaces as "repair me" in HA → user must re-enter auth_key
            self._note_poll_not_rate_limited()
            raise ConfigEntryAuthFailed(str(err)) from err
        except ShellyCloudRateLimitError as err:
            # Shelly signals its 1 req/s limit as HTTP 401 with ``max_req``
            # in the body — indistinguishable from a rejected key by status
            # code alone. Surface it as its own repair once sustained. (#6)
            self._note_rate_limited()
            raise UpdateFailed(f"Shelly Cloud poll failed: {err}") from err
        except ShellyCloudError as err:
            self._note_poll_not_rate_limited()
            raise UpdateFailed(f"Shelly Cloud poll failed: {err}") from err

        devices_status = data.get("devices_status") or {}
        if not isinstance(devices_status, dict):
            self._note_poll_not_rate_limited()
            raise UpdateFailed(
                f"Unexpected devices_status shape: {type(devices_status)}"
            )

        new_devices: dict[str, dict[str, Any]] = {}
        now = time.monotonic()
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

            # A deep-sleep battery device is "not connected" by construction
            # between wakes, so its availability comes from whether it is
            # still checking in rather than from ``online``. (#13)
            sleeping, sleep_stale_at = self._evaluate_sleep_state(
                device_id, status, now
            )

            new_devices[device_id] = {
                "status": status,
                "online": online,
                "sleeping": sleeping,
                "sleep_stale_at": sleep_stale_at,
                "device_code": code,
                # Seed with whatever we already resolved via the v2 name
                # lookup; stays None until that lookup succeeds.
                "name": self.device_names.get(device_id),
                # Seed cached virtual-component config (real names/units/
                # options); stays None until the v2 config fetch succeeds. (#9)
                "virtual_config": self.virtual_configs.get(device_id),
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

        # Forget check-in history for devices that have been absent from the
        # poll for longer than the longest window we would ever apply, so
        # ``_sleep_seen`` cannot grow without bound. Absence alone is not
        # enough: ``/device/all_status`` intermittently omits devices, and
        # dropping the history on every omission would restart the window on
        # each reappearance — a device that flaps in and out would then never
        # be able to go stale.
        for gone in set(self._sleep_seen) - set(new_devices):
            if now - self._sleep_seen[gone][1] > SLEEP_STALE_CAP_S:
                self._sleep_seen.pop(gone, None)

        self.devices = new_devices

        # Schedule a name lookup for any device we haven't resolved yet.
        # Sleeping devices are included: the lookup hits the account-wide v1
        # alias listing, which returns aliases regardless of whether a device
        # is currently connected — gating them out only meant battery sensors
        # never got their Shelly-app name. Devices already covered by a
        # completed lookup are excluded even when they came back nameless,
        # otherwise a device the user never renamed would re-trigger the
        # lookup on every single poll and burn the shared 1 req/s budget. (#13)
        unresolved = [
            did for did, info in new_devices.items()
            if did not in self.device_names
            and did not in self._names_attempted
            and (info.get("online") or info.get("sleeping"))
        ]
        if unresolved and not self._name_lookup_in_flight:
            self._name_lookup_in_flight = True
            self.hass.async_create_task(self._refresh_device_names(unresolved))

        # Schedule a one-time v2 config fetch for any ONLINE device whose
        # status carries at least one virtual component we haven't resolved
        # yet, so the read-only virtual entities can render real names, units
        # and enum options. Config changes rarely, so once a device is in
        # ``virtual_configs`` it is never re-fetched. (#9)
        needs_config = [
            did for did, info in new_devices.items()
            if did not in self.virtual_configs
            and info.get("online")
            and any(
                isinstance(k, str) and _VIRTUAL_COMPONENT_KEY_RE.match(k)
                for k in info.get("status", {})
            )
        ]
        if needs_config and not self._vcomp_config_in_flight:
            self._vcomp_config_in_flight = True
            self.hass.async_create_task(self._refresh_virtual_configs(needs_config))

        # The poll succeeded, so whatever the previous outcome was, it was
        # not a sustained rate limit.
        self._note_poll_not_rate_limited()
        self._evaluate_missing_devices(set(new_devices))

        return new_devices

    # ── Repair-issue evaluation ───────────────────────────────────────

    def _note_rate_limited(self) -> None:
        """Extend the consecutive rate-limit streak and re-evaluate."""
        now = time.monotonic()
        if self._rate_limit_since is None:
            self._rate_limit_since = now
        self._rate_limit_streak += 1
        active = rate_limit_verdict(
            self._rate_limit_streak, self._rate_limit_since, now
        )
        # Log on the transition into "reportable", never on the streak count
        # alone: at the 5 s default interval a streak of 5 is only ~30 s, so
        # logging there would warn that the limit "has persisted" a full 90 s
        # before the integration is willing to say so in the UI, and would
        # re-warn every time a flapping limit re-crossed the count.
        if active and not self._rate_limit_reported:
            _LOGGER.warning(
                "Shelly Cloud rate limit (HTTP 401 max_req) has persisted "
                "for %d consecutive polls over %.0f s; the credentials are "
                "still valid",
                self._rate_limit_streak,
                now - self._rate_limit_since,
            )
        self._rate_limit_reported = active
        async_manage_rate_limit_issue(self.hass, self._entry, active=active)

    def _note_poll_not_rate_limited(self) -> None:
        """Reset the rate-limit streak and clear the issue if raised.

        Clearing on ANY non-rate-limit outcome (success, auth failure,
        transport failure) is deliberate: the issue asserts a *pure
        sustained rate limit*, and a different failure type falsifies that
        claim.
        """
        self._rate_limit_streak = 0
        self._rate_limit_since = None
        if self._rate_limit_reported:
            _LOGGER.info("Shelly Cloud rate limit has cleared")
            self._rate_limit_reported = False
        async_manage_rate_limit_issue(self.hass, self._entry, active=False)

    def _explicit_enabled_ids(self) -> set[str]:
        """Return only the ids the user EXPLICITLY selected in options.

        Deliberately not ``enabled_ids``: that property falls back to
        ``set(self.devices.keys())`` in two branches (create_all_initially,
        and the greenfield no-selection path), which would make the
        difference against the seen set vacuous and silently disable this
        check.
        """
        raw = self._options.get(CONF_ENABLED_DEVICES)
        if isinstance(raw, list):
            return {d for d in raw if isinstance(d, str)}
        return set()

    def _evaluate_missing_devices(self, seen: set[str]) -> None:
        """Track enabled device ids the cloud never reports back.

        Only reachable on a successful poll — the failure branches raise
        before this runs — so the streak counts successful polls.
        """
        enabled = self._explicit_enabled_ids()
        missing = compute_missing_devices(
            enabled, seen, self.create_all_initially
        )
        now = time.monotonic()

        # A device that reported again drops its history entirely, so a
        # device that vanishes later serves a fresh 24 h.
        for did in list(self._missing_since):
            if did not in missing:
                self._missing_since.pop(did, None)
                self._missing_streak.pop(did, None)
        for did in missing:
            self._missing_streak[did] = self._missing_streak.get(did, 0) + 1
            self._missing_since.setdefault(did, now)

        # Only devices that have individually served both gates are named on
        # the card. A newly-vanished device therefore joins an existing card
        # 24 h later without disturbing the ids already on it.
        reportable = {
            did
            for did in missing
            if missing_devices_verdict(
                self._missing_streak.get(did, 0),
                self._missing_since.get(did),
                now,
                {did},
                enabled,
            )
        }
        # The mass-absence guard is judged on the REPORTABLE set, not on the
        # whole missing set. Judging it on ``missing`` would let a transient
        # fleet-wide outage RETRACT a card that individual ids had already
        # earned over 24 h — deleting the issue, which discards the user's
        # "Ignore", and re-creating it un-ignored once the outage passes.
        # That is precisely the delete-then-create loop this design exists to
        # avoid, arriving through a side door.
        #
        # Judging it on ``reportable`` loses nothing: a genuine account-side
        # wipe-out makes every id vanish at the same moment, so they all
        # clear their individual 24 h gates together and land in
        # ``reportable`` together, where the guard still catches them.
        active = bool(reportable) and not is_mass_absence(reportable, enabled)

        # Replacing an already-raised issue with new placeholders is an
        # in-place upsert; it preserves the user's "Ignore". Never delete
        # and re-create here.
        async_manage_missing_devices_issue(
            self.hass,
            self._entry,
            active=active,
            missing=reportable,
            names=self._display_names(reportable),
        )

    def _display_names(self, ids: set[str]) -> dict[str, str]:
        """Best-effort human labels for ``ids``, for the repair card.

        ``device_names`` only ever holds CLOUD ALIASES, and the cloud omits
        an alias for any device the user never renamed in the Shelly app —
        exactly the device most likely to be unrecognisable as a bare hex
        id. Fall back to whatever the HA device registry still knows, since
        the DeviceEntry outlives the device's disappearance from the poll.
        """
        labels: dict[str, str] = {}
        if not ids:
            # Nothing to label — the common case on every healthy poll. Skip
            # the registry lookup entirely rather than building a handle to
            # iterate zero ids.
            return labels
        dev_reg = dr.async_get(self.hass)
        for did in ids:
            if name := self.device_names.get(did):
                labels[did] = name
                continue
            device_entry = dev_reg.async_get_device(identifiers={(DOMAIN, did)})
            if device_entry is None:
                continue
            raw = device_entry.name_by_user or device_entry.name or ""
            # Registry rows are stored as "<label> (<device_id>)"; strip the
            # id so format_device_list does not print it twice.
            suffix = f" ({did})"
            if raw.endswith(suffix):
                raw = raw[: -len(suffix)]
            if raw:
                labels[did] = raw
        return labels

    async def _refresh_device_names(self, ids: list[str]) -> None:
        """Fetch the Shelly-App aliases for ``ids`` and cache them.

        Runs as a background task after ``_async_update_data`` completes so
        it does not delay the coordinator's next tick. Waits
        ``_V2_NAME_LOOKUP_GAP_S`` to stay under the shared 1 req/s rate
        limit, then covers every missing id with a single request to the
        account-wide v1 alias listing. Failures are logged at debug level —
        a missing name is not worth bubbling up as an UpdateFailed.

        Every requested id is recorded in ``_names_attempted`` once the call
        comes back, including the ones the account has no alias for, so a
        never-renamed device is not looked up again on the next poll. A failed
        call records nothing and is therefore retried. (#13)
        """
        try:
            await asyncio.sleep(_V2_NAME_LOOKUP_GAP_S)
            names = await self._api.get_device_names(ids)
        except ShellyCloudAuthError:
            _LOGGER.debug("Device name lookup rejected auth_key — skipping")
            return
        except ShellyCloudError as err:
            _LOGGER.debug("Device name lookup failed: %s", err)
            return
        finally:
            self._name_lookup_in_flight = False

        self._names_attempted.update(ids)

        if not names:
            return

        self.device_names.update(names)
        for did, name in names.items():
            entry = self.devices.get(did)
            if entry is not None:
                entry["name"] = name
        _LOGGER.info("Resolved %d device name(s) from the cloud alias list", len(names))

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

    async def _refresh_virtual_configs(self, ids: list[str]) -> None:
        """Fetch virtual-component config for ``ids`` via the v2 API and cache it.

        Mirrors :meth:`_refresh_device_names`: runs as a background task after
        ``_async_update_data`` completes, then batches every requested id into
        the v2 settings endpoint. The config carries the user-set name, the
        number unit and the enum options that the cloud status omits, so the
        read-only virtual entities can render them.

        Waits ``2 * _V2_NAME_LOOKUP_GAP_S`` before firing — double the
        name-lookup gap — so that on the first poll (when both this task and
        :meth:`_refresh_device_names` are scheduled together) the two v2/v1
        requests do not fire back-to-back and trip the shared 1 req/s budget
        (``401 max_req``). Staggering keeps both under the rate limit instead
        of relying on the request-retry backoff to mask the collision.

        Failures are logged at debug level — a missing config just means the
        entities keep their generic names, not worth an ``UpdateFailed``. Every
        requested id is recorded (with an empty map when it returned no
        virtual-component config) so a device is never re-fetched, exactly like
        cached names. (#9)
        """
        try:
            # Double the gap so this v2 lookup lands after the name lookup
            # rather than alongside it — see the rate-limit note above.
            await asyncio.sleep(2 * _V2_NAME_LOOKUP_GAP_S)
            configs = await self._api.get_device_configs(ids)
        except ShellyCloudAuthError:
            _LOGGER.debug("v2 virtual-component config lookup rejected auth_key — skipping")
            return
        except ShellyCloudError as err:
            _LOGGER.debug("v2 virtual-component config lookup failed: %s", err)
            return
        finally:
            self._vcomp_config_in_flight = False

        # Mark every requested id resolved so it is never re-fetched, seeding
        # an empty map for devices that returned no virtual-component config.
        for did in ids:
            self.virtual_configs.setdefault(did, {})
        if configs:
            self.virtual_configs.update(configs)
        for did in ids:
            entry = self.devices.get(did)
            if entry is not None:
                entry["virtual_config"] = self.virtual_configs.get(did)

        if configs:
            _LOGGER.info(
                "Resolved virtual-component config for %d device(s) via v2 API",
                len(configs),
            )

        # Re-render entities with the real names/units/options straight away.
        self.async_update_listeners()

    # ── Command dispatch (compat shim for platform files) ─────────────

    async def send_command(
        self,
        device_id: str,
        cmd: str,
        channel: int = 0,
        action: Any = "toggle",
        gen2: bool = False,
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
                        device_id, channel=channel, go_to_pos=action, gen2=gen2
                    )
                elif isinstance(action, str):
                    data = await self._api.roller_control(
                        device_id, channel=channel, direction=action, gen2=gen2
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

        # Schedule a *delayed* status refresh. An immediate poll races the
        # optimistic entity state — the cloud may not have propagated the new
        # state yet, so the poll would overwrite it and flicker the UI — and
        # it fires a request right after the command, straining the 1 req/s
        # budget (which Shelly enforces with a 401 rate-limit reply). Waiting
        # a beat lets the cloud reflect the change first. (#6)
        async def _delayed_refresh(_now: Any) -> None:
            await self.async_request_refresh()

        async_call_later(
            self.hass, POST_COMMAND_REFRESH_DELAY, _delayed_refresh
        )
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
            for key in (
                "brightness", "gain", "white", "temp", "red", "green", "blue"
            ):
                if key in action and action[key] is not None:
                    kw[key] = action[key]
            return kw
        return {}
