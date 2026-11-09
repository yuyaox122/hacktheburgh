"""Keep interactive agents off the held-out test chips until the frozen bench has scored them.

The rule (AGENTS.md, spec section 5): nobody opens or re-runs anything on the test split before the frozen run.
Agents are the easiest way to break it by accident: asked to "continue testing", the guide once called
`benchmark`, picked test chips itself, decompiled them and checked them against the author's source.

This middleware sits in every server the agent layer builds (website, terminal, `decap mcp` for Claude Code /
JetBrains Air). A call that names a test-split chip, or the cross-chip `benchmark` tool, gets a clear tool error
the agent can relay. The frozen bench (`decap bench --split test --frozen`) sets DECAP_HELDOUT_OK=1 for its own
process and is the only thing allowed through.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastmcp.server.middleware import Middleware

SPLIT = Path(__file__).resolve().parents[2] / "decap" / "bench" / "split.json"
BLOCKED_TOOLS = {"benchmark"}   # reads or re-runs results across chips, test split included

_cache: set[str] | None = None


def test_chips() -> set[str]:
    global _cache
    if _cache is None:
        try:
            _cache = set(json.loads(SPLIT.read_text()).get("test", []))
        except (OSError, json.JSONDecodeError):
            _cache = set()
    return _cache


def heldout_ok() -> bool:
    return os.environ.get("DECAP_HELDOUT_OK") == "1"


def refusal(tool: str, args: dict) -> str | None:
    """Why this call is refused, or None if it may run."""
    if heldout_ok():
        return None
    if tool in BLOCKED_TOOLS:
        return (f"{tool} is off for interactive agents: it touches the held-out test chips, which only the frozen "
                f"bench may run. Work on the current chip instead.")
    chip = str(args.get("chip") or "")
    base = chip.split("+")[0]
    if base and base in test_chips():
        return (f"{chip} is a held-out test chip. Interactive agents can't open or re-run it until the frozen bench "
                f"has scored it (AGENTS.md rule 4). Use a dev chip.")
    return None


class HeldOutGuard(Middleware):
    async def on_call_tool(self, context, call_next):
        name = getattr(context.message, "name", "")
        why = refusal(name, getattr(context.message, "arguments", None) or {})
        if why:
            from fastmcp.exceptions import ToolError
            raise ToolError(why)
        return await call_next(context)
