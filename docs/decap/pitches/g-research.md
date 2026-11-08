# G-Research pitch: "Make the invisible visible"

**Prize:** Nintendo Switch 2 + Mario Kart. **Our best-fit track.** The brief is nearly our product description.

Numbers and honesty rules: [`../PITCH.md`](../PITCH.md). If this file disagrees with it, `PITCH.md` wins.
Status: 8 Nov 2026, 09:15 (synced with main through #99 and the board's 08:33 decision: no held-out Witness number in any pitch). On stage and on slides the product is **bASIC**; code and folder names (`decap/`, the `decap` CLI, API paths) stay as they are.

## What the judges want

From their slide:
- Choose something that's hard to observe. Build tools to collect your own data, or dig into public data.
- *"Show us what data is hidden, how we can find it, and what we can do with it."*

From the opening ceremony:
- Find hidden patterns in data, and make them look and be cool.
- Creative **and** structured solutions.

G-Research are quant researchers. Expect them to probe two things:
- **Held-out evaluation:** did we tune on the test set?
- **Whether the visual means something,** or is just pretty.

## How this pitch is different from the others

- **Built on their three questions.** We say "hidden", "find" and "do" out loud, as section headers.
- **The visual carries the most weight.** Minecraft here is the *layout* world (Alex's showcase: the real GDS as blocks, wires that glow, the trojan as a red annex). It shows the hidden structure, and the lamp too.
- **The data story is explicit:** where the data comes from (TT08 netlists, GDS property 61, `commit_id.json` answer keys), how we collected it, and the held-out split.
- **One headline number:** the decompiler's. The AI gets a live moment instead of a number (see Witness below).

## The hook (0–15 s)

> "Every chip is built from a file nobody can read: the gate-level netlist. Hundreds of gates, no names, no comments. That's where tampering hides, because code review never sees it. We made it visible: first as code you can read, then as a chip you can walk through."

## The script (90 s)

Run Presenter mode (`D`). Its step buttons can be clicked in any order. Minecraft is a separate window: alt-tab to it.

| Time | Beat | On screen | Say |
|---|---|---|---|
| 0–15 s | **Hook** | 3 s of the fly-through (`minecraft/out/flythrough.mp4`), then the 3D chip (Presenter step 1) | The hook |
| 15–30 s | **1. What's hidden** | Explore (step 2): click a gate, press Run, wires glow | "This is a real Tiny Tapeout chip, from its published layout. Every gate has a meaning, but nobody can read hundreds of them. And every one of these chips has a hidden answer key: the author's source, at the exact commit." |
| 30–50 s | **2. How we find it** | Results wall (step 3), then the judge's chip: Read (step 4), "Proven equivalent", author's code beside it | "bASIC reads the gates back into code, blind, with names stripped. Then a formal checker *proves* the code is exactly the same circuit. 15 of 18 real chips we never tuned on, proven. And here's what the author actually wrote." |
| 50–70 s | **3. What we do with it** | Find (step 5): the judge's number as a flagged line; click its flag card | "You picked a number. We hid a trigger for it in this chip, and here it is: a line of code nobody wrote. On held-out chips: 16 of 16 planted triggers caught, false alarms on 3 of 16 clean chips." |
| 70–85 s | **Walk inside it** | Minecraft showcase world: `/function decap:tour/…` to the trojan annex (red blocks, the flagged line floating above), then the redstone lever bank: flip to the trigger value, the lamp lights | "Invisible to code review, visible from orbit. And because the code is proven, it's the real circuit, so we rebuilt it in redstone. The trojan comes along with it." |
| 85–90 s | **Close** | Lamp lit | "You can't review what you can't read. **Now you can read silicon.**" |

**Minecraft honesty:** until Yuyao's "judge's number in Minecraft" lands, the redstone copy is pre-planted with **78** (on `uo_out[0]`; the honest sum at 78 is 18, bit 0 = 0, so the clean copy stays dark). Say: *"This one we planted earlier with 78. Yours is the one in the browser."* If the live version lands and has been rehearsed, use the judge's N.

**If the Minecraft client fails:** the website's Minecraft page (`#/minecraft`) shows the same build in 3D with real Minecraft block models (repeaters, comparators, dust, levers, lamps), and the levers and lamps toggle live. This needs `npm run mc-assets` once on the demo laptop, and the page served from `npm run dev`: the textures are loaded in dev only and stripped from builds (Mojang's assets stay off the public site, #72), so the public site shows flat colours.

## The data story (say it if they ask, or use it to fill the 30–50 s beat)

| Question | Answer |
|---|---|
| **Data source** | Tiny Tapeout 08: 121 netlists fetched with `gh api` (14 failed, mostly analog/ROM). Each digital chip ships a netlist, a GDS, and `commit_id.json` pointing at the author's repo + commit. |
| **The hidden join** | The GDS keeps each netlist instance name as **property 61**, so every gate can be placed in 3D without a DEF (25 of 27 chips match fully: every cell matched, no shorts or split nets; re-measured 2026-11-08 with `ingest/ingest_gds.py --all`; the two `micro_tiles` chips are skipped. Same figure as `PITCH.md` §5.) |
| **Leak we had to close** | TT08 netlists keep the author's wire names (`co`, `s_w[0]`). We strip them before anything "blind". |
| **Evaluation** | 27 chips: 9 dev, 18 test, **split frozen before tuning**. Both scored runs reported (first run: 15/18 proven, 15/16 caught). |
| **Grouping, beyond correctness** | A gate-by-gate copy would pass the proof trivially, so we also report the share of gates grouped into named blocks (counters, adders…): 52% of logic cells (mean; 37% weighted). Every line is decompiled and proven either way; this number is only about grouping, so don't headline it or call it a readability score. The decompile is also shorter than the author's code on all 9 test chips with hand-written Verilog (480 vs 1,307 lines). |

## Witness: what to say (decided on the board, 08:33)

There is **no held-out Witness number**: the bench was not run, so no "___ of ___" beat anywhere. Say instead:
**"Agents propose, a proof scores every claim"**, and show one live example. Any Witness number you quote must be
labelled **dev** (it comes from the 9 dev chips), never held-out.

What the agent does live, on main (pick one for the demo):
- **Explain** (hover a line, or a cell in 3D): a plain-English explanation in about 3 to 6 s, read from the
  decompiled code, which Yosys has already proven equal to the gates. If the agent also finds a short formula
  Yosys can prove, that replaces it with a green "proven" card. Dev sweep: 93 of 98 blocks on 9 dev chips got an
  explanation, median 2.6 s.
- **Red-team drill** (Agent drawer): it plants a trigger at a secret random value, then hunts it blind. It passes
  only if the proof's counterexample is exactly that value. Rehearsed: secret 192, found `ui_in = 192` in 9.5 s.
- **Check my Verilog:** upload your own `.v` and Yosys says whether this chip implements it, or the input where
  they differ. Round trip on `cla_4bits`: download the decompiled Verilog, upload it back, "match", 0.4 s.
- **Audit this chip:** the agent writes a plan, gives a reason before each step, and ends with findings tagged by
  their evidence (`yosys`, `proof`, `code`, `structure`).

Honest lines if asked:
- The live website agent runs on **DeepSeek** for speed (the dev bench used Claude Sonnet). The model can be
  swapped (Gemini is one flag); the verdict never depends on it.
- A hunt on a real chip with no planted trigger comes back **"inconclusive"** (a 20-cycle bounded check passed,
  which is not a proof). Don't call it "clean".
- Interactive agents can't touch the held-out chips at all: the agent layer refuses them (added 08:20).

**For G-Research, use the drill.** It answers "how do we find it?" with a fresh secret the team didn't choose:
> "We just hid a backdoor at a number none of us picked. The agent found the exact input that wakes it, from a
> proof, not from a guess."

Don't say "a proof behind every chip we ship": the proof shows the code equals the gates, not that a chip has no
trojan.

## Likely questions

| They'll ask | We answer |
|---|---|
| "Did you tune on the test chips?" | "No. The split was frozen first. Our first scored run was 15/18 proven and 15/16 caught. We made general fixes after it and report both runs." |
| "Is the Minecraft layout real?" | "Yes: every cell sits at its GDS position, the wires come from the GDS nets, and the world's logic matches the reference simulation 30 out of 30. The redstone build keeps the real rows and approximate x; it stretches the rows apart to make room for wiring." |
| "Would it catch a stealthier trojan?" | "Not with the blind flag alone. Published trojans are built to evade rare-signal checks. The stronger guarantee is the proof: whatever is in the chip is in the proven code." |
| "3 false alarms out of 16?" | "One is a combination lock, `input == 118`. A lock and a backdoor look identical to a blind check. bASIC shows you the line; intent needs a human." |
| "Real chips are huge." | "Today, 27 to 260 cells. Proofs take seconds. Recognising blocks at scale is the open problem." |

## Don't say

- "First to decompile chips" (Sisco PLDI 2023 and Subramanyan 2014 came first; name them yourself).
- "Trojans are everywhere."
- That bASIC reads manufactured chips. It reads design files.

## Prep checklist

- [ ] `flythrough.mp4` plays offline, cued to the first 3 s
- [ ] `npm run mc-assets` run once; web served with `npm run dev` (real block textures in the Minecraft page fallback)
- [ ] Showcase world loaded; `/function decap:tour/…` lands on the trojan annex
- [ ] Redstone copies built and settled (planted 78 + clean); `/tick sprint` used to speed up settling
- [ ] Judge's chip steered to `tt_um_emmyxu_obstacle_detection` (fallback: `tt_um_kb2ghz_xalu`, `tt_um_dpmu`); chip warmed
- [ ] Witness: no held-out number (08:33). Drill rehearsed on the demo chip; one Explain card ready
