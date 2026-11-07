"""Decap Witness: the region agent loop (docs/decap/research/witness-plan.md, section 2).

    judge drops an agent on a region
      -> the LLM LOOKS (get_region, probe_truth, simulate): free, never decides anything
      -> the LLM returns ONE claim (a Verilog expression + an optional shape label)
      -> this code calls propose_claim: only the checker (Yosys in the real core) gives a verdict
      -> refuted / tautology / unparseable / restated: the verdict goes back to the LLM, max 2 refinements

The LLM cannot call propose_claim itself (its toolset is filtered to the look tools), so every claim is
counted: attempt 0 is the FIRST DRAFT, made with a simulator and no proof. Its error rate is the
headline number in the plan (section 4). Each attempt is a kind="claim" transcript event carrying the
region's cells, so the 3D view and the timeline can light it up.

Results are cached by (netlist_sha, decompiler, chip, scope, model, prompt version): the overnight
bench pre-warms the demo, and a cached run replays its events marked cached=True.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from fastmcp import Client, FastMCP
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

from .transcript import Transcript, result_payload

DEFAULT_MODEL = "anthropic:claude-sonnet-5-5"
LOOK_TOOLS = {"get_region", "probe_truth", "simulate", "resolve_scope"}
DONE = {"proven", "inductive", "bounded"}      # verdicts that end the loop
PROMPT_VERSION = "witness-v1"

INSTRUCTIONS = """You explain one region of a real chip in ONE short claim. A formal checker (Yosys) then tries
to prove the claim against the chip's gates. It is shown to people only if it is proven, so a wrong claim
costs you: it comes back refuted with the input that breaks it.

How to work:
1. Call get_region with the scope you are given. It lists the names you may use and the decompiled code.
2. Call probe_truth on the same scope to see what the outputs actually do. Samples are evidence, not proof:
   rare cases (a value of 255, one input pattern in 256) are easy to miss, so think about edge cases.
3. Return one claim about the region's outputs.

The claim is a Verilog expression:
- Names: exactly the ports and flip-flops get_region lists (with [i] or [hi:lo]), or a register alias it
  gives. next(x) is x one clock later. 'A when B' means: whenever B, A holds.
- rst_n = 1 and ena = 1 are assumed (the chip is running, not in reset).
- Verilog widths apply: an unsized number such as 1 is 32 bits wide and does not wrap; write 8'd1 when an
  8-bit value should wrap around.
- Make it much shorter than the decompiled code. Name the shape in `label` if it is one of: adder,
  incrementer, counter, comparator, mux, decoder, constant. A claim that only restates the decompiled code
  is proven but earns nothing; a claim true of any chip (a tautology) is rejected.
