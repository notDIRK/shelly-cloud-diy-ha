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
    CONF_POLL_INTERVAL,
    CONF_SERVER_URI,
    DOMAIN,
    POLL_INTERVAL_DEFAULT,
    POLL_INTERVAL_MAX,
    POLL_INTERVAL_MIN,
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
) -> list[SelectOptionDict]:
    """Build multi-select option list: labelled devices, online-first then by name.

    ``devices`` is the raw ``devices_status`` dict from ``/device/all_status``
    (keys are device_ids, values carry at least ``code``, ``_dev_info``, etc.).
    ``names`` maps device_id → user-set name (may be a subset of the devices).
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
    return [SelectOptionDict(value=did, label=label) for _, _, did, label in options]


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
        """Menu: choose a device-selection mode before the picker.

        Three options:
        - ``all``: enable every current device AND auto-enable future
          devices. One click. No picker shown.
        - ``none``: disable every device (polling still runs; HA just
          doesn't materialise entities). One click. No picker shown.
        - ``select``: open the multi-select list pre-ticked with every
          device, so the user can untick individual ones.
        """
        return self.async_show_menu(
            step_id="devices",
            menu_options=["devices_all", "devices_none", "devices_select"],
        )

    async def async_step_devices_all(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Save with every device enabled + future-device auto-enable on."""
        return self._finalize_devices(
            enabled=list(self._pending_devices.keys()),
            create_all=True,
        )

    async def async_step_devices_none(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Save with no device enabled — picker-add them later via options."""
        return self._finalize_devices(enabled=[], create_all=False)

    async def async_step_devices_select(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Multi-select list with every device pre-ticked; untick to exclude."""
        options = _build_device_options(
            self._pending_devices, self._pending_names
        )
        all_ids = [opt["value"] for opt in options]

        if user_input is not None:
            selected = user_input.get(CONF_ENABLED_DEVICES) or []
            if not isinstance(selected, list):
                selected = [selected]
            selected = [d for d in selected if isinstance(d, str)]
            create_all = set(selected) == set(all_ids) and len(all_ids) > 0
            return self._finalize_devices(enabled=selected, create_all=create_all)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ENABLED_DEVICES, default=all_ids
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
            step_id="devices_select",
            data_schema=schema,
            description_placeholders={
                "total": str(len(self._pending_devices)),
            },
        )

    def _finalize_devices(
        self, enabled: list[str], create_all: bool
    ) -> FlowResult:
        """Persist the collected auth + device selection as a config entry."""
        entry_options = dict(self._pending_options)
        entry_options[CONF_CREATE_ALL_INITIALLY] = create_all
        entry_options[CONF_ENABLED_DEVICES] = enabled
        return self.async_create_entry(
            title="Shelly Cloud DIY",
            data=self._pending_data,
            options=entry_options,
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

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_POLL_INTERVAL, default=current_interval
                ): vol.All(int, vol.Range(min=POLL_INTERVAL_MIN, max=POLL_INTERVAL_MAX)),
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
        """Menu mirroring the config flow's choice of selection mode."""
        return self.async_show_menu(
            step_id="devices",
            menu_options=[
                "devices_keep",
                "devices_all",
                "devices_none",
                "devices_select",
            ],
        )

    async def async_step_devices_keep(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Keep the existing enabled_devices selection unchanged."""
        current = self.config_entry.options
        raw_enabled = current.get(CONF_ENABLED_DEVICES)
        enabled = raw_enabled if isinstance(raw_enabled, list) else []
        create_all = bool(current.get(CONF_CREATE_ALL_INITIALLY, False))
        return self._finalize_devices(enabled=enabled, create_all=create_all)

    async def async_step_devices_all(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Enable every currently-visible device + auto-enable future."""
        return self._finalize_devices(
            enabled=list(self._pending_devices.keys()),
            create_all=True,
        )

    async def async_step_devices_none(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Disable every device. Entities are removed on reload."""
        return self._finalize_devices(enabled=[], create_all=False)

    async def async_step_devices_select(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Multi-select list pre-ticked with the current enabled_devices."""
        options = _build_device_options(
            self._pending_devices, self._pending_names
        )
        all_ids = [opt["value"] for opt in options]

        if user_input is not None:
            selected = user_input.get(CONF_ENABLED_DEVICES) or []
            if not isinstance(selected, list):
                selected = [selected]
            selected = [d for d in selected if isinstance(d, str)]
            create_all = set(selected) == set(all_ids) and len(all_ids) > 0
            return self._finalize_devices(enabled=selected, create_all=create_all)

        current_opts = self.config_entry.options
        if current_opts.get(CONF_CREATE_ALL_INITIALLY):
            default_enabled = all_ids
        else:
            raw_enabled = current_opts.get(CONF_ENABLED_DEVICES)
            if isinstance(raw_enabled, list):
                default_enabled = [d for d in raw_enabled if isinstance(d, str)]
            else:
                # Pre-v0.4.0 entry being edited for the first time — default
                # to "all currently visible" so the user sees their fleet
                # pre-ticked instead of empty.
                default_enabled = all_ids

        schema = vol.Schema(
            {
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
            step_id="devices_select",
            data_schema=schema,
            description_placeholders={
                "total": str(len(self._pending_devices)),
            },
        )

    def _finalize_devices(
        self, enabled: list[str], create_all: bool
    ) -> FlowResult:
        """Persist poll/gateway + device selection as options."""
        opts = dict(self._pending_base_options)
        opts[CONF_CREATE_ALL_INITIALLY] = create_all
        opts[CONF_ENABLED_DEVICES] = enabled
        return self._save(opts)

    def _save(self, options: dict[str, Any]) -> FlowResult:
        """Persist options and return an empty-title entry so HA saves them."""
        return self.async_create_entry(title="", data=options)
