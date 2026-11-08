@AGENTS.md

## Non-negotiable for Claude in this repo

Follow the board method in AGENTS.md on every task, without being reminded:

1. **Session start:** read the unread board posts the hook shows. Then start exactly ONE background Bash command, `tools/board wait`, with run_in_background. When it finishes, handle the posts and start it again. Never run two at once.
2. **Before editing:** check the owner table in AGENTS.md section 4. Edit only folders owned by your human's GitHub login (`tools/board whoami`). For anything else, post a handoff request on the board tagging the owner, then wait. The owner guard hook will block edits to another owner's folders.
3. **Post on the board** when you finish a unit of work, change a shared contract, merge something others depend on, or are blocked. Start every post with `[<human>'s Claude]` and @ the GitHub login you need.
4. **When tagged** (a post flagged `** MENTION **` or `@all`): act on it and reply on the board.
5. **Git:** your own branch `<issue#>-<slug>`, stage only your own files (never `git add -A`), fetch and rebase before pushing, never force-push shared branches.
