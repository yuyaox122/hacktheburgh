"""The Witness investigator: a multi-step agent that plans, acts, observes and reports — Witness's "work mode".

    basic agent-send "audit this chip: what are its parts, is anything hidden, and what's the slowest path?"

Where `explain` is one fixed loop (look -> claim -> Yosys -> refine) and `hunt` is one fixed recipe, the
investigator decides *which* of those to run, in what order, from what it sees:

  1. plan       it writes a short numbered plan first (event kind="plan"), and may revise it later
  2. act        every tool call carries a one-line `why` (event kind="thought") so the reasoning is visible
  3. observe    tool results come back as data; it picks the next step from them (not from a script)
  4. remember   anything Yosys decided is written to chip memory (runs/memory/<chip>.json, kind="memory"),
                so the next question on this chip starts from what is already proven — the chip analogue of
                DimOS's spatial memory
  5. report     a structured Report: summary, findings (each tagged with where its evidence came from), next steps

The safety layer is unchanged: the investigator cannot mark anything proven. Its findings quote verdicts the
tools returned; "proven" comes only from propose_claim (Yosys), "hidden logic found" only from a failing
prove_equiv with a counterexample. Tool calls go through the same MCP server, so they land in the transcript.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp import Client, FastMCP
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

from basic.paths import RUNS

from .cli_model import DEEPSEEK
from .transcript import Transcript, result_payload

MEMORY = RUNS / "memory"
DEFAULT_MODEL = DEEPSEEK
MAX_EXPLAINS = 3          # each explain is its own look->claim->Yosys loop; keep a run under ~2 minutes
REQUEST_LIMIT = 16

INSTRUCTIONS = """You are Witness, an agent that reads chips. A chip's netlist has had every name stripped; bASIC
decompiles it into readable Verilog blocks and Yosys formally checks claims. You investigate one chip for a user.

How you work:
1. Call `plan` FIRST with 2-5 short steps tailored to the user's request. Call `plan` again only if a result
   changes WHICH steps you will take next (never just to restate progress or before reporting).
2. Every other tool takes `why`: one short sentence saying what you expect to learn. Be specific.
3. Start from `recall` if the request might already be answered, then `overview` to see the blocks.
4. Choose steps from what you observe. Explain the blocks the user cares about, or the unrecognised ones
   (kind 'logic', recognised=false) — those are where the decompiler is least sure. At most 3 explains.
5. Use `hunt` only when the user asks about hidden, suspicious, trojan, backdoor or "anything wrong".
6. Use `slowest_path` only for speed/timing/optimisation questions.
7. Finish with the report. Every finding cites its evidence: 'yosys' (a proven/inductive/refuted verdict),
   'proof' (prove_equiv), 'structure' (overview/blocks), 'code' (an explanation read from the proven decompiled code, when no short
   claim was proven), 'estimate' (timing), or 'memory'.

Writing the report: the reader is a sharp engineer new to this chip. Say what the chip does first, in hardware
terms ("a clock with an alarm"), then the findings. Where an explain result has `plain`, reuse its wording rather
than reciting the formal claim. Group repeated structure instead of listing bits. Plain words, short sentences of
varied length, no em dashes, no "not just X but Y", no hype words (crucial, robust, seamless, essentially).
Interpretation of purpose gets "looks like" or "probably"; proven facts are stated plainly.

