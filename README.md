# HackTheBurgh 2026: Decap

**Decap: View Source for silicon.** Decap reads a real chip's gate-level netlist, with every internal name stripped, back into short Verilog a person can review. It then proves with Yosys that the Verilog is exactly the same circuit, and shows a trigger planted for the judge's number as a line of code. Measured on 18 held-out Tiny Tapeout 08 chips: 15 proven, and 16 of 16 planted triggers caught (with false alarms on 3 of 16 clean chips).

## What's where

| Folder | What | Start here |
|---|---|---|
| `decap/` | The decompiler, Yosys proofs, benchmark, FastAPI backend | [decap/README.md](decap/README.md), [decap/CONTRACTS.md](decap/CONTRACTS.md) |
| `decap/web/` | The web app: Explore (3D chip), Results, Decompile, Compare, Minecraft | [decap/web/README.md](decap/web/README.md) |
| `decap/agent/` | Witness: AI agents whose claims about a chip region count only once Yosys proves them; `decap` CLI and MCP skills | [decap/agent/README.md](decap/agent/README.md) |
| `redstone/` | Compiles a chip or any small Verilog into real Minecraft redstone, checked in a vanilla server | [redstone/README.md](redstone/README.md) |
| `docs/` | The pitch and its facts, the Witness plan, event notes | [docs/decap/PITCH.md](docs/decap/PITCH.md), [docs/decap/witness-plan.md](docs/decap/witness-plan.md), [docs/event-info.md](docs/event-info.md) |
| `tools/`, `.claude/`, `.github/` | The team message board | below |

`docs/decap/prototype.html` is the original single-file prototype that the web app was ported from. It runs on the Jane Street warm-up, so don't host it publicly.

Who owns which folder, and how we work: [AGENTS.md](AGENTS.md).

## Agent board

This repo uses [hackathon-agent-board](https://github.com/azizu06/hackathon-agent-board): one pinned GitHub issue that every teammate's coding agent reads and posts to. Agents tag each other by GitHub login, and each one waits for its own tag, so work gets handed between agents without anyone sending a text. Rules for agents live in [AGENTS.md](AGENTS.md) (Claude Code loads them via `CLAUDE.md`).

**Setup:** install the [GitHub CLI](https://cli.github.com) (`brew install gh`), run `gh auth login` once, then `tools/board whoami` to check it works. If the board isn't issue #1, `export BOARD_ISSUE=<number>`.

| Command | What it does |
|---|---|
| `tools/board post "[Yuyao's Claude] ..."` | Posts to the board. Every post starts with whose agent it is. |
| `tools/board unread` | Shows posts since your last check and flags the ones that tag you. |
| `tools/board wait` | Blocks until someone else posts, then prints it. |
| `tools/board read -n 20` | Shows the last 20 posts. |
| `tools/board whoami` | Prints your GitHub login so you can check `gh` is set up. |

`tools/board wait` polls every 30 s (one API call when nothing is new). Use `--interval 60` if many agents share one account. Claude Code gets the hooks in `.claude/`, which show unread posts at session start and on every prompt.
