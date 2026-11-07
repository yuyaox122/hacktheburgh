# Decap: View Source for silicon

> **Status (Sat 7 Nov 2026, evening, v3):** Rebuilt around the council's combined pick: a **proven silicon decompiler, scored on held-out Tiny Tapeout chips, with the judge's hidden number as the finale** (§1–3, §5, §6.7–6.8, §9–13). Council results are in §13. It has not been rescored yet. The previous find-and-fix plan is now Plan B (§5.1). Yuyao's proposal for HackTheBurgh 2026. The **team direction is NOT decided yet**: Alex and Ian lean towards Fault Lines (see `ideas/README.md`). Confirm direction on the board (issue #1) before assigning work. Original idea: issue #4. Council variant: `ideas/08-glassdie.md`.
>
> **Reading order for a new session:** §1–3 (what and why), §8 (verified facts), §6 (architecture), §10 (build order), then the rest as needed. A clickable prototype lives at `docs/decap/prototype.html`, also published at https://claude.ai/artifact/E6mMDJXF6mY6uG5arCRHNb (private to Yuyao).

---

## 1. The pitch

**To a random person (one sentence):**
> Decap turns a computer chip back into readable code and proves the code is exactly right, so anyone can see what the chip really does, including anything hidden inside.

**Tagline:** *View source for silicon, with proof.*
**Closing line:** *"You can't review what you can't read. Now you can read silicon."*

**One-liner (technical audience):** Decap decompiles real chips it has never seen (gate-level netlists with every name stripped) back into readable Verilog, formally proves each result is the same circuit, and scores itself against the authors' real source code. Because the output is code, hidden logic shows up as a line you can review.

**The shape of the pitch:** one problem (silicon can't be read, so it can't be reviewed), measured at scale (proven on N real chips it never saw), with one moment the judge controls (their secret number surfaces as a line of recovered code). The 90-second script is in §9.

## 2. Story, pain point and purpose

### 2.1 The problem
**Silicon has no "View Source".** Software has decompilers: turn a compiled program back into readable code, and you can see what it really does. Chips have none. Code goes into synthesis and place-and-route, twenty lines become hundreds of gates with meaningless names, and what comes out is the only thing that's actually true, yet nobody can read it. You can't review what you can't read.

### 2.2 Why it matters now: two pressures
- **People: chips are built from parts you didn't make.** These include third-party IP blocks, outsourced layout and someone else's factory. A few extra gates (a *hardware trojan*, e.g. a comparator waiting for a secret value) appear in no source file. Code review can't catch what isn't in the code.
- **AI: AI is starting to write chip code.** A change nobody can read back is a change nobody can check.
- **Hardware is permanent.** Software bugs get patched; a shipped chip can't be. Intel's 1994 Pentium division bug cost it about $475M (verify before stage, §2.8).

### 2.3 Why it's possible now: the unlock
- **Every gate has a known meaning:** each SKY130 standard cell's function is published (Liberty).
- **Open tools can prove two circuits identical:** Yosys equivalence checking.
- **Real chips come with answer keys:** every Tiny Tapeout 08 chip ships its gate-level netlist and a `commit_id.json` pointing at the author's source at the exact commit (verified, §8). So we can measure how well Decap reads chips it has never seen.

