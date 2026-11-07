"""Transcript: one JSON line per event, so runs can be streamed to the UI and replayed.

Event kinds:
  tool   - an MCP tool call (tool, args, result, is_error, wall_s, scope, cells, nets); written by
           TranscriptMiddleware, so it records calls from *any* client: the guide, the CLI, Claude Code,
           JetBrains Air. `cells`/`nets` = what the call touched, for the 3D view to light up.
  agent  - a guide-agent answer (question, answer, model, tokens, requests, wall_s).
  note   - free text (e.g. "demo started").
Every event has seq, ts (UTC ISO), run_id.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp.server.middleware import Middleware


class Transcript:
    def __init__(self, path: Path | None = None, run_id: str | None = None):
        self.path = Path(path) if path else None
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.events: list[dict[str, Any]] = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, kind: str, **fields: Any) -> dict[str, Any]:
        event = {"seq": len(self.events) + 1, "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                 "run_id": self.run_id, "kind": kind, **fields}
        self.events.append(event)
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        return event


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def result_payload(result: Any) -> Any:
    """The JSON a client would see: structured content if present, else the text blocks."""
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]  # FastMCP wraps non-object results as {"result": ...}
        return structured
    texts = [getattr(c, "text", None) for c in (getattr(result, "content", None) or [])]
    texts = [t for t in texts if t is not None]
    for t in texts:
        try:
            return json.loads(t)
        except (TypeError, ValueError):
            pass
    return texts


class StrictArgsMiddleware(Middleware):
    """Reject tool calls with arguments the tool doesn't have.

    FastMCP's FastAPI bridge only *warns* and drops unknown arguments, so an agent that misspells
    `scope` or `against` would silently get the default (e.g. the whole chip, or the wrong proof).
    Here that becomes a loud tool error that lists the valid arguments, so the agent can retry.
    """

    def __init__(self):
        self._props: dict[str, set[str]] | None = None

    async def on_call_tool(self, context, call_next):
        server = getattr(getattr(context, "fastmcp_context", None), "fastmcp", None)
        if server is not None and self._props is None:
            tools = await server.list_tools(run_middleware=False)
            self._props = {t.name: set((getattr(t, "parameters", None) or {}).get("properties", {})) for t in tools}
        name = getattr(context.message, "name", "")
        allowed = (self._props or {}).get(name)
        unknown = sorted(set((getattr(context.message, "arguments", None) or {})) - allowed) if allowed is not None else []
        if unknown:
            from fastmcp.exceptions import ToolError
            raise ToolError(f"unknown argument(s) {unknown} for {name}; valid arguments: {sorted(allowed)}")
        return await call_next(context)


class TranscriptMiddleware(Middleware):
    """Records every tool call that passes through the Decap MCP server."""

    def __init__(self, transcript: Transcript):
        self.transcript = transcript

    async def on_call_tool(self, context, call_next):
        params = context.message
        name = getattr(params, "name", "?")
        args = dict(getattr(params, "arguments", None) or {})
        t0 = time.perf_counter()
        try:
            result = await call_next(context)
        except Exception as exc:  # tool raised: record, then let FastMCP report it to the client
            self.transcript.record("tool", tool=name, args=args, result=None, is_error=True,
                                   error=f"{type(exc).__name__}: {exc}", wall_s=round(time.perf_counter() - t0, 4))
            raise
        payload = result_payload(result)
        touched = payload.get("touched", {}) if isinstance(payload, dict) else {}
        self.transcript.record("tool", tool=name, args=args, result=payload,
                               is_error=bool(getattr(result, "is_error", False)),
                               wall_s=round(time.perf_counter() - t0, 4),
                               # top-level so the 3D view can light these up without parsing results
                               scope=args.get("scope"), cells=touched.get("cells", []), nets=touched.get("nets", []))
        return result
