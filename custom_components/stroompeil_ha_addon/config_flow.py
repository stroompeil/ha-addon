"""Config flow for the Stroompeil HA addon integration."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries

from .const import CONF_HOST_NAME, CONF_LOCATION, CONF_SERVER_URL, CONF_TOKEN, DOMAIN

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SERVER_URL): str,
        vol.Required(CONF_TOKEN): str,
        vol.Optional(CONF_HOST_NAME, default=""): str,
        vol.Optional(CONF_LOCATION, default=""): str,
    }
)


class StroompeilHAAddonConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_TOKEN])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input.get(CONF_HOST_NAME) or "Stroompeil HA addon",
                data=user_input,
            )
        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)
