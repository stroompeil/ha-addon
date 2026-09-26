"""Tests for the critical log collector (`logs.py`).

The collector reads HA's system_log ring buffer and must never raise, no
matter how malformed the buffer or the caller's arguments are.
"""

from __future__ import annotations

import datetime

import pytest

from custom_components.stroompeil_ha_addon import logs


class FakeLogEntry:
    def __init__(self, *, level, name, message, timestamp, first_occurrence, count=1):
        self.level = level
        self.name = name
        self.message = message
        self.timestamp = timestamp
        self.first_occurrence = first_occurrence
        self.count = count


def _entry(level="ERROR", name="custom_components.test", message="boom", count=1):
    ts = datetime.datetime(2026, 9, 18, 12, 34, 56, tzinfo=datetime.timezone.utc)
    return FakeLogEntry(
        level=level, name=name, message=message,
        timestamp=ts, first_occurrence=ts, count=count,
    )


class FakeHass:
    def __init__(self, data=None):
        self.data = data if data is not None else {}


def test_reads_entries_newest_first_with_shape():
    hass = FakeHass({"system_log": {"items": [_entry(), _entry(level="CRITICAL", name="x")]}})
    out = logs.collect_critical_logs(hass)
    assert len(out) == 2
    assert out[0]["level"] == "ERROR"
    assert out[0]["logger"] == "custom_components.test"
    assert out[0]["ts"].startswith("2026-09-18")
    assert out[0]["count"] == 1


def test_filters_below_min_level():
    hass = FakeHass({"system_log": {"items": [_entry(level="WARNING"), _entry()]}})
    assert len(logs.collect_critical_logs(hass)) == 1
    assert logs.collect_critical_logs(hass, min_level="CRITICAL") == []


def test_limit_is_respected_and_clamped():
    hass = FakeHass({"system_log": {"items": [_entry() for _ in range(30)]}})
    assert len(logs.collect_critical_logs(hass, limit=5)) == 5
    assert len(logs.collect_critical_logs(hass, limit=0)) == 1
    assert len(logs.collect_critical_logs(hass, limit=-3)) == 1


def test_missing_buffer_yields_empty_list():
    assert logs.collect_critical_logs(FakeHass({})) == []
    assert logs.collect_critical_logs(FakeHass({"system_log": {}})) == []
    assert logs.collect_critical_logs(FakeHass({"system_log": None})) == []


def test_invalid_min_level_falls_back_to_error():
    hass = FakeHass({"system_log": {"items": [_entry(level="WARNING"), _entry()]}})
    assert len(logs.collect_critical_logs(hass, min_level="bogus")) == 1


def test_broken_entry_attributes_never_raise():
    class Broken:
        level = "ERROR"
        name = None
        message = None
        timestamp = object()
        first_occurrence = None
        count = "not-a-number"

    hass = FakeHass({"system_log": {"items": [Broken()]}})
    out = logs.collect_critical_logs(hass)
    assert len(out) == 1
    assert out[0]["ts"] == ""
    assert out[0]["count"] == 1
