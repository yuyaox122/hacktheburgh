# For coding agents: how to drive Decap

Decap turns a chip's gate-level netlist (every name stripped) back into readable Verilog, and proves the result with Yosys.
You reach it through MCP tools (`decap mcp`) or the CLI (`decap/agent/.venv/bin/decap`).

## Connect
- Claude Code: `claude mcp add decap -- decap/agent/.venv/bin/decap mcp`
- Any MCP client: command `decap/agent/.venv/bin/decap`, args `["mcp"]` (stdio). Or `decap mcp --http 8791` for HTTP.
- No MCP? Use `decap call <tool> -a key=value` and read the JSON.

## Rules (non-negotiable)
1. Every chip id, cone id, number and verdict you report must come from a tool result. Quote it exactly.
2. `proven` = a Yosys proof passed. `tested` = random simulation only, **not proven**. `failed` = disproved. Never upgrade a verdict.
3. If a result says `"source": "stub"`, say you are looking at the stub core, not a real chip.
4. Don't open or read the benchmark test set's author sources before a run is scored (spec §5, held-out rule).

## Region scope
Most tools take `scope` = `{"cells": [...], "nets": [...], "cone_of": "<output or register>"}` (empty = whole chip) and return
`touched.cells` / `touched.nets`. When the user points at part of the chip, call `resolve_scope` first, then pass the same
scope to every later call, so you only work (and only claim results) inside that region.

## Recipes
- **"Is anything hidden here?"** (`here` = the user's region) `resolve_scope` → `find_rare_triggers(chip, scope)` → for each `likely: "unexplained"` cone, `cone_of_influence` → `prove_equiv(chip, scope, against="author_source")`. A `failed` proof's `counterexample` is the input that wakes the hidden logic; `touched.cells` is where it lives.
- **"What does this chip do?"** `strip_names` → `recover_blocks` → `decompile`; report proven/total.
- **"Show it running."** `simulate(chip, cycles, ui_in, en)`.
- **Try the whole story with no LLM:** `decap demo --n 137`.
