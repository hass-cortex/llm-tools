# LLM Tools for Home Assistant

[![GitHub Release](https://img.shields.io/github/v/release/hass-cortex/llm-tools)](https://github.com/hass-cortex/llm-tools/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-blue.svg)](https://hacs.xyz/)
[![HA Version](https://img.shields.io/badge/HA-2026.8.0+-green.svg)](https://www.home-assistant.io/)
[![GitHub License](https://img.shields.io/github/license/hass-cortex/llm-tools)](https://github.com/hass-cortex/llm-tools/blob/main/LICENSE)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/hass-cortex/llm-tools)

**Run an MCP tool from Home Assistant without an agent.** Connect an MCP server
to HA, then call any of its tools by name from an automation, a script, a sentence
trigger or a dashboard button — no model, no inference, no round-trip.

MCP is built around a model doing the choosing: a server publishes tools with
descriptions, and an agent reads them and decides what to call. Home Assistant's
MCP client already brings a server's tools into the instance, but only two things
could invoke them — a conversation agent, or `mcp_server` handing them back out
over HTTP to a client somewhere else. The tools were in the house and still out of
reach of everything that is not a model.

```
      MCP server                  Assist API
           │ tools                     │
           ▼                           │
     HA MCP client                     │
           │ one API per server        │
           └─────────────┬─────────────┘
                         ▼
               HA LLM tool catalogue
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
     conversation agent         llm_tools
       a model chooses       your automation
          the tool           names the tool
```

## What it looks like

"What's on my list today" is the shape this integration exists for. A
conversation agent can answer it — understand the phrase, pick a tool, read the
result, phrase a reply, four model steps that can each go a different way. Here
the whole thing is one automation and nothing infers anything.

```yaml
automation:
  - triggers:
      - trigger: conversation
        command:
          # [optional] words and (a|b) alternatives, so the short form is
          # what you actually say and the long one still matches
          - "[what is on] my list today"
          - "[my] (tasks|todos) today"
    actions:
      # 1. Read — one MCP tool call, no model deciding which
      - action: llm_tools.call_tool
        data:
          api_id: mcp-notion
          tool: notion-query-data-sources
          args:
            data:
              mode: view
              view_url: "https://www.notion.so/<workspace>/Tasks-<id>?v=<view>"
        response_variable: notion

      # 2. Filter and assemble — plain Jinja over the tool's own object.
      #    Property names are your data source's, not Notion's.
      - variables:
          due_today: >-
            {{ notion.results | default([], true)
               | selectattr('Status', 'in', ['To Do', 'Doing'])
               | selectattr('Due', 'eq', now().strftime('%Y-%m-%d'))
               | list }}

      # 3. Answer — count, then each item, with its time when it has one.
      #    The {%- -%} markers matter: without them the folded block leaves
      #    newlines in the string and the assistant reads them out.
      - set_conversation_response: >-
          {%- if due_today -%}
            {{ due_today | count }} thing{{ "s" if due_today | count != 1 }} today:
            {%- for t in due_today %} {{ t.Name }}
              {%- if t.Time %} at {{ t.Time }}{% endif %}
              {{- "," if not loop.last }}
            {%- endfor -%}
          {%- else -%}
            Nothing due today
          {%- endif -%}
```

Read, filter, answer. The same three steps cover most of what people want an
assistant for, and the middle one — the part that decides what matters — is a
template you can read and a trace you can replay, not a prompt.

Swap step 3 for another `call_tool` and the same pattern writes instead of
speaks; swap step 1's trigger for `time_pattern` and it runs on a schedule with
nobody asking.

Two things follow, and the second matters more:

- **The model is skipped** where the caller already knows which tool to run. A
  sentence trigger matching a fixed phrase does not need one to work out what it
  meant.
- **Tools become reachable at all.** An MCP tool has no Home Assistant action
  behind it — the LLM API is its only door. So a dashboard tap, a physical button
  or a nightly cron had no way to it. Now they call an action like anything else.

**A connected MCP tool *is* an HA LLM tool.** The MCP client registers each
server as an API in `homeassistant/helpers/llm.py`, and from there its tools are
the same objects the Assist agent would be handed and the same ones `call_tool`
runs — same name, same JSON schema, same result. That is why everything below
says *LLM tool*.

The catalogue holds **APIs, not loose tools**, and core registers exactly two
kinds: the single **Assist** API, and **one API per connected MCP server**.
Scripts, calendar and to-do lists do not appear beside them — they implement the
`llm` tool *platform* (`components/<domain>/llm.py`) and contribute their tools
*into* Assist, each one returning nothing when asked for any other API. So
`list_apis` shows you servers and Assist; `list_tools` is where the scripts and
calendars surface.

## Features

- **`call_tool`** — run one tool by name and get its result back, verbatim.
- **`list_apis` / `list_tools`** — read the catalogue at runtime, with search and
  a limit, each tool's parameters rendered as a JSON schema.
- **Callable from anywhere an action is** — automations, scripts, sentence
  triggers, dashboard `tap_action`, scheduled tasks.
- **Arguments validated up front** against the tool's own schema, so a bad
  automation reads as a clear message instead of failing deep inside a tool.
- **Errors split by cause**, so an automation trace says what to fix.

## Getting Started

**Prerequisites:** Home Assistant **2026.8.0+**, and at least one integration
that registers an LLM API.

### 1. Install

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=hass-cortex&repository=llm-tools&category=integration)

Click the button above, or manually: HACS > three-dot menu > **Custom
repositories** > add `https://github.com/hass-cortex/llm-tools` (Integration) >
install > restart HA.

<details>
<summary>Manual installation</summary>

Copy `custom_components/llm_tools/` to your HA `config/custom_components/`
directory, then restart.

</details>

### 2. Add Integration

[![Open your Home Assistant instance and start setting up this integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=llm_tools)

Click the button above, or manually: **Settings > Devices & Services > Add
Integration** > search "LLM Tools" > confirm. There is nothing to configure — the
tools come from whichever integrations have registered an LLM API.

### Uninstallation

[![Open your Home Assistant instance and show this integration.](https://my.home-assistant.io/badges/integration.svg)](https://my.home-assistant.io/redirect/integration/?domain=llm_tools)

**Settings > Devices & Services** > LLM Tools > three-dot menu > **Delete** >
(HACS) remove the repository or (manual) delete `custom_components/llm_tools/` >
restart HA.

The three actions are registered at startup rather than when the entry loads, so
an automation referencing them still validates while the entry is missing. They
therefore still appear after a delete — until the restart — and answer every call
with "LLM Tools is not set up" rather than silently doing nothing.

## Actions

| Action | Response | Purpose |
|--------|----------|---------|
| `llm_tools.list_apis` | always | Every registered LLM API: `id`, `name`, `tool_count` |
| `llm_tools.list_tools` | always | Each tool's `name`, `description` and `parameters` as a JSON schema |
| `llm_tools.call_tool` | optional | Runs one tool and returns its result |

### `list_apis`

```yaml
- action: llm_tools.list_apis
  response_variable: catalogue
# catalogue.apis == [
#   {"id": "assist", "name": "Assist", "tool_count": 12},
#   {"id": "mcp-notion", "name": "Notion", "tool_count": 22},
#   {"id": "mcp-01K9…", "name": "Internal wiki", "tool_count": 8},
# ]
```

Read your own ids here rather than copying one. The MCP client builds its id as
`mcp-<slug>` from what the server advertises, and falls back to `mcp-<entry_id>`
— the hex third entry above — for a server added by URL.

An API that fails while being prepared is still listed, with `tool_count: null`
and an `error` — one broken provider cannot hide the rest.

### `list_tools`

| Field | Required | Description |
|-------|----------|-------------|
| `api_id` | no | Restrict to one API. Omit for every registered API. |
| `search` | no | Keep tools whose name or description contains **every** word, ignoring case. Name matches rank first. |
| `limit` | no | Keep at most this many, applied after ranking. |

```yaml
- action: llm_tools.list_tools
  data:
    api_id: mcp-notion
    search: page
    limit: 10
  response_variable: catalogue
# catalogue.tools == [
#   {"api_id": "mcp-notion", "api_name": "Notion", "name": "notion-create-pages",
#    "description": "Creates one or more pages ...",
#    "parameters": {"type": "object", "properties": {...}, "required": [...]}},
#   ...
# ]
```

`parameters` is the tool's schema rendered as a JSON schema — the same
description a conversation agent is given, so what you read here is exactly what
the tool accepts.

### `call_tool`

| Field | Required | Description |
|-------|----------|-------------|
| `api_id` | yes | The API that provides the tool, as reported by `list_apis`. Required because a bare tool name breaks the day a second API registers the same name. |
| `tool` | yes | Tool name, as reported by `list_tools` |
| `args` | no | Arguments object. Validated against the tool's own schema first |

```yaml
# Search Notion from an automation, with no LLM in the loop
- action: llm_tools.call_tool
  data:
    api_id: mcp-notion
    tool: notion-search
    args:
      query: roadmap
      query_type: internal
      page_size: 25
  response_variable: found
# found == {"type": "workspace_search", "results": [
#   {"id": "...", "title": "Q3 roadmap", "url": "https://…", "type": "page",
#    "path": "DEV / Planning", "timestamp": "2026-01-27T02:41:00.000Z"},
#   ...
# ]}
```

Tool names and argument shapes come from the server you connected, so read them
off your own instance with `list_tools` rather than copying them. The ones here
are the hosted Notion MCP server's.

```yaml
# A dashboard button, for a tool with no action of its own to bind to
type: button
name: Refresh from Notion
tap_action:
  action: perform-action
  perform_action: llm_tools.call_tool
  data:
    api_id: mcp-notion
    tool: notion-search
    args:
      query: roadmap
      query_type: internal
```

To take free text from the sentence, put a wildcard in it — `remind me to {task}`
— and it arrives as `trigger.slots.task`. Two constraints bite silently: a
command sentence may not contain punctuation, and a wildcard at the *end* is
greedy enough to swallow utterances you meant for the conversation agent, so
bound it on both sides where the phrasing allows.

**Assist's own tools are the wrong thing to reach for here.** `HassTurnOn` and
its neighbours wrap actions you can already call, so `api_id: assist` is a slower
`light.turn_on`. Reach for a tool that has no other way in — one contributed by
an integration that never registered an action for it.

**The response is the tool's own object, verbatim.** Nothing is wrapped and no
key is invented. A tool returning something other than an object fails with a
message naming it, since Home Assistant requires an action response to be an
object. Omit `response_variable` if you do not need the result.

## Errors

| Situation | Raised as |
|-----------|-----------|
| Unknown `api_id`, unknown tool, arguments failing the tool's schema | `ServiceValidationError` — the message names what is registered or available |
| An API that cannot be prepared, a tool that raises, a tool returning a non-object | `HomeAssistantError` — the message names the tool and its API |

## Troubleshooting

| What you see | What it means |
|--------------|---------------|
| `LLM Tools is not set up` | No loaded config entry. Add the integration under Settings → Devices & Services. |
| `Unknown LLM API 'x'. Registered APIs: …` | Ids are per-instance, not portable. Run `list_apis` on *this* instance and use what it reports. |
| `Tool 'x' is not provided by …` | The tool is not in the catalogue under the context these actions use — see [Known limitations](#known-limitations). |
| A tool listed with empty `parameters` and an `error` key | Its schema could not be serialised. The listing survives on purpose; the reason is logged as a warning naming the tool. |
| An API listed with `tool_count: null` and an `error` | That API raised while being prepared. The other APIs are still listed. |
| The `api_id` dropdown is empty | It is filled at runtime from the registered APIs, on start and on any config-entry change. An API registered by something that never touches a config entry will not appear until a restart — type the id instead. |

**Download diagnostics** (Settings → Devices & Services → LLM Tools → ⋮ →
Download diagnostics) for the API list and the context it was read under.

## Known limitations

- **The timer tools are unreachable.** `intent` offers `intent__Hass*Timer` only
  to a device that supports timers, and no `LLMContext` field is exposed here, so
  they are not in the catalogue and `call_tool` reports the name as unknown.
- **Arguments are validated against the tool's declared schema before it runs**,
  which Home Assistant core does not do. A tool whose declared schema is narrower
  than what it actually accepts will reject a call here that a conversation agent
  might have got through.
- **Arguments reach the tool exactly as written.** The validated, possibly
  coerced value is discarded, so this integration cannot make a tool behave
  differently from the LLM path.
- **No conversation trace.** Tool calls appear in the automation trace, not under
  Settings → Voice assistants → debug.

## License

[MIT](./LICENSE)
