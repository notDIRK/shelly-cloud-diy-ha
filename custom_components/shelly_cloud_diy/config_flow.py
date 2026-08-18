"""Config flow for Shelly Cloud DIY.

User setup is a two-step flow:

1. **auth** — paste ``auth_key`` + ``server URI`` from the Shelly App
   (*User settings → Authorization cloud key*). We validate both by
   hitting ``/device/all_status`` once and cache the snapshot so the
   second step does not need to re-poll.
2. **devices** — offer either "create entities for every device" (one
   checkbox) or a multi-select picker of the devices the account can see,
   labelled with their user-set Shelly-App names where available (fetched
   from the v2 API). This prevents the 275-entity auto-creation that
   happens for users who also run the HA-Core Shelly LAN integration and
   only want cloud-only devices materialised.

Options flow exposes the poll interval, the optional local gateway URL
for the historical-data service, and a mirror of the device-selection
step so users can change their mind later.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api.cloud_control import (
    ShellyCloudAuthError,
    ShellyCloudControl,
    ShellyCloudError,
    ShellyCloudTransportError,
)
from .const import (
    CONF_AUTH_KEY,
    CONF_CREATE_ALL_INITIALLY,
    CONF_ENABLED_DEVICES,
    CONF_LOCAL_GATEWAY_URL,
    CONF_OFFLINE_AFTER,
    CONF_POLL_INTERVAL,
    CONF_RELAY_FAULT_DETECTION,
    CONF_SERVER_URI,
    DOMAIN,
    OFFLINE_AFTER_DEFAULT,
    OFFLINE_AFTER_MAX,
    OFFLINE_AFTER_MIN,
    POLL_INTERVAL_DEFAULT,
    POLL_INTERVAL_MAX,
    POLL_INTERVAL_MIN,
    RELAY_FAULT_DETECTION_DEFAULT,
)
from .entities.descriptions import get_model_name
from .utils import validate_gateway_url

_LOGGER = logging.getLogger(__name__)

# Gap between the /device/all_status call and the v2 name lookup so we
# stay under the shared 1 req/s rate limit.
_V2_NAME_LOOKUP_GAP_S = 1.2

# ── Schemas ────────────────────────────────────────────────────────────

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_AUTH_KEY): str,
        vol.Required(CONF_SERVER_URI): str,
        vol.Optional(CONF_POLL_INTERVAL, default=POLL_INTERVAL_DEFAULT): vol.All(
            int, vol.Range(min=POLL_INTERVAL_MIN, max=POLL_INTERVAL_MAX)
        ),
        vol.Optional(CONF_LOCAL_GATEWAY_URL): str,
    }
)


def _build_device_options(
    devices: dict[str, dict[str, Any]],
    names: dict[str, str],
    keep_ids: list[str] | None = None,
) -> list[SelectOptionDict]:
    """Build multi-select option list: labelled devices, online-first then by name.

    ``devices`` is the raw ``devices_status`` dict from ``/device/all_status``
    (keys are device_ids, values carry at least ``code``, ``_dev_info``, etc.).
    ``names`` maps device_id → user-set name (may be a subset of the devices).

    ``keep_ids`` are device ids the user has already saved. Any of them missing
    from ``devices`` still gets an option, listed last and labelled as
    unavailable. Without this the form becomes unsubmittable: the saved value is
    pre-ticked but is not among the permitted options, so Home Assistant rejects
    the WHOLE form with "value must be one of [...]" — which also blocks adding
    a perfectly fine new device. Note this is not the same as being offline;
    offline devices are still listed (with a ⚠ prefix). A device has to drop out
    of the cloud listing entirely, which happens. (#25)
    """
    options: list[tuple[bool, str, str, str]] = []
    for did, status in devices.items():
        if not isinstance(status, dict):
            continue
        dev_info = status.get("_dev_info") if isinstance(status, dict) else None
        if not isinstance(dev_info, dict):
            dev_info = {}
        code = dev_info.get("code") or status.get("code") or ""
        if "online" in dev_info:
            online = bool(dev_info.get("online"))
        else:
            cloud = status.get("cloud")
            online = bool(cloud.get("connected")) if isinstance(cloud, dict) else False

        name = names.get(did)
        if name:
            label_base = name
        elif code:
            label_base = get_model_name(code)
        else:
            label_base = "Shelly"

        prefix = "" if online else "⚠ "
        label = f"{prefix}{label_base} ({did})"
        options.append((online, (name or label_base).lower(), did, label))

    # Online first (True sorts before False when we invert), then by
    # lower-cased name, then by id.
    options.sort(key=lambda t: (not t[0], t[1], t[2]))
    result = [SelectOptionDict(value=did, label=label) for _, _, did, label in options]

    # Saved devices the cloud is not listing right now, appended last so the
    # normal fleet stays at the top. The label says why they look odd and what
    # to do; keeping them selectable is the whole point — the user can now
    # deliberately drop one instead of being stuck. (#25)
    if keep_ids:
        known = set(devices)
        for did in keep_ids:
            if not isinstance(did, str) or did in known:
                continue
            known.add(did)
            name = names.get(did)
            base = name if name else "Shelly"
            result.append(
                SelectOptionDict(
                    value=did,
                    label=f"⚠ {base} ({did}) — not in the current cloud listing, "
                          f"untick to remove",
                )
            )

    return result


async def _fetch_devices_and_names(
    api: ShellyCloudControl,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Fetch the device list + user-set names while respecting the rate limit.

    Returns (devices_status, name_map). Failures in the v2 name lookup are
    non-fatal; device selection still works without names (device_ids
    remain in the label).
    """
    data = await api.get_all_status()
    devices_status = data.get("devices_status") or {}
    if not isinstance(devices_status, dict):
        devices_status = {}
    if not devices_status:
        return devices_status, {}

    await asyncio.sleep(_V2_NAME_LOOKUP_GAP_S)
    try:
        names = await api.get_device_names(list(devices_status.keys()))
    except ShellyCloudError as err:
        _LOGGER.debug("Config-flow v2 name lookup failed: %s", err)
        names = {}
    return devices_status, names


class ShellyCloudDiyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """User-initiated setup flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow's per-attempt state."""
        # Populated by ``async_step_user`` after successful auth and
        # consumed by ``async_step_devices`` — keeps us from hitting the
        # Cloud API twice for the same setup attempt.
        self._pending_data: dict[str, Any] = {}
        self._pending_options: dict[str, Any] = {}
        self._pending_devices: dict[str, dict[str, Any]] = {}
        self._pending_names: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the handler for the options flow."""
        return ShellyCloudDiyOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the first step — auth + server URI."""
        errors: dict[str, str] = {}

        if user_input is not None:
            auth_key = user_input[CONF_AUTH_KEY].strip()
            server_uri = user_input[CONF_SERVER_URI].strip()
            poll_interval = int(
                user_input.get(CONF_POLL_INTERVAL, POLL_INTERVAL_DEFAULT)
            )
            raw_gw = user_input.get(CONF_LOCAL_GATEWAY_URL) or ""
            safe_gw = ""

            if raw_gw:
                try:
                    safe_gw = validate_gateway_url(raw_gw)
                except ValueError:
                    errors[CONF_LOCAL_GATEWAY_URL] = "invalid_gateway_url"

            if not auth_key:
                errors[CONF_AUTH_KEY] = "required"
            if not server_uri:
                errors[CONF_SERVER_URI] = "required"

            if not errors:
                session = async_get_clientsession(self.hass)
                try:
                    api = ShellyCloudControl(session, server_uri, auth_key)
                    devices, names = await _fetch_devices_and_names(api)
                except ShellyCloudAuthError:
                    errors["base"] = "invalid_auth"
                except ShellyCloudTransportError:
                    errors["base"] = "cannot_connect"
                except ShellyCloudError:
                    _LOGGER.exception("Unexpected API error during validation")
                    errors["base"] = "unknown"
                else:
                    _LOGGER.info(
                        "Shelly Cloud DIY: validated %d device(s) on %s (%d named)",
                        len(devices),
                        server_uri,
                        len(names),
                    )

                    # Tie the entry to the server URI so the user cannot
                    # accidentally add two entries for the same account.
                    await self.async_set_unique_id(server_uri)
                    self._abort_if_unique_id_configured()

                    self._pending_data = {
                        CONF_AUTH_KEY: auth_key,
                        CONF_SERVER_URI: server_uri,
                    }
                    self._pending_options = {
                        CONF_POLL_INTERVAL: poll_interval,
                    }
                    if safe_gw:
                        self._pending_options[CONF_LOCAL_GATEWAY_URL] = safe_gw
                    self._pending_devices = devices
                    self._pending_names = names

                    return await self.async_step_devices()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Single form: bulk action radio + per-device multi-select list.

        UX: the user can either tick/untick individual devices in the
        list and submit, or use the bulk action radio as a shortcut.
        Picking ``all`` or ``none`` and clicking *Submit* **re-renders**
        the form with the list live-updated (all ticked or all unticked)
        and the radio reset to ``manual``. The user then verifies /
        fine-tunes and clicks Submit a second time to actually save —
        only a ``manual`` submit persists the entry.
        """
        options = _build_device_options(
            self._pending_devices, self._pending_names
        )
        all_ids = [opt["value"] for opt in options]

        if user_input is not None:
            action = user_input.get("bulk_action", "manual")

            if action == "all":
                return self._show_devices_form(
                    options, all_ids, default_enabled=all_ids, bulk_applied=True
                )
            if action == "none":
                return self._show_devices_form(
                    options, all_ids, default_enabled=[], bulk_applied=True
                )

            # action == "manual" → persist whatever is currently ticked
            raw = user_input.get(CONF_ENABLED_DEVICES) or []
            if not isinstance(raw, list):
                raw = [raw]
            selected = [d for d in raw if isinstance(d, str)]

            create_all = set(selected) == set(all_ids) and len(all_ids) > 0

            entry_options = dict(self._pending_options)
            entry_options[CONF_CREATE_ALL_INITIALLY] = create_all
            entry_options[CONF_ENABLED_DEVICES] = selected

            return self.async_create_entry(
                title="Shelly Cloud DIY",
                data=self._pending_data,
                options=entry_options,
            )

        # Initial render — every device pre-ticked.
        return self._show_devices_form(options, all_ids, default_enabled=all_ids)


    async def async_step_devices_bulk(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the form a bulk action re-rendered.

        A separate step id exists only so that form can carry its own
        translated title and text; what the user submits from it means
        exactly what it means on ``devices``, so it is handled there.
        """
        return await self.async_step_devices(user_input)

    def _show_devices_form(
        self,
        options: list[SelectOptionDict],
        all_ids: list[str],
        default_enabled: list[str],
        *,
        bulk_applied: bool = False,
    ) -> FlowResult:
        """Render the device-picker form with the given default ticks.

        ``bulk_applied`` re-renders under a second step id. The form is
        otherwise identical — the point is the text: a bulk action only
        updates the ticks and saves nothing, and the previous single-step
        version gave no sign of that. It redrew a form that looked exactly
        like the one just submitted, which reads as "saved" and cost a real
        user their selection.
        """
        schema = vol.Schema(
            {
                vol.Required(
                    "bulk_action", default="manual"
                ): SelectSelector(
                    SelectSelectorConfig(
                        # Labels come from the ``selector.bulk_action.options``
                        # block in strings.json / translations. Hard-coded
                        # ``label=`` values bypass translation entirely and
                        # show the same text in every language (#18).
                        options=["manual", "all", "none"],
                        mode=SelectSelectorMode.LIST,
                        translation_key="bulk_action",
                    )
                ),
                vol.Optional(
                    CONF_ENABLED_DEVICES, default=default_enabled
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="devices_bulk" if bulk_applied else "devices",
            data_schema=schema,
            description_placeholders={
                "total": str(len(self._pending_devices)),
                # Makes the current state visible on both forms: a picker
                # that shows neither what is ticked nor whether it is saved
                # leaves the user guessing on every submit.
                "selected": str(len(default_enabled)),
            },
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> FlowResult:
        """HA triggers this when ConfigEntryAuthFailed is raised."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Re-ask for the auth_key only; server URI stays as-is."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="reauth_entry_missing")

        if user_input is not None:
            auth_key = user_input[CONF_AUTH_KEY].strip()
            if not auth_key:
                errors[CONF_AUTH_KEY] = "required"
            else:
                session = async_get_clientsession(self.hass)
                try:
                    api = ShellyCloudControl(
                        session, entry.data[CONF_SERVER_URI], auth_key
                    )
                    await api.validate()
                except ShellyCloudAuthError:
                    errors["base"] = "invalid_auth"
                except ShellyCloudTransportError:
                    errors["base"] = "cannot_connect"
                except ShellyCloudError:
                    _LOGGER.exception("Unexpected API error during reauth")
                    errors["base"] = "unknown"
                else:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={**entry.data, CONF_AUTH_KEY: auth_key},
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_AUTH_KEY): str}),
            errors=errors,
        )


class ShellyCloudDiyOptionsFlow(OptionsFlow):
    """Options flow — poll interval, local gateway URL, and device selection."""

    def __init__(self) -> None:
        self._pending_devices: dict[str, dict[str, Any]] = {}
        self._pending_names: dict[str, str] = {}
        self._pending_base_options: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """First step — poll interval + local gateway URL."""
        errors: dict[str, str] = {}

        if user_input is not None:
            raw_gw = user_input.get(CONF_LOCAL_GATEWAY_URL, "")
            safe_gw = ""
            if raw_gw:
                try:
                    safe_gw = validate_gateway_url(raw_gw)
                except ValueError:
                    errors[CONF_LOCAL_GATEWAY_URL] = "invalid_gateway_url"

            if not errors:
                self._pending_base_options = {
                    CONF_POLL_INTERVAL: int(
                        user_input.get(CONF_POLL_INTERVAL, POLL_INTERVAL_DEFAULT)
                    ),
                    CONF_LOCAL_GATEWAY_URL: safe_gw,
                    CONF_OFFLINE_AFTER: int(
                        user_input.get(CONF_OFFLINE_AFTER, OFFLINE_AFTER_DEFAULT)
                    ),
                    CONF_RELAY_FAULT_DETECTION: bool(
                        user_input.get(
                            CONF_RELAY_FAULT_DETECTION,
                            RELAY_FAULT_DETECTION_DEFAULT,
                        )
                    ),
                }

                # Fetch the current fleet + names so the device-selection
                # step can present up-to-date labels. Errors here are not
                # fatal — we fall back to skipping the step and preserving
                # the previously-saved selection.
                session = async_get_clientsession(self.hass)
                api = ShellyCloudControl(
                    session,
                    self.config_entry.data[CONF_SERVER_URI],
                    self.config_entry.data[CONF_AUTH_KEY],
                )
                try:
                    devices, names = await _fetch_devices_and_names(api)
                except ShellyCloudError as err:
                    _LOGGER.warning(
                        "Options flow: skipped device refresh (%s); "
                        "device selection stays as previously saved.",
                        err,
                    )
                    return self._save(self._pending_base_options)

                self._pending_devices = devices
                self._pending_names = names
                return await self.async_step_devices()

        current_interval = int(
            self.config_entry.options.get(CONF_POLL_INTERVAL, POLL_INTERVAL_DEFAULT)
        )
        current_gw = self.config_entry.options.get(CONF_LOCAL_GATEWAY_URL, "")
        current_offline = int(
            self.config_entry.options.get(CONF_OFFLINE_AFTER, OFFLINE_AFTER_DEFAULT)
        )
        current_relay_fault = bool(
            self.config_entry.options.get(
                CONF_RELAY_FAULT_DETECTION, RELAY_FAULT_DETECTION_DEFAULT
            )
        )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_POLL_INTERVAL, default=current_interval
                ): vol.All(int, vol.Range(min=POLL_INTERVAL_MIN, max=POLL_INTERVAL_MAX)),
                vol.Required(
                    CONF_OFFLINE_AFTER, default=current_offline
                ): vol.All(
                    int, vol.Range(min=OFFLINE_AFTER_MIN, max=OFFLINE_AFTER_MAX)
                ),
                vol.Required(
                    CONF_RELAY_FAULT_DETECTION, default=current_relay_fault
                ): bool,
                vol.Optional(
                    CONF_LOCAL_GATEWAY_URL, default=current_gw
                ): str,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Single form mirroring the config flow's bulk-action + list UX."""
        current_opts = self.config_entry.options

        # Read the saved selection BEFORE building the options, so devices the
        # cloud is not listing right now survive as choices instead of making
        # the form unsubmittable. (#25)
        raw_enabled = current_opts.get(CONF_ENABLED_DEVICES)
        saved_ids = (
            [d for d in raw_enabled if isinstance(d, str)]
            if isinstance(raw_enabled, list)
            else []
        )

        options = _build_device_options(
            self._pending_devices, self._pending_names, keep_ids=saved_ids
        )
        all_ids = [opt["value"] for opt in options]

        if user_input is not None:
            action = user_input.get("bulk_action", "manual")

            if action == "all":
                return self._show_devices_form(
                    options, all_ids, default_enabled=all_ids, bulk_applied=True
                )
            if action == "none":
                return self._show_devices_form(
                    options, all_ids, default_enabled=[], bulk_applied=True
                )

            raw = user_input.get(CONF_ENABLED_DEVICES) or []
            if not isinstance(raw, list):
                raw = [raw]
            selected = [d for d in raw if isinstance(d, str)]

            create_all = set(selected) == set(all_ids) and len(all_ids) > 0

            opts = dict(self._pending_base_options)
            opts[CONF_CREATE_ALL_INITIALLY] = create_all
            opts[CONF_ENABLED_DEVICES] = selected
            return self._save(opts)

        if current_opts.get(CONF_CREATE_ALL_INITIALLY):
            default_enabled = all_ids
        else:
            if isinstance(raw_enabled, list):
                default_enabled = saved_ids
            else:
                # Pre-v0.4.0 entry being edited for the first time — default
                # to "all currently visible" so the user sees their fleet
                # pre-ticked instead of empty.
                default_enabled = all_ids

        return self._show_devices_form(options, all_ids, default_enabled=default_enabled)


    async def async_step_devices_bulk(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the form a bulk action re-rendered.

        A separate step id exists only so that form can carry its own
        translated title and text; what the user submits from it means
        exactly what it means on ``devices``, so it is handled there.
        """
        return await self.async_step_devices(user_input)

    def _show_devices_form(
        self,
        options: list[SelectOptionDict],
        all_ids: list[str],
        default_enabled: list[str],
        *,
        bulk_applied: bool = False,
    ) -> FlowResult:
        """Render the device-picker form with the given default ticks.

        ``bulk_applied`` re-renders under a second step id. The form is
        otherwise identical — the point is the text: a bulk action only
        updates the ticks and saves nothing, and the previous single-step
        version gave no sign of that. It redrew a form that looked exactly
        like the one just submitted, which reads as "saved" and cost a real
        user their selection.
        """
        schema = vol.Schema(
            {
                vol.Required(
                    "bulk_action", default="manual"
                ): SelectSelector(
                    SelectSelectorConfig(
                        # Labels come from the ``selector.bulk_action.options``
                        # block in strings.json / translations. Hard-coded
                        # ``label=`` values bypass translation entirely and
                        # show the same text in every language (#18).
                        options=["manual", "all", "none"],
                        mode=SelectSelectorMode.LIST,
                        translation_key="bulk_action",
                    )
                ),
                vol.Optional(
                    CONF_ENABLED_DEVICES, default=default_enabled
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="devices_bulk" if bulk_applied else "devices",
            data_schema=schema,
            description_placeholders={
                "total": str(len(self._pending_devices)),
                # Makes the current state visible on both forms: a picker
                # that shows neither what is ticked nor whether it is saved
                # leaves the user guessing on every submit.
                "selected": str(len(default_enabled)),
            },
        )

    def _save(self, options: dict[str, Any]) -> FlowResult:
        """Persist options and return an empty-title entry so HA saves them."""
        return self.async_create_entry(title="", data=options)
