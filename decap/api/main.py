"""Decap core API (FastAPI).

Two audiences, one app:
- the web app, through the routes in decap/CONTRACTS.md §4 (`/api/bench`, `/api/chip/…`, `/api/plant`, …);
- AI agents, through the routes under `/api/chips/…`. Ian's agent layer (agent/decap_agent) turns every route into an
  MCP tool: operation_id = tool name, docstring = what the agent reads. Bodies use Body(default_factory=…)
  (an Optional body makes FastMCP drop its arguments).

Run (repo root, after `uv venv && uv pip install -e .`):  .venv/bin/uvicorn decap.api.main:app --port 8000
The agent layer serves the same app to agents with DECAP_CORE_APP=decap.api.main:app.
"""
from __future__ import annotations

import json
import shutil
from typing import Literal

from ..env import load_env_file
from ..paths import BENCH_JSON, ENV_FILE

# decap/.env (gitignored) holds the AI key (ANTHROPIC_API_KEY) for the guide and the agent layer; real env vars win.
# Loaded before the core is imported, so the guide sees it. Never commit it.
load_env_file(ENV_FILE)

from fastapi import Body, FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from ..core import claims, compare, find, guide, service  # noqa: E402
from .scope import Scope, ScopeIn, probe, public, region_of, resolve, touched  # noqa: E402  (region scope for agent tools)

app = FastAPI(title="Decap core", version="1.0.0",
              description="Decap turns a chip's gate-level netlist back into readable Verilog and proves it equal with Yosys.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _get(chip: str):
    try:
        return service.get(chip)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'\""))
    except ValueError as e:
        raise HTTPException(400, str(e))


# ======================================================================================= web app (CONTRACTS §4)
class ChipIn(BaseModel):
    chip: str | None = Field(None, description="A chip id from list_chips.")
    netlist: str | None = Field(None, description="Gate-level SKY130 HD netlist text (alternative to chip).")
    name: str | None = None


class PlantWebIn(BaseModel):
    chip: str
    n: int = Field(ge=0, le=255)


class CompareIn(BaseModel):
    verilog: str | None = None
    netlist: str | None = None
    chip: str | None = None
    name: str | None = None


class ChatIn(BaseModel):
    messages: list[dict] = Field(default_factory=list)
    context: dict = Field(default_factory=dict)


@app.get("/api/health", operation_id="health", include_in_schema=False)
def health() -> dict:
    return {"ok": True, "yosys": shutil.which("yosys") is not None, "llm": guide.available(), "model": guide.MODEL}


@app.get("/api/bench", operation_id="benchmark")
def bench() -> dict:
    """The benchmark: every chip in the frozen dev/test split with its proof verdict, block coverage, line counts
    (decompiled vs the author's source) and the planted-trigger test. 'summary.test' is the held-out headline."""
    if not BENCH_JSON.exists():
        raise HTTPException(404, "no benchmark yet: run `python -m decap.bench.run`")
    return json.loads(BENCH_JSON.read_text())


@app.get("/api/chip/{chip}", operation_id="chip_result", include_in_schema=False)
def chip_result(chip: str) -> dict:
    return _get(chip).result


@app.post("/api/decompile", operation_id="decompile_web", include_in_schema=False)
def decompile_web(body: ChipIn = Body(default_factory=ChipIn)) -> dict:
    if body.chip:
        return _get(body.chip).result
    if body.netlist:
        try:
            r, _ = compare.import_design(netlist_text=body.netlist, name=body.name or "user_netlist")
        except Exception as e:
            raise HTTPException(400, str(e))
        return r
    raise HTTPException(400, "give chip or netlist")


@app.post("/api/plant", operation_id="plant_web", include_in_schema=False)
def plant_web(body: PlantWebIn) -> dict:
    _get(body.chip)
    return service.plant(body.chip, body.n).result


@app.post("/api/fix", operation_id="fix_web", include_in_schema=False)
def fix_web(body: PlantWebIn) -> dict:
    v = service.plant(body.chip, body.n)
    try:
        return service.fix(v.id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/compare", operation_id="compare_web", include_in_schema=False)
def compare_web(body: CompareIn = Body(default_factory=CompareIn)) -> dict:
    try:
        if body.chip:
            r = _get(body.chip).result
            return {**compare.neighbours(r, exclude=body.chip), "imported": {"chip": body.chip}}
        if body.verilog or body.netlist:
            r, _ = compare.import_design(verilog=body.verilog, netlist_text=body.netlist, name=body.name or "your_design")
            out = compare.neighbours(r)
            out["imported"] = {k: r[k] for k in ("decompiled", "blocks", "coverage", "verdict", "stats")}
            return out
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))
    raise HTTPException(400, "give verilog, netlist or chip")


