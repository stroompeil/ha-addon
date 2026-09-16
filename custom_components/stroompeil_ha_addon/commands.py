"""Command allowlist and handlers.

The Stroompeil HA server pushes `{"msg_type": "command", "command_id": ..., "type": ..., "args": ...}`
frames. Here we map `type` to a fixed handler. Anything not in ALLOWED_COMMANDS is
rejected. This is the trust boundary on the host side.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Awaitable

from .const import COMMAND_TYPE_RESTART

_LOGGER = logging.getLogger(__name__)

CommandHandler = Callable[[Any, dict[str, Any]], Awaitable[dict[str, Any]]]


async def _handle_restart(hass, args: dict[str, Any]) -> dict[str, Any]:
    """Restart Home Assistant. The socket will drop; that's expected."""
    hass.async_create_task(hass.services.async_call("homeassistant", "restart"))
    return {"status": "dispatched", "detail": "restart queued"}


ALLOWED_COMMANDS: dict[str, CommandHandler] = {
    COMMAND_TYPE_RESTART: _handle_restart,
}


async def dispatch_command(hass, command_id: str, type_: str, args: dict[str, Any]) -> dict[str, Any]:
    handler = ALLOWED_COMMANDS.get(type_)
    if handler is None:
        _LOGGER.warning("Stroompeil HA addon rejected unknown command type=%s id=%s", type_, command_id)
        return {"status": "error", "detail": f"unknown command type: {type_}"}
    try:
        return await handler(hass, args)
    except Exception as exc:
        _LOGGER.exception("command handler failed type=%s id=%s", type_, command_id)
        return {"status": "error", "detail": str(exc)}
