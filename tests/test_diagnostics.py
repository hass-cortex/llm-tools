"""Tests for diagnostics."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from conftest import StubTool, async_get_config_entry_diagnostics


class TestDiagnostics:
    """What a bug report carries."""

    async def test_reports_apis_and_context(
        self, mock_hass: MagicMock, register_api: Any
    ) -> None:
        """The visible APIs and the context they were read under are included."""
        register_api("assist", "Assist", [StubTool("HassTurnOn")])
        entry = MagicMock()
        entry.title = "LLM Tools"

        diagnostics = await async_get_config_entry_diagnostics(mock_hass, entry)

        assert diagnostics["title"] == "LLM Tools"
        assert diagnostics["llm_context"] == {
            "platform": "llm_tools",
            "language": "en",
            "assistant": "conversation",
        }
        assert diagnostics["apis"] == [
            {"id": "assist", "name": "Assist", "tool_count": 1}
        ]

    async def test_survives_a_broken_api(
        self, mock_hass: MagicMock, register_api: Any
    ) -> None:
        """A provider that raises is reported instead of failing diagnostics."""
        register_api("broken", "Broken", error=RuntimeError("provider exploded"))

        diagnostics = await async_get_config_entry_diagnostics(mock_hass, MagicMock())

        assert diagnostics["apis"][0]["error"] == "provider exploded"
