"""Repair: a design that no longer matches its chip -> the agent edits the logic -> Yosys re-verifies.

The loop Ian asked for after seeing Alex's verification view (switch one gate; rank which gate failed):

    faulty Verilog  --Yosys-->  "differs" + an input where it differs
          |                                   |
          |   suspects (Alex's ranked gates, or none)   reference behaviour: the real chip, simulated
          v                                   v
       the agent proposes ONE edit (line range -> replacement), with a reason
          |
          +--Yosys--> "match": fixed, proven equal to the chip.   "differs": new counterexample, try again (max 3).

What the agent sees: the faulty file, the suspects, Yosys's verdict and counterexample, and the REAL chip's outputs
for a few inputs (gate-level simulation of the netlist). It never sees the chip's original source, so the fix is a
real repair from evidence, not a copy. The verdict is never the agent's: "fixed" means `check_verilog` (the core's
author_check, Yosys equivalence) passed on the edited file.

`inject_fault` makes the faulty design for a self-contained demo: it switches one operator in one line of the
decompiled Verilog (& <-> |, ^ <-> ~^, + <-> -) and keeps which line it changed out of the agent's view.
"""

from __future__ import annotations

import asyncio
import difflib
import os
import random
import re
import time
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from .source_check import check_verilog
from .transcript import Transcript

MAX_TRIES = 3

INSTRUCTIONS = """You repair one Verilog design so it behaves exactly like a real chip again. Somebody changed one
gate somewhere (one operator, or in a gate-level file one cell's type); the design now differs from the chip. You get the numbered file, the lines a
fault-localisation tool suspects (with scores, if any), the formal checker's verdict with an input where they
differ, and the real chip's outputs for several inputs.

Propose ONE edit: a line range and the replacement lines. Change as little as possible: usually one operator on
one line. Keep the indentation and keep every signal name. Use the reference outputs to decide which operator is
right: compute what the line gives with your edit for those inputs. Give a one-sentence reason a person can
follow ("line 14 used | where the reference behaviour needs &").
In a gate-level file (one sky130_fd_sc_hd__ cell instance per line), the fix is usually one cell's type, e.g.
sky130_fd_sc_hd__and2_1 -> sky130_fd_sc_hd__or2_1; keep the instance name and every pin connection."""


class Rank(BaseModel):
    lines: list[int] = Field(description="Line numbers, most likely first, at most 8")
    why: str = Field("", description="One sentence")


class Edit(BaseModel):
    start: int = Field(description="First line to replace (1-based, inclusive)")
    end: int = Field(description="Last line to replace (1-based, inclusive)")
    replacement: list[str] = Field(description="The new lines, with their indentation")
    why: str = Field(description="One sentence")


# ---- fault injection (demo) ------------------------------------------------------------------------------------

_SWAPS = [(r"(?<![&|])&(?![&|])", "|"), (r"(?<![&|])\|(?![&|])", "&"), (r"(?<!~)\^", "~^"), (r"~\^", "^"),
          (r"(?<![+])\+(?![+])", "-")]


def inject_fault(verilog: str, seed: int | None = None) -> dict:
    """Switch one operator in one logic line. Returns {verilog, line, before, after} (the line is for the reveal)."""
    rng = random.Random(seed)
    lines = verilog.split("\n")
    cands = []
    for i, l in enumerate(lines):
        s = l.strip()
        if not (s.startswith("assign") or "<=" in s or re.match(r"wire\s+\w+\s*=", s)) or s.startswith("//"):
            continue
        body = s.split("=", 1)[1] if "=" in s else s
        if "'b" in body and not re.search(r"[&|^+]", body.replace("'b", "")):
            continue
        for pat, rep in _SWAPS:
            if re.search(pat, l.split("=", 1)[-1] if "=" in l else l):
                cands.append((i, pat, rep))
    if not cands:
        raise ValueError("no operator to switch in this design")
    i, pat, rep = rng.choice(cands)
    head, eq, tail = lines[i].partition("<=") if "<=" in lines[i] else lines[i].partition("=")
    ms = list(re.finditer(pat, tail))
    m = rng.choice(ms)
    new_tail = tail[:m.start()] + rep + tail[m.end():]
    before, lines[i] = lines[i], head + eq + new_tail
    return {"verilog": "\n".join(lines), "line": i + 1, "before": before.strip(), "after": lines[i].strip()}


# ---- evidence -------------------------------------------------------------------------------------------------

