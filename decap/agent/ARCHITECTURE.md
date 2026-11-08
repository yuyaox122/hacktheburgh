# The Witness agent — architecture

A plain-English map of `decap/agent/`, so you can explain how the AI part of bASIC is put together. The rest of
the project (the decompiler and the Yosys proof, in `decap/`) is Yuyao's core; this folder is the agent layer that
sits on top of it and talks to it.

## The one idea that everything hangs off

**The agent proposes. Yosys decides.** The AI writes claims, explanations, edits and guesses. It is *never* allowed
to mark anything as true. Every claim goes through the core's Yosys proof, and the proof's verdict is the only
thing shown to a person. A wrong answer doesn't just get flagged — Yosys hands back the exact input that breaks
it, and that counterexample is the whole demo.

This is what makes the product honest: the model is replaceable (DeepSeek, Sonnet, Gemini), but the proof isn't.

## The two layers

```
   decap/web/src/agent/                    decap/agent/decap_agent/
   (browser, TypeScript)                   (server, Python)
   ─────────────────────                   ─────────────────────
   panel / terminal / store                witness_api.py   ← HTTP + SSE endpoints
   agent.ts / editor.ts / verilogIO.ts     terminal.py      ← natural-language router
   stageAgent.ts / codeAgent.ts            witness.py       ← the region claim loop
                                           investigate.py   ← the multi-step planner
                                           repair.py        ← find-and-fix a gate
                                           plain.py         ← plain-English explanations
                                           guide.py         ← free-form Q&A
                                           server.py        ← the MCP server
                                           ...              ← the rest (see below)
```

The browser never talks to Yosys directly. It talks to `witness_api.py`, which is the one door into the agent, and
the agent talks to the core over MCP.

## The core contract: MCP

`server.py` turns Yuyao's core API into **MCP tools** — one tool per core route — using FastMCP's `from_fastapi`.
So the agent (and Claude Code, JetBrains Air, Cursor) all reach the same functions through one interface. The tools
are things like `decompile`, `recover_blocks`, `get_region`, `probe_truth`, `propose_claim`, `prove_equiv`,
`find_rare_triggers`, `simulate`, `plant_trigger`, `critical_path`.

Two pieces of middleware wrap every tool call:

- **`transcript.py`** — records every call as one JSON line (tool, args, result, timing, which cells it touched).
  This is the audit trail and the live stream the UI renders.
- **`heldout.py`** — refuses any call on a held-out test chip, unless the frozen bench has set `DECAP_HELDOUT_OK`.
  This is what stopped an agent wandering onto the test split when someone typed "continue testing".

## The claim loop (`witness.py`) — the core moment

When you point the agent at a part of a chip and ask "what does this do?", here is the loop:

1. **Look** — the agent reads the region: its inputs, outputs, and the decompiled Verilog lines
   (`get_region`), and runs random inputs through the simulator (`probe_truth`, up to 1024 vectors). Samples are
   *evidence, never a verdict*.
2. **Claim** — the agent writes one Verilog expression it believes describes the region, plus a short `label`
   (e.g. "counter"). It can only see the look tools; it can't call the checker itself.
3. **Check** — the controller (not the agent) sends the claim to `propose_claim`, which is Yosys. The verdict comes
   back: `proven`, `inductive`, `partial`, `restated`, `bounded`, `refuted` (with a counterexample), `tautology`,
   `unparseable`, `timeout`, or `tested` (fallback: random simulation only).
4. **Refine** — if the claim was refuted or unparseable, the agent gets the counterexample or the parse error and
   tries again, at most twice (bench) or four times (interactive).

Every attempt is a `claim` event in the transcript, and the whole run is cached, so the same region answers
instantly the second time.

**Credit rules** — a claim is only *credited* (shown as a win) if it is: on-topic; complete (Yosys found no
second value); covers **every** output bit of the region; and shorter than a third of the decompiled logic, or
carries a recognised shape. This is what stops the agent "cheating" by restating the code or explaining one
trivial pin.

