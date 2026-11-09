# bASIC: View Source for silicon

A chip is manufactured from its **gate-level netlist**, not from the Verilog its author wrote, and nobody can read a netlist. bASIC can:

1. **Read it back.** Take a real chip's netlist, with every internal name stripped, and turn it back into short Verilog a person can review.
2. **Prove it.** Show with Yosys that the Verilog is exactly the same circuit as the gates. No AI is involved in the decompiler or the proof.
3. **Catch what doesn't belong.** Plant a hidden trigger, for example `out |= (ui_in == 137)`, and it comes back as a line of code nobody wrote.
4. **Explain it.** AI agents (*Witness*) explain any region of the chip in one sentence. A sentence only counts once Yosys proves it.

Built at HackTheBurgh 2026 (7–8 Nov) by Yuyao Xie, Ian Zhang and Alex. Live site: <https://basic-asic.vercel.app>.

## Results

Measured on Tiny Tapeout 08 chips. The dev/test split was frozen before any tuning. Details and caveats: [basic/README.md](basic/README.md).

| | Held-out test (18 chips) | Dev (9 chips) |
|---|---|---|
| Proven equal by Yosys | **15** (+1 tested only, 2 unreadable) | 9 |
| Planted trigger found blind | **16 of 16** | 9 of 9 |
| False alarms on clean copies | 3 of 16 | 4 of 9 |

## What's where

```
basic/        The core (Python): ingest, decompiler, Yosys proofs, find/fix triggers, benchmark, FastAPI
agent/        Witness: AI agents whose claims count only once proven; the `basic` CLI and MCP server
web/          The web app (Vite + TypeScript + three.js): 3D chip, results, decompile, compare, Minecraft
minecraft/    Chips in Minecraft: real redstone builds (redstone/) and the chip as a world (world/)
docs/         How chips work, plus the hackathon archive (pitches, plans, prototype)
tools/board   The team's agent message board (a pinned GitHub issue)
```

| Start here | For |
|---|---|
| [docs/how-chips-work.md](docs/how-chips-work.md) | New to chips? A plain-English guide from Verilog to silicon. |
| [basic/README.md](basic/README.md) | How the decompiler and proofs work, and the full results. |
| [basic/CONTRACTS.md](basic/CONTRACTS.md) | The JSON shapes and API routes every part shares. |
| [agent/README.md](agent/README.md) | Witness, the CLI and connecting an IDE agent over MCP. |
| [web/README.md](web/README.md) | The web app's pages, scripts and code map. |
| [minecraft/README.md](minecraft/README.md) | Building chips in Minecraft. |

## Run it

You need `gh` (logged in), `yosys`, `iverilog`, [`uv`](https://docs.astral.sh/uv/), Python 3.12+ and Node 22. Everything runs from the repo root, and one Python environment covers the core, the agent and Minecraft.

```bash
uv venv -p 3.12 .venv && uv pip install -e '.[dev]'
.venv/bin/python -m basic.ingest.fetch        # chips, their authors' sources and the SKY130 cells → basic/data/
.venv/bin/python -m basic.bench.run           # decompile and prove every chip → basic/out/ (about 2 min)

cd web && npm install && npm run stage-data && npm run sync-data
npm run dev:all                               # API on :8000 and the app on http://localhost:5173
```

Optional extras:

- **Guide chat:** set `ANTHROPIC_API_KEY` in `basic/.env` (see `basic/.env.example`). Without a key the chat says it's offline, and everything else works.
- **Witness agent:** `.venv/bin/basic serve --port 8791`, then press `A` in the app. Model keys and options: [agent/README.md](agent/README.md).
- **Minecraft:** see [minecraft/README.md](minecraft/README.md).

Checks: `.venv/bin/pytest -q` (agent tests; set `BASIC_REAL_CORE_ROOT=$PWD` to include the real-core ones), `.venv/bin/python -m basic.bench.claims_check`, `cd web && npm run build && npm run check-data`.

## Working on this repo

[AGENTS.md](AGENTS.md) has the team rules: who owns which folder, the branch and PR workflow, and the agent message board. Coding agents load it through `CLAUDE.md`. The board is one pinned GitHub issue that every teammate's agent reads and posts to through `tools/board` (`post`, `unread`, `wait`, `read -n 20`, `whoami`). It uses [hackathon-agent-board](https://github.com/azizu06/hackathon-agent-board).

Data rule: the Jane Street warm-up chip has no licence. It is fetched locally and never committed or hosted.
