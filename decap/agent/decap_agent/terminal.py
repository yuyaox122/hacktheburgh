"""The bASIC terminal: a natural-language command that drives the chip agent — the `dimos agent-send` equivalent.

    decap agent-send "is anything hidden in this chip?" --chip tt_um_kapilan_alarm

You type an intent; the terminal picks the right flow and runs it through the deterministic tools, with Yosys as
the safety layer. Every step is a transcript event, so the UI can stream it. Nothing here is a raw-prompt answer:
any "proven" claim came from `propose_claim`, any "hidden logic found" came from a `prove_equiv` counterexample.

Intents (keyword-dispatch, honest about what it is):
  hunt    "hidden / trigger / trojan / suspicious" -> find_rare_triggers, then prove each unexplained flag vs the
          author's source (a planted trigger is exposed by a failing proof with a counterexample).
  explain "explain / what does / optimize / suggest" -> resolve the named region (or the whole chip) and run the
          region agent (look -> claim -> Yosys checks -> refine).
  investigate  "audit / review / walk me through / ... and ...?" -> the investigator (investigate.py): plans,
          picks skills from what it sees, writes chip memory, ends with an evidence-tagged report.
  drill   "red team / plant / test yourself" -> plant a trigger at a secret value, hunt it blind, reveal.
  guide   anything else -> the guide agent answers with facts, quoted from tool results.
"""

from __future__ import annotations

import re
from typing import Any

from fastmcp import Client, FastMCP

from . import lifecycle
from .transcript import Transcript, result_payload

DRILL = re.compile(r"drill|red.?team|plant|test (yourself|the hunt)|prove it works", re.I)
HUNT = re.compile(r"hidden|trigger|trojan|suspicious|backdoor|planted|anything (extra|wrong|else|weird)", re.I)
INVESTIGATE = re.compile(r"audit|investigat|review|report|overview|walk me|tour|everything|plan|deep|\bthen\b|\band\b.*\?|"
                         r"slow|speed|timing|critical|faster|memory|remember|what do (you|we) know|"
                         r"\btest(ing)?\b|\bcheck\b|\bverify\b|analy[sz]|continue|keep going|next step", re.I)
EXPLAIN = re.compile(r"explain|what does|optimize|optimise|suggest|understand|how does|summari", re.I)


async def _call(client: Client, tool: str, **args) -> tuple[bool, Any]:
    r = await client.call_tool(tool, args, raise_on_error=False)
    return bool(getattr(r, "is_error", False)), result_payload(r)


async def _hunt(client: Client, chip: str, transcript: Transcript | None) -> dict:
    """The AGENTS.md 'is anything hidden here?' recipe, as a terminal command.

    find_rare_triggers lists the candidates; the whole-chip proof vs the author's source is the verdict
    (the proof never takes a scope — it's whole-chip by design). A `failed` proof means extra logic exists,
    and its counterexample is the input that wakes it."""
    err, flags = await _call(client, "find_rare_triggers", chip=chip)
    rows = [] if err else flags.get("flagged", [])
    err2, proof = await _call(client, "prove_equiv", chip=chip, against="author_source")
    if err2:
        proof = {"error": str(proof)}
    found = proof.get("verdict") in ("failed", "refuted")
    # 'clean' needs a proof. 'tested' is random simulation: a 1-in-2^17 trigger can slip past it, so say so.
    verdict = "found" if found else "clean" if proof.get("verdict") in ("proven", "inductive") else "inconclusive"
    if transcript is not None:
        transcript.record("note", intent="hunt", chip=chip,
                          flags=[r.get("condition") for r in rows],
                          result={"verdict": proof.get("verdict"), "counterexample": proof.get("counterexample")})
    return {"intent": "hunt", "chip": chip, "flags": rows,
            "verdict": verdict,
            "proof": proof.get("verdict", proof.get("error")),
            "counterexample": proof.get("counterexample"),
            "note": ("a counterexample here is the exact input that wakes the hidden logic" if found else
                     None if verdict == "clean" else
                     "no proof was possible (only random testing passed); rare flags above are still open")}


async def _drill(client: Client, chip: str, transcript: Transcript | None) -> dict:
    """Red-team drill: plant a trigger at a secret random value in a copy of the chip, then hunt it blind.

    The hunt never sees N: find_rare_triggers and prove_equiv only get the planted chip id (names are re-stripped,
    so nothing marks the new cells). The drill passes only if the proof's counterexample is exactly N."""
    import secrets
    n = secrets.randbelow(254) + 1
    if "+t" in chip:
        chip = chip.split("+t")[0]
    err, planted = await _call(client, "plant_trigger", chip=chip, n=n)
    if err:
        return {"intent": "drill", "chip": chip, "verdict": "error", "error": str(planted)[:300]}
    target = planted["chip"]
    if transcript is not None:
        transcript.record("thought", step="drill", why=f"planted a hidden trigger in a copy ({target.split('+')[0]}+t?, "
                                                        f"{planted.get('inserted_cells')} cells, value sealed); now hunting it blind")
    res = await _hunt(client, target, transcript)
    got = (res.get("counterexample") or {}).get("ui_in")
    caught = res["verdict"] == "found" and got == n
    return {**res, "intent": "drill", "base": chip, "secret": n, "found_value": got, "caught": caught,
            "verdict": "caught" if caught else "missed",
            "note": (f"planted at ui_in == {n}; the proof's counterexample is ui_in == {got}" if got is not None
                     else f"planted at ui_in == {n}; no counterexample came back")}