async def _reference_samples(call, chip: str, cex: dict | None, n: int = 6) -> list[dict]:
    """The real chip's outputs (gate-level simulation, 2 clocks from reset with the inputs held) for a few inputs,
    starting with the counterexample's."""
    pts: list[tuple[int, int]] = []
    if cex:
        pts.append((int(cex.get("ui_in", 0)) & 255, int(cex.get("uio_in", 0)) & 255))
    rng = random.Random(7)
    while len(pts) < n:
        pts.append((rng.randrange(256), rng.randrange(256)))
    out = []
    for ui, uio in pts:
        try:
            r = await call("simulate", chip=chip, cycles=2, ui_in=ui, uio_in=uio)
            last = (r.get("trace") or [{}])[-1]
            out.append({"ui_in": ui, "uio_in": uio,
                        "outputs_after_1_clock": {k: v for k, v in last.items() if k in ("uo_out", "uio_out", "uio_oe")}})
        except Exception:
            continue
    return out


async def _suspect_lines(call, chip: str, suspects: list[dict] | None, verilog: str = "") -> list[dict]:
    """Normalise suspects to {line, p, cell?}: lines pass through; a cell id maps to its instance line when the file
    is gate-level (Alex's rev B), else to its decompiled lines (switching one gate keeps every cell id, so the chip's
    own region lookup is valid for the faulty copy)."""
    out = []
    text = verilog.split("\n")
    for s in suspects or []:
        p = s.get("p", s.get("score"))
        extra = {k: s[k] for k in ("explains", "confirmed") if k in s}
        if s.get("line"):
            out.append({"line": int(s["line"]), "p": p, **extra})
            continue
        cell = str(s.get("cell") or s.get("id") or "")
        inst = next((i + 1 for i, l in enumerate(text) if "sky130_" in l and re.search(rf"\b{re.escape(cell)}\b", l)), None) if cell else None
        if inst:
            out.append({"line": inst, "p": p, "cell": cell, **extra})
        elif cell:
            try:
                r = await call("get_region", chip=chip, scope={"cells": [cell]})
                for ln in (r.get("touched") or {}).get("lines", [])[:3]:
                    out.append({"line": int(ln), "p": p, "cell": cell, **extra})
            except Exception:
                continue
    return out[:12]


def _numbered(verilog: str) -> str:
    return "\n".join(f"{i:3d}: {l}" for i, l in enumerate(verilog.split("\n"), 1))


def _apply(verilog: str, e: Edit) -> str:
    lines = verilog.split("\n")
    a, b = max(1, e.start), min(len(lines), max(e.start, e.end))
    return "\n".join(lines[:a - 1] + list(e.replacement) + lines[b:])


def _diff(before: str, after: str) -> list[dict]:
    rows = []
    for op in difflib.SequenceMatcher(a=before.split("\n"), b=after.split("\n")).get_opcodes():
        tag, i1, i2, j1, j2 = op
        if tag != "equal":
            rows.append({"line": i1 + 1, "before": before.split("\n")[i1:i2], "after": after.split("\n")[j1:j2]})
    return rows


# ---- mutation search: the "find which gate" step, checked by Yosys -------------------------------------------

_GATES2 = ["and2_1", "or2_1", "nand2_1", "nor2_1", "xor2_1", "xnor2_1"]


def candidate_fixes(verilog: str, lines: list[int] | None = None) -> list[tuple[int, str, str]]:
    """Every single-gate change on the given lines (all logic lines if None): (line, new line text, description).
    Behavioural lines: one operator swapped (the inverse of inject_fault's swaps). Gate-level lines: the cell type
    swapped to another 2-input gate with the same pins."""
    text = verilog.split("\n")
    todo = lines or list(range(1, len(text) + 1))
    out, seen = [], set()
    for ln in todo:
        if not 1 <= ln <= len(text):
            continue
        l = text[ln - 1]
        m = re.search(r"sky130_fd_sc_hd__((?:and|or|nand|nor|xor|xnor)2)_(\d+)", l)
        if m:
            for g in _GATES2:
                if g.split("_")[0] != m.group(1):
                    new = l[:m.start()] + f"sky130_fd_sc_hd__{g}" + l[m.end():]
                    out.append((ln, new, f"cell {m.group(1)}_{m.group(2)} -> {g}"))
            continue
        if "=" not in l or l.strip().startswith("//"):
            continue
        head, eq, tail = l.partition("<=") if "<=" in l else l.partition("=")
        for pat, rep in _SWAPS:
            for mm in re.finditer(pat, tail):
                new = head + eq + tail[:mm.start()] + rep + tail[mm.end():]
                if new not in seen:
                    seen.add(new)
                    out.append((ln, new, f"line {ln}: '{mm.group(0)}' -> '{rep}'"))
    return out


