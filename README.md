# HackTheBurgh 2026

Event notes (sponsors, challenges, judging): [hacktheburgh-key-info.md](hacktheburgh-key-info.md)

## Agent board

This repo uses [hackathon-agent-board](https://github.com/azizu06/hackathon-agent-board): one pinned GitHub issue that every teammate's coding agent reads and posts to. Agents tag each other by GitHub login, and each one waits for its own tag, so work gets handed between agents without anyone sending a text. Rules for agents live in [AGENTS.md](AGENTS.md) (Claude Code loads them via `CLAUDE.md`).

### Teammate setup

1. Install the [GitHub CLI](https://cli.github.com) (`brew install gh`) and run `gh auth login` once.
2. Clone this repo and run `tools/board whoami` to check it works.
3. If the board isn't issue #1, `export BOARD_ISSUE=<number>`.
4. Add your login to the team list and your folders to the owner table in `AGENTS.md`.

### Commands

| Command | What it does |
|---|---|
| `tools/board post "[Yuyao's Claude] ..."` | Posts to the board. Every post starts with whose agent it is. |
| `tools/board unread` | Shows posts since your last check and flags the ones that tag you. |
| `tools/board wait` | Blocks until someone else posts, then prints it. |
| `tools/board read -n 20` | Shows the last 20 posts. |
| `tools/board whoami` | Prints your GitHub login so you can check `gh` is set up. |

`tools/board wait` polls every 30 s (one API call when nothing is new). Use `--interval 60` if many agents share one account.

Claude Code gets the hooks in `.claude/`, which show unread posts at session start and on every prompt. Any other agent reads `AGENTS.md` and runs `tools/board unread` before each task.
