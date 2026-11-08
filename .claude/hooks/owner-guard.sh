#!/usr/bin/env bash
# PreToolUse (Edit|Write|MultiEdit|NotebookEdit): block edits to folders another teammate owns,
# per the owner table in AGENTS.md section 4. Fails open: any setup problem allows the edit.
# Local handoff override: add the repo-relative path (or folder prefix) to .board/handoffs
# after the owner or lead approves the handoff on the board.
cd "${CLAUDE_PROJECT_DIR:-.}" 2>/dev/null || exit 0
command -v python3 >/dev/null 2>&1 || exit 0
[ -f AGENTS.md ] || exit 0

login="${BOARD_LOGIN:-}"
[ -n "$login" ] || login="$(cat .board/login 2>/dev/null)"
[ -n "$login" ] || login="$(tools/board whoami 2>/dev/null)"
[ -n "$login" ] || exit 0

BOARD_ME="$login" python3 -c '
import json, os, re, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
ti = data.get("tool_input") or {}
path = ti.get("file_path") or ti.get("notebook_path") or ""
if not path:
    sys.exit(0)
root = os.path.realpath(os.getcwd())
full = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
if not full.startswith(root + os.sep):
    sys.exit(0)
rel = os.path.relpath(full, root).replace(os.sep, "/")

me = os.environ["BOARD_ME"].lower()
owners = []  # (prefix, login, area)
for line in open("AGENTS.md", encoding="utf-8"):
    if not line.startswith("|"):
        continue
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) < 3:
        continue
    m = re.search(r"@([A-Za-z0-9-]+)", cells[1])
    if not m or m.group(1).lower().startswith("todo"):
        continue
    for f in re.findall(r"`([^`]+)`", cells[2]):
        f = f.strip()
        while f.startswith("./"):  # drop a "./" prefix; keep dot-folders like .claude/
            f = f[2:]
        f = f.lstrip("/")
        if f:
            owners.append((f, m.group(1), cells[0]))

best = None
for prefix, login, area in owners:
    p = prefix.rstrip("/")
    if rel == p or rel.startswith(p + "/"):
        if best is None or len(p) > len(best[0]):
            best = (p, login, area)
if best is None or best[1].lower() == me:
    sys.exit(0)

try:
    for h in open(".board/handoffs", encoding="utf-8"):
        h = h.strip().rstrip("/")
        if h and (rel == h or rel.startswith(h + "/")):
            sys.exit(0)
except FileNotFoundError:
    pass

sys.stderr.write(
    f"Blocked by AGENTS.md ownership: {rel} is in {best[0]}/, owned by @{best[1]} ({best[2]}). "
    f"You are @{me}. Do not edit it. Post a handoff request on the board, e.g. "
    f"tools/board post \"[<human>\x27s Claude] @{best[1]} need <change> in {rel} because <reason>; can you do it or hand it to me?\" "
    f"then wait for the reply (tools/board wait). Only after the owner or lead approves on the board, "
    f"append {rel} to .board/handoffs and retry.\n")
sys.exit(2)
'
