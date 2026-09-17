"""Tests for the sensor platform (`sensor.py`).

`StroompeilLastStatusUpdateSensor` is a timestamp sensor that records when the
WS client last handed a status frame to the local TCP stack. The platform
registers the entity into `hass.data[DOMAIN][entry_id]` so `__init__` can hand
it to the WS client; the client then drives it via `set_last_status_update`.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custom_components.stroompeil_ha_addon.const import DOMAIN
from custom_components.stroompeil_ha_addon.sensor import (
    StroompeilLastStatusUpdateSensor,
    async_setup_entry,
)


class FakeEntry:
    def __init__(self, *, entry_id="abc123"):
        self.entry_id = entry_id


class FakeHass:
    def __init__(self):
        self.data = {DOMAIN: {}}


async def _capture_entity(hass, entry) -> StroompeilLastStatusUpdateSensor:
    created: list = []

    def add_entities(entities):
        created.extend(entities)

    await async_setup_entry(hass, entry, add_entities)
    return created[0]


async def test_platform_creates_one_timestamp_sensor():
    hass = FakeHass()
    entry = FakeEntry()

    sensor = await _capture_entity(hass, entry)

    assert isinstance(sensor, StroompeilLastStatusUpdateSensor)
    from homeassistant.components.sensor import SensorDeviceClass

    assert sensor._attr_device_class == SensorDeviceClass.TIMESTAMP
    assert sensor._attr_has_entity_name is True


def test_unique_id_is_scoped_to_entry():
    entry = FakeEntry(entry_id="entry-7")

    sensor = StroompeilLastStatusUpdateSensor(entry)

    assert sensor._attr_unique_id == "entry-7_last_status_update"


def test_name_is_human_readable():
    sensor = StroompeilLastStatusUpdateSensor(FakeEntry())

    assert sensor.name == "Last status update"


def test_native_value_starts_none():
    assert StroompeilLastStatusUpdateSensor(FakeEntry()).native_value is None


async def test_async_added_to_hass_registers_into_entry_data():
    hass = FakeHass()
    entry = FakeEntry(entry_id="e1")
    sensor = await _capture_entity(hass, entry)
    sensor.hass = hass
    hass.data[DOMAIN][entry.entry_id] = {}
    await sensor.async_added_to_hass()

    assert hass.data[DOMAIN][entry.entry_id]["last_status_update_sensor"] is sensor


def test_set_last_status_update_stores_and_writes_state():
    sensor = StroompeilLastStatusUpdateSensor(FakeEntry())

    writes = 0
    orig = sensor.async_write_ha_state

    def counting_write():
        nonlocal writes
        writes += 1
        return orig()

    sensor.async_write_ha_state = counting_write  # type: ignore[assignment]

    ts = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)

    sensor.set_last_status_update(ts)

    assert sensor.native_value == ts
    assert writes == 1


def test_set_last_status_update_with_new_timestamp_overwrites_previous():
    sensor = StroompeilLastStatusUpdateSensor(FakeEntry())

    first = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    second = datetime(2026, 9, 17, 12, 1, tzinfo=timezone.utc)

    sensor.set_last_status_update(first)
    sensor.set_last_status_update(second)

    assert sensor.native_value == second


def test_set_last_status_update_value_is_timezone_aware_utc():
    sensor = StroompeilLastStatusUpdateSensor(FakeEntry())

    sensor.set_last_status_update(datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc))

    value = sensor.native_value
    assert value is not None
    assert value.tzinfo is not None
    assert value.utcoffset().total_seconds() == 0
