"""Tests for the tool registry."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
import voluptuous as vol
from conftest import (
    DEFAULT_ASSISTANT,
    DOMAIN,
    StubTool,
    tool_registry,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from voluptuous_openapi import UNSUPPORTED

# =============================================================================
# build_llm_context
# =============================================================================


class TestBuildLlmContext:
    """The context every tool is prepared and executed under."""

    def test_defaults(self, mock_hass: MagicMock) -> None:
        """Platform is the domain, language the instance language, assistant conversation."""
        context = tool_registry.build_llm_context(mock_hass)

        assert context.platform == DOMAIN
        assert context.context is None
        assert context.language == "en"
        assert context.assistant == "conversation"
        assert context.device_id is None

    def test_default_assistant_is_conversation(self) -> None:
        """The Assist API reads this value to decide which entities are exposed."""
        assert DEFAULT_ASSISTANT == "conversation"

    def test_every_field_is_overridable(self, mock_hass: MagicMock) -> None:
        """Caller-supplied values win over the defaults."""
        call_context = object()

        context = tool_registry.build_llm_context(
            mock_hass,
            context=call_context,
            language="zh-Hant",
            assistant="my_assistant",
            device_id="device-1",
        )

        assert context.context is call_context
        assert context.language == "zh-Hant"
        assert context.assistant == "my_assistant"
        assert context.device_id == "device-1"

    def test_empty_language_falls_back(self, mock_hass: MagicMock) -> None:
        """An empty language is treated as "not given", not as a language."""
        mock_hass.config.language = "de"

        assert tool_registry.build_llm_context(mock_hass, language="").language == "de"


# =============================================================================
# format_tool — schema serialisation
# =============================================================================


class TestSchemaSerializerDispatch:
    """Choosing a serializer by the schema in hand, not by what is installed.

    Home Assistant is mid-migration from voluptuous to probatio. 2026.9 hands
    out `voluptuous.Schema`; the 2026.10 branch hands out probatio's. Neither
    serializer accepts the other's schema, and both packages are declared, so
    the choice cannot be made at import time.
    """

    def test_voluptuous_schema_is_serialized(self) -> None:
        """A voluptuous schema round-trips through voluptuous-openapi."""
        described = tool_registry.format_tool(
            StubTool("Vol", parameters=vol.Schema({vol.Required("name"): str})), None
        )

        assert described["parameters"]["properties"] == {"name": {"type": "string"}}

    def test_probatio_schema_is_serialized(self) -> None:
        """A probatio schema round-trips through probatio.

        voluptuous-openapi cannot read it, so a passing assertion here proves
        the dispatch picked probatio rather than falling back to empty
        parameters.
        """
        probatio = pytest.importorskip("probatio")
        schema = probatio.Schema({probatio.Required("name"): str})

        described = tool_registry.format_tool(StubTool("Prob", parameters=schema), None)

        assert described["parameters"]["properties"] == {"name": {"type": "string"}}

    def test_probatio_schema_is_tried_first(self) -> None:
        """Dispatch order follows the schema's module."""
        probatio = pytest.importorskip("probatio")
        ordered = tool_registry._serializers_for(
            type(probatio.Schema({probatio.Required("a"): str}))
        )

        assert ordered[0] is tool_registry._probatio_to_openapi

    def test_voluptuous_schema_is_tried_first(self) -> None:
        """The reverse: a voluptuous schema puts voluptuous-openapi first."""
        ordered = tool_registry._serializers_for(type(vol.Schema({})))

        assert ordered[0] is tool_registry._vol_openapi_convert


