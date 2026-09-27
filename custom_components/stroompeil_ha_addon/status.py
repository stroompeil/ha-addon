"""Status snapshot collector.

Builds the JSON `status` frame sent to the Stroompeil HA server every 60s. The shape
MUST match the server's `StatusFrame`.
"""
from __future__ import annotations

import logging
import shutil
from typing import Any

from .logs import collect_critical_logs

_MEMINFO_PATH = "/proc/meminfo"
_STAT_PATH = "/proc/stat"

_LOGGER = logging.getLogger(__name__)

_last_cpu_times: tuple[float, float] | None = None


async def collect_status(hass) -> dict[str, Any]:
    running, installed = await _addon_versions(hass)
    return {
        "msg_type": "status",
        "host_name": hass.config.location_name or "",
        "ha_version": _ha_version(hass),
        "uptime_seconds": _uptime_seconds(hass),
        "entity_count": len(hass.states.async_entity_ids()),
        "cpu_percent": _cpu_percent(),
        "load_avg_1m": _load_avg_1m(),
        "cpu_count": _cpu_count(),
        "ram_used_percent": _ram_used_percent(),
        "ram_total_mb": _ram_total_mb(),
        "disk_used_percent": _disk_used_percent(hass),
        "integrations": sorted(hass.data.get("custom_components", {}).keys()),
        "addons": _addons(hass),
        "automation_count": _domain_count(hass, "automation"),
        "dashboard_count": _dashboard_count(hass),
        "available_updates": _available_updates(hass),
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


def _cpu_percent() -> float | None:
    """Host CPU utilization percent between two /proc/stat reads (ADR 0040).

    Reads the aggregate `cpu` line once on the first status frame and again
    on the next, reporting the delta — the same approach HA's own system
    monitor uses. None until a second sample exists (a missing reading is
    data, not 0) and on non-Linux hosts; never raises.
    """
    global _last_cpu_times
    try:
        with open(_STAT_PATH, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("cpu "):
                    parts = [float(v) for v in line.split()[1:]]
                    total = sum(parts)
                    idle = parts[3] + (parts[4] if len(parts) > 4 else 0.0)
                    break
            else:
                return None
        previous = _last_cpu_times
        _last_cpu_times = (total, idle)
        if previous is None or total <= previous[0]:
            return None
        return round((1.0 - (idle - previous[1]) / (total - previous[0])) * 100.0, 1)
    except Exception as exc:  # noqa: BLE001 - never raise out of status collection
        _LOGGER.debug("cpu percent read failed: %s", exc)
        return None


def _load_avg_1m() -> float:
    try:
        import os

        return round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else 0.0
    except Exception:
        return 0.0


def _cpu_count() -> int:
    try:
        import os

        return os.cpu_count() or 0
    except Exception:
        return 0


def _ram_used_percent() -> float:
    """Host memory in use, percent, from /proc/meminfo.

    MemAvailable is the honest "could be given to a process without swapping"
    figure, matching what HA's own system monitor reports. Reading /proc
    directly works on every Linux install type (OS, Supervised, Container,
    Core in a venv) with no Supervisor round-trip. Best-effort: 0.0 on
    non-Linux hosts or a missing/unparseable file; never raises.
    """
    try:
        with open(_MEMINFO_PATH, encoding="utf-8") as fh:
            fields: dict[str, float] = {}
            for line in fh:
                key, _, rest = line.partition(":")
                value = rest.split()[0] if rest.split() else "0"
                fields[key.strip()] = float(value)
        total = fields.get("MemTotal", 0.0)
        available = fields.get("MemAvailable", fields.get("MemFree", 0.0))
        if total <= 0:
            return 0.0
        return round((total - available) / total * 100.0, 1)
    except Exception as exc:  # noqa: BLE001 - never raise out of status collection
        _LOGGER.debug("meminfo read failed: %s", exc)
        return 0.0


def _ram_total_mb() -> float:
    """Host memory total in MB from /proc/meminfo (kB lines / 1024).

    Best-effort: 0.0 on failure; never raises.
    """
    try:
        with open(_MEMINFO_PATH, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return round(float(line.split()[1]) / 1024.0, 1)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("meminfo read failed: %s", exc)
    return 0.0


def _disk_used_percent(hass) -> float:
    """Data-disk usage percent via shutil.disk_usage on the config directory.

    The config directory is where recorder data, backups, and the SQLite DB
    live — the disk whose exhaustion takes the host down. Works on every
    Linux install type with no Supervisor round-trip. Best-effort: 0.0 on
    failure; never raises.
    """
    try:
        config_dir = getattr(getattr(hass, "config", None), "config_dir", None)
        if not config_dir:
            return 0.0
        usage = shutil.disk_usage(config_dir)
        if usage.total <= 0:
            return 0.0
        return round(usage.used / usage.total * 100.0, 1)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("disk usage read failed: %s", exc)
        return 0.0


def _available_updates(hass) -> list[str]:
    """Pending update labels from update entities (ADR 0028's `"core"` contract).

    `"core"` is reported when the Supervisor's core update entity exists and
    offers a newer version. HACS repository entities are best-effort extras
    and not part of the reconciliation contract. Best-effort; never raises.
    """
    updates: list[str] = []
    try:
        from .commands import find_core_update_entity

        entity_id = find_core_update_entity(hass)
        if entity_id:
            state = hass.states.get(entity_id)
            if state is not None:
                installed = str(state.attributes.get("installed_version") or "")
                latest = str(state.attributes.get("latest_version") or "")
                if latest and latest != installed:
                    updates.append("core")
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("available updates lookup failed: %s", exc)
    return updates


def _addons(hass) -> list[dict[str, Any]]:
    """Installed Supervisor add-ons from the hassio integration's cached
    supervisor info (ADR 0033).

    `get_supervisor_info` reads an in-process cache maintained by the hassio
    integration — no per-add-on Supervisor round-trip. Absent on non-Supervised
    installs; the import lives inside the function so those hosts report [].
    """
    try:
        from homeassistant.components.hassio import get_supervisor_info

        info = get_supervisor_info(hass)
        addons = info.get("addons") if isinstance(info, dict) else None
        if not isinstance(addons, list):
            return []
        return [
            {
                "slug": str(a.get("slug") or ""),
                "name": str(a.get("name") or a.get("slug") or ""),
                "version": str(a.get("version") or ""),
                "state": str(a.get("state") or ""),
                "update_available": bool(a.get("update_available")),
            }
            for a in addons
            if isinstance(a, dict)
        ]
    except Exception as exc:  # noqa: BLE001 - never raise out of status collection
        _LOGGER.debug("addon inventory unavailable: %s", exc)
        return []


def _domain_count(hass, domain: str) -> int:
    try:
        return len(hass.states.async_entity_ids(domain))
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("%s count unavailable: %s", domain, exc)
        return 0


def _dashboard_count(hass) -> int:
    """Lovelace dashboards from the lovelace integration's view set (ADR 0033).

    Best-effort: 0 when the lovelace data is absent or shaped unexpectedly
    (e.g. older/newer HA internals); the count is display context, not a
    health signal.
    """
    try:
        view_set = hass.data.get("lovelace")
        dashboards = getattr(view_set, "dashboards", None)
        return len(dashboards) if isinstance(dashboards, dict) else 0
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("dashboard count unavailable: %s", exc)
        return 0
