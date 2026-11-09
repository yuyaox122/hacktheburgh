# bASIC / Witness — pitch strategy (Sun 8 Nov, 02:15)

Decisions recorded for the morning. Naming: the agent is **Witness** ("AI that can't bluff"); the DimOS-style
console is the **Witness terminal**.

## Tracks, in order of how hard we compete

| Track | Angle | Status |
|---|---|---|
| **JetBrains** (strongest) | "Bring the agent you already use." Register `decap mcp` in Air (`.mcp.json` committed); Air's built-in agent drives our deterministic Yosys tools. The terminal is a dev tool a judge can hold. | Build: done (MCP + terminal). Demo: register in Air, ask "is anything hidden?". |
| **MetaView** (best project) | **Witness: AI that can't bluff.** The whole loop: agent proposes, Yosys proves or refutes with a counterexample. | Build: done. |
| **Entire** (strong, balances JetBrains) | Agent↔human forge: every claim is logged with its proof (the transcript + replay), so a commit links to the *provable* agent work that made it — not just a prompt. | Build: transcript/replay done. Demo: `entire enable` + one commit shows prompt↔proof. |
| **G-Research** (make the invisible visible) | A chip is readable to its designer and **unreadable to anyone adopting or building on it**. We make the design *intent* visible again, with a proof — not just a decompile, a provably-correct read-back. | Build: done (decompile + proof + Witness). |
| **HRT** (proof beats data — honest caveat) | We make a *prediction* (the agent's claim) and a *decision* (prove/refute) with a measured error rate, and show a formal proof beats any amount of sampling (the Monte Carlo ablation: random testing passed wrong claims the proof caught). | Build: ablation done. This is a second-place angle — it argues *against* "more data", so lead with it only if the judge is receptive. |
| **ElevenLabs** (MLH) | Voice-command the terminal: STT → natural-language command. Simple once the terminal has a text prompt. | Build: terminal has NL input; STT is a thin add. |

## Witness terminal — MVP scope (do NOT over-build)

The terminal is a **solid prototype, not a product**:
- One prompt line → natural-language command.
- Streamed tool calls (`▶ name`) and a final **proven claim / counterexample / hunt verdict**.
- Intents: **hunt** ("is anything hidden?"), **explain/optimize** ("what does block b0 do?"), **guide** (anything else).

Already built and merged: `decap agent-send` (CLI), `POST /witness/command` (SSE), the command bar in the Agent
drawer, and the hover Explain/Suggest edit/Comment on both the Decompile page and the Explore 3D view.

Out of scope for tonight (nice-to-haves if time): Gemini as the LLM, Tiger Data/Postgres for the transcript,
ElevenLabs STT, the full DimOS `humancli` chrome.

## 02:30 freeze — status

The held-out bench is armed: `decap/agent/scripts/overnight_bench.sh` waits for the `bench-freeze` tag, checks the
freeze hash, runs the 15 held-out chips at concurrency 1 with `cli:sonnet`, restarts from cache on crash, and writes
`decap/agent/out/witness-summary.json` (model, n, first_draft_proven, proven_after_refinement, coverage_pct) for the
Results-page card (#87).