@app.post("/api/chat", operation_id="chat_web", include_in_schema=False)
def chat_web(body: ChatIn = Body(default_factory=ChatIn)) -> dict:
    if not guide.available():
        raise HTTPException(503, "The guide needs an LLM key (ANTHROPIC_API_KEY) on the server.")
    chip = body.context.get("chip") or body.context.get("variant_id")
    selection = body.context.get("selection")
    design = body.context.get("design")
    if not chip and isinstance(design, str) and service.parse_id(design)[0] in service.known_chips():
        chip = design                 # Explore sends the chip it shows as `design`;
        selection = body.context      # keep its live facts (selected cell, pin values, cycle) for the guide
    try:
        if not chip:   # Results, Compare, Minecraft, or a non-chip demo design: answer from the page's own facts
            return guide.ask(None, body.messages, page=body.context)
        return guide.ask(_get(chip).result, body.messages, selection)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # API errors: report, don't crash the page
        raise HTTPException(503, f"The guide is unavailable right now: {type(e).__name__}")


# ======================================================================================= agent tools
class ProveIn(BaseModel):
    against: Literal["netlist", "author_source"] = Field(
        "netlist", description="'netlist': the decompiled Verilog vs the gates it came from. "
                               "'author_source': the gates vs the author's original source (exposes anything extra).")


class SimulateIn(BaseModel):
    cycles: int = Field(8, ge=1, le=256, description="Clock cycles to run, starting from reset.")
    ui_in: int = Field(0, ge=0, le=255, description="Value held on the 8-bit input ui_in.")
    uio_in: int = Field(0, ge=0, le=255, description="Value held on the 8-bit bidirectional input uio_in.")
    ena: int = Field(1, ge=0, le=1)
    rst_n: int = Field(1, ge=0, le=1, description="Active-low reset input (1 = running).")


class TriggersIn(BaseModel):
    max_rate_log2: int = Field(8, ge=1, le=16, description="Flag logic that fires for at most 1 in 2^k of its input values.")
    scope: Scope = Field(default_factory=Scope, description="Only hunt inside this region (empty = whole chip).")


class PlantIn(BaseModel):
    n: int = Field(ge=0, le=255, description="The secret 8-bit value the hidden trigger waits for (0-255).")


class ConeIn(BaseModel):
    cone: str = Field(description="A flag id from find_rare_triggers (e.g. 'f0'), a block id from recover_blocks "
                                  "(e.g. 'b3'), or a cell id (e.g. 'c17').")


class CompareDesignIn(BaseModel):
    verilog: str = Field(description="A small Verilog design (≤ ~500 logic cells after synthesis).")


@app.get("/api/chips", operation_id="list_chips")
def list_chips() -> list[dict]:
    """List the chips Decap can read: real Tiny Tapeout 08 chips (split 'dev' or held-out 'test'), the warm-up chip,
    and any copies with a planted trigger (ids like 'tt_um_mac+t137'). Includes each chip's verdict when benchmarked."""
    rows = {}
    if BENCH_JSON.exists():
        for r in json.loads(BENCH_JSON.read_text())["chips"]:
            rows[r["chip"]] = {"chip": r["chip"], "title": r.get("title"), "split": r["split"], "logic_cells": r.get("logic_cells"),
                               "verdict": r.get("verdict"), "coverage_pct": r.get("coverage_pct")}
    for c in service.known_chips():
        rows.setdefault(c, {"chip": c, "split": service.split_of(c)})
    for cid, v in list(service._cache.items()):
        if v.plant:
            rows[cid] = {"chip": cid, "title": f"{v.base} with a planted trigger", "split": "planted",
                         "logic_cells": len(v.nl.insts), "verdict": v.result["verdict"]}
    return list(rows.values())


@app.post("/api/chips/{chip}/scope", operation_id="resolve_scope")
def resolve_scope(chip: str, body: ScopeIn = Body(default_factory=ScopeIn)) -> dict:
    """Turn a region of the chip (cell ids, nets/port bits, or 'cone_of' a port bit, net, flag or block) into the exact
    cells, nets and decompiled lines it covers. Use it when a user points at part of the chip, then pass the same
    scope to recover_blocks, decompile and find_rare_triggers so you only work inside that region."""
    v = _get(chip)
    cells = resolve(v, body.scope)
    if cells is None:
        cells = {i.id for i in v.nl.insts}
    return {"chip": chip, "scope": body.scope.model_dump(), "touched": touched(v, cells)}


