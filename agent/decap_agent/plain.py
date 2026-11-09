"""Plain English for a checked claim: what the website shows people, written after Yosys has decided.

The region agent's job is a short formal claim, and the checker's `english` is a literal word-for-word reading of
it ("Count0 after the next clock equals ((0 if count0 equals 59, otherwise ...") that is exact but hard to read.
This module adds one LLM call that explains the same fact the way an engineer would to a colleague.

It runs AFTER the verdict, outside the region loop, so it can't change what gets proven and it isn't part of the
bench (the frozen prompt in witness.py is untouched). Guards:
  - it only runs for verdicts with something true to explain (proven, inductive, partial, bounded, restated, tested);
  - every constant it writes above 32 must appear in its source material (the claim, the decompiled lines, the
    checker's reading); one retry with the offending numbers, then it falls back to nothing (the UI shows the
    checker's English instead);
  - interpretation of purpose ("looks like minutes") must be hedged; the formal claim stays visible as the source.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from decap.paths import RUNS

CACHE = RUNS / "cache" / "plain"
VERSION = "plain-v4"
EXPLAINABLE = {"proven", "inductive", "partial", "bounded", "restated", "tested"}

INSTRUCTIONS = """You explain one checked fact about a chip to a sharp engineer who has never seen this chip
(think of a recent computer-science graduate). You get the region's decompiled Verilog, the formal claim Yosys
checked, its verdict, the checker's literal reading of the claim, and a short list of the chip's other blocks.

Write three fields.
- headline: at most 10 words, mechanism only ("A counter that wraps at 59 and pauses when bit0 is set").
  Never put a purpose (seconds, minutes, alarm, password) in the headline; purposes go in the explanation, hedged.
- explanation: 2 or 3 short sentences, at most 60 words. First what it does, then what it is probably for in this
  chip. Group repeated structure ("compares 11 bits of the counters against 11 input pins") instead of listing
  every bit. Use the real signal names (count0, ui_in[3]) and constants (59, 23).
- example: exactly ONE clock step, read straight off the claim: the values before, and what the claim says comes
  next. Example: "count2 = 59, bit0 = 0, count0 = 12  ->  next clock count0 = 13." No story about later clocks.
  For pure wiring (no next()), show inputs -> output with no clock. Leave it empty if the claim gives neither.

Truth rules:
- If the verdict is proven or inductive, what the claim says is proven. State it plainly, without hedging.
- Purpose is your interpretation of names and constants (that count0 holds minutes, that this is an alarm). Mark
  it with "looks like" or "probably".
- If the verdict is partial, bounded, restated or tested, say in one clause what is NOT proven.
- Never write a constant that isn't in the material. Don't invent signal names.
- Don't claim overflow, carries being dropped or wrap-around unless you checked the largest value fits or not.
- Headline, explanation and example must agree with each other. If you are unsure what a register is for, say
  what it counts, not what it means.

Style: plain words, short sentences of varied length. No em dashes. No "not just X but Y". No lists of three for
rhythm. Don't open with "This block" or "This region". Never use: crucial, essentially, simply, seamlessly, robust,
intricate, delve, leverage, ensures, plays a role, serves as, it's worth noting."""

CAVEAT = {
    "proven": "Verdict: proven for every input and every reachable state.",
    "inductive": "Verdict: proven by induction, for every reachable state.",
    "partial": "Verdict: partial. The claim is true but leaves some of this region's outputs or inputs unexplained.",
    "bounded": "Verdict: bounded. It holds for 30 clock cycles after reset; Yosys could not prove it forever.",
    "restated": "Verdict: restated. True, but no shorter than the decompiled code, so it adds little.",
    "tested": "Verdict: tested. It passed random simulation only; it is NOT proven.",
}


class Plain(BaseModel):
    headline: str = Field(description="At most 10 words")
    explanation: str = Field(description="2 to 4 short sentences")
    example: str = Field("", description="One concrete moment with real values, or empty")


def _verilog_numbers(text: str) -> set[int]:
    """Every integer a reader could find in the material: decimals and sized Verilog literals (6'd59, 8'hFF, 4'b1010)."""
    nums = {int(n) for n in re.findall(r"(?<![\w'])\d+(?![\w'])", text)}
    for base, digits in re.findall(r"\d*'([dhbo])([0-9a-fA-F_xz]+)", text):
        try:
            nums.add(int(digits.replace("_", ""), {"d": 10, "h": 16, "b": 2, "o": 8}[base]))
        except ValueError:
            pass
    return nums


def unsupported_numbers(out: Plain, material: str) -> list[int]:
    known = _verilog_numbers(material)
    near = known | {k + 1 for k in known} | {k - 1 for k in known}   # 58 for "wraps after 59", 256 for an 8-bit range
    near |= {1 << k for k in range(33)} | {(1 << k) - 1 for k in range(33)}
    said = _verilog_numbers(" ".join([out.headline, out.explanation, out.example]))
    return sorted(n for n in said if n > 32 and n not in near)


