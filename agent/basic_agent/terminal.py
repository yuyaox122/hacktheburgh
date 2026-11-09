"""The bASIC terminal: a natural-language command that drives the chip agent — the `dimos agent-send` equivalent.

    basic agent-send "is anything hidden in this chip?" --chip tt_um_kapilan_alarm

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

from basic.core.service import parse_id

from . import lifecycle
from .transcript import Transcript, result_payload

FAULT = re.compile(r"fault drill|break (a|one) gate|inject (a )?fault|switch (a|one) gate|repair drill|mutat", re.I)
# Explicit drill wording only: "was a backdoor planted here?" is a hunt on THIS chip, not a drill (review of #95).
DRILL = re.compile(r"\bdrill\b|red.?team|test (yourself|the hunt)|prove (that )?it works", re.I)
HUNT = re.compile(r"hidden|trigger|trojan|suspicious|backdoor|planted|anything (extra|wrong|else|weird)", re.I)
INVESTIGATE = re.compile(r"audit|investigat|review|report|overview|walk me|tour|everything|\bplan\b|deep|\bthen\b|\band\b.*\?|"
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
        proof = {"error": str(proof)[:300]}
    v = proof.get("verdict")
    found = v in ("failed", "refuted")
    # clean needs a proof. 'tested' = a 20-cycle bounded check from reset (Yosys SAT), not a proof. 'unavailable' or
    # an error means nothing was checked against the author's source at all, so make no testing claim.
    verdict = ("found" if found else "clean" if v in ("proven", "inductive") else
               "inconclusive" if v == "tested" else "unchecked")
    if transcript is not None:
        transcript.record("note", intent="hunt", chip=chip,
                          flags=[r.get("condition") for r in rows],
                          result={"verdict": proof.get("verdict"), "counterexample": proof.get("counterexample")})
    return {"intent": "hunt", "chip": chip, "flags": rows,
            "verdict": verdict,
            "proof": proof.get("verdict", proof.get("error")),
            "counterexample": proof.get("counterexample"),
            "note": ("the proof failed: the chip does something its author's source doesn't" if found else
                     None if verdict == "clean" else
                     "only a 20-cycle bounded check from reset passed, which is not a proof; rare flags above are still open"
                     if verdict == "inconclusive" else
                     "nothing could be checked against the author's source (" + str(proof.get("detail") or proof.get("error")
                     or "no source")[:160] + "); the rare flags above are unexamined")}


async def _drill(client: Client, chip: str, transcript: Transcript | None) -> dict:
    """Red-team drill: plant a trigger at a secret random value in a copy of the chip, then hunt it blind.

    The hunting tools never get N: find_rare_triggers and prove_equiv only get the planted chip id (names are
    re-stripped, so nothing marks the new cells). Caught = the proof against the author's source FAILS (something
    extra exists) AND a flagged rare condition names N. The proof's counterexample alone is not used: on real chips it
    is the last step of a 20-cycle trace, not the trigger value (review of #95: tt_um_mac+t163 gave ui_in=23)."""
    import secrets
    n = secrets.randbelow(254) + 1
    chip = parse_id(chip)[0]   # plant into the clean chip
    err, planted = await _call(client, "plant_trigger", chip=chip, n=n)
    if err:
        return {"intent": "drill", "chip": chip, "verdict": "error", "error": str(planted)[:300]}
    target = planted["chip"]
    if transcript is not None:
        transcript.record("thought", step="drill", why=f"planted a hidden trigger in a copy ({parse_id(target)[0]}+t?, "
                                                        f"{planted.get('inserted_cells')} cells); the hunting tools don't get the value")
    res = await _hunt(client, target, transcript)
    want = re.compile(rf"(?<![\w'])(8'd{n}|8'h{n:02x}|8'b{n:08b}|{n})(?![\w'])", re.I)
    cond = lambda f: str(f.get("condition") or f.get("fires_when") or f.get("code_line") or "")   # real core / stub keys
    hit = next((f for f in res["flags"] if want.search(cond(f))), None)
    if hit is not None:
        hit = {"id": hit.get("id") or hit.get("cone"), "condition": cond(hit), "line": hit.get("line")}
    caught = res["verdict"] == "found" and hit is not None
    why = (f"the proof against the author's source failed, and flag {hit.get('id')} reads `{hit.get('condition')}`"
           + (f" at line {hit.get('line')}" if hit.get("line") else "") if caught else
           "the proof found extra logic, but no flagged condition names the secret value" if res["verdict"] == "found" else
           f"the proof didn't fail ({res.get('proof')})")
    return {**res, "intent": "drill", "base": chip, "secret": n, "caught": caught, "flag": hit,
            "verdict": "caught" if caught else "missed", "note": f"planted at ui_in == {n}; {why}"}


def _intent(command: str) -> str:
    """Single-skill commands take the fast fixed flow; compound or open-ended ones go to the investigator,
    which plans its own steps. A bare question with no chip verb falls through to the guide."""
    if FAULT.search(command):
        return "fault"
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


async def _repair(client: Client, chip: str, verilog: str | None, suspects: list[dict] | None,
                  transcript: Transcript | None, model: Any) -> dict:
    """Fix a design that differs from the chip (repair.py). With no design: the fault drill, which switches one gate
    in the decompiled Verilog, hides which from the agent, and reveals it at the end."""
    from .repair import inject_fault, repair
    from .scoping import mcp_caller
    call = mcp_caller(client)
    injected = None
    if verilog is None:
        dec = (await call("decompile", chip=chip))["verilog"]
        injected = inject_fault(dec)
        verilog = injected["verilog"]
        if transcript is not None:
            transcript.record("thought", step="fault drill", why="switched one gate in a copy of the decompiled Verilog; "
                                                                "the agent isn't told which")
    res = await repair(chip, verilog, call=call, suspects=suspects, model=model, transcript=transcript)
    if injected:
        res["injected"] = {k: injected[k] for k in ("line", "before", "after")}
        res["found_injected"] = any(d["line"] == injected["line"] for d in res.get("diff", []))
    return res


async def run_command(server: FastMCP, chip: str, command: str, *, transcript: Transcript | None = None,
                      model: Any = None, max_refine: int = 2, verilog: str | None = None,
                      filename: str | None = None, repair: bool = False, suspects: list[dict] | None = None) -> dict:
    """Interpret one natural-language command and run the matching flow (tracked as a session)."""
    kind = "repair" if repair else "check_source" if verilog else _intent(command)
    if kind in ("repair", "fault"):
        sid = lifecycle.start(chip, "repair", command, transcript=transcript) if transcript is not None else None
        try:
            async with Client(server) as client:
                result = await _repair(client, chip, verilog if repair else None, suspects, transcript, model)
        except Exception as exc:
            if sid:
                lifecycle.finish(sid, "error", transcript=transcript, error=f"{type(exc).__name__}: {exc}")
            raise
        if sid:
            lifecycle.finish(sid, "done", transcript=transcript, intent="repair", verdict=result.get("verdict"))
        return result
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