@app.post("/api/chips/{chip}/strip", operation_id="strip_names")
def strip_names(chip: str) -> dict:
    """The stripped netlist Decap reads: every internal instance and net name removed, only cell types, wires and the
    top-level port names kept, so the analysis is blind. Returns counts and the first cells."""
    v = _get(chip)
    st = v.nl.stripped()
    return {"chip": chip, "logic_cells": len(v.nl.insts), "flops": len(v.nl.flops), "physical_cells_dropped": v.nl.physical,
            "names_hidden": True, "ports": st["ports"], "cells_preview": st["cells"][:20]}


@app.post("/api/chips/{chip}/blocks", operation_id="recover_blocks")
def recover_blocks(chip: str, body: ScopeIn = Body(default_factory=ScopeIn)) -> dict:
    """Blocks recovered from structure alone (names stripped): registers, counters, shift registers, adders,
    comparators, lookup tables and leftover logic, each with its decompiled line range and cell count.
    With a scope, only blocks that overlap the region."""
    v = _get(chip)
    r = v.result
    region = resolve(v, body.scope)
    blocks = [b for b in r["blocks"] if region is None or set(b["cells"]) & region]
    out = {"chip": chip, "coverage": r["coverage"],
           "blocks": [{"id": b["id"], "kind": b["kind"], "recognised": b["recognised"], "width": b["width"], "lines": b["lines"],
                       "cells": len(b["cells"]), "text": b["text"]} for b in blocks]}
    if region is not None:
        out["touched"] = touched(v, {c for b in blocks for c in b["cells"]} & region)
    return out


@app.post("/api/chips/{chip}/decompile", operation_id="decompile")
def decompile(chip: str, body: ScopeIn = Body(default_factory=ScopeIn)) -> dict:
    """Turn the stripped netlist back into readable Verilog and prove it equal to the netlist with Yosys.
    verdict: 'proven' (Yosys equivalence passed, and a broken control copy was rejected), 'tested' (bounded
    simulation only, NOT proven) or 'failed'. Line numbers in other tools refer to this Verilog.
    With a scope, 'focus' gives the lines that account for that region; the proof always covers the whole chip."""
    v = _get(chip)
    r = v.result
    out = {"chip": chip, "verilog": r["decompiled"]["verilog"], "lines": r["decompiled"]["lines"], "verdict": r["verdict"],
           "proof": r["proof"], "coverage": r["coverage"], "author_lines": r["author_source"].get("lines")}
    region = resolve(v, body.scope)
    if region is not None:
        t = touched(v, region)
        text = r["decompiled"]["verilog"].splitlines()
        out["focus"] = [{"line": n, "text": text[n - 1].strip()} for n in t["lines"] if 0 < n <= len(text)]
        out["touched"] = t
    return out


@app.post("/api/chips/{chip}/prove", operation_id="prove_equiv")
def prove_equiv(chip: str, body: ProveIn = Body(default_factory=ProveIn)) -> dict:
    """Formally check equivalence with Yosys. against='netlist' checks the decompile against its gates;
    against='author_source' checks the gates against the author's source code, and on failure returns the
    counterexample input (e.g. {'ui_in': 137}) where they differ."""
    out = service.prove_against(chip, body.against)
    return {"chip": chip, "against": body.against, **out}


class ReviewEditIn(BaseModel):
    verilog: str = Field(description="The FULL edited module (the decompiled Verilog from `decompile` with your changes). "
                                     "Keep the module name and the port list exactly; only the logic inside may change.")
    baseline: str | None = Field(default=None, description="Optional: the full module BEFORE this edit (with edits you "
                                 "already kept). Then `this_edit` also says what this edit alone does compared with it.")


