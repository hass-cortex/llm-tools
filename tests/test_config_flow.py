"""Tests for the config flow."""

from __future__ import annotations

from conftest import DOMAIN, LlmToolsConfigFlow


class TestConfigFlow:
    """Adding the integration from the UI."""

    async def test_shows_a_confirmation_form(self) -> None:
        """The first step asks for confirmation; there is nothing to fill in."""
        flow = LlmToolsConfigFlow()

        result = await flow.async_step_user()

        assert result["type"] == "form"
        assert result["step_id"] == "user"
        assert result["data_schema"] is None

    async def test_creates_an_empty_entry(self) -> None:
        """Confirming creates an entry that stores no configuration."""
        flow = LlmToolsConfigFlow()

        result = await flow.async_step_user({})

        assert result["type"] == "create_entry"
        assert result["title"] == "LLM Tools"
        assert result["data"] == {}

    def test_flow_is_bound_to_the_domain(self) -> None:
        """The flow handler registers under the integration's domain."""
        assert LlmToolsConfigFlow._domain == DOMAIN
