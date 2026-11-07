"""A stub of the Decap core API, shaped like docs/decap/SPEC.md section 6.1, with region scope.

Until Yuyao's real FastAPI app lands (decap/CONTRACTS.md), this app lets the agent layer, the CLI,
the MCP server and the UI timeline run end to end. FastMCP turns every route into an MCP tool: the
operation_id becomes the tool name and the docstring becomes the description an agent reads, so the
docstrings here are written for agents.

Region scope (requested by Yuyao, 7 Nov 19:31): the headline is "AI agents aimed at a selected
region of a visual chip". So every tool that works on a chip accepts a `scope` (cell ids, net names,
or the cone of an output/register) and returns `touched: {cells, nets}`: exactly what the 3D view
should light up. An empty scope means the whole chip.

The chip is a made-up 8-bit counter (`stub_counter8`): `count` increments each clock while `en` is
high, and `hit` goes high when `count == 200`. Planting a trigger for N adds a comparator
`ui_in == N` OR'd into the output (the spec's hidden-trigger shape). Every response carries
`"source": "stub"` so nothing here can be mistaken for a real result.
"""

from __future__ import annotations

from typing import Literal

from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import stub_witness

SOURCE = "stub"
BASE = "stub_counter8"
BASE_COMPARE = 200

# Made-up cell ids, grouped by cone. A real core returns real netlist cell ids.
CONES = {
    "c0": {"drives": "count", "cells": [f"ff_{i}" for i in range(8)] + [f"inc_{i}" for i in range(24)],
           "nets": [f"count[{i}]" for i in range(8)]},
    "c1": {"drives": "hit", "cells": [f"cmp_{i}" for i in range(11)], "nets": ["hit"]},
    "c2": {"drives": "trig", "cells": [f"trg_{i}" for i in range(11)], "nets": ["trig"] + [f"ui_in[{i}]" for i in range(8)]},
}
OUT_CELLS = ["or_out"]


class Scope(BaseModel):
    """Which part of the chip to work on. Leave everything empty for the whole chip."""
    cells: list[str] = Field(default_factory=list, description="Cell ids, e.g. picked in the 3D view.")
    nets: list[str] = Field(default_factory=list, description="Net names, e.g. 'hit' or 'count[3]'.")
    cone_of: str = Field("", description="An output or register name; selects every cell that drives it, e.g. 'out', 'hit'.")


class ScopeIn(BaseModel):
    scope: Scope = Field(default_factory=Scope, description="Region to work on (empty = whole chip).")


class PlantIn(BaseModel):
    n: int = Field(ge=0, le=255, description="The secret 8-bit value the hidden trigger waits for (0-255).")


class ProveIn(ScopeIn):
    against: Literal["netlist", "author_source"] = Field(
        "netlist",
        description="'netlist': prove the decompiled Verilog equals the gates. "
        "'author_source': prove the gates equal the author's original code (exposes anything extra).",
    )


class SimulateIn(ScopeIn):
    cycles: int = Field(8, ge=1, le=64, description="Clock cycles to run.")
    ui_in: int = Field(0, ge=0, le=255, description="Value held on the 8-bit input ui_in.")
    en: int = Field(1, ge=0, le=1, description="Count enable.")
    start: int = Field(0, ge=0, le=255, description="Initial counter value.")


class TriggersIn(ScopeIn):
    max_rate_log2: int = Field(8, ge=1, le=32, description="Flag cones that fire for at most 1 in 2^k inputs.")


class ConeIn(BaseModel):
    cone: str = Field(description="A cone id from decompile or find_rare_triggers, e.g. 'c2'.")


class ProbeIn(ScopeIn):
    n: int = Field(256, ge=1, le=4096, description="How many random input vectors to try (all of them if the region has <= 12 input bits).")
    seed: int = Field(1, description="Random seed, so a probe can be repeated.")
    fix: dict[str, int] = Field(default_factory=dict, description="Hold some region inputs at fixed values while the rest vary, e.g. {\"bit0\": 0, \"ui_in\": 37}. Names as the region lists them.")