@app.post("/api/chips/{chip}/review-edit", operation_id="review_edit")
def review_edit(chip: str, body: ReviewEditIn) -> dict:
    """Check an edited copy of this chip's decompiled Verilog against the chip's real gates with Yosys, so you know
    what your edit really does before anyone builds it. verdict: 'same' (Yosys proved it the same circuit for every
    input and register value, and the ports, clocking and wiring are unchanged), 'tested_same' (no difference in 20
    clock cycles from reset, NOT proven), 'changed' (behaviour differs; counterexample gives the inputs and clock step
    where the outputs differ) or 'broken' (doesn't compile, ports changed, a wiring problem such as an undriven wire,
    two drivers, a loop, a latch or x/z, or Yosys couldn't finish; detail says which). With `baseline`, `this_edit`
    ({verdict, headline, detail, counterexample}) compares the edit with that text instead. headline and detail are
    written by the checker; quote them as given. Takes up to 90 s."""
    _get(chip)
    return service.review_edit(chip, body.verilog, body.baseline)


@app.post("/api/chips/{chip}/simulate", operation_id="simulate")
def simulate(chip: str, body: SimulateIn = Body(default_factory=SimulateIn)) -> dict:
    """Run the gate-level netlist from reset for some clock cycles with fixed inputs; returns outputs and the
    decompiled registers' values per cycle."""
    _get(chip)
    return {"chip": chip, **service.simulate(chip, body.cycles, {"ui_in": body.ui_in, "uio_in": body.uio_in, "ena": body.ena,
                                                                 "rst_n": body.rst_n})}


@app.post("/api/chips/{chip}/triggers", operation_id="find_rare_triggers")
def find_rare_triggers(chip: str, body: TriggersIn = Body(default_factory=TriggersIn)) -> dict:
    """Blind hunt for hidden triggers: logic that fires for at most 1 in 2^k of its input values, the classic
    trojan signature. Each flag gives the exact condition (e.g. "ui_in == 8'd137"), the decompiled line, and what
    it reaches. Legitimate comparators (a counter hitting its limit) are flagged too: judge each by its line."""
    v = _get(chip)
    fl = v.result["flags"]
    if body.max_rate_log2 != 8:
        fl = service.public(find.flags(v.dec, v.res, k=body.max_rate_log2))
    region = resolve(v, body.scope)
    if region is not None:
        fl = [f for f in fl if set(f["cone"]) & region]
    lines = v.result["decompiled"]["verilog"].splitlines()
    return {"chip": chip, "k": body.max_rate_log2,
            "flagged": [{**{x: f[x] for x in ("id", "condition", "rate", "line", "signal", "reaches")},
                         "code_line": lines[f["line"] - 1].strip() if 0 < f["line"] <= len(lines) else "",
                         "cells": len(f["cells"])} for f in fl],
            "touched": touched(v, {c for f in fl for c in f["cells"]})}


@app.post("/api/chips/{chip}/plant", operation_id="plant_trigger")
def plant_trigger(chip: str, body: PlantIn) -> dict:
    """Hide a trigger for the secret value N in a copy of the chip: `output_bit |= (ui_in == N)`, built from real
    SKY130 cells and spliced into the netlist, then re-read with fresh names so nothing marks the new cells.
    Returns the new chip id (e.g. 'tt_um_mac+t137') to use with the other tools."""
    if service.parse_id(chip)[1] is not None:
        raise HTTPException(400, "plant into a clean chip")
    _get(chip)
    v = service.plant(chip, body.n)
    return {"chip": v.id, "base": chip, "n": v.plant["n"], "target": v.plant["target"],
            "inserted_cells": len(v.plant["inserted_cells"]), "names_hidden": True, "verdict": v.result["verdict"]}


@app.post("/api/chips/{chip}/cone", operation_id="cone_of_influence")
def cone_of_influence(chip: str, body: ConeIn) -> dict:
    """Everything a flag, block or cell can reach before the next clock edge: the cells, the outputs and
    flip-flops its value flows into, and the decompiled lines involved."""
    v = _get(chip)
    r = v.result
    f = next((x for x in v.flags if x["id"] == body.cone), None)
    if f:
        cells = f["cone"]
        reaches = f["reaches"]
    else:
        b = next((x for x in r["blocks"] if x["id"] == body.cone), None)
        if b:
            cells, reaches = b["cells"], []
        elif body.cone in r["cell_lines"]:
            cells, reaches = [body.cone], []
        else:
            raise HTTPException(404, f"unknown cone {body.cone!r}; use a flag id, block id or cell id")
    lines = sorted({r["cell_lines"].get(c, 0) for c in cells} - {0})
    return {"chip": chip, "cone": body.cone, "cells": cells, "reaches": reaches, "lines": lines, "touched": touched(v, cells)}


