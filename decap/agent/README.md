# Decap agent layer (`decap/agent/`)

Owner: **@izhangfly** (AGENTS.md section 4). The rest of `decap/` is Yuyao's core.

The core reads a chip. This layer puts an AI agent on top of it and makes the agent's work checkable. The agent
is called **Witness**. It can look at any part of a chip and say what it does, but it can't decide whether it is
right. Yosys decides that. So every sentence Witness shows you has one of three labels: proven, refuted (with
the input that breaks it), or only tested.

What's in here:

- **MCP server.** Every route of the core API becomes a tool that Claude Code, JetBrains Air/Junie or Cursor can
  call. FastMCP's `from_fastapi` generates them, so nothing is written twice.
- **Witness, the region agent.** Point it at a region and it proposes one claim about it. Yosys checks the claim,
  and if it's wrong the agent gets the counterexample and tries again.
- **The Witness terminal.** Type what you want in plain English. Simple asks run a fixed flow. Bigger ones go to
  an investigator that plans its own steps.
- **`decap` CLI.** The same things from a shell, plus a no-LLM version of the demo.
- **Transcript.** Every tool call and answer becomes one JSONL line, whichever client made it. The UI streams it,
  replay re-runs it, and the bench scores it.

## Quick start

```bash
cd decap/agent
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/decap tools                 # the tools an agent sees
.venv/bin/decap demo --n 137          # plant 137, find it, prove it. No LLM.
.venv/bin/decap agent-send "audit this chip" --chip tt_um_kapilan_alarm   # needs a model key, see below
.venv/bin/pytest -q
```

Without a real core you get a stub (`decap_agent/stub_core.py`): a made-up 8-bit counter, with every result
marked `"source": "stub"`. To use the real core, run from a checkout whose `decap/data/` is fetched
(`cd decap && python -m ingest.fetch`) with `brew install yosys` done:

```bash
export DECAP_CORE_APP=decap.api.main:app PYTHONPATH=$PWD
decap/agent/.venv/bin/decap demo --n 137          # first dev chip, or --chip tt_um_mac
DECAP_REAL_CORE_ROOT=$PWD decap/agent/.venv/bin/pytest -q decap/agent/tests
```

On `tt_um_Electom_cla_4bits` the demo takes about 3 s. It plants a trigger for 137 (9 cells, proven), finds
`ui_in == 8'd137` in `assign uo_out[0] = state0[2] | (ui_in == 8'd137);`, proves the netlist against the
author's source (failed, counterexample `ui_in = 137`), then removes the trigger (proven).

**Models.** Keys live in `~/.config/decap/secrets.env` (or `$DECAP_SECRETS`), never in the repo. DeepSeek
(`--model deepseek:deepseek-chat`) is the fast default for the terminal. `--model cli:sonnet` runs each request
through your local `claude` login (`decap_agent/cli_model.py`). Use it when the API key is capped. It can't
call tools, so it works for the bench and region claims but not for the investigator. **Gemini:** put `GOOGLE_API_KEY` (from AI Studio)
in the same secrets file and use `--model google-gla:gemini-flash-latest` (the `google` extra is in pyproject).

**Connect an IDE agent.** Claude Code: `claude mcp add decap -- decap/agent/.venv/bin/decap mcp`. JetBrains
Air picks up the repo's `.mcp.json`. Its paths are for Ian's Mac, so edit them on another machine. Anything
else: command `decap/agent/.venv/bin/decap`, args `mcp`.

## The Witness terminal

```bash
decap serve --port 8791 --model deepseek:deepseek-chat   # the web app's Agent drawer talks to this
decap agent-send "is anything hidden?" --chip tt_um_kapilan_alarm
decap status · decap log -f <session> · decap stop <session>
```

You type a command and the terminal routes it (`decap_agent/terminal.py`). The routing is plain keyword
matching, and it doesn't pretend to be more:

| You say | What runs | Typical time |
|---|---|---|
| "is anything hidden?", "trojan", "backdoor" | **hunt**: `find_rare_triggers`, then a whole-chip `prove_equiv` against the author's source | ~1 s |
| "what does b3 do?", "explain uo_out[0]" | **explain**: the Witness loop on that block or output | 5–30 s |
| "red team drill", "plant", "test yourself" | **drill**: plant a trigger at a secret random value, hunt it blind, reveal | ~10 s |
| "audit this chip", "...then...", "slowest path", "what do we know?" | **investigate**: plans its own steps (below) | ~30 s |
| anything else | **guide**: a Q&A agent whose only facts come from tool results | ~5 s |

