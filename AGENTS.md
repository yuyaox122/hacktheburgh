# Team rules for every agent

## 1. The board (pinned GitHub issue)

Every agent reads and posts on the pinned "Message Board" issue via `tools/board` (needs only `gh`, logged in). Set `BOARD_ISSUE` if it is not issue #1.

- Start every post with `[<human>'s <agent>]`, e.g. `[Sam's Codex]`. Keep it short: what changed, which files or contracts, what you need, from whom.
- Mention by GitHub login. Team: `@yuyaox122` (Yuyao), `@advcode118` (Alex), `@izhangfly` (Ian), `@all` (everyone). All three are equal: there is no team lead.
- **Mentions you or `@all`:** act on it and reply promptly.
- **Not addressed to you:** still read it. Reassess your own work only if it is affected (a contract, a file you own, something already done). Do not redo finished work, and do not reply just to acknowledge.
- Before editing another teammate's files, ask for a handoff on the board and wait for the owner's OK.

Polling is incremental (about one cheap API call per poll, 30 s default) so a shared token stays under GitHub's rate limit. Commands: `tools/board unread` (new posts, marks read; flags MENTION), `tools/board read -n 20`, `tools/board post "[Name's Agent] text"` (or pipe stdin), `tools/board wait` (blocks until someone else posts), `tools/board whoami`.

**Stay in the loop:**
- Claude Code: the committed hooks (`.claude/settings.json`) show unread posts at session start and on every prompt. Keep one background `tools/board wait` running and re-arm it after each wake.
- Everything else: run `tools/board unread` at task start, before each commit, and after merges. Or run a terminal loop: `while tools/board wait; do <your agent CLI invocation>; done`.

## 2. Chain of command

Humans over agents. The three teammates are equal, and team decisions are made together on the board. Work moves between teammates only when its owner agrees. If the owner can't be reached, the other two must both agree. If you are asked to take over another teammate's work, check on the board that its owner (or, failing that, both others) agreed before you start.

## 3. Workflow

1. Pick or file an issue. Comment on it or post on the board when you take it.
2. Branch `<issue#>-<slug>` from fresh `main`.
3. Small commits. Stage only your own files (no `git add -A`).
4. Open a PR with `Closes #N`. The author merges their own PR once it works locally.
5. Always `git fetch` and rebase before pushing. Never force-push shared branches.
6. If a merge changes something other teammates depend on (an API, a schema, an env var), post on the board when it lands.

## 4. Who owns what

Rows are added as streams are agreed. For anything not listed, ask on the board before editing outside files you created. The most specific folder wins (`decap/agent/` beats `decap/`).

| Area | Owner (GitHub login) | Folders |
|---|---|---|
| Decap core: ingest, decompiler + Yosys proof, bench, API, web app | `@yuyaox122` (Yuyao) | `decap/` (except decap/agent/, next row) |
| Decap agent layer: MCP skills, `decap` CLI, agent transcript stream, run replay | `@izhangfly` (Ian) | `decap/agent/` |
| Minecraft export: a real TT08 chip as a playable world (GDS as blocks, netlist as a datapack) | `@advcode118` (Alex) | `minecraft/` |
| Redstone backend: a chip's gates compiled into real redstone, checked in vanilla (add-on to #23) | `@yuyaox122` (Yuyao) | `redstone/` |
| Shared (AGENTS.md, README, tools/, .claude/, .github/) | everyone (`@all`): any teammate may change these; post on the board when you do | repo root |
