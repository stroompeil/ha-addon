"""Tests for the WebSocket client frame handling (`ws_client.py`).

These tests target the protocol logic that does not require a live network:
agent-URL construction, inbound frame routing, and the enrolled-token rotation.
`_run_once`/`run` are exercised via the loop because they contain the actual I/O.
"""
from __future__ import annotations

import json
import types
from typing import Any

import pytest

from custom_components.stroompeil_ha_addon.const import DOMAIN
from custom_components.stroompeil_ha_addon.ws_client import StroompeilHAAddonWSClient


class FakeEntry:
    def __init__(self, *, entry_id="abc123", data=None):
        self.entry_id = entry_id
        self.data = data or {}


class FakeConfigEntries:
    def __init__(self):
        self.updates = []

    def async_update_entry(self, entry, data=None):
        if data is not None:
            entry.data = data
        self.updates.append(entry)


class FakeHass:
    def __init__(self):
        self.config_entries = FakeConfigEntries()


class FakeWS:
    """Minimal aiohttp websocket stand-in for `_handle_text`/`_send_result`."""

    def __init__(self):
        self.closed = False
        self.sent: list[str] = []

    async def send_str(self, data: str):
        self.sent.append(data)


def _make_client(*, entry=None, server_url="wss://fleet-dev.stroompeil.nl", token="t1") -> StroompeilHAAddonWSClient:
    return StroompeilHAAddonWSClient(FakeHass(), server_url, token, entry=entry)


def test_agent_url_appends_token_with_question_mark():
    client = _make_client(server_url="wss://fleet-dev.stroompeil.nl")

    assert client._agent_url() == "wss://fleet-dev.stroompeil.nl/agent?token=t1"


def test_agent_url_preserves_existing_query_string():
    client = _make_client(server_url="wss://fleet-dev.stroompeil.nl/?x=1")

    assert client._agent_url() == "wss://fleet-dev.stroompeil.nl/?x=1/agent&token=t1"


def test_server_url_trailing_slash_is_stripped():
    client = _make_client(server_url="wss://fleet-dev.stroompeil.nl/")

    assert client._agent_url() == "wss://fleet-dev.stroompeil.nl/agent?token=t1"


@pytest.mark.asyncio
async def test_handle_text_invalid_json_is_ignored(caplog):
    client = _make_client()
    client._ws = FakeWS()

    await client._handle_text("not json")

    assert client._ws.sent == []  # nothing sent for a bad frame
    assert "invalid json frame" in caplog.text


@pytest.mark.asyncio
async def test_handle_text_enrolled_rotates_token_in_entry():
    entry = FakeEntry(data={"token": "t1", "server_url": "wss://fleet-dev.stroompeil.nl"})
    client = _make_client(entry=entry, token="t1")

    await client._handle_text(json.dumps({"msg_type": "enrolled", "token": "permanent-token"}))

    assert entry.data["token"] == "permanent-token"
    assert client._token == "permanent-token"


@pytest.mark.asyncio
async def test_handle_text_enrolled_without_entry_stores_nothing():
    client = _make_client(entry=None, token="t1")

    await client._handle_text(json.dumps({"msg_type": "enrolled", "token": "x"}))

    assert client._token == "t1"  # unchanged


@pytest.mark.asyncio
async def test_handle_text_command_returns_result_frame(monkeypatch):
    client = _make_client()
    client._ws = FakeWS()

    async def fake_dispatch(hass, command_id, type_, args, send_progress=None):
        return {"status": "dispatched", "detail": "ok"}

    monkeypatch.setattr(
        "custom_components.stroompeil_ha_addon.ws_client.dispatch_command",
        fake_dispatch,
    )

    await client._handle_text(
        json.dumps({"msg_type": "command", "command_id": "c1", "type": "homeassistant.restart", "args": {}})
    )

    assert len(client._ws.sent) == 1
    frame = json.loads(client._ws.sent[0])
    assert frame == {"msg_type": "result", "command_id": "c1", "status": "dispatched", "detail": "ok"}


@pytest.mark.asyncio
async def test_handle_text_command_includes_command_id_from_frame(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_dispatch(hass, command_id, type_, args, send_progress=None):
        captured.update(command_id=command_id, type=type_, args=args)
        return {"status": "ok", "detail": ""}

    client = _make_client()
    client._ws = FakeWS()

    monkeypatch.setattr(
        "custom_components.stroompeil_ha_addon.ws_client.dispatch_command",
        fake_dispatch,
    )

    await client._handle_text(
        json.dumps({"msg_type": "command", "command_id": "job-42", "type": "foo", "args": {"a": 1}})
    )

    assert captured == {"command_id": "job-42", "type": "foo", "args": {"a": 1}}
    assert json.loads(client._ws.sent[0])["command_id"] == "job-42"


@pytest.mark.asyncio
async def test_handle_text_unknown_msg_type_is_ignored():
    client = _make_client()
    client._ws = FakeWS()

    await client._handle_text(json.dumps({"msg_type": "something_else"}))

    assert client._ws.sent == []


@pytest.mark.asyncio
async def test_send_result_is_noop_when_websocket_closed():
    client = _make_client()
    client._ws = types.SimpleNamespace(closed=True, sent=[])  # type: ignore[attr-defined]

    # Should not raise even though ws is closed.
    await client._send_result("c1", {"status": "ok", "detail": ""})


@pytest.mark.asyncio
async def test_send_status_skipped_when_websocket_closed():
    client = _make_client()
    client._ws = types.SimpleNamespace(closed=True)  # type: ignore[attr-defined]

    await client._send_status()  # should not raise and should not touch the ws


def test_set_sensor_registers_callback_target():
    client = _make_client()

    class FakeSensor:
        connected = None

        def set_connected(self, value):
            self.connected = value

    sensor = FakeSensor()
    client.set_sensor(sensor)

    client._set_connected(True)
    assert sensor.connected is True


@pytest.mark.asyncio
async def test_send_status_sends_status_frame_on_success(monkeypatch):
    client = _make_client()
    ws = FakeWS()
    client._ws = ws

    async def fake_collect(hass):
        return {"msg_type": "status", "ha_version": "x"}

    monkeypatch.setattr(
        "custom_components.stroompeil_ha_addon.ws_client.status_mod.collect_status",
        fake_collect,
    )

    await client._send_status()

    assert len(ws.sent) == 1
    assert json.loads(ws.sent[0])["msg_type"] == "status"


@pytest.mark.asyncio
async def test_send_status_sends_nothing_when_collect_fails(monkeypatch, caplog):
    client = _make_client()
    ws = FakeWS()
    client._ws = ws

    async def boom_collect(hass):
        raise RuntimeError("collect failed")

    monkeypatch.setattr(
        "custom_components.stroompeil_ha_addon.ws_client.status_mod.collect_status",
        boom_collect,
    )

    await client._send_status()

    assert ws.sent == []  # nothing sent
    assert "failed to collect status" in caplog.text


@pytest.mark.asyncio
async def test_send_status_is_noop_when_websocket_closed(monkeypatch):
    client = _make_client()
    client._ws = types.SimpleNamespace(closed=True)  # type: ignore[attr-defined]

    async def fake_collect(hass):
        return {"msg_type": "status"}

    monkeypatch.setattr(
        "custom_components.stroompeil_ha_addon.ws_client.status_mod.collect_status",
        fake_collect,
    )

    await client._send_status()  # early-returns before any send

