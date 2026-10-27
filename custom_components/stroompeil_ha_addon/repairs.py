"""Repair flows for the Stroompeil HA addon integration."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_SERVER_URL, CONF_TOKEN

from .config_flow import _token_schema


class UnregisteredRepairFlow(RepairsFlow):
    """Re-register a host the Stroompeil server no longer recognizes."""

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self.data["entry_id"])
            if entry is None:
                return self.async_abort(reason="entry_gone")
            self.hass.config_entries.async_update_entry(
                entry,
                data={
                    **entry.data,
                    CONF_SERVER_URL: user_input[CONF_SERVER_URL],
                    CONF_TOKEN: user_input[CONF_TOKEN],
                },
            )
            await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="init",
            data_schema=_token_schema(""),
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the flow that fixes an issue."""
    if issue_id.startswith("unregistered_"):
        return UnregisteredRepairFlow()
    raise ValueError(f"Unknown repair issue: {issue_id}")
