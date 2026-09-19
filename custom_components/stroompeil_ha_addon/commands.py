"""Command allowlist and handlers.

The Stroompeil HA server pushes `{\"msg_type\": \"command\", \"command_id\": ..., \"type\": ..., \"args\": ...}`
frames. Here we map `type` to a fixed handler. Anything not in ALLOWED_COMMANDS is
rejected. This is the trust boundary on the host side.

A handler may report ongoing progress back to the server via the optional
`send_progress` coroutine passed to `dispatch_command` (ADR 0019). For long-running
commands (e.g. `integration.update`) the handler emits `progress` frames while the
work is in flight, then `dispatch_command` sends the final `result` frame.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from .const import COMMAND_TYPE_RESTART, COMMAND_TYPE_LOGS, COMMAND_TYPE_UPDATE, DOMAIN
from .logs import DEFAULT_LIMIT, DEFAULT_MIN_LEVEL, collect_critical_logs

_LOGGER = logging.getLogger(__name__)

CommandHandler = Callable[
    [Any, dict[str, Any], "SendProgress | None"],
    Awaitable[dict[str, Any]],
]
SendProgress = Callable[[str, int | None, str, str], Awaitable[None]]


async def _send_progress_noop(phase: str, percent: int | None, version_target: str, detail: str) -> None:
    return None


async def _handle_restart(hass, args: dict[str, Any], send_progress: SendProgress | None) -> dict[str, Any]:
    """Restart Home Assistant. The socket will drop; that's expected."""
    hass.async_create_task(hass.services.async_call("homeassistant", "restart"))
    return {"status": "dispatched", "detail": "restart queued"}


async def _handle_update(hass, args: dict[str, Any], send_progress: SendProgress | None) -> dict[str, Any]:
    """Update this add-on via HACS, then restart to load the new code (ADR 0019).

    Restricted to the agent's own domain. Reports progress through `send_progress`
    while the HACS install runs; the running version is reconciled server-side from
    the status frame after the restart.
    """
    send = send_progress or _send_progress_noop
    version = args.get("version") or ""

    entity_id = _find_update_entity(hass, DOMAIN)
    if not entity_id:
        return {"status": "error", "detail": "no HACS update entity for this integration"}

    state = hass.states.get(entity_id)
    if state is None:
        return {"status": "error", "detail": "update entity unavailable"}
    installed = state.attributes.get("installed_version")
    latest = state.attributes.get("latest_version")

    target = version or latest
    if target and installed == target:
        return {"status": "ok", "detail": f"already on {installed}"}

    await send("checking", None, target or "", "checking for update")
    try:
        await hass.services.async_call(
            "update", "install", {"entity_id": entity_id, **({"version": version} if version else {})},
            blocking=True,
        )
    except Exception as exc:
        _LOGGER.exception("HACS update.install failed for %s", entity_id)
        return {"status": "error", "detail": f"update.install failed: {exc}"}

    last_percent: int | None = None
    for _ in range(120):
        await asyncio.sleep(1)
        state = hass.states.get(entity_id)
        if state is None:
            break
        in_progress = bool(state.attributes.get("in_progress"))
        percent = state.attributes.get("update_percentage")
        if percent is None and in_progress:
            percent = last_percent
        if percent != last_percent or in_progress:
            await send("installing", percent, target or "", "downloading" if in_progress else "installing")
            last_percent = percent
        if not in_progress:
            break

    await send("installed", None, target or "", "HACS install complete, restarting to load new code")
    hass.async_create_task(hass.services.async_call("homeassistant", "restart"))
    return {"status": "dispatched", "detail": f"update to {target or 'latest'} installed, restart queued"}


def _find_update_entity(hass, domain: str) -> str:
    """Locate the HACS update entity for this integration's domain.

    HACS creates one update entity per repository. The entity id is not a fixed
    convention, so match by the integration's config entry device name falling back to
    a domain-based substring. Returns "" if none found.
    """
    try:
        for entity_id in hass.states.async_entity_ids("update"):
            if domain in entity_id:
                return entity_id
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("update entity lookup failed: %s", exc)
    return ""


async def _handle_logs(hass, args: dict[str, Any], send_progress: SendProgress | None) -> dict[str, Any]:
    """Read recent log entries from HA's system_log ring buffer (read-only)."""
    min_level = str(args.get("min_level") or DEFAULT_MIN_LEVEL)
    try:
        limit = int(args.get("limit") or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, 50))
    entries = collect_critical_logs(hass, min_level=min_level, limit=limit)
    return {
        "status": "ok",
        "detail": f"{len(entries)} log entries at {min_level} or higher",
        "data": {"entries": entries},
    }


ALLOWED_COMMANDS: dict[str, CommandHandler] = {
    COMMAND_TYPE_RESTART: _handle_restart,
    COMMAND_TYPE_UPDATE: _handle_update,
    COMMAND_TYPE_LOGS: _handle_logs,
}


async def dispatch_command(
    hass,
    command_id: str,
    type_: str,
    args: dict[str, Any],
    send_progress: SendProgress | None = None,
) -> dict[str, Any]:
    handler = ALLOWED_COMMANDS.get(type_)
    if handler is None:
        _LOGGER.warning("Stroompeil HA addon rejected unknown command type=%s id=%s", type_, command_id)
        return {"status": "error", "detail": f"unknown command type: {type_}"}
    try:
        return await handler(hass, args, send_progress)
    except Exception as exc:
        _LOGGER.exception("command handler failed type=%s id=%s", type_, command_id)
        return {"status": "error", "detail": str(exc)}
