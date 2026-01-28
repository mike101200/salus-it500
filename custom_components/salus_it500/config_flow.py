"""Config flow for Salus integration."""
import logging
import voluptuous as vol
from homeassistant import config_entries

from . import DOMAIN, DEFAULT_NAME, CONF_NAME
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD

_LOGGER = logging.getLogger(__name__)


class SalusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Salus."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            # Use username as the unique id (account-scoped integration)
            await self.async_set_unique_id(user_input[CONF_USERNAME])
            self._abort_if_unique_id_configured()

            # Skip long-running validation here; create the config entry immediately.
            return self.async_create_entry(
                title=user_input.get(CONF_NAME, DEFAULT_NAME),
                data={
                    CONF_USERNAME: user_input[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                },
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
