"""Decap Witness: the region agent loop (docs/decap/witness-plan.md, section 2).

    judge drops an agent on a region
      -> the controller fetches the region + a first probe into the prompt; the LLM may probe twice more (`fix`)
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
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.usage import UsageLimits

from decap.core.claim_expr import expand_temps, fix_mux_precedence, temp_defs, temps_used

from . import lifecycle
from .cli_model import SONNET
from .transcript import Transcript, result_payload

DEFAULT_MODEL = SONNET
LOOK_TOOLS = {"probe_truth"}          # the region and a first probe are already in the prompt
LIMITS = UsageLimits(request_limit=6, input_tokens_limit=120_000)   # per attempt: a 23-bit region cost 1.37M tokens uncapped
# `verdict` is the credit outcome (core/claims.py, CONTRACTS §4): proven/inductive only when credited; `truth` says
# whether the claim holds. 'tested' = credited by the fallback simulation checker (never a proof).
DONE = {"proven", "inductive", "tested"}   # credited: the loop ends
PROOF = {"proven", "inductive"}
PROMPT_VERSION = "witness-v7"

INSTRUCTIONS = """You explain one region of a real chip in ONE short claim. A formal checker (Yosys) then tries
to prove the claim against the chip's gates. It is shown to people only if it is proven, so a wrong claim
costs you: it comes back refuted with the input that breaks it.

How to work:
1. Read the region (its inputs, outputs, decompiled code) and the probe samples given to you.
2. If you need more evidence, call probe_truth at most twice, e.g. with `fix` to hold some inputs at values
   ({"bit0": 0}) and see what the outputs do then. Samples are evidence, not proof: rare cases (a value of 255,
   one input pattern in 256) are easy to miss, so think about edge cases.
3. Return one claim about the region's outputs.

The claim is a Verilog expression:
- ONE expression, nothing else. No semicolons, no `=` assignments, no commas, no intermediate variables and no
  prose or asides (no "i.e.", "where", "in the samples", "bit N of the sum"). Join parts with `&&`.
- Names: exactly the ports and flip-flops the region lists (with [i] or [hi:lo]), or a register alias it
  gives. next(x) is x one clock later. 'A when B' means: whenever B, A holds. Never invent a name.
- rst_n = 1 and ena = 1 are assumed (the chip is running, not in reset).
- Verilog widths apply: an unsized number such as 1 is 32 bits wide and does not wrap; write 8'd1 when an
  8-bit value should wrap around.
- Cover EVERY output of the region (all of them, joined with &&): a claim about one easy output (e.g. a pin that
  just copies a register) while skipping the logic that computes another earns nothing.
- Say what the output IS for every input, in the form `output == expression` (use ?: for cases). A true but
  partial fact ("once set it stays set", "x == 5 when en") is rejected as partial: it leaves the output open for
  some inputs. For a register, describe next(reg) completely, including when it holds its value.
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
    from .cli_model import resolve
    return Agent(resolve(model, os.environ.get("DECAP_WITNESS_MODEL", DEFAULT_MODEL)), instructions=INSTRUCTIONS,
                 toolsets=[look], output_type=Claim, defer_model_check=True)


