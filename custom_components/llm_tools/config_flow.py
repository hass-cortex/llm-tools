"""Config flow for the LLM Tools integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN

TITLE = "LLM Tools"


class LlmToolsConfigFlow(ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Handle the config flow.

    There is nothing to configure: the tools come from whichever integrations
    have registered an LLM API. The flow exists so the integration installs,
    appears and is removed like any other, which `single_config_entry` in the
    manifest reduces to a single confirmation step.
    """

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm adding the integration."""
        if user_input is None:
            return self.async_show_form(step_id="user")
        return self.async_create_entry(title=TITLE, data={})
