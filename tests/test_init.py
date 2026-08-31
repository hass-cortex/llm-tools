"""Tests for the integration setup and its three services."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import voluptuous as vol
import yaml
from conftest import (
    CALL_TOOL_SCHEMA,
    DEFAULT_ASSISTANT,
    DOMAIN,
    LIST_APIS_SCHEMA,
    LIST_TOOLS_SCHEMA,
    SERVICE_CALL_TOOL,
    SERVICE_LIST_APIS,
    SERVICE_LIST_TOOLS,
    StubTool,
    _async_publish_api_options,
    async_setup,
    async_setup_entry,
    async_unload_entry,
    make_call,
    setup_services,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "llm_tools"


# =============================================================================
# Entry lifecycle
# =============================================================================


class TestEntryLifecycle:
    """Setting up and tearing down the config entry."""

    async def test_setup_registers_services(self, mock_hass: MagicMock) -> None:
        """All three services exist after async_setup."""
        await async_setup(mock_hass, {})

        for service in (SERVICE_LIST_APIS, SERVICE_LIST_TOOLS, SERVICE_CALL_TOOL):
            assert (DOMAIN, service) in mock_hass.services.registered

    async def test_setup_is_idempotent(self, mock_hass: MagicMock) -> None:
        """A second setup does not re-register the services."""
        await async_setup(mock_hass, {})
        first = dict(mock_hass.services.registered)

        await async_setup(mock_hass, {})

        assert mock_hass.services.registered == first

    async def test_entry_setup_and_unload(self, mock_hass: MagicMock) -> None:
        """The entry holds no state, so both directions simply succeed."""
        entry = MagicMock()

        assert await async_setup_entry(mock_hass, entry) is True
        assert await async_unload_entry(mock_hass, entry) is True


# =============================================================================
# Service call schemas
# =============================================================================


class TestServiceSchemas:
    """What the service schemas accept before a handler ever runs."""

    def test_tool_is_required(self) -> None:
        """call_tool without a tool name is rejected by the schema."""
        with pytest.raises(vol.Invalid):
            CALL_TOOL_SCHEMA({})

    def test_args_default_to_empty(self) -> None:
        """Omitting args yields an empty dict, not None."""
        assert CALL_TOOL_SCHEMA({"tool": "X", "api_id": "assist"})["args"] == {}

    def test_args_must_be_an_object(self) -> None:
        """A list of arguments is rejected up front."""
        with pytest.raises(vol.Invalid):
            CALL_TOOL_SCHEMA({"tool": "X", "api_id": "assist", "args": [1, 2]})

    def test_call_tool_takes_only_its_own_fields(self) -> None:
        """`tool`, `api_id`, `args` — no `LLMContext` field is exposed."""
        data = CALL_TOOL_SCHEMA({"tool": "X", "api_id": "assist"})

        assert data["api_id"] == "assist"
        assert data["args"] == {}

    @pytest.mark.parametrize(
        "schema", [LIST_APIS_SCHEMA, LIST_TOOLS_SCHEMA, CALL_TOOL_SCHEMA]
    )
    @pytest.mark.parametrize("field", ["device_id", "language", "assistant"])
    def test_context_fields_are_rejected(self, schema: Any, field: str) -> None:
        """No `LLMContext` field is exposed on any service.

        `device_id` was, because it decides whether the `intent__Hass*Timer`
        tools are in the catalogue at all. The request that wants them reaches
        a sentence trigger as `trigger.device_id`, and that path has its own
        conversation agent, so the field earned nothing here.
        `build_llm_context()` still takes all three; exposing one is a schema
        change with no code behind it.
        """
        with pytest.raises(vol.Invalid):
            schema({"tool": "X", "api_id": "assist", field: "x"})


# =============================================================================
# The dynamic api_id dropdown
# =============================================================================


class TestApiOptions:
    """`services.yaml` is static, so the dropdown is published at runtime."""

    @pytest.fixture(autouse=True)
    def _descriptions(self, mock_hass: MagicMock) -> None:
        """Stand in for the descriptions Home Assistant loaded from disk."""
        mock_hass.service_descriptions = {
            DOMAIN: {
                SERVICE_LIST_TOOLS: {
                    "name": "List LLM tools",
                    "description": "…",
                    "fields": {
                        "api_id": {"required": False, "selector": {"select": {}}},
                        "search": {"required": False, "selector": {"text": {}}},
                    },
                },
                SERVICE_CALL_TOOL: {
                    "name": "Call LLM tool",
                    "description": "…",
                    "fields": {
                        "api_id": {"required": True, "selector": {"select": {}}},
                    },
                },
            }
        }

    async def test_options_are_the_registered_apis(
        self, mock_hass: MagicMock, register_api: Any
    ) -> None:
        """Each registered API becomes one option, id as value, name as label."""
        register_api("assist", "Assist")
        register_api("notes", "Notes")

        await _async_publish_api_options(mock_hass)

        published = mock_hass.published_schemas[(DOMAIN, SERVICE_CALL_TOOL)]
        assert published["fields"]["api_id"]["selector"] == {
            "select": {
                "options": [
                    {"value": "assist", "label": "Assist"},
                    {"value": "notes", "label": "Notes"},
                ],
                "sort": False,
            }
        }

    async def test_the_rest_of_the_description_survives(
        self, mock_hass: MagicMock, register_api: Any
    ) -> None:
        """Only the one selector is replaced; name, text and siblings stay.

        The descriptions are read back through Home Assistant rather than from
        `services.yaml`, so a translated name is not lost by republishing.
        """
        register_api("assist", "Assist")

        await _async_publish_api_options(mock_hass)

        published = mock_hass.published_schemas[(DOMAIN, SERVICE_LIST_TOOLS)]
        assert published["name"] == "List LLM tools"
        assert published["fields"]["search"]["selector"] == {"text": {}}
        assert published["fields"]["api_id"]["required"] is False

    async def test_a_service_without_api_id_is_left_alone(
        self, mock_hass: MagicMock, register_api: Any
    ) -> None:
        """list_apis has no such field and must not be republished."""
        register_api("assist", "Assist")

        await _async_publish_api_options(mock_hass)

        assert (DOMAIN, SERVICE_LIST_APIS) not in mock_hass.published_schemas

    async def test_missing_description_is_not_an_error(
        self, mock_hass: MagicMock, register_api: Any
    ) -> None:
        """Descriptions load lazily; publishing before that must not raise."""
        register_api("assist", "Assist")
        mock_hass.service_descriptions = {}

        await _async_publish_api_options(mock_hass)

        assert mock_hass.published_schemas == {}

    async def test_refresh_is_wired_to_start_and_to_entry_changes(
        self, mock_hass: MagicMock
    ) -> None:
        """No signal exists for "an API appeared", so both proxies are used."""
        await async_setup(mock_hass, {})

        assert "homeassistant_started" in mock_hass.bus.listeners
        assert [s for s, _ in mock_hass.dispatcher_connections] == [
            "config_entry_changed"
        ]


# =============================================================================
# Guard: the integration must actually be set up
# =============================================================================


class TestNotConfiguredGuard:
    """Services are registered in async_setup, so they need their own guard."""

    @pytest.mark.parametrize(
        ("service", "data"),
        [
            (SERVICE_LIST_APIS, {}),
            (SERVICE_LIST_TOOLS, {}),
            (SERVICE_CALL_TOOL, {"tool": "X", "api_id": "assist", "args": {}}),
        ],
    )
    async def test_refuses_without_a_loaded_entry(
        self, mock_hass: MagicMock, service: str, data: dict[str, Any]
    ) -> None:
        """Every service refuses while no config entry is loaded."""
        handlers = await setup_services(mock_hass)
        mock_hass.config_entries.async_loaded_entries.return_value = []

        with pytest.raises(ServiceValidationError) as err:
            await handlers[service](make_call(mock_hass, data))

        assert "not set up" in str(err.value)


# =============================================================================
# list_apis / list_tools services
# =============================================================================


class TestListServices:
    """The two discovery services."""

    async def test_list_apis_response_shape(
        self, mock_hass: MagicMock, register_api: Any
    ) -> None:
        """The response is a JSON object keyed by `apis`."""
        register_api("assist", "Assist", [StubTool("HassTurnOn")])
        handlers = await setup_services(mock_hass)

        response = await handlers[SERVICE_LIST_APIS](make_call(mock_hass, {}))

        assert response == {
            "apis": [{"id": "assist", "name": "Assist", "tool_count": 1}]
        }

    async def test_list_tools_response_shape(
        self, mock_hass: MagicMock, register_api: Any
    ) -> None:
        """The response is a JSON object keyed by `tools`."""
        register_api("assist", "Assist", [StubTool("HassTurnOn", description="on")])
        handlers = await setup_services(mock_hass)

        response = await handlers[SERVICE_LIST_TOOLS](make_call(mock_hass, {}))

        assert list(response) == ["tools"]
        assert response["tools"][0]["name"] == "HassTurnOn"
        assert response["tools"][0]["description"] == "on"
        assert response["tools"][0]["parameters"]["type"] == "object"

    async def test_list_tools_honours_api_id(
        self, mock_hass: MagicMock, register_api: Any
    ) -> None:
        """The api_id field narrows the listing."""
        register_api("assist", "Assist", [StubTool("HassTurnOn")])
        register_api("notes", "Notes", [StubTool("AddNote")])
        handlers = await setup_services(mock_hass)

        response = await handlers[SERVICE_LIST_TOOLS](
            make_call(mock_hass, {"api_id": "notes"})
        )

        assert [t["name"] for t in response["tools"]] == ["AddNote"]

    async def test_list_services_use_the_default_context(
        self, mock_hass: MagicMock, register_api: Any
    ) -> None:
        """Discovery runs under the same defaults a call would use."""
        api = register_api("assist", "Assist")
        handlers = await setup_services(mock_hass)

        await handlers[SERVICE_LIST_APIS](make_call(mock_hass, {}))

        context = api.contexts[0]
        assert context.platform == DOMAIN
        assert context.assistant == "conversation"
        assert context.language == "en"

    @pytest.mark.parametrize("service", [SERVICE_LIST_APIS, SERVICE_LIST_TOOLS])
    async def test_discovery_uses_the_same_context_as_a_call(
        self, mock_hass: MagicMock, register_api: Any, service: str
    ) -> None:
        """An API decides its tools from the context it is instantiated under.

        A listing taken under a different context would advertise a different
        tool set from the one `call_tool` reaches. With no context field
        exposed, "the same" means the defaults, on every service.
        """
        api = register_api("assist", "Assist", [StubTool("HassTurnOn")])
        handlers = await setup_services(mock_hass)

        await handlers[service](make_call(mock_hass, {}))

        context = api.contexts[0]
        assert context.device_id is None
        assert context.assistant == DEFAULT_ASSISTANT


# =============================================================================
# call_tool service
# =============================================================================


class TestCallToolService:
    """The execution service."""

    async def test_returns_the_tool_result(
        self, mock_hass: MagicMock, register_api: Any
    ) -> None:
        """A JSON object result is returned verbatim."""
        register_api(
            "assist", "Assist", [StubTool("HassTurnOn", result={"success": True})]
        )
        handlers = await setup_services(mock_hass)

        response = await handlers[SERVICE_CALL_TOOL](
            make_call(mock_hass, {"tool": "HassTurnOn", "api_id": "assist", "args": {}})
        )

        assert response == {"success": True}

    async def test_non_dict_result_names_the_tool(
        self, mock_hass: MagicMock, register_api: Any
    ) -> None:
        """A non-object result is an error, not a silently wrapped value.

        Wrapping it as {"result": ...} made that key indistinguishable from a
        tool that genuinely returns one.
        """
        register_api("assist", "Assist", [StubTool("Listy", result=["a", "b"])])
        handlers = await setup_services(mock_hass)

        with pytest.raises(HomeAssistantError, match="Listy"):
            await handlers[SERVICE_CALL_TOOL](
                make_call(mock_hass, {"tool": "Listy", "api_id": "assist", "args": {}})
            )

    async def test_returns_none_without_response(
        self, mock_hass: MagicMock, register_api: Any
    ) -> None:
        """The tool still runs when the caller wants no response back."""
        tool = StubTool("HassTurnOn")
        register_api("assist", "Assist", [tool])
        handlers = await setup_services(mock_hass)

        response = await handlers[SERVICE_CALL_TOOL](
            make_call(
                mock_hass,
                {"tool": "HassTurnOn", "api_id": "assist", "args": {}},
                return_response=False,
            )
        )

        assert response is None
        assert len(tool.calls) == 1

    async def test_call_context_is_forwarded(
        self, mock_hass: MagicMock, register_api: Any
    ) -> None:
        """The automation's context reaches the tool, so traces stay linked."""
        tool = StubTool("HassTurnOn")
        register_api("assist", "Assist", [tool])
        handlers = await setup_services(mock_hass)
        call = make_call(
            mock_hass, {"tool": "HassTurnOn", "api_id": "assist", "args": {}}
        )

        await handlers[SERVICE_CALL_TOOL](call)

        assert tool.calls[0][2].context is call.context

    async def test_the_tool_sees_the_default_context(
        self, mock_hass: MagicMock, register_api: Any
    ) -> None:
        """No context field is exposed, so a tool always sees the defaults."""
        tool = StubTool("HassTurnOn")
        register_api("assist", "Assist", [tool])
        handlers = await setup_services(mock_hass)

        await handlers[SERVICE_CALL_TOOL](
            make_call(mock_hass, {"tool": "HassTurnOn", "api_id": "assist", "args": {}})
        )

        seen = tool.calls[0][2]
        assert seen.device_id is None
        assert seen.assistant == DEFAULT_ASSISTANT

    def test_api_id_is_required(self) -> None:
        """A call without api_id never reaches the handler.

        The service used to resolve a bare tool name across every registered
        API. That worked until a second API registered the same name, at which
        point an untouched automation started failing, so the schema now
        rejects the call instead.
        """
        with pytest.raises(vol.Invalid):
            CALL_TOOL_SCHEMA({"tool": "Shared", "args": {}})

    async def test_invalid_args_raise_validation_error(
        self, mock_hass: MagicMock, register_api: Any
    ) -> None:
        """Argument errors are validation errors, not failures."""
        register_api(
            "assist",
            "Assist",
            [StubTool("Strict", parameters=vol.Schema({vol.Required("name"): str}))],
        )
        handlers = await setup_services(mock_hass)

        with pytest.raises(ServiceValidationError):
            await handlers[SERVICE_CALL_TOOL](
                make_call(mock_hass, {"tool": "Strict", "api_id": "assist", "args": {}})
            )

    async def test_tool_failure_raises_home_assistant_error(
        self, mock_hass: MagicMock, register_api: Any
    ) -> None:
        """A failing tool is a HomeAssistantError, not a validation error."""
        register_api(
            "assist", "Assist", [StubTool("Failing", error=RuntimeError("boom"))]
        )
        handlers = await setup_services(mock_hass)

        with pytest.raises(HomeAssistantError) as err:
            await handlers[SERVICE_CALL_TOOL](
                make_call(
                    mock_hass, {"tool": "Failing", "api_id": "assist", "args": {}}
                )
            )

        assert not isinstance(err.value, ServiceValidationError)