**Hunt verdicts mean exactly this.** `found`: the proof failed, and its counterexample is the input that wakes
the hidden logic. `clean`: proven equivalent to the author's source. `inconclusive`: only a bounded check passed
(20 clock cycles on real chips). That isn't a proof, so a rare trigger could still be there. Before 8 Nov the
terminal said `clean` here. That was an overclaim, and it's fixed.

**The drill** (`_drill`) is the strongest live demo. The secret `N` never reaches the hunting tools, because they
only get the planted chip's id and the new cells are renamed. The drill passes only if the counterexample's
`ui_in` is exactly `N`. On `tt_um_kapilan_alarm`: secret 192, counterexample `ui_in = 192`, 9.5 s.

### The investigator (`decap_agent/investigate.py`)

This is the open-ended mode. The fixed flows run one script. The investigator picks its steps from what it
sees:

1. It writes a 2–5 step plan (`plan` event). It re-plans only if a result changes what it will do next.
2. Before every tool call it says, in one line, what it expects to learn (`thought` event).
3. Its tools: `recall` (chip memory), `overview` (blocks), `explain` (the Witness loop on one block, at most 3
   per run), `hunt`, `slowest_path`. It runs one at a time, so the reasoning reads in order.
4. Anything Yosys settled goes into chip memory, `runs/memory/<chip>.json` (`memory` event). The next question on
   that chip starts from it. This is our version of DimOS's spatial memory.
5. It ends with a report: a short summary, findings tagged by evidence (`yosys`, `proof`, `structure`,
   `estimate`, `memory`), and up to three next commands you can click.

Two guards stop it overclaiming. A `yosys` finding about a block that wasn't explained in this run is relabelled
`unverified`. If the hunt was inconclusive and the summary still says "no hidden logic", a correction is added.

## Witness: the region loop (`decap_agent/witness.py`, plan in docs/decap/witness-plan.md)

```
decap explain CHIP --cone-of 'uo_out[0]'      # look, claim, check, refine (max 2)
decap claim CHIP "expr" --cone-of X [--dry]    # one claim straight to the checker; --dry = parse + English only
```

The LLM sees only the look tools: `get_region`, `probe_truth`, `simulate`, `resolve_scope`. It returns one
`{claim, label, gist}`. The controller, not the LLM, sends it to `propose_claim`, and Yosys checks it through
Yuyao's `core/claims.py`. If the verdict is refuted, tautology, unparseable or restated, the LLM gets the verdict
and counterexample back and may refine, at most twice in the bench and four times in the web app. Every attempt
is a `claim` event, and the whole run is cached in `runs/cache/`.

- **The first draft is counted honestly.** The LLM can't call `propose_claim`, so attempt 0 is always its claim
  with a simulator and no proof. That first-draft rate is the headline number.
- **`probe_truth` is evidence, never a verdict.** It runs random vectors through the simulator (all of them if
  the region has 12 input bits or fewer), plus the rows where a rare 1-bit output fired.
- **Claim syntax** (`claim_expr.py`): a Verilog expression over ports and flip-flops. `next(x)` means x one clock
  later. `A when B` means B implies A. `rst_n` and `ena` are assumed 1. Verilog's unsigned width rules apply, so
  `next(count) == count + 1` is refuted at count = 255 while `count + 8'd1` is proven. The parser also accepts
  commas as `&&`, real `(expr)[hi:lo]` slices, and drops prose after the expression.

**When a claim earns credit.** It has to be true and also useful:
- **Complete.** It pins down every output bit it names, for every input. Otherwise it's `partial`. On its first
  run the agent found the loophole this closes: "once set, bit0 stays set" is true and short but says almost
  nothing.
- **Covers the whole region.** Every output bit counts, through any alias. Otherwise it's `partial`. On Yuyao's
  real checker the agent next tried `uio_out[7] == bit0`. It's true and complete, but that pin just copies the
  alarm flip-flop, so the claim skipped the 50 literals of logic behind it.
