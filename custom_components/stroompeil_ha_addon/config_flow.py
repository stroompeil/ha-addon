"""Config flow for the Stroompeil HA addon integration."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries

from .const import (
    CONF_ENVIRONMENT,
    CONF_HOST_NAME,
    CONF_SERVER_URL,
    CONF_TOKEN,
    DOMAIN,
    ENVIRONMENT_TEST,
    ENVIRONMENT_URLS,
)


def _env_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_ENVIRONMENT, default=ENVIRONMENT_TEST): vol.In(
                {ENVIRONMENT_TEST: "Test (fleet-dev.stroompeil.nl)"}
            )
        }
    )


def _token_schema(server_url: str) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_SERVER_URL, default=server_url): str,
            vol.Required(CONF_TOKEN): str,
        }
    )


class StroompeilHAAddonConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None):
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=_env_schema())

        environment = user_input[CONF_ENVIRONMENT]
        server_url = ENVIRONMENT_URLS.get(environment, "")

        self._environment = environment
        return await self.async_step_token()

    async def async_step_token(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            host_name = self.hass.config.location_name or "Home Assistant"
            await self.async_set_unique_id(user_input[CONF_TOKEN])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=host_name,
                data={
                    CONF_ENVIRONMENT: self._environment,
                    CONF_SERVER_URL: user_input[CONF_SERVER_URL],
                    CONF_TOKEN: user_input[CONF_TOKEN],
                    CONF_HOST_NAME: host_name,
                },
            )
        server_url = ENVIRONMENT_URLS.get(getattr(self, "_environment", ENVIRONMENT_TEST), "")
        return self.async_show_form(
            step_id="token",
            data_schema=_token_schema(server_url),
            errors=errors,
        )
