"""HTTP API for the web app's Witness features (plan section 6: drag-to-deploy, SSE timeline, claim box).

    decap serve --port 8791          (core from DECAP_CORE_APP; model from --model / DECAP_WITNESS_MODEL)

  POST /witness/explain    {chip, scope, max_refine?, cache?}  -> Server-Sent Events, live: every tool call the agent
                           makes (tool, args, cells, lines) and every claim attempt (kind="claim"), then
                           {kind: "done", ...summary} or {kind: "error", error}. Cached regions replay instantly.
  POST /witness/formalise  {chip, sentence, scope?, check?}     -> formal line + the checker's English (confirm step);
                           check=true also returns the verdict (after the person clicked "Yes, that's what I meant")
  POST /witness/ideas      {chip, line, lines: [a, b], verilog, ask, history}  -> {reply, ideas: [{id, title, why,
                           intent: same|change, lines, replacement}], model}: 2-3 edits of that part (decap_agent/ideas.py).
                           The LLM only proposes; the web then calls the core's review_edit (Yosys decides).
  GET  /witness/cache?chip=X                                   -> cached region runs (the demo plays these)
  GET  /healthz
CORS is open so the Vite dev server and a static page can call it. Every run is also written to runs/.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
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


class ExplainTextIn(BaseModel):
    chip: str
    lines: list[dict] = Field(description="[{line, text}] the selected lines, as shown in the editor")


class CommandIn(BaseModel):
    chip: str
    command: str = Field(description="A natural-language command: 'is anything hidden?', 'what does block b0 do?', ...")
    verilog: str | None = Field(None, description="Your own Verilog: check whether this chip implements it")
    filename: str | None = None
    repair: bool = Field(False, description="With verilog: the agent fixes it until Yosys proves it equal to the chip")
    suspects: list[dict] | None = Field(None, description="Fault-localiser output: [{cell|line, p, explains?, confirmed?}]")
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
            return f"{v}" + (" (20-cycle bounded check, not a proof)" if v == "tested" else
                             " (no author source to compare)" if v == "unavailable" else "")
        if tool == "critical_path":
            d = r.get("delay_ns_estimate")
            return f"~{d} ns to {r.get('ends_at', '?')}" if d is not None else None
        if tool == "simulate":
            last = (r.get("trace") or [{}])[-1]
            ins = r.get("inputs") or {}
            return f"real chip: ui_in={ins.get('ui_in')} -> uo_out={last.get('uo_out')}"
        if tool == "list_chips":
            return f"{len(r.get('chips') or r)} chips" if isinstance(r.get("chips"), list) else None
    except Exception:
        return None
    return None


class Turn(BaseModel):
    role: Literal["user", "agent"]
    text: str


class IdeasIn(BaseModel):
    chip: str
    line: int = Field(description="The line the person pointed at (1-based, in `verilog`).")
    lines: list[int] = Field(min_length=2, max_length=2, description="[a, b]: the region's line range, 1-based.")
    verilog: str = Field(description="The full text shown, with already-kept edits applied.")
    ask: str = ""
    history: list[Turn] = Field(default_factory=list)


def _safe(chip: str) -> str:
    """The chip id as a file-name part: no path separators or leading dots (the id comes from the request)."""
    return re.sub(r"[^A-Za-z0-9_+.-]", "_", chip).lstrip(".")[:80] or "chip"


def _slim(ev: dict) -> dict:
    out = {k: v for k, v in ev.items() if k not in SLIM_DROP}
    if ev.get("kind") == "tool" and "summary" not in out:
        s = tool_summary(str(ev.get("tool")), ev.get("result"))
        if s:
            out["summary"] = s
    return out


def create_witness_app(core_app=None, model: Any = None, runs: Path = RUNS, readings: bool = True) -> FastAPI:
    """`readings`: also stream the plain explanation read from the decompiled code (off in tests with scripted models)."""
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
        t = Transcript(runs / f"witness-{stamp}-{_safe(body.chip)}.jsonl")
        t.listeners.append(lambda ev: queue.put_nowait(_slim(ev)))
        server = build_server(core_app, transcript=t)

        async def go():
            try:
                from fastmcp import Client
                from .plain import plain_for_run, reading_for_scope
                from .scoping import best_attempt, mcp_caller, narrow_scope
                quiet = build_server(core_app)            # lookups for the explanations stay out of the run's steps
                async with Client(quiet) as client:
                    scope, why = await narrow_scope(mcp_caller(client), body.chip, body.scope)
                if why:
                    t.record("thought", step="aim", why=why)

                async def read_code():                   # the always-available explanation, streamed as soon as ready
                    async with Client(quiet) as c:
                        r = await reading_for_scope(c, body.chip, scope, model=model)
                    if r:
                        await queue.put({"kind": "reading", **r})
                    return r
                reading_task = asyncio.create_task(read_code()) if readings else None
                run = await explain_region(body.chip, scope, server, model=model, transcript=t,
                                           max_refine=body.max_refine, cache_dir=cache if body.cache else None)
                final = best_attempt(run["attempts"])     # what people see; the bench keeps run["final"]
                async with Client(quiet) as client:
                    plain = await plain_for_run(client, run, model=model)
                reading = await reading_task if reading_task else None
                shown = final.get("verdict")
                await queue.put({"kind": "done", "chip": body.chip, "scope": body.scope, "aimed": scope if why else None,
                                 "first_draft": run["first_draft"], "final": run["final"], "shown": shown,
                                 "explained": shown in ("proven", "inductive") and bool((final.get("credit") or {}).get("cleared")),
                                 "proof": run.get("proof"),
                                 "claim": final.get("claim"), "english": final.get("english"), "cells": final.get("cells"),
                                 "cached": run.get("cached", False), "plain": plain or reading, "reading": reading})
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
        t = Transcript(runs / f"terminal-{stamp}-{_safe(body.chip)}.jsonl")
        t.listeners.append(lambda ev: queue.put_nowait(_slim(ev)))
        server = build_server(core_app, transcript=t)

        async def go():
            try:
                res = await run_command(server, body.chip, body.command, transcript=t, model=model, max_refine=body.max_refine,
                                        verilog=body.verilog, filename=body.filename, repair=body.repair, suspects=body.suspects)
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

    @app.post("/witness/ideas")
    async def ideas_route(body: IdeasIn) -> dict:
        from .ideas import IdeasInputError, propose_ideas
        try:
            out = await propose_ideas(body.chip, body.line, body.lines, body.verilog, body.ask,
                                      [h.model_dump() for h in body.history], model=model)
        except IdeasInputError as e:   # only the request's own problems are 400; anything from the model is 502
            raise HTTPException(400, str(e))
        except Exception as e:  # model or CLI failure: say so, never an empty answer
            raise HTTPException(502, f"The ideas agent failed: {type(e).__name__}: {str(e)[:300]}")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        Transcript(runs / f"ideas-{stamp}-{_safe(body.chip)}.jsonl").record(
            "ideas", chip=body.chip, line=body.line, lines=body.lines, ask=body.ask,
            **{k: out[k] for k in ("reply", "ideas", "dropped", "model", "prompt", "wall_s")})
        return out

    @app.post("/witness/explain-text")
    async def explain_text(body: ExplainTextIn) -> dict:
        """The editor's Explain: plain English for the lines a person selected (read from the code itself)."""
        from fastmcp import Client
        from .claim_expr import temp_defs
        from .plain import explain_code
        from .transcript import result_payload
        async with Client(build_server(core_app)) as c:
            async def look(tool, **a):
                r = await c.call_tool(tool, a, raise_on_error=False)
                return None if getattr(r, "is_error", False) else result_payload(r)
            dec = await look("decompile", chip=body.chip)
            blocks = await look("recover_blocks", chip=body.chip)
        region = {"inputs": [], "outputs": [], "focus": [{"line": l.get("line"), "text": l.get("text", "")} for l in body.lines[:40]]}
        r = await explain_code(body.chip, region, defs=temp_defs((dec or {}).get("verilog", "") + "\n" + "\n".join(l.get("text", "") for l in body.lines)),
                               blocks=(blocks or {}).get("blocks"), proof=None, model=model)
        if not r:
            raise HTTPException(502, "The agent couldn't explain those lines")
        return r

    @app.get("/witness/status")
    def status() -> list[dict]:
        """The terminal's /status: recent agent sessions (the same registry as `decap status`)."""
        from . import lifecycle
        return lifecycle.list_sessions()[-12:]

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