- `gist` is your own one-line plain-English reading. The checker writes the English that people see."""


class Claim(BaseModel):
    """One claim about the region."""
    claim: str = Field(description="One Verilog expression about the region, e.g. \"next(count) == count + 8'd1 when en\".")
    label: str = Field("", description="adder, incrementer, counter, comparator, mux, decoder, constant, or empty.")
    gist: str = Field("", description="Your one-line plain-English reading of the claim.")


def build_region_agent(server: FastMCP, model: Any = None) -> Agent:
    look = MCPToolset(server).filtered(lambda ctx, tool: tool.name in LOOK_TOOLS)
    return Agent(model or os.environ.get("DECAP_WITNESS_MODEL", DEFAULT_MODEL), instructions=INSTRUCTIONS,
                 toolsets=[look], output_type=Claim, defer_model_check=True)


def feedback(verdict: dict) -> str:
    """What the LLM is told after a claim that did not end the loop."""
    v = verdict.get("verdict")
    if v == "refuted":
        return (f"REFUTED. Counterexample (rst_n=1, ena=1): {json.dumps(verdict.get('counterexample'))}. "
                f"Your claim is false for that input; the values shown are what the chip really does. "
                f"Refine the claim so it holds for every input, including this one.")
    if v == "tautology":
        return "REJECTED as a tautology: it is true of any chip, so it says nothing about this region. Claim something specific."
    if v == "unparseable":
        return f"UNPARSEABLE: {verdict.get('error')}. Fix the syntax or the names (use get_region's names)."
    if v == "restated":
        credit = verdict.get("credit") or {}
        return (f"PROVEN but no credit: {credit.get('reason')}. Say the same thing more compactly, or name its shape "
                f"in `label` if it really is one.")
    return f"The checker returned {v!r}: {json.dumps(verdict)[:300]}. Try a simpler claim."


def cache_key(region: dict, chip: str, scope: dict, model_name: str) -> str:
    raw = json.dumps({"netlist_sha": region.get("netlist_sha"), "decompiler": region.get("decompiler"), "chip": chip,
                      "scope": scope, "model": model_name, "prompt": PROMPT_VERSION,
                      "instructions": hashlib.sha256(INSTRUCTIONS.encode()).hexdigest()[:12]}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _model_name(agent: Agent) -> str:
    m = agent.model
    return getattr(m, "model_name", None) or str(m)


async def explain_region(chip: str, scope: dict, server: FastMCP, *, model: Any = None,
                         transcript: Transcript | None = None, max_refine: int = 2,
                         cache_dir: Path | None = None) -> dict:
    """Run one region agent: look, claim, check, refine. Returns the attempts and the final verdict."""
    agent = build_region_agent(server, model)
    model_name = _model_name(agent)
    async with Client(server) as client:
        async def call(tool: str, **args) -> dict:
            r = await client.call_tool(tool, args, raise_on_error=False)
            payload = result_payload(r)
            if r.is_error:
                raise RuntimeError(f"{tool} failed: {payload}")
            return payload

        region = await call("get_region", chip=chip, scope=scope)
        key = cache_key(region, chip, scope, model_name)
        hit = cache_dir / f"{key}.json" if cache_dir else None
        if hit and hit.exists():
            run = json.loads(hit.read_text())
            if transcript is not None:
                for a in run["attempts"]:
                    transcript.record("claim", **{**a, "cached": True})
            return {**run, "cached": True}

        prompt = (f"Chip {chip!r}. Your region: scope = {json.dumps(scope)}. Pass exactly this scope to the tools. "
                  f"Explain the region in one claim.")
        history, attempts = None, []
        for attempt in range(1 + max_refine):
            t0 = time.perf_counter()
            res = await agent.run(prompt, message_history=history)
            claim: Claim = res.output
            verdict = await call("propose_claim", chip=chip, scope=scope, claim=claim.claim, label=claim.label)
            usage = res.usage
            row = {
                "chip": chip, "scope": scope, "attempt": attempt, "first_draft": attempt == 0,
                "claim": claim.claim, "label": claim.label, "gist": claim.gist,
                "verdict": verdict.get("verdict"), "formal": verdict.get("formal"), "english": verdict.get("english"),
                "counterexample": verdict.get("counterexample"), "refute_kind": verdict.get("refute_kind"),
                "error": verdict.get("error"), "credit": verdict.get("credit"),
                "cells": (verdict.get("touched") or {}).get("cells", []),
                "model": model_name, "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
                "look_calls": getattr(usage, "tool_calls", None), "wall_s": round(time.perf_counter() - t0, 3),
                "source": verdict.get("source", "core"),
            }
            attempts.append(row)
            if transcript is not None:
                transcript.record("claim", **row)
            if row["verdict"] in DONE or row["verdict"] == "timeout":
                break
            history, prompt = res.all_messages(), feedback(verdict)

    final = attempts[-1]
    run = {"chip": chip, "scope": scope, "key": key, "model": model_name, "prompt": PROMPT_VERSION,
           "netlist_sha": region.get("netlist_sha"), "decompiler": region.get("decompiler"),
           "cone_literals": region.get("cone_literals"),
           "first_draft": attempts[0]["verdict"], "final": final["verdict"],
           "explained": final["verdict"] in DONE and bool((final.get("credit") or {}).get("cleared")),
           "attempts": attempts}
    if hit:
        hit.parent.mkdir(parents=True, exist_ok=True)
        hit.write_text(json.dumps(run, indent=1))
    return {**run, "cached": False}
