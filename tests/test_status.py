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
    def __init__(self, ids, entities=None):
        self._ids = ids
        self._entities = entities or {}

    def async_entity_ids(self, domain=None):
        if domain is None:
            return self._ids
        return [eid for eid in self._entities]

    def get(self, entity_id):
        return self._entities.get(entity_id)


class FakeState:
    def __init__(self, attributes=None):
        self.attributes = attributes or {}


class FakeHass:
    def __init__(self, *, location_name="Living Room", version="2026.1.0",
                 entity_ids=("light.kitchen", "sensor.temp"), custom_components=None,
                 started_at=None, update_entities=None):
        self.config = FakeConfig(location_name=location_name, version=version)
        self.states = FakeStates(entity_ids, update_entities or {})
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
    assert payload["ha_latest_version"] == ""
    assert payload["extra"] == {}
    assert payload["addon_running_version"] == ""
    assert payload["addon_installed_version"] == ""
    assert isinstance(payload["uptime_seconds"], int)


@pytest.mark.asyncio
async def test_collect_status_reports_ha_latest_version_from_core_entity():
    hass = FakeHass(update_entities={
        "update.home_assistant_core_update": FakeState({"latest_version": "2026.9.1"}),
    })
    payload = await status.collect_status(hass)
    assert payload["ha_latest_version"] == "2026.9.1"


@pytest.mark.asyncio
async def test_collect_status_ha_latest_version_empty_without_core_entity():
    hass = FakeHass(update_entities={
        "update.some_hacs_repo_update": FakeState({"latest_version": "1.0.0"}),
    })
    payload = await status.collect_status(hass)
    assert payload["ha_latest_version"] == ""


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


@pytest.mark.asyncio
async def test_addon_versions_from_disk_manifest(tmp_path, monkeypatch):
    import custom_components.stroompeil_ha_addon.status as status_mod
    from custom_components.stroompeil_ha_addon.const import DOMAIN

    comp_dir = tmp_path / "custom_components" / DOMAIN
    comp_dir.mkdir(parents=True)
    (comp_dir / "manifest.json").write_text('{"version": "0.4.0"}')

    class Config:
        config_dir = str(tmp_path)

    class Hass:
        config = Config()

    running, installed = await status_mod._addon_versions(Hass())
    # running stays "" because homeassistant.loader is not installed in tests
    assert running == ""
    assert installed == "0.4.0"


@pytest.mark.asyncio
async def test_addon_versions_running_from_loader(monkeypatch):
    import custom_components.stroompeil_ha_addon.status as status_mod
    from custom_components.stroompeil_ha_addon.const import DOMAIN

    class Integration:
        version = "0.3.0"

    class LoaderMod:
        @staticmethod
        async def async_get_custom_components(hass):
            return {DOMAIN: Integration()}

    import sys
    loader = type(sys)("homeassistant.loader")
    loader.async_get_custom_components = LoaderMod.async_get_custom_components
    monkeypatch.setitem(sys.modules, "homeassistant.loader", loader)

    class Hass:
        pass

    running, installed = await status_mod._addon_versions(Hass())
    assert running == "0.3.0"
    assert installed == ""  # no config_dir on Hass


@pytest.mark.asyncio
async def test_collect_status_carries_critical_logs():
    import datetime

    class FakeLogEntry:
        def __init__(self):
            ts = datetime.datetime(2026, 9, 18, 12, 0, tzinfo=datetime.timezone.utc)
            self.level = "ERROR"
            self.name = "zigbee2mqtt"
            self.message = "Adapter disconnected"
            self.timestamp = ts
            self.first_occurrence = ts
            self.count = 4

    hass = FakeHass()
    hass.data = {
        "custom_components": {"stroompeil_ha_addon": object()},
        "system_log": {"items": [FakeLogEntry()]},
    }
    payload = await status.collect_status(hass)
    assert payload["critical_logs"][0]["logger"] == "zigbee2mqtt"
    assert payload["critical_logs"][0]["count"] == 4


@pytest.mark.asyncio
async def test_collect_status_critical_logs_empty_without_buffer():
    payload = await status.collect_status(FakeHass())
    assert payload["critical_logs"] == []
