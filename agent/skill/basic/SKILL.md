---
name: basic
description: Read a chip like code. Use when asked what a gate-level netlist or Tiny Tapeout chip does, whether anything is hidden in it, or to prove a decompiled Verilog matches its gates. Drives bASIC's tools via the `basic` CLI or MCP server and reports proof verdicts exactly.
---

# bASIC: read silicon, with proof

bASIC decompiles a chip's gate-level netlist (names stripped) into readable Verilog and proves the result with Yosys.

## How to use
- If the `basic` MCP server is connected, call its tools directly.
- Otherwise run the CLI from the repo root: `.venv/bin/basic <command>`:
  - `basic tools`: list the tools.
  - `basic call <tool> -a key=value ...`: call one tool, JSON out.
  - `basic demo --n <0-255>`: the full hidden-trigger story without an LLM.

## Finding hidden logic
1. `find_rare_triggers` on the chip.
2. For each cone marked `likely: "unexplained"`: `cone_of_influence`, then `prove_equiv` with `against="author_source"`.
3. A `failed` verdict's `counterexample` is the input that triggers it. Report it as the code line bASIC returned.

## Rules
- Quote ids, numbers and verdicts exactly as the tools return them. Never guess.
- `proven` = Yosys proof passed; `tested` = simulation only, not proven. Never upgrade a verdict.
- If results say `"source": "stub"`, say so.


## Witness (explain a region, proven)
1. `get_region` with the user's scope: the names you may use, the decompiled lines and `cone_literals`.
2. `probe_truth`, optionally with `fix={"name": value}`, to see what the outputs do. This is evidence, not proof.
3. `propose_claim` with ONE Verilog expression covering every region output, e.g. `next(r) == (en ? r + 8'd1 : r)`.
4. Report the verdict exactly. If it's refuted, show the counterexample and refine, at most twice. Never call a `tested` or `partial` result proven.
