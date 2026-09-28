"""Tests for the command allowlist and dispatch (`commands.py`).

This is the trust boundary on the host side: only allowlisted command types reach
a handler; anything else is rejected. Handlers must never raise out of
`dispatch_command`.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from custom_components.stroompeil_ha_addon import commands
from custom_components.stroompeil_ha_addon.const import (
    COMMAND_TYPE_CORE_UPDATE,
    COMMAND_TYPE_LOGS,
    COMMAND_TYPE_RESTART,
    COMMAND_TYPE_UPDATE,
    COMMAND_TYPE_UPDATES_ALL,
    DOMAIN,
)


class FakeServices:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, data=None, blocking=False):
        self.calls.append((domain, service, data))


class FakeStates:
    """Minimal states stand-in for update-entity lookups."""

    def __init__(self, entities=None):
        self._entities = entities or {}

    def get(self, entity_id):
        return self._entities.get(entity_id)

    def async_entity_ids(self, domain):
        return [eid for eid, st in self._entities.items()]


class FakeState:
    def __init__(self, attributes=None):
        self.attributes = attributes or {}


class FakeHass:
    def __init__(self, states=None):
        self.services = FakeServices()
        self.states = states or FakeStates()
        self._tasks = []

    def async_create_task(self, coro):
        task = asyncio.ensure_future(coro)
        self._tasks.append(task)
        return task


@pytest.mark.asyncio
async def test_unknown_command_type_is_rejected(caplog):
    result = await commands.dispatch_command(FakeHass(), "cmd-1", "evil.shutdown", {})

    assert result["status"] == "error"
    assert "evil.shutdown" in result["detail"]
    assert "rejected unknown command type" in caplog.text


@pytest.mark.asyncio
async def test_restart_dispatches_homeassistant_service():
    hass = FakeHass()

    result = await commands.dispatch_command(hass, "cmd-2", COMMAND_TYPE_RESTART, {})

    assert result == {"status": "dispatched", "detail": "restart queued"}
    # Let the scheduled service-call task actually execute so we can assert it.
    await asyncio.gather(*hass._tasks)
    assert (hass.services.calls[0][0], hass.services.calls[0][1]) == ("homeassistant", "restart")


@pytest.mark.asyncio
async def test_restart_ignores_args_field():
    # The restart handler should not be tripped up by unexpected args.
    result = await commands.dispatch_command(
        FakeHass(), "cmd-3", COMMAND_TYPE_RESTART, {"force": True}
    )
    assert result["status"] == "dispatched"


@pytest.mark.asyncio
async def test_handler_exception_is_caught_and_reported(caplog):
    class BoomHass(FakeHass):
        def async_create_task(self, coro):
            coro.close()  # avoid an un-awaited-coroutine warning
            raise RuntimeError("scheduler unavailable")

    with caplog.at_level(logging.ERROR):
        result = await commands.dispatch_command(
            BoomHass(), "cmd-4", COMMAND_TYPE_RESTART, {}
        )

    assert result["status"] == "error"
    assert "scheduler unavailable" in result["detail"]


def test_known_commands_are_allowlisted():
    # Guard against accidentally widening the trust boundary.
    assert set(commands.ALLOWED_COMMANDS) == {
        COMMAND_TYPE_RESTART,
        COMMAND_TYPE_UPDATE,
        COMMAND_TYPE_CORE_UPDATE,
        COMMAND_TYPE_LOGS,
        COMMAND_TYPE_UPDATES_ALL,
    }
    assert callable(commands.ALLOWED_COMMANDS[COMMAND_TYPE_RESTART])
    assert callable(commands.ALLOWED_COMMANDS[COMMAND_TYPE_UPDATE])
    assert callable(commands.ALLOWED_COMMANDS[COMMAND_TYPE_CORE_UPDATE])
    assert callable(commands.ALLOWED_COMMANDS[COMMAND_TYPE_LOGS])
    assert callable(commands.ALLOWED_COMMANDS[COMMAND_TYPE_UPDATES_ALL])


class _UpdateEntity(FakeState):
    pass


class _InstallDoneServices(FakeServices):
    """Service stand-in where update.install completes immediately (in_progress -> False)."""

    async def async_call(self, domain, service, data=None, blocking=False):
        await super().async_call(domain, service, data, blocking)
        if domain == "update" and service == "install":
            self._hass.states._entities[self._entity_id] = FakeState(
                {"installed_version": self._new_version, "latest_version": self._new_version, "in_progress": False}
            )


def _make_update_hass(*, installed, latest, in_progress=False, new_version=None):
    entity_id = f"update.{DOMAIN}_update"
    states = FakeStates({entity_id: FakeState({
        "installed_version": installed,
        "latest_version": latest,
        "in_progress": in_progress,
        "update_percentage": None,
    })})
    services = _InstallDoneServices()
    hass = FakeHass(states=states)
    hass.services = services
    services._hass = hass
    services._entity_id = entity_id
    services._new_version = new_version or latest
    return hass, entity_id


@pytest.mark.asyncio
async def test_update_returns_error_without_hacs_entity():
    hass = FakeHass(states=FakeStates({}))  # no update entities
    result = await commands.dispatch_command(hass, "u1", COMMAND_TYPE_UPDATE, {})
    assert result["status"] == "error"
    assert "update entity" in result["detail"]


@pytest.mark.asyncio
async def test_update_already_on_target_returns_ok():
    hass, _ = _make_update_hass(installed="0.2.0", latest="0.2.0")
    result = await commands.dispatch_command(hass, "u2", COMMAND_TYPE_UPDATE, {})
    assert result["status"] == "ok"
    assert "already on" in result["detail"]
    assert hass.services.calls == []  # no install, no restart


@pytest.mark.asyncio
async def test_update_emits_progress_then_restarts(monkeypatch):
    hass, _ = _make_update_hass(installed="0.2.0", latest="0.3.0", new_version="0.3.0")
    # avoid the 1s sleep in the poll loop
    import custom_components.stroompeil_ha_addon.commands as cmds
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(cmds.asyncio, "sleep", lambda *_: _real_sleep(0))

    progress: list[tuple] = []

    async def send_progress(phase, percent, version_target, detail):
        progress.append((phase, percent, version_target, detail))

    result = await commands.dispatch_command(hass, "u3", COMMAND_TYPE_UPDATE, {}, send_progress)

    assert result["status"] == "dispatched"
    phases = [p[0] for p in progress]
    assert "checking" in phases
    assert "installed" in phases
    # install service was called against the entity
    assert any(c[0] == "update" and c[1] == "install" for c in hass.services.calls)
    # restart was queued
    await asyncio.gather(*hass._tasks)
    assert any(c[0] == "homeassistant" and c[1] == "restart" for c in hass.services.calls)
    assert result["detail"] == "update to 0.3.0 installed, restart queued"


@pytest.mark.asyncio
async def test_update_install_failure_returns_error(monkeypatch):
    class FailServices(FakeServices):
        async def async_call(self, domain, service, data=None, blocking=False):
            await super().async_call(domain, service, data, blocking)
            if domain == "update":
                raise RuntimeError("network down")

    entity_id = f"update.{DOMAIN}_update"
    states = FakeStates({entity_id: FakeState({
        "installed_version": "0.2.0", "latest_version": "0.3.0", "in_progress": False,
    })})
    hass = FakeHass(states=states)
    hass.services = FailServices()
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(commands.asyncio, "sleep", lambda *_: _real_sleep(0))

    result = await commands.dispatch_command(hass, "u4", COMMAND_TYPE_UPDATE, {})
    assert result["status"] == "error"
    assert "update.install failed" in result["detail"]


CORE_ENTITY = "update.home_assistant_core_update"
_REAL_SLEEP = asyncio.sleep


def _make_core_hass(*, installed, latest, in_progress=False, new_version=None, entity_id=CORE_ENTITY):
    states = FakeStates({entity_id: FakeState({
        "installed_version": installed,
        "latest_version": latest,
        "in_progress": in_progress,
        "update_percentage": None,
    })})
    services = _InstallDoneServices()
    hass = FakeHass(states=states)
    hass.services = services
    services._hass = hass
    services._entity_id = entity_id
    services._new_version = new_version or latest
    return hass


@pytest.mark.asyncio
async def test_core_update_returns_error_without_supervisor_entity():
    hass = FakeHass(states=FakeStates({}))  # no update entities at all
    result = await commands.dispatch_command(hass, "c1", COMMAND_TYPE_CORE_UPDATE, {})
    assert result["status"] == "error"
    assert "no HA-core update entity" in result["detail"]


def test_core_update_entity_lookup_falls_back_to_substring():
    renamed = "update.home_assistant_core_new"
    states = FakeStates({renamed: FakeState({"latest_version": "2026.9.1"})})
    assert commands.find_core_update_entity(FakeHass(states=states)) == renamed


def test_core_update_entity_lookup_excludes_supervisor_entity():
    supervisor = "update.home_assistant_supervisor_update"
    states = FakeStates({supervisor: FakeState({"latest_version": "2026.9.1"})})
    assert commands.find_core_update_entity(FakeHass(states=states)) == ""


@pytest.mark.asyncio
async def test_core_update_already_on_target_returns_ok():
    hass = _make_core_hass(installed="2026.9.0", latest="2026.9.0")
    result = await commands.dispatch_command(hass, "c2", COMMAND_TYPE_CORE_UPDATE, {})
    assert result["status"] == "ok"
    assert "already on" in result["detail"]
    assert hass.services.calls == []  # no install, no restart


@pytest.mark.asyncio
async def test_core_update_emits_progress_then_restarts(monkeypatch):
    hass = _make_core_hass(installed="2026.9.0", latest="2026.9.1", new_version="2026.9.1")
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(commands.asyncio, "sleep", lambda *_: _real_sleep(0))
    progress: list[tuple] = []

    async def send_progress(phase, percent, version_target, detail):
        progress.append((phase, percent, version_target, detail))

    result = await commands.dispatch_command(hass, "c3", COMMAND_TYPE_CORE_UPDATE, {}, send_progress)
    assert result["status"] == "dispatched"
    phases = [p[0] for p in progress]
    assert "checking" in phases
    assert "installed" in phases
    assert any(p[2] == "2026.9.1" for p in progress)
    assert any(c[0] == "update" and c[1] == "install" for c in hass.services.calls)
    await asyncio.gather(*hass._tasks)
    assert any(c[0] == "homeassistant" and c[1] == "restart" for c in hass.services.calls)
    assert result["detail"] == "core update to 2026.9.1 installed, restart queued"


@pytest.mark.asyncio
async def test_core_update_pinned_version_is_passed_to_install(monkeypatch):
    hass = _make_core_hass(installed="2026.9.0", latest="2026.9.1", new_version="2026.8.0")
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(commands.asyncio, "sleep", lambda *_: _real_sleep(0))

    result = await commands.dispatch_command(hass, "c4", COMMAND_TYPE_CORE_UPDATE, {"version": "2026.8.0"})
    assert result["status"] == "dispatched"
    install_calls = [c for c in hass.services.calls if c[:2] == ("update", "install")]
    assert install_calls[0][2]["version"] == "2026.8.0"


@pytest.mark.asyncio
async def test_core_update_install_failure_returns_error(monkeypatch):
    class FailServices(FakeServices):
        async def async_call(self, domain, service, data=None, blocking=False):
            await super().async_call(domain, service, data, blocking)
            if domain == "update":
                raise RuntimeError("supervisor offline")

    states = FakeStates({CORE_ENTITY: FakeState({
        "installed_version": "2026.9.0", "latest_version": "2026.9.1", "in_progress": False,
    })})
    hass = FakeHass(states=states)
    hass.services = FailServices()
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(commands.asyncio, "sleep", lambda *_: _real_sleep(0))

    result = await commands.dispatch_command(hass, "c5", COMMAND_TYPE_CORE_UPDATE, {})
    assert result["status"] == "error"
    assert "update.install failed" in result["detail"]


class FakeLogEntry:
    def __init__(self, *, level, name, message, timestamp=None, first_occurrence=None, count=1):
        self.level = level
        self.name = name
        self.message = message
        self.timestamp = timestamp
        self.first_occurrence = first_occurrence
        self.count = count


class FakeLogHass(FakeHass):
    def __init__(self, entries=None):
        super().__init__()
        self.data = {"system_log": {"items": entries or []}}


@pytest.mark.asyncio
async def test_logs_command_returns_entries_in_data():
    entry = FakeLogEntry(
        level="ERROR",
        name="zigbee2mqtt",
        message="Adapter disconnected",
        count=3,
    )
    result = await commands.dispatch_command(
        FakeLogHass(entries=[entry]), "cmd-logs-1", COMMAND_TYPE_LOGS, {}
    )
    assert result["status"] == "ok"
    assert result["data"]["entries"][0]["logger"] == "zigbee2mqtt"
    assert result["data"]["entries"][0]["count"] == 3


@pytest.mark.asyncio
async def test_logs_command_clamps_limit_and_filters_level():
    entries = [
        FakeLogEntry(level="WARNING", name="w", message="m"),
        FakeLogEntry(level="ERROR", name="e", message="m"),
    ]
    result = await commands.dispatch_command(
        FakeLogHass(entries=entries), "cmd-logs-2", COMMAND_TYPE_LOGS,
        {"min_level": "WARNING", "limit": 999},
    )
    assert result["status"] == "ok"
    assert len(result["data"]["entries"]) == 2


@pytest.mark.asyncio
async def test_logs_command_default_filters_warning_out():
    entries = [
        FakeLogEntry(level="WARNING", name="w", message="m"),
    ]
    result = await commands.dispatch_command(
        FakeLogHass(entries=entries), "cmd-logs-3", COMMAND_TYPE_LOGS, {}
    )
    assert result["status"] == "ok"
    assert result["data"]["entries"] == []


@pytest.mark.asyncio
async def test_logs_command_handles_broken_limit_arg():
    entry = FakeLogEntry(level="ERROR", name="e", message="m")
    result = await commands.dispatch_command(
        FakeLogHass(entries=[entry]), "cmd-logs-4", COMMAND_TYPE_LOGS,
        {"limit": "not-a-number"},
    )
    assert result["status"] == "ok"
    assert len(result["data"]["entries"]) == 1


class FakeUpdateStates:
    """States stand-in that knows per-entity state strings."""

    def __init__(self, entities):
        self._entities = entities

    def async_entity_ids(self, domain=None):
        return list(self._entities) if domain == "update" else []

    def get(self, entity_id):
        return self._entities.get(entity_id)


class UpdateFakeState:
    def __init__(self, state, attributes=None):
        self.state = state
        self.attributes = attributes or {}


@pytest.mark.asyncio
async def test_updates_all_installs_pending_sequentially_and_skips_core(monkeypatch):
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(commands.asyncio, "sleep", lambda *_: _real_sleep(0))

    entities = {
        "update.home_assistant_core_update": UpdateFakeState("on"),
        "update.some_addon_update": UpdateFakeState("on", {"friendly_title": "Some add-on"}),
        "update.hacs_repo_update": UpdateFakeState("on", {"friendly_title": "HACS repo"}),
        "update.up_to_date_thing": UpdateFakeState("off"),
    }
    hass = FakeHass(states=FakeUpdateStates(entities))
    progress = []

    async def send_progress(phase, percent, version_target, detail):
        progress.append((phase, percent, detail))

    result = await commands.dispatch_command(hass, "cmd-u1", COMMAND_TYPE_UPDATES_ALL, {}, send_progress)

    assert result["status"] == "ok"
    installed = result["data"]["installed"]
    assert "update.home_assistant_core_update" not in installed
    assert "update.some_addon_update" in installed
    assert "update.hacs_repo_update" in installed
    assert "update.up_to_date_thing" not in installed
    assert result["data"]["skipped_core"] == "update.home_assistant_core_update"
    called = [c[2]["entity_id"] for c in hass.services.calls]
    assert called == ["update.some_addon_update", "update.hacs_repo_update"]
    assert ("installed", 100, "2 updated, 0 failed") in progress


@pytest.mark.asyncio
async def test_updates_all_continues_after_failure(monkeypatch):
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(commands.asyncio, "sleep", lambda *_: _real_sleep(0))

    class FlakyServices(FakeServices):
        def __init__(self):
            super().__init__()
            self.calls_made = 0

        async def async_call(self, domain, service, data=None, blocking=False):
            self.calls_made += 1
            if data and data.get("entity_id") == "update.broken_update":
                raise RuntimeError("download failed")
            self.calls.append((domain, service, data))

    entities = {
        "update.broken_update": UpdateFakeState("on"),
        "update.healthy_update": UpdateFakeState("on"),
    }
    hass = FakeHass(states=FakeUpdateStates(entities))
    hass.services = FlakyServices()

    result = await commands.dispatch_command(hass, "cmd-u2", COMMAND_TYPE_UPDATES_ALL, {})

    assert result["status"] == "error"
    assert result["detail"] == "1 updated, 1 failed"
    assert result["data"]["installed"] == ["update.healthy_update"]
    assert result["data"]["failed"] == [
        {"entity_id": "update.broken_update", "error": "download failed"}
    ]


@pytest.mark.asyncio
async def test_updates_all_noop_without_pending_updates():
    hass = FakeHass(states=FakeUpdateStates({}))
    result = await commands.dispatch_command(hass, "cmd-u3", COMMAND_TYPE_UPDATES_ALL, {})
    assert result == {"status": "ok", "detail": "no pending add-on or integration updates"}


class _SlowInstallServices(FakeServices):
    """Service stand-in where update.install stays in progress and reports
    rising percentages while it runs (ADR 0038 narration path)."""

    def __init__(self, hass, entity_id, steps=(10, 55, 90)):
        super().__init__()
        self._hass = hass
        self._entity_id = entity_id
        self._steps = list(steps)

    async def async_call(self, domain, service, data=None, blocking=False):
        await super().async_call(domain, service, data, blocking)
        if domain != "update" or service != "install":
            return
        states = self._hass.states
        for pct in self._steps:
            states._entities[self._entity_id] = FakeState({
                "installed_version": "2026.9.0",
                "latest_version": "2026.9.1",
                "in_progress": True,
                "update_percentage": pct,
            })
            await _REAL_SLEEP(0.005)
        states._entities[self._entity_id] = FakeState({
            "installed_version": "2026.9.1",
            "latest_version": "2026.9.1",
            "in_progress": False,
            "update_percentage": None,
        })


@pytest.mark.asyncio
async def test_core_update_narrates_live_percent_during_install(monkeypatch):
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(commands.asyncio, "sleep", lambda *_: _real_sleep(0.001))
    entity_id = CORE_ENTITY
    states = FakeStates({entity_id: FakeState({
        "installed_version": "2026.9.0", "latest_version": "2026.9.1",
        "in_progress": False, "update_percentage": None,
    })})
    hass = FakeHass(states=states)
    hass.services = _SlowInstallServices(hass, entity_id)
    progress: list[tuple] = []

    async def send_progress(phase, percent, version_target, detail):
        progress.append((phase, percent, version_target, detail))

    result = await commands.dispatch_command(
        hass, "c-live", COMMAND_TYPE_CORE_UPDATE, {}, send_progress
    )
    assert result["status"] == "dispatched"
    installing = [p for p in progress if p[0] == "installing"]
    percents = [p[1] for p in installing if p[1] is not None]
    distinct: list[int] = []
    for pct in percents:
        if not distinct or distinct[-1] != pct:
            distinct.append(pct)
    assert distinct == [10, 55, 90]
    assert all(p[2] == "2026.9.1" for p in installing)
    assert progress[-1][0] == "installed"
    await asyncio.gather(*hass._tasks)
    assert any(c[0] == "homeassistant" and c[1] == "restart" for c in hass.services.calls)


@pytest.mark.asyncio
async def test_core_update_narration_goes_indeterminate_when_percent_stalls(monkeypatch):
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(commands.asyncio, "sleep", lambda *_: _real_sleep(0.001))

    entity_id = CORE_ENTITY

    class StalledInstallServices(_SlowInstallServices):
        async def async_call(self, domain, service, data=None, blocking=False):
            await super().async_call(domain, service, data, blocking)
            if domain != "update" or service != "install":
                return
            states = self._hass.states
            states._entities[self._entity_id] = FakeState({
                "installed_version": "2026.9.0",
                "latest_version": "2026.9.1",
                "in_progress": True,
                "update_percentage": 19,
            })
            for _ in range(60):
                await _REAL_SLEEP(0.001)
            states._entities[self._entity_id] = FakeState({
                "installed_version": "2026.9.1",
                "latest_version": "2026.9.1",
                "in_progress": False,
                "update_percentage": None,
            })

    states = FakeStates({entity_id: FakeState({
        "installed_version": "2026.9.0", "latest_version": "2026.9.1",
        "in_progress": False, "update_percentage": None,
    })})
    hass = FakeHass(states=states)
    hass.services = StalledInstallServices(hass, entity_id, steps=(19,))
    progress: list[tuple] = []

    async def send_progress(phase, percent, version_target, detail):
        progress.append((phase, percent, version_target, detail))

    result = await commands.dispatch_command(
        hass, "c-stall", COMMAND_TYPE_CORE_UPDATE, {}, send_progress
    )
    assert result["status"] == "dispatched"
    installing = [p for p in progress if p[0] == "installing"]
    assert installing, "expected installing frames"
    assert installing[0][1] == 19
    assert any(p[1] is None for p in installing[1:]), (
        "narration should switch to indeterminate frames while the percent stalls"
    )
    assert progress[-1][0] == "installed"


@pytest.mark.asyncio
async def test_core_update_install_failure_during_narration_returns_error(monkeypatch):
    _real_sleep = asyncio.sleep
    monkeypatch.setattr(commands.asyncio, "sleep", lambda *_: _real_sleep(0.001))

    class FailingSlowServices(_SlowInstallServices):
        async def async_call(self, domain, service, data=None, blocking=False):
            await super().async_call(domain, service, data, blocking)
            if domain == "update" and service == "install":
                raise RuntimeError("download interrupted")

    entity_id = CORE_ENTITY
    states = FakeStates({entity_id: FakeState({
        "installed_version": "2026.9.0", "latest_version": "2026.9.1",
        "in_progress": False,
    })})
    hass = FakeHass(states=states)
    hass.services = FailingSlowServices(hass, entity_id)
    result = await commands.dispatch_command(hass, "c-fail", COMMAND_TYPE_CORE_UPDATE, {})
    assert result["status"] == "error"
    assert "update.install failed" in result["detail"]


def test_as_int_percent_normalizes_and_clamps():
    assert commands._as_int_percent(42.6) == 43
    assert commands._as_int_percent(7) == 7
    assert commands._as_int_percent(-3) == 0
    assert commands._as_int_percent(250) == 100
    assert commands._as_int_percent(None) is None
    assert commands._as_int_percent(True) is None
    assert commands._as_int_percent("42") is None
