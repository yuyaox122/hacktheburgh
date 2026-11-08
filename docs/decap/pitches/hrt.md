# HRT pitch: "Best use of data for predictions & decision making"

**Prize:** 1st PS5; runners-up mechanical keyboards. Data can be anything, not just financial.

Numbers and honesty rules: [`../PITCH.md`](../PITCH.md). If this file disagrees with it, `PITCH.md` wins.
Status: 8 Nov 2026, 09:00 (synced with main through #104; the held-out Witness bench is running at `bench-freeze` e7708d6). On stage and on slides the product is **bASIC**; code and folder names (`decap/`, the `decap` CLI, API paths) stay as they are.

## What the judges want

From their slide:
- **Massive scale:** ingest data.
- **Diverse inputs:** combine sources into predictive features.
- **Real-time ML:** statistical models that turn raw signals into **automated decisions**.

HRT are quant traders and researchers. They will judge on:
- **Out-of-sample evaluation:** is the test set honest?
- **Error rates, not anecdotes:** precision, false positives.
- **Whether a prediction actually drives a decision.**

## How this pitch is different from the others

- **It's framed as a decision with error bars:** "ship or reject this chip block". The 3D chip and the redstone are evidence, not the point.
- **It leads with the numbers.** Results wall first, presented as a model card.
- **It's the only pitch where the AI is the prediction:** Witness agents *predict* what a region of a chip does, and Yosys is the ground truth that scores every prediction. That is a real predict → verify → score loop. Its held-out score comes from the frozen run (`witness-summary.json`); until it lands, show it live.
- **Minecraft is short:** lamp lit = reject, lamp dark = ship. 10 s, not 20.
- **Be upfront that there's no trained model in the decompiler.** HRT will respect "we can't be wrong, and here's how we measure it" over a fake ML claim.

## The hook (0–15 s)

> "Every chip block a company buys forces a yes-or-no decision: ship it, or reject it. Today that decision is made blind, because nobody can read what's inside. We turned it into a decision with measured error rates on data we never tuned on, and a mathematical proof behind every 'ship'."

## The script (90 s)

Presenter mode (`D`), but click the steps **out of order**: Results first.

| Time | Beat | On screen | Say |
|---|---|---|---|
| 0–15 s | **Hook** | Results wall (Presenter step 3) | The hook |
| 15–35 s | **The model card** | Results wall: green/amber/red tiles; point at the numbers | "Held-out test set: 18 real chips, split frozen before we tuned anything. 15 proven exactly right. Planted triggers: caught 16 of 16. False positives: 3 of 16 clean chips. And we keep our first run on the board too, because that's how you avoid fooling yourself." |
| 35–55 s | **One decision, live** | Find (step 5) on the judge's chip with the judge's N: the flagged line, its condition and its rate | "You picked a number. The signal: a condition that fires on 1 input in 256, reaching exactly one output pin. That's the 'reject' call, and the evidence is one readable line instead of 260 gates." |
| 55–75 s | **The AI predicts, the proof scores it** | Witness drawer (`A`) on a region: the agent's claim, then proven or refuted with a counterexample | "Here AI agents make predictions about what a region does. Every prediction is checked by a formal proof, so every one gets a ground-truth score. On ___ chips the agents had never seen: ___% proven on the first draft." *(Fill from `witness-summary.json`. If it hasn't landed: one live proven/refuted Explain card, or the drill once it's been seen working, #104.)* |
| 75–85 s | **The decision, physical** | Minecraft: flip the levers to the trigger value. Planted lamp lit, clean lamp dark | "Same proven circuit, in redstone. Lamp on: reject. Lamp off: ship." |
| 85–90 s | **Close** | | "A decision you can't check is a bet. **This one's proven.**" |

**If the held-out number isn't in by pitch time:** cut this beat to 10 s. Say "agents propose, a proof scores every claim", show one live proven or refuted card, and give the time to the model card.

**Minecraft honesty:** the redstone copy is pre-planted with **78** until Yuyao's live "judge's number in Minecraft" is rehearsed. Say so.

**If the Minecraft client fails:** the website's Minecraft page (`#/minecraft`) shows the same build in 3D with real Minecraft block models (repeaters, comparators, dust, levers, lamps), and the levers and lamps toggle live. This needs `npm run mc-assets` once on the demo laptop, and the page served from `npm run dev`: the textures are loaded in dev only and stripped from builds (Mojang's assets stay off the public site, #72), so the public site shows flat colours.

## The prediction framing, honestly

| HRT concept | bASIC | Honest caveat |
|---|---|---|
| Raw data | Gate-level netlists of real chips, names stripped | Small chips: 27–260 logic cells |
| Features | Recognised blocks (adders, counters, comparators), guessed from random input samples | Templates + exact logic, not learned |
| Signal | Rare-signal flag: a condition that fires on very few inputs | The easy case; multi-cycle triggers aren't caught |
| Out-of-sample check | Yosys proves or rejects every guess. It rejected a guess on 5 chips; 4 then proved as plain logic | |
| Model predictions | Witness agents propose one-line claims about a region | Held-out run (cli:sonnet) in progress; the live demo runs on DeepSeek, and the proof decides either way |
| Ground truth | Formal proof: every input, every state | Says nothing about timing or the factory |
| Backtest discipline | Frozen split, both runs reported, failures shown in red | |
| Decision | Proven + no flag → ship; flag → a human reads one line | Intent (lock vs backdoor) still needs a human |

## Witness: the held-out slot, and what to show live

The 08:33 "no held-out stat" post was **retracted** (board, 08:44). The freeze is on: tag `bench-freeze` at
`e7708d6`, freeze hash `e83f4114…`, model `cli:sonnet`, 15 held-out chips, running on Ian's Mac since about 08:50.

- **Keep the held-out slot.** Fill it from `decap/agent/out/witness-summary.json` when the run lands: first-draft
  proven rate, proven after refinement, coverage, model. Line to use: *"On ___ chips the agents had never seen,
  ___% of regions were explained in a claim Yosys proved."*
- **If the run hasn't finished by pitch time:** say "agents propose, a proof scores every claim" and show one live
  proven or refuted example instead. Don't quote a partial run as the result.
- Any number from the 9 dev chips is labelled **dev**, never held-out.

What the agent does live, on main (pick one for the demo):
- **Explain** (hover a line, or a cell in 3D): a plain-English explanation in about 3 to 6 s, read from the
  decompiled code, which Yosys has already proven equal to the gates. If the agent also finds a short formula
  Yosys can prove, that replaces it with a green "proven" card. Dev sweep: 93 of 98 blocks on 9 dev chips got an
  explanation, median 2.6 s.
- **Red-team drill** (Agent drawer): it plants a trigger at a secret random value, then hunts it blind. Caught =
  the proof against the author's source fails and a flagged condition names the value. Yuyao's review found it said
  "missed" on real chips (#95); fixed in #104 and re-run on the real core, 4/4 caught. **Use it live only after
  Yuyao or Alex has seen it work once.** Safe live beats in any case: Explain, and Plant/Find on Decompile.
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

**The HRT angle on the model:** the dev bench is a real model comparison. DeepSeek's first drafts were weak (65% of
parseable first drafts wrong, 37% unparseable, dev); Sonnet's were better. The proof is what told them apart, so
the checker doubles as a model evaluator. Label all of this **dev**.

## Likely questions

| They'll ask | We answer |
|---|---|
| "Where's the model?" | "In the decompiler, deliberately none. It guesses from samples and a proof decides, so a wrong guess can't get through. The learned part is the agents, and we score every one of their predictions against a proof." |
| "Is 3 out of 16 false positives good?" | "It's what a blind check can do without the author's intent. One of the three is a real combination lock, `input == 118`. With the author's source, the proof settles it: any difference fails with a counterexample." |
| "Is your test set really held out?" | "Frozen before tuning; we tuned on 9 dev chips. We report the first scored run as well as the current one. The agent bench is hash-locked to the freeze and refuses to run if anything changed." |
| "Does it scale?" | "Proofs take seconds on these sizes; the slowest whole chip took 73 s. Recognising blocks at millions of gates is the open problem." |
| "Couldn't an LLM just read the netlist?" | "On small chips it often can. We ran Claude Opus on the stripped gates and Yosys checked each answer. It's sometimes confidently wrong, and it can't tell you when. bASIC is never wrong but marked proven, and it takes about 0.4 s a chip against 30-70 s for the LLM." *(Partial run so far: 9 proven, 1 wrong with a counterexample, several with no usable answer. Use the final numbers from #87 once merged.)* |
| "Why does this matter?" | "Hardware can't be patched. Intel took a $475 million charge for one division bug in 1994. The cost of a wrong 'ship' is permanent." |

## Don't say

- "Machine learning model" about the decompiler.
- "LLMs can't do this." They partly can; the point is that they can't tell you when they're wrong.
- "Real-time" (proofs take seconds to a minute).
- "Predicts trojans in the wild." We plant the trigger ourselves, and we say so.

## Prep checklist

- [ ] Results wall loads from cached data, numbers readable from 2 m away
- [ ] Judge's chip warmed; planting returns within 15 s
- [ ] Witness drawer (`A`) works on the chosen chip; a refuted claim with a counterexample is ready as a backup
- [ ] Ian's three numbers in this file, or the beat shortened
- [ ] Redstone planted 78 + clean built and settled
- [ ] `npm run mc-assets` run once; web served with `npm run dev` (real block textures in the Minecraft page fallback)
