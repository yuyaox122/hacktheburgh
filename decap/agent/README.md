# Decap agent layer (`decap/agent/`)

Owner: **@izhangfly** (AGENTS.md section 4). The rest of `decap/` is Yuyao's core.

This layer lets any AI agent use Decap's tools, and lets the judges watch it happen:

- **MCP server.** Every route of the Decap core API becomes an MCP tool, via FastMCP's `from_fastapi`, so the tools aren't hand-written twice. Claude Code, JetBrains Air/Junie and Cursor can all call them.
- **Guide agent.** A pydantic-ai agent (spec §6.5) whose *only* source of facts is those tools.
- **`decap` CLI.** Includes a scripted, no-LLM version of the demo's judge moment.
- **Transcript.** One JSONL event per tool call or agent answer, from any client. It feeds the UI's live timeline (SSE), replay, and the data for any calibration work.

**Status: v0, against a STUB core** (`decap_agent/stub_core.py`), a made-up 8-bit counter. Every stub result says `"source": "stub"`. Once Yuyao's FastAPI app exists, point at it and nothing else changes:

```bash
export DECAP_CORE_APP=decap.api.main:app   # module:attr of the core FastAPI app (name TBC in decap/CONTRACTS.md)
```

## Quick start

```bash
cd decap/agent
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/decap tools                 # what agents see
.venv/bin/decap demo --n 137          # plant 137 → find it → prove it, no LLM
.venv/bin/decap call find_rare_triggers -a chip=stub_counter8+t137   # (after demo, in one process: use demo)
.venv/bin/decap ask "Is anything hidden in stub_counter8?"           # needs ANTHROPIC_API_KEY (or set DECAP_GUIDE_MODEL)
.venv/bin/pytest -q
```

Connect Claude Code (from the repo root): `claude mcp add decap -- decap/agent/.venv/bin/decap mcp`.
For JetBrains Air/Junie or Cursor, add an MCP server with command `decap/agent/.venv/bin/decap` and args `mcp`.

## How it fits

```
Claude Code · JetBrains Air/Junie · Cursor          Web app (Yuyao): chat + agent timeline
            │ MCP (stdio / HTTP)                            ▲ SSE /events     │ /chat
            ▼                                               │                 ▼
┌──────── decap/agent ─────────────────────────────────────┴─────────────────────────┐
│ FastMCP server = FastMCP.from_fastapi(core_app)  ── TranscriptMiddleware ─► runs/*.jsonl │
│ Guide agent (pydantic-ai, MCPToolset → same server; tokens + model logged)          │
│ decap CLI · replay --check · SKILL.md for Claude Code                               │
└───────────────┬─────────────────────────────────────────────────────────────────────┘
                ▼ in-process ASGI (or HTTP)
     Decap core (Yuyao): FastAPI → block recovery, decompiler, Yosys proofs, plant/flag
```

## The contract with the core

With `from_fastapi`, Yuyao's API *is* the tool definition:

| FastAPI element | Becomes |
|---|---|
| `operation_id` | the tool name (e.g. `prove_equiv`) |
| the route function's docstring | the description the LLM reads (write it for an agent) |
| path params, body model fields, `Field(description=…)` | the tool's input schema |
| the response model or dict | the structured result recorded in the transcript |

### Region scope: agents aimed at a selected part of the chip (Yuyao's ask, 7 Nov 19:31)
Every tool that works on a chip takes an optional `scope` and returns what it `touched`, so the 3D view can light it up:

```json
"scope":   {"cells": ["trg_3"], "nets": ["hit"], "cone_of": "out"}      // any mix; empty = whole chip
"touched": {"cells": ["trg_0", "trg_1", ...], "nets": ["trig", "ui_in[0]", ...]}
```

- `resolve_scope(chip, scope)` turns a user's selection into the exact cones and cells; pass the same scope on to other tools.
- A scope that matches nothing is an **error**, never a silent widening to the whole chip.
- The transcript copies `scope`, `cells` and `nets` to the top level of each tool event, for the timeline and the 3D highlight.
- A real core should return real netlist cell ids (the ids TT Explorer / the 3D view use) in `touched.cells`.

**⚠️ Two traps found while building (FastMCP 4.1 + FastAPI 0.162):**
- A request body whose fields all have defaults is still *required* by FastAPI, so an agent that sends only `chip` gets a 422.
- Declaring it `body: Model | None = None` is worse: the schema becomes `anyOf` and FastMCP **silently drops** the body's arguments. In our test, a planted chip then "proved" equal to its author's source.

**Use `body: Model = Body(default_factory=Model)`.** `tests/test_agent_layer.py::test_arguments_reach_the_core` guards this.

The stub uses the names spec §6.1 implies: `list_chips`, `strip_names`, `recover_blocks`, `decompile`, `prove_equiv`, `simulate`, `find_rare_triggers`, `plant_trigger`, `cone_of_influence`, `critical_path`. Yuyao's real names win; the guide's rules and the demo script follow whatever `decap/CONTRACTS.md` says.

## Transcript format (for the UI timeline)

One JSON object per line:

```json
{"seq": 3, "ts": "2026-11-07T19:40:01.123+00:00", "run_id": "a1b2c3", "kind": "tool",
 "tool": "find_rare_triggers", "args": {"chip": "stub_counter8+t137"},
 "result": {"flagged": [...], "source": "stub"}, "is_error": false, "wall_s": 0.004}
{"seq": 9, "kind": "agent", "question": "...", "answer": "...", "model": "claude-sonnet-5-5",
 "input_tokens": 2810, "output_tokens": 164, "requests": 4, "tool_calls": 3, "wall_s": 6.2}
```

Live: `decap stream runs/<file>.jsonl --port 8790`, then `EventSource("http://127.0.0.1:8790/events")`, or fetch `/events.json`.

## Built on (credit)
- [FastMCP](https://github.com/PrefectHQ/fastmcp) (Apache-2.0): the MCP server and client, `from_fastapi`, middleware.
- [Pydantic AI](https://github.com/pydantic/pydantic-ai) (MIT): the guide agent, model providers, MCP toolset, token usage.
- Design borrowed from [DimOS](https://github.com/dimensionalOS/dimos): skills over MCP, a CLI for agents, transcript as a stream, replay.