# =============================================================================
# Metadata consistency
# =============================================================================


class TestMetadata:
    """manifest, services.yaml and translations must agree with the code."""

    @pytest.fixture
    def manifest(self) -> dict[str, Any]:
        """Load manifest.json."""
        return json.loads((COMPONENT_DIR / "manifest.json").read_text())

    @pytest.fixture
    def services_yaml(self) -> dict[str, Any]:
        """Load services.yaml."""
        return yaml.safe_load((COMPONENT_DIR / "services.yaml").read_text())

    @pytest.fixture
    def strings(self) -> dict[str, Any]:
        """Load strings.json."""
        return json.loads((COMPONENT_DIR / "strings.json").read_text())

    def test_manifest_domain_matches(self, manifest: dict[str, Any]) -> None:
        """The manifest domain is the domain the code registers under."""
        assert manifest["domain"] == DOMAIN

    def test_manifest_is_single_entry_with_config_flow(
        self, manifest: dict[str, Any]
    ) -> None:
        """Installation is a config flow limited to one entry."""
        assert manifest["config_flow"] is True
        assert manifest["single_config_entry"] is True

    def test_manifest_declares_llm_dependencies(self, manifest: dict[str, Any]) -> None:
        """`llm` registers the Assist API; `conversation` backs the trace hook.

        `APIInstance.async_call_tool` imports `homeassistant.components.
        conversation` at call time, so the dependency is real even though
        nothing in this integration imports it directly.
        """
        assert set(manifest["dependencies"]) == {"conversation", "llm"}

    def test_services_yaml_matches_registered_services(
        self, services_yaml: dict[str, Any]
    ) -> None:
        """Every registered service is documented, and nothing else is."""
        assert set(services_yaml) == {
            SERVICE_LIST_APIS,
            SERVICE_LIST_TOOLS,
            SERVICE_CALL_TOOL,
        }

    def test_services_yaml_fields_match_the_schemas(
        self, services_yaml: dict[str, Any]
    ) -> None:
        """The documented fields are exactly the schema keys."""
        documented = set(services_yaml[SERVICE_CALL_TOOL]["fields"])
        schema_keys = {str(key) for key in CALL_TOOL_SCHEMA.schema}

        assert documented == schema_keys

    def test_call_tool_required_fields_match_the_schema(
        self, services_yaml: dict[str, Any]
    ) -> None:
        """`tool` and `api_id` are required in the UI, matching the schema."""
        fields = services_yaml[SERVICE_CALL_TOOL]["fields"]
        required = {name for name, spec in fields.items() if spec.get("required")}

        assert required == {"tool", "api_id"}

    def test_every_field_has_a_selector(self, services_yaml: dict[str, Any]) -> None:
        """A field without a selector renders as a bare text box."""
        for service, spec in services_yaml.items():
            for name, field in (spec.get("fields") or {}).items():
                assert "selector" in field, f"{service}.{name} has no selector"

    def test_strings_cover_every_service(self, strings: dict[str, Any]) -> None:
        """Each service has a name and description in strings.json."""
        assert set(strings["services"]) == set(
            [SERVICE_LIST_APIS, SERVICE_LIST_TOOLS, SERVICE_CALL_TOOL]
        )
        for service in strings["services"].values():
            assert service["name"]
            assert service["description"]

    def test_strings_cover_every_field(self, strings: dict[str, Any]) -> None:
        """Each documented field is translated."""
        assert set(strings["services"][SERVICE_CALL_TOOL]["fields"]) == {
            str(key) for key in CALL_TOOL_SCHEMA.schema
        }

    def test_translations_match_strings(self, strings: dict[str, Any]) -> None:
        """translations/en.json is identical to strings.json."""
        translations = json.loads(
            (COMPONENT_DIR / "translations" / "en.json").read_text()
        )

        assert translations == strings

    def test_icons_cover_every_service(self) -> None:
        """Each service has an icon, keyed by a name that actually exists.

        A typo here costs nothing at load time and silently shows no icon.
        """
        icons = json.loads((COMPONENT_DIR / "icons.json").read_text())

        assert set(icons["services"]) == {
            SERVICE_LIST_APIS,
            SERVICE_LIST_TOOLS,
            SERVICE_CALL_TOOL,
        }
        for spec in icons["services"].values():
            assert spec["service"].startswith("mdi:")

    def test_brand_assets_are_complete(self) -> None:
        """The eight files Home Assistant looks for, at the sizes it expects."""
        from struct import unpack

        expected = {
            "icon.png": (128, 128),
            "icon@2x.png": (256, 256),
            "dark_icon.png": (128, 128),
            "dark_icon@2x.png": (256, 256),
        }
        brand = COMPONENT_DIR / "brand"
        for name, size in expected.items():
            data = (brand / name).read_bytes()
            assert data[:8] == b"\x89PNG\r\n\x1a\n", name
            assert unpack(">II", data[16:24]) == size, name
        for name in ("logo.png", "logo@2x.png", "dark_logo.png", "dark_logo@2x.png"):
            data = (brand / name).read_bytes()
            assert data[:8] == b"\x89PNG\r\n\x1a\n", name
            width, height = unpack(">II", data[16:24])
            assert height == (100 if "@2x" not in name else 200), name
            assert width > height, name
