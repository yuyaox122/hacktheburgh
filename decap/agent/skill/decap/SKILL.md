---
name: decap
description: Read a chip like code. Use when asked what a gate-level netlist or Tiny Tapeout chip does, whether anything is hidden in it, or to prove a decompiled Verilog matches its gates. Drives Decap's tools via the `decap` CLI or MCP server and reports proof verdicts exactly.
---

# Decap: read silicon, with proof

Decap decompiles a chip's gate-level netlist (names stripped) into readable Verilog and proves the result with Yosys.

## How to use
- If the `decap` MCP server is connected, call its tools directly.
- Otherwise run the CLI from the repo root: `decap/agent/.venv/bin/decap <command>`:
  - `decap tools`: list the tools.
  - `decap call <tool> -a key=value ...`: call one tool, JSON out.
  - `decap demo --n <0-255>`: the full hidden-trigger story without an LLM.

## Finding hidden logic
1. `find_rare_triggers` on the chip.
2. For each cone marked `likely: "unexplained"`: `cone_of_influence`, then `prove_equiv` with `against="author_source"`.
3. A `failed` verdict's `counterexample` is the input that triggers it. Report it as the code line Decap returned.

## Rules
- Quote ids, numbers and verdicts exactly as the tools return them. Never guess.
- `proven` = Yosys proof passed; `tested` = simulation only, not proven. Never upgrade a verdict.
- If results say `"source": "stub"`, say so.