class TestFormatTool:
    """Rendering a tool's voluptuous schema as a JSON schema."""

    def test_empty_schema(self) -> None:
        """A tool with no parameters still reports an object schema."""
        described = tool_registry.format_tool(StubTool("NoArgs"), None)

        assert described["name"] == "NoArgs"
        assert described["description"] == ""
        assert described["parameters"] == {
            "type": "object",
            "properties": {},
            "required": [],
        }

    def test_description_is_reported(self) -> None:
        """The description an LLM would see is passed through."""
        tool = StubTool("Described", description="Turns something on")

        assert tool_registry.format_tool(tool, None)["description"] == (
            "Turns something on"
        )

    def test_scalar_and_required_markers(self) -> None:
        """Required and optional markers land in `required`."""
        tool = StubTool(
            "Mixed",
            parameters=vol.Schema(
                {
                    vol.Required("name"): str,
                    vol.Optional("count"): int,
                }
            ),
        )

        parameters = tool_registry.format_tool(tool, None)["parameters"]

        assert parameters["properties"] == {
            "name": {"type": "string"},
            "count": {"type": "integer"},
        }
        assert parameters["required"] == ["name"]

    def test_ranges_and_lists(self) -> None:
        """`vol.All`/`vol.Range` and list schemas convert to JSON schema keywords."""
        tool = StubTool(
            "Ranged",
            parameters=vol.Schema(
                {
                    vol.Optional("brightness"): vol.All(int, vol.Range(min=0, max=100)),
                    vol.Optional("domain"): [str],
                }
            ),
        )

        properties = tool_registry.format_tool(tool, None)["parameters"]["properties"]

        assert properties["brightness"] == {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
        }
        assert properties["domain"] == {"type": "array", "items": {"type": "string"}}

    def test_enum_schema(self) -> None:
        """`vol.In` becomes an enum."""
        tool = StubTool(
            "Enumerated",
            parameters=vol.Schema({vol.Required("mode"): vol.In(["heat", "cool"])}),
        )

        properties = tool_registry.format_tool(tool, None)["parameters"]["properties"]

        assert properties["mode"]["enum"] == ["heat", "cool"]

    def test_nested_schema(self) -> None:
        """A nested mapping converts recursively."""
        tool = StubTool(
            "Nested",
            parameters=vol.Schema(
                {vol.Required("target"): {vol.Optional("entity_id"): str}}
            ),
        )

        properties = tool_registry.format_tool(tool, None)["parameters"]["properties"]

        assert properties["target"]["type"] == "object"
        assert properties["target"]["properties"] == {"entity_id": {"type": "string"}}

    def test_custom_serializer_is_used(self) -> None:
        """The API instance's serializer resolves what plain voluptuous cannot.

        Home Assistant selectors reach `convert` as opaque validators; the
        serializer is the only thing that can describe them. Its contract is the
        `UNSUPPORTED` sentinel, not `None` — returning `None` would make the
        whole schema serialise as null.
        """

        class Selector:
            def __call__(self, value: Any) -> Any:
                return value

        selector = Selector()

        def serializer(schema: Any) -> Any:
            if isinstance(schema, Selector):
                return {"type": "string", "enum": ["kitchen", "bedroom"]}
            return UNSUPPORTED

        tool = StubTool(
            "Selected", parameters=vol.Schema({vol.Required("area"): selector})
        )

        without = tool_registry.format_tool(tool, None)["parameters"]
        with_serializer = tool_registry.format_tool(tool, serializer)["parameters"]

        assert without["properties"]["area"] == {"type": "string"}
        assert with_serializer["properties"]["area"] == {
            "type": "string",
            "enum": ["kitchen", "bedroom"],
        }

    def test_unconvertible_schema_degrades(self) -> None:
        """One tool with a broken schema must not break the whole listing."""

        class Exploding:
            def __call__(self, value: Any) -> Any:
                return value

        def serializer(schema: Any) -> Any:
            raise RuntimeError("cannot serialise")

        tool = StubTool(
            "Broken", parameters=vol.Schema({vol.Required("x"): Exploding()})
        )

        described = tool_registry.format_tool(tool, serializer)

        assert described["parameters"] == {"type": "object", "properties": {}}
        assert "cannot serialise" in described["error"]


# =============================================================================
# async_list_apis
# =============================================================================


