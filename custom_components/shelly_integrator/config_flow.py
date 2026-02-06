"""Config flow for Shelly Integrator."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import get_url

from .const import (
    DOMAIN,
    API_GET_TOKEN,
    INTEGRATOR_TAG,
    CONF_INTEGRATOR_TOKEN,
    CONF_LOCAL_GATEWAY_URL,
    SHELLY_CONSENT_URL,
    WEBHOOK_ID,
)
from .core.consent import build_consent_url

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_INTEGRATOR_TOKEN): str,
        vol.Optional(CONF_LOCAL_GATEWAY_URL): str,
    }
)


class ShellyIntegratorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Shelly Integrator."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._token: str | None = None
        self._gateway_url: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return ShellyIntegratorOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - token input."""
        errors: dict[str, str] = {}

        if user_input is not None:
            token = user_input[CONF_INTEGRATOR_TOKEN]

            # Validate credentials by trying to get JWT
            try:
                session = async_get_clientsession(self.hass)
                async with session.post(
                    API_GET_TOKEN,
                    data={"itg": INTEGRATOR_TAG, "token": token},
                ) as response:
                    data = await response.json()

                    if not data.get("isok"):
                        errors["base"] = "invalid_auth"
                    else:
                        # Only allow one instance of this integration
                        await self.async_set_unique_id(INTEGRATOR_TAG)
                        self._abort_if_unique_id_configured()

                        # Store token and gateway URL, proceed to consent step
                        self._token = token
                        self._gateway_url = user_input.get(CONF_LOCAL_GATEWAY_URL)
                        return await self.async_step_consent()

            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_consent(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the consent step - show link to grant device access."""
        if user_input is not None:
            # User clicked submit, create the entry
            # Store gateway URL in options if provided (from step 1)
            options = {}
            if self._gateway_url:
                options[CONF_LOCAL_GATEWAY_URL] = self._gateway_url

            return self.async_create_entry(
                title="Shelly Integrator",
                data={CONF_INTEGRATOR_TOKEN: self._token},
                options=options,
            )

        # Build consent URL
        consent_url = self._build_consent_url()

        return self.async_show_form(
            step_id="consent",
            data_schema=vol.Schema({}),
            description_placeholders={"consent_url": consent_url},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfiguration of the integration."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])

        if user_input is not None:
            token = user_input[CONF_INTEGRATOR_TOKEN]

            # Validate token
            try:
                session = async_get_clientsession(self.hass)
                async with session.post(
                    API_GET_TOKEN,
                    data={"itg": INTEGRATOR_TAG, "token": token},
                ) as response:
                    data = await response.json()

                    if not data.get("isok"):
                        errors["base"] = "invalid_auth"
                    else:
                        # Update entry with new data
                        new_data = {CONF_INTEGRATOR_TOKEN: token}
                        new_options = {
                            CONF_LOCAL_GATEWAY_URL: user_input.get(
                                CONF_LOCAL_GATEWAY_URL, ""
                            ),
                        }
                        self.hass.config_entries.async_update_entry(
                            entry, data=new_data, options=new_options
                        )
                        await self.hass.config_entries.async_reload(entry.entry_id)
                        return self.async_abort(reason="reconfigure_successful")

            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        # Get current values
        current_token = entry.data.get(CONF_INTEGRATOR_TOKEN, "") if entry else ""
        current_gateway = entry.options.get(CONF_LOCAL_GATEWAY_URL, "") if entry else ""

        # Build consent URL
        consent_url = self._build_consent_url()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Required(CONF_INTEGRATOR_TOKEN, default=current_token): str,
                vol.Optional(CONF_LOCAL_GATEWAY_URL, default=current_gateway): str,
            }),
            errors=errors,
            description_placeholders={"consent_url": consent_url},
        )

    def _build_consent_url(self) -> str:
        """Build the Shelly consent URL."""
        return _safe_build_consent_url(self.hass)


class ShellyIntegratorOptionsFlow(OptionsFlow):
    """Handle options flow for Shelly Integrator."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # If token is provided, validate it
            new_token = user_input.get(CONF_INTEGRATOR_TOKEN)
            if new_token:
                try:
                    session = async_get_clientsession(self.hass)
                    async with session.post(
                        API_GET_TOKEN,
                        data={"itg": INTEGRATOR_TAG, "token": new_token},
                    ) as response:
                        data = await response.json()
                        if not data.get("isok"):
                            errors["base"] = "invalid_auth"
                except aiohttp.ClientError:
                    errors["base"] = "cannot_connect"

            if not errors:
                # Update token in data if changed
                if new_token:
                    new_data = {**self.config_entry.data, CONF_INTEGRATOR_TOKEN: new_token}
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=new_data
                    )
                
                # Save options (gateway URL)
                options = {
                    CONF_LOCAL_GATEWAY_URL: user_input.get(CONF_LOCAL_GATEWAY_URL, ""),
                }
                return self.async_create_entry(title="", data=options)

        # Build consent URL
        consent_url = self._build_consent_url()
        
        # Get current values
        current_gateway = self.config_entry.options.get(CONF_LOCAL_GATEWAY_URL, "")

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_INTEGRATOR_TOKEN,
                    description={"suggested_value": ""},
                ): str,
                vol.Optional(
                    CONF_LOCAL_GATEWAY_URL,
                    default=current_gateway,
                ): str,
            }),
            errors=errors,
            description_placeholders={"consent_url": consent_url},
        )

    def _build_consent_url(self) -> str:
        """Build the Shelly consent URL."""
        return _safe_build_consent_url(self.hass)


def _safe_build_consent_url(hass) -> str:
    """Build consent URL with fallback on error."""
    try:
        ha_url = get_url(hass, prefer_external=True)
        return build_consent_url(INTEGRATOR_TAG, ha_url, WEBHOOK_ID)
    except Exception as err:
        _LOGGER.warning("Could not build consent URL: %s", err)
        return f"{SHELLY_CONSENT_URL}?itg={INTEGRATOR_TAG}"
