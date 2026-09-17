"""Sensor platform for the Stroompeil HA addon."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([StroompeilLastStatusUpdateSensor(entry)])


class StroompeilLastStatusUpdateSensor(SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_native_value: datetime | None = None
        self._attr_unique_id = f"{entry.entry_id}_last_status_update"

    @property
    def name(self) -> str:
        return "Last status update"

    @property
    def native_value(self) -> StateType:
        return self._attr_native_value

    async def async_added_to_hass(self) -> None:
        domain_data = self.hass.data.get(DOMAIN, {})
        entry_data = domain_data.get(self._entry.entry_id)
        if entry_data is not None:
            entry_data["last_status_update_sensor"] = self

    def set_last_status_update(self, timestamp: datetime) -> None:
        self._attr_native_value = timestamp
        self.async_write_ha_state()
