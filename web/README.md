# bASIC web

The front end of bASIC: Vite + TypeScript + three.js 0.160. It reads static JSON from `public/data/` first, then the FastAPI backend (`/api`, proxied to `127.0.0.1:8000`), then a made-up dev fixture (shown with an amber "Sample data" banner).

## Run it

```sh
cd web
npm install
npm run stage-data   # every TT08 chip with a GDS -> public/data/designs/<chip>.json (Stage chip picker); SKY130 Liberty -> lib.json
                     # (npm run stage-data:warmup = the old warm-up DEF, local only: no licence to publish)
npm run sync-data    # copy ../decap/out/bench.json and ../decap/out/chips/*.json into public/data/ (when the backend has made them)
npm run dev          # http://localhost:5173
```

The live features (planting, Fix, Compare, the guide, Yosys reviews of edits) need the core API. From the repo root, with the root `.venv`:

```sh
.venv/bin/uvicorn decap.api.main:app --port 8000
```

Other scripts:

| Script | What it does |
|---|---|
| `npm run build` | type-check (`tsc --noEmit`) and build into `dist/` |
| `npm run golden` | the P0 golden check: the browser simulator vs `iverilog` on ≥1,000 random cycles of the warm-up (and every TT08 netlist in `../decap/data/tt08`, reported) |
| `npm run check-data` | headless checks: the warm-up's 79 logic cells inside the die on legal sites, its cell counts, every cell pickable by a raycast, fixtures follow CONTRACTS.md |

`public/data/` and `dist/` are gitignored: they contain data derived from the Jane Street warm-up, which has no licence. Never commit them.

## The views

Routes and element ids kept their old names when the pages were renamed.

- **Explore** (`#/stage`, key `1`): a real TT08 chip in 3D from its GDS (chip picker; cells, met1–met4 routing, vias, power straps). Click a cell for a plain-English inspector (function, live pin values, truth table, its line of decompiled code); click a pin label for its fan-in/fan-out cone. The bottom bar is the gate-level simulator: Step (`.`), Run (`Space`), Reset (`R`), toggle inputs. Wires carrying a 1 glow. `X` explodes the layers.
- **Results** (`#/wall`, key `2`): one tile per benchmark chip from `bench.json` (green proven, amber tested only, red failed). The headline counts only the held-out test set; the dev set is shown separately.
- **Decompile** (`#/chip/<name>`, key `3`): the decompiled Verilog (blocks marked in the gutter) beside the author's source, the verdict and proof detail, coverage, and the stripped netlist as a 2D graph. **Find a trigger**: enter 0–255, `POST /api/plant`, see the flagged line, condition and rate, and the cone. **Fix**: `POST /api/fix`, removed cells and the verdict. **Build in Minecraft** sends the code on screen to the Minecraft page.
- **Compare** (`#/compare`, key `4`): paste or upload Verilog or a netlist, `POST /api/compare`, see the nearest real chips and why they match.
- **Minecraft** (`#/minecraft`, key `5`): builds Verilog as real redstone through `minecraft/redstone/bridge.py` (Vite proxies `/redstone` to `:8002`). See `../minecraft/redstone/README.md`. For real Minecraft textures run `npm run mc-assets` (copies them from your local client jar into gitignored `public/mc/`); they load under `npm run dev` only and are stripped from builds, so the public site stays flat-colour.
- **Guide** (`G`): a chat drawer, `POST /api/chat` with a compact context of what is on screen; it lights up the cells and lines it mentions. Without an LLM it answers 503 and says so.
- **Verify** (`#/verify`, key `V`): the 4-bit CLA on its real layout: 1,000 random vectors at once (bit-parallel), then "rev B" with one gate changed, traced back to the cell. `#/verify/auto` plays both steps.
- **Witness agent** (`A`): Ian's agent drawer (Explain / Suggest edit / Comment on code lines, "Explain block" in the Explore inspector). Vite proxies `/witness` to `WITNESS_API`; see `../agent/README.md`.
- **Presenter mode** (`D`): a strip that follows the 90-second script (`docs/archive/pitch/PITCH.md` §6). `→` / `PgDn` steps through it. `⌘K` opens the command palette; **Larger text** is for a projector.

## Code map

```
src/
  main.ts          router (#/stage, #/wall, #/chip/<id>, #/compare, #/minecraft, #/verify), top bar, keyboard shortcuts
  api/             every request the app makes
    http.ts          the one fetch helper: JSON, Server-Sent Events, errors as sentences ("start it with ...")
    core.ts          static data first (public/data), then the core API (/api), then the dev fixture
    witness.ts       the Witness agent (/witness) and the core's review-edit route
    redstone.ts      the Minecraft bridge (/redstone)
    types.ts         CONTRACTS.md shapes
  pages/           one class per page: el, then mount/unmount or load, context() for the guide, commands() for ⌘K
    stage/           Explore: stagePage.ts, plus inspector.ts (right pane HTML), netlist.ts (fan-in/out),
                     waveform.ts (logic analyser), inputs.ts (custom inputs)
    wallPage.ts  chipPage.ts  comparePage.ts  minecraftPage.ts  verifyPage.ts
  ui/              shared chrome: dom.ts (helpers, Verilog highlighting, downloads), icons.ts, state.ts (cross-page
                   hooks), palette.ts (⌘K), guide.ts (Ask drawer), demo.ts (presenter mode), graph.ts (netlist graph)
  agent/           the Witness agent (Ian's): panel.ts + terminal.ts (drawer), codeAgent.ts + ideas.ts + ideaEdits.ts
                   (the code pane's toolbar, threads and edits), editor.ts (CodeMirror), verilogIO.ts, stageAgent.ts
                   (3D hover bar), agent.ts (runs), store.ts (saved per browser), words.ts (verdicts in words and pills)
  sim/             expr.ts (Liberty functions), sim.ts (levelised simulator), verify.ts (bit-parallel engine behind
                   Verify), verilog.ts (netlist reader for the golden check)
  three/           stage3d.ts + stageScene.ts (the chip in 3D), geometry.ts, redstone3d.ts + mcmodels.ts (Minecraft)
  lib/             cells.ts (plain-English cell descriptions and categories), kinds.ts (block kinds: words, colours),
                   md.ts (safe Markdown for agent text)
  styles/          index.css imports, in cascade order: tokens.css (every colour, size, radius, shadow), base.css,
                   shell.css (top bar, drawers, palette, presenter), components.css (buttons, pills, panes, code, page
                   header), agent.css, pages/*.css (one per page)
  fixtures/        a made-up chip and bench for development (not real chip data)
```

Colours live only in `styles/tokens.css`. Canvases and the 3D views read them at runtime with `css("--name")`, so a change there reaches everything. There is one button (`.btn`, with `.primary`, `.plain`, `.sm`, `.lg`, `.icon`).

Python ingest lives in `../decap/ingest/` (`liberty.py`, `ingest_gds.py`, and `ingest_def.py` for the warm-up).
