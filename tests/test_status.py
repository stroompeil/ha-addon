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
    assert payload["cpu_count"] >= 0
    assert isinstance(payload["load_avg_1m"], float)
    assert payload["cpu_percent"] is None  # first frame has no delta yet
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


def test_cpu_percent_none_on_first_sample(monkeypatch, tmp_path):
    stat = tmp_path / "stat"
    stat.write_text("cpu  100 0 100 700 0 0 0\n")
    monkeypatch.setattr(status, "_STAT_PATH", str(stat))
    status._last_cpu_times = None
    assert status._cpu_percent() is None


def test_cpu_percent_delta_between_samples(monkeypatch, tmp_path):
    stat = tmp_path / "stat"
    monkeypatch.setattr(status, "_STAT_PATH", str(stat))
    status._last_cpu_times = None
    stat.write_text("cpu  100 0 100 700 0 0 0\n")
    assert status._cpu_percent() is None
    stat.write_text("cpu  200 0 100 1400 0 0 0\n")
    # s1: total 900, idle 700; s2: total 1700, idle 1400
    # Δtotal 800, Δidle 700 → 12.5% busy
    assert status._cpu_percent() == 12.5


def test_cpu_percent_none_without_proc_stat(monkeypatch):
    monkeypatch.setattr(status, "_STAT_PATH", "/nonexistent/stat")
    status._last_cpu_times = None
    assert status._cpu_percent() is None


def test_cpu_percent_idle_delta_zero_busy(monkeypatch, tmp_path):
    stat = tmp_path / "stat"
    monkeypatch.setattr(status, "_STAT_PATH", str(stat))
    status._last_cpu_times = None
    stat.write_text("cpu  0 0 0 900 0 0 0\n")
    status._cpu_percent()
    stat.write_text("cpu  0 0 0 1800 0 0 0\n")
    # purely idle delta: total 900, idle 900 → 0% busy
    assert status._cpu_percent() == 0.0


def test_load_avg_1m_is_a_float():
    assert isinstance(status._load_avg_1m(), float)


def test_cpu_count_is_a_non_negative_int():
    assert status._cpu_count() >= 0


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


@pytest.mark.asyncio
async def test_collect_status_reports_ram_and_disk_metrics(monkeypatch, tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "MemTotal:       16384000 kB\n"
        "MemFree:         2000000 kB\n"
        "MemAvailable:    9270000 kB\n"
    )
    monkeypatch.setattr(status, "_MEMINFO_PATH", str(meminfo))

    hass = FakeHass()
    hass.config.config_dir = str(tmp_path)
    monkeypatch.setattr(status.shutil, "disk_usage", lambda p: type("U", (), {"total": 100, "used": 71})())
    payload = await status.collect_status(hass)
    assert payload["ram_used_percent"] == 43.4
    assert payload["ram_total_mb"] == 16000.0
    assert payload["disk_used_percent"] == 71.0


@pytest.mark.asyncio
async def test_collect_status_ram_and_disk_zero_without_proc(monkeypatch):
    monkeypatch.setattr(status, "_MEMINFO_PATH", "/nonexistent/meminfo")

    hass = FakeHass()
    hass.config.config_dir = "/nonexistent/config"
    payload = await status.collect_status(hass)
    assert payload["ram_used_percent"] == 0.0
    assert payload["ram_total_mb"] == 0.0
    assert payload["disk_used_percent"] == 0.0


@pytest.mark.asyncio
async def test_collect_status_disk_zero_without_config_dir(monkeypatch, tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 100 kB\nMemAvailable: 40 kB\n")
    monkeypatch.setattr(status, "_MEMINFO_PATH", str(meminfo))

    hass = FakeHass()
    hass.config.config_dir = None
    payload = await status.collect_status(hass)
    assert payload["ram_used_percent"] == 60.0
    assert payload["ram_total_mb"] == 0.1
    assert payload["disk_used_percent"] == 0.0


@pytest.mark.asyncio
async def test_collect_status_reports_addon_inventory(monkeypatch):
    supervisor = {
        "addons": [
            {
                "slug": "mosquitto",
                "name": "Mosquitto broker",
                "version": "6.4.1",
                "state": "started",
                "update_available": True,
            },
            {"slug": "file_editor", "name": "", "version": "1.9", "state": "stopped"},
            "not-a-dict",
        ]
    }

    import sys
    import types

    hassio = types.ModuleType("homeassistant.components.hassio")
    hassio.get_supervisor_info = lambda hass: supervisor
    monkeypatch.setitem(sys.modules, "homeassistant.components.hassio", hassio)

    payload = await status.collect_status(FakeHass())
    assert payload["addons"] == [
        {
            "slug": "mosquitto",
            "name": "Mosquitto broker",
            "version": "6.4.1",
            "state": "started",
            "update_available": True,
        },
        {
            "slug": "file_editor",
            "name": "file_editor",
            "version": "1.9",
            "state": "stopped",
            "update_available": False,
        },
    ]


@pytest.mark.asyncio
async def test_collect_status_addons_empty_without_supervisor():
    payload = await status.collect_status(FakeHass())
    assert payload["addons"] == []


@pytest.mark.asyncio
async def test_collect_status_reports_automation_and_dashboard_counts():
    hass = FakeHass(
        entity_ids=("automation.a", "automation.b"),
        update_entities={"automation.a": FakeState(), "automation.b": FakeState()},
    )
    hass.data["lovelace"] = type(
        "FakeLovelace", (), {"dashboards": {"lovelace": object(), "extra": object()}}
    )()
    payload = await status.collect_status(hass)
    assert payload["automation_count"] == 2
    assert payload["dashboard_count"] == 2


@pytest.mark.asyncio
async def test_collect_status_automation_and_dashboard_counts_default_zero():
    payload = await status.collect_status(FakeHass())
    assert payload["automation_count"] == 0
    assert payload["dashboard_count"] == 0