async def mutation_search(verilog: str, verify, ranked_lines: list[int], *, budget: int = 160, parallel: int = 6,
                          note=None) -> dict | None:
    """Try single-gate changes, ranked lines first, Yosys checking each; return the first proven fix or None."""
    all_lines = [i + 1 for i in range(len(verilog.split("\n")))]
    order = ranked_lines + [l for l in all_lines if l not in ranked_lines]
    cands = candidate_fixes(verilog, order)
    # keep the ranked lines' candidates first, in rank order
    rank = {l: i for i, l in enumerate(order)}
    cands.sort(key=lambda c: rank.get(c[0], 10**6))
    cands = cands[:budget]
    if note:
        note("thought", step="search", why=f"trying {len(cands)} single-gate changes, starting with the suspected lines; "
                                           "Yosys checks each one")
    sem = asyncio.Semaphore(parallel)
    lines = verilog.split("\n")

    async def one(c):
        ln, new, desc = c
        async with sem:
            cand = "\n".join(lines[:ln - 1] + [new] + lines[ln:])
            r = await verify(cand, "candidate.v", quiet=True)
            return (c, cand, r)
    for i in range(0, len(cands), parallel * 2):          # in waves, so a hit early in the ranking stops the search
        for c, cand, r in await asyncio.gather(*(one(c) for c in cands[i:i + parallel * 2])):
            if r["verdict"] == "proven":
                return {"line": c[0], "new": c[1], "desc": c[2], "verilog": cand, "tried": min(i + parallel * 2, len(cands)),
                        "proof": r}
    return None


# ---- the loop -------------------------------------------------------------------------------------------------

