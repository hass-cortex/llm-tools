"""Read and run Home Assistant LLM tools without a conversation agent.

Everything in this module works against `homeassistant.helpers.llm` only. It
knows nothing about services or config entries, so the service layer in
`__init__.py` stays a thin adapter over these functions.

Error contract: anything caused by the caller's input (an unknown API, an
unknown or ambiguous tool name, arguments that fail the tool's own schema)
raises `ServiceValidationError`; anything that goes wrong while preparing or
running a tool raises `HomeAssistantError`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import llm

from .const import DEFAULT_ASSISTANT, DOMAIN

_LOGGER = logging.getLogger(__name__)

EMPTY_PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}}

try:  # Home Assistant 2026.10+
    from probatio import Invalid as _ProbatioInvalid
    from probatio import to_openapi as _probatio_to_openapi
except ImportError:  # pragma: no cover - exercised by the other branch
    _ProbatioInvalid = None
    _probatio_to_openapi = None

try:  # Home Assistant 2026.9 and earlier
    from voluptuous_openapi import convert as _vol_openapi_convert
except ImportError:  # pragma: no cover - exercised by the other branch
    _vol_openapi_convert = None

# Neither library's `Invalid` subclasses the other's, so a schema rejection has
# to be caught under both names for the same reason `_to_openapi` dispatches on
# the schema in hand.
SCHEMA_INVALID: tuple[type[Exception], ...] = tuple(
    error for error in (vol.Invalid, _ProbatioInvalid) if error is not None
)


def _to_openapi(schema: Any, custom_serializer: Any) -> dict[str, Any]:
    """Serialize a tool's schema to OpenAPI, whichever library produced it.

    Home Assistant is mid-migration from voluptuous to probatio: 2026.9 hands
    out `voluptuous.Schema` and ships `voluptuous-openapi`, while the 2026.10
    development branch imports `to_openapi` from probatio and has dropped
    voluptuous from requirements.txt. Neither serializer accepts the other's
    schema — probatio raises TypeError on a voluptuous Schema — so the choice
    has to follow the object in hand rather than what happens to be installed.

    An unrecognised schema tries every serializer available, since a future
    core may hand out something neither branch anticipates.
    """
    serializers = _serializers_for(type(schema))
    if not serializers:
        raise RuntimeError(
            "No schema serializer available; declare probatio or "
            "voluptuous-openapi in manifest.json requirements"
        )
    last_error: Exception | None = None
    for serialize in serializers:
        try:
            return serialize(schema, custom_serializer=custom_serializer)
        except Exception as err:  # noqa: BLE001 - try the next serializer
            last_error = err
    raise last_error  # type: ignore[misc]


def _serializers_for(schema_type: type) -> list[Any]:
    """Order the available serializers, most likely to work first."""
    probatio_first = schema_type.__module__.partition(".")[0] == "probatio"
    ordered = (
        [_probatio_to_openapi, _vol_openapi_convert]
        if probatio_first
        else [_vol_openapi_convert, _probatio_to_openapi]
    )
    return [fn for fn in ordered if fn is not None]


@dataclass(frozen=True, slots=True)
class ToolLocation:
    """A single tool together with the API instance that owns it."""

    api_id: str
    api_name: str
    tool: llm.Tool
    instance: llm.APIInstance


def build_llm_context(
    hass: HomeAssistant,
    *,
    context: Context | None = None,
    language: str | None = None,
    assistant: str | None = None,
    device_id: str | None = None,
) -> llm.LLMContext:
    """Build the `LLMContext` a tool is prepared and executed under.

    Args:
        hass: Home Assistant instance.
        context: Calling context, so the tool's own service calls stay
            attributable to the automation or script that started them.
        language: Language of the request. Falls back to the instance language.
        assistant: Assistant domain deciding which entities count as exposed.
            Falls back to `conversation`, the value every Assist path uses.
        device_id: Device the request originates from, used by tools that
            resolve "here" to an area or floor.

    Returns:
        An `LLMContext` identifying this integration as the platform.
    """
    return llm.LLMContext(
        platform=DOMAIN,
        context=context,
        language=language or hass.config.language,
        assistant=assistant or DEFAULT_ASSISTANT,
        device_id=device_id,
    )


def format_tool(
    tool: llm.Tool, custom_serializer: Callable[[Any], Any] | None
) -> dict[str, Any]:
    """Describe one tool as a JSON-serialisable dict.

    `parameters` is the tool's voluptuous schema rendered as an OpenAPI/JSON
    schema, the same conversion the `mcp_server` integration performs, so what a
    caller reads here is what an LLM would have been shown.

    Args:
        tool: The tool to describe.
        custom_serializer: The owning API instance's serializer, which resolves
            Home Assistant selectors that plain voluptuous cannot express.

    Returns:
        A dict with `name`, `description` and `parameters`. A schema that cannot
        be converted yields empty parameters plus an `error` key rather than
        failing the whole listing.
    """
    described: dict[str, Any] = {
        "name": tool.name,
        "description": tool.description or "",
    }
    try:
        described["parameters"] = _to_openapi(tool.parameters, custom_serializer)
    # Third-party schemas are arbitrary; a listing must survive a bad one.
    except Exception as err:
        _LOGGER.warning("Could not convert parameters of tool %s: %s", tool.name, err)
        described["parameters"] = dict(EMPTY_PARAMETERS)
        described["error"] = f"Could not convert parameters: {err}"
    return described


def _registered_apis(hass: HomeAssistant, api_id: str | None) -> list[llm.API]:
    """Return the APIs to work with, validating an explicitly requested one."""
    apis = llm.async_get_apis(hass)
    if api_id is None:
        return apis
    for api in apis:
        if api.id == api_id:
            return [api]
    known = ", ".join(sorted(f"'{api.id}'" for api in apis)) or "(none registered)"
    raise ServiceValidationError(
        f"Unknown LLM API '{api_id}'. Registered APIs: {known}"
    )


async def _async_get_instance(
    hass: HomeAssistant, api_id: str, llm_context: llm.LLMContext
) -> llm.APIInstance:
    """Instantiate one API.

    Each API is instantiated on its own. Asking core for several at once returns
    a `MergedAPI`, which renames every tool to `<api_name>__<tool>` — names no
    caller of this integration ever sees anywhere else.
    """
    return await llm.async_get_api(hass, api_id, llm_context)


async def async_get_api_instances(
    hass: HomeAssistant, llm_context: llm.LLMContext, api_id: str | None = None
) -> list[llm.APIInstance]:
    """Instantiate every requested API, failing loudly if one cannot be prepared.

    Args:
        hass: Home Assistant instance.
        llm_context: Context the APIs are instantiated under.
        api_id: Restrict to a single API. `None` covers every registered API.

    Returns:
        One `APIInstance` per requested API.

    Raises:
        ServiceValidationError: `api_id` names an API that is not registered.
        HomeAssistantError: An API raised while being prepared.
    """
    instances: list[llm.APIInstance] = []
    for api in _registered_apis(hass, api_id):
        try:
            instances.append(await _async_get_instance(hass, api.id, llm_context))
        except Exception as err:
            raise HomeAssistantError(
                f"LLM API '{api.id}' could not be prepared: {err}"
            ) from err
    return instances


async def async_list_apis(
    hass: HomeAssistant, llm_context: llm.LLMContext
) -> list[dict[str, Any]]:
    """List every registered LLM API with its tool count.

    Discovery degrades rather than fails: an API that raises while being
    prepared is still listed, with a null `tool_count` and an `error`, so one
    broken provider cannot hide every other API from the caller.
    """
    listed: list[dict[str, Any]] = []
    for api in llm.async_get_apis(hass):
        entry: dict[str, Any] = {"id": api.id, "name": api.name, "tool_count": None}
        try:
            instance = await _async_get_instance(hass, api.id, llm_context)
        # One broken provider must not hide every other API from the caller.
        except Exception as err:
            _LOGGER.warning("LLM API %s could not be prepared: %s", api.id, err)
            entry["error"] = str(err)
        else:
            entry["tool_count"] = len(instance.tools)
        listed.append(entry)
    return listed


def tool_matches(tool: llm.Tool, terms: list[str]) -> bool:
    """Whether every search term appears in the tool's name or description.

    Terms are ANDed so that adding one narrows the result, which is what
    someone typing a second word expects. Matching the description as well as
    the name is deliberate: a caller looking for "alarm" should find
    `ha_snooze_alarm` and anything whose description is about alarms, since
    the point of searching is to find a tool whose name you do not know.
    """
    haystack = f"{tool.name}\n{tool.description or ''}".casefold()
    return all(term in haystack for term in terms)


def match_rank(tool: llm.Tool, terms: list[str]) -> int:
    """Sort key putting a name match ahead of a description-only match.

    A caller who types the name of the tool they want should see it first, and
    the descriptions match generously on purpose — searching "alarm" also
    returns tools that merely mention alarms. That is a two-bucket problem, so
    it gets a boolean rather than a relevance score: BM25 would need a
    tokenizer that splits `ha_snooze_alarm`, a segmenter for CJK descriptions,
    and corpus statistics, to order a result set that is usually under ten.
    """
    return 0 if all(term in tool.name.casefold() for term in terms) else 1


async def async_list_tools(
    hass: HomeAssistant,
    llm_context: llm.LLMContext,
    api_id: str | None = None,
    *,
    search: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Describe the tools of one API, or of every registered API.

    Args:
        hass: Home Assistant instance.
        llm_context: Context the APIs are instantiated under.
        api_id: Restrict to a single API. `None` covers every registered API.
        search: Keep only tools matching every whitespace-separated term,
            case-insensitively, in the name or description. Listing every API
            unfiltered runs to hundreds of tools with a JSON schema each.
        limit: Keep at most this many tools, after ranking. Truncating an
            unranked list would drop matches arbitrarily, so the two belong
            together.

    Returns:
        One dict per tool: `api_id`, `api_name`, `name`, `description`,
        `parameters`. Name matches come first; within a rank the registration
        order of the APIs and their tools is preserved.

    Raises:
        ServiceValidationError: `api_id` names an API that is not registered.
        HomeAssistantError: An API raised while being prepared.
    """
    terms = (search or "").casefold().split()
    matched: list[tuple[int, llm.APIInstance, llm.Tool]] = [
        (match_rank(tool, terms), instance, tool)
        for instance in await async_get_api_instances(hass, llm_context, api_id)
        for tool in instance.tools
        if not terms or tool_matches(tool, terms)
    ]
    # Sorting on the rank alone keeps Python's sort stable, so tools that rank
    # equally stay in registration order. Ranking before formatting also means
    # a schema is only serialized for a tool that survives `limit`.
    matched.sort(key=lambda entry: entry[0])
    if limit is not None:
        matched = matched[:limit]
    return [
        {
            "api_id": instance.api.id,
            "api_name": instance.api.name,
            **format_tool(tool, instance.custom_serializer),
        }
        for _, instance, tool in matched
    ]


