"""Config flow for Shelly Integrator."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, API_GET_TOKEN, INTEGRATOR_TAG, CONF_INTEGRATOR_TOKEN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_INTEGRATOR_TOKEN): str,
    }
)


class ShellyIntegratorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Shelly Integrator."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
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

                        return self.async_create_entry(
                            title="Shelly Integrator",
                            data=user_input,
                        )

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
