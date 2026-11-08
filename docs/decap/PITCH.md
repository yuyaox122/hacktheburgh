# Decap: View Source for silicon (pitch and facts)

What we say on stage, the numbers behind it, and what we must not claim. As built there is **no LLM in the decompiler or the proof**: it is templates, exact logic and Yosys. How it works and how to run it: [`decap/README.md`](../../decap/README.md). Data shapes and API: [`decap/CONTRACTS.md`](../../decap/CONTRACTS.md). The agent layer (proven claims about a chip region): [`witness-plan.md`](witness-plan.md).

This file replaces `SPEC.md`. The planning parts of the spec (feature list, priority ladder, Plan B, planned architecture, build order, team split, council log) were removed once the build existed. They are in git history.

## 1. The product, the pain and the claim

**Product:** Decap turns the file a chip is actually made from (the gate-level netlist) back into short Verilog a person can review, and proves that Verilog is exactly the same circuit.

**Pain:** the source code isn't what gets manufactured. The netlist is, and nobody can read it, so nobody can check it does only what the source says.

- **The software parallel is the xz backdoor (2024):** the build-script line that switched it on was only in the release tarball, not in git ([Freund, oss-security, 29 Mar 2024](https://www.openwall.com/lists/oss-security/2024/03/29/4)). (The payload itself *was* in git, disguised as test files, so don't say the backdoor was absent from git.) Chips have the same gap between what was reviewed and what was shipped, and no way to read what was shipped.
- **Third-party chip parts are often delivered as netlists only** (to protect the vendor's secrets). Quote WordRev (HOST 2013, abstract on the authors' page) word for word: *"Often times only post-synthesis gate-level netlists or actual Silicon chips are available for security inspection."* (The IEEE abstract reads "Often only ... or actual silicons ...".) Avoid percentages or "most IP".
- **AI now writes chip code, and AI can hide things in it.**
  - GHOST (arXiv 2412.02816, Dec 2024, a preprint): none of 14 synthesisable trojans inserted by LLMs was caught by one open-source ML detector (Hw2vec): 9 went undetected and 5 timed out after 4 hours.
  - CSAW 2025 ran an "AI Hardware Attack Challenge": students used generative AI to plant flaws in open RISC-V and crypto designs.
  - In 2026 Cadence, Synopsys and Siemens all *announced* AI agents for chip design. Siemens' Questa One RTL Code Agent writes RTL and is in early access. Cadence's RTL agent is early access from Q4 2026. Say "announced", not "sell".
- **Today's tools give a verdict, not something to read.** An equivalence checker says "N points don't match"; a trojan detector names a wire. Decap's answer is **a line of code**.

**User:** a security reviewer handed a chip block they didn't write. One persona.

**The claim and its three numbers.** On 18 real Tiny Tapeout chips held out from tuning (names stripped; 27–260 logic cells; measured, `decap/out/bench.json`, decompiler `5dc6bfd`):
1. **Proven: 15 of 18** decompiles proven equal by Yosys. 1 is "tested, not proven" (its clock relies on real gate delays). 2 can't be read (they contain a hard macro whose netlist isn't published). The first scored run (`911f15a`) was also 15 proven, with 3 failed. `dpmu` and `dpmunit` are the same design, so the 15 are 14 distinct designs.
2. **Readable:**
   - **52% of logic cells** are explained by recognised blocks (mean per chip; **37% weighted by cell count**; median 37%).
   - The decompile is **shorter than the author's code on all 9 test chips with hand-written Verilog**: 480 vs 1,307 lines in total (37%), from 4% to 96% per chip. The author total still includes some unused modules.
   - *This is the hard part.* A gate-by-gate transliteration passes the proof trivially, so "proven" alone proves nothing about readability.
   - Don't quote the README's 675 vs 1,769: 462 of those author lines are the generic 77-line Wokwi `cells.v` on 6 schematic chips.
   - Don't set 52% beside Subramanyan's 68% as if they were comparable. Theirs is "68% of the gates left after simplification" of a 375k-gate SoC, and 45–93% on smaller designs. We count every cell, buffers included.
3. **Finds what doesn't belong:** **16 of 16** planted triggers caught (the first run caught 15 of 16), with false alarms on **3 of 16** clean chips. One of them is a combination lock (`ui_in == 8'd118`): legitimate, and the same shape as a backdoor.

**The moment:** the judge picks a number; it comes back as a line of recovered code nobody wrote: `assign uo_out[0] = … | (ui_in == 8'd<N>);`. The author check (only through the agent route `POST /api/chips/{chip}/prove`, not in the web UI, and only on the 6 of 18 test chips whose author source Yosys can read) fails on the planted copy. **Don't promise the counterexample is `ui_in = N`:** `core/chip.py` `_parse_cex` reports the inputs at the last of 20 steps, not the step where the outputs differ (measured: `tt_um_mac` + 137 returns `ui_in = 55`).

## 2. The pitch

**To a random person (one sentence):**
> Decap turns a computer chip back into readable code and proves the code is exactly right, so anyone can see what the chip really does, including anything hidden inside.

**Tagline:** *View source for silicon, with proof.*
**Closing line:** *"You can't review what you can't read. Now you can read silicon."*

**One-liner (technical audience):** Decap decompiles real chips it has never seen (gate-level netlists with every name stripped) back into readable Verilog, formally proves each result is the same circuit, and scores itself against the authors' real source code. Because the output is code, hidden logic shows up as a line you can review.

**Why it matters now:**
- **Chips are built from parts you didn't make:** third-party IP blocks, outsourced layout, someone else's factory. A few extra gates (a *hardware trojan*, e.g. a comparator waiting for a secret value) appear in no source file. Code review can't catch what isn't in the code.
- **AI is starting to write chip code.** A change nobody can read back is a change nobody can check.
- **Hardware is permanent.** Software bugs get patched; a shipped chip can't be. Intel took a $475 million charge for its 1994 Pentium division bug (§5).

**Why it's possible now:**
- **Every gate has a known meaning:** each SKY130 standard cell's function is published (Liberty).
- **Open tools can prove two circuits identical:** Yosys equivalence checking.
- **Real chips come with answer keys:** almost every digital Tiny Tapeout 08 chip ships its gate-level netlist and a `commit_id.json` pointing at the author's source at the exact commit. So we can measure how well Decap reads chips it has never seen.

**The argument chain** (pitch order; each step earns the next):

| # | Argument | Job it does | Line |
|---|---|---|---|
| 1 | You can only trust code you can read | Something every judge already agrees with | "You'd never merge a pull request you can't read." |
| 2 | Silicon can't be read | The pain | "Every chip is one. There's no View Source for silicon." |
| 3 | Two pressures | Stakes, why now | "Chips are built from parts you didn't make, and AI is starting to write them. And you can't patch a chip." |
| 4 | Decap reads it back, with proof | The answer | "Decap turns gates back into code, and proves the code is exactly right." |
| 5 | Measured on real chips it never tuned on | Credibility (G-Research's criterion) | "Proven on 15 of 18 real Tiny Tapeout chips held out from tuning. Here's the author's own code beside ours." |
| 6 | The judge's number | The moment | "You picked a number. Here it is, as a line of code nobody wrote." |
| 7 | Close | The line they repeat | "You can't review what you can't read. Now you can read silicon." |

**Per track** (one build; only the first 15 s and the headline number change):

| Track | Opening line | Headline number | First moment |
|---|---|---|---|
| **G-Research** (security angle) | "The netlist is where tampering hides, and nobody can read it." | 16/16 triggers caught, false alarms on 3/16 clean chips, then 15/18 proven | The judge's number |
| **MetaView** (best overall) | "Software has View Source. Silicon doesn't." | 15/18 real chips read back and proven, beside the author's code where it exists | Reading a chip, then the judge's number |
| JetBrains, HRT | n/a | n/a | Skip: no plugin, no prediction driving a decision |

**In the main 90 s:** the decompile + proof, the Results wall, block coverage, the planted trigger + judge's number, the author's code side by side, the 3D chip as a 10-second opener. **Built but kept for Q&A:** Fix, Compare ("chips like mine"), the guide chat.

## 3. What the pitch leaves out, and honesty rules

- **The agentic AI editor as the headline.** "Cursor for X" is crowded, and an 8-bit "make it faster" shows nothing. As built, the AI has no job in the decompiler or the proof. It is only the optional guide chat that explains a proven result, and it can't change a verdict (`decap/core/guide.py`). Witness ([`witness-plan.md`](witness-plan.md)) gives it a checked job: proposing claims that Yosys must prove.
- **3D as the argument.** It's the stage, not the reason. 3D chip viewers already exist (§4).
- **"Microscope, tour guide, scalpel."** Three heroes read as a feature list.
- **The Jane Street ASIC puzzle.** Not the motivation (Yuyao's call). The warm-up files are data only (§8), and the hero number is the *judge's*.

**Honesty rules (say these if asked; never contradict them):**
- We plant the trigger ourselves and say so. The judge picks the number; we don't know it in advance.
- Decap reads design files (netlists, layouts), not manufactured chips.
- Small open chips (27–260 logic cells in the benchmark, the small end of Tiny Tapeout, whose median is about 800) today. We report the proven rate exactly as measured, including failures, and both scored runs.
- "Proven" means a Yosys equivalence proof passed with flip-flops paired 1:1 (same outputs and next state from any matching state), and a deliberately broken control copy failed. It says nothing about timing, the factory, or readability. "Tested, not proven" means a 20-cycle bounded formal check from reset passed, not random simulation.
- The planted trigger is always `uo_out[0] |= (ui_in == N)` on the input pins. That is the easy case for a rare-signal flag. Multi-cycle triggers, triggers split across a register stage, and triggers over more than 18 bits are not caught today.
- Confirmed trojans in real chips are rare, and some famous reports are disputed. Say "can't be checked", never "is happening everywhere".
- Hardware decompilation with formal checks has been published before (Sisco PLDI 2023, Subramanyan 2014), and so has AI netlist → RTL (§4). There is no AI in our decompiler. Our claim is proven whole chips, blind, scored on real held-out chips, live, not that the idea is new.
- The test split was frozen before tuning. After the first scored run we made general fixes, and we report both runs. Don't say "never touched after the first run".

## 4. Prior art and our edge

| Tool or work | What it does | What Decap adds |
|---|---|---|
| Tiny Tapeout GDS viewer, Tiny Tapeout Explorer | 3D shapes of real open chips | Meaning: readable code recovered blind, with proof |
| DigitalJS (BSD-2) | Browser simulation of Yosys netlists as schematics | Readable code, not a schematic; a proof, not a simulation |
| HAL (github.com/emsec/hal, MPI-SP, MIT licence) | Aims to be "the hardware-reverse-engineering-equivalent of tools like IDA or Ghidra": exploration and manipulation for experts; its writers emit the netlist, not readable RTL | A short, proven decompile anyone can read, live, in a browser *(checked)* |
| **Sisco et al., "Loop Rerolling for Hardware Decompilation", PLDI 2023** (doi 10.1145/3591237) | Introduced "hardware decompilation"; rerolls loops in 52 of 53 netlists and checks each output wire with an SMT solver | Whole-chip proofs on 18 real held-out chips, scored against the authors' code, with a live judge-chosen trigger *(checked)* |
| Narayanan et al., ISQED 2023 (arXiv 2303.02762); Subramanyan et al., IEEE TETC 2014 / WordRev HOST 2013 | Recovered RTL or blocks **checked formally** (JasperGold; SAT, BDDs, QBF) on benchmark designs. Subramanyan includes a trojan-detection case study | Same as above *(checked)* |
| Gate-Breaker (IEEE IPCCC 2025, github.com/NMSU-PEARL/Gate-Breaker), GenEDA (ICCAD'25, arXiv 2504.09485) | LLMs turning netlists back into RTL, **checked by testbench or simulation** (GenEDA: 9 arithmetic designs, about 60% pass in its best setup) | Every result formally proven; no AI in the decompiler *(checked)* |
| EXERT and other SAT-based trigger finders; ML detectors on Trust-Hub | Find trojan triggers in netlists | The trigger appears as readable code, measured against clean controls |
| Commercial equivalence tools (Cadence Conformal, Synopsys Formality) | Prove two netlists equal, for specialists | The same kind of proof, used to certify a decompile |

**Never say "first decompiler", "first to prove", or "first to find trojans by decompiling".** Name Sisco 2023 and Subramanyan 2014 yourself. The defensible edge: *earlier decompilers proved pieces on benchmark designs, and the AI ones only test. Decap proves whole decompiled chips, blind, on 18 real Tiny Tapeout chips it never tuned on, scored against the authors' code where it exists, with a trigger the judge picks, live.* **That combination is the edge, not the idea of decompiling.**

## 5. Facts

**Check before saying on stage:**

| Claim | Status |
|---|---|
| Pentium FDIV cost Intel ~$475M | **OK:** say "Intel took a $475 million charge" (Jan 1995). It was a design bug, not a trojan. |
| "AI is starting to write chip code" | **OK:** Siemens Questa One RTL Code Agent (27 Feb 2026, early access); CSAW 2025 AI Hardware Attack Challenge. The Synopsys "10–30% RTL productivity" figure is one customer's own quote (Fujitsu, on Synopsys' industry-quotes page; the blog calls it "one customer engagement"). It is vendor-published, not a benchmark, and covers assertions, wrappers and refactoring. |
| Gate-Breaker, GenEDA, HAL | **Checked**, see §4. EXERT not checked. |
| xz, GHOST, WordRev quote | **Checked**; use the wording in §1. |
| "Every TT08 chip ships its netlist" | Say **"almost every digital TT08 chip"**: some are analog or ROM stubs, and 2 of our test chips contain an unpublished hard macro. |
| Tiny Tapeout Explorer and the GDS viewer are live | Not checked |

**Measured (7 Nov 2026):**

| Fact | How verified |
|---|---|
| **Benchmark (decompiler `5dc6bfd`):** 27 TT08 chips (9 dev, 18 test, frozen split) + warm-up. Test: 15 proven, 1 tested, 2 failed; 52.4% mean coverage (36.9% weighted); 16/16 triggers caught, 3/16 clean chips flagged; author check proven on 6 of 18. Dev: 9/9 proven. Counter blocks proven on 5 chips. Slowest proof 15.2 s; slowest whole chip 72.6 s (`yuri_panchul`: clean pass, author check and planted copy; the API reuses the saved author check, so it takes about 2 s in the demo). | `decap/out/bench.json`, recounted per chip |
| **First smoke test** (`tt_um_Electom_cla_4bits`, 59 logic cells, 5 flip-flops): author source ≡ shipped netlist proven (24/24 points, ~1 s); one swapped gate fails; a planted `uo_out[4] \|= (ui_in == 137)` fails with counterexample `ui_in = 137`; a hand-written 5-line blind decompile (`r <= a + b + cin`) is proven by induction, and fails against the planted copy | Yosys, before the decompiler existed |
| **Trap:** `miter -ignore_gold_x` made the planted-trigger check pass silently. Never use it; every proof runs a broken control alongside | Smoke test |
| **Leak:** TT08 netlists keep the author's internal wire names (`co`, `s_w[0]`). They are stripped (`rename -hide w:* c:*; opt_clean -purge`) before anything "blind" | Smoke test |
| TT08 sizes (logic cells, excluding fill/tap/decap/diode/conb): 121 netlists fetched (14 failed, mostly analog/ROM projects); median 819; 20 Verilog/SystemVerilog chips with 20–300 logic cells, plus 6 Wokwi chips (28–168). Counts include buffers. | `gh api`, regex count, `info.yaml` |
| A TT08 `commit_id.json` points to the author's repo + commit, and the source exists there (`tt_um_Electom_cla_4bits` → `Electom/tt08_CSA_4bits@213ea90`, `src/project.v`) | `gh api` |
| TT08 GDS files keep each netlist instance name as GDS property 61, so netlist cells can be placed in 3D without a DEF (25 of 27 chips match fully: every cell matched, 0 conflicts, no split nets. The two `micro_tiles` chips are skipped because they have no decompile result; they contain a hard macro whose netlist isn't published) | `ingest/ingest_gds.py` |
| The warm-up: 79 logic cells, 16 types; die 100 × 100 µm; hierarchical instance names (`add0/_32_`), no `src` attributes | `01_netlist.v`, `klayout.db` |

## 6. Demo script (90 s) and judging moments

**Before the timer:** ask a judge for a number from 0 to 255, and let them pick a test chip from the Results wall. Planting runs in the background during the hook; planted proofs take ≤15 s. Press "Plant now" as soon as they pick. If it fails, use a pre-planted copy and say so.

**Steer the chip pick** (measured; check each before rehearsal):
- **Best:** `tt_um_emmyxu_obstacle_detection` (100% coverage, author check proven, 0 false alarms, 25 vs 59 lines; bench N = 73).
- **Next:** `tt_um_kb2ghz_xalu` (N = 128) or `tt_um_dpmu` (N = 198).
- **`tt_um_Richard28277`: for every N, the page jumps to its clean false alarm** (line 89, `ui_in[3:0] != 4'd1 || …`), because it is listed before the trigger (line 108). Click the trigger's flag card by hand, or pick another chip.
- **Avoid:**
  - **The Wokwi chips:** their author pane shows a generic `cells.v`, and on `408216` / `408231` about half of all N leave no line in the code (only the flag card shows N).
  - **`brailliance`:** the repo is a Braille converter, but the shipped top is the template adder, and the author pane opens on an unused file.
  - **`yuri_panchul`:** no author check (its bounded check doesn't finish), and its 292-line author source includes four unused buffer variants.
  - **The two red `micro_tiles` chips.**
- **Warm the chosen chip** (open it once through the API) after starting uvicorn.

> **(0–15 s) Hook.** "You'd never merge a pull request you can't read. Every chip is one. Software has decompilers; silicon has no View Source. That matters, because chips are built from parts you didn't make, AI is starting to write them, and you can't patch a chip."
>
> **(15–30 s) Explore.** *(a real chip in 3D; click a gate, run it, wires glow)* "This is real silicon. Every gate has a meaning, but nobody can read hundreds of them."
>
> **(30–55 s) Read.** *(the Results wall: real Tiny Tapeout chips Decap has never seen)* "Decap read 15 of these 18 back into code, blind. Green means a formal checker *proved* the code is exactly the same circuit." *(the judge's chip, precomputed by the benchmark: names stripped, blocks recovered, Verilog, **"Equivalence: proven"**)* "And here's what the author actually wrote." *(side by side)* Don't say "watch it decompile": the clean read is cached. The planted copy is the live part.
>
> **(55–80 s) Find.** "You picked a number, and we hid a trigger for it in this chip." *(flagged line: `assign uo_out[0] = … | (ui_in == 8'd<N>);`)* "There it is: your number, as a line of code nobody wrote. And it reaches exactly one output pin." Click the trigger's flag card, not "Light the cone", which lights every flag's cone. "Across the test chips, Decap caught 16 of 16 planted triggers, with false alarms on 3 of 16 clean chips." Press 2 for the wall if you want the numbers on screen. Don't promise "one flagged block": some chips flag a legitimate comparator too.
>
> **(80–90 s) Close.** *(optional, needs the API: remove it, then "proven equal to the chip before tampering", plus "and to the author's code" only on chips whose author check is proven)* "You can't review what you can't read. **Now you can read silicon.**"

**The moments that win:**
- 15 of 18 proven on chips we never tuned on (G-Research's criterion).
- The judge's own number appearing as code. It can't be rehearsed or faked.

### Likely questions

| They'll ask | We answer |
|---|---|
| "Isn't AI decompiling unreliable?" | "There's no AI in the decompiler. It's fixed templates plus exact logic, and Yosys proves every result. Failures are shown in red. The AI is only an optional chat that explains a proven result, and it can't change a verdict." |
| "Of course it's proven: you built the code from the netlist." | "Matching isn't the hard part; being both short and exact is. The decompiler guesses blocks from random samples. The proof rejected a guess on 5 chips; the rejected parts were rewritten as plain logic, and 4 of them (3 distinct designs) then proved. The fifth is the delay-line chip we label 'tested'. A gate-for-gate copy would pass trivially, so we measure readability separately: on average 52% of each chip's logic is recognised blocks (37% weighted by size)." |
| "Has this been done?" | "Decompiling chips isn't new, and neither is proving it: Sisco at PLDI 2023, Subramanyan in 2014. The AI versions only test. We prove whole chips, blind, on 18 real Tiny Tapeout chips we never tuned on, against the authors' own code, live." |
| "You planted the trigger yourselves." | "Yes, and you chose the number. We also report false alarms on clean chips." |
| "Would it catch a stealthier trojan?" | "Not with the blind flag alone. It's the classic rare-signal check, and published trojans are built to evade it (multi-cycle, split across registers). We test the easy case and say so. The stronger guarantee is the proof: whatever is in the chip is in the proven code, and against the author's source any difference fails with a counterexample." |
| "3 false alarms on 16 clean chips?" | "One is a combination lock: `input == 118`. A lock and a backdoor look identical to a blind check. Decap shows you the line in seconds; deciding whether it belongs needs the author's intent or source." |
| "Real chips have millions of gates." | "Today, tiny ones: 27 to 260 cells, the small end even of Tiny Tapeout. Proofs take seconds. The proof is the same kind industry runs on big designs; recognising blocks at that scale is the open problem." |
| "Can it check a manufactured chip?" | "No. It reads the design files, the last point before the factory where you can still check." |
| "Can I try it on my own design?" | "Yes: paste it into Compare and Decap shows the most similar real chips and how they built the same blocks." |
| "Did you tune on the test chips?" | "The split was frozen before any tuning, and we tuned on the dev chips. Our first scored run was 15 of 18 proven, with 15 of 16 triggers caught, and we keep it. After seeing its failures we made general fixes (derived clocks, hierarchical netlists, flag wording, the warm-up planter), which turned one failed chip into 'tested' and caught one more trigger. We report both runs." |
| "Is the AI writing any of this?" | "No. Every line comes from templates and is proven by Yosys." (If pitching Witness: "The AI proposes short claims, and only a formal proof lets one count.") |

**Backups:** a recorded video, pre-planted copies, cached Results-wall data, and offline guide answers.

## 7. Chip primer (for teammates and new sessions)

- **Transistor** → **logic gate** (AND/OR/XOR/NOT, a few transistors) → **flip-flop** (stores 1 bit, updates on each clock tick) → **blocks** (adder, counter, register, comparator) → chip.
- **Standard cells:** the factory's pre-made Lego bricks. SKY130 HD (`sky130_fd_sc_hd__*`) has a few hundred, each with a known function (in the **Liberty** `.lib` file) and a known shape. Cell height is 2.72 µm; widths are multiples of a 0.46 µm "site".
- **Flow:** Verilog → **synthesis** (Yosys) → **netlist** (cells plus wires) → **place & route** (OpenROAD/OpenLane/LibreLane) → **DEF** (cell positions and routing, with names) and **GDS** (final polygons on every layer) → factory.
- **Three views of one design:** code (intent), netlist (circuit), layout (physical). Decap's job is moving between them, with proof.
- **Decompiling** (software term): turning compiled output back into readable source. For chips, it means netlist → Verilog.
- **Equivalence checking:** a formal tool (here Yosys) proves two circuits give the same outputs for *every* input and state, not just the ones you tested. "Proven" in this doc always means this.
- **Hardware trojan:** extra logic hidden in a chip, usually a *trigger* (a rare condition, like one secret value) plus a *payload* (what it does when triggered).
- **What's NOT feasible:** reverse-engineering a commercial chip from silicon (needs delayering and electron microscopes), and manufacturing anything during a hackathon.

## 8. Data and licences

| Source | What | Licence / use |
|---|---|---|
| **Tiny Tapeout 08** (`TinyTapeout/tinytapeout-08`) | Per project: gate-level netlist, GDS, LEF, `info.yaml`, and `commit_id.json` (the author's source repo + commit = **our answer key**). No DEF. | Projects are mostly Apache-2.0; check per project |
| **SKY130 PDK** (`google/skywater-pdk-libs-sky130_fd_sc_hd`) | Per-cell Liberty JSON (`cells/<c>/…__tt_025C_1v80.lib.json`; there is no merged `.lib`), LEF, functional models | Apache-2.0 |
| **Jane Street `asic-puzzle-2026`, `warmup/`** | `00_source.v` (S = 1 when A + B == 496), netlists, DEF, GDS | **No licence file.** Fetched at setup, never committed, never on the public site; credit them on screen. **Never feed `puzzle.gds` to an AI tool.** We don't use the puzzle at all. |
