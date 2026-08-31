"""LLM Tools integration for Home Assistant.

Home Assistant's LLM API framework has two ways out: a conversation agent, which
needs a model to decide what to call, and `mcp_server`, which leaves the
instance over HTTP and comes back in. This integration adds a third: three
services that let an automation or script read the tool catalogue and run a
single tool directly, with no LLM in the loop.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.llm import async_get_apis
from homeassistant.helpers.service import (
    async_get_all_descriptions,
    async_set_service_schema,
)
from homeassistant.helpers.typing import ConfigType

from . import tool_registry
from .const import (
    ATTR_API_ID,
    ATTR_ARGS,
    ATTR_LIMIT,
    ATTR_SEARCH,
    ATTR_TOOL,
    DOMAIN,
    KEY_APIS,
    KEY_TOOLS,
    SERVICE_CALL_TOOL,
    SERVICE_LIST_APIS,
    SERVICE_LIST_TOOLS,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# No `LLMContext` field is a caller's to set; the cost of that — the
# `intent__Hass*Timer` tools are unreachable — is in README's *`LLMContext` is
# not exposed*.

LIST_APIS_SCHEMA = vol.Schema({})

LIST_TOOLS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_API_ID): cv.string,
        vol.Optional(ATTR_SEARCH): cv.string,
        vol.Optional(ATTR_LIMIT): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)

# `api_id` is required here while it stays optional on the listings, where
# omitting it means "every API" and cannot be ambiguous. Resolving a bare tool
# name across APIs was convenient until a second API registered the same name:
# the automation that relied on it then broke without being touched.
CALL_TOOL_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_API_ID): cv.string,
        vol.Required(ATTR_TOOL): cv.string,
        vol.Optional(ATTR_ARGS, default=dict): dict,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up LLM Tools.

    Services are registered here rather than on entry setup so that automations
    referencing them still validate while the entry is unavailable.
    """
    _async_register_services(hass)
    _async_track_api_changes(hass)
    return True


async def _async_publish_api_options(hass: HomeAssistant) -> None:
    """Fill the `api_id` dropdown with the APIs actually registered.

    `services.yaml` is a static file, so the dropdown would otherwise offer one
    hardcoded id and rely on `custom_value` to let a caller type the truth. The
    registered set is known only at runtime, and `async_set_service_schema` is
    how Home Assistant's own integrations publish a description built at
    runtime (`script`, `esphome`, `python_script`).

    The descriptions are read back through `async_get_all_descriptions` rather
    than from `services.yaml` directly, so the translated name and description
    survive; only the one selector is replaced.
    """
    options = [{"value": api.id, "label": api.name} for api in async_get_apis(hass)]
    descriptions = await async_get_all_descriptions(hass)
    for service in (SERVICE_LIST_TOOLS, SERVICE_CALL_TOOL):
        description = descriptions.get(DOMAIN, {}).get(service)
        if description is None:
            continue
        fields = {
            name: dict(spec) for name, spec in description.get("fields", {}).items()
        }
        if ATTR_API_ID not in fields:
            continue
        fields[ATTR_API_ID] = {
            **fields[ATTR_API_ID],
            "selector": {"select": {"options": options, "sort": False}},
        }
        async_set_service_schema(
            hass, DOMAIN, service, {**description, "fields": fields}
        )


@callback
def _async_track_api_changes(hass: HomeAssistant) -> None:
    """Refresh the dropdown when the set of registered APIs can have changed.

    `llm.async_register_api` writes into a dict and fires nothing, so there is
    no signal for "an API appeared". Config entries are what create them in
    practice, and everything registered by core is in place by the time Home
    Assistant has started.
    """

    async def refresh(*_: Any) -> None:
        await _async_publish_api_options(hass)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, refresh)
    async_dispatcher_connect(hass, SIGNAL_CONFIG_ENTRY_CHANGED, refresh)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up LLM Tools from a config entry.

    There is nothing to connect to and nothing to poll: the entry exists so the
    integration can be added and removed from the UI like any other, and so the
    services can tell "not installed" apart from "misconfigured".
    """
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the config entry."""
    return True


def _async_require_loaded_entry(hass: HomeAssistant) -> None:
    """Fail the call if the integration is not set up.

    Registering the services in `async_setup` means they exist even before the
    entry loads; without this check they would silently work and the config
    entry would be decoration.
    """
    if not hass.config_entries.async_loaded_entries(DOMAIN):
        raise ServiceValidationError(
            "LLM Tools is not set up. Add the integration under "
            "Settings > Devices & Services first."
        )


def _context_from_call(call: ServiceCall) -> Any:
    """Build the LLM context for a service call from its optional fields."""
    return tool_registry.build_llm_context(call.hass, context=call.context)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the integration's services once.

    Services:
        list_apis: Every registered LLM API, with its tool count.
        list_tools: Tool name, description and JSON schema per tool.
        call_tool: Run one tool and return its result.
    """
    if hass.services.has_service(DOMAIN, SERVICE_LIST_APIS):
        return

    async def handle_list_apis(call: ServiceCall) -> ServiceResponse:
        """Return every registered LLM API."""
        _async_require_loaded_entry(hass)
        llm_context = _context_from_call(call)
        return {KEY_APIS: await tool_registry.async_list_apis(hass, llm_context)}

    async def handle_list_tools(call: ServiceCall) -> ServiceResponse:
        """Return the tools of one API, or of every registered API."""
        _async_require_loaded_entry(hass)
        llm_context = _context_from_call(call)
        return {
            KEY_TOOLS: await tool_registry.async_list_tools(
                hass,
                llm_context,
                call.data.get(ATTR_API_ID),
                search=call.data.get(ATTR_SEARCH),
                limit=call.data.get(ATTR_LIMIT),
            )
        }

    async def handle_call_tool(call: ServiceCall) -> ServiceResponse:
        """Run one LLM tool directly and return its result."""
        _async_require_loaded_entry(hass)
        llm_context = _context_from_call(call)
        result = await tool_registry.async_call_tool(
            hass,
            llm_context,
            call.data[ATTR_TOOL],
            call.data[ATTR_ARGS],
            call.data[ATTR_API_ID],
        )

        if not call.return_response:
            return None
        # The response is the tool's own object, never a wrapper. Wrapping a
        # non-object as {"result": ...} made that key indistinguishable from a
        # tool that genuinely returns one, so a tool that breaks Home
        # Assistant's "a ServiceResponse is a JSON object" rule is named
        # instead of being papered over.
        if not isinstance(result, dict):
            raise HomeAssistantError(
                f"Tool '{call.data[ATTR_TOOL]}' returned "
                f"{type(result).__name__}, not an object; a service response "
                f"has to be a JSON object"
            )
        return result

    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_APIS,
        handle_list_apis,
        schema=LIST_APIS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_TOOLS,
        handle_list_tools,
        schema=LIST_TOOLS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CALL_TOOL,
        handle_call_tool,
        schema=CALL_TOOL_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
