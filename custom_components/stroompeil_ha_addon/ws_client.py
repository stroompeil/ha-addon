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
)

_LOGGER = logging.getLogger(__name__)


class StroompeilHAAddonWSClient:
    def __init__(
        self,
        hass,
        server_url: str,
        token: str,
        status_interval: int = DEFAULT_STATUS_INTERVAL_SECONDS,
    ) -> None:
        self._hass = hass
        self._server_url = server_url.rstrip("/")
        self._token = token
        self._status_interval = status_interval
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._stop = asyncio.Event()

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
        try:
            async with self._session.ws_connect(self._agent_url(), heartbeat=30) as ws:
                self._ws = ws
                _LOGGER.info("connected to Stroompeil HA server")
                receiver = asyncio.create_task(self._receive_loop(ws))
                while not self._stop.is_set() and not ws.closed:
                    await self._send_status()
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=self._status_interval)
                    except asyncio.TimeoutError:
                        pass
                if not receiver.done():
                    receiver.cancel()
        finally:
            self._ws = None

    async def _send_status(self) -> None:
        if self._ws is None or self._ws.closed:
            return
        payload = await status_mod.collect_status(self._hass)
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
        if frame.get("msg_type") != "command":
            return
        command_id = frame.get("command_id", "")
        type_ = frame.get("type", "")
        args = frame.get("args", {}) or {}
        result = await dispatch_command(self._hass, command_id, type_, args)
        await self._send_result(command_id, result)

    async def _send_result(self, command_id: str, result: dict[str, Any]) -> None:
        if self._ws is None or self._ws.closed:
            return
        await self._ws.send_str(json.dumps({
            "msg_type": "result",
            "command_id": command_id,
            "status": result.get("status", "error"),
            "detail": result.get("detail", ""),
        }))
