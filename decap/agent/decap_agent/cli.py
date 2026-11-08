"""`decap`: drive Decap from a terminal or a coding agent.

  decap tools                          list Decap's tools (what any agent sees)
  decap call TOOL -a key=value ...     call one tool, print JSON
  decap demo [--n 137]                 the judge moment, no LLM: plant N, find it, prove it
  decap ask "question"                 ask the guide agent (needs a model key; DECAP_GUIDE_MODEL)
  decap explain CHIP --cone-of X       Witness: a region agent looks, claims, gets checked, refines (LLM)
  decap claim CHIP "expr" --cone-of X  Witness: send one claim straight to the checker (no LLM)
  decap formalise "sentence" [CHIP]    the judge's sentence -> a formal line + the checker's English (--check: verdict)
  decap bench --split dev              Witness bench: a region agent per unrecognised block, first drafts scored
  decap bench --freeze-hash            the hash to post before scoring the test split (--split test needs --frozen)
  decap timeline FILE... -o page.html  the Witness timeline page from transcripts (--bench, --ablation)
  decap serve [--port 8791]            HTTP API for the web app: /witness/explain (SSE), /witness/formalise
  decap mcp [--http PORT]              serve the tools over MCP (stdio by default)
  decap replay FILE [--check]          step through a recorded run; --check re-runs and compares
  decap stream FILE [--port 8790]      serve a transcript to the UI (SSE /events)
  decap status                         list sessions (running first, then recent done)
  decap log -f SESSION                 tail a session's transcript (append-only follow)
  decap stop SESSION                   mark a session stopped

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

import os

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")


def load_secrets(path: str | None = None) -> list[str]:
    """Load KEY=VALUE lines (e.g. DEEPSEEK_API_KEY) from a file OUTSIDE the repo, default ~/.config/decap/secrets.env.
    Never printed, never written anywhere; values already in the environment win. Returns the key names loaded."""
    p = Path(os.path.expanduser(path or os.environ.get("DECAP_SECRETS", "~/.config/decap/secrets.env")))
    loaded = []
    try:
        for line in p.read_text().splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and k and not k.startswith("#") and k not in os.environ:
                v = v.split("#", 1)[0].strip()   # drop inline '# comment' (e.g. DECAP_GUIDE_CLI=1  # default)
                os.environ[k] = v.strip('"').strip("'")
                loaded.append(k)
    except OSError:
        pass
    return loaded

from fastmcp import Client

from .server import build_server, load_core_app
from .transcript import Transcript, load, result_payload

RUNS = Path(__file__).resolve().parent.parent / "runs"
CACHE = RUNS / "cache"


def _scope(args) -> dict[str, Any]:
    scope: dict[str, Any] = {}
    if args.cone_of:
        scope["cone_of"] = args.cone_of
    if args.cells:
        scope["cells"] = args.cells.split(",")
    if args.nets:
        scope["nets"] = args.nets.split(",")
    return scope


def _scope_args(sp) -> None:
    sp.add_argument("--cone-of", help="the region: everything driving this port bit, net, flag or block (e.g. uo_out[0])")
    sp.add_argument("--cells", help="the region: comma-separated cell ids")
    sp.add_argument("--nets", help="the region: comma-separated nets / port bits")


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


async def _demo(server, n: int, chip: str | None = None) -> dict:
    """The 90-second 'judge picks a number' moment, scripted: no LLM, every step a real tool call.

    Works on the stub (region-targeted) and on the real core (Yuyao's API: flags f0.., remove_trigger).
    """
    async with Client(server) as c:
        async def call(tool, **a):
            return result_payload(await c.call_tool(tool, a))
        rows = await call("list_chips")
        if not (isinstance(rows, list) and rows and rows[0].get("source") == "stub"):  # the real core
            return await _demo_real(call, n, chip)
        base = "stub_counter8"   # list_chips above was the demo's first step
        planted = (await call("plant_trigger", chip=base, n=n))["chip"]
        await call("strip_names", chip=planted)
        # The user points at the output logic in the 3D view; the agent works only inside that region.
        region = {"cone_of": "out"}
        selected = await call("resolve_scope", chip=planted, scope=region)
        flagged = (await call("find_rare_triggers", chip=planted, scope=region))["flagged"]
        suspect = next(f for f in flagged if f["likely"] == "unexplained")
        focus = {"cells": selected["touched"]["cells"]}
        dec = await call("decompile", chip=planted, scope=focus)
        vs_netlist = await call("prove_equiv", chip=planted, scope=focus, against="netlist")
        vs_source = await call("prove_equiv", chip=planted, scope=focus, against="author_source")
        reach = await call("cone_of_influence", chip=planted, cone=suspect["cone"])
    return {"chip": planted, "region": region, "region_cells": len(selected["touched"]["cells"]),
            "found": suspect["code_line"], "decompiled_proven": f"{dec['proven']}/{dec['total']}",
            "decompile_vs_netlist": vs_netlist["verdict"], "netlist_vs_author_source": vs_source["verdict"],
            "counterexample": vs_source["counterexample"], "trigger_cells": vs_source["touched"]["cells"][:3] + ["…"],
            "trigger_reaches": reach["reaches"], "source": vs_source["source"]}


async def _demo_real(call, n: int, chip: str | None) -> dict:
    """The same story on a real Tiny Tapeout chip via the real core."""
    if chip is None:
        rows = await call("list_chips")
        chip = next(r["chip"] for r in rows if r.get("split") == "dev")
    planted = await call("plant_trigger", chip=chip, n=n)
    pid = planted["chip"]
    flags = (await call("find_rare_triggers", chip=pid)).get("flagged", [])
    hit = next((f for f in flags if f"8'd{n}" in (f.get("condition") or "")), flags[0] if flags else None)
    vs_source = await call("prove_equiv", chip=pid, against="author_source")
    reach = await call("cone_of_influence", chip=pid, cone=hit["id"]) if hit else {}
    fixed = await call("remove_trigger", chip=pid)
    return {"chip": pid, "inserted_cells": planted.get("inserted_cells"), "plant_verdict": planted.get("verdict"),
            "found": hit and hit.get("code_line"), "condition": hit and hit.get("condition"),
            "flags": len(flags), "netlist_vs_author_source": vs_source.get("verdict"),
            "counterexample": vs_source.get("counterexample"), "trigger_reaches": reach.get("reaches"),
            "fix_verdict": fixed.get("verdict"), "fix_detail": fixed.get("detail"), "source": "core"}


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
    d.add_argument("--chip", help="real core only: which chip (default: first dev chip)")
    a = sub.add_parser("ask", help="ask the guide agent")
    a.add_argument("question")
    a.add_argument("--model", help="e.g. anthropic:claude-haiku-5-5 (default: $DECAP_GUIDE_MODEL)")
    t = sub.add_parser("agent-send", help="the terminal: a natural-language command for the chip agent")
    t.add_argument("command")
    t.add_argument("--chip", help="chip to act on (default: first dev chip)")
    t.add_argument("--model", help="default: $DECAP_WITNESS_MODEL")
    w = sub.add_parser("explain", help="Witness region agent: look, claim, check, refine")
    w.add_argument("chip")
    _scope_args(w)
    w.add_argument("--model", help="e.g. anthropic:claude-sonnet-5-5 (default: $DECAP_WITNESS_MODEL)")
    w.add_argument("--max-refine", type=int, default=2)
    w.add_argument("--no-cache", action="store_true", help="don't read or write runs/cache")
    k = sub.add_parser("claim", help="send one claim to the checker (no LLM)")
    k.add_argument("chip")
    k.add_argument("claim")
    _scope_args(k)
    k.add_argument("--label", default="")
    k.add_argument("--dry", action="store_true", help="parse and render the English only (the confirm step)")
    f = sub.add_parser("formalise", help="a person's sentence -> one checkable claim")
    f.add_argument("sentence")
    f.add_argument("chip", nargs="?", help="chip to take names from (default: the Tiny Tapeout pinout)")
    _scope_args(f)
    f.add_argument("--model", help="default: $DECAP_FORMALISE_MODEL or anthropic:claude-sonnet-5-5")
    f.add_argument("--check", action="store_true", help="also send it to the checker (after the person confirms)")
    b = sub.add_parser("bench", help="Witness bench driver")
    b.add_argument("--split", choices=["dev", "test"], default="dev")
    b.add_argument("--chips", help="comma-separated subset of the split")
    b.add_argument("--max-regions", type=int, default=4)
    b.add_argument("--max-refine", type=int, default=2)
    b.add_argument("--concurrency", type=int, default=3)
    b.add_argument("--model", help="e.g. cli:sonnet or anthropic:claude-sonnet-5-5 (default: $DECAP_WITNESS_MODEL)")
    b.add_argument("--no-cache", action="store_true")
    b.add_argument("--out", help="bench JSON (default: runs/bench-<split>-<time>.json)")
    b.add_argument("--frozen", action="store_true", help="required for --split test: the freeze hash is posted")
    b.add_argument("--freeze-hash", action="store_true", help="print the freeze hash and exit")
    b.add_argument("--ablate", metavar="TRANSCRIPT", help="re-check every claim in a bench transcript with random and "
                                                          "biased simulation instead of Yosys (no LLM; real core only)")
    tl = sub.add_parser("timeline", help="render the Witness timeline page from transcripts")
    tl.add_argument("files", nargs="+")
    tl.add_argument("-o", "--out", default=str(Path(__file__).resolve().parent.parent / "prototypes" / "witness-timeline.html"))
    tl.add_argument("--bench", help="bench JSON (for region sizes)")
    tl.add_argument("--ablation", help="ablation JSON from 'decap bench --ablate FILE --out X'")
    tl.add_argument("--include-fallback", action="store_true", help="also show simulation-checked runs")
    sv = sub.add_parser("serve", help="HTTP API for the web app (Witness explain/formalise)")
    sv.add_argument("--port", type=int, default=8791)
    sv.add_argument("--model", help="e.g. cli:sonnet (default: $DECAP_WITNESS_MODEL)")
    m = sub.add_parser("mcp", help="serve tools over MCP")
    m.add_argument("--http", type=int, metavar="PORT", help="streamable HTTP on this port instead of stdio")
    r = sub.add_parser("replay", help="step through a recorded run")
    r.add_argument("file")
    r.add_argument("--check", action="store_true", help="re-run each call and compare")
    s = sub.add_parser("stream", help="serve a transcript to the UI")
    s.add_argument("file")
    s.add_argument("--port", type=int, default=8790)
    stt = sub.add_parser("status", help="list agent sessions (running first, then recent done)")
    lg = sub.add_parser("log", help="print or follow a session's transcript")
    lg.add_argument("session_id")
    lg.add_argument("-f", "--follow", action="store_true", help="follow append-only until the run ends")
    sp = sub.add_parser("stop", help="mark a session stopped")
    sp.add_argument("session_id")
    args = p.parse_args(argv)
    load_secrets()

    if args.cmd == "stream":
        import uvicorn
        from .events import create_events_app
        uvicorn.run(create_events_app(Path(args.file)), host="127.0.0.1", port=args.port, log_level="warning")
        return 0

    if args.cmd == "timeline":
        from .timeline import write
        out = write([Path(f) for f in args.files], Path(args.out), Path(args.bench) if args.bench else None,
                    Path(args.ablation) if args.ablation else None, args.include_fallback)
        print(f"[decap] timeline: {out}", file=sys.stderr)
        return 0

    if args.cmd == "serve":
        import uvicorn
        from . import lifecycle
        from .witness_api import create_witness_app
        core, _ = load_core_app()
        sid = lifecycle.start(None, "serve")
        print(f"[decap] serve session {sid} (decap status / decap stop {sid})", file=sys.stderr)
        try:
            uvicorn.run(create_witness_app(core, model=args.model), host="127.0.0.1", port=args.port,
                        log_level="warning")
        finally:
            lifecycle.finish(sid, "stopped")
        return 0

    if args.cmd == "replay":
        from .replay import replay
        events = load(Path(args.file))
        rows = asyncio.run(replay(events, build_server() if args.check else None, check=args.check))
        _print(rows)
        return 1 if any(r["status"] == "mismatch" for r in rows) else 0

    if args.cmd == "status":
        from .lifecycle import format_session, list_sessions
        for s in list_sessions():
            print(format_session(s))
        return 0

    if args.cmd == "log":
        from .lifecycle import follow
        return follow(args.session_id, follow=args.follow)

    if args.cmd == "stop":
        from .lifecycle import stop
        ok = stop(args.session_id)
        print(f"[decap] {'stopped' if ok else 'no such session'} {args.session_id}", file=sys.stderr)
        return 0 if ok else 1

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
        _print(asyncio.run(_demo(server, args.n, args.chip)))
    elif args.cmd == "ask":
        from .guide import ask
        print(asyncio.run(ask(args.question, server, transcript, model=args.model)))
    elif args.cmd == "agent-send":
        from .terminal import run_command
        chip = args.chip or asyncio.run(_call(server, "list_chips", {}))[0]["chip"]
        _print(asyncio.run(run_command(server, chip, args.command, transcript=transcript, model=args.model)))
    elif args.cmd == "explain":
        from .witness import explain_region
        run = asyncio.run(explain_region(args.chip, _scope(args), server, model=args.model, transcript=transcript,
                                         max_refine=args.max_refine, cache_dir=None if args.no_cache else CACHE))
        for a in run["attempts"]:
            tag = "FIRST DRAFT" if a["first_draft"] else f"refine {a['attempt']}"
            print(f"[{tag:11s}] {a['verdict']:11s} {a['claim']}" + (f"   cex={json.dumps(a['counterexample'])}" if a["counterexample"] else ""),
                  file=sys.stderr)
        _print({k: run[k] for k in ("chip", "scope", "model", "first_draft", "final", "explained", "cached")}
               | {"claim": run["attempts"][-1]["claim"], "english": run["attempts"][-1]["english"],
                  "credit": run["attempts"][-1]["credit"]})
    elif args.cmd == "formalise":
        from .formalise import formalise
        _print(asyncio.run(formalise(args.sentence, server=server if args.chip else None, chip=args.chip,
                                     scope=_scope(args), model=args.model, transcript=transcript, check=args.check)))
    elif args.cmd == "bench":
        from .bench import freeze_hash, run_bench
        from .witness import DEFAULT_MODEL
        rows = asyncio.run(_call(server, "list_chips", {}))
        if args.freeze_hash:
            first = next(r["chip"] for r in rows if r.get("split") == "dev")
            region = asyncio.run(_call(server, "get_region", {"chip": first, "scope": {}}))
            model_name = args.model or os.environ.get("DECAP_WITNESS_MODEL", DEFAULT_MODEL)
            _print(freeze_hash(model_name, Path("decap/bench/split.json"), region.get("decompiler", "unknown")))
            return 0
        if args.ablate:
            from .bench import ablate
            res = ablate(load(Path(args.ablate)))
            if args.out:
                Path(args.out).write_text(json.dumps(res, indent=1))
            _print({k: v for k, v in res.items() if k != "rows"})
            return 0
        if args.split == "test" and not args.frozen:
            print("[decap] refusing to score the held-out split without --frozen (post the freeze hash first)",
                  file=sys.stderr)
            return 2
        if args.split == "test":
            os.environ["DECAP_HELDOUT_OK"] = "1"   # the frozen bench is the one thing allowed past heldout.py
        chips = [r["chip"] for r in rows if r.get("split") == args.split]
        if args.chips:
            want = args.chips.split(",")
            chips = [c for c in chips if c in want]
        out = Path(args.out) if args.out else RUNS / f"bench-{args.split}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
        res = asyncio.run(run_bench(server, chips, model=args.model, max_regions=args.max_regions,
                                    max_refine=args.max_refine, transcript=transcript, concurrency=args.concurrency,
                                    cache_dir=None if args.no_cache else CACHE, out_path=out,
                                    log=lambda m: print(m, file=sys.stderr)))
        _print(res["summary"] | {"wall_s": res["wall_s"], "out": str(out)})
    elif args.cmd == "claim":
        out = asyncio.run(_call(server, "propose_claim", {"chip": args.chip, "scope": _scope(args), "claim": args.claim,
                                                          "label": args.label, "check": not args.dry}))
        _print(out)
    if transcript and transcript.path:
        print(f"[decap] transcript: {transcript.path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
