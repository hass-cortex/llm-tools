"""Constants for the LLM Tools integration."""

from __future__ import annotations

DOMAIN = "llm_tools"

# ============================================================================
# Services
# ============================================================================

SERVICE_LIST_APIS = "list_apis"
SERVICE_LIST_TOOLS = "list_tools"
SERVICE_CALL_TOOL = "call_tool"

# ============================================================================
# Service call fields
# ============================================================================

ATTR_API_ID = "api_id"
ATTR_ARGS = "args"
ATTR_LIMIT = "limit"
ATTR_SEARCH = "search"
ATTR_TOOL = "tool"

# ============================================================================
# Service response keys
# ============================================================================

KEY_APIS = "apis"
KEY_TOOLS = "tools"

# ============================================================================
# Defaults
# ============================================================================

# Domain of the `conversation` integration. Tools that gate on entity exposure
# (the Assist API in particular) read `LLMContext.assistant` to decide which
# entities count as exposed; "conversation" is the assistant every voice and
# chat path in Home Assistant uses, so it is the only default under which a
# caller sees the same tools an assistant would.
DEFAULT_ASSISTANT = "conversation"
