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
  guide   anything else -> the guide agent answers with facts, quoted from tool results.
"""

from __future__ import annotations

import re
from typing import Any

from fastmcp import Client, FastMCP

from . import lifecycle
from .transcript import Transcript, result_payload

HUNT = re.compile(r"hidden|trigger|trojan|suspicious|backdoor|planted|anything (extra|wrong|else|weird)", re.I)
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
    if transcript is not None:
        transcript.record("note", intent="hunt", chip=chip,
                          flags=[r.get("condition") for r in rows],
                          result={"verdict": proof.get("verdict"), "counterexample": proof.get("counterexample")})
    return {"intent": "hunt", "chip": chip, "flags": rows,
            "verdict": "found" if found else "clean",
            "proof": proof.get("verdict", proof.get("error")),
            "counterexample": proof.get("counterexample"),
            "note": "a counterexample here is the exact input that wakes the hidden logic" if found else None}


async def run_command(server: FastMCP, chip: str, command: str, *, transcript: Transcript | None = None,
                      model: Any = None, max_refine: int = 2) -> dict:
    """Interpret one natural-language command and run the matching flow (tracked as a session)."""
    kind = "hunt" if HUNT.search(command) else "explain" if EXPLAIN.search(command) else "guide"
    sid = lifecycle.start(chip, kind, command, transcript=transcript) if transcript is not None else None
    try:
        result = await _run_command(server, chip, command, transcript=transcript, model=model, max_refine=max_refine)
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
    async with Client(server) as client:
        if HUNT.search(command):
            return await _hunt(client, chip, transcript)

        if EXPLAIN.search(command):
            from .witness import explain_region
            # name a region if the command mentions a block/output, else the whole chip
            block = re.search(r"\b(b\d+)\b", command)
            out = re.search(r"\b(u[io]_out|u[io]_in)(?:\[\d+(?::\d+)?\])?", command)
            scope = {"cone_of": block.group(1)} if block else ({"cone_of": out.group(0)} if out else {})
            run = await explain_region(chip, scope, server, model=model, transcript=transcript,
                                       max_refine=max_refine, register=False)
            final = run["attempts"][-1]
            return {"intent": "explain", "chip": chip, "scope": scope,
                    "verdict": run["final"], "claim": final.get("claim"), "english": final.get("english"),
                    "counterexample": final.get("counterexample"), "explained": run["explained"],
                    "attempts": len(run["attempts"])}

        from .guide import ask
        answer = await ask(command, server, transcript, model=model)
        return {"intent": "guide", "chip": chip, "answer": answer}
