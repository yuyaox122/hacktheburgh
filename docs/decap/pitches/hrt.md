# HRT pitch: "Best use of data for predictions & decision making"

**Prize:** 1st PS5; runners-up mechanical keyboards. Data can be anything, not just financial.

Numbers and honesty rules: [`../PITCH.md`](../PITCH.md). If this file disagrees with it, `PITCH.md` wins.
Status: 8 Nov 2026, 00:50. On stage and on slides the product is **bASIC**; code and folder names (`decap/`, the `decap` CLI, API paths) stay as they are.

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
- **It's the only pitch where the AI is the prediction:** Witness agents *predict* what a region of a chip does, and Yosys is the ground truth that scores every prediction. That is a real predict → verify → score loop, run on held-out chips.
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
| 55–75 s | **The AI predicts, the proof scores it** | Witness drawer (`A`) on a region: the agent's claim, then proven or refuted with a counterexample | "Here AI agents make predictions about what a region does. Every prediction is checked by a formal proof, so we get a ground-truth score for every single one. On chips the agents had never seen: ___ of ___ proven on the first draft." *(Ian's number, see below)* |
| 75–85 s | **The decision, physical** | Minecraft: flip the levers to the trigger value. Planted lamp lit, clean lamp dark | "Same proven circuit, in redstone. Lamp on: reject. Lamp off: ship." |
| 85–90 s | **Close** | | "A decision you can't check is a bet. **This one's proven.**" |

**If Ian's held-out number isn't in by pitch time:** cut the 55–75 s beat to 10 s. Say "AI agents propose explanations and a proof scores every one; the full run is overnight", and give the time to the model card.

**Minecraft honesty:** the redstone copy is pre-planted with **78** until Yuyao's live "judge's number in Minecraft" is rehearsed. Say so.

## The prediction framing, honestly

| HRT concept | bASIC | Honest caveat |
|---|---|---|
| Raw data | Gate-level netlists of real chips, names stripped | Small chips: 27–260 logic cells |
| Features | Recognised blocks (adders, counters, comparators), guessed from random input samples | Templates + exact logic, not learned |
| Signal | Rare-signal flag: a condition that fires on very few inputs | The easy case; multi-cycle triggers aren't caught |
| Out-of-sample check | Yosys proves or rejects every guess. It rejected a guess on 5 chips; 4 then proved as plain logic | |
| Model predictions | Witness agents propose one-line claims about a region | DeepSeek; numbers pending (Ian) |
| Ground truth | Formal proof: every input, every state | Says nothing about timing or the factory |
| Backtest discipline | Frozen split, both runs reported, failures shown in red | |
| Decision | Proven + no flag → ship; flag → a human reads one line | Intent (lock vs backdoor) still needs a human |

## Ian's Witness number (the HRT headline if it lands)

Ian is running the Witness bench with DeepSeek.
- Dev: 9 chips, running now.
- Held-out: after the **02:30 freeze**, via `overnight_bench.sh`. It checks the freeze hash and aborts on any mismatch, which is exactly the anti-overfitting story HRT likes.
- Smoke test: 1 chip, 2 regions, both proven on the first draft, in 10 s.

Ask Ian for these three numbers:
1. **First-draft proven rate** (the prediction accuracy)
2. **Proven after refinement** (max 2 refinements)
3. **Coverage** (share of the region's cells explained)

**Never quote:**
- the dev number as a held-out result;
- the old `cli:sonnet` 80% (it errored on 19 of 27 regions).

## Likely questions

| They'll ask | We answer |
|---|---|
| "Where's the model?" | "In the decompiler, deliberately none. It guesses from samples and a proof decides, so a wrong guess can't get through. The learned part is the agents, and we score every one of their predictions against a proof." |
| "Is 3 out of 16 false positives good?" | "It's what a blind check can do without the author's intent. One of the three is a real combination lock, `input == 118`. With the author's source, the proof settles it: any difference fails with a counterexample." |
| "Is your test set really held out?" | "Frozen before tuning; we tuned on 9 dev chips. We report the first scored run as well as the current one. The agent bench is hash-locked to the freeze and refuses to run if anything changed." |
| "Does it scale?" | "Proofs take seconds on these sizes; the slowest whole chip took 73 s. Recognising blocks at millions of gates is the open problem." |
| "Why does this matter?" | "Hardware can't be patched. Intel took a $475 million charge for one division bug in 1994. The cost of a wrong 'ship' is permanent." |

## Don't say

- "Machine learning model" about the decompiler.
- "Real-time" (proofs take seconds to a minute).
- "Predicts trojans in the wild." We plant the trigger ourselves, and we say so.

## Prep checklist

- [ ] Results wall loads from cached data, numbers readable from 2 m away
- [ ] Judge's chip warmed; planting returns within 15 s
- [ ] Witness drawer (`A`) works on the chosen chip; a refuted claim with a counterexample is ready as a backup
- [ ] Ian's three numbers in this file, or the beat shortened
- [ ] Redstone planted 78 + clean built and settled
