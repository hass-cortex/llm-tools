"""Pytest fixtures for LLM Tools tests.

Home Assistant is not installed here, so its modules are mocked before
`custom_components` is imported, following the `llm-mcp-bridge` pattern.

`homeassistant.helpers.llm` is not a MagicMock but a faithful miniature of the
real helper — the registry, `async_get_api`'s "API not found" error, and the
`Tool` / `API` / `APIInstance` / `ToolInput` / `LLMContext` shapes. Everything
this integration does is a statement about that helper's behaviour, so a stand-in
that only records calls would test nothing. `voluptuous`, `voluptuous_openapi` and `probatio`
are the real libraries for the same reason.
"""

from __future__ import annotations

import dataclasses
import sys
import types
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

# Real libraries: schema conversion is behaviour under test, not a detail.
import probatio as _real_probatio
import pytest
import voluptuous as _real_vol
import voluptuous_openapi as _real_vol_openapi

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

sys.modules["voluptuous"] = _real_vol
sys.modules["voluptuous_openapi"] = _real_vol_openapi
sys.modules["probatio"] = _real_probatio

# =============================================================================
# Mock Home Assistant modules BEFORE importing custom_components
# =============================================================================

mock_homeassistant = MagicMock()
mock_config_entries = MagicMock()
mock_core = MagicMock()
mock_helpers = MagicMock()