class TestListApis:
    """Enumerating the registered LLM APIs."""

    async def test_empty_registry(self, mock_hass: MagicMock, llm_context: Any) -> None:
        """No registered API yields an empty list, not an error."""
        assert await tool_registry.async_list_apis(mock_hass, llm_context) == []

    async def test_lists_id_name_and_tool_count(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """Every registered API is reported with its tool count."""
        register_api("assist", "Assist", [StubTool("HassTurnOn"), StubTool("GetState")])
        register_api("notes", "Notes", [StubTool("AddNote")])

        listed = await tool_registry.async_list_apis(mock_hass, llm_context)

        assert listed == [
            {"id": "assist", "name": "Assist", "tool_count": 2},
            {"id": "notes", "name": "Notes", "tool_count": 1},
        ]

    async def test_broken_api_is_still_listed(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """A provider that raises is reported, not allowed to hide the others."""
        register_api("assist", "Assist", [StubTool("HassTurnOn")])
        register_api("broken", "Broken", error=RuntimeError("provider exploded"))

        listed = await tool_registry.async_list_apis(mock_hass, llm_context)

        assert listed[0] == {"id": "assist", "name": "Assist", "tool_count": 1}
        assert listed[1]["tool_count"] is None
        assert listed[1]["error"] == "provider exploded"

    async def test_context_reaches_the_api(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """APIs are instantiated under the supplied context."""
        api = register_api("assist", "Assist")

        await tool_registry.async_list_apis(mock_hass, llm_context)

        assert api.contexts == [llm_context]


# =============================================================================
# async_get_api_instances / async_list_tools
# =============================================================================


class TestListTools:
    """Describing the tools of one or every API."""

    async def test_lists_every_api(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """With no api_id, tools from every API are returned, tagged by owner."""
        register_api("assist", "Assist", [StubTool("HassTurnOn")])
        register_api("notes", "Notes", [StubTool("AddNote")])

        described = await tool_registry.async_list_tools(mock_hass, llm_context)

        assert [(t["api_id"], t["name"]) for t in described] == [
            ("assist", "HassTurnOn"),
            ("notes", "AddNote"),
        ]
        assert described[0]["api_name"] == "Assist"

    async def test_restricted_to_one_api(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """An api_id narrows the listing to that API."""
        register_api("assist", "Assist", [StubTool("HassTurnOn")])
        register_api("notes", "Notes", [StubTool("AddNote")])

        described = await tool_registry.async_list_tools(
            mock_hass, llm_context, "notes"
        )

        assert [t["name"] for t in described] == ["AddNote"]

    async def test_search_matches_name_case_insensitively(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """A term is matched against the tool name, ignoring case."""
        register_api(
            "assist", "Assist", [StubTool("HassTurnOn"), StubTool("HassSetTimer")]
        )

        described = await tool_registry.async_list_tools(
            mock_hass, llm_context, search="timer"
        )

        assert [t["name"] for t in described] == ["HassSetTimer"]

    async def test_search_matches_the_description_too(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """Searching finds a tool whose name you do not know."""
        register_api(
            "assist",
            "Assist",
            [
                StubTool("HassTurnOn"),
                StubTool("Snooze", description="Postpone a ringing alarm"),
            ],
        )

        described = await tool_registry.async_list_tools(
            mock_hass, llm_context, search="alarm"
        )

        assert [t["name"] for t in described] == ["Snooze"]

    async def test_search_terms_are_anded(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """A second word narrows rather than widens the result."""
        register_api(
            "assist",
            "Assist",
            [
                StubTool("StopAlarm", description="Stop a ringing alarm"),
                StubTool("StopTimer", description="Stop a running timer"),
            ],
        )

        described = await tool_registry.async_list_tools(
            mock_hass, llm_context, search="stop timer"
        )

        assert [t["name"] for t in described] == ["StopTimer"]

    async def test_search_spans_every_api_when_api_id_is_omitted(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """Filtering does not narrow which APIs are consulted."""
        register_api("assist", "Assist", [StubTool("HassSetTimer")])
        register_api("notes", "Notes", [StubTool("TimerNote")])

        described = await tool_registry.async_list_tools(
            mock_hass, llm_context, search="timer"
        )

        assert [t["api_id"] for t in described] == ["assist", "notes"]

    async def test_name_matches_rank_before_description_matches(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """The tool you named comes first, not the ones that mention it."""
        register_api(
            "assist",
            "Assist",
            [
                StubTool("StopEverything", description="Also stops an alarm"),
                StubTool("SnoozeAlarm", description="Postpone it"),
            ],
        )

        described = await tool_registry.async_list_tools(
            mock_hass, llm_context, search="alarm"
        )

        assert [t["name"] for t in described] == ["SnoozeAlarm", "StopEverything"]

    async def test_equal_ranks_keep_registration_order(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """Ranking must not reshuffle tools that rank the same."""
        register_api("assist", "Assist", [StubTool("AlarmA"), StubTool("AlarmB")])
        register_api("notes", "Notes", [StubTool("AlarmC")])

        described = await tool_registry.async_list_tools(
            mock_hass, llm_context, search="alarm"
        )

        assert [t["name"] for t in described] == ["AlarmA", "AlarmB", "AlarmC"]

    async def test_limit_keeps_the_highest_ranked(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """Truncation happens after ranking, not before."""
        register_api(
            "assist",
            "Assist",
            [
                StubTool("Mentions", description="talks about a timer"),
                StubTool("TimerTool", description="unrelated words"),
            ],
        )

        described = await tool_registry.async_list_tools(
            mock_hass, llm_context, search="timer", limit=1
        )

        assert [t["name"] for t in described] == ["TimerTool"]

    async def test_limit_without_search_caps_the_listing(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """A bare cap is legal and leaves the order alone."""
        register_api(
            "assist", "Assist", [StubTool("One"), StubTool("Two"), StubTool("Three")]
        )

        described = await tool_registry.async_list_tools(
            mock_hass, llm_context, limit=2
        )

        assert [t["name"] for t in described] == ["One", "Two"]

    async def test_blank_search_is_no_filter(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """Whitespace-only input must not silently return nothing."""
        register_api("assist", "Assist", [StubTool("HassTurnOn")])

        described = await tool_registry.async_list_tools(
            mock_hass, llm_context, search="   "
        )

        assert [t["name"] for t in described] == ["HassTurnOn"]

    async def test_unknown_api_id_is_a_validation_error(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """The message names what is registered so the trace is actionable."""
        register_api("assist", "Assist")

        with pytest.raises(ServiceValidationError) as err:
            await tool_registry.async_list_tools(mock_hass, llm_context, "nope")

        assert "'nope'" in str(err.value)
        assert "'assist'" in str(err.value)

    async def test_unknown_api_id_with_empty_registry(
        self, mock_hass: MagicMock, llm_context: Any
    ) -> None:
        """With nothing registered the message says so rather than listing nothing."""
        with pytest.raises(ServiceValidationError) as err:
            await tool_registry.async_list_tools(mock_hass, llm_context, "nope")

        assert "(none registered)" in str(err.value)

    async def test_broken_api_fails_the_listing(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """Unlike list_apis, a scoped listing surfaces the failure."""
        register_api("broken", "Broken", error=RuntimeError("provider exploded"))

        with pytest.raises(HomeAssistantError) as err:
            await tool_registry.async_list_tools(mock_hass, llm_context)

        assert "'broken' could not be prepared" in str(err.value)
        assert "provider exploded" in str(err.value)

    async def test_apis_are_instantiated_one_at_a_time(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """Never a MergedAPI: it would rename tools to `<api>__<tool>`.

        The conftest stand-in for `async_get_api` asserts on a multi-id call, so
        this passing at all is the guarantee.
        """
        register_api("assist", "Assist", [StubTool("HassTurnOn")])
        register_api("notes", "Notes", [StubTool("AddNote")])

        described = await tool_registry.async_list_tools(mock_hass, llm_context)

        assert [t["name"] for t in described] == ["HassTurnOn", "AddNote"]


# =============================================================================
# async_resolve_tool
# =============================================================================


class TestResolveTool:
    """Finding the API that owns a tool name."""

    async def test_resolves_across_apis(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """A tool is found without naming its API."""
        register_api("assist", "Assist", [StubTool("HassTurnOn")])
        register_api("notes", "Notes", [StubTool("AddNote")])

        location = await tool_registry.async_resolve_tool(
            mock_hass, llm_context, "AddNote"
        )

        assert location.api_id == "notes"
        assert location.api_name == "Notes"
        assert location.tool.name == "AddNote"

    async def test_api_id_disambiguates(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """With api_id given, a name shared by two APIs resolves cleanly."""
        register_api("assist", "Assist", [StubTool("Shared")])
        register_api("notes", "Notes", [StubTool("Shared")])

        location = await tool_registry.async_resolve_tool(
            mock_hass, llm_context, "Shared", "notes"
        )

        assert location.api_id == "notes"

    async def test_duplicate_name_is_an_error(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """A shared name is never resolved by picking one arbitrarily."""
        register_api("assist", "Assist", [StubTool("Shared")])
        register_api("notes", "Notes", [StubTool("Shared")])

        with pytest.raises(ServiceValidationError) as err:
            await tool_registry.async_resolve_tool(mock_hass, llm_context, "Shared")

        message = str(err.value)
        assert "'assist'" in message
        assert "'notes'" in message
        assert "api_id" in message

    async def test_unknown_tool_lists_what_exists(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """The error names the tools that are available instead."""
        register_api("assist", "Assist", [StubTool("HassTurnOn")])

        with pytest.raises(ServiceValidationError) as err:
            await tool_registry.async_resolve_tool(mock_hass, llm_context, "Nope")

        message = str(err.value)
        assert "'Nope'" in message
        assert "any registered LLM API" in message
        assert "'HassTurnOn'" in message

    async def test_unknown_tool_within_one_api(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """A scoped lookup says which API it searched."""
        register_api("assist", "Assist", [StubTool("HassTurnOn")])

        with pytest.raises(ServiceValidationError) as err:
            await tool_registry.async_resolve_tool(
                mock_hass, llm_context, "Nope", "assist"
            )

        assert "LLM API 'assist'" in str(err.value)

    async def test_unknown_tool_with_no_tools_at_all(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """An API with no tools produces a message, not an empty list artefact."""
        register_api("assist", "Assist", [])

        with pytest.raises(ServiceValidationError) as err:
            await tool_registry.async_resolve_tool(mock_hass, llm_context, "Nope")

        assert "(none)" in str(err.value)


# =============================================================================
# validate_tool_args
# =============================================================================


class TestValidateToolArgs:
    """Pre-validating arguments against the tool's own schema."""

    def test_valid_args_pass(self) -> None:
        """Matching arguments raise nothing."""
        tool = StubTool("Tool", parameters=vol.Schema({vol.Required("name"): str}))

        tool_registry.validate_tool_args(tool, {"name": "kitchen"})

    def test_missing_required_arg(self) -> None:
        """A missing required key names the tool and the key."""
        tool = StubTool("Tool", parameters=vol.Schema({vol.Required("name"): str}))

        with pytest.raises(ServiceValidationError) as err:
            tool_registry.validate_tool_args(tool, {})

        assert "Tool" in str(err.value)
        assert "name" in str(err.value)

    def test_wrong_type(self) -> None:
        """A value of the wrong type is rejected."""
        tool = StubTool("Tool", parameters=vol.Schema({vol.Required("count"): int}))

        with pytest.raises(ServiceValidationError):
            tool_registry.validate_tool_args(tool, {"count": "not a number"})

    def test_unexpected_key(self) -> None:
        """An argument the tool never declared is rejected."""
        tool = StubTool("Tool", parameters=vol.Schema({vol.Optional("name"): str}))

        with pytest.raises(ServiceValidationError):
            tool_registry.validate_tool_args(tool, {"nope": 1})

    def test_probatio_schema_rejection_is_a_validation_error(self) -> None:
        """A probatio schema's own `Invalid` reaches the caller as a HA error.

        `probatio.Invalid` does not subclass `voluptuous.Invalid`, so catching
        only the latter let a 2026.10 core's rejection escape as a raw
        `MultipleInvalid` — the unreadable trace this function exists to avoid.
        """
        probatio = pytest.importorskip("probatio")
        tool = StubTool(
            "Tool", parameters=probatio.Schema({probatio.Required("name"): str})
        )

        with pytest.raises(ServiceValidationError) as err:
            tool_registry.validate_tool_args(tool, {})

        assert "Tool" in str(err.value)
        assert "name" in str(err.value)

    def test_probatio_valid_args_pass(self) -> None:
        """Matching arguments raise nothing under a probatio schema either."""
        probatio = pytest.importorskip("probatio")
        tool = StubTool(
            "Tool", parameters=probatio.Schema({probatio.Required("name"): str})
        )

        tool_registry.validate_tool_args(tool, {"name": "kitchen"})


# =============================================================================
# async_call_tool
# =============================================================================


class TestCallTool:
    """Running a tool."""

    async def test_returns_the_tool_result(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """Whatever the tool returns comes back untouched."""
        tool = StubTool("HassTurnOn", result={"success": True, "id": 7})
        register_api("assist", "Assist", [tool])

        result = await tool_registry.async_call_tool(
            mock_hass, llm_context, "HassTurnOn", {}
        )

        assert result == {"success": True, "id": 7}

    async def test_arguments_reach_the_tool_unchanged(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """Validation must not rewrite what the tool receives.

        A conversation agent passes the model's raw arguments; passing the
        coerced ones instead would make the same tool behave differently here
        than on the LLM path.
        """
        tool = StubTool(
            "Coercing",
            parameters=vol.Schema({vol.Required("count"): vol.Coerce(int)}),
        )
        register_api("assist", "Assist", [tool])

        await tool_registry.async_call_tool(
            mock_hass, llm_context, "Coercing", {"count": "5"}
        )

        assert tool.calls[0][1].tool_args == {"count": "5"}

    async def test_tool_input_carries_the_name(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """The tool is dispatched through the API instance with a ToolInput."""
        tool = StubTool("HassTurnOn", parameters=vol.Schema({vol.Optional("a"): int}))
        register_api("assist", "Assist", [tool])

        await tool_registry.async_call_tool(
            mock_hass, llm_context, "HassTurnOn", {"a": 1}, "assist"
        )

        _hass, tool_input, _context = tool.calls[0]
        assert tool_input.tool_name == "HassTurnOn"
        assert tool_input.tool_args == {"a": 1}

    async def test_llm_context_reaches_the_tool(
        self, mock_hass: MagicMock, register_api: Any
    ) -> None:
        """Every LLMContext field the caller set is visible inside the tool."""
        call_context = object()
        tool = StubTool("HassTurnOn")
        register_api("assist", "Assist", [tool])

        llm_context = tool_registry.build_llm_context(
            mock_hass,
            context=call_context,
            language="zh-Hant",
            assistant="my_assistant",
            device_id="device-1",
        )
        await tool_registry.async_call_tool(mock_hass, llm_context, "HassTurnOn", {})

        _hass, _tool_input, seen = tool.calls[0]
        assert seen.platform == DOMAIN
        assert seen.context is call_context
        assert seen.language == "zh-Hant"
        assert seen.assistant == "my_assistant"
        assert seen.device_id == "device-1"

    async def test_invalid_args_are_a_validation_error(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """Bad arguments fail before the tool runs."""
        tool = StubTool("Strict", parameters=vol.Schema({vol.Required("name"): str}))
        register_api("assist", "Assist", [tool])

        with pytest.raises(ServiceValidationError):
            await tool_registry.async_call_tool(mock_hass, llm_context, "Strict", {})

        assert tool.calls == []

    async def test_tool_failure_becomes_a_home_assistant_error(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """A tool that raises produces a message naming the tool and its API."""
        tool = StubTool("Failing", error=RuntimeError("device offline"))
        register_api("assist", "Assist", [tool])

        with pytest.raises(HomeAssistantError) as err:
            await tool_registry.async_call_tool(mock_hass, llm_context, "Failing", {})

        message = str(err.value)
        assert "'Failing'" in message
        assert "'assist'" in message
        assert "device offline" in message

    async def test_tool_validation_error_is_not_reclassified(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """A tool that itself raises ServiceValidationError keeps that meaning."""
        tool = StubTool("Picky", error=ServiceValidationError("no such entity"))
        register_api("assist", "Assist", [tool])

        with pytest.raises(ServiceValidationError) as err:
            await tool_registry.async_call_tool(mock_hass, llm_context, "Picky", {})

        assert str(err.value) == "no such entity"

    async def test_ambiguous_tool_is_not_called(
        self, mock_hass: MagicMock, llm_context: Any, register_api: Any
    ) -> None:
        """Neither candidate runs when the name is ambiguous."""
        first = StubTool("Shared")
        second = StubTool("Shared")
        register_api("assist", "Assist", [first])
        register_api("notes", "Notes", [second])

        with pytest.raises(ServiceValidationError):
            await tool_registry.async_call_tool(mock_hass, llm_context, "Shared", {})

        assert first.calls == []
        assert second.calls == []