### 2.4 The argument chain (pitch order; each step earns the next)
| # | Argument | Job it does | Line |
|---|---|---|---|
| 1 | You can only trust code you can read | Something every judge already agrees with | "You'd never merge a pull request you can't read." |
| 2 | Silicon can't be read | The pain | "Every chip is one. There's no View Source for silicon." |
| 3 | Two pressures | Stakes, why now | "Chips are built from parts you didn't make, and AI is starting to write them. And you can't patch a chip." |
| 4 | Decap reads it back, with proof | The answer | "Decap turns gates back into code, and proves the code is exactly right." |
| 5 | Measured on real chips it never saw | Credibility (G-Research's criterion) | "Proven on N of M real Tiny Tapeout chips. Here's each author's own code beside ours." |
| 6 | The judge's number | The moment | "You picked a number. Here it is, as a line of code nobody wrote." |
| 7 | Close | The line they repeat | "You can't review what you can't read. Now you can read silicon." |

### 2.5 Purpose statement
Make silicon readable, and therefore reviewable: turn any open chip back into code, prove it's right, and let people see what doesn't belong.

### 2.6 What the pitch leaves out, and why
- **The agentic AI editor as the headline.** The council scored it about 54 (§13): "Cursor for X" is crowded, and an 8-bit "make it faster" shows nothing. The AI has a real job inside Decap (naming signals, writing leftover logic, proposing the fix); it isn't the headline.
- **3D as the argument.** It's the stage for the warm-up chip, not the reason. 3D chip viewers already exist (§2.7).
- **"Microscope, tour guide, scalpel."** Three heroes read as a feature list.
- **The Jane Street ASIC puzzle.** Not the motivation (Yuyao's call). The warm-up files are data only (§7), and the hero number is the *judge's*.

**Honesty rules (say these if asked; never contradict them):**
- We plant the trigger ourselves and say so. The judge picks the number; we don't know it in advance.
- Decap reads design files (netlists, layouts), not manufactured chips.
- Small open chips (20–300 logic cells) today. We report the proven rate exactly as measured, including failures.
- "Proven" means a Yosys equivalence proof passed. Anything checked only by simulation is labelled "tested, not proven".
- Confirmed trojans in real chips are rare, and some famous reports are disputed. Say "can't be checked", never "is happening everywhere".
- Turning gates into code with AI has been published before (§2.7). Our claim is proof on every result, scored on real held-out chips, live, not that the idea is new.

### 2.7 Prior art and our edge
| Tool or work | What it does | What Decap adds |
|---|---|---|
| Tiny Tapeout GDS viewer, Tiny Tapeout Explorer | 3D shapes of real open chips | Meaning: readable code recovered blind, with proof |
| DigitalJS (BSD-2) | Browser simulation of Yosys netlists as schematics | Readable code, not a schematic; a proof, not a simulation |
| HAL (gate-level netlist reverse-engineering framework, academic) | Netlist analysis for experts | A proven decompile anyone can read, live, in a browser *(found by a council judge; not yet checked)* |
| Gate-Breaker (2025), GenEDA (arXiv 2504.09485) | Research on LLMs turning netlists back into RTL | Every result formally proven and scored against real authors' code on held-out chips *(found by a council judge; read them before Q&A)* |
| EXERT and other SAT-based trigger finders; ML detectors on Trust-Hub | Find trojan triggers in netlists | The trigger appears as readable code, measured against clean controls |
| Commercial equivalence tools (Cadence Conformal, Synopsys Formality) | Prove two netlists equal, for specialists | The same kind of proof, used to certify a decompile |

**Our edge:** blind decompile + formal proof + an answer key (real authors' code) + live. Nobody else shows all four at once.

### 2.8 Facts to verify before saying them on stage
| Claim | Status |
|---|---|
| Pentium FDIV cost Intel ~$475M | Widely reported; confirm the source |
| "AI is starting to write chip code" | Find one or two solid, citable examples |
| Gate-Breaker, GenEDA, HAL, EXERT: what each actually does | Not checked; a council judge found them by search |
| Tiny Tapeout Explorer and the GDS viewer are live | Not checked |

## 3. Users
| Persona | Their question | What Decap gives them |
|---|---|---|
| **Team receiving silicon they didn't build** (primary: chip teams using third-party parts, security reviewers) | "What does this chip really do? Does it do only what our source says?" | A proven decompile, suspicious-logic flags, the trigger as a line of code, cone of influence |
| **Designer letting AI touch a chip** (secondary) | "What exactly changed?" | Decompile before and after, proof, a diff in code, not in gates |
| **Student / Tiny Tapeout maker** (one line in the pitch) | "What did my code become? How did others build the same thing?" | Their own chip read back, side by side with their code; click a gate to see what it does; **chips like mine** (feature 16–19) |
| **Hackathon judges** (demo audience) | Software people who know decompilers and code review, not chips | "View Source", "decompiler" and "pull request" make it instantly legible |

## 4. Chip primer (for teammates and new sessions)

- **Transistor** → **logic gate** (AND/OR/XOR/NOT, a few transistors) → **flip-flop** (stores 1 bit, updates on each clock tick) → **blocks** (adder, counter, register, comparator) → chip.
- **Standard cells:** the factory's pre-made Lego bricks. SKY130 HD (`sky130_fd_sc_hd__*`) has a few hundred, each with a known function (in the **Liberty** `.lib` file) and a known shape. Cell height is 2.72 µm; widths are multiples of a 0.46 µm "site".
- **Flow:** Verilog → **synthesis** (Yosys) → **netlist** (cells plus wires) → **place & route** (OpenROAD/OpenLane/LibreLane) → **DEF** (cell positions and routing, with names) and **GDS** (final polygons on every layer, usually with internal names removed) → factory.
- **Three views of one design:** code (intent), netlist (circuit), layout (physical). Decap's job is moving between them, with AI help.
- **Decompiling** (software term): turning compiled output back into readable source. For chips, it means netlist → Verilog.
- **Equivalence checking:** a formal tool (here Yosys) proves two circuits give the same outputs for *every* input and state, not just the ones you tested. "Proven" in this doc always means this.
- **Hardware trojan:** extra logic hidden in a chip, usually a *trigger* (a rare condition, like one secret value) plus a *payload* (what it does when triggered).
- **What's NOT feasible:** reverse-engineering a commercial chip from silicon (needs delayering and electron microscopes), and manufacturing anything during a hackathon.

## 5. Scope

### Sponsor targets
| Track | Fit | How |
|---|---|---|
| **G-Research**: "make the invisible visible" (Switch 2) | Primary | Hidden structure recovered blind from real chips, **proven on held-out chips** against the authors' real source; the planted trigger made visible as code |
| **MetaView**: best overall ($500 per person) | Strong | Relatable "View Source" problem; the judge's own number appears as a line of code |
| **JetBrains**: "Help the Developer" (€350 per person 1st) | Only if we ship a plugin (P3) | The council scored the editor framing low (§13). Enter only if JetBrains confirms a JCEF web view counts and P0–P2 are solid. |
| HRT, Wordsmith, Viridien, Bank of Scotland, Instalily | No | Don't bolt them on. |

**Confirm with the organisers:** can one project enter several sponsor tracks?

### Core features (outline)
The demo is **Stage → Read → Find**, with **Fix** as an optional finale.

**Stage: see it and run it** (warm-up chip)
1. **Real chip in 3D:** the warm-up DEF + netlist, SKY130 layer colours, cells and routing.
2. **Click any gate:** what it does in plain English, live pin values.
3. **Live simulation:** gate-level, in the browser, glowing wires, verified against iverilog. The same evaluator powers the decompiler's probes.

**Read: decompile, prove, compare** (the hero; G-Research)

4. **Strip names:** all instance and net names removed; only cell types and wires remain.
5. **Recover blocks:** registers, shift registers, counters, adders, comparators, muxes, found from structure plus functional probes.
6. **Write the code, structure first:** recovered blocks become Verilog through fixed templates. The LLM only names signals and writes the leftover glue logic.
7. **Prove it:** Yosys proves the decompiled Verilog equals the netlist, cone by cone, with flip-flops kept 1:1. On failure, the counterexample goes back to the LLM (max 2 retries). The verdict is shown as **"proven"**, **"tested, not proven"** or **"failed"**.
8. **Compare with the answer key:** each author's real source (from `commit_id.json`) is shown side by side, with line counts.
9. **Proof wall:** every benchmark chip as a tile: green when proven, amber when tested only, red when failed. Score X/N on held-out chips. This borrows GlassDie's data-scale idea at a fraction of the cost.

**Find: what shouldn't be there**

10. **Plant:** the judge picks a number. A trigger (an `== N` comparator OR'd into an output) is spliced into a benchmark chip, and names are stripped.
11. **Flag suspicious logic, blind:** "rare-trigger" cones whose output fires for ≤1 in 2^k inputs, the classic trojan signature, are flagged. The decompiled code shows the line: `if (a == 8'd<N>) …`
12. **Measure it:** detection recall on planted chips and false positives on clean controls, across the benchmark. Legitimate comparators (e.g. the warm-up's `== 496`) will also be flagged, and we show that honestly. With the source available, the unexplained-logic check (decompile vs author source) separates them exactly.
13. **Cone of influence:** everything the trigger can reach lights up.

**Fix: change only what should** (optional finale)

14. **Remove and prove:** delete the flagged cone, then prove the chip is equivalent to the author's source. Deterministic first; an AI-proposed patch only if time allows.

**Explain (supporting)**

15. **AI guide:** chat that explains and highlights blocks and never invents ids or results.

**Compare: "chips like mine"** (P2; for students and designers)

16. **Import your design:** upload a small Verilog file (synthesised with Yosys, path C) or a TT-format gate-level netlist (≤ ~500 logic cells).
17. **Fingerprint:** build a structural fingerprint from what Decap recovered: counts of each block type (registers, counters, adders, comparators, muxes, shift registers), flip-flop count, logic-cell count, and the logic-cell type mix (fill/tap/decap dropped).
18. **Nearest neighbours:** show the 3–5 most similar TT08 chips (cosine similarity on the fingerprint) with *why* they match, e.g. "both have one 8-bit counter and a 7-segment decoder".
19. **How others built it:** side by side, your block vs theirs: cells, area (Liberty), longest path (estimate, labelled), and the decompiled code. "Theirs does the same job in 40% fewer cells" is the hook for learning and optimising.

This reuses the proof wall's data (every benchmark chip is already decompiled), so the extra work is the fingerprint, the ranking and one view. It overlaps with GlassDie's structural galaxy; a 2D or 3D map of the fingerprints is the P3 version.

### Benchmark protocol (what makes it "held-out")
- **Candidates (verified, §8):** 20 Verilog/SystemVerilog TT08 chips with 20–300 logic cells, plus 6 Wokwi (schematic-drawn) chips and the warm-up.
- **Dev set:** the warm-up plus about 5 TT08 chips. Templates and prompts are tuned only on these.
- **Test set:** every other candidate. **Frozen before any tuning.** Nobody opens its source until the run is scored. Report the proven rate, the tested-only rate and the failure rate, all three.
- **Trojan test:** for each test chip, one planted copy (random N) and one clean copy. Report recall and false positives.
- **Wokwi chips:** their "source" is a diagram, not Verilog. Score their proofs, but reveal the diagram link instead of code.

### Priority ladder (cut from the bottom; P0 and P1 are never cut)
| P | Feature | Acceptance criteria |
|---|---|---|
| **P0 Stage** | Warm-up in 3D + inspector (features 1–2) | All 79 logic cells clickable at correct positions; <100 ms pick latency |
| **P0 Stage** | Live gate-level simulation (feature 3) | Matches `iverilog` with SKY130 functional models on ≥1,000 random cycles |
| **P0 Read** | **Proof pipeline:** Yosys reads a TT08 netlist with SKY130 cell models, strips names, and proves a hand-written Verilog equal to it | **Hour-6 gate:** ≥3 TT08 chips proven equivalent end to end (decompiled Verilog vs netlist). If it fails, switch to Plan B (§5.1). |
| **P1 Read** | Block recovery + template decompiler + LLM glue + retry loop (features 4–7) | Runs on the frozen test set; reports proven / tested / failed per chip |
| **P1 Read** | Side-by-side author source + proof wall (features 8–9) | Every test chip shown with its verdict; X/N headline number |
| **P1 Find** | Plant + blind rare-trigger flag + cone (features 10, 11, 13) | The judge's N recovered as a line of decompiled code, on the warm-up and at least one TT08 chip |
| **P2 Find** | Trojan benchmark (feature 12) | Recall on planted copies and false positives on clean copies across the test set |
| **P2 Fix** | Remove and prove (feature 14) | The flagged cone is removed, and the result is proven equivalent to the author's source |
| **P2 Explain** | AI guide (feature 15) | Answers "what does this block do?" with highlights in under 10 s |
| **P2 Compare** | Import + fingerprint + nearest neighbours + side-by-side (features 16–19) | Importing a TT08 chip held out from the index returns at least one chip of the same kind (e.g. another ALU, counter or decoder) in its top 3 on ≥70% of trials. Importing our own counter Verilog shows the similar counter chips. |
| **P3** | 3D for TT08 chips from GDS cell positions (KLayout, verified); a GlassDie-style galaxy/map of the fingerprints; JetBrains plugin; Trust-Hub scoring; line-level code ↔ cells | |

**Cut order if behind:** P3 → Compare → AI guide → Fix → trojan benchmark → LLM glue (templates only, with an honest lower proven rate) → shrink the test set (never below 10 chips).

### 5.1 Plan B (if the hour-6 gate fails)
Fall back to the previous find-and-fix plan on the warm-up chip alone: a planted trigger found blind, read off the gates, removed with an equivalence proof (or exhaustive simulation, labelled), using pre-planted variants. The council scored that version about 75. It is weaker on held-out data, but it is safe.

**Cut list:** uploading arbitrary large chips or unknown formats (Compare accepts small Verilog / TT netlists only), OpenLane/Docker DEF regeneration, rebuilding layouts after a change, Minecraft export, die photos, transistor-level views, HRT/Wordsmith halves, a per-cell classifier, copy detection, a free-form (non-template) LLM decompiler.

## 6. Architecture

### 6.1 Shape
```
                 offline ingest (Python)                         web app (Vite + TypeScript + three.js)
 DEF + netlist ──► ingest_def.py ─┐                          ┌─► 3D view · inspector · source pane · sim · diff
 GDS (P3)      ──► ingest_gds.py ─┼─► design.json (+ lib.json) ┤
 Liberty .lib  ──► liberty.py ────┘                          └─► JS simulator (levelised, Liberty-driven)
                                                                       ▲
 small API (FastAPI, Python) ─── /chat (LLM + tools, key server-side) ──┘
                              ├── /decompile (recovery → templates → LLM glue → Yosys proof → verdict)  ◄── hero
                              ├── /plant (splice a trigger for N → stripped netlist + insertion record)
                              └── /surgery (Yosys synth → equiv/spec check → returns new cells + verdict)

 benchmark (offline, Python): TT08 netlists + commit_id.json sources ─► frozen dev/test split ─► /decompile
                              on every test chip ─► bench.json (verdict, lines, time, trojan recall/FP) ─► proof wall
```
- Keep the **web app static-first**: it loads `design.json` and runs the simulation, cone, goal-seek (brute force) and reverse-mode recovery in the browser. The API is only for the LLM (so the key stays server-side) and for Yosys.
- Reuse the prototype's code (`docs/decap/prototype.html`): the scene build, instanced meshes, routing, picking, fly-to, the incremental placer, the simulator loop, the recovery heuristics, the LCS code diff and the guide tools. Port it to TypeScript modules.

### 6.2 Data paths
- **Path A (P0): DEF + netlist** (Jane Street warm-up; anything run through OpenLane/LibreLane). DEF gives component names, cell types, positions, orientation and routed wires. The netlist gives connectivity. Parse the DEF in Python (small hand parser, or `lefdef` tools if they install). Cell outlines come from the SKY130 HD LEF (or width = sites × 0.46 µm, height 2.72 µm).
- **Path B (P3): GDS only** (Tiny Tapeout shuttle repos have GDS + netlist + LEF but **no DEF**). Read cell instances (type + position) with `klayout.db` (verified, §8). Connectivity is either geometric extraction (`LayoutToNetlist`, SKY130 layers: li1 67/20, mcon 67/44, met1 68/20, via 68/44, met2 69/20, via2 69/44, met3 70/20, …) or matching positions to the provided netlist.
- **Path D (P0, the benchmark): TT08 netlist + author source.** Netlist from `projects/<id>/<id>.v`; source from `commit_id.json` (repo + commit) → `src/*.v` at that commit. No layout needed: the proof wall and the code view don't need positions. 3D for these chips is P3 (cells from GDS via path B).
- **Path C (own designs and surgery):** our Verilog → Yosys (`synth -top X; dfflibmap -liberty L; abc -liberty L; opt_clean; write_json`) keeps per-cell `src` attributes for line-level mapping. Placement comes from the prototype's incremental placer (approximate, always labelled), seeded from the original layout so diffs stay local.

### 6.3 `design.json` (sketch)
```json
{ "name": "adder_demo", "pdk": "sky130_fd_sc_hd", "die": {"w": 100, "h": 100}, "rowHeight": 2.72, "site": 0.46,
  "ports": [{"name": "A", "dir": "in", "x": 0, "y": 40}],
  "cells": [{"id": "add0/_32_", "type": "nand2_2", "x": 12.4, "y": 30.0, "w": 2.3, "orient": "N",
             "pins": {"A": "b_reg[0]", "B": "a_reg[0]", "Y": "add0/_00_"}, "module": "adder8", "src": null}],
  "phys": [{"type": "decap_3", "x": 0, "y": 0, "w": 1.38}],
  "routes": [{"net": "add0/_00_", "layer": "met1", "pts": [[1, 2], [5, 2]]}],
  "source": {"files": {"00_source.v": "..."}, "modules": {"adder8": {"file": "00_source.v", "line": 17}}} }
```
`lib.json` (generated from Liberty, only the cell types actually used): `{ "nand2_2": {"pins": {"A": "in", "B": "in", "Y": "out"}, "function": {"Y": "!(A&B)"}, "area": 6.256, "ff": null } }`. Sequential cells carry `ff: {"clocked_on": "CLK", "next_state": "D", "clear": "!RESET_B"}`.

### 6.4 Simulator
- Levelise the combinational cells (topological order). Evaluate them with functions compiled from Liberty strings (`!`, `&`, `|`, `^`, parentheses). On a clock edge, latch every flip-flop's D at once; async clear overrides.
- **Golden check (P0 gate):** `iverilog` the same netlist with SKY130 cell functional models (`sky130_fd_sc_hd__*.functional.v`, or `primitives.v` + the cells) under a random testbench, dump outputs, and compare with the JS sim run headless (Node) on the same stimulus.

### 6.5 AI guide
- Server-side LLM with tool use. Every call sends standing rules + a compact design context: cells (id, type, function, pins), modules, recovered blocks, simulation state, selection. Tools run in the page (the API relays tool calls to the browser, or the page talks to the LLM directly in the artifact build).
- **Model choice:** keep it provider-agnostic.
  - Claude: `claude-sonnet-5-5` for the guide, `claude-haiku-5-5` for quick replies.
  - The free JetBrains OpenAI key also works.
  - Never commit keys.
- **Rules:** ≤110 words, plain language, highlight what you mention, never invent ids, numbers or verification results, and report tool results verbatim.
- **Reverse mode:** send only anonymised names and the recovered blocks, so the explanation is genuinely blind.

### 6.6 Chip surgery pipeline (P2 Fix; also Plan B)
1. The LLM proposes a Verilog patch (shown as a code diff). For the hero demo, the patch removes the planted trigger, and the target is the **trusted source**: after the fix, the silicon must be equivalent to the code you wrote.
2. Yosys synthesises that module to SKY130 HD (same script as path C).
3. **Verify.**
   - **Trojan removal (hero):** an equivalence proof of the fixed design against the trusted source. Before the fix, the same check fails, and its counterexample is the trigger (feature 9).
   - **Refactor:** an equivalence proof against the old module, using Yosys `miter -equiv -flatten -make_assert gold gate miter; sat -verify -prove-asserts -show-inputs miter`. For sequential logic, use `equiv_make` / `equiv_simple` / `equiv_induct`. Exhaustive simulation is a valid proof when the input + state space is ≤2^20.
   - **Behaviour change:** a spec check. The LLM writes the property, or we use a reference model.
   - **On failure:** show it, feed the counterexample back to the LLM, and retry at most twice.
4. Swap the module's cells in `design.json`, place new cells with the incremental placer, and compute the diff (by type + connectivity) and the deltas (cells, area, longest path from Liberty delays or a simple per-cell estimate, always labelled "estimate").
5. If Yosys is unavailable at demo time, fall back to pre-computed surgery results, labelled as such.

### 6.7 Decompiler and proof pipeline (the hero, P0/P1)
```
TT08 netlist (.v) ──► Yosys read + SKY130 functional models ──► strip names (keep cell types + wires)
                                                                   │
                       ┌───────────────────────────────────────────┘
                       ▼
   block recovery (structure + functional probes via the JS/iverilog evaluator)
     registers · shift registers · counters · adders · comparators · muxes
                       │
                       ▼
   templates → Verilog for every recovered block; flip-flops named after their netlist instances (1:1)
   LLM: names signals, writes the leftover glue per cone (it sees only stripped names and probe results)
                       │
                       ▼
   proof: per-cone equivalence, decompiled vs netlist (Yosys equiv_make / equiv_simple / equiv_induct /
          equiv_status -assert; combinational cones via miter + sat)
     ├─ proven → green
     ├─ counterexample → back to the LLM (max 2 retries)
     └─ no proof → random simulation ≥1,000 cycles → amber "tested, not proven", else red
                       │
                       ▼
   score vs answer key: author source from commit_id.json (repo + commit) → side by side, line counts
```
- **Why structure first:** both council judges flagged free-form LLM decompiling of flattened netlists as the main risk. Templates make the common blocks deterministic and provable. The LLM handles only what's left.
- **The proof direction:** decompiled Verilog is synthesised with the same Yosys script as path C, then checked against the original netlist. Nothing is "proven" unless Yosys says so.

### 6.8 Plant and flag (Find, P1/P2)
- **Plant:** synthesise `if (a == N) out |= 1` (an 8-bit comparator on an internal register, OR'd into one output) to SKY130 cells, splice it into the netlist, and record the inserted cells. For the warm-up, place it into free sites with the incremental placer (labelled "approximate").
- **Blind flag (rare-trigger):** for each single-output cone feeding an output or a flop, estimate how often it is 1. Exhaustive when the cone has ≤20 inputs, else sampled, with SAT for the exact satisfying value. Cones that fire for ≤1 in 2^k inputs are flagged, with the satisfying value shown, e.g. `a == 8'd137`.
- **With source (exact):** unexplained logic = cells in the decompile that no author-source construct accounts for (from the equivalence check of the decompile vs the author's source).
- **Metrics:** recall on planted copies, false positives on clean copies, on the frozen test set.

## 7. Data sources

| Source | What | Licence / use |
|---|---|---|
| **Jane Street `asic-puzzle-2026`, `warmup/`** (github.com/janestreet/asic-puzzle-2026) | `00_source.v` (shift registers + adder + comparator: S = 1 when A + B == 496), `01_netlist.v`, `02_netlist_with_power_rails.v`, `03_post_place_and_route.def`, `04_final.gds` | **No licence file.** Don't commit their files. Fetch them at setup with a script (`gh api repos/janestreet/asic-puzzle-2026/contents/warmup/<file> -H 'Accept: application/vnd.github.raw'`). Credit them on screen. Email asic-puzzle@janestreet.com before any *public* demo. **Never feed `puzzle.gds` to an AI tool.** Jane Street asked people not to, and we don't use the puzzle at all. |
| **Tiny Tapeout shuttles** (`TinyTapeout/tinytapeout-08`, `-06`) | Per project: gate-level netlist, GDS, LEF, `info.yaml`, docs, stats, and `commit_id.json` (the author's source repo + commit = **our answer key**). 135 project folders in the API listing. **No DEF.** See §8 for the size count. | Projects are mostly Apache-2.0; check per project |
| **SKY130 PDK** (`sky130_fd_sc_hd`): Liberty `.lib` (e.g. `tt_025C_1v80`), LEF, functional Verilog models | Cell functions, areas, delays, outlines | Apache-2.0. Fetch via `volare` or the `google/skywater-pdk-libs-sky130_fd_sc_hd` repo (verify exact paths at setup). |
| **Our own designs** | The prototype's 8-bit counter (base / lookahead / count-down), plus anything we synthesise | Ours. Use them as the fallback demo if Jane Street data can't be used. |

## 8. Verified facts (checked today, 7 Nov 2026)
| Fact | How verified |
|---|---|
| `pip install klayout` works on Yuyao's Mac (arm64), version 0.30.13, and includes `LayoutToNetlist` | Installed in a venv and imported |
| The warm-up GDS keeps standard-cell **types and positions** as named cell references (1,099 top-level instances: cells + vias), plus pin labels `A B S clk en rst_n`; die 100 × 100 µm | Read with `klayout.db` |
| Warm-up logic: **79 logic cells, 16 types** (16 mux2_1, 16 dfrtp_2, 8 nor2_2, 7 and2_2, 5 xor2_2, 5 or2_2, 5 a31o_2, 4 nand2_2, 3 xnor2_2, 3 clkbuf_16, 2 and4bb_2, 1 each of o21bai_2, and3_2, a21o_2, a21boi_2, a21bo_2) | Counted from `01_netlist.v` |
| The warm-up netlist has **hierarchical instance names** (`add0/_32_`), so module-level code mapping is free. It has **no `src` attributes**, so line-level mapping needs our own Yosys run. | grep |
| The warm-up DEF: 230 components, 8 pins, 84 nets, 2 special nets | DEF header |
| Homebrew has `yosys` 0.70 and `icarus-verilog` 13.0 with arm64 bottles. **Neither is installed yet.** Node and npm are installed. | `brew info`, `which` |
| The prototype logic is correct: base counts up; lookahead equivalent on 512/512; count-down correct; placement legal | Node test of the prototype's pure functions |
| **TT08 sizes (logic cells, excluding fill/tap/decap/diode/conb):** 121 netlists fetched (14 failed, mostly analog/ROM projects); median 819; **20 Verilog/SystemVerilog chips with 20–300 logic cells** (e.g. obstacle detection 27, 4-bit CLA 59, signed add/sub 65, 4-bit ALU 91, 7-seg decode 94, clock divider 106, MAC 118, alarm clock 119, I2C peripheral 190, I2S→PWM 231, 4-bit ALU 260), plus **6 Wokwi** chips (28–168). Some may be near-duplicates (`dpmu`/`dpmunit`, two micro-tile containers). Counts include buffers. | Downloaded all netlists with `gh api`; regex count; `info.yaml` for language and title |
| A TT08 `commit_id.json` points to the author's repo + commit, and the source exists there (`tt_um_Electom_cla_4bits` → `Electom/tt08_CSA_4bits@213ea90`, `src/project.v`) | `gh api` (checked on 1 chip; check all candidates at setup) |
| **Not verified:** KLayout geometric connectivity extraction on SKY130; the exact Liberty/LEF paths; Yosys `equiv`/`sat` behaviour on these cells; whether JetBrains accepts a JCEF-wrapped web app | |

## 9. Demo script (90 s) and judging moments

**Before the timer:** ask a judge for a number from 0 to 255, and let them pick a test chip from the proof wall. Planting runs in the background (<60 s) during the hook. If it fails, use a pre-planted copy and say so.

> **(0–15 s) Hook.** "You'd never merge a pull request you can't read. Every chip is one. Software has decompilers; silicon has no View Source. That matters, because chips are built from parts you didn't make, AI is starting to write them, and you can't patch a chip."
>
> **(15–30 s) Stage.** *(the warm-up chip in 3D, credited to Jane Street; click a gate, run it, wires glow)* "This is real silicon. Every gate has a meaning, but nobody can read hundreds of them."
>
> **(30–55 s) Read.** *(the proof wall: real Tiny Tapeout chips Decap has never seen)* "Decap turned every one of these back into code. Green means a formal checker *proved* the code is exactly the same circuit." *(the judge's chip: names stripped, blocks recovered, Verilog appears, **"Equivalence: proven"**)* "And here's what the author actually wrote." *(side by side)*
>
> **(55–80 s) Find.** "You picked a number, and we hid a trigger for it in this chip." *(re-run: one flagged block; the decompiled code shows `if (a == 8'd<N>)`)* "There it is: your number, as a line of code nobody wrote. And here's everything it can reach." *(cone)* "Across all test chips, Decap catches X of Y planted triggers, with Z false alarms on clean chips."
>
> **(80–90 s) Close.** *(optional: remove it, then "proven equal to the author's code")* "You can't review what you can't read. **Now you can read silicon.**"

**The moments that win:**
- The X/N proven number on chips we never saw (G-Research's criterion).
- The judge's own number appearing as code. It can't be rehearsed or faked.

### Likely questions
| They'll ask | We answer |
|---|---|
| "Isn't AI decompiling unreliable?" | "That's why every result is proven, not trusted. The AI only names things and fills gaps; a formal checker decides. Failures are shown in red." |
| "Has this been done?" | "LLM netlist-to-code has been published. We add a proof on every result, scored live against real authors' code on chips we never saw." |
| "You planted the trigger yourselves." | "Yes, and you chose the number. We also report false alarms on clean chips." |
| "Real chips have millions of gates." | "Today, small open chips, and we say so. The proof method is what industry uses at scale." |
| "Can it check a manufactured chip?" | "No. It reads the design files, the last point before the factory where you can still check." |
| "Can I try it on my own design?" | "Yes: import it and Decap shows the most similar real chips and how they built the same blocks." (Compare, P2) |
| "Did you tune on the test chips?" | "No. The test set was frozen before any tuning, and its sources were opened only for scoring." |

**Backups:** a recorded video, pre-planted copies, cached proof-wall results, and offline guide answers.

## 10. Build order (for a one-shot session)
1. **Setup (30 min):**
   - `brew install yosys icarus-verilog`; a Python venv with `klayout fastapi uvicorn`.
   - Fetch the warm-up files and the TT08 candidate netlists, plus their `commit_id.json` sources, into a gitignored `data/`.
   - Fetch SKY130 HD Liberty, LEF and functional models.
   - Scaffold Vite + TypeScript + three.js.
   - **Freeze the dev/test split and commit the list (not the data).**
2. **Proof pipeline first (the hour-6 gate):** Yosys reads a TT08 netlist with the SKY130 models, strips names, and proves equivalence against hand-written Verilog for 3 small dev chips. Go/no-go at hour 6.
3. **In parallel, Stage:** ingest the warm-up (`liberty.py` → `lib.json`; `ingest_def.py` → `design.json`); 3D + inspector; JS simulator + iverilog golden check.
4. **Decompiler:** block recovery → templates → LLM glue → per-cone proof → retry. Tune on the dev set only.
5. **Run the frozen test set:** proof wall + side-by-side view + X/N number.
6. **Find:** planting script, rare-trigger flag, cone; then the trojan benchmark on the test set.
7. **Fix and guide** if time allows.
8. **Polish, backup video, pitch rehearsal** (§9 and its Q&A table).

**Checkpoints** (submission time to confirm; Alex's docs assume Sun ~11:30):
- **Hour 6 (gate):** ≥3 TT08 chips proven end to end, or switch to Plan B (§5.1).
- **Sat 22:00:** P0 done (warm-up in 3D, simulation matches iverilog, proof pipeline working).
- **Sun 03:00:** P1 done (test set decompiled and scored; judge's number found as code).
- **Sun 07:00:** P2 done or dropped.
- **Sun 08:00:** feature freeze, record the backup video, rehearse.

## 11. Team split (proposal; the lead assigns)
| Stream | Owns | Folders (proposal) |
|---|---|---|
| A. Proof + data | TT08 fetch, the dev/test split, Yosys proof pipeline, benchmark harness and scoring, Liberty/DEF ingest | `decap/ingest/`, `decap/api/proof/`, `decap/bench/` |
| B. Decompiler + find | Block recovery, Verilog templates, LLM glue and retry loop, planting script, rare-trigger flag, cone | `decap/web/src/analysis/`, `decap/api/decompile/` |
| C. Stage + UI | 3D warm-up, JS simulator + golden check, proof wall, side-by-side code view, demo flow | `decap/web/src/view/`, `decap/web/src/ui/`, `decap/web/src/sim/` |

Fill the owner table in `AGENTS.md` §4 once agreed (Ian's PR #5 owner guard reads it).

## 12. Risks and fallbacks
| Risk | Fallback |
|---|---|
| Yosys can't prove equivalence on SKY130 cells / these netlists | Hour-6 gate → Plan B (§5.1). Within the plan: prove combinational cones only, and label the rest "tested, not proven" (random simulation) |
| Templates don't cover a chip's logic | The LLM writes the leftover glue; if that fails, the chip shows red on the wall. Report it honestly. |
| Low proven rate on the test set | Report it as measured. A smaller honest number beats a big claimed one. Shrink scope to the smallest chips, but never below 10 test chips. |
| A sequential design's state doesn't map 1:1 | Keep flip-flops 1:1 by construction (the decompiler names registers after netlist flops); otherwise "tested, not proven" |
| Rare-trigger flag has many false positives (legit comparators) | Show them. The with-source check (unexplained logic) separates them exactly; blind mode is a ranked list for a human |
| Prior art (Gate-Breaker, GenEDA) undercuts novelty | Read them before Q&A. Our claim is proof + held-out score + live, not novelty of the idea |
| JS sim doesn't match iverilog | Use iverilog in the API to drive values (slower but correct) |
| DEF parsing or routing rendering eats time | Render cells only, with straight-line "flylines" for connectivity |
| Jane Street data can't be used publicly | The Stage uses a TT08 chip instead (cells from GDS via KLayout) or the prototype counter |
| Venue wifi / API down | Cached proof-wall results, offline guide answers, recorded video |

## 13. Context from today (decision log)
- **Ideation history and scores:** #4 (Decap), `ideas/` (Alex's 3-model council: Sonnet / Opus / Grok).
  - Decap as written: 81.0 for G-Research. Decap v2: 76.0 (too much scope).
  - "Silicon, live": 85.3.
  - GlassDie: **90.7** (after five improvement rounds; v1 was 85.0).
  - Fault Lines: 89.3.
- **Yuyao wants the IDE framing:** analyse a chip, click to understand, an AI guide, AI part-swapping. The current plan keeps those as the Stage, the AI's role in the decompiler, and the optional Fix.
- **Story exploration (Sat evening, Yuyao + Claude):** nine angles weighed: hidden logic/trust, learner, synthesis black box, inherited chip, gate-level debugging, late change (ECO), AI writing hardware, wonder, copy detection. Plus two new ones: silicon decompiler and glitch hunter (fault injection).
- **Council round 1 (Sat evening; Sonnet + Opus; `ideas/COUNCIL.md` rubric; brief in Yuyao's session scratchpad):**

  | Framing | Track | Sonnet | Opus | Mean |
  |---|---|---|---|---|
  | Silicon decompiler | G-R | 75 | 78 | **76.5** |
  | Code review for silicon (hidden trigger) | G-R | 77 | 74 | **75.5** |
  | Code review for silicon | MetaView | 74 | 76 | 75.0 |
  | Silicon decompiler | MetaView | 69 | 73 | 71.0 |
  | Hybrid (agent + trigger) | G-R | 67 | 59 | 63.0 |
  | Glitch hunter | G-R | 60 | 59 | 59.5 |
  | Hybrid | JetBrains | 56 | 55 | 55.5 |
  | Agentic chip editor with proof | JetBrains | 55 | 53 | 54.0 |
  | Agentic chip editor with proof | MetaView | 54 | 51 | 52.5 |

  - **Both judges independently proposed the same combination:** decompile held-out TT08 chips, score against the authors' source, and use the planted trigger as the finale. Estimates: 82–85 (Sonnet) and ~83 (Opus). **This spec is that combination; it has not been rescored yet.**
  - **Their key advice:**
    - Make the decompiler structure-first (templates; the LLM only names and writes glue).
    - Fix the benchmark before tuning.
    - Gate at hour 6 on ≥3 TT08 chips proven.
    - Report false positives on clean controls.
  - **The agentic-editor headline scored lowest** ("Cursor for X" is crowded, there's prior art, and the win is tiny on 8-bit logic), so the AI is a helper, not the headline.
- **Taken from GlassDie (`ideas/08-glassdie.md`, Alex's variant):** data-scale proof on real TT chips (as a proof wall, not a galaxy), the iverilog golden check, "measured, not claimed", hard gates and a cut order.
- **Not taken from GlassDie:** the UMAP galaxy (P3 at most), docs-text family labels, and OpenLane-in-Docker DEF regeneration (our benchmark needs only netlists).
- **Where Decap differs from GlassDie:** the answer key is each author's full source plus a formal proof, versus GlassDie's leftover register names and accuracy bands. The two overlap heavily, so discuss merging them with Alex and Ian rather than competing.
- **The council's technical notes still apply:** strip instance names before blind analysis; sequential cells need the Liberty `ff` groups.
- **Today's findings:**
  - KLayout on the Mac reads GDS cell types and positions directly.
  - The TT08 size count (§8): enough small digital chips for a benchmark.
  - Neither has been posted to the board yet.
- **"Chips like mine" (Yuyao, Sat evening):** import your design and compare it with similar real chips. Added as P2 Compare (features 16–19). It stays out of the 90-second script (a third moment would dilute it) and appears as a Q&A answer and the student line.
- **Team board:** pinned issue #1, `tools/board` (rules in `AGENTS.md`). Keep one background `tools/board wait` running. Use `tools/board read -n N` to look without marking anything read.