Hard rules: a hunt verdict 'inconclusive' is not 'clean' — say the chip was tested, not proven, free of
hidden logic. Never call something proven unless a tool returned verdict proven or inductive. 'tested' and
'bounded' are not proven. Quote block ids, numbers and verdicts exactly. Plain English, no hype."""


class Finding(BaseModel):
    text: str = Field(description="One plain-English sentence")
    evidence: str = Field(description="yosys | proof | structure | estimate | memory")
    block: str | None = Field(None, description="Block id like b3, if the finding is about one block")


class Report(BaseModel):
    summary: str = Field(description="Two or three sentences answering the user's request directly")
    findings: list[Finding] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list, description="At most 3 follow-ups, each a short command (under 10 words) the user could type, e.g. 'what does b3 do?'")


@dataclass
class Deps:
    chip: str
    client: Client
    server: FastMCP
    transcript: Transcript | None
    model: Any
    max_refine: int
    explains: int = 0
    blocks: dict[str, dict] = field(default_factory=dict)
    trail: list[dict] = field(default_factory=list)   # what was learnt this run (for the done event + memory)


# ---- chip memory -------------------------------------------------------------------------------------------

def _mem_path(chip: str) -> Path:
    return MEMORY / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', chip)}.json"


def recall_memory(chip: str) -> list[dict]:
    p = _mem_path(chip)
    try:
        return json.loads(p.read_text()) if p.exists() else []
    except (OSError, json.JSONDecodeError):
        return []


def remember(chip: str, fact: dict, transcript: Transcript | None = None) -> None:
    """Upsert one fact by its key (e.g. 'explain:b3', 'hunt'); the newest result wins."""
    facts = [f for f in recall_memory(chip) if f.get("key") != fact["key"]]
    fact = {**fact, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    facts.append(fact)
    MEMORY.mkdir(parents=True, exist_ok=True)
    _mem_path(chip).write_text(json.dumps(facts, indent=1, default=str))
    if transcript is not None:
        transcript.record("memory", chip=chip, fact=fact)


# ---- the agent ---------------------------------------------------------------------------------------------

def _thought(deps: Deps, step: str, why: str) -> None:
    if deps.transcript is not None:
        deps.transcript.record("thought", step=step, why=why.strip()[:300])


async def _call(deps: Deps, tool: str, **args) -> Any:
    r = await deps.client.call_tool(tool, args, raise_on_error=False)
    payload = result_payload(r)
    if getattr(r, "is_error", False):
        return {"error": str(payload)[:400]}
    return payload


def build_investigator(model: Any = None) -> Agent[Deps, Report]:
    from .cli_model import resolve
    agent: Agent[Deps, Report] = Agent(
        resolve(model, os.environ.get("BASIC_INVESTIGATE_MODEL", DEFAULT_MODEL)), deps_type=Deps,
        output_type=Report, instructions=INSTRUCTIONS, defer_model_check=True, retries=2,
        model_settings={"parallel_tool_calls": False})   # one step at a time, so the reasoning reads in order

    @agent.tool
    async def plan(ctx: RunContext[Deps], steps: list[str]) -> str:
        """Write (or revise) your plan: 2-5 short imperative steps."""
        steps = [s.strip() for s in steps if s and s.strip()][:6]
        if ctx.deps.transcript is not None:
            ctx.deps.transcript.record("plan", steps=steps, revision=sum(1 for t in ctx.deps.trail if t.get("plan")))
        ctx.deps.trail.append({"plan": steps})
        return "Plan recorded. Carry it out."

    @agent.tool
    async def recall(ctx: RunContext[Deps], why: str) -> list[dict] | str:
        """What earlier runs already established on this chip (proven claims, hunt verdicts), newest last."""
        _thought(ctx.deps, "recall", why)
        facts = recall_memory(ctx.deps.chip)
        if ctx.deps.transcript is not None:
            ctx.deps.transcript.record("tool", tool="recall", args={}, wall_s=0.0, is_error=False,
                                       summary=f"{len(facts)} remembered")
        return facts or "Nothing remembered for this chip yet."

    @agent.tool
    async def overview(ctx: RunContext[Deps], why: str) -> dict:
        """The chip's blocks recovered from structure: id, kind, recognised?, width, cell count, decompiled line."""
        _thought(ctx.deps, "overview", why)
        r = await _call(ctx.deps, "recover_blocks", chip=ctx.deps.chip)
        if isinstance(r, dict) and "blocks" in r:
            ctx.deps.blocks = {b["id"]: b for b in r["blocks"]}
            return {"coverage": r.get("coverage"),
                    "blocks": [{k: b.get(k) for k in ("id", "kind", "recognised", "width", "cells")} |
                               {"text": (b.get("text") or "")[:160]} for b in r["blocks"][:40]]}
        return r

    @agent.tool
    async def explain(ctx: RunContext[Deps], block: str, why: str) -> dict:
        """Run the region agent on one block (e.g. 'b3'): it claims what the block does and Yosys checks it,
        refining on counterexamples. Returns the verdict and the claim."""
        _thought(ctx.deps, f"explain {block}", why)
        if ctx.deps.explains >= MAX_EXPLAINS:
            return {"error": f"explain budget used ({MAX_EXPLAINS}); report with what you have"}
        if ctx.deps.blocks and block not in ctx.deps.blocks:
            return {"error": f"no block {block}; known: {', '.join(list(ctx.deps.blocks)[:30])}"}
        ctx.deps.explains += 1
        try:
            return await _explain_block(ctx, block)
        except Exception as e:   # one bad block must not abort the whole investigation
            return {"block": block, "error": f"{type(e).__name__}: {str(e)[:200]}"}

    async def _explain_block(ctx: RunContext[Deps], block: str) -> dict:
        from .witness import explain_region
        from .scoping import best_attempt, mcp_caller, narrow_scope
        scope, aimed = await narrow_scope(mcp_caller(ctx.deps.client), ctx.deps.chip, {"cone_of": block})
        if aimed:
            _thought(ctx.deps, "aim", aimed)
        run = await explain_region(ctx.deps.chip, scope, ctx.deps.server, model=ctx.deps.model,
                                   transcript=ctx.deps.transcript, max_refine=ctx.deps.max_refine, register=False)
        final = best_attempt(run["attempts"])
        verdict = final.get("verdict")
        out = {"block": block, "verdict": verdict, "explained": verdict in ("proven", "inductive"), "claim": final.get("claim"),
               "english": final.get("english"), "attempts": len(run["attempts"]),
               "counterexample": final.get("counterexample") if verdict == "refuted" else None,
               "credit_reason": (final.get("credit") or {}).get("reason")}
        from .plain import plain_for_run, reading_for_scope
        plain = (await plain_for_run(ctx.deps.client, run, model=ctx.deps.model)
                 or await reading_for_scope(ctx.deps.client, ctx.deps.chip, scope, model=ctx.deps.model))
        if plain:   # the readable version, so the report can say it the same way the card does
            out["plain"] = {"headline": plain["headline"], "explanation": plain["explanation"]}
        ctx.deps.trail.append({"explain": out})
        if verdict in ("proven", "inductive", "partial"):   # a true fact is worth remembering, a refuted guess isn't
            remember(ctx.deps.chip, {"key": f"explain:{block}", "verdict": verdict, "claim": final.get("claim"),
                                     "english": final.get("english")}, ctx.deps.transcript)
        return out

    @agent.tool
    async def hunt(ctx: RunContext[Deps], why: str) -> dict:
        """Look for hidden logic: rare-trigger candidates, then a whole-chip proof against the author's source.
        A failing proof's counterexample is the input that wakes the hidden logic."""
        _thought(ctx.deps, "hunt", why)
        from .terminal import _hunt
        res = await _hunt(ctx.deps.client, ctx.deps.chip, None)
        meaning = {"found": "Hidden logic exists: the counterexample is the input that wakes it.",
                   "clean": "Proven equivalent to the author's source: no hidden logic.",
                   "inconclusive": "NOT proven clean: only a 20-cycle bounded check passed, so a rare trigger could "
                                   "still hide. Say this plainly; do not write 'no hidden logic'.",
                   "unchecked": "NOTHING was checked against the author's source (no source, or an error). Make no "
                                "claim about hidden logic either way."}.get(res["verdict"], "unknown result")
        out = {"verdict": res["verdict"], "meaning": meaning, "proof": res["proof"], "counterexample": res.get("counterexample"),
               "flags": [{k: f.get(k) for k in ("id", "condition", "rate", "line") if k in f} for f in res["flags"][:8]]}
        ctx.deps.trail.append({"hunt": out})
        if res["proof"] in ("proven", "failed", "refuted"):
            remember(ctx.deps.chip, {"key": "hunt", "verdict": res["verdict"], "proof": res["proof"],
                                     "counterexample": res.get("counterexample")}, ctx.deps.transcript)
        return out

    @agent.tool
    async def slowest_path(ctx: RunContext[Deps], why: str) -> dict:
        """The slowest combinational path (typical SKY130 delays, an estimate) and the decompiled lines on it."""
        _thought(ctx.deps, "slowest path", why)
        r = await _call(ctx.deps, "critical_path", chip=ctx.deps.chip)
        if isinstance(r, dict):
            r = {k: v for k, v in r.items() if k not in ("cells", "nets")}
            ctx.deps.trail.append({"timing": {k: r.get(k) for k in ("delay_ns_estimate", "ends_at", "lines") if k in r}})
        return r

    return agent


