"""Critical log collection.

Reads Home Assistant's `system_log` component in-memory ring buffer and returns
the most recent entries at or above a severity threshold. This works while the
frontend is dead (the buffer is a core component, not a frontend feature), which
is what makes log access possible over the server connection alone.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}

DEFAULT_MIN_LEVEL = "ERROR"
DEFAULT_LIMIT = 20
STATUS_LIMIT = 10


def _buffer_entries(hass) -> list[dict[str, Any]]:
    """Return the raw system_log ring buffer entries, newest first.

    The buffer lives in `hass.data["system_log"]["items"]` as a deque. Any
    failure to read it yields an empty list; this must never raise.
    """
    try:
        data = hass.data.get("system_log")
        if not data:
            return []
        items = data.get("items")
        if items is None:
            return []
        return list(items)
    except Exception as exc:  # noqa: BLE001 - never raise out of log collection
        _LOGGER.debug("system_log buffer read failed: %s", exc)
        return []


def _entry_ts(entry: Any) -> str:
    timestamp = getattr(entry, "timestamp", None)
    if timestamp is None:
        return ""
    try:
        return timestamp.isoformat()
    except Exception:  # noqa: BLE001
        return ""


def _entry_first_ts(entry: Any) -> str:
    first_ts = getattr(entry, "first_occurrence", None)
    if first_ts is None:
        return ""
    try:
        return first_ts.isoformat()
    except Exception:  # noqa: BLE001
        return ""


def _entry_count(entry: Any) -> int:
    try:
        return int(getattr(entry, "count", 1) or 1)
    except (TypeError, ValueError):
        return 1


def _shaped(entry: Any) -> dict[str, Any]:
    return {
        "ts": _entry_ts(entry),
        "level": str(getattr(entry, "level", "")),
        "logger": str(getattr(entry, "name", "")),
        "message": str(getattr(entry, "message", "")),
        "first_occurred_ts": _entry_first_ts(entry),
        "count": _entry_count(entry),
    }


def _clamp_min_level(min_level: str) -> str:
    level = (min_level or "").upper()
    if level not in _LEVEL_ORDER:
        return DEFAULT_MIN_LEVEL
    return level


def collect_critical_logs(
    hass, min_level: str = DEFAULT_MIN_LEVEL, limit: int = STATUS_LIMIT
) -> list[dict[str, Any]]:
    """Return recent system_log entries at or above `min_level`, newest first.

    `limit` is clamped to at least 1. Never raises.
    """
    threshold = _LEVEL_ORDER[_clamp_min_level(min_level)]
    limit = max(1, int(limit))
    out: list[dict[str, Any]] = []
    for entry in _buffer_entries(hass):
        level = str(getattr(entry, "level", "")).upper()
        if _LEVEL_ORDER.get(level, 0) < threshold:
            continue
        out.append(_shaped(entry))
        if len(out) >= limit:
            break
    return out