async def repair(chip: str, verilog: str, *, call, suspects: list[dict] | None = None, model: Any = None,
                 transcript: Transcript | None = None, max_tries: int = MAX_TRIES) -> dict:
    """`call(tool, **args)` reaches the core's tools (simulate, get_region). Returns the repair record."""
    def note(kind: str, **f):
        if transcript is not None:
            transcript.record(kind, **f)

    async def verify(text: str, label: str, quiet: bool = False) -> dict:
        """review_edit (core, #89) for decompiled-style Verilog; the cell-model author check for gate-level files.
        Both answer in one vocabulary: proven / tested / failed / unavailable (+ detail, counterexample)."""
        t0 = time.perf_counter()
        res = None
        if transcript is not None:                 # the check is shown once, as one row, by the note below
            transcript.muted += 1
        try:
            res = await _verify_raw(text, label)
        finally:
            if transcript is not None:
                transcript.muted -= 1
        if quiet:
            return res
        note("tool", tool="review_edit" if "sky130_fd_sc_hd__" not in text else "author_check",
             args={"chip": chip, "against": label}, is_error=res["verdict"] == "unavailable",
             wall_s=round(time.perf_counter() - t0, 3),
             summary={"proven": "match (proven)", "failed": "differs", "tested": "no difference in 20 cycles (not a proof)"}
             .get(res["verdict"], res["verdict"]))
        return res

    async def _verify_raw(text: str, label: str) -> dict:
        res = None
        if "sky130_fd_sc_hd__" not in text:
            try:
                r = await call("review_edit", chip=chip, verilog=text)
                res = {"verdict": {"same": "proven", "tested_same": "tested", "changed": "failed"}.get(r.get("verdict"), "unavailable"),
                       "detail": f"{r.get('headline', '')}. {r.get('detail', '')}".strip(". "), "counterexample": r.get("counterexample")}
            except Exception:
                res = None
        if res is None:
            res = await asyncio.to_thread(check_verilog, chip, text, label)
        return res

    first = await verify(verilog, "faulty.v")
    if first["verdict"] == "proven":
        return {"intent": "repair", "chip": chip, "verdict": "already matches", "verilog": verilog, "diff": [], "attempts": []}
    if first["verdict"] == "unavailable" and not suspects:
        return {"intent": "repair", "chip": chip, "verdict": "unchecked", "detail": first.get("detail"), "verilog": verilog,
                "diff": [], "attempts": []}
    if first["verdict"] == "unavailable":   # e.g. a gate-level file using a cell type the chip doesn't have: the caller
        note("thought", step="verify", why="the checker couldn't read the faulty design itself; going by the suspects")

    sus = await _suspect_lines(call, chip, suspects, verilog)
    if sus:
        note("thought", step="localize", why="suspects from the fault localiser: " +
             ", ".join(f"line {s['line']}" + (f" (p={s['p']})" if s.get("p") is not None else "") for s in sus[:5]))
    else:
        note("thought", step="localize", why="no suspects given: the agent works from the counterexample and the chip's real outputs")

    from .cli_model import DEEPSEEK, resolve
    agent = Agent(resolve(model, os.environ.get("BASIC_WITNESS_MODEL") or DEEPSEEK),
                  instructions=INSTRUCTIONS, output_type=Edit, defer_model_check=True, retries=2)
    model_obj = resolve(model, os.environ.get("BASIC_WITNESS_MODEL") or DEEPSEEK)

    # 1. rank lines: the localiser's suspects if given, else the agent ranks them from the evidence
    ranked = [x["line"] for x in sorted(sus, key=lambda x: -(x.get("p") or 0))]
    if not ranked:
        refs0 = await _reference_samples(call, chip, first.get("counterexample"))
        try:
            ranker = Agent(model_obj, output_type=Rank, defer_model_check=True, retries=1, instructions=(
                "Rank the lines of this Verilog design most likely to hold one wrong operator, given the checker's "
                "counterexample and the real chip's outputs. Logic lines only; most likely first; at most 8."))
            rr = await ranker.run(f"{_numbered(verilog)}\n\nChecker: {first.get('detail', '')[:400]}\n"
                                  f"Counterexample: {first.get('counterexample')}\nReal chip outputs: {refs0}")
            ranked = [int(x) for x in rr.output.lines][:8]
            note("thought", step="localize", why=f"the agent's suspects: lines {ranked}. {rr.output.why}")
        except Exception:
            ranked = []

    # 2. mutation search, Yosys deciding each candidate
    hit = await mutation_search(verilog, verify, ranked, note=note)
    if hit:
        await verify(hit["verilog"], "repaired.v")          # the visible, final check
        why = f"{hit['desc']}"
        try:
            ex = Agent(model_obj, defer_model_check=True, instructions=(
                "In one or two plain sentences, say what the fix changed and why the chip needs it. No hype."))
            why = (await ex.run(f"Before: {verilog.split(chr(10))[hit['line'] - 1].strip()}\nAfter: {hit['new'].strip()}\n"
                                f"Counterexample that the old line got wrong: {first.get('counterexample')}")).output
        except Exception:
            pass
        note("thought", step=f"fix line {hit['line']}", why=str(why)[:300])
        return {"intent": "repair", "chip": chip, "verdict": "fixed", "proof": "proven", "verilog": hit["verilog"],
                "candidate": hit["verilog"], "diff": _diff(verilog, hit["verilog"]), "suspects": sus, "ranked": ranked,
                "method": f"single-gate search ({hit['tried']} candidates checked)", "why": str(why),
                "attempts": [{"try": 1, "start": hit["line"], "end": hit["line"], "replacement": [hit["new"]],
                              "why": str(why), "verdict": "proven"}]}
    note("thought", step="search", why="no single-gate change fixed it; the agent tries a free-form edit")
    current, verdict, attempts, history = verilog, first, [], None
    for k in range(max_tries):
        refs = await _reference_samples(call, chip, verdict.get("counterexample"))
        prompt = (f"Chip: {chip}\nThe design (numbered):\n{_numbered(verilog)}\n\n"
                  f"Suspected lines: {sus or 'none given'}\n"
                  f"Checker verdict on the current design: {verdict['verdict']} ({verdict.get('detail', '')[:200]})\n"
                  f"Input where it differs (inputs at the step the outputs differ): {verdict.get('counterexample')}\n"
                  f"The real chip's outputs: {refs}\n"
                  + (f"Your previous edits were checked and still differ: {[a['why'] for a in attempts]}\n" if attempts else "")
                  + "Propose one edit to the ORIGINAL numbered design above.")
        try:
            res = await agent.run(prompt, message_history=history)
        except Exception as e:
            attempts.append({"try": k + 1, "error": f"{type(e).__name__}: {str(e)[:200]}"})
            break
        e: Edit = res.output
        history = res.all_messages()
        candidate = _apply(verilog, e)
        note("thought", step=f"edit lines {e.start}-{e.end}", why=e.why)
        verdict = await verify(candidate, "repaired.v")
        attempts.append({"try": k + 1, "start": e.start, "end": e.end, "replacement": e.replacement, "why": e.why,
                         "verdict": verdict["verdict"], "counterexample": verdict.get("counterexample")})
        current = candidate
        if verdict["verdict"] == "proven":
            break
    fixed = verdict["verdict"] == "proven"
    return {"intent": "repair", "chip": chip, "verdict": "fixed" if fixed else "not fixed",
            "proof": verdict["verdict"], "detail": verdict.get("detail"), "verilog": current if fixed else verilog,
            "candidate": current, "diff": _diff(verilog, current), "attempts": attempts, "suspects": sus,
            "counterexample": None if fixed else verdict.get("counterexample")}
