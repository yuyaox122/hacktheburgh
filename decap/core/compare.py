"""Compare (SPEC features 16-19): "chips like mine".

Import a small Verilog design (synthesised to SKY130 HD with Yosys) or a gate-level netlist, decompile it, build a
structural fingerprint from what Decap recovered, and rank the benchmark chips by cosine similarity.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from ..paths import CHIPS_OUT
from . import lib, netlist, pipeline

KINDS = ["counter", "shift", "register", "adder", "subtractor", "multiplier", "comparator", "mux", "table", "reduce",
         "bitwise", "copy", "logic"]
FAMILIES = {"xor": "xor", "xnor": "xor", "mux": "mux", "df": "flop", "and": "and", "nand": "and", "or": "or", "nor": "or",
            "a2": "aoi", "a3": "aoi", "a4": "aoi", "o2": "oai", "o3": "oai", "o4": "oai", "inv": "inv", "clkinv": "inv",
            "buf": "buf", "clkbuf": "buf", "dly": "buf"}


def liberty_text(cells=lib.SYNTH_CELLS) -> str:
    out = ["library(decap_min) {", '  delay_model : table_lookup;', '  time_unit : "1ns";', '  capacitive_load_unit (1,pf);']
    for name in cells:
        c = lib.cell(name)
        body = [f"    area : {c.area};"]
        if c.ff:
            g = [f'clocked_on : "{c.ff["clk"]}";', f'next_state : "{c.ff["d"]}";']
            if c.ff["clear"]:
                g.append(f'clear : "{c.ff["clear"]}";')
            if c.ff["preset"]:
                g.append(f'preset : "{c.ff["preset"]}";')
            body.append(f"    ff (IQ, IQ_N) {{ {' '.join(g)} }}")
        for p in c.inputs:
            body.append(f"    pin({p}) {{ direction : input; {'clock : true; ' if c.ff and p == c.ff['clk'] else ''}}}")
        for p, fn in c.fn_text.items():
            body.append(f'    pin({p}) {{ direction : output; function : "{fn}"; }}')
        out.append(f"  cell({lib.PREFIX}{name}) {{\n" + "\n".join(body) + "\n  }")
    out.append("}")
    return "\n".join(out) + "\n"


def synthesise(verilog: str, top: str | None = None, timeout: int = 120) -> str:
    """Verilog -> SKY130 HD gate-level netlist text."""
    with tempfile.TemporaryDirectory() as td:
        Path(td, "in.v").write_text(verilog)
        Path(td, "cells.lib").write_text(liberty_text())
        topcmd = f"-top {top}" if top else "-auto-top"
        script = f"""read_verilog -sv {td}/in.v
