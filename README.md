# bASIC

**We built an agentic IDE for ASIC chip design. It shows you exactly what a chip is doing, gate by gate, even in Minecraft.**

**Live:** <https://basic-asic.vercel.app> · Built at HackTheBurgh 2026 (7–8 Nov) by Yuyao Xie, Ian Zhang and Alex De Vrieze.

> You can't review what you can't read.

## The problem

Every chip in bASIC is a real Tiny Tapeout 08 chip, rebuilt from its public layout. Its logic is tens to hundreds of gates packed into a tile about 160 × 110 micrometres. The file it was manufactured from (the gate-level netlist) has every internal name stripped, so nobody can read it.

That matters because hardware is forever. Once a chip is made, you can't patch a bug or a hidden trigger. It has to be right the first time.

## The demo

### 1. Explore a real chip

The chip in 3D from its real layout: cells, four metal layers and vias. Click any gate to see what it does, its live pin values and the line of code it belongs to. Press Run and watch the wires that carry a 1 light up.

### 2. Explain: read it back as code

bASIC turns the stripped netlist back into short Verilog a person can review, then proves with Yosys that the code is exactly the same circuit as the gates. No AI is involved in the decompiler or the proof. Plant a hidden trigger, such as `out |= (ui_in == 137)`, and it comes back as a line of code nobody wrote.

### 3. The agent layer

AI agents (*Witness*) work on the chip like a teammate in an IDE. They get three things:

- **A verification layer.** The agent proposes and Yosys decides. Every claim comes back proven, refuted with the input that breaks it, or clearly labelled "tested only".
- **Access to the Verilog.** Explain any block in one proven sentence, suggest edits, or edit the code yourself. Every edit is checked against the real gates: same circuit, changed, or broken.
- **A formal checker.** Ask "is anything hidden?", "audit this chip" or "red team drill" in the terminal. The answers rest on equivalence proofs, not on the model's word.

IDE agents (Claude Code, JetBrains, Cursor) get the same tools over MCP.

### 4. Verify, then the results

**Verify** runs the 4-bit CLA chip on its real layout against 1,000 random inputs at once. It then swaps in a "rev B" with one gate changed and traces the difference back to that cell.

**Results** covers every chip in the benchmark. The dev/test split was frozen before any tuning ([details and caveats](basic/README.md)).

| | Held-out test (18 chips) | Dev (9 chips) |
|---|---|---|
| Proven equal by Yosys | **15** (+1 tested only, 2 unreadable) | 9 |
| Planted trigger found blind | **16 of 16** | 9 of 9 |
| False alarms on clean copies | 3 of 16 | 4 of 9 |

### 5. Minecraft

Because it's pretty cool. bASIC compiles a chip, or any small Verilog, into real redstone: repeaters, comparators and dust, no command blocks. Every build is checked against the netlist in a vanilla server. The chip's real layout can also be walked through as a world, with levers for inputs and lamps for outputs.

## In short

You can't review what you can't read, and nobody can read silicon that is micrometres wide. bASIC reads it back, proves what it read, and lets you and your agents work on it like code.

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
- **Deploy to Vercel:** see [deploy/vercel/README.md](deploy/vercel/README.md).

Checks: `.venv/bin/pytest -q` (agent tests; set `BASIC_REAL_CORE_ROOT=$PWD` to include the real-core ones), `.venv/bin/python -m basic.bench.claims_check`, `cd web && npm run build && npm run check-data`.

## Working on this repo

[AGENTS.md](AGENTS.md) has the team rules: who owns which folder, the branch and PR workflow, and the agent message board. Coding agents load it through `CLAUDE.md`. The board is one pinned GitHub issue that every teammate's agent reads and posts to through `tools/board` (`post`, `unread`, `wait`, `read -n 20`, `whoami`). It uses [hackathon-agent-board](https://github.com/azizu06/hackathon-agent-board).

Data rule: the Jane Street warm-up chip has no licence. It is fetched locally and never committed or hosted.
