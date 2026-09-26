"""Status snapshot collector.

Builds the JSON `status` frame sent to the Stroompeil HA server every 60s. The shape
MUST match the server's `StatusFrame`.
"""
from __future__ import annotations

import logging
from typing import Any

import homeassistant.helpers.system_info as system_info

from .logs import collect_critical_logs

_LOGGER = logging.getLogger(__name__)


async def collect_status(hass) -> dict[str, Any]:
    info = await system_info.async_get_system_info(hass)
    running, installed = await _addon_versions(hass)
    return {
        "msg_type": "status",
        "host_name": hass.config.location_name or "",
        "ha_version": _ha_version(hass),
        "uptime_seconds": _uptime_seconds(hass),
        "entity_count": len(hass.states.async_entity_ids()),
        "cpu_load": _cpu_load(),
        "integrations": sorted(hass.data.get("custom_components", {}).keys()),
        "available_updates": list(info.get("updates", [])) if isinstance(info, dict) else [],
        "ha_latest_version": _ha_latest_version(hass),
        "addon_running_version": running,
        "addon_installed_version": installed,
        "critical_logs": collect_critical_logs(hass),
        "extra": {},
    }


def _ha_latest_version(hass) -> str:
    """Latest HA-core version from the Supervisor update entity (ADR 0028).

    Best-effort: "" when the entity is absent or disabled (non-Supervised
    installs) or the attribute is missing. Never raises.
    """
    from .commands import find_core_update_entity

    try:
        entity_id = find_core_update_entity(hass)
        if not entity_id:
            return ""
        state = hass.states.get(entity_id)
        if state is None:
            return ""
        return str(state.attributes.get("latest_version") or "")
    except Exception as exc:  # noqa: BLE001 - never raise out of status collection
        _LOGGER.debug("ha latest version lookup failed: %s", exc)
        return ""


async def _addon_versions(hass) -> tuple[str, str]:
    """Return (running, installed) versions of this add-on (ADR 0019).

    `running` is the version HA loaded at boot, read from the loader's cached
    Integration object. `installed` is what's on disk right now, read from the
    manifest file. They differ when an update is staged but not yet restarted.
    Both are best-effort and return "" on failure.
    """
    from .const import DOMAIN

    running = await _running_version(hass, DOMAIN)
    installed = _disk_version(hass, DOMAIN)
    return running, installed


async def _running_version(hass, domain: str) -> str:
    try:
        from homeassistant.loader import async_get_custom_components

        comps = await async_get_custom_components(hass)
        integration = comps.get(domain)
        if integration is not None and integration.version is not None:
            return str(integration.version)
    except Exception as exc:  # noqa: BLE001 - never raise out of status collection
        _LOGGER.debug("running version lookup failed for %s: %s", domain, exc)
    return ""


def _disk_version(hass, domain: str) -> str:
    import json as _json
    import os

    config_dir = getattr(getattr(hass, "config", None), "config_dir", None)
    if not config_dir:
        return ""
    manifest_path = os.path.join(config_dir, "custom_components", domain, "manifest.json")
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            return str(_json.load(fh).get("version", ""))
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("disk version read failed for %s: %s", domain, exc)
    return ""


def _ha_version(hass) -> str:
    version = getattr(hass.config, "version", None)
    if version is not None:
        return str(version)
    from homeassistant.const import __version__
    return __version__


def _uptime_seconds(hass) -> int:
    started = getattr(hass, "started_at", None)
    if started is None:
        return 0
    import time
    from datetime import datetime

    now = datetime.fromtimestamp(time.time(), tz=started.tzinfo) if started.tzinfo else datetime.fromtimestamp(time.time())
    return int((now - started).total_seconds())


def _cpu_load() -> float:
    try:
        import os

        return round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else 0.0
    except Exception:
        return 0.0
