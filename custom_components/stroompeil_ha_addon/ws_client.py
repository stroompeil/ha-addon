"""WebSocket client to the Stroompeil HA server.

Connects to `<server_url>/agent?token=<token>`, sends a status snapshot every 60s, and
handles inbound command frames. Reconnects with exponential backoff on any disconnect or
error. Must never raise out of the integration.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp

from . import status as status_mod
from .commands import dispatch_command
from .const import (
    DEFAULT_BACKOFF_INITIAL_SECONDS,
    DEFAULT_BACKOFF_MAX_SECONDS,
    DEFAULT_STATUS_INTERVAL_SECONDS,
    DOMAIN,
)

_WS_CLOSE_UNREGISTERED = 4401

_LOGGER = logging.getLogger(__name__)


def _create_unregistered_issue(hass, entry_id: str) -> None:
    from homeassistant.helpers.issue_registry import (
        IssueSeverity,
        async_create_issue,
    )

    async_create_issue(
        hass=hass,
        domain=DOMAIN,
        issue_id=f"unregistered_{entry_id}",
        is_fixable=True,
        issue_domain=DOMAIN,
        severity=IssueSeverity.WARNING,
        translation_key="unregistered",
        translation_placeholders={"entry_id": entry_id},
    )


def _delete_unregistered_issue(hass, entry_id: str) -> None:
    from homeassistant.helpers.issue_registry import async_delete_issue

    async_delete_issue(hass, DOMAIN, f"unregistered_{entry_id}")


class StroompeilHAAddonWSClient:
    def __init__(
        self,
        hass,
        server_url: str,
        token: str,
        entry=None,
        status_interval: int = DEFAULT_STATUS_INTERVAL_SECONDS,
    ) -> None:
        self._hass = hass
        self._server_url = server_url.rstrip("/")
        self._token = token
        self._entry = entry
        self._status_interval = status_interval
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._stop = asyncio.Event()
        self._sensor = None

    def set_sensor(self, sensor) -> None:
        self._sensor = sensor

    def _set_connected(self, connected: bool) -> None:
        if self._sensor is not None:
            self._sensor.set_connected(connected)

    async def stop(self) -> None:
        self._stop.set()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._session is not None and not self._session.closed:
            await self._session.close()

    def _agent_url(self) -> str:
        sep = "&" if "?" in self._server_url else "?"
        return f"{self._server_url}/agent{sep}token={self._token}"

    async def run(self) -> None:
        backoff = DEFAULT_BACKOFF_INITIAL_SECONDS
        while not self._stop.is_set():
            try:
                await self._run_once()
                backoff = DEFAULT_BACKOFF_INITIAL_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _LOGGER.warning("Stroompeil HA addon connection error: %s", exc)
            if self._stop.is_set():
                break
            _LOGGER.info("reconnecting in %ss", backoff)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, DEFAULT_BACKOFF_MAX_SECONDS)

    async def _run_once(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        close_code = None
        try:
            async with self._session.ws_connect(self._agent_url(), heartbeat=30) as ws:
                self._ws = ws
                self._set_connected(True)
                _LOGGER.info("connected to Stroompeil HA server")
                receiver = asyncio.create_task(self._receive_loop(ws))
                try:
                    await asyncio.sleep(1)
                    while not self._stop.is_set() and not ws.closed:
                        await self._send_status()
                        try:
                            await asyncio.wait_for(self._stop.wait(), timeout=self._status_interval)
                        except asyncio.TimeoutError:
                            pass
                except asyncio.CancelledError:
                    await self._send_restarting_if_restart()
                    raise
                if not receiver.done():
                    receiver.cancel()
                close_code = ws.close_code
        finally:
            self._ws = None
            self._set_connected(False)
        if close_code == _WS_CLOSE_UNREGISTERED and self._entry is not None:
            _LOGGER.warning("server rejected token (unregistered), stopping reconnection")
            _create_unregistered_issue(self._hass, self._entry.entry_id)
            self._stop.set()

    async def _send_restarting_if_restart(self) -> None:
        """Best-effort "restarting" notification, sent as HA begins a restart.

        HA cancels background tasks before setting its exit code and firing
        `homeassistant_stop`; by the time the cancellation reaches this
        coroutine, `hass.exit_code` is final. `RESTART_EXIT_CODE` means HA is
        actually executing a restart (not a plain stop or an entry unload); the
        socket is still open here, so this is the last moment we can tell the
        server what is happening.
        """
        try:
            from homeassistant.const import RESTART_EXIT_CODE
        except ImportError:
            return
        if getattr(self._hass, "exit_code", 0) != RESTART_EXIT_CODE:
            return
        ws = self._ws
        if ws is None or ws.closed:
            return
        try:
            await ws.send_str(json.dumps({"msg_type": "restarting"}))
            _LOGGER.info("Home Assistant is restarting; notified the server")
        except Exception:
            _LOGGER.debug("failed to send restarting frame", exc_info=True)

    async def _send_status(self) -> None:
        if self._ws is None or self._ws.closed:
            return
        try:
            payload = await status_mod.collect_status(self._hass)
        except Exception as exc:
            _LOGGER.warning("Stroompeil HA addon: failed to collect status: %s", exc)
            return
        await self._ws.send_str(json.dumps(payload))

    async def _receive_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                await self._handle_text(msg.data)
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

    async def _handle_text(self, data: str) -> None:
        try:
            frame = json.loads(data)
        except json.JSONDecodeError:
            _LOGGER.warning("Stroompeil HA addon: invalid json frame")
            return
        msg_type = frame.get("msg_type")
        if msg_type == "enrolled":
            new_token = frame.get("token", "")
            if new_token and self._entry is not None:
                from .const import CONF_TOKEN

                new_data = dict(self._entry.data)
                new_data[CONF_TOKEN] = new_token
                self._hass.config_entries.async_update_entry(self._entry, data=new_data)
                self._token = new_token
                _LOGGER.info("enrolled successfully, permanent token stored")
            return
        if msg_type != "command":
            return
        command_id = frame.get("command_id", "")
        type_ = frame.get("type", "")
        args = frame.get("args", {}) or {}
        result = await dispatch_command(
            self._hass, command_id, type_, args, self._send_progress(command_id)
        )
        await self._send_result(command_id, result)

    async def _send_result(self, command_id: str, result: dict[str, Any]) -> None:
        if self._ws is None or self._ws.closed:
            return
        frame = {
            "msg_type": "result",
            "command_id": command_id,
            "status": result.get("status", "error"),
            "detail": result.get("detail", ""),
        }
        if result.get("data") is not None:
            frame["data"] = result["data"]
        await self._ws.send_str(json.dumps(frame))

    def _send_progress(self, command_id: str):
        async def _send(phase: str, percent: int | None, version_target: str, detail: str) -> None:
            if self._ws is None or self._ws.closed:
                return
            await self._ws.send_str(json.dumps({
                "msg_type": "progress",
                "command_id": command_id,
                "phase": phase,
                "percent": percent,
                "version_target": version_target,
                "detail": detail,
            }))
        return _send
