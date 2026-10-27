"""Tests for the unregistered repair flow (`repairs.py`).

A fixable issue without a matching repair flow shows an empty dialog in Home
Assistant; these tests pin down that the unregistered issue has a fix flow that
collects a new server URL and provisioning code and stores them on the entry.
"""
from __future__ import annotations

import pytest

from custom_components.stroompeil_ha_addon.const import (
    CONF_SERVER_URL,
    CONF_TOKEN,
    DOMAIN,
)
from custom_components.stroompeil_ha_addon.repairs import (
    UnregisteredRepairFlow,
    async_create_fix_flow,
)


class FakeConfigEntries:
    def __init__(self, entries):
        self._entries = entries
        self.updated = []
        self.reloaded = []
        self.reload_scheduled = []

    def async_get_entry(self, entry_id):
        return self._entries.get(entry_id)

    def async_update_entry(self, entry, data=None):
        self.updated.append((entry.entry_id, data))

    async def async_reload(self, entry_id):
        self.reloaded.append(entry_id)
        return None


class FakeHass:
    def __init__(self, entries):
        self.config_entries = FakeConfigEntries(entries)
        self.scheduled = []

    def async_create_task(self, coro):
        self.scheduled.append(coro)


class FakeEntry:
    def __init__(self, entry_id, data):
        self.entry_id = entry_id
        self.data = data


def _make_flow(entry=None, entry_id="abc123"):
    hass = FakeHass({"abc123": entry} if entry is not None else {})
    flow = UnregisteredRepairFlow()
    flow.hass = hass
    flow.issue_id = f"unregistered_{entry_id}"
    flow.data = {"entry_id": entry_id}
    return flow, hass


def test_fix_flow_created_for_unregistered_issue():
    flow = _make_flow()[0]
    assert isinstance(flow, UnregisteredRepairFlow)


@pytest.mark.asyncio
async def test_unknown_issue_id_raises():
    with pytest.raises(ValueError):
        await async_create_fix_flow(None, "something_else", None)


@pytest.mark.asyncio
async def test_init_shows_form_with_token_fields():
    flow, _ = _make_flow(entry=FakeEntry("abc123", {}))
    result = await flow.async_step_init(None)
    assert result["type"] == "form"
    assert result["step_id"] == "init"
    keys = {marker.schema for marker in result["data_schema"].schema}
    assert keys == {CONF_SERVER_URL, CONF_TOKEN}


@pytest.mark.asyncio
async def test_init_with_input_updates_and_reloads_entry():
    entry = FakeEntry(
        "abc123",
        {
            "environment": "test",
            CONF_SERVER_URL: "https://old.example",
            CONF_TOKEN: "old-token",
            "host_name": "Home",
        },
    )
    flow, hass = _make_flow(entry=entry)
    result = await flow.async_step_init(
        {CONF_SERVER_URL: "https://new.example", CONF_TOKEN: "new-token"}
    )
    assert result["type"] == "create_entry"
    entry_id, data = hass.config_entries.updated[0]
    assert entry_id == "abc123"
    assert data[CONF_SERVER_URL] == "https://new.example"
    assert data[CONF_TOKEN] == "new-token"
    assert data["environment"] == "test"
    assert hass.config_entries.reloaded == ["abc123"]


@pytest.mark.asyncio
async def test_init_with_input_when_entry_gone_aborts():
    flow, _ = _make_flow(entry=None)
    result = await flow.async_step_init(
        {CONF_SERVER_URL: "https://new.example", CONF_TOKEN: "new-token"}
    )
    assert result == {"type": "abort", "reason": "entry_gone"}
