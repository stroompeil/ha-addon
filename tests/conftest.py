"""Test configuration.

The Stroompeil HA addon is a Home Assistant custom integration that imports the
`homeassistant` package at runtime. Installing the full Home Assistant test
suite in CI is heavy and slow, so this conftest installs minimal in-memory stub
modules for the handful of `homeassistant` symbols the integration touches.
Real third-party packages (`aiohttp`, `voluptuous`) are installed normally and
used as-is.

The stubs exist purely so the integration modules can be imported; tests then
exercise the integration's own logic with lightweight fakes.
"""
from __future__ import annotations

import sys
import types
from enum import Enum


def _install_module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _install_package(name: str) -> types.ModuleType:
    pkg = _install_module(name)
    pkg.__path__ = []
    return pkg


def _install_ha_stubs() -> None:
    ha = _install_package("homeassistant")

    const = _install_module("homeassistant.const")
    const.RESTART_EXIT_CODE = 100
    const.__version__ = "2026.1.0"

    core = _install_module("homeassistant.core")
    core.HomeAssistant = object  # type: ignore[attr-defined]

    config_entries = _install_module("homeassistant.config_entries")

    class ConfigEntry:  # minimal stand-in used only for type hints
        pass

    class ConfigFlow:
        """Minimal base for config flows. Methods are overridable in tests."""

        def __init_subclass__(cls, *, domain=None, **_):
            cls._domain = domain
            super().__init_subclass__()

        VERSION = 1
        hass = None

        def async_show_form(self, *, step_id, data_schema=None, errors=None, **_):
            return {"type": "form", "step_id": step_id, "data_schema": data_schema, "errors": errors or {}}

        async def async_set_unique_id(self, unique_id):
            self._unique_id = unique_id
            return None

        def _abort_if_unique_id_configured(self):
            return None

        def async_create_entry(self, *, title, data):
            return {"type": "create_entry", "title": title, "data": data}

        def async_abort(self, *, reason):
            return {"type": "abort", "reason": reason}

    config_entries.ConfigEntry = ConfigEntry  # type: ignore[attr-defined]
    config_entries.ConfigFlow = ConfigFlow  # type: ignore[attr-defined]

    helpers = _install_package("homeassistant.helpers")

    issue_registry = _install_module("homeassistant.helpers.issue_registry")

    class IssueSeverity(Enum):
        WARNING = "warning"
        ERROR = "error"

    created: list[dict] = []
    deleted: list[tuple] = []

    async def async_create_issue(**kwargs):
        created.append(kwargs)
        return None

    async def async_delete_issue(hass, domain, issue_id):
        deleted.append((domain, issue_id))
        return None

    issue_registry.IssueSeverity = IssueSeverity  # type: ignore[attr-defined]
    issue_registry.async_create_issue = async_create_issue  # type: ignore[attr-defined]
    issue_registry.async_delete_issue = async_delete_issue  # type: ignore[attr-defined]
    issue_registry.created = created  # type: ignore[attr-defined]
    issue_registry.deleted = deleted  # type: ignore[attr-defined]

    system_info = _install_module("homeassistant.helpers.system_info")

    async def async_get_system_info(hass):
        return {"updates": []}

    system_info.async_get_system_info = async_get_system_info  # type: ignore[attr-defined]

    entity_platform = _install_module("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object  # type: ignore[attr-defined]

    components = _install_package("homeassistant.components")
    binary_sensor = _install_module("homeassistant.components.binary_sensor")

    class BinarySensorDeviceClass(Enum):
        CONNECTIVITY = "connectivity"

    class BinarySensorEntity:
        _attr_device_class = None
        _attr_has_entity_name = False
        _attr_is_on = None
        _attr_unique_id = None

        def __init__(self, *args, **kwargs):
            self.hass = None

        async def async_added_to_hass(self):
            return None

        def async_write_ha_state(self):
            return None

    binary_sensor.BinarySensorDeviceClass = BinarySensorDeviceClass  # type: ignore[attr-defined]
    binary_sensor.BinarySensorEntity = BinarySensorEntity  # type: ignore[attr-defined]


_install_ha_stubs()
