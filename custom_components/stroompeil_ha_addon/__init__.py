"""Stroompeil HA addon custom integration.

Each Home Assistant host running this integration is a client of the Stroompeil HA
server's WebSocket.
"""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_SERVER_URL, CONF_TOKEN, DOMAIN
from .ws_client import StroompeilHAAddonWSClient

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({})}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    from .ws_client import _delete_unregistered_issue

    _delete_unregistered_issue(hass, entry.entry_id)

    server_url = entry.data[CONF_SERVER_URL]
    token = entry.data[CONF_TOKEN]
    client = StroompeilHAAddonWSClient(hass, server_url, token, entry=entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"client": client, "task": None}
    await hass.config_entries.async_forward_entry_setups(entry, ["binary_sensor", "sensor"])
    entry_data = hass.data[DOMAIN][entry.entry_id]
    if "sensor" in entry_data:
        client.set_sensor(entry_data["sensor"])
    if "last_status_update_sensor" in entry_data:
        client.set_last_status_update_sensor(entry_data["last_status_update_sensor"])
    task = hass.async_create_background_task(client.run(), "stroompeil_ha_addon_ws")
    entry_data["task"] = task
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_unload_platforms(entry, ["binary_sensor", "sensor"])
    data = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if data is not None:
        await data["client"].stop()
        data["task"].cancel()
    return True