## The claim grammar (`claim_expr.py`)

Claims are a small Verilog subset: expressions over ports and flip-flops, `next(x)` = x one clock later, `A when B`
= B implies A, concatenation `{...}`, bit/part selects `[hi:lo]`. The parser understands Verilog's unsigned width
rules (so `count + 1` wraps at 255 while `count + 8'd1` doesn't), expands the decompiler's helper wires (`t5 = …`),
and fixes the mux-precedence trap (`next(d) == c ? a : b` means `next(d) == (c ? a : b)`, which is what the writer
meant). The core's `claims.py` imports this parser, so both sides agree on what a claim means.

## Plain-English explanations (`plain.py`)

The formal claim is exact but unreadable ("Acc0 after the next clock equals the bits 0, bits 3..0 of ui_in
joined…"). `plain.py` runs a *separate* LLM call, **after** Yosys has decided, that turns the fact into a
headline + two or three sentences + one worked example. Two guards keep it honest:

- it only runs for verdicts with something true to say (never for refuted/unparseable);
- any number it writes above 32 must appear in the source material, else it retries, then gives up.

There are two modes:
- **claim reading** — explains a proven/checked claim;
- **code reading** — reads the decompiled lines themselves (which Yosys has already proven equal to the gates). This
  is what "Explain" falls back to when no short formula can be proven — and it's what lets the agent explain
  tangled "leftover logic" and fixture chips that have no core proof.

## The natural-language router (`terminal.py`)

You type a command in the Agent drawer; `terminal.py` decides which flow to run. It's plain keyword matching, and it
doesn't pretend to be more:

| Intent | Trigger | Flow |
|---|---|---|
| `hunt` | "hidden / trigger / trojan / suspicious" | `find_rare_triggers` → whole-chip `prove_equiv` against the author's source |
| `explain` | "what does / explain / …" | the claim loop, or a code read for fixtures |
| `drill` | "drill / red-team" | plant a secret trigger, hunt it blind |
| `fault` | "fault drill / switch one gate" | inject one wrong gate, then repair it |
| `investigate` | "audit / review / … and …?" | the planner (below) |
| `check_source` | (you uploaded a .v file) | Yosys checks your file against the chip |
| `repair` | (verify page handed over a faulty design) | find and fix the gate |
| `guide` | anything else | free-form Q&A |

**Honesty of the hunt:** "clean" means Yosys *proved* equivalence. "inconclusive" means only a bounded check
passed — a rare trigger could still hide. "unchecked" means there was no author source to compare. The terminal
never lets a bounded result be called clean.

## The planner (`investigate.py`) — "work mode"

For open-ended asks ("audit this chip"), the terminal hands off to a multi-step agent:

1. **plan** — it writes 2–5 numbered steps first (a `plan` event);
2. **act** — each step carries a one-line `why` (a `thought` event), and it runs one tool at a time so the reasoning
   reads in order;
3. **remember** — anything Yosys decided is written to chip memory (`runs/memory/<chip>.json`), so the next question
   starts from it — our version of DimOS's spatial memory;
4. **report** — a summary, findings tagged by evidence (`yosys` / `proof` / `code` / `structure` / `estimate`), and
   clickable next steps.

A guard re-checks the report: a finding the model labelled `yosys` but no proof actually backed is relabelled
`unverified`, and a hunt that came back "inconclusive" gets a correction if the report still says "no hidden logic".

## The repair loop (`repair.py`)

When a design differs from its chip (the verify page's "one gate switched"), `repair.py` finds and fixes it:

1. Yosys says the design differs, with an input that breaks it (`review_edit`, or the cell-model author check).
2. Rank the suspect lines — Alex's fault-localiser's suspects if given, else the agent's own ranking.
3. **Mutation search**: try single-gate changes, Yosys deciding each candidate (deterministic — the model is not in
   the verdict).
4. The first change Yosys proves is the fix; the agent explains it in plain words.

`source_check.py` is the simpler sibling: check whether a chip implements *your* uploaded Verilog.

## The supporting pieces

- **`guide.py`** — a Q&A agent whose only source of facts is the tools, pinned to the current chip (no cross-chip
  tools), so it can't wander.
- **`lifecycle.py`** — a session registry (`sessions.json`): start/finish/stop/get, backing `decap status`,
  `log -f`, `stop` and the `/status` command.
- **`cli_model.py`** — an opt-in backend that runs each request through the local `claude` CLI (isolated: no tools,
  no user settings, API key removed). Used for the bench; can't call tools, so not for the planner.
- **`cli.py`** — the `decap` command-line tool (`decap agent-send`, `explain`, `claim`, `demo`, `bench`, `mcp`,
  `status`…).
- **`formalise.py`** — turns a person's sentence ("LED 3 lights when switches 1 and 2 are on") into a formal line,
  for the "ask the agent to check this" flow.
- **`stub_core.py` / `stub_witness.py`** — a made-up counter chip and checker, so the whole layer runs and is
  tested without the real core.
- **`bench.py`** — the held-out benchmark (frozen, hash-locked, never retuned).
- **`ideas.py`** — Yuyao's "suggest edits" agent (#89), a sibling to explain.

## The HTTP door (`witness_api.py`)

Streaming endpoints the browser calls (proxied at `/witness`):

| Endpoint | What it does |
|---|---|
| `POST /witness/explain` | the claim loop, as SSE |
| `POST /witness/command` | one natural-language command, as SSE |
| `POST /witness/explain-text` | read selected lines (editor's Explain) |
| `POST /witness/explain-fix` | explain a gate fix the verify page found |
| `POST /witness/formalise` | sentence → formal line |
| `POST /witness/ideas` | suggest edits |
| `GET /witness/cache` / `status` | cached runs / session list |

## The browser side (`decap/web/src/agent/`)

- **`api.ts`** — the fetch/SSE client for the endpoints above.
- **`store.ts`** — per-chip state: runs, attempts, edits, comments. One event store the whole UI re-renders from.
- **`agent.ts`** — the controller: start a run from a Verilog line, a block, a 3D cell, a comment, or the repair
  hook; it maps lines to regions and applies plain explanations.
- **`terminal.ts`** — renders a command as a DimOS-style stream (sender column, `▶` tool calls, `↳` results, a
  spinner, a status line); follows new lines only while you're at the bottom.
- **`panel.ts`** — the Agent drawer (runs, the terminal, the command chips), with slash commands.
- **`editor.ts`** — the Decompile pane as a real CodeMirror editor (Check with Yosys / Explain / Ideas / Fix).
- **`verilogIO.ts`** — Download / upload Verilog buttons, and the fixture detection.
- **`stageAgent.ts`** — the 3D view's hover Explain/Suggest/Comment toolbar.
- **`md.ts` / `words.ts`** — a small safe Markdown renderer, and plain sentences for each verdict.

## The safety model, in three sentences

1. The model proposes; Yosys scores. No proposal is shown as true until the proof passes.
2. The agent's tools are a fixed, filtered set (`heldout.py` keeps the test split off-limits; the region agent sees
   only look tools, the guide is pinned to one chip).
3. Everything is recorded (`transcript.py`) and replayable, so a judge can audit both the code and the AI's
   reasoning.

## How a request actually flows (example: hover a line → Explain)

1. `stageAgent.ts` / `codeAgent.ts` maps the line to a region and calls `runAgent`.
2. `agent.ts` calls `api.explain(chip, scope)` → `POST /witness/explain`.
3. `witness_api.py` opens a transcript, builds the MCP server, aims the scope (`scoping.py`), streams a code reading
   (`plain.py`) immediately, then runs the claim loop (`witness.py`).
4. Every tool call and claim is recorded to a `runs/*.jsonl` file and pushed to the browser over SSE.
5. The browser's `terminal.ts`/`panel.ts` renders the tool calls, the verdict, and the plain-English result; the
   formal claim folds underneath as "how it was checked".
