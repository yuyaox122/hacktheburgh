# GlassDie: every open chip, made of glass

> **This is a variant of Yuyao's Decap (#4).** It keeps Decap's core (explore a real Tiny Tapeout chip in 3D, click to understand its logic) and was developed from it by Alex's agent with a 3-model council (Claude Sonnet 5.5, Claude Opus 5.5, Grok 4.7). Decap is the original idea; GlassDie is one way to push it to a single track.

**Track:** G-Research ("make the invisible visible"). Also enter MetaView (best overall).
**Council final:** **90.7** (Sonnet 89 · Opus 92 · Grok 91). The **only idea of ~70 to clear the bar** (mean ≥90, no judge below 85).
**Lineage:** Decap as written (#4: 81.0 for G-Research) → council development "Silicon, live" (85.3) → GlassDie v1 85.0 → v2 87.7 → v3 88.3 → v4 89.0 → **v5 90.7**. Decap v2 (Yuyao's agent, 76.0) contributed the idea of proving a change correct; the council chose the live iverilog-matched simulation as that proof.

### What changed from Decap
| Decap (#4) | GlassDie |
|---|---|
| One chip at a time, cell names read from the layout | ~375 chips: structure recovered **blind** (names stripped) and scored against the stripped names |
| An LLM explains each cell | No per-cell LLM. Cell meaning comes from the SKY130 Liberty files; an LLM only labels families from docs text |
| Click-to-explain plus an optional simulation | Live gate-level simulation verified against iverilog is the centrepiece |
| Minecraft export, redstone | Cut, to protect the centrepiece |
| No data-scale result | A structural galaxy plus held-out family accuracy vs baselines |

> Hundreds of real chips, built by students and hobbyists, look like coloured rectangles. GlassDie discovers what they are from their structure alone, proves it on chips it has never seen, and lets you watch the defining structure run inside the silicon.

## Why it wins (in the judges' words)
- Opus (92): "The highest score I've given this session. I'd bet on it placing 1st in G-Research, and it's a strong MetaView contender."
- Grok (91): "A bet on 1st if that DEF exists by Saturday evening."
- Sonnet (89): "My highest score for any G-Research idea… the safest of the G-Research entries because the ladder protects the hero."

The idea answers every objection raised against Decap:

| Objection | Answer |
|---|---|
| Reading logic off cell names is lookup, not discovery | Names are stripped; structure is recovered blind and scored against what was stripped. |
| Is it data? | A structural embedding of hundreds of real chips, with a held-out accuracy number. |
| Too much scope | A strict priority ladder; the live-silicon hero is never cut. |

## The demo: one zoom, one argument (90 seconds)
1. **Galaxy (0–30s).** TT06 + TT08 chips float as a 3D galaxy, embedded by **logic-cell mix + recovered motif counts** (names stripped, filler cells dropped). Families emerge that nobody labelled. On screen:
   - held-out macro-accuracy **band** (50 precomputed splits) for GlassDie vs majority-class vs cell-mix-only;
   - the **40-chip label-agreement** figure right beside it (Grok: the band is split noise only);
   - a judge picks a seed and the dot lands inside the band.
   
   A judge picks a chip from the ~20-chip validated list: *"counter/clock family: 3 counters, 1 LFSR"*, then its real title is revealed.
2. **Inside (30–55s).** Fly into that chip with every name stripped. Its counters and LFSR taps light up as they are recovered. Shuttle-wide precision/recall is shown, with "ground truth covers X% of chips / Y% of flops". *"This XOR-tap flop chain is why it lives in this family."*
3. **Alive (55–90s).** Real routed wires in 3D. A Liberty-built event-driven simulator runs the real netlist, with the **waveform matching iverilog** on screen. Toggle the clock: the recovered structure runs along glowing wires. Close on one honest measured fact: *"Clock tree removed, this LFSR is N% of the chip's switching."*

## How it works
```
TinyTapeout/tinytapeout-06, -08 shuttle repos  (verified live 7 Nov 2026)
  projects/<tt_um_*>/  →  gate-level netlist (.v) · GDS · LEF · info.yaml · docs/info.md · stats/
        │
        ├─► strip all instance/net names ─► motif recognisers (counters, LFSR/shift registers)
        │        └─ surviving RTL-derived names (cnt_reg, lfsr_q…) = automatic ground truth → P/R + coverage
        │
        ├─► logic-cell mix (drop fill/tap/decap/diode/antenna) + motif counts ─► UMAP 3D + HDBSCAN
        │
        └─► docs text ONLY (never the netlist) ─► small LLM, explicit "unclear" ─► family labels
                 frozen taxonomy: video · analog · arithmetic · counter/clock · CPU · other (<8 chips merged)
                 validated on a 40-chip hand-checked sample
                         ▼
             50 precomputed splits → macro-accuracy band vs majority + cell-mix-only baselines

Demo chip (<2,000 cells):
  pinned OpenLane (TT08-era) in local Docker ─► DEF (positions + routed wires, keeps names)
  SKY130 Liberty (function + ff groups) ─► ~300-line event-driven JS simulator ─► golden check vs iverilog
                         ▼
  three.js: SKY130 layer-stack extrusion, instanced meshes, glow + switching heat driven by simulator values
```

## Verified facts (checked live today)
- `tinytapeout-08/projects/` has **137 project folders**. Each ships the gate-level netlist, GDS, LEF, `info.yaml`, `docs/` and `stats/` (`metrics.csv`, `synthesis-stats.txt`). TT06 has the same repo layout. These are in-repo, so nothing expires.
- **Title keywords alone label only 36% of TT08** (49/135 name exactly one family), which is why labels come from the docs text.
- The per-chip **DEF is not in the repo**. Today's `tt-gds-action` uses **LibreLane 3.x**, not the OpenLane flow that built TT08, so the primary path is the **pinned OpenLane version locally in Docker**.

## Priority ladder (cut from the bottom, never P0)

| Priority | What | Done by |
|---|---|---|
| **P0** | One chip alive: DEF wires in 3D + simulator matching iverilog | **Sat 18:00** |
| P1 | Blind recovery (counters, LFSR/shift regs) on stripped netlists, P/R + coverage | Sun 03:00 |
| P2 | Galaxy + held-out band vs baselines + label agreement | Sat 22:00 |
| P3 | Surprise family/outlier that the docs don't name (locked, or dropped with the honest purity line) | Sat 18:00 |

**Cut order if behind:** P3 → judge-seed dot → TT06 (keep TT08) → motifs in the atlas (cell-mix only, said openly).

## Gates
- **Hour 0, first task:** pull the pinned OpenLane image and SKY130 PDK (several GB via `volare`) onto **two laptops**, on the fastest connection available. Start DEF regeneration for 3 candidate demo chips; keep the first one that finishes. *Both Sonnet and Opus say this is the single thing between the plan and P0.*
- **Hour 2:** DEF↔netlist join works (or the local run is under way); iverilog runs the stripped netlist; histograms parse.
- **Hour 6:** motif-vs-cell-mix gap measured on TT08. If motifs add nothing, present the galaxy as a cell-mix structure map and move person A to the hero.
- **Sat 18:00:** P0 done; surprise locked. **Sat 22:00:** held-out band computed. **Sun 03:00:** judge list validated. **Sun 08:00:** freeze, backup video, rehearse ×3. **Sun 11:30:** submit.

**Fallbacks:** a fork's Action rerun in parallel (bonus); the last resort is a netlist-generated **schematic** placement, shown visibly as a schematic and never passed off as layout.

## Team (4)

| Person | Owns |
|---|---|
| A | Atlas: histograms, batch motifs, docs labelling (one batched small-model pass, 40-chip hand check Saturday afternoon), UMAP/HDBSCAN, 50-split band |
| B | Recovery: counter + LFSR recognisers, name stripping, shuttle-wide P/R + coverage |
| C | Simulator: Liberty → event-driven sim, iverilog golden waveform |
| D | 3D: static DEF scene from hour 0, glow + heat once the simulator matches, zoom transitions |

## Cut list
Minecraft · Yosys adder swap · per-block LLM summaries · silicon-proven colouring · any-chip upload · raw-GDS connectivity extraction · TT07 · classifiers and agents.

## Comparable past winners
- **The Git Bang (Contra × Builder.io grand prize):** a three.js galaxy of 55k repos. GlassDie's galaxy carries a scored discovery and zooms into a live chip.
- **Visual6502 / Tiny Tapeout's GDS viewer:** render or animate one chip. Neither discovers structure across a collection nor scores blind recovery on held-out chips.

## Remaining risks
- The DEF path (pinned OpenLane + PDK download on venue wifi) → pull before or at hour 0 on two laptops.
- The motif gap might be small → hour-6 gate and an honest fallback.
- Label quality caps accuracy → label agreement shown beside the band.
