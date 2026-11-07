"""A stub of the Decap core API, shaped like docs/decap/SPEC.md section 6.1.

Until Yuyao's real FastAPI app lands (decap/CONTRACTS.md), this app lets the agent layer,
the CLI, the MCP server and the UI timeline run end to end. FastMCP turns every route into an
MCP tool: the operation_id becomes the tool name and the docstring becomes the description an
agent reads, so the docstrings here are written for agents.

The chip is a made-up 8-bit counter (`stub_counter8`): `count` increments each clock while
`en` is high, and the output `hit` goes high when `count == 200`. Planting a trigger for N adds
a comparator `ui_in == N` OR'd into the output, which is the spec's hidden-trigger shape.
Every response carries `"source": "stub"` so nothing here can be mistaken for a real result.
"""

from __future__ import annotations

from typing import Literal

from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel, Field

SOURCE = "stub"
BASE = "stub_counter8"
BASE_COMPARE = 200


class PlantIn(BaseModel):
    n: int = Field(ge=0, le=255, description="The secret 8-bit value the hidden trigger waits for (0-255).")


class ProveIn(BaseModel):
    cone: str = Field("all", description="A cone id from decompile, or 'all'.")
    against: Literal["netlist", "author_source"] = Field(
        "netlist",
        description="'netlist': prove the decompiled Verilog equals the gates. "
        "'author_source': prove the gates equal the author's original code (exposes anything extra).",
    )


class SimulateIn(BaseModel):
    cycles: int = Field(8, ge=1, le=64, description="Clock cycles to run.")
    ui_in: int = Field(0, ge=0, le=255, description="Value held on the 8-bit input ui_in.")
    en: int = Field(1, ge=0, le=1, description="Count enable.")
    start: int = Field(0, ge=0, le=255, description="Initial counter value.")


class TriggersIn(BaseModel):
    max_rate_log2: int = Field(8, ge=1, le=32, description="Flag cones that fire for at most 1 in 2^k inputs.")


class ConeIn(BaseModel):
    cone: str = Field(description="A cone id from decompile or find_rare_triggers.")


