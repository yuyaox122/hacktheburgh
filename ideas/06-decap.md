# Decap: "Silicon, live"

**Proposed by:** Yuyao (#4) · **Track:** G-Research (+ MetaView), deliberately *not* G-R × HRT
**Council:** as written, G-Research alone **81.0** (75 / 86 / 82) → after hackathon office hours **85.3** (Sonnet 81 · Opus 89 · Grok 86)
Against the combined G-R × HRT target: 65.7 → 66.3 (no prediction half by design). The HRT variant ("Silicon Atlas", D-B) scored 71.7 and is **not recommended**.

> This is a real chip, a few thousand logic gates made through Tiny Tapeout. To you it's coloured rectangles. Flip one input and watch the signal ripple through the silicon.

## The vision
Chip layouts are millions of rectangles. KLayout and Tiny Tapeout's own viewer show geometry, never meaning. Decap turns a real, manufactured open chip into something you can understand by playing with it. It rises in 3D, every cell is clickable and explained, the hidden structure (registers, counters, carry chains) is recovered and coloured, and the hero moment is a **live gate-level simulation in the browser**: toggle the clock and watch the counter count *in the silicon*. Then alt-tab and fly over the same chip in Minecraft.

## Who it is for
Digital-design students and Tiny Tapeout hobbyists who want to see how Verilog becomes silicon. Judges: G-Research engineers ("make the invisible visible") and MetaView ("best creative project").

## The 90-second demo
1. **0–15s**: "This is a real chip." Drop it in; layers extrude and the chip rises out of the page.
2. **15–35s**: Click a cell: "D flip-flop · bit 3 of an 8-bit counter". The whole recovered counter lights up, and the colour overlay shows registers, adders and carry chains.
3. **35–65s**: **Toggle the clock.** Values ripple along the 3D wires; the counter's flip-flops tick 0001 → 0010 → 0011. A waveform panel shows our browser simulation matching iverilog on the same netlist. "That's not an animation. That's the chip's logic running."
4. **65–80s**: Click "Export to Minecraft", alt-tab, fly over the chip in-game.
5. **80–90s**: "Any open Tiny Tapeout design, understood in seconds."

## How it works
```
Tiny Tapeout shuttle repo / GDS action artefacts (per design)
  ├─ gate-level netlist   (runs/.../gl/*.v  or  final/nl / final/pnl)
  ├─ DEF placement        (cell positions + routed wire geometry, keeps instance names)
  └─ GDS                  (layer polygons for the 3D extrusion)
                ▼
SKY130 Liberty: combinational `function` attrs + sequential `ff` / next-state groups
                ▼
netlist → graph (networkx), with instance names STRIPPED before analysis
   → recover registers / counters / adders / carry chains from structure alone
   → (then compare to the original names: "we recovered counter[0..7] blind")
                ▼
~300-line event-driven simulator (JS) over the netlist   ← verified vs iverilog golden waveform
                ▼
three.js: SKY130 layer-stack extrusion, instanced meshes, raycast click, fan-in/fan-out highlight,
          wire glow driven by simulator values
                ▼
Minecraft .schem export (scale model of the demo chip only) via mcschematic / nbtlib
```

### Council build notes (important)
- **Positions and wires come from the DEF, not the GDS.** GDS cell references and metal polygons don't reliably carry instance or net names; the DEF from the same OpenLane run does (Opus, Grok).
- **Strip instance names before the graph pass.** Gate-level flop names often still read `counter[3]`, which would make "recovered register" just a label. Recover blind, then reveal that it matched (Grok).
- **Sequential cells** need the Liberty `ff` / next-state group, not just `function` (Grok).
- GitHub Actions artefacts expire, so vendor the 2–3 demo designs from the shuttle repos at hour 0.

## Data and tools
Tiny Tapeout shuttle repos (GDS, DEF, gate-level netlists) · SKY130 PDK Liberty files · `gdstk` · a DEF parser · `networkx` · iverilog (golden check) · three.js · `mcschematic` / `nbtlib`.

## 24-hour build plan (3 people)

| Hours | Work | Owner |
|---|---|---|
| **0–2** | **GO/NO-GO**: vendor 2–3 designs; confirm netlist ↔ DEF instance-name join; parse the Liberty functions | Data |
| 0–6 | three.js layer extrusion + instancing + click → cell ID | Front end |
| 2–8 | Event-driven simulator + iverilog golden comparison on the counter | Sim |
| 6–12 | Blind register/counter/adder recovery; colour overlay; fan-in/fan-out highlight | Data |
| 8–14 | Wire glow driven by simulator values; clock and input toggles; waveform panel | Front end + Sim |
| 14–18 | One-sentence LLM summary per recovered block; polish | Data |
| 18–20 | Minecraft scale export of the demo chip | Sim |
| 20–24 | Record backup video, rehearse ×3, submit by 11:30 | All |

**Cut:** "any chip" upload, raw-GDS connectivity extraction, per-cell LLM explanations, working redstone, and the Silicon Atlas / timing-prediction variant.

## Risks
- The name join fails → use DEF positions (above); worst case, map cells by position overlap.
- The 3D mesh eats the day and the simulator never drives it → build the simulator against a flat 2D view first, then swap in 3D.
- "Is recovering known logic a hidden pattern?" (Sonnet) → the blind-recovery reveal answers it.

## Comparable past winners
- **The Git Bang (grand prize):** a 3D, explorable view of a hidden structure. Same visual class, with live behaviour on top.
- MetaView's "best creative" brief fits the Minecraft moment.

## Decap v2 (Yuyao's agent, 14:40) vs "Silicon, live"
Yuyao's agent proposed v2: an agent with Tour and Investigate modes, Yosys-proven "chip surgery", and an HRT half (a calibrated block classifier deciding where to spend the simulation budget). It self-scored 66 → 76.

| Version | G-Research alone (S / O / G) | G-R × HRT (S / O / G) |
|---|---|---|
| Silicon, live | **85.3** (81 / 89 / 86) | 66.3 |
| Decap v2 | 76.0 (69 / 79 / 80) | 67.3 (62 / 70 / 70) |

All three judges say v2 is worse for the team: the scope roughly doubles, the live-simulation hero gets diluted, and the classifier has no labels (flattened Tiny Tapeout netlists lose module boundaries) and still reads as bolted on.

**Agreed merge (all three judges):** keep "Silicon, live" as the base and add **one** thing from v2: a function-preserving adder swap proven equivalent with Yosys (Sunday, after the simulation works). Drop the classifier, the Investigate agent and the behaviour-change surgery. Sonnet expects ~82–84; Opus calls the swap "the best new idea in v2".

## Team-level note
Decap is the strongest **single-track** idea the council saw (Opus 89 for G-Research). It doesn't serve the G-R × HRT combined prize, so choosing it means choosing G-Research + MetaView over the HRT prize. Fault Lines remains the pick for the combined target.
