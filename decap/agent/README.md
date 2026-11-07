# Decap agent layer (`decap/agent/`)

Owner: **@izhangfly** (AGENTS.md section 4). The rest of `decap/` is Yuyao's core.

This layer lets any AI agent use Decap's tools, and lets the judges watch it happen:

- **MCP server.** Every route of the Decap core API becomes an MCP tool, via FastMCP's `from_fastapi`, so the tools aren't hand-written twice. Claude Code, JetBrains Air/Junie and Cursor can all call them.
- **Guide agent.** A pydantic-ai agent (spec §6.5) whose *only* source of facts is those tools.
- **`decap` CLI.** Includes a scripted, no-LLM version of the demo's judge moment.
- **Transcript.** One JSONL event per tool call or agent answer, from any client. It feeds the UI's live timeline (SSE), replay, and the data for any calibration work.

**Status: v0.3 adds the Witness region agent loop (below), stub-checked. v0.2 was verified against Yuyao's real core** (branch `4-decap-build`; 13 tools generated from its API). On the real Tiny Tapeout chip `tt_um_Electom_cla_4bits`, `decap demo --n 137` does the following in about 3 s:
1. plants 137 (9 cells, proven);
2. finds `ui_in == 8'd137` at `assign uo_out[0] = state0[2] | (ui_in == 8'd137);`;
3. proves the netlist against the author's source: **failed**, with counterexample `ui_in = 137`;
4. runs `remove_trigger`: **proven**.

Without a core, a STUB (`decap_agent/stub_core.py`, a made-up counter, every result `"source": "stub"`) is served instead. The stub also models **region scope**, which the real core doesn't have yet.

```bash
# from a checkout of 4-decap-build whose decap/data/ is fetched (cd decap && python -m ingest.fetch); needs `brew install yosys`
export DECAP_CORE_APP=decap.api.main:app PYTHONPATH=$PWD
decap/agent/.venv/bin/decap demo --n 137          # real chip (first dev chip, or --chip tt_um_mac)
DECAP_REAL_CORE_ROOT=$PWD decap/agent/.venv/bin/pytest -q decap/agent/tests   # includes the real-core test
```

**Strict arguments:** the FastAPI bridge only *warns* and drops arguments a tool doesn't have, so an agent that misspells `scope` or `against` would silently get the default. `StrictArgsMiddleware` turns that into a tool error that lists the valid arguments.

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

## Witness: the region agent loop (v0.3, plan: docs/decap/research/witness-plan.md)

An agent dropped on a region explains it in **one claim**; only the checker decides whether it is shown.

```
decap explain CHIP --cone-of 'uo_out[0]'      # LLM: look, claim, check, refine (max 2)
decap claim CHIP "expr" --cone-of X [--dry]    # one claim straight to the checker; --dry = parse + English only
```

```
 explain_region (decap_agent/witness.py)
   LLM (pydantic-ai, output_type=Claim) ── sees ONLY get_region, probe_truth, simulate, resolve_scope
        │ returns {claim, label, gist}
   controller ── propose_claim ──► checker (stub: exhaustive sim; real: core/claims.py + Yosys, Yuyao's)
        │ refuted / tautology / unparseable / restated → verdict + counterexample back to the LLM (≤ 2 refinements)
        └ every attempt → transcript event kind="claim" {attempt, first_draft, claim, verdict, counterexample,
          english, credit, cells, model, tokens, wall_s}; whole run cached in runs/cache/
```

- **The first draft is counted honestly.** The LLM cannot call `propose_claim`, so attempt 0 is always its
  claim with a simulator and no proof. That is the plan's headline number (section 4).
- **`probe_truth` is the Monte Carlo look:** random vectors through the simulator (every vector if the region has
  ≤ 12 input bits), plus the rows where a rare 1-bit output fired. Evidence, never a verdict.
- **Claim syntax** (`decap_agent/claim_expr.py`): a Verilog expression over ports and flip-flops, `next(x)` = one
  clock later, `A when B` = B → A; rst_n = ena = 1 assumed; Verilog's unsigned width rules, so
  `next(count) == count + 1` is refuted at count = 255 and `count + 8'd1` is proven.
- **Cache key:** netlist_sha, decompiler commit, chip, scope, model, prompt version + instructions hash.
- **Contract proposed to the core** (board, 7 Nov 20:32): `get_region` and `probe_truth` (region description and
  samples) and `propose_claim` → `{verdict: proven|inductive|bounded|refuted|restated|tautology|unparseable|timeout,
  formal, english, counterexample, refute_kind, credit {cleared, reason, literals, cone_literals}, touched}`.
  The stub implements all three on `stub_counter8` so the loop runs offline.

Live check (stub chip, `claude-sonnet-5-5`, about 10 s): the agent claimed
`next(count) == (en ? count + 8'd1 : count)` labelled counter, which was proven on its first draft and credited
(4 literals vs 40 in the decompiled cone).

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
