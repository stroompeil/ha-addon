"""Status snapshot collector.

Builds the JSON `status` frame sent to the Stroompeil HA server every 60s. The shape
MUST match the server's `StatusFrame`.
"""
from __future__ import annotations

from typing import Any

import homeassistant.helpers.system_info as system_info


async def collect_status(hass) -> dict[str, Any]:
    info = await system_info.async_get_system_info(hass)
    return {
        "msg_type": "status",
        "host_name": hass.config.location_name or "",
        "ha_version": hass.config.version,
        "uptime_seconds": _uptime_seconds(hass),
        "entity_count": len(hass.states.async_entity_ids()),
        "cpu_load": _cpu_load(),
        "integrations": sorted(hass.data.get("custom_components", {}).keys()),
        "available_updates": list(info.get("updates", [])) if isinstance(info, dict) else [],
        "extra": {},
    }


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