- **Shorter than the code.** It needs under ⅓ of `cone_literals`, or a recognised shape in `label`. Otherwise
  it's `restated`.

**Why the proof matters, in one example.** A claim with a planted bug passed 4096 plain random vectors: kapilan's
alarm was made to compare hour bit 0 with 0 instead of `ui_in[0]`. The 11-bit alarm match almost never happens
at random. Biased patterns caught it. Yosys catches it by construction.

**Budget.** The controller puts the region and a 1024-vector probe in the first prompt. The LLM may probe twice
more, within 6 requests and 120k input tokens per attempt. Before the cap, one 23-input region cost 1.37M tokens.
Now it costs about 3k.

**Cache key:** netlist sha, decompiler commit, chip, scope, model, prompt version + instructions hash, max_refine.

## The bench and the Monte Carlo ablation

```
decap bench --split dev [--max-regions 4] [--model cli:sonnet]   # tune on dev chips only
decap bench --freeze-hash                                        # post this before scoring the held-out split
decap bench --split test --frozen ...                            # refuses to run without --frozen
decap bench --ablate runs/<bench>.jsonl                          # re-check every claim by simulation, no LLM
```

- **Which regions.** The rule was set before any test chip was seen: every block the templates couldn't name
  (`recognised: false`), largest first, at most `--max-regions` per chip.
- **What it reports.** First-draft outcomes; the semantic error rate with a bootstrap 95% CI clustered by chip;
  explained coverage of unrecognised cells; median compression; honesty counts by chip.
- **The ablation** answers Ian's Monte Carlo question. Every claim is re-checked with 1024 random vectors and the
  core's 4096 biased ones, then compared with Yosys. Each Yosys counterexample is replayed in the simulator
  first. If the simulator disagrees, that's a model mismatch, such as a cut ring-oscillator loop, not a sampling
  miss, so it's excluded.
- **Dev dry run** (3 chips, 5 regions, claude CLI, 6 min). The kapilan alarm blob was proven on its first draft.
  segdecode was refuted three times. Over 12 claims Yosys refuted 3. Of the 2 comparable ones, random and biased
  testing passed both. Simulation never contradicted a proof. That's a small sample from dev chips. The
  held-out run gives the real number.
- **Overnight.** `scripts/overnight_bench.sh` waits for the `bench-freeze` tag, runs the held-out split with
  `cli:sonnet` at concurrency 1, and writes `out/witness-summary.json` for the Results card.

## The formaliser

```
decap formalise "LED 3 lights when switches 1 and 2 are on" [CHIP --cone-of X] [--check]
```

One LLM call turns a person's sentence into a formal line. It also lists the reading choices it made and, if the
sentence is ambiguous, the other reading. The line is parse-checked, with one retry. With a chip, the checker's
English comes back so the person can confirm "yes, that's what I meant". `--check` then runs the proof. By
default switch k is `ui_in[k]` and LED k is `uo_out[k]`. The web app can pass its own legend.

On 30 sloppy phrasings, scored by logical equivalence (which catches the width traps: "plus one" must become
`+ 8'd1`, "times two" `<< 1`), claude-sonnet-5-5 and claude-haiku-5-5 both scored 30/30 (median 3.0 s and 2.6 s).

## The contract with the core

With `from_fastapi`, Yuyao's API *is* the tool definition:

| FastAPI element | Becomes |
|---|---|
| `operation_id` | the tool name (e.g. `prove_equiv`) |
| the route's docstring | the description the LLM reads, so write it for an agent |
| path params, body fields, `Field(description=…)` | the tool's input schema |
| the response | the structured result in the transcript |

**Region scope.** Every chip tool takes an optional `scope` and returns what it `touched`, so the 3D view can
light it up:

```json
"scope":   {"cells": ["trg_3"], "nets": ["hit"], "cone_of": "out"}      // any mix; empty = whole chip
"touched": {"cells": ["trg_0", "trg_1", ...], "nets": ["trig", "ui_in[0]", ...]}
```

`resolve_scope` turns a selection into exact cones and cells. A scope that matches nothing is an error. It never
silently widens to the whole chip.

