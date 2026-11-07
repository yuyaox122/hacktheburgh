# Decap research notes (Sat 7 Nov 2026)

Produced by Yuyao's Claude with multi-agent workflows. Sources are linked inside each file. Vendor and startup numbers are self-reported unless stated.

| File | What it answers |
|---|---|
| [witness-plan.md](witness-plan.md) | **The plan.** "Decap Witness: chip agents that can't bluff". Agents dropped on a region of a real chip make short claims that only show once Yosys proves them. The judge types their own claim. Build plan, gates, cut order. |
| [prior-art-3d-agents.md](prior-art-3d-agents.md) | Does anything combine a 3D chip view + live editing + live simulation + agents aimed at chip regions? No. Closest is Tiny Tapeout Explorer (3D + live sim, no editing/AI). Region-targeted agents are the rare part. |
| [pain-points.md](pain-points.md) | 24 chip-workflow pain points, the tools for each, and whether each is solved. Waveform debugging deep dive. 5% first-silicon success in 2026 (Siemens/Wilson). |
| [angle-tournament.md](angle-tournament.md) | 33 pitch angles for Decap scored by simulated sponsor judges and red-teamed. No clear winner; best ~83 (G-R). |
| [scripts/gdsmatch.py](scripts/gdsmatch.py) | Checks that netlist instance names appear as GDS property 61 in TT08 GDS files, so netlist cells can be placed in 3D. |

**gdsmatch result (27 TT08 chips, GDS in `decap/data/gds/`, gitignored):** on 24 chips every logic cell's netlist name is found in the GDS. One Wokwi chip (`tt_um_wokwi_407306064811090945`) matches 84 of 124, probably escaped names. The two `micro_tiles` chips contain an unpublished hard macro and can't be read. So `touched.cells` → 3D positions works without DEF.
