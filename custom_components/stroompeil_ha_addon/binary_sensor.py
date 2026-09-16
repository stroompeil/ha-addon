"""Binary sensor platform for the Stroompeil HA addon."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([StroompeilConnectionSensor(entry)])


class StroompeilConnectionSensor(BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_is_on = False
        self._attr_unique_id = f"{entry.entry_id}_connected"

    @property
    def name(self) -> str:
        return "Connected"

    async def async_added_to_hass(self) -> None:
        domain_data = self.hass.data.get(DOMAIN, {})
        entry_data = domain_data.get(self._entry.entry_id)
        if entry_data is not None:
            entry_data["sensor"] = self

    def set_connected(self, connected: bool) -> None:
        if self._attr_is_on != connected:
            self._attr_is_on = connected
            self.async_write_ha_state()