**Two traps we hit (FastMCP 4.1 + FastAPI 0.162).** FastAPI treats a body as required even when all its fields
have defaults, so an agent that sends only `chip` gets a 422. Declaring it `body: Model | None = None` is worse.
The schema becomes `anyOf` and FastMCP silently drops the body's arguments. In our test a planted chip then
"proved" equal to its source. Use `body: Model = Body(default_factory=Model)`.
`tests/test_agent_layer.py::test_arguments_reach_the_core` guards it.

**Strict arguments.** On its own the bridge only warns about an argument a tool doesn't have, then drops it, so a
misspelt `against` would quietly get the default. `StrictArgsMiddleware` turns that into an error that lists the
valid arguments.

## Transcript format

One JSON object per line. Kinds: `tool`, `claim`, `agent`, `note`, plus `plan`, `thought`, `memory` and
`session` from the terminal.

```json
{"seq": 3, "ts": "2026-11-07T19:40:01.123+00:00", "run_id": "a1b2c3", "kind": "tool",
 "tool": "find_rare_triggers", "args": {"chip": "stub_counter8+t137"},
 "result": {"flagged": [...], "source": "stub"}, "is_error": false, "wall_s": 0.004}
```

Stream one: `decap stream runs/<file>.jsonl --port 8790`, then `EventSource("http://127.0.0.1:8790/events")`.

## Built on

- [FastMCP](https://github.com/PrefectHQ/fastmcp) (Apache-2.0): MCP server and client, `from_fastapi`, middleware.
- [Pydantic AI](https://github.com/pydantic/pydantic-ai) (MIT): agents, model providers, MCP toolset, token usage.
- Design borrowed from [DimOS](https://github.com/dimensionalOS/dimos): skills over MCP, a CLI for agents, the
  transcript as a stream, replay, and the terminal's look.

## Ideas: edit the decompiled Verilog, Yosys reviews it (issue #68, JetBrains "Help the Developer")

In the web app's code pane: hover a line, click **Ideas**, optionally say what you want. The agent proposes 2-3 edits;
**Apply** puts one into the Verilog and the core checks it against the chip's real gates. Same rule as Witness: the
LLM only proposes, Yosys decides, and the verdict text is written by code.

```
POST /witness/ideas   {chip, line, lines: [a, b], verilog, ask, history: [{role: user|agent, text}]}
  -> {reply, ideas: [{id, title, why, intent: same|change, lines: [a, b], replacement: [lines]}], model, dropped}
POST /api/chips/{chip}/review-edit  {verilog, baseline?}      (core; MCP tool review_edit)
  -> {verdict: same|tested_same|changed|broken, headline, detail, counterexample, seconds, this_edit?}
```

- `decap_agent/ideas.py`: one LLM call (pydantic-ai, output `IdeasOut`), same model resolution as Witness
  (`create_witness_app(model=...)`, else `DECAP_WITNESS_MODEL`, else `witness.DEFAULT_MODEL`; `cli:sonnet` works). The
  prompt is small: the region with line numbers, the whole module (a window around the region above 400 lines), the
  ask and the last 10 turns.
- **Validation in code**, not trusted to the model: an idea is dropped if its lines are outside the file, touch the
  `module ... );` header or `endmodule`, add `module`/`endmodule`, or change nothing; at most 3 are kept, and `dropped`
  says why the rest went (and the reply says how many were dropped). Bad requests are 400, a model failure is 502
  with the error. The run log's file name uses a cleaned chip id, so a request can't write outside runs/.
- **`review_edit` is a normal core tool**, so Claude Code, JetBrains Air/Junie or Cursor can call it over MCP: decompile
  a chip, edit the Verilog, then ask Yosys whether the edit is a safe refactor (`same`), only tested (`tested_same`),
  a behaviour change (`changed`, with the first clock step and inputs where the outputs differ, or a changed clock)
  or can't be kept (`broken`: doesn't compile, ports changed, a wiring problem, x/z, simulation-only code, or Yosys
  couldn't finish). Pass `baseline` (the text before this edit) to also get `this_edit`, what this edit alone does.
  See decap/CONTRACTS.md section 4.
- Live check (`tt_um_Electom_cla_4bits`, line 15, no ask, `cli:sonnet`, 5.8 s): "Name the adder inputs" (same) and
  "Widen sum explicitly" (same) were both proven the same circuit; "Gate update with ena" (change) was reported as
  changed at clock cycle 3. Each review took 0.1-0.3 s.