async def async_resolve_tool(
    hass: HomeAssistant,
    llm_context: llm.LLMContext,
    tool_name: str,
    api_id: str | None = None,
) -> ToolLocation:
    """Find the one API that owns `tool_name`.

    With no `api_id` the search covers every registered API. A name owned by
    more than one API is an error rather than a choice: picking one would make
    the same automation call different code depending on which integrations
    happen to be loaded.

    Args:
        hass: Home Assistant instance.
        llm_context: Context the APIs are instantiated under.
        tool_name: Name of the tool to find.
        api_id: Restrict the search to a single API.

    Returns:
        The matching tool and the API instance that owns it.

    Raises:
        ServiceValidationError: The tool is unknown, ambiguous, or `api_id`
            names an API that is not registered.
        HomeAssistantError: An API raised while being prepared.
    """
    instances = await async_get_api_instances(hass, llm_context, api_id)
    matches = [
        ToolLocation(
            api_id=instance.api.id,
            api_name=instance.api.name,
            tool=tool,
            instance=instance,
        )
        for instance in instances
        for tool in instance.tools
        if tool.name == tool_name
    ]

    if not matches:
        scope = f"LLM API '{api_id}'" if api_id else "any registered LLM API"
        available = ", ".join(
            sorted({f"'{tool.name}'" for i in instances for tool in i.tools})
        )
        raise ServiceValidationError(
            f"Tool '{tool_name}' is not provided by {scope}. "
            f"Available tools: {available or '(none)'}"
        )

    if len(matches) > 1:
        owners = ", ".join(sorted({f"'{match.api_id}'" for match in matches}))
        raise ServiceValidationError(
            f"Tool '{tool_name}' is provided by more than one LLM API ({owners}). "
            f"Set 'api_id' to choose which one to call."
        )

    return matches[0]


