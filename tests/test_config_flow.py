"""Tests for the config flow (`config_flow.py`).

The flow has two steps (environment -> token) and a reconfigure step. These
tests drive the flow with the stub `ConfigFlow` base from conftest, which
captures `async_show_form`/`async_create_entry` results as plain dicts.
"""
from __future__ import annotations

import voluptuous as vol

from custom_components.stroompeil_ha_addon.config_flow import (
    StroompeilHAAddonConfigFlow,
    _env_schema,
    _token_schema,
)
from custom_components.stroompeil_ha_addon.const import (
    CONF_ENVIRONMENT,
    CONF_HOST_NAME,
    CONF_SERVER_URL,
    CONF_TOKEN,
    DOMAIN,
    ENVIRONMENT_TEST,
    ENVIRONMENT_URLS,
)


class FakeLocationConfig:
    def __init__(self, location_name="Home Assistant"):
        self.location_name = location_name


class FakeHass:
    def __init__(self, location_name="Home Assistant"):
        self.config = FakeLocationConfig(location_name)


def _make_flow(*, location_name="Home Assistant") -> StroompeilHAAddonConfigFlow:
    flow = StroompeilHAAddonConfigFlow()
    flow.hass = FakeHass(location_name)
    return flow


def _marker(schema, key):
    """Return the voluptuous marker object for a given key (by schema name)."""
    for marker in schema.schema:
        if marker.schema == key:
            return marker
    raise KeyError(key)


def _validator(schema, key):
    """Return the validator applied to a given key in the schema."""
    marker = _marker(schema, key)
    return schema.schema[marker]


def test_env_schema_defaults_to_test_environment():
    schema = _env_schema()
    marker = _marker(schema, CONF_ENVIRONMENT)

    assert marker.default() == ENVIRONMENT_TEST


def test_env_schema_only_offers_test_environment():
    schema = _env_schema()
    validator = _validator(schema, CONF_ENVIRONMENT)

    assert ENVIRONMENT_TEST in validator.container


def test_token_schema_requires_url_and_token():
    schema = _token_schema("wss://fleet-dev.stroompeil.nl")
    data = schema({CONF_SERVER_URL: "wss://example", CONF_TOKEN: "abc"})

    assert data[CONF_SERVER_URL] == "wss://example"
    assert data[CONF_TOKEN] == "abc"


def test_token_schema_defaults_server_url():
    schema = _token_schema("wss://fleet-dev.stroompeil.nl")
    marker = _marker(schema, CONF_SERVER_URL)

    assert marker.default() == "wss://fleet-dev.stroompeil.nl"


async def test_async_step_user_shows_form_when_no_input():
    flow = _make_flow()

    result = await flow.async_step_user(None)

    assert result["type"] == "form"
    assert result["step_id"] == "user"


async def test_async_step_user_advances_to_token_step():
    flow = _make_flow()

    result = await flow.async_step_user({CONF_ENVIRONMENT: ENVIRONMENT_TEST})

    assert flow._environment == ENVIRONMENT_TEST
    # async_step_user delegates to async_step_token, which (stub) shows the form.
    assert result["type"] == "form"
    assert result["step_id"] == "token"


async def test_async_step_token_creates_entry_with_host_name():
    flow = _make_flow(location_name="Basement Hub")
    flow._environment = ENVIRONMENT_TEST

    result = await flow.async_step_token(
        {CONF_SERVER_URL: "wss://fleet-dev.stroompeil.nl", CONF_TOKEN: "tok-1"}
    )

    assert result["type"] == "create_entry"
    assert result["title"] == "Basement Hub"
    assert result["data"] == {
        CONF_ENVIRONMENT: ENVIRONMENT_TEST,
        CONF_SERVER_URL: "wss://fleet-dev.stroompeil.nl",
        CONF_TOKEN: "tok-1",
        CONF_HOST_NAME: "Basement Hub",
    }


async def test_async_step_token_falls_back_to_default_host_name():
    flow = _make_flow(location_name=None)
    flow._environment = ENVIRONMENT_TEST

    result = await flow.async_step_token(
        {CONF_SERVER_URL: "wss://fleet-dev.stroompeil.nl", CONF_TOKEN: "tok-2"}
    )

    assert result["title"] == "Home Assistant"
    assert result["data"][CONF_HOST_NAME] == "Home Assistant"


async def test_async_step_token_shows_form_when_no_input():
    flow = _make_flow()
    flow._environment = ENVIRONMENT_TEST

    result = await flow.async_step_token(None)

    assert result["type"] == "form"
    assert result["step_id"] == "token"
    assert result["errors"] == {}


async def test_async_step_token_uses_test_url_default_when_no_environment():
    flow = _make_flow()  # _environment not set

    result = await flow.async_step_token(None)

    marker = _marker(result["data_schema"], CONF_SERVER_URL)
    assert marker.default() == ENVIRONMENT_URLS[ENVIRONMENT_TEST]


async def test_reconfigure_shows_form_without_input():
    flow = _make_flow()

    result = await flow.async_step_reconfigure(None)

    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure"
