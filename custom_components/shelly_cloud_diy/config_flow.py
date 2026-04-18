"""Config flow for Shelly Cloud DIY.

Milestone 1 UX: the user pastes the ``auth_key`` and the per-account
``server URI`` from the Shelly App (*User settings → Authorization cloud
key*). We validate both by hitting ``/device/all_status`` once and count
the devices the account can see.

Options flow exposes the poll interval (3–60 s) and an optional local
gateway URL for the historical-data service.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api.cloud_control import (
    ShellyCloudAuthError,
    ShellyCloudControl,
    ShellyCloudError,
    ShellyCloudTransportError,
)
from .const import (
    CONF_AUTH_KEY,
    CONF_LOCAL_GATEWAY_URL,
    CONF_POLL_INTERVAL,
    CONF_SERVER_URI,
    DOMAIN,
    POLL_INTERVAL_DEFAULT,
    POLL_INTERVAL_MAX,
    POLL_INTERVAL_MIN,
)
from .utils import validate_gateway_url

_LOGGER = logging.getLogger(__name__)

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


class ShellyCloudDiyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """User-initiated setup flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the handler for the options flow."""
        return ShellyCloudDiyOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the single-step user form."""
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
                    device_count = await api.validate()
                except ShellyCloudAuthError:
                    errors["base"] = "invalid_auth"
                except ShellyCloudTransportError:
                    errors["base"] = "cannot_connect"
                except ShellyCloudError:
                    _LOGGER.exception("Unexpected API error during validation")
                    errors["base"] = "unknown"
                else:
                    _LOGGER.info(
                        "Shelly Cloud DIY: validated %d device(s) on %s",
                        device_count,
                        server_uri,
                    )

                    # Tie the entry to the server URI so the user cannot
                    # accidentally add two entries for the same account.
                    await self.async_set_unique_id(server_uri)
                    self._abort_if_unique_id_configured()

                    options: dict[str, Any] = {
                        CONF_POLL_INTERVAL: poll_interval,
                    }
                    if safe_gw:
                        options[CONF_LOCAL_GATEWAY_URL] = safe_gw

                    return self.async_create_entry(
                        title="Shelly Cloud DIY",
                        data={
                            CONF_AUTH_KEY: auth_key,
                            CONF_SERVER_URI: server_uri,
                        },
                        options=options,
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
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
    """Options flow — poll interval and optional local gateway URL."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Single-step options form."""
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
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_POLL_INTERVAL: int(
                            user_input.get(CONF_POLL_INTERVAL, POLL_INTERVAL_DEFAULT)
                        ),
                        CONF_LOCAL_GATEWAY_URL: safe_gw,
                    },
                )

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
