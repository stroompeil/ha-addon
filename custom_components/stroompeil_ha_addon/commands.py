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

from .const import (
    COMMAND_TYPE_RESTART,
    COMMAND_TYPE_LOGS,
    COMMAND_TYPE_UPDATE,
    COMMAND_TYPE_CORE_UPDATE,
    COMMAND_TYPE_UPDATES_ALL,
    COMMAND_TYPE_ADDON_UPDATE,
    DOMAIN,
)
from .logs import DEFAULT_LIMIT, DEFAULT_MIN_LEVEL, collect_critical_logs

_LOGGER = logging.getLogger(__name__)

CommandHandler = Callable[
    [Any, dict[str, Any], "SendProgress | None"],
    Awaitable[dict[str, Any]],
]
SendProgress = Callable[[str, int | None, str, str], Awaitable[None]]


async def _send_progress_noop(phase: str, percent: int | None, version_target: str, detail: str) -> None:
    return None


def _as_int_percent(value) -> int | None:
    """Normalize HA's `update_percentage` (int | float | None) to a 0-100 int."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, min(100, round(value)))


async def _narrate_install_progress(hass, entity_id, send, target, detail, iterations) -> None:
    """Poll the update entity and emit a progress frame per change (ADR 0019/0038).

    Runs concurrently with the `update.install` service call so mid-install
    percentages reach the manager while the download is in flight. Stops when
    the entity leaves `in_progress` or the iteration budget is exhausted;
    the install task's completion is awaited by the caller regardless.

    HA's core update entity can freeze on one percentage for many minutes while
    the Supervisor installs (long download/unpack steps emit no new values).
    After `stalled_polls` consecutive polls with an unchanged percentage, the
    narration switches to indeterminate frames (`percent=None`), which the
    manager renders as a plain "in progress" line instead of a stuck "N%".
    """
    stalled_polls = 30
    last_percent: int | None = None
    last_raw_percent: int | None = None
    same_percent_polls = 0
    indeterminate = False
    for _ in range(iterations):
        await asyncio.sleep(1)
        state = hass.states.get(entity_id)
        if state is None:
            continue
        in_progress = bool(state.attributes.get("in_progress"))
        raw_percent = _as_int_percent(state.attributes.get("update_percentage"))
        if raw_percent != last_raw_percent:
            same_percent_polls = 0
            indeterminate = False
        elif in_progress:
            same_percent_polls += 1
        else:
            same_percent_polls = 0
        if same_percent_polls >= stalled_polls:
            indeterminate = True
        percent = raw_percent if not indeterminate else None
        if percent is None and in_progress and not indeterminate:
            percent = last_percent
        if percent != last_percent or in_progress:
            await send("installing", percent, target, "downloading" if in_progress else detail)
            last_percent = percent
        last_raw_percent = raw_percent
        if not in_progress:
            break


async def _install_with_progress(hass, entity_id, install_args, send, target, detail, iterations):
    """Run `update.install` while narrating its live percentage (ADR 0038).

    The service call blocks until HA finishes the install, so the narration
    poll must run as a sibling task, not after it. Returns the install
    exception (or None on success); the poll task is always cancelled and
    awaited before returning.
    """
    install_task = asyncio.ensure_future(
        hass.services.async_call("update", "install", install_args, blocking=True)
    )
    poll_task = asyncio.ensure_future(
        _narrate_install_progress(hass, entity_id, send, target, detail, iterations)
    )
    try:
        await install_task
        return None
    except Exception as exc:  # noqa: BLE001 - reported to the server as an error result
        return exc
    finally:
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass


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
    exc = await _install_with_progress(
        hass,
        entity_id,
        {"entity_id": entity_id, **({"version": version} if version else {})},
        send,
        target or "",
        "installing",
        120,
    )
    if exc is not None:
        _LOGGER.exception("HACS update.install failed for %s", entity_id)
        return {"status": "error", "detail": f"update.install failed: {exc}"}

    await send("installed", None, target or "", "HACS install complete, restarting to load new code")
    hass.async_create_task(hass.services.async_call("homeassistant", "restart"))
    return {"status": "dispatched", "detail": f"update to {target or 'latest'} installed, restart queued"}


CORE_UPDATE_ENTITY_ID = "update.home_assistant_core_update"
CORE_UPDATE_ENTITY_MATCH = "home_assistant_core"


def find_core_update_entity(hass) -> str:
    """Locate the Supervisor update entity for HA core (ADR 0028).

    The canonical id is `update.home_assistant_core_update`; fall back to a
    substring match in case of a renamed install. The entity is disabled by
    default, so absence is a normal state (non-Supervised installs have none
    at all). Returns "" if not found.
    """
    try:
        state = hass.states.get(CORE_UPDATE_ENTITY_ID)
        if state is not None:
            return CORE_UPDATE_ENTITY_ID
        for entity_id in hass.states.async_entity_ids("update"):
            if CORE_UPDATE_ENTITY_MATCH in entity_id and "supervisor" not in entity_id:
                return entity_id
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("core update entity lookup failed: %s", exc)
    return ""


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


async def _handle_core_update(hass, args: dict[str, Any], send_progress: SendProgress | None) -> dict[str, Any]:
    """Update HA core via the Supervisor update entity, then restart (ADR 0028).

    Mirrors `_handle_update`: reports progress while the Supervisor install runs,
    then queues a restart to load the new core. The restart kills our own socket,
    so the server reconciles from the status frame after reconnect. Core downloads
    are multi-minute, hence the longer poll window.
    """
    send = send_progress or _send_progress_noop
    version = args.get("version") or ""

    entity_id = find_core_update_entity(hass)
    if not entity_id:
        return {"status": "error", "detail": "no HA-core update entity (Supervisor required, or entity disabled)"}

    state = hass.states.get(entity_id)
    if state is None:
        return {"status": "error", "detail": "core update entity unavailable"}
    installed = state.attributes.get("installed_version")
    latest = state.attributes.get("latest_version")

    target = version or latest
    if target and installed == target:
        return {"status": "ok", "detail": f"already on {installed}"}

    await send("checking", None, target or "", "checking for core update")
    exc = await _install_with_progress(
        hass,
        entity_id,
        {"entity_id": entity_id, **({"version": version} if version else {})},
        send,
        target or "",
        "installing",
        1800,
    )
    if exc is not None:
        _LOGGER.exception("core update.install failed for %s", entity_id)
        return {"status": "error", "detail": f"update.install failed: {exc}"}

    await send("installed", None, target or "", "core install complete, restarting to load new version")
    hass.async_create_task(hass.services.async_call("homeassistant", "restart"))
    return {"status": "dispatched", "detail": f"core update to {target or 'latest'} installed, restart queued"}


async def _handle_updates_all(hass, args: dict[str, Any], send_progress: SendProgress | None) -> dict[str, Any]:
    """Sequentially install every pending update entity except HA core (ADR 0035).

    Fault-tolerant batch: one failing install is recorded and the batch moves
    on. The core update entity is skipped deliberately — core updates gate
    behind their own confirmation and restart (ADR 0028); a batch never
    restarts Home Assistant.
    """
    send = send_progress or _send_progress_noop
    skipped_core = find_core_update_entity(hass)
    targets: list[str] = []
    try:
        for entity_id in hass.states.async_entity_ids("update"):
            if entity_id == skipped_core:
                continue
            state = hass.states.get(entity_id)
            if state is None or state.state != "on":
                continue
            targets.append(entity_id)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("updates.all enumeration failed: %s", exc)
        return {"status": "error", "detail": f"could not enumerate update entities: {exc}"}
    if not targets:
        return {"status": "ok", "detail": "no pending add-on or integration updates"}
    installed: list[str] = []
    failed: list[dict[str, str]] = []
    for i, entity_id in enumerate(targets):
        state = hass.states.get(entity_id)
        name = (state.attributes.get("friendly_title") if state else None) or entity_id
        await send("installing", int(i * 100 / len(targets)), "", f"updating {name}")
        exc = await _install_with_progress(
            hass,
            entity_id,
            {"entity_id": entity_id},
            send,
            "",
            f"updating {name}",
            600,
        )
        if exc is not None:  # noqa: BLE001 - one failure must not stop the batch
            _LOGGER.warning("updates.all: install failed for %s: %s", entity_id, exc)
            failed.append({"entity_id": entity_id, "error": str(exc)})
            continue
        installed.append(entity_id)
    summary = f"{len(installed)} updated, {len(failed)} failed"
    data: dict[str, Any] = {"installed": installed}
    if failed:
        data["failed"] = failed
    if skipped_core:
        data["skipped_core"] = skipped_core
    await send("installed", 100, "", summary)
    status = "ok" if not failed else "error"
    return {"status": status, "detail": summary, "data": data}


def find_addon_update_entity(hass, slug: str) -> str:
    """Locate the Supervisor update entity for an add-on slug (ADR 0042).

    Supervisor names the entity after the add-on, but the id is a convention,
    not a contract, so match on the entity's title attributes first and fall
    back to a case-insensitive slug substring. Returns "" when the add-on
    has no update entity (non-Supervised install, entity disabled, unknown
    slug) — absence is a normal state, never an exception.
    """
    if not slug:
        return ""
    wanted = slug.lower()
    try:
        for entity_id in hass.states.async_entity_ids("update"):
            state = hass.states.get(entity_id)
            if state is None:
                continue
            title = (
                state.attributes.get("friendly_title")
                or state.attributes.get("title")
                or ""
            )
            if title and title.lower() == wanted:
                return entity_id
        for entity_id in hass.states.async_entity_ids("update"):
            if wanted in entity_id.lower():
                return entity_id
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("addon update entity lookup failed: %s", exc)
    return ""


async def _handle_addon_update(hass, args: dict[str, Any], send_progress: SendProgress | None) -> dict[str, Any]:
    """Update one Supervisor add-on via its update entity (ADR 0042).

    The add-on container restarts inside Supervisor; HA core keeps running,
    so the agent socket survives and the normal result frame closes the
    command. No HA restart is ever queued here.
    """
    send = send_progress or _send_progress_noop
    slug = str(args.get("slug") or "")
    version = args.get("version") or ""

    entity_id = find_addon_update_entity(hass, slug)
    if not entity_id:
        return {
            "status": "error",
            "detail": f"no update entity for add-on '{slug}' (Supervisor required, entity disabled, or unknown slug)",
        }

    state = hass.states.get(entity_id)
    if state is None:
        return {"status": "error", "detail": "update entity unavailable"}
    installed = state.attributes.get("installed_version")
    latest = state.attributes.get("latest_version")

    target = version or latest
    if target and installed == target:
        return {"status": "ok", "detail": f"{slug} already on {installed}"}

    await send("checking", None, target or "", f"checking for {slug} update")
    exc = await _install_with_progress(
        hass,
        entity_id,
        {"entity_id": entity_id, **({"version": version} if version else {})},
        send,
        target or "",
        f"updating {slug}",
        600,
    )
    if exc is not None:
        _LOGGER.warning("addon update.install failed for %s: %s", entity_id, exc)
        return {"status": "error", "detail": f"update.install failed: {exc}"}

    await send("installed", None, target or "", f"{slug} install complete")
    return {
        "status": "ok",
        "detail": f"{slug} updated to {target or 'latest'}",
        "data": {"slug": slug, "entity_id": entity_id, "version": str(target or "")},
    }


ALLOWED_COMMANDS: dict[str, CommandHandler] = {
    COMMAND_TYPE_RESTART: _handle_restart,
    COMMAND_TYPE_UPDATE: _handle_update,
    COMMAND_TYPE_CORE_UPDATE: _handle_core_update,
    COMMAND_TYPE_LOGS: _handle_logs,
    COMMAND_TYPE_UPDATES_ALL: _handle_updates_all,
    COMMAND_TYPE_ADDON_UPDATE: _handle_addon_update,
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