class _MockConfigFlow:
    """Mock ConfigFlow base class for subclassing."""

    def __init_subclass__(cls, *, domain: str = "", **kwargs: Any) -> None:
        """Accept the domain kwarg used by HA's ConfigFlow."""
        super().__init_subclass__(**kwargs)
        cls._domain = domain

    def __init__(self) -> None:
        self.hass = MagicMock()
        self.flow_id = "test-flow-id"
        self.handler = "llm_tools"
        self.source = "user"

    def async_show_form(
        self,
        *,
        step_id: str,
        data_schema: Any = None,
        errors: dict[str, str] | None = None,
        description_placeholders: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Return a form result dict."""
        return {
            "type": "form",
            "flow_id": self.flow_id,
            "handler": self.handler,
            "step_id": step_id,
            "data_schema": data_schema,
            "errors": errors or {},
        }

    def async_create_entry(
        self,
        *,
        title: str,
        data: dict[str, Any],
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a create_entry result dict."""
        return {
            "type": "create_entry",
            "flow_id": self.flow_id,
            "handler": self.handler,
            "title": title,
            "data": data,
            "options": options or {},
        }

    def async_abort(self, *, reason: str) -> dict[str, Any]:
        """Return an abort result dict."""
        return {"type": "abort", "flow_id": self.flow_id, "reason": reason}


mock_config_entries.ConfigEntry = MagicMock
mock_config_entries.ConfigFlow = _MockConfigFlow
mock_config_entries.ConfigFlowResult = dict


class _MockContext:
    """Mock Context: an id is enough to prove the call context is forwarded."""

    def __init__(self, context_id: str = "test-context") -> None:
        self.id = context_id


class _MockSupportsResponse(Enum):
    """Mock SupportsResponse enum."""

    NONE = "none"
    OPTIONAL = "optional"
    ONLY = "only"


mock_core.HomeAssistant = MagicMock
mock_core.Context = _MockContext
mock_core.ServiceCall = MagicMock
mock_core.ServiceResponse = dict
mock_core.SupportsResponse = _MockSupportsResponse
mock_core.callback = lambda f: f


class _MockHomeAssistantError(Exception):
    """Mock HomeAssistantError."""


class _MockServiceValidationError(_MockHomeAssistantError):
    """Mock ServiceValidationError.

    A distinct class, not a bare Exception: telling "the caller asked for
    something impossible" apart from "the tool blew up" is the whole point of
    the error contract under test.
    """


mock_exceptions = MagicMock()
mock_exceptions.HomeAssistantError = _MockHomeAssistantError
mock_exceptions.ServiceValidationError = _MockServiceValidationError


def _mock_cv_string(value: Any) -> str:
    """Mirror cv.string closely enough for the service schemas."""
    if value is None:
        raise _real_vol.Invalid("string value is None")
    if isinstance(value, list | dict):
        raise _real_vol.Invalid("value should be a string")
    return str(value)


mock_helpers_cv = MagicMock()
mock_helpers_cv.string = _mock_cv_string
mock_helpers_cv.config_entry_only_config_schema = MagicMock(return_value=MagicMock())

mock_helpers_typing = MagicMock()
mock_helpers_typing.ConfigType = dict


# =============================================================================
# homeassistant.helpers.llm — a faithful miniature of the real helper
# =============================================================================


@dataclasses.dataclass(slots=True)
class _MockLLMContext:
    """Mirror of llm.LLMContext."""

    platform: str
    context: Any
    language: str | None
    assistant: str
    device_id: str | None


@dataclasses.dataclass(slots=True)
class _MockToolInput:
    """Mirror of llm.ToolInput."""

    tool_name: str
    tool_args: dict[str, Any]
    id: str = "test-tool-input-id"
    external: bool = False


class _MockTool:
    """Mirror of llm.Tool, including the empty default parameters schema."""

    name: str
    description: str | None = None
    parameters: Any = _real_vol.Schema({})

    async def async_call(
        self, hass: Any, tool_input: _MockToolInput, llm_context: _MockLLMContext
    ) -> Any:
        """Call the tool."""
        raise NotImplementedError


@dataclasses.dataclass
class _MockAPIInstance:
    """Mirror of llm.APIInstance, including its unvalidated tool dispatch.

    Core's `async_call_tool` looks the tool up by name and calls it. It does
    *not* validate `tool_args` against the tool's schema, and the mock must not
    either, or the integration's own validation would be tested against a
    strawman.
    """

    api: Any
    api_prompt: str
    llm_context: Any
    tools: list[Any]
    custom_serializer: Callable[[Any], Any] | None = None

    async def async_call_tool(self, tool_input: _MockToolInput) -> Any:
        """Call a tool by name."""
        for tool in self.tools:
            if tool.name == tool_input.tool_name:
                break
        else:
            raise _MockHomeAssistantError(f'Tool "{tool_input.tool_name}" not found')
        return await tool.async_call(self.api.hass, tool_input, self.llm_context)


@dataclasses.dataclass(kw_only=True)
class _MockAPI:
    """Mirror of llm.API."""

    hass: Any
    id: str
    name: str

    async def async_get_api_instance(self, llm_context: Any) -> _MockAPIInstance:
        """Return the instance of the API."""
        raise NotImplementedError


_LLM_APIS_KEY = "llm_apis"


def _llm_apis(hass: Any) -> dict[str, Any]:
    """Return the per-hass API registry."""
    return hass.data.setdefault(_LLM_APIS_KEY, {})


def _mock_async_register_api(hass: Any, api: Any) -> Callable[[], None]:
    """Mirror of llm.async_register_api."""
    apis = _llm_apis(hass)
    if api.id in apis:
        raise _MockHomeAssistantError(f"API {api.id} is already registered")
    apis[api.id] = api

    def unregister() -> None:
        apis.pop(api.id)

    return unregister


def _mock_async_get_apis(hass: Any) -> list[Any]:
    """Mirror of llm.async_get_apis."""
    return list(_llm_apis(hass).values())


async def _mock_async_get_api(hass: Any, api_id: Any, llm_context: Any) -> Any:
    """Mirror of llm.async_get_api for the single-id case."""
    apis = _llm_apis(hass)
    api_ids = [api_id] if isinstance(api_id, str) else api_id
    for key in api_ids:
        if key not in apis:
            raise _MockHomeAssistantError(f"API {key} not found")
    if len(api_ids) != 1:
        raise AssertionError(
            "The integration must never ask for several APIs at once: core "
            "would return a MergedAPI whose tools are renamed."
        )
    return await apis[api_ids[0]].async_get_api_instance(llm_context)


mock_helpers_llm = types.ModuleType("homeassistant.helpers.llm")
mock_helpers_llm.API = _MockAPI  # type: ignore[attr-defined]
mock_helpers_llm.APIInstance = _MockAPIInstance  # type: ignore[attr-defined]
mock_helpers_llm.LLMContext = _MockLLMContext  # type: ignore[attr-defined]
mock_helpers_llm.Tool = _MockTool  # type: ignore[attr-defined]
mock_helpers_llm.ToolInput = _MockToolInput  # type: ignore[attr-defined]
mock_helpers_llm.LLM_API_ASSIST = "assist"  # type: ignore[attr-defined]
mock_helpers_llm.async_register_api = _mock_async_register_api  # type: ignore[attr-defined]
mock_helpers_llm.async_get_apis = _mock_async_get_apis  # type: ignore[attr-defined]
mock_helpers_llm.async_get_api = _mock_async_get_api  # type: ignore[attr-defined]


# The dynamic `api_id` dropdown reaches for three more corners of core. Each is
# a thin surface the integration only calls, so a recording double is enough —
# unlike `helpers.llm`, whose behaviour is what the tests are about.
mock_const = types.ModuleType("homeassistant.const")
mock_const.EVENT_HOMEASSISTANT_STARTED = "homeassistant_started"  # type: ignore[attr-defined]

mock_config_entries.SIGNAL_CONFIG_ENTRY_CHANGED = "config_entry_changed"  # type: ignore[attr-defined]


def _mock_async_dispatcher_connect(hass: Any, signal: Any, target: Any) -> Any:
    """Record the subscription and hand back an unsubscribe callable."""
    hass.dispatcher_connections.append((signal, target))
    return lambda: None


mock_helpers_dispatcher = types.ModuleType("homeassistant.helpers.dispatcher")
mock_helpers_dispatcher.async_dispatcher_connect = _mock_async_dispatcher_connect  # type: ignore[attr-defined]


async def _mock_async_get_all_descriptions(hass: Any) -> dict[str, Any]:
    """Return whatever the test put on the hass double."""
    return hass.service_descriptions


def _mock_async_set_service_schema(
    hass: Any, domain: str, service: str, schema: dict[str, Any]
) -> None:
    """Record the published description."""
    hass.published_schemas[(domain, service)] = schema


mock_helpers_service = types.ModuleType("homeassistant.helpers.service")
mock_helpers_service.async_get_all_descriptions = _mock_async_get_all_descriptions  # type: ignore[attr-defined]
mock_helpers_service.async_set_service_schema = _mock_async_set_service_schema  # type: ignore[attr-defined]


# Wire submodule attributes so `from homeassistant.helpers import llm` resolves.
mock_helpers.llm = mock_helpers_llm
mock_helpers.dispatcher = mock_helpers_dispatcher
mock_helpers.service = mock_helpers_service
mock_helpers.config_validation = mock_helpers_cv
mock_helpers.typing = mock_helpers_typing

mock_homeassistant.config_entries = mock_config_entries
mock_homeassistant.const = mock_const
mock_homeassistant.core = mock_core
mock_homeassistant.exceptions = mock_exceptions
mock_homeassistant.helpers = mock_helpers

sys.modules["homeassistant"] = mock_homeassistant
sys.modules["homeassistant.config_entries"] = mock_config_entries
sys.modules["homeassistant.core"] = mock_core
sys.modules["homeassistant.exceptions"] = mock_exceptions
sys.modules["homeassistant.helpers"] = mock_helpers
sys.modules["homeassistant.helpers.llm"] = mock_helpers_llm
sys.modules["homeassistant.helpers.config_validation"] = mock_helpers_cv
sys.modules["homeassistant.helpers.typing"] = mock_helpers_typing
sys.modules["homeassistant.const"] = mock_const
sys.modules["homeassistant.helpers.dispatcher"] = mock_helpers_dispatcher
sys.modules["homeassistant.helpers.service"] = mock_helpers_service

# =============================================================================
# Now import the integration
# =============================================================================

from custom_components.llm_tools import (  # noqa: E402
    CALL_TOOL_SCHEMA,
    LIST_APIS_SCHEMA,
    LIST_TOOLS_SCHEMA,
    _async_publish_api_options,
    async_setup,
    async_setup_entry,
    async_unload_entry,
    tool_registry,  # noqa: E402
)
from custom_components.llm_tools.config_flow import (  # noqa: E402
    LlmToolsConfigFlow,
)
from custom_components.llm_tools.const import (  # noqa: E402
    DEFAULT_ASSISTANT,
    DOMAIN,
    SERVICE_CALL_TOOL,
    SERVICE_LIST_APIS,
    SERVICE_LIST_TOOLS,
)
from custom_components.llm_tools.diagnostics import (  # noqa: E402
    async_get_config_entry_diagnostics,
)

__all__ = [
    "CALL_TOOL_SCHEMA",
    "DEFAULT_ASSISTANT",
    "_async_publish_api_options",
    "DOMAIN",
    "LIST_APIS_SCHEMA",
    "LIST_TOOLS_SCHEMA",
    "LlmToolsConfigFlow",
    "SERVICE_CALL_TOOL",
    "SERVICE_LIST_APIS",
    "SERVICE_LIST_TOOLS",
    "async_get_config_entry_diagnostics",
    "async_setup",
    "async_setup_entry",
    "async_unload_entry",
    "tool_registry",
]


# =============================================================================
# Test doubles for tools and APIs
# =============================================================================


class StubTool(_MockTool):
    """A tool whose behaviour each test decides."""

    def __init__(
        self,
        name: str,
        *,
        description: str | None = None,
        parameters: Any = None,
        result: Any = None,
        error: Exception | None = None,
    ) -> None:
        """Init the stub."""
        self.name = name
        self.description = description
        if parameters is not None:
            self.parameters = parameters
        self.result = {"success": True} if result is None else result
        self.error = error
        self.calls: list[tuple[Any, _MockToolInput, _MockLLMContext]] = []

    async def async_call(
        self, hass: Any, tool_input: _MockToolInput, llm_context: _MockLLMContext
    ) -> Any:
        """Record the call and return the configured result."""
        self.calls.append((hass, tool_input, llm_context))
        if self.error is not None:
            raise self.error
        return self.result


class StubAPI(_MockAPI):
    """An API returning a fixed set of tools."""

    def __init__(
        self,
        hass: Any,
        api_id: str,
        name: str,
        tools: list[Any] | None = None,
        *,
        custom_serializer: Callable[[Any], Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        """Init the stub."""
        super().__init__(hass=hass, id=api_id, name=name)
        self.tools = tools or []
        self.custom_serializer = custom_serializer
        self.error = error
        self.contexts: list[_MockLLMContext] = []

    async def async_get_api_instance(self, llm_context: Any) -> _MockAPIInstance:
        """Record the context and build an instance."""
        self.contexts.append(llm_context)
        if self.error is not None:
            raise self.error
        return _MockAPIInstance(
            api=self,
            api_prompt=f"prompt for {self.id}",
            llm_context=llm_context,
            tools=list(self.tools),
            custom_serializer=self.custom_serializer,
        )


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_hass() -> MagicMock:
    """Create a mock Home Assistant instance with a working service registry."""
    hass = MagicMock()
    hass.data = {}
    hass.config.language = "en"

    registered: dict[tuple[str, str], Any] = {}

    def _has_service(domain: str, service: str) -> bool:
        return (domain, service) in registered

    def _async_register(domain: str, service: str, handler: Any, **kwargs: Any) -> None:
        registered[(domain, service)] = handler

    hass.services.has_service = _has_service
    hass.services.async_register = _async_register
    hass.services.registered = registered

    # State the dynamic `api_id` dropdown reads and writes.
    hass.dispatcher_connections = []
    hass.published_schemas = {}
    hass.service_descriptions = {}
    hass.bus.listeners = {}

    def _listen_once(event: str, target: Any) -> Any:
        hass.bus.listeners[event] = target
        return lambda: None

    hass.bus.async_listen_once = _listen_once

    # One loaded config entry by default: the services refuse to run without one.
    hass.config_entries.async_loaded_entries = MagicMock(return_value=[MagicMock()])
    return hass


@pytest.fixture
def register_api(mock_hass: MagicMock) -> Callable[..., StubAPI]:
    """Return a helper that registers a StubAPI on the mock hass."""

    def _register(
        api_id: str,
        name: str | None = None,
        tools: list[Any] | None = None,
        **kwargs: Any,
    ) -> StubAPI:
        api = StubAPI(mock_hass, api_id, name or api_id.title(), tools, **kwargs)
        _mock_async_register_api(mock_hass, api)
        return api

    return _register


@pytest.fixture
def llm_context() -> _MockLLMContext:
    """Return a default LLM context."""
    return _MockLLMContext(
        platform=DOMAIN,
        context=None,
        language="en",
        assistant=DEFAULT_ASSISTANT,
        device_id=None,
    )


def make_call(
    hass: Any, data: dict[str, Any], *, return_response: bool = True
) -> MagicMock:
    """Build a ServiceCall-like object carrying validated data."""
    call = MagicMock()
    call.hass = hass
    call.data = data
    call.context = _MockContext()
    call.return_response = return_response
    return call


async def setup_services(hass: Any) -> dict[str, Any]:
    """Run async_setup and return the registered service handlers by name."""
    await async_setup(hass, {})
    return {
        service: handler for (_, service), handler in hass.services.registered.items()
    }
