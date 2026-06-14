"""Config flow for Salus integration."""
import logging

import voluptuous as vol
from homeassistant import config_entries

from . import DOMAIN, DEFAULT_NAME, CONF_NAME
from .auth import login
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD

_LOGGER = logging.getLogger(__name__)


class SalusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Salus."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
            }
        )

        if user_input is not None:
            # Use username as the unique id (account-scoped integration)
            await self.async_set_unique_id(user_input[CONF_USERNAME])
            self._abort_if_unique_id_configured()

            # Validate credentials before creating entry
            from homeassistant.helpers.aiohttp_client import async_get_clientsession

            session = async_get_clientsession(self.hass)
            ok, error_msg = await login(
                session,
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
            )

            if not ok:
                errors["base"] = "login_failed"
                _LOGGER.warning("Salus IT500 config flow login failed: %s", error_msg)
                return self.async_show_form(
                    step_id="user",
                    data_schema=schema,
                    errors=errors,
                    description_placeholders={"error_detail": error_msg},
                )
            else:
                return self.async_create_entry(
                    title=user_input.get(CONF_NAME, DEFAULT_NAME),
                    data={
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