async def investigate(server: FastMCP, chip: str, request: str, *, transcript: Transcript | None = None,
                      model: Any = None, max_refine: int = 1) -> dict:
    agent = build_investigator(model)
    async with Client(server) as client:
        deps = Deps(chip=chip, client=client, server=server, transcript=transcript, model=model, max_refine=max_refine)
        result = await agent.run(f"Chip: {chip}\nRequest: {request}", deps=deps,
                                 usage_limits=UsageLimits(request_limit=REQUEST_LIMIT))
    rep: Report = result.output
    # never let the report claim more than Yosys gave: downgrade 'yosys' findings with no matching verdict
    proven_blocks = {t["explain"]["block"] for t in deps.trail if "explain" in t and t["explain"]["verdict"] in ("proven", "inductive")}
    hunt_ran = any("hunt" in t for t in deps.trail)
    findings = []
    for f in rep.findings:   # a green pill must be backed by this run's tool results (review of #95)
        d = f.model_dump()
        if d["evidence"] == "yosys" and (d.get("block") not in proven_blocks if d.get("block") else not proven_blocks):
            d["evidence"] = "unverified"
        elif d["evidence"] == "proof" and not hunt_ran:
            d["evidence"] = "unverified"
        findings.append(d)
    hunt_v = next((t["hunt"]["verdict"] for t in deps.trail if "hunt" in t), None)
    summary = rep.summary
    if hunt_v in ("inconclusive", "unchecked"):   # the model's prose may still round "tested" up to "clean": correct it, visibly
        findings.append({"text": ("The hidden-logic check is inconclusive: a 20-cycle bounded check passed, but no proof "
                                  "against the author's source was possible, so a rare trigger is not ruled out.")
                         if hunt_v == "inconclusive" else
                         "Hidden logic was not checked: there is no author source to compare against for this chip.",
                         "evidence": "proof", "block": None})
        honest = re.search(r"inconclusive|not proven|tested, not|only (random )?test", summary, re.I)
        if not honest and re.search(r"no hidden|clean|nothing hidden|no (extra|suspicious)|no .*payload", summary, re.I):
            summary += " (Correction: 'no hidden logic' was tested, not proven.)"
    plans = [t["plan"] for t in deps.trail if "plan" in t]
    return {"intent": "investigate", "chip": chip, "summary": summary, "findings": findings,
            "next_steps": rep.next_steps[:3], "plan": plans[-1] if plans else [],
            "explains": [t["explain"] for t in deps.trail if "explain" in t],
            "hunt": next((t["hunt"] for t in deps.trail if "hunt" in t), None),
            "verdict": "done"}