class ClaimIn(ScopeIn):
    claim: str = Field(description="One Verilog expression about the region, e.g. \"next(count) == count + 8'd1 when en\". "
                                   "Names: ports and flip-flops as get_region lists them; next(x) = x one clock later; "
                                   "'A when B' means B -> A.")
    label: str = Field("", description="Known shape, if any: adder, incrementer, counter, comparator, mux, decoder, constant.")
    check: bool = Field(True, description="False: only parse and render the English (the confirm step), no verdict.")


def create_stub_app() -> FastAPI:
    app = FastAPI(title="Decap core (stub)", version="0.2.0")
    planted: dict[str, int] = {}

    def trigger_of(chip: str) -> int | None:
        if chip == BASE:
            return None
        if chip in planted:
            return planted[chip]
        raise HTTPException(404, f"unknown chip {chip!r}; call list_chips")

    def cones_of(chip: str) -> list[str]:
        return ["c0", "c1"] + (["c2"] if trigger_of(chip) is not None else [])

    def scoped_cones(chip: str, scope: Scope) -> list[str]:
        """Cones that intersect the scope (all cones if the scope is empty)."""
        present = cones_of(chip)
        alias = {"uo_out[0]": "out", "uo_out[1]": "hit", "next(count)": "count"}
        scope = scope.model_copy(update={"cone_of": alias.get(scope.cone_of, scope.cone_of)})
        if not (scope.cells or scope.nets or scope.cone_of):
            return present
        hit = []
        for c in present:
            spec = CONES[c]
            if (set(scope.cells) & set(spec["cells"]) or set(scope.nets) & set(spec["nets"])
                    or scope.cone_of in (spec["drives"], c) or (scope.cone_of == "out" and c in ("c1", "c2"))):
                hit.append(c)
        if not hit:
            raise HTTPException(404, f"scope matches nothing on {chip!r}: {scope.model_dump()}")
        return hit

    def touched(cones: list[str], extra_cells: list[str] = ()) -> dict:
        return {"cells": [x for c in cones for x in CONES[c]["cells"]] + list(extra_cells),
                "nets": [x for c in cones for x in CONES[c]["nets"]]}

    @app.get("/chips", operation_id="list_chips")
    def list_chips() -> list[dict]:
        """List the chips Decap can read: the stub counter plus any copies with a planted trigger."""
        rows = [{"chip": BASE, "title": "8-bit counter with compare (stub)", "logic_cells": 79, "split": "dev"}]
        rows += [{"chip": c, "title": f"{BASE} with a planted trigger", "logic_cells": 90, "split": "planted"} for c in planted]
        return [{**r, "source": SOURCE} for r in rows]

    @app.post("/chips/{chip}/scope", operation_id="resolve_scope")
    def resolve_scope(chip: str, body: ScopeIn = Body(default_factory=ScopeIn)) -> dict:
        """Turn a region (cell ids, net names, or 'cone_of' an output) into the exact cells and cones it covers.

        Use this first when a user points at part of the chip, then pass the same scope to other tools.
        """
        cones = scoped_cones(chip, body.scope)
        return {"chip": chip, "cones": cones, "touched": touched(cones), "source": SOURCE}

    @app.post("/chips/{chip}/strip", operation_id="strip_names")
    def strip_names(chip: str) -> dict:
        """Hide every instance and net name, keeping only cell types and wires, so analysis is blind."""
        n = trigger_of(chip)
        return {"chip": chip, "logic_cells": 79 if n is None else 90, "nets": 84 if n is None else 96,
                "names_hidden": True, "touched": touched(cones_of(chip), OUT_CELLS), "source": SOURCE}

    @app.post("/chips/{chip}/blocks", operation_id="recover_blocks")
    def recover_blocks(chip: str, body: ScopeIn = Body(default_factory=ScopeIn)) -> dict:
        """Recover registers, counters, adders and comparators from structure alone (names stripped), within the scope."""
        n = trigger_of(chip)
        cones = scoped_cones(chip, body.scope)
        all_blocks = [
            ("c0", {"id": "b0", "kind": "register", "width": 8, "cells": 8, "detail": "count[7:0]"}),
            ("c0", {"id": "b1", "kind": "incrementer", "width": 8, "cells": 24, "detail": "count + 1 when en"}),
            ("c1", {"id": "b2", "kind": "comparator", "width": 8, "cells": 11, "detail": f"count == {BASE_COMPARE}"}),
        ]
        if n is not None:
            all_blocks.append(("c2", {"id": "b3", "kind": "comparator", "width": 8, "cells": 11, "detail": f"ui_in == {n}"}))
        return {"chip": chip, "blocks": [b for c, b in all_blocks if c in cones], "touched": touched(cones), "source": SOURCE}

    @app.post("/chips/{chip}/decompile", operation_id="decompile")
    def decompile(chip: str, body: ScopeIn = Body(default_factory=ScopeIn)) -> dict:
        """Turn the stripped netlist back into readable Verilog, cone by cone, and prove each cone (within the scope).

        Verdicts: 'proven' (Yosys equivalence passed), 'tested' (random simulation only, not proven), 'failed'.
        """
        n = trigger_of(chip)
        cones = scoped_cones(chip, body.scope)
        code = {
            "c0": ["  reg [7:0] count;", "  always @(posedge clk or negedge rst_n)",
                   "    if (!rst_n) count <= 8'd0; else if (en) count <= count + 8'd1;"],
            "c1": [f"  wire hit = (count == 8'd{BASE_COMPARE});"],
            "c2": [f"  wire trig = (ui_in == 8'd{n});"] if n is not None else [],
        }
        lines = ["module decap_out(input clk, input rst_n, input en, input [7:0] ui_in, output out);"]
        for c in cones:
            lines += code[c]
        if set(cones) >= {"c1"} and (n is None or "c2" in cones):
            lines.append("  assign out = hit | trig;" if n is not None else "  assign out = hit;")
        lines.append("endmodule")
        verdicts = [{"cone": c, "drives": CONES[c]["drives"], "verdict": "proven", "retries": 1 if c == "c2" else 0}
                    for c in cones]
        return {"chip": chip, "verilog": "\n".join(lines), "cones": verdicts,
                "proven": len(verdicts), "total": len(verdicts), "touched": touched(cones), "source": SOURCE}

    @app.post("/chips/{chip}/prove", operation_id="prove_equiv")
    def prove_equiv(chip: str, body: ProveIn = Body(default_factory=ProveIn)) -> dict:
        """Formally check equivalence with Yosys, within the scope. A failure returns the counterexample input that breaks it."""
        n = trigger_of(chip)
        cones = scoped_cones(chip, body.scope)
        if body.against == "author_source" and n is not None and "c2" in cones:
            return {"chip": chip, "against": body.against, "verdict": "failed",
                    "counterexample": {"ui_in": n, "step": 1}, "failing_cone": "c2",
                    "method": "yosys miter + sat -prove (stub)", "touched": touched(["c2"]), "source": SOURCE}
        return {"chip": chip, "against": body.against, "verdict": "proven", "counterexample": None,
                "method": "yosys equiv_induct (stub)", "touched": touched(cones), "source": SOURCE}

    @app.post("/chips/{chip}/simulate", operation_id="simulate")
    def simulate(chip: str, body: SimulateIn = Body(default_factory=SimulateIn)) -> dict:
        """Run the gate-level circuit for some clock cycles and return the trace (scope picks which cells to highlight)."""
        n = trigger_of(chip)
        cones = scoped_cones(chip, body.scope)
        count, trace = body.start, []
        for cycle in range(body.cycles):
            hit = int(count == BASE_COMPARE)
            trig = int(n is not None and body.ui_in == n)
            trace.append({"cycle": cycle, "count": count, "hit": hit, "out": hit | trig})
            if body.en:
                count = (count + 1) & 0xFF
        return {"chip": chip, "trace": trace, "touched": touched(cones), "source": SOURCE}

    @app.post("/chips/{chip}/triggers", operation_id="find_rare_triggers")
    def find_rare_triggers(chip: str, body: TriggersIn = Body(default_factory=TriggersIn)) -> dict:
        """Flag cones (within the scope) that fire rarely, at most 1 in 2^k inputs: the classic hidden-trigger signature.

        Legitimate comparators are flagged too; 'likely' says which ones the author's source explains.
        """
        n = trigger_of(chip)
        cones = scoped_cones(chip, body.scope)
        flagged = []
        if "c1" in cones:
            flagged.append({"cone": "c1", "fires_when": f"count == 8'd{BASE_COMPARE}", "rate": "1/256",
                            "code_line": f"wire hit = (count == 8'd{BASE_COMPARE});", "likely": "legitimate"})
        if n is not None and "c2" in cones:
            flagged.append({"cone": "c2", "fires_when": f"ui_in == 8'd{n}", "rate": "1/256",
                            "code_line": f"wire trig = (ui_in == 8'd{n});", "likely": "unexplained"})
        flagged = [f for f in flagged if body.max_rate_log2 >= 8]
        return {"chip": chip, "flagged": flagged, "touched": touched([f["cone"] for f in flagged]), "source": SOURCE}

    @app.post("/chips/{chip}/plant", operation_id="plant_trigger")
    def plant_trigger(chip: str, body: PlantIn) -> dict:
        """Hide a trigger for the secret value N in a copy of the chip (the demo's 'judge picks a number')."""
        if chip != BASE:
            raise HTTPException(400, f"plant into {BASE!r}")
        new = f"{BASE}+t{body.n}"
        planted[new] = body.n
        return {"chip": new, "inserted_cells": 11, "names_hidden": True, "touched": touched(["c2"]), "source": SOURCE}

    @app.post("/chips/{chip}/cone", operation_id="cone_of_influence")
    def cone_of_influence(chip: str, body: ConeIn) -> dict:
        """Everything a cone can reach: the outputs and flip-flops its value flows into."""
        if body.cone not in cones_of(chip):
            raise HTTPException(404, f"unknown cone {body.cone!r}")
        reaches = {"c0": ["count", "hit", "out"], "c1": ["out"], "c2": ["out"]}[body.cone]
        return {"chip": chip, "cone": body.cone, "reaches": reaches,
                "touched": touched([body.cone], OUT_CELLS), "source": SOURCE}

    @app.post("/chips/{chip}/critical-path", operation_id="critical_path")
    def critical_path(chip: str, body: ScopeIn = Body(default_factory=ScopeIn)) -> dict:
        """The slowest path through the logic in the scope, estimated from Liberty cell delays (an estimate, not timing sign-off)."""
        cones = scoped_cones(chip, body.scope)
        slowest = "c0" if "c0" in cones else cones[0]
        delays = {"c0": 1.84, "c1": 0.62, "c2": 0.58}
        return {"chip": chip, "delay_ns_estimate": delays[slowest], "cone": slowest,
                "path": CONES[slowest]["cells"][:3] + ["…"], "label": "estimate from Liberty delays (stub)",
                "touched": touched([slowest]), "source": SOURCE}

    # ---- Witness: agents explain a region in one claim; only the checker decides (contract: board, 7 Nov 20:32)
    @app.post("/chips/{chip}/region", operation_id="get_region")
    def get_region(chip: str, body: ScopeIn = Body(default_factory=ScopeIn)) -> dict:
        """Describe a region before making a claim about it: its inputs and outputs (the names a claim may use),
        the decompiled lines it covers, and how many literals that code has. Call this first."""
        cones = scoped_cones(chip, body.scope)
        return {"chip": chip, **stub_witness.get_region(cones), "touched": touched(cones), "source": SOURCE}

    @app.post("/chips/{chip}/probe", operation_id="probe_truth")
    def probe_truth(chip: str, body: ProbeIn = Body(default_factory=ProbeIn)) -> dict:
        """Try many input vectors on the region and show what its outputs did (random, or every input if the region is
        small). Free to call; a pattern that held on samples can still be false, which only propose_claim decides."""
        cones = scoped_cones(chip, body.scope)
        return {"chip": chip, **stub_witness.probe(cones, trigger_of(chip), body.n, body.seed, body.fix),
                "touched": touched(cones), "source": SOURCE}

    @app.post("/chips/{chip}/claim", operation_id="propose_claim")
    def propose_claim(chip: str, body: ClaimIn) -> dict:
        """Submit one claim about the region to the checker. Verdicts: proven (and credited), restated (proven but no
        shorter than the decompile), refuted (with the counterexample input), tautology, unparseable."""
        cones = scoped_cones(chip, body.scope)
        res = stub_witness.check(body.claim, cones, trigger_of(chip), body.label, run=body.check)
        return {"chip": chip, "claim": body.claim, "label": body.label, **res, "touched": touched(cones),
                "netlist_sha": "stub", "decompiler": "stub", "method": "exhaustive simulation (stub checker)",
                "source": SOURCE}

    return app