def feedback(verdict: dict) -> str:
    """What the LLM is told after a claim that did not end the loop."""
    v = verdict.get("verdict")
    if v == "refuted":
        return (f"REFUTED ({verdict.get('refute_kind') or 'semantic'}). Counterexample (rst_n=1, ena=1, registers as "
                f"shown): {json.dumps(verdict.get('counterexample'))}. "
                f"Your claim is false for that input; the values shown are what the chip really does. "
                f"Refine the claim so it holds for every input, including this one.")
    if v == "tautology":
        return "REJECTED as a tautology: it is true of any chip, so it says nothing about this region. Claim something specific."
    if v == "unparseable":
        return f"UNPARSEABLE: {verdict.get('error')}. Fix the syntax or the names (use get_region's names)."
    if v == "partial" and "missing" in str((verdict.get("credit") or {}).get("reason")):
        return (f"TRUE but INCOMPLETE, so no credit: {verdict['credit']['reason']}. Add a conjunct (&&) that says what "
                "each missing output is, as `output == expression`.")
    if v == "partial":
        return ("TRUE but PARTIAL, so no credit: for some inputs your claim allows more than one value of the output. "
                "Write `output == expression` that gives the output for EVERY input (use ?: for the cases), e.g. "
                "next(r) == (cond ? new_value : r) for a register that holds.")
    if v == "bounded":
        return ("NOT PROVEN: it holds for 30 cycles after reset, but fails for some register values the chip never "
                f"reaches (counterexample with free registers: {json.dumps(verdict.get('counterexample'))}). Make it hold "
                "for every register value, e.g. describe next() as a function of the current registers and inputs.")
    if v == "restated":
        credit = verdict.get("credit") or {}
        return (f"PROVEN but no credit: {credit.get('reason')}. Say the same thing more compactly, or name its shape "
                f"in `label` if it really is one.")
    return f"The checker returned {v!r}: {json.dumps(verdict)[:300]}. Try a simpler claim."


def brief(region: dict, probe: dict) -> str:
    """The region and a first probe, compact, for the first prompt (saves the agent a dozen look calls)."""
    ins = ", ".join(f"{i['name']}[{i['width']}]" + (f" bits {i['bits_used']}" if i.get("bits_used") else "")
                    for i in region.get("inputs", []))
    outs = ", ".join(f"{o['name']}[{o['width']}]" + (f" bits {o['bits_in_region']}" if o.get("bits_in_region") else "")
                     for o in region.get("outputs", []))
    focus = "\n".join(f"  {f['line'] if isinstance(f, dict) else ''}: {f['text'] if isinstance(f, dict) else f}"
                      for f in region.get("focus", []))
    rows = probe.get("samples", [])[:24]
    cols_in = list(rows[0]["in"]) if rows else []
    cols_out = list(rows[0]["out"]) if rows else []
    table = "\n".join("  " + " ".join(str(r["in"][c]) for c in cols_in) + " | " + " ".join(str(r["out"][c]) for c in cols_out)
                      for r in rows)
    return (f"Region inputs: {ins}\nRegion outputs: {outs}\n"
            f"Decompiled code for it ({region.get('cone_literals')} literals):\n{focus}\n"
            f"Probe: {probe.get('vectors')} vectors, {'every input' if probe.get('exhaustive') else 'random'}; "
            f"assumed {json.dumps(probe.get('assumed'))}.\nOutput stats: {json.dumps(probe.get('stats'))}\n"
            f"Samples ({' '.join(cols_in)} | {' '.join(cols_out)}):\n{table}")