def _material(chip: str, region: dict | None, attempt: dict, blocks: list[dict] | None) -> str:
    lines = [f"Chip: {chip}", f"Formal claim: {attempt.get('claim')}", f"Shape label: {attempt.get('label') or 'none'}",
             f"Checker's literal reading: {attempt.get('english') or '(none)'}",
             f"The agent's own one-line reading: {attempt.get('gist') or '(none)'}"]
    if region:
        ins = ", ".join(f"{i['name']}[{i.get('width', 1)}] ({i.get('kind')})" for i in region.get("inputs", []))
        outs = ", ".join(o["name"] for o in region.get("outputs", []))
        lines += [f"Region inputs: {ins}", f"Region outputs: {outs}", "Decompiled lines:"]
        lines += [f"  {f.get('line')}: {f.get('text')}" for f in (region.get("focus") or [])[:24]]
    if blocks:
        lines.append("Other blocks in the chip:")
        lines += [f"  {b.get('id')} {b.get('kind')} ({b.get('width')} bit): {(b.get('text') or '')[:120]}" for b in blocks[:14]]
    return "\n".join(lines)


def _key(chip: str, attempt: dict, model: str) -> str:
    raw = json.dumps([VERSION, chip, attempt.get("scope"), attempt.get("claim"), attempt.get("verdict"), model], sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def _model_name(model: Any) -> str:
    return model if isinstance(model, str) else getattr(model, "model_name", None) or str(model)


async def explain_plainly(chip: str, attempt: dict, *, region: dict | None = None, blocks: list[dict] | None = None,
                          model: Any = None, cache: bool = True) -> dict | None:
    """{headline, explanation, example, verdict} for the website, or None if there's nothing safe to say."""
    verdict = attempt.get("verdict")
    if verdict not in EXPLAINABLE or not attempt.get("claim"):
        return None
    from .cli_model import resolve
    model = resolve(model, os.environ.get("DECAP_PLAIN_MODEL") or os.environ.get("DECAP_WITNESS_MODEL") or "deepseek:deepseek-chat")
    hit = CACHE / f"{_key(chip, attempt, _model_name(model))}.json"
    if cache and hit.exists():
        try:
            return json.loads(hit.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    material = _material(chip, region, attempt, blocks)
    agent = Agent(model, instructions=INSTRUCTIONS, output_type=Plain, defer_model_check=True, retries=1)
    prompt = f"{material}\n\n{CAVEAT[verdict]}\nExplain it."
    try:
        res = await agent.run(prompt)
        out: Plain = res.output
        bad = unsupported_numbers(out, material)
        if bad:   # one retry, told exactly what it made up
            res = await agent.run(f"Your answer used {bad}, which are not in the material. Rewrite it without them.",
                                  message_history=res.all_messages())
            out = res.output
            if unsupported_numbers(out, material):
                return None
    except Exception:   # an explanation is a nicety: never break the verdict for it
        return None
    plain = {"headline": out.headline.strip(), "explanation": out.explanation.strip(),
             "example": out.example.strip(), "verdict": verdict, "source": "claim"}
    if cache:
        hit.parent.mkdir(parents=True, exist_ok=True)
        hit.write_text(json.dumps(plain, indent=1))
    return plain


async def plain_for_run(client, run: dict, model: Any = None) -> dict | None:
    """Explain a finished explain_region run. Never raises: a missing explanation must not cost the verdict."""
    try:
        return await _plain_for_run(client, run, model)
    except Exception:
        return None


async def _plain_for_run(client, run: dict, model: Any = None) -> dict | None:
    from .scoping import best_attempt
    final = best_attempt(run.get("attempts") or [])
    if final.get("verdict") not in EXPLAINABLE:
        return None
    from .transcript import result_payload

    async def look(tool: str, **args):
        r = await client.call_tool(tool, args, raise_on_error=False)
        return None if getattr(r, "is_error", False) else result_payload(r)
    region = await look("get_region", chip=run["chip"], scope=run.get("scope") or {})
    blocks = await look("recover_blocks", chip=run["chip"])
    return await explain_plainly(run["chip"], {**final, "scope": run.get("scope")},
                                 region=region if isinstance(region, dict) else None,
                                 blocks=blocks.get("blocks") if isinstance(blocks, dict) else None, model=model)


# ---- reading the decompiled code (always available) ---------------------------------------------------------
# The decompiled Verilog is itself proven equal to the chip's gates (the core's decompile verdict). So a plain
# explanation read straight off those lines is grounded in proven code, and it exists for EVERY region, including
# tangled "leftover logic" where no short formal claim can be found. The Witness claim loop then becomes a bonus:
# a shorter summary that Yosys proves on top.

CODE_VERSION = "code-v2"

CODE_INSTRUCTIONS = """You explain one part of a chip to a sharp engineer who has never seen this chip (think of a
recent computer-science graduate). You get the decompiled Verilog lines for this part, the helper wires (tN) they
use, the part's inputs and outputs, and a list of the chip's other blocks. The decompiled code is exact.

Write three fields.
- headline: at most 10 words, the mechanism ("A sticky flag set when the input bits match").
- explanation: 2 to 4 short sentences, at most 80 words. Say what each output does in behaviour: when it goes high
  or low, whether it holds its value (it feeds back into itself), and what it depends on. For a big boolean
  expression, don't transcribe it: summarise its structure (which signals matter, what sets it, what clears it),
  and say "roughly" when you summarise. End with what it is probably for in this chip.
- example: one concrete case read straight off the code, or empty.

Truth rules: only say what the code shows. Purpose (that a counter holds minutes) is a guess: use "probably" or
"looks like". If the logic is too tangled to summarise exactly, say so plainly and describe what it depends on.
Never write a constant that isn't in the material; never invent signal names.

Style: plain words, short sentences of varied length. No em dashes. No "not just X but Y". Don't open with "This
block". Never use: crucial, essentially, simply, seamlessly, robust, intricate, delve, leverage, ensures."""


def _code_material(chip: str, region: dict, defs: dict[str, str], blocks: list[dict] | None) -> str:
    from decap.core.claim_expr import temps_used
    focus = [f for f in region.get("focus") or [] if isinstance(f, dict)]
    lines = [f"Chip: {chip}",
             "Inputs: " + ", ".join(f"{i['name']}[{i.get('width', 1)}] ({i.get('kind')})" for i in region.get("inputs", [])),
             "Outputs: " + ", ".join(o["name"] for o in region.get("outputs", [])),
             "Decompiled lines:"] + [f"  {f.get('line')}: {f.get('text')}" for f in focus[:40]]
    helpers = temps_used([f.get("text", "") for f in focus], defs)
    if helpers:
        lines.append("Helper wires:")
        lines += [f"  {t} = {defs[t]}" for t in helpers[:24]]
    if blocks:
        lines.append("Other blocks in the chip:")
        lines += [f"  {b.get('id')} {b.get('kind')} ({b.get('width')} bit): {(b.get('text') or '')[:100]}" for b in blocks[:14]]
    return "\n".join(lines)


async def explain_code(chip: str, region: dict, *, defs: dict[str, str] | None = None, blocks: list[dict] | None = None,
                       proof: str | None = None, model: Any = None, cache: bool = True) -> dict | None:
    """{headline, explanation, example, verdict: 'code', source: 'code', proof} or None. Never raises."""
    try:
        from .cli_model import resolve
        model = resolve(model, os.environ.get("DECAP_PLAIN_MODEL") or os.environ.get("DECAP_WITNESS_MODEL") or "deepseek:deepseek-chat")
        material = _code_material(chip, region, defs or {}, blocks)
        key = hashlib.sha1(json.dumps([CODE_VERSION, chip, material, _model_name(model)]).encode()).hexdigest()[:20]
        hit = CACHE / f"code-{key}.json"
        if cache and hit.exists():
            return json.loads(hit.read_text())
        agent = Agent(model, instructions=CODE_INSTRUCTIONS, output_type=Plain, defer_model_check=True, retries=1)
        res = await agent.run(f"{material}\n\nExplain this part.")
        out: Plain = res.output
        bad = unsupported_numbers(out, material)
        if bad:
            res = await agent.run(f"Your answer used {bad}, which are not in the material. Rewrite it without them.",
                                  message_history=res.all_messages())
            out = res.output
            if unsupported_numbers(out, material):
                return None
        reading = {"headline": out.headline.strip(), "explanation": out.explanation.strip(), "example": out.example.strip(),
                   "verdict": "code", "source": "code", "proof": proof}
        if cache:
            hit.parent.mkdir(parents=True, exist_ok=True)
            hit.write_text(json.dumps(reading, indent=1))
        return reading
    except Exception:
        return None


async def reading_for_scope(client, chip: str, scope: dict, model: Any = None) -> dict | None:
    """Fetch the region, the decompiled Verilog (helper wires + its proof verdict) and the block list, then read it."""
    try:
        from decap.core.claim_expr import temp_defs
        from .transcript import result_payload

        async def look(tool: str, **args):
            r = await client.call_tool(tool, args, raise_on_error=False)
            return None if getattr(r, "is_error", False) else result_payload(r)
        region = await look("get_region", chip=chip, scope=scope)
        dec = await look("decompile", chip=chip)
        blocks = await look("recover_blocks", chip=chip)
        if not isinstance(region, dict):   # some blocks (pure copies) have no region: explain the block's own line
            bid = scope.get("cone_of") if isinstance(scope, dict) else None
            b = next((x for x in (blocks or {}).get("blocks", []) if x.get("id") == bid), None) if isinstance(blocks, dict) else None
            if not b:
                return None
            region = {"inputs": [], "outputs": [], "focus": [{"line": (b.get("lines") or [0])[0], "text": b.get("text", "")}]}
        return await explain_code(chip, region, defs=temp_defs(dec.get("verilog", "")) if isinstance(dec, dict) else {},
                                  blocks=blocks.get("blocks") if isinstance(blocks, dict) else None,
                                  proof=dec.get("verdict") if isinstance(dec, dict) else None, model=model)
    except Exception:
        return None