@app.post("/api/chips/{chip}/fix", operation_id="remove_trigger")
def remove_trigger(chip: str) -> dict:
    """Remove the flagged trigger from a planted chip (tie it off, delete the dead cells) and prove the result
    equal to the clean chip with Yosys. Use a planted chip id from plant_trigger."""
    try:
        out = service.fix(chip)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"chip": chip, **{k: out[k] for k in ("verdict", "detail", "removed_cells", "seconds", "flag", "author_check")},
            "verilog": out["verilog"], "touched": {"cells": list(out["removed_cells"]), "nets": [], "lines": []}}


class ProbeIn(BaseModel):
    scope: Scope = Field(default_factory=Scope, description="Region to probe (empty = whole chip).")
    n: int = Field(1024, ge=1, le=4096, description="Random input vectors to try (every vector if the region has <= 14 input bits).")
    seed: int = Field(1, description="Random seed, so a probe can be repeated.")
    fix: dict[str, int] = Field(default_factory=dict, description="Hold some region inputs at fixed values while the rest "
                                                                  "vary, e.g. {\"bit0\": 0, \"ui_in\": 37}. Names as get_region lists them.")


@app.post("/api/chips/{chip}/region", operation_id="get_region")
def get_region(chip: str, body: ScopeIn = Body(default_factory=ScopeIn)) -> dict:
    """Describe a region before making a claim about it: the outputs it feeds (output-port bits, flip-flops' next
    values), the ports and flip-flops they depend on (the only names a claim may use), the decompiled lines it covers
    and how many literals they have. rst_n = 1 and ena = 1 are assumed. Call this first."""
    return {"chip": chip, **public(region_of(_get(chip), body.scope))}


@app.post("/api/chips/{chip}/probe", operation_id="probe_truth")
def probe_truth(chip: str, body: ProbeIn = Body(default_factory=ProbeIn)) -> dict:
    """Try many input vectors on the region and show what its outputs did (every vector if the region has few input
    bits, otherwise random; `fix` holds chosen inputs). Free to call. Samples are evidence, not proof: a pattern that
    held on samples can still be false, and only propose_claim decides."""
    return {"chip": chip, **probe(_get(chip), body.scope, body.n, body.seed, body.fix)}


class ClaimIn(ScopeIn):
    claim: str = Field(description="One Verilog expression about the region, e.g. \"uo_out[4:0] == ui_in[3:0] + ui_in[7:4]\". "
                                   "Names: ports and flip-flops exactly as get_region lists them; next(x) = x one clock "
                                   "later; 'A when B' means B -> A. rst_n = 1 and ena = 1 are assumed.")
    label: str = Field("", description="Known shape, if any: adder, incrementer, counter, comparator, mux, decoder, constant.")
    check: bool = Field(True, description="False: only parse and render the English (the confirm step), no verdict.")


@app.post("/api/chips/{chip}/claim", operation_id="propose_claim")
def propose_claim(chip: str, body: ClaimIn) -> dict:
    """Submit one claim about the region to the checker (Yosys; no LLM decides). Verdicts: proven (for every input
    and register value), inductive (true from reset, proven by induction), bounded (holds 30 cycles from reset, not
    proven), refuted (with the counterexample), partial (true, but doesn't fix its outputs for every input), restated
    (true, but no shorter than the decompile and no known shape), tautology, unparseable, timeout. Only proven or
    inductive claims with credit.cleared explain the region."""
    v = _get(chip)
    region = region_of(v, body.scope)
    return claims.check(v, body.claim, body.label, region=region, do_check=body.check)


@app.get("/api/chips/{chip}/critical-path", operation_id="critical_path")
def critical_path(chip: str) -> dict:
    """The slowest path through the logic, estimated from typical SKY130 HD cell delays (an estimate, not timing
    sign-off), with the decompiled lines it passes through."""
    _get(chip)
    return {"chip": chip, **service.critical_path(chip)}


@app.post("/api/compare-design", operation_id="compare_design")
def compare_design(body: CompareDesignIn) -> dict:
    """'Chips like mine': synthesise a small Verilog design to SKY130 cells, decompile it, and return the most
    similar real Tiny Tapeout chips with why they match."""
    try:
        r, _ = compare.import_design(verilog=body.verilog)
    except Exception as e:
        raise HTTPException(400, str(e))
    out = compare.neighbours(r)
    return {"verdict": r["verdict"], "decompiled": r["decompiled"]["verilog"], **out}
