"""Tests for the status snapshot collector (`status.py`).

`collect_status` must produce the shape the Stroompeil HA server's `StatusFrame`
expects, must never raise, and must tolerate missing attributes on `hass`.
"""
from __future__ import annotations

import datetime
import time

import pytest

from custom_components.stroompeil_ha_addon import status


class FakeConfig:
    def __init__(self, *, location_name="Living Room", version="2026.1.0"):
        self.location_name = location_name
        self.version = version


class FakeStates:
    def __init__(self, ids):
        self._ids = ids

    def async_entity_ids(self):
        return self._ids


class FakeHass:
    def __init__(self, *, location_name="Living Room", version="2026.1.0",
                 entity_ids=("light.kitchen", "sensor.temp"), custom_components=None,
                 started_at=None):
        self.config = FakeConfig(location_name=location_name, version=version)
        self.states = FakeStates(entity_ids)
        self.data = {"custom_components": custom_components or {"stroompeil_ha_addon": object()}}
        self.started_at = started_at


@pytest.mark.asyncio
async def test_collect_status_shape():
    payload = await status.collect_status(FakeHass())

    assert payload["msg_type"] == "status"
    assert payload["host_name"] == "Living Room"
    assert payload["ha_version"] == "2026.1.0"
    assert payload["entity_count"] == 2
    assert payload["integrations"] == ["stroompeil_ha_addon"]  # sorted
    assert payload["available_updates"] == []
    assert payload["extra"] == {}
    assert isinstance(payload["uptime_seconds"], int)


@pytest.mark.asyncio
async def test_collect_status_unsorted_integrations_are_sorted():
    hass = FakeHass(custom_components={"zeta": object(), "alpha": object()})

    payload = await status.collect_status(hass)

    assert payload["integrations"] == ["alpha", "zeta"]


@pytest.mark.asyncio
async def test_collect_status_falls_back_to_const_version():
    class NoVersionConfig(FakeConfig):
        def __init__(self):
            super().__init__()
            del self.version

    hass = FakeHass()
    hass.config = NoVersionConfig()

    payload = await status.collect_status(hass)

    # Falls back to homeassistant.const.__version__ injected by conftest.
    assert payload["ha_version"] == "2026.1.0"


@pytest.mark.asyncio
async def test_collect_status_empty_host_name():
    hass = FakeHass(location_name=None)
    payload = await status.collect_status(hass)
    assert payload["host_name"] == ""


def test_uptime_seconds_zero_when_no_started_at():
    assert status._uptime_seconds(FakeHass(started_at=None)) == 0


def test_uptime_seconds_positive_when_started_in_past():
    started = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=100)
    hass = FakeHass(started_at=started)

    uptime = status._uptime_seconds(hass)

    assert uptime >= 99  # allow a little clock slack
    assert uptime <= 110


def test_cpu_load_is_a_float():
    assert isinstance(status._cpu_load(), float)
