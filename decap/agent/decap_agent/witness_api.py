"""HTTP API for the web app's Witness features (plan section 6: drag-to-deploy, SSE timeline, claim box).

    decap serve --port 8791          (core from DECAP_CORE_APP; model from --model / DECAP_WITNESS_MODEL)

  POST /witness/explain    {chip, scope, max_refine?, cache?}  -> Server-Sent Events, live: every tool call the agent
                           makes (tool, args, cells, lines) and every claim attempt (kind="claim"), then
                           {kind: "done", ...summary} or {kind: "error", error}. Cached regions replay instantly.
  POST /witness/formalise  {chip, sentence, scope?, check?}     -> formal line + the checker's English (confirm step);
                           check=true also returns the verdict (after the person clicked "Yes, that's what I meant")
  GET  /witness/cache?chip=X                                   -> cached region runs (the demo plays these)
  GET  /healthz
CORS is open so the Vite dev server and a static page can call it. Every run is also written to runs/.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .server import build_server
from .transcript import Transcript

RUNS = Path(__file__).resolve().parent.parent / "runs"
CACHE = RUNS / "cache"
SLIM_DROP = {"result"}   # tool results can be large; the stream carries what the UI draws


class ExplainIn(BaseModel):
    chip: str
    scope: dict = Field(default_factory=dict, description="{cone_of} / {cells} / {nets}, as for the core's tools")
    max_refine: int = Field(2, ge=0, le=4)
    cache: bool = True


class FormaliseIn(BaseModel):
    chip: str
    sentence: str
    scope: dict = Field(default_factory=dict)
    check: bool = False


class CommandIn(BaseModel):
    chip: str
    command: str = Field(description="A natural-language command: 'is anything hidden?', 'what does block b0 do?', ...")
    verilog: str | None = Field(None, description="Your own Verilog: check whether this chip implements it")
    filename: str | None = None
    max_refine: int = Field(2, ge=0, le=4)


def tool_summary(tool: str, r: Any) -> str | None:
    """A one-line `↳` result for the terminal, computed server-side before the (large) result is dropped."""
    if not isinstance(r, dict):
        return None
    try:
        if tool == "recover_blocks":
            bl = r.get("blocks") or []
            unrec = sum(1 for b in bl if not b.get("recognised"))
            cov = (r.get("coverage") or {}).get("pct")
            return f"{len(bl)} blocks, {unrec} unrecognised" + (f" · {cov}% explained" if cov is not None else "")
        if tool == "find_rare_triggers":
            n = len(r.get("flagged") or [])
            return "no rare conditions" if n == 0 else f"{n} rare condition{'s' if n > 1 else ''}"
        if tool == "prove_equiv":
            v = r.get("verdict")
            return f"{v}" + (" (random testing only, not a proof)" if v == "tested" else "")
        if tool == "critical_path":
            d = r.get("delay_ns_estimate")
            return f"~{d} ns to {r.get('ends_at', '?')}" if d is not None else None
        if tool == "list_chips":
            return f"{len(r.get('chips') or r)} chips" if isinstance(r.get("chips"), list) else None
    except Exception:
        return None
    return None


def _slim(ev: dict) -> dict:
    out = {k: v for k, v in ev.items() if k not in SLIM_DROP}
    if ev.get("kind") == "tool" and "summary" not in out:
        s = tool_summary(str(ev.get("tool")), ev.get("result"))
        if s:
            out["summary"] = s
    return out


def create_witness_app(core_app=None, model: Any = None, runs: Path = RUNS) -> FastAPI:
    app = FastAPI(title="Decap Witness agent API")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])
    cache = runs / "cache"

    @app.get("/healthz")
    @app.get("/witness/healthz")
    def healthz() -> dict:
        return {"ok": True, "model": str(model) if model else "default", "cache": str(cache)}

    @app.post("/witness/explain")
    async def explain(body: ExplainIn) -> StreamingResponse:
        from .witness import explain_region
        queue: asyncio.Queue = asyncio.Queue()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        t = Transcript(runs / f"witness-{stamp}-{body.chip}.jsonl")
        t.listeners.append(lambda ev: queue.put_nowait(_slim(ev)))
        server = build_server(core_app, transcript=t)

        async def go():
            try:
                run = await explain_region(body.chip, body.scope, server, model=model, transcript=t,
                                           max_refine=body.max_refine, cache_dir=cache if body.cache else None)
                final = run["attempts"][-1]
                from fastmcp import Client
                from .plain import plain_for_run
                async with Client(server) as client:
                    plain = await plain_for_run(client, run, model=model)
                await queue.put({"kind": "done", "chip": body.chip, "scope": body.scope, "first_draft": run["first_draft"],
                                 "final": run["final"], "explained": run["explained"], "proof": run.get("proof"),
                                 "claim": final.get("claim"), "english": final.get("english"), "cells": final.get("cells"),
                                 "cached": run.get("cached", False), "plain": plain})
            except Exception as e:  # shown to the user, not swallowed
                await queue.put({"kind": "error", "error": f"{type(e).__name__}: {str(e)[:400]}"})

        task = asyncio.create_task(go())

        async def stream():
            while True:
                ev = await queue.get()
                yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
                if ev.get("kind") in ("done", "error"):
                    break
            await task

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/witness/command")
    async def command(body: CommandIn) -> StreamingResponse:
        from .terminal import run_command
        queue: asyncio.Queue = asyncio.Queue()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        t = Transcript(runs / f"terminal-{stamp}-{body.chip}.jsonl")
        t.listeners.append(lambda ev: queue.put_nowait(_slim(ev)))
        server = build_server(core_app, transcript=t)

        async def go():
            try:
                res = await run_command(server, body.chip, body.command, transcript=t, model=model, max_refine=body.max_refine,
                                        verilog=body.verilog, filename=body.filename)
                await queue.put({"kind": "done", **res})
            except Exception as e:
                await queue.put({"kind": "error", "error": f"{type(e).__name__}: {str(e)[:400]}"})

        task = asyncio.create_task(go())

        async def stream():
            while True:
                ev = await queue.get()
                yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
                if ev.get("kind") in ("done", "error"):
                    break
            await task

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/witness/formalise")
    async def formalise_route(body: FormaliseIn) -> dict:
        from .formalise import formalise
        return await formalise(body.sentence, server=build_server(core_app), chip=body.chip, scope=body.scope,
                               model=model, check=body.check)

    @app.get("/witness/cache")
    def cached(chip: str = "") -> list[dict]:
        rows = []
        for f in sorted(cache.glob("*.json")) if cache.exists() else []:
            run = json.loads(f.read_text())
            if chip and run.get("chip") != chip:
                continue
            last = run["attempts"][-1]
            rows.append({"chip": run["chip"], "scope": run["scope"], "first_draft": run["first_draft"], "final": run["final"],
                         "explained": run["explained"], "attempts": len(run["attempts"]), "claim": last.get("claim"),
                         "english": last.get("english"), "cells": last.get("cells"), "model": run.get("model")})
        return rows

    return app
