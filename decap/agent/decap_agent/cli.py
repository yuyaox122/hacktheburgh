"""`decap`: drive Decap from a terminal or a coding agent.

  decap tools                          list Decap's tools (what any agent sees)
  decap call TOOL -a key=value ...     call one tool, print JSON
  decap demo [--n 137]                 the judge moment, no LLM: plant N, find it, prove it
  decap ask "question"                 ask the guide agent (needs a model key; DECAP_GUIDE_MODEL)
  decap mcp [--http PORT]              serve the tools over MCP (stdio by default)
  decap replay FILE [--check]          step through a recorded run; --check re-runs and compares
  decap stream FILE [--port 8790]      serve a transcript to the UI (SSE /events)

Every command except mcp/replay/stream records a transcript in runs/ unless --no-transcript.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp import Client

from .server import build_server, load_core_app
from .transcript import Transcript, load, result_payload

RUNS = Path(__file__).resolve().parent.parent / "runs"


def _kv(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in pairs or []:
        key, sep, value = p.partition("=")
        if not sep:
            raise SystemExit(f"bad -a {p!r}; use key=value")
        try:
            out[key] = json.loads(value)
        except ValueError:
            out[key] = value
    return out


def _transcript(args) -> Transcript | None:
    if args.no_transcript:
        return None
    path = Path(args.transcript) if args.transcript else RUNS / f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{args.cmd}.jsonl"
    return Transcript(path)


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False))


async def _call(server, tool: str, arguments: dict) -> Any:
    async with Client(server) as c:
        r = await c.call_tool(tool, arguments, raise_on_error=False)
        payload = result_payload(r)
        return {"is_error": True, "error": payload} if r.is_error else payload


async def _tools(server) -> list[dict]:
    async with Client(server) as c:
        return [{"name": t.name, "description": (t.description or "").strip().splitlines()[0]} for t in await c.list_tools()]


async def _demo(server, n: int) -> dict:
    """The 90-second 'judge picks a number' moment, scripted: no LLM, every step a real tool call."""
    base = "stub_counter8"
    async with Client(server) as c:
        async def call(tool, **a):
            return result_payload(await c.call_tool(tool, a))
        await call("list_chips")
        planted = (await call("plant_trigger", chip=base, n=n))["chip"]
        await call("strip_names", chip=planted)
        flagged = (await call("find_rare_triggers", chip=planted))["flagged"]
        suspect = next(f for f in flagged if f["likely"] == "unexplained")
        dec = await call("decompile", chip=planted)
        vs_netlist = await call("prove_equiv", chip=planted, cone="all", against="netlist")
        vs_source = await call("prove_equiv", chip=planted, cone="all", against="author_source")
        reach = await call("cone_of_influence", chip=planted, cone=suspect["cone"])
    return {"chip": planted, "found": suspect["code_line"], "decompiled_proven": f"{dec['proven']}/{dec['total']}",
            "decompile_vs_netlist": vs_netlist["verdict"], "netlist_vs_author_source": vs_source["verdict"],
            "counterexample": vs_source["counterexample"], "trigger_reaches": reach["reaches"],
            "source": vs_source["source"]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="decap", description="Drive Decap's tools (agent layer).")
    p.add_argument("--transcript", help="JSONL file to append events to (default: runs/<time>-<cmd>.jsonl)")
    p.add_argument("--no-transcript", action="store_true", help="don't record")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tools", help="list tools")
    c = sub.add_parser("call", help="call one tool")
    c.add_argument("tool")
    c.add_argument("-a", "--arg", action="append", default=[], metavar="KEY=VALUE")
    d = sub.add_parser("demo", help="scripted judge moment (no LLM)")
    d.add_argument("--n", type=int, default=137)
    a = sub.add_parser("ask", help="ask the guide agent")
    a.add_argument("question")
    a.add_argument("--model", help="e.g. anthropic:claude-haiku-5-5 (default: $DECAP_GUIDE_MODEL)")
    m = sub.add_parser("mcp", help="serve tools over MCP")
    m.add_argument("--http", type=int, metavar="PORT", help="streamable HTTP on this port instead of stdio")
    r = sub.add_parser("replay", help="step through a recorded run")
    r.add_argument("file")
    r.add_argument("--check", action="store_true", help="re-run each call and compare")
    s = sub.add_parser("stream", help="serve a transcript to the UI")
    s.add_argument("file")
    s.add_argument("--port", type=int, default=8790)
    args = p.parse_args(argv)

    if args.cmd == "stream":
        import uvicorn
        from .events import create_events_app
        uvicorn.run(create_events_app(Path(args.file)), host="127.0.0.1", port=args.port, log_level="warning")
        return 0

    if args.cmd == "replay":
        from .replay import replay
        events = load(Path(args.file))
        rows = asyncio.run(replay(events, build_server() if args.check else None, check=args.check))
        _print(rows)
        return 1 if any(r["status"] == "mismatch" for r in rows) else 0

    transcript = None if args.cmd == "mcp" else _transcript(args)
    _, core = load_core_app()
    if core == "stub" and args.cmd != "mcp":
        print("[decap] using the STUB core (set DECAP_CORE_APP=module:attr for the real one)", file=sys.stderr)
    server = build_server(transcript=transcript)

    if args.cmd == "mcp":
        if args.http:
            server.run(transport="http", host="127.0.0.1", port=args.http)
        else:
            server.run(show_banner=False)
        return 0
    if args.cmd == "tools":
        _print(asyncio.run(_tools(server)))
    elif args.cmd == "call":
        out = asyncio.run(_call(server, args.tool, _kv(args.arg)))
        _print(out)
        if isinstance(out, dict) and out.get("is_error"):
            return 1
    elif args.cmd == "demo":
        _print(asyncio.run(_demo(server, args.n)))
    elif args.cmd == "ask":
        from .guide import ask
        print(asyncio.run(ask(args.question, server, transcript, model=args.model)))
    if transcript and transcript.path:
        print(f"[decap] transcript: {transcript.path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