def validate_tool_args(tool: llm.Tool, args: dict[str, Any]) -> None:
    """Check `args` against the tool's own schema.

    Core's `APIInstance.async_call_tool` does not validate arguments — only some
    tools validate themselves, and the rest fail somewhere deeper with a message
    that says nothing about which argument was wrong. Validating up front is
    what turns a bad automation into a readable trace entry.

    The validated (possibly coerced) value is deliberately discarded: the tool
    is then called with exactly the arguments it would have received from a
    conversation agent, so this integration cannot make a tool behave
    differently from the LLM path.

    Args:
        tool: The tool that will be called.
        args: Arguments supplied by the caller.

    Raises:
        ServiceValidationError: The arguments do not match the tool's schema.
    """
    try:
        tool.parameters(args)
    except SCHEMA_INVALID as err:
        raise ServiceValidationError(
            f"Invalid arguments for tool '{tool.name}': {err}"
        ) from err


async def async_call_tool(
    hass: HomeAssistant,
    llm_context: llm.LLMContext,
    tool_name: str,
    args: dict[str, Any],
    api_id: str | None = None,
) -> Any:
    """Resolve and run one LLM tool, with no conversation agent involved.

    Args:
        hass: Home Assistant instance.
        llm_context: Context the tool is prepared and executed under.
        tool_name: Name of the tool to call.
        args: Arguments passed to the tool.
        api_id: Restrict the search to a single API.

    Returns:
        Whatever the tool returned, normally a JSON object.

    Raises:
        ServiceValidationError: Unknown API, unknown or ambiguous tool, or
            arguments that fail the tool's schema.
        HomeAssistantError: The tool raised while running.
    """
    location = await async_resolve_tool(hass, llm_context, tool_name, api_id)
    validate_tool_args(location.tool, args)

    tool_input = llm.ToolInput(tool_name=tool_name, tool_args=args)
    try:
        return await location.instance.async_call_tool(tool_input)
    except ServiceValidationError:
        raise
    except Exception as err:
        raise HomeAssistantError(
            f"Tool '{tool_name}' from LLM API '{location.api_id}' failed: {err}"
        ) from err
