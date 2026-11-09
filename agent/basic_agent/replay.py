"""Replay a recorded run step by step; with check=True, re-run every tool call and compare results.

Replay doubles as the demo safety net (show a recorded run if Wi-Fi or the LLM fails) and as a
regression test: the same calls must give the same answers.
"""

from __future__ import annotations

from typing import Any

from fastmcp import Client, FastMCP

from .transcript import result_payload


async def replay(events: list[dict[str, Any]], server: FastMCP | None = None, check: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    tool_events = [e for e in events if e.get("kind") == "tool"]
    if not check or server is None:
        return [{"seq": e["seq"], "tool": e["tool"], "status": "listed", "wall_s": e.get("wall_s")} for e in tool_events]
    async with Client(server) as client:
        for e in tool_events:
            r = await client.call_tool(e["tool"], e.get("args") or {}, raise_on_error=False)
            got, want = result_payload(r), e.get("result")
            same = (bool(r.is_error) == bool(e.get("is_error"))) and (got == want or bool(r.is_error))
            rows.append({"seq": e["seq"], "tool": e["tool"], "status": "ok" if same else "mismatch",
                         **({} if same else {"expected": want, "got": got})})
    return rows