def create_stub_app() -> FastAPI:
    app = FastAPI(title="Decap core (stub)", version="0.1.0")
    planted: dict[str, int] = {}

    def trigger_of(chip: str) -> int | None:
        if chip == BASE:
            return None
        if chip in planted:
            return planted[chip]
        raise HTTPException(404, f"unknown chip {chip!r}; call list_chips")

    @app.get("/chips", operation_id="list_chips")
    def list_chips() -> list[dict]:
        """List the chips Decap can read: the stub counter plus any copies with a planted trigger."""
        rows = [{"chip": BASE, "title": "8-bit counter with compare (stub)", "logic_cells": 79, "split": "dev"}]
        rows += [{"chip": c, "title": f"{BASE} with a planted trigger", "logic_cells": 90, "split": "planted"} for c in planted]
        return [{**r, "source": SOURCE} for r in rows]

    @app.post("/chips/{chip}/strip", operation_id="strip_names")
    def strip_names(chip: str) -> dict:
        """Hide every instance and net name, keeping only cell types and wires, so analysis is blind."""
        n = trigger_of(chip)
        return {"chip": chip, "logic_cells": 79 if n is None else 90, "nets": 84 if n is None else 96,
                "names_hidden": True, "source": SOURCE}

    @app.post("/chips/{chip}/blocks", operation_id="recover_blocks")
    def recover_blocks(chip: str) -> dict:
        """Recover registers, counters, adders and comparators from structure alone (names stripped)."""
        n = trigger_of(chip)
        blocks = [
            {"id": "b0", "kind": "register", "width": 8, "cells": 8, "detail": "count[7:0]"},
            {"id": "b1", "kind": "incrementer", "width": 8, "cells": 24, "detail": "count + 1 when en"},
            {"id": "b2", "kind": "comparator", "width": 8, "cells": 11, "detail": f"count == {BASE_COMPARE}"},
        ]
        if n is not None:
            blocks.append({"id": "b3", "kind": "comparator", "width": 8, "cells": 11, "detail": f"ui_in == {n}"})
        return {"chip": chip, "blocks": blocks, "source": SOURCE}

    @app.post("/chips/{chip}/decompile", operation_id="decompile")
    def decompile(chip: str) -> dict:
        """Turn the stripped netlist back into readable Verilog, cone by cone, and prove each cone.

        Verdicts: 'proven' (Yosys equivalence passed), 'tested' (random simulation only, not proven), 'failed'.
        """
        n = trigger_of(chip)
        lines = [
            "module decap_out(input clk, input rst_n, input en, input [7:0] ui_in, output out);",
            "  reg [7:0] count;",
            "  always @(posedge clk or negedge rst_n)",
            "    if (!rst_n) count <= 8'd0; else if (en) count <= count + 8'd1;",
            f"  wire hit = (count == 8'd{BASE_COMPARE});",
        ]
        cones = [{"cone": "c0", "drives": "count", "verdict": "proven", "retries": 0},
                 {"cone": "c1", "drives": "hit", "verdict": "proven", "retries": 0}]
        if n is None:
            lines.append("  assign out = hit;")
        else:
            lines.append(f"  wire trig = (ui_in == 8'd{n});")
            lines.append("  assign out = hit | trig;")
            cones.append({"cone": "c2", "drives": "trig", "verdict": "proven", "retries": 1})
        lines.append("endmodule")
        return {"chip": chip, "verilog": "\n".join(lines), "cones": cones,
                "proven": sum(c["verdict"] == "proven" for c in cones), "total": len(cones), "source": SOURCE}

    @app.post("/chips/{chip}/prove", operation_id="prove_equiv")
    def prove_equiv(chip: str, body: ProveIn = Body(default_factory=ProveIn)) -> dict:
        """Formally check equivalence with Yosys. A failure returns the counterexample input that breaks it."""
        n = trigger_of(chip)
        if body.against == "author_source" and n is not None:
            return {"chip": chip, "cone": body.cone, "against": body.against, "verdict": "failed",
                    "counterexample": {"ui_in": n, "step": 1},
                    "method": "yosys miter + sat -prove (stub)", "source": SOURCE}
        return {"chip": chip, "cone": body.cone, "against": body.against, "verdict": "proven",
                "counterexample": None, "method": "yosys equiv_induct (stub)", "source": SOURCE}

    @app.post("/chips/{chip}/simulate", operation_id="simulate")
    def simulate(chip: str, body: SimulateIn = Body(default_factory=SimulateIn)) -> dict:
        """Run the gate-level circuit for some clock cycles and return the trace."""
        n = trigger_of(chip)
        count, trace = body.start, []
        for cycle in range(body.cycles):
            hit = int(count == BASE_COMPARE)
            trig = int(n is not None and body.ui_in == n)
            trace.append({"cycle": cycle, "count": count, "hit": hit, "out": hit | trig})
            if body.en:
                count = (count + 1) & 0xFF
        return {"chip": chip, "trace": trace, "source": SOURCE}

    @app.post("/chips/{chip}/triggers", operation_id="find_rare_triggers")
    def find_rare_triggers(chip: str, body: TriggersIn = Body(default_factory=TriggersIn)) -> dict:
        """Flag cones that fire rarely (at most 1 in 2^k inputs): the classic hidden-trigger signature.

        Legitimate comparators are flagged too; 'likely' says which ones the author's source explains.
        """
        n = trigger_of(chip)
        flagged = [{"cone": "c1", "fires_when": f"count == 8'd{BASE_COMPARE}", "rate": "1/256",
                    "code_line": f"wire hit = (count == 8'd{BASE_COMPARE});", "likely": "legitimate"}]
        if n is not None:
            flagged.append({"cone": "c2", "fires_when": f"ui_in == 8'd{n}", "rate": "1/256",
                            "code_line": f"wire trig = (ui_in == 8'd{n});", "likely": "unexplained"})
        flagged = [f for f in flagged if body.max_rate_log2 >= 8]
        return {"chip": chip, "flagged": flagged, "source": SOURCE}

    @app.post("/chips/{chip}/plant", operation_id="plant_trigger")
    def plant_trigger(chip: str, body: PlantIn) -> dict:
        """Hide a trigger for the secret value N in a copy of the chip (the demo's 'judge picks a number')."""
        if chip != BASE:
            raise HTTPException(400, f"plant into {BASE!r}")
        new = f"{BASE}+t{body.n}"
        planted[new] = body.n
        return {"chip": new, "inserted_cells": 11, "names_hidden": True, "source": SOURCE}

    @app.post("/chips/{chip}/cone", operation_id="cone_of_influence")
    def cone_of_influence(chip: str, body: ConeIn) -> dict:
        """Everything a cone can reach: the outputs and flip-flops its value flows into."""
        trigger_of(chip)
        reaches = {"c0": ["count", "hit", "out"], "c1": ["out"], "c2": ["out"]}.get(body.cone)
        if reaches is None:
            raise HTTPException(404, f"unknown cone {body.cone!r}")
        return {"chip": chip, "cone": body.cone, "reaches": reaches, "source": SOURCE}

    @app.get("/chips/{chip}/critical-path", operation_id="critical_path")
    def critical_path(chip: str) -> dict:
        """The slowest path through the logic, estimated from Liberty cell delays (an estimate, not timing sign-off)."""
        trigger_of(chip)
        return {"chip": chip, "delay_ns_estimate": 1.84,
                "path": ["count[0]", "incrementer carry chain", "count[7]"],
                "label": "estimate from Liberty delays (stub)", "source": SOURCE}

    return app