def cache_key(region: dict, chip: str, scope: dict, model_name: str, max_refine: int = 2) -> str:
    raw = json.dumps({"netlist_sha": region.get("netlist_sha"), "decompiler": region.get("decompiler"), "chip": chip,
                      "scope": scope, "model": model_name, "prompt": PROMPT_VERSION, "max_refine": max_refine,
                      "instructions": hashlib.sha256(INSTRUCTIONS.encode()).hexdigest()[:12]}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _model_name(agent: Agent) -> str:
    m = agent.model
    return getattr(m, "model_name", None) or str(m)


async def explain_region(chip: str, scope: dict, server: FastMCP, *, model: Any = None,
                         transcript: Transcript | None = None, max_refine: int = 2,
                         cache_dir: Path | None = None, register: bool = True) -> dict:
    """Run one region agent: look, claim, check, refine. Returns the attempts and the final verdict.

    With `register` (default) the run is tracked as a terminal session; callers that already
    register their own session (run_command) or that fan out many regions (bench) pass register=False.
    """
    sid = lifecycle.start(chip, "explain", None, transcript=transcript) if (register and transcript is not None) else None
    try:
        run = await _explain_region(chip, scope, server, model=model, transcript=transcript,
                                    max_refine=max_refine, cache_dir=cache_dir)
    except Exception as exc:
        if sid:
            lifecycle.finish(sid, "error", transcript=transcript, error=f"{type(exc).__name__}: {exc}")
        raise
    if sid:
        lifecycle.finish(sid, "done", transcript=transcript, intent="explain", verdict=run.get("final"))
    return run


async def _explain_region(chip: str, scope: dict, server: FastMCP, *, model: Any = None,
                          transcript: Transcript | None = None, max_refine: int = 2,
                          cache_dir: Path | None = None) -> dict:
    """The region loop (moved out of explain_region so the session wrapper stays small)."""
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
        key = cache_key(region, chip, scope, model_name, max_refine)
        hit = cache_dir / f"{key}.json" if cache_dir else None
        if hit and hit.exists():
            run = json.loads(hit.read_text())
            if transcript is not None:
                for a in run["attempts"]:
                    transcript.record("claim", **{**a, "cached": True})
            return {**run, "cached": True}

        probe = await call("probe_truth", chip=chip, scope=scope, n=1024)
        # The decompiled lines use helper wires (t5 = ...). Show their definitions, and let the claim use the names:
        # the controller expands them before the checker sees the claim, so what is checked is fully explicit.
        try:
            defs = temp_defs((await call("decompile", chip=chip)).get("verilog", ""))
        except Exception:
            defs = {}
        helpers = temps_used([f.get("text", "") if isinstance(f, dict) else str(f) for f in region.get("focus") or []], defs)
        helper_txt = ("\nHelper wires used by those lines (you may write these names in the claim; they are expanded "
                      "before checking):\n" + "\n".join(f"  {t} = {defs[t]}" for t in helpers[:24])) if helpers else ""
        prompt = (f"Chip {chip!r}. Your region: scope = {json.dumps(scope)} (pass exactly this scope to probe_truth).\n"
                  f"{brief(region, probe)}{helper_txt}\nExplain the region in one claim.")
        history, attempts = None, []
        for attempt in range(1 + max_refine):
            t0 = time.perf_counter()
            try:
                res = await agent.run(prompt, message_history=history, usage_limits=LIMITS)
            except UnexpectedModelBehavior as e:   # a malformed reply is a failed attempt, not a crashed run
                row = {"chip": chip, "scope": scope, "attempt": attempt, "first_draft": attempt == 0, "claim": None,
                       "verdict": "no_claim", "error": f"the model's reply was malformed: {str(e)[:200]}", "model": model_name,
                       "wall_s": round(time.perf_counter() - t0, 3), "cells": [], "credit": None, "counterexample": None,
                       "english": None, "label": "", "source": region.get("source", "core")}
                attempts.append(row)
                if transcript is not None:
                    transcript.record("claim", **row)
                break
            except UsageLimitExceeded as e:
                row = {"chip": chip, "scope": scope, "attempt": attempt, "first_draft": attempt == 0, "claim": None,
                       "verdict": "no_claim", "error": f"look budget exceeded: {e}", "model": model_name,
                       "wall_s": round(time.perf_counter() - t0, 3), "cells": [], "credit": None, "counterexample": None,
                       "english": None, "label": "", "source": region.get("source", "core")}
                attempts.append(row)
                if transcript is not None:
                    transcript.record("claim", **row)
                break
            claim: Claim = res.output
            checked = fix_mux_precedence(expand_temps(claim.claim, defs) if defs else claim.claim)
            verdict = await call("propose_claim", chip=chip, scope=scope, claim=checked, label=claim.label)
            usage = res.usage
            row = {
                "chip": chip, "scope": scope, "attempt": attempt, "first_draft": attempt == 0,
                "claim": checked, "claim_written": claim.claim if checked != claim.claim else None,
                "label": claim.label, "gist": claim.gist,
                "verdict": verdict.get("verdict"), "truth": verdict.get("truth"), "formal": verdict.get("formal"),
                "english": verdict.get("english"), "checker_s": verdict.get("seconds"),
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
           "first_truth": attempts[0].get("truth"), "first_refute_kind": attempts[0].get("refute_kind"),
           "explained": final["verdict"] in DONE and bool((final.get("credit") or {}).get("cleared")),
           "proof": final["verdict"] in PROOF,
           "attempts": attempts}
    if hit:
        hit.parent.mkdir(parents=True, exist_ok=True)
        hit.write_text(json.dumps(run, indent=1))
    return {**run, "cached": False}
