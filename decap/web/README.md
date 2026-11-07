# Decap web

The front end of Decap: Vite + TypeScript + three.js 0.160. It reads static JSON from `public/data/` first, then the FastAPI backend (`/api`, proxied to `127.0.0.1:8000`), then a made-up dev fixture (shown with an amber "FIXTURE" banner).

## Run it

```sh
cd decap/web
npm install
npm run stage-data   # every TT08 chip with a GDS -> public/data/designs/<chip>.json (Stage chip picker); SKY130 Liberty -> lib.json
                     # (npm run stage-data:warmup = the old warm-up DEF, local only: no licence to publish)
npm run sync-data    # copy ../out/bench.json and ../out/chips/*.json into public/data/ (when the backend has made them)
npm run dev          # http://localhost:5173
```

Other scripts:

| Script | What it does |
|---|---|
| `npm run build` | type-check (`tsc --noEmit`) and build into `dist/` |
| `npm run golden` | the P0 golden check: the browser simulator vs `iverilog` on ≥1,000 random cycles of the warm-up (and every TT08 netlist in `../data/tt08`, reported) |
| `npm run check-data` | headless checks: 79 logic cells inside the die on legal sites, SPEC §8 counts, every cell pickable by a raycast, fixtures follow CONTRACTS.md |

`public/data/` and `dist/` are gitignored: they contain data derived from the Jane Street warm-up, which has no licence. Never commit them.

## The views

- **Stage** (`#/stage`, key `1`): the warm-up chip in 3D from the real DEF (cells, met1–met4 routing, vias, power straps, package and bond wires). Click a cell for a plain-English inspector (function, live pin values, truth table, module and source line); click a source line to light its cells; click a pin label for its fan-in/fan-out cone. The bottom bar is the gate-level simulator: Step (`.`), Run (`Space`), Reset (`R`), toggle inputs, or **Shift in** two numbers (S lights up when they add to 496). Wires carrying a 1 glow. `X` explodes the layers. Credit: "Warm-up chip: Jane Street asic-puzzle-2026".
- **Proof wall** (`#/wall`, key `2`): one tile per benchmark chip from `bench.json` (green proven, amber tested only, red failed). The headline counts only the held-out test set; the dev set is shown separately.
- **Chip** (`#/chip/<name>`, key `3`): the decompiled Verilog (blocks marked in the gutter) beside the author's source, the verdict and proof detail, coverage, and the stripped netlist as a 2D left-to-right graph. Click a line to light its cells; click a cell to find its line. **Find a trigger**: enter 0–255, `POST /api/plant`, see the flagged line, condition and rate, and the cone in the graph, plus false alarms on the clean copy. **Fix**: `POST /api/fix`, removed cells and the verdict.
- **Compare** (`#/compare`, key `4`): paste or upload Verilog or a netlist, `POST /api/compare`, see the nearest real chips and why they match.
- **Guide** (`G`): a chat drawer, `POST /api/chat` with a compact context of what is on screen; it lights up the cells and lines it mentions. Without an LLM key the backend answers 503 and the drawer says so.
- **Demo** (`D`): a strip that follows the 90-second script (SPEC §9). Before the timer, pick the judge's chip and number and press **Plant now** (planting runs in the background); then `→` / `PgDn` steps through Hook, Stage, Proof wall, Read, Find, Close. `⌘K` opens the command palette; **Aa** makes text bigger for a projector.

## Code map

- `src/sim/`: `expr.ts` (Liberty function parser), `sim.ts` (levelised simulator: flip-flops latch on clock edges, async clear/preset), `verilog.ts` (netlist reader used by the golden check).
- `src/view/`: `stage3d.ts` (three.js scene, ported from `docs/decap/prototype.html`), `geometry.ts`, `describe.ts` (plain-English cell descriptions).
- `src/ui/`: one file per page, plus `graph.ts` (netlist graph), `guide.ts`, `palette.ts`, `demo.ts`.
- `src/data/`: `types.ts` (CONTRACTS.md shapes), `load.ts` (static → API → fixture).
- `src/fixtures/`: a made-up chip and bench for development (not real chip data).
- Python ingest lives in `../ingest/liberty.py` and `../ingest/ingest_def.py`.
