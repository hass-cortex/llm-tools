"""Diagnostics support for LLM Tools."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import tool_registry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return the LLM APIs visible to this integration.

    This is the same view `list_apis` returns, under the default context, which
    is what makes a "the tool is not found" report answerable without asking the
    reporter to run a service by hand.
    """
    llm_context = tool_registry.build_llm_context(hass)
    return {
        "title": entry.title,
        "llm_context": {
            "platform": llm_context.platform,
            "language": llm_context.language,
            "assistant": llm_context.assistant,
        },
        "apis": await tool_registry.async_list_apis(hass, llm_context),
    }