synth -flatten {topcmd}
dfflibmap -liberty {td}/cells.lib
abc -liberty {td}/cells.lib
hilomap -hicell {lib.PREFIX}conb_1 HI -locell {lib.PREFIX}conb_1 LO
setundef -zero
opt_clean -purge
write_verilog -noattr -noexpr {td}/out.v
"""
        Path(td, "s.ys").write_text(script)
        r = subprocess.run(["yosys", "-q", f"{td}/s.ys"], capture_output=True, text=True, timeout=timeout)
        if r.returncode:
            err = [l for l in (r.stderr + r.stdout).splitlines() if "ERROR" in l]
            raise ValueError("Yosys could not synthesise the design: " + (err[0] if err else (r.stderr or r.stdout)[-400:]))
        return Path(td, "out.v").read_text()


def family(t: str) -> str:
    for k, v in FAMILIES.items():
        if t.startswith(k):
            return v
    return "other"


def fingerprint(result: dict) -> dict:
    kinds = Counter()
    for b in result["blocks"]:
        if b["kind"] in ("const",):
            continue
        kinds[b["kind"]] += max(1, len(b["cells"]))
    fam = Counter(family(c["type"]) for c in result["netlist"]["cells"])
    st = result["stats"]
    return {"blocks": dict(kinds), "cell_families": dict(fam), "logic_cells": st["logic_cells"], "flops": st["flops"],
            "block_count": {k: sum(1 for b in result["blocks"] if b["kind"] == k) for k in KINDS if any(b["kind"] == k for b in result["blocks"])}}


def _vec(fp: dict) -> list[float]:
    n = max(fp["logic_cells"], 1)
    v = [fp["blocks"].get(k, 0) / n for k in KINDS]
    fams = ["xor", "mux", "flop", "and", "or", "aoi", "oai", "inv", "buf", "other"]
    v += [0.6 * fp["cell_families"].get(f, 0) / n for f in fams]
    v += [0.5 * math.log1p(fp["logic_cells"]) / 6, 0.5 * fp["flops"] / n]
    return v


def cosine(a, b) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def index() -> list[dict]:
    rows = []
    for p in sorted(CHIPS_OUT.glob("*.json")):
        if "__" in p.stem:
            continue
        r = json.loads(p.read_text())
        rows.append({"chip": r["chip"], "title": r.get("title", r["chip"]), "fp": fingerprint(r), "result": r})
    return rows


def why(a: dict, b: dict) -> str:
    shared = [k for k in KINDS if k != "logic" and a["block_count"].get(k) and b["block_count"].get(k)]
    parts = []
    for k in shared[:3]:
        x, y = a["block_count"][k], b["block_count"][k]
        word = {"counter": "counter", "shift": "shift register", "register": "register", "adder": "adder", "subtractor": "subtractor",
                "multiplier": "multiplier", "comparator": "comparator", "mux": "selector", "table": "lookup table", "reduce": "reduction",
                "bitwise": "bitwise block", "copy": "direct wiring"}[k]
        art = "an" if word[0] in "aeiou" else "a"
        parts.append(f"both have {word}s ({x} vs {y})" if x > 1 or y > 1 else f"both have {art} {word}")
    parts.append(f"{a['logic_cells']} vs {b['logic_cells']} logic cells, {a['flops']} vs {b['flops']} flip-flops")
    return "; ".join(parts)


def neighbours(result: dict, k: int = 5, exclude: str | None = None) -> dict:
    fp = fingerprint(result)
    v = _vec(fp)
    rows = []
    for row in index():
        if row["chip"] in (exclude, result.get("chip")):
            continue
        s = cosine(v, _vec(row["fp"]))
        r = row["result"]
        rec = [b for b in r["blocks"] if b.get("recognised") and b["kind"] not in ("const", "copy")]
        exc = "\n".join(r["decompiled"]["verilog"].splitlines()[rec[0]["lines"][0] - 1: rec[0]["lines"][1]]) if rec else ""
        rows.append({"chip": row["chip"], "title": row["title"], "score": round(s, 3), "why": why(fp, row["fp"]),
                     "logic_cells": r["stats"]["logic_cells"], "area_um2": r["stats"]["area_um2"],
                     "blocks": row["fp"]["block_count"], "excerpt": exc[:600], "verdict": r["verdict"]})
    rows.sort(key=lambda x: -x["score"])
    return {"fingerprint": fp, "neighbours": rows[:k]}


def import_design(verilog: str | None = None, netlist_text: str | None = None, name: str = "user_design") -> tuple[dict, object]:
    from . import chip as C
    text = netlist_text or synthesise(verilog or "")
    nl = netlist.load(text=text)
    if len(nl.insts) > 600:
        raise ValueError(f"design has {len(nl.insts)} logic cells; Compare accepts up to about 500")
    res, proof = pipeline.run(nl)
    meta = {"title": name, "author": "you", "language": "Verilog" if verilog else "netlist"}
    src = {"files": {"design.v": verilog} if verilog else {}, "lines": C.code_lines(verilog or ""), "check": "unavailable",
           "check_detail": "imported design", "repo": "", "commit": "", "url": ""}
    result = C.assemble(name, "user", nl, res, proof, meta, src)
    return result, nl
