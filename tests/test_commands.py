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
from custom_components.stroompeil_ha_addon.const import COMMAND_TYPE_RESTART


class FakeServices:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service):
        self.calls.append((domain, service))


class FakeHass:
    def __init__(self):
        self.services = FakeServices()
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
    assert hass.services.calls == [("homeassistant", "restart")]


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


def test_only_restart_is_allowlisted():
    # Guard against accidentally widening the trust boundary.
    assert set(commands.ALLOWED_COMMANDS) == {COMMAND_TYPE_RESTART}
    assert callable(commands.ALLOWED_COMMANDS[COMMAND_TYPE_RESTART])
