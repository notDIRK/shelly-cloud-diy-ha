"""Config flow for Shelly Integrator."""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus

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

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_INTEGRATOR_TOKEN): str,
    }
)


class ShellyIntegratorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Shelly Integrator."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._token: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler."""
        return ShellyIntegratorOptionsFlow(config_entry)

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

                        # Store token and proceed to consent step
                        self._token = token
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
            return self.async_create_entry(
                title="Shelly Integrator",
                data={CONF_INTEGRATOR_TOKEN: self._token},
            )

        # Build consent URL
        consent_url = self._build_consent_url()

        return self.async_show_form(
            step_id="consent",
            data_schema=vol.Schema({}),
            description_placeholders={"consent_url": consent_url},
        )

    def _build_consent_url(self) -> str:
        """Build the Shelly consent URL."""
        try:
            ha_url = get_url(self.hass, prefer_external=True)
            # Use static webhook ID since we only allow one instance
            webhook_url = f"{ha_url}/api/webhook/{WEBHOOK_ID}"
            encoded_callback = quote_plus(webhook_url)
            return f"{SHELLY_CONSENT_URL}?itg={INTEGRATOR_TAG}&cb={encoded_callback}"
        except Exception as err:
            _LOGGER.warning("Could not build consent URL: %s", err)
            return f"{SHELLY_CONSENT_URL}?itg={INTEGRATOR_TAG}"


class ShellyIntegratorOptionsFlow(OptionsFlow):
    """Handle options flow for Shelly Integrator."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Build consent URL
        consent_url = self._build_consent_url()
        
        # Get current options
        current_gateway = self.config_entry.options.get(CONF_LOCAL_GATEWAY_URL, "")

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_LOCAL_GATEWAY_URL,
                    default=current_gateway,
                ): str,
            }),
            description_placeholders={"consent_url": consent_url},
        )

    def _build_consent_url(self) -> str:
        """Build the Shelly consent URL."""
        try:
            ha_url = get_url(self.hass, prefer_external=True)
            # Use static webhook ID since we only allow one instance
            webhook_url = f"{ha_url}/api/webhook/{WEBHOOK_ID}"
            encoded_callback = quote_plus(webhook_url)
            return f"{SHELLY_CONSENT_URL}?itg={INTEGRATOR_TAG}&cb={encoded_callback}"
        except Exception as err:
            _LOGGER.warning("Could not build consent URL: %s", err)
            return f"{SHELLY_CONSENT_URL}?itg={INTEGRATOR_TAG}"