def _intent(command: str) -> str:
    """Single-skill commands take the fast fixed flow; compound or open-ended ones go to the investigator,
    which plans its own steps. A bare question with no chip verb falls through to the guide."""
    if DRILL.search(command):
        return "drill"
    if INVESTIGATE.search(command):
        return "investigate"
    if HUNT.search(command):
        return "hunt"
    if EXPLAIN.search(command):
        return "explain"
    return "guide"


async def _check_source(chip: str, verilog: str, filename: str, transcript: Transcript | None) -> dict:
    """Your own Verilog vs the chip's gates (source_check.py). Not an MCP tool, so the event is recorded here."""
    import asyncio
    import time
    from .source_check import check_verilog
    t0 = time.perf_counter()
    res = await asyncio.to_thread(check_verilog, chip, verilog, filename)
    if transcript is not None:
        transcript.record("tool", tool="prove_equiv", args={"chip": chip, "against": filename}, is_error=res["verdict"] == "unavailable",
                          wall_s=round(time.perf_counter() - t0, 3), summary=res["verdict"] + (" (bounded, not a proof)" if res["verdict"] == "tested" else ""))
    return {"intent": "check_source", "chip": chip, "filename": filename, **res}


async def run_command(server: FastMCP, chip: str, command: str, *, transcript: Transcript | None = None,
                      model: Any = None, max_refine: int = 2, verilog: str | None = None,
                      filename: str | None = None) -> dict:
    """Interpret one natural-language command and run the matching flow (tracked as a session)."""
    kind = "check_source" if verilog else _intent(command)
    sid = lifecycle.start(chip, kind, command, transcript=transcript) if transcript is not None else None
    try:
        result = (await _check_source(chip, verilog, filename or "upload.v", transcript) if verilog else
                  await _run_command(server, chip, command, transcript=transcript, model=model, max_refine=max_refine))
    except Exception as exc:
        if sid:
            lifecycle.finish(sid, "error", transcript=transcript, error=f"{type(exc).__name__}: {exc}")
        raise
    if sid:
        lifecycle.finish(sid, "done", transcript=transcript,
                         intent=result.get("intent"), verdict=result.get("verdict"))
    return result


async def _run_command(server: FastMCP, chip: str, command: str, *, transcript: Transcript | None,
                       model: Any, max_refine: int) -> dict:
    """The dispatch, moved out of run_command so the lifecycle wrapper stays small."""
    kind = _intent(command)
    if kind == "investigate":
        from .investigate import investigate
        return await investigate(server, chip, command, transcript=transcript, model=model,
                                 max_refine=min(max_refine, 2))
    async with Client(server) as client:
        if kind == "drill":
            return await _drill(client, chip, transcript)
        if kind == "hunt":
            return await _hunt(client, chip, transcript)

        if kind == "explain":
            from .witness import explain_region
            # name a region if the command mentions a block/output, else the whole chip
            block = re.search(r"\b(b\d+)\b", command)
            out = re.search(r"\b(u[io]_out|u[io]_in)(?:\[\d+(?::\d+)?\])?", command)
            scope = {"cone_of": block.group(1)} if block else ({"cone_of": out.group(0)} if out else {})
            from .scoping import best_attempt, mcp_caller, narrow_scope
            scope, why = await narrow_scope(mcp_caller(client), chip, scope)
            if why and transcript is not None:
                transcript.record("thought", step="aim", why=why)
            run = await explain_region(chip, scope, server, model=model, transcript=transcript,
                                       max_refine=max_refine, register=False)
            final = best_attempt(run["attempts"])
            from .plain import plain_for_run, reading_for_scope
            plain = await plain_for_run(client, run, model=model) or await reading_for_scope(client, chip, scope, model=model)
            return {"intent": "explain", "chip": chip, "scope": scope, "plain": plain,
                    "verdict": final.get("verdict"), "claim": final.get("claim"), "english": final.get("english"),
                    "counterexample": final.get("counterexample"), "explained": final.get("verdict") in ("proven", "inductive") and bool((final.get("credit") or {}).get("cleared")),
                    "attempts": len(run["attempts"])}

        from .guide import ask
        answer = await ask(command, server, transcript, model=model, chip=chip)
        return {"intent": "guide", "chip": chip, "answer": answer}
