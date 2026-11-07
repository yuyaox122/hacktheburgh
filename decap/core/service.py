"""Chip state for the API: clean and planted variants, cached in memory, with the operations the routes call."""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import chip as C
from . import find, netlist, pipeline, prove
from .sim import Sim

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out"
SPLIT = json.loads((ROOT / "bench" / "split.json").read_text())

_lock = threading.RLock()


@dataclass
class Variant:
    id: str                    # "tt_um_mac" or "tt_um_mac+t137"
    base: str
    nl: object
    res: dict
    proof: dict
    dec: object
    result: dict               # chip result JSON (CONTRACTS §1)
    flags: list = field(default_factory=list)   # with private "_net"
    plant: dict | None = None
    text: str | None = None    # netlist text (planted copies)


_cache: dict[str, Variant] = {}


def split_of(chip: str) -> str:
    if chip == "warmup":
        return "warmup"
    return "dev" if chip in SPLIT["dev"] else "test" if chip in SPLIT["test"] else "user"


def known_chips() -> list[str]:
    return SPLIT["dev"] + SPLIT["test"] + (["warmup"] if C.netlist_path("warmup").exists() else [])


def parse_id(cid: str) -> tuple[str, int | None]:
    m = re.match(r"^(.+)\+t(\d+)$", cid)
    return (m.group(1), int(m.group(2))) if m else (cid, None)


def get(cid: str) -> Variant:
    with _lock:
        if cid in _cache:
            return _cache[cid]
    base, n = parse_id(cid)
    if base not in known_chips() and base not in _cache:
        raise KeyError(f"unknown chip {base!r}; call list_chips")
    if n is None:
        nl = netlist.load(C.netlist_path(base))
        res, proof, dec = pipeline.run(nl, return_decompiler=True)
        saved = OUT / "chips" / f"{base}.json"
        if saved.exists():
            result = json.loads(saved.read_text())
            # the saved result may predate a decompiler change: keep its author check, refresh the rest
            fresh = C.assemble(base, split_of(base), nl, res, proof, C.info(base), result.get("author_source") or C.author_source(base))
            result = {**result, **{k: fresh[k] for k in ("decompiled", "blocks", "coverage", "verdict", "proof", "netlist", "cell_lines", "stats")}}
        else:
            src = C.author_source(base)
            src["check"], src["check_detail"] = C.author_check(base, src, nl.top)
            result = C.assemble(base, split_of(base), nl, res, proof, C.info(base), src)
        fl = find.flags(dec, res)
        result["flags"] = public(fl)
        v = Variant(cid, base, nl, res, proof, dec, result, fl)
    else:
        return plant(base, n)
    with _lock:
        _cache[cid] = v
    return v


def public(fl):
    return [{k: x for k, x in f.items() if not k.startswith("_")} for f in fl]


def plant(base: str, n: int) -> Variant:
    cid = f"{base}+t{n}"
    with _lock:
        if cid in _cache:
            return _cache[cid]
    clean = get(base)
    pnl, info = find.plant(clean.nl, n)
    res, proof, dec = pipeline.run(pnl, return_decompiler=True)
    src = {k: v for k, v in clean.result["author_source"].items()}
    src["check"], src["check_detail"] = "unavailable", "call prove_equiv with against='author_source'"
    result = C.assemble(base, clean.result["split"], pnl, res, proof, C.info(base), src, variant="planted")
    fl = find.flags(dec, res)
    result["flags"] = public(fl)
    result["plant"] = info
    result["chip"] = base
    result["variant_id"] = cid
    text = find.write_netlist(pnl, pnl.insts, pnl.ports, {i.id: pnl._orig.get(i.id, i.id) for i in pnl.insts})
    v = Variant(cid, base, pnl, res, proof, dec, result, fl, info, text)
    with _lock:
        _cache[cid] = v
    return v


def planted_flag(v: Variant) -> dict | None:
    """The flag that best matches the planted trigger (or, blind, the rarest flag)."""
    if not v.flags:
        return None
    if v.plant:
        ins = set(v.plant["inserted_cells"])
        best = max(v.flags, key=lambda f: len(set(f["cells"]) & ins))
        if set(best["cells"]) & ins:
            return best
    return v.flags[0]


def fix(cid: str) -> dict:
    v = get(cid)
    if v.plant is None:
        raise ValueError("this chip has no planted trigger; plant one first (plant_trigger)")
    f = planted_flag(v)
    if f is None:
        raise ValueError("no suspicious logic was flagged in this chip")
    clean = get(v.base)
    out = find.fix(v.nl, f, clean.nl, clean.res)
    fixed = out.pop("_fixed")
    r2, _ = pipeline.run(fixed)
    out["verilog"] = r2["verilog"]
    out["flag"] = {k: x for k, x in f.items() if not k.startswith("_")}
    out["author_check"] = clean.result["author_source"].get("check")
    return out


def prove_against(cid: str, against: str) -> dict:
    v = get(cid)
    if against == "author_source":
        src = v.result["author_source"]
        if not src.get("files"):
            return {"verdict": "unavailable", "detail": "no author source for this chip", "counterexample": None}
        check, detail, cex = C.author_check(v.base, src, v.nl.top, nl_text=v.text, want_cex=True)
        verdict = {"proven": "proven", "bounded": "tested", "failed": "failed"}.get(check, "unavailable")
        return {"verdict": verdict, "check": check, "detail": detail, "counterexample": cex}
    p = v.proof
    return {"verdict": C.verdict_of(p), "detail": v.result["proof"]["detail"], "points": p["points"], "proven": p["proven"],
            "control_caught": p.get("control_caught"), "counterexample": None}


# ---------- simulation (gate level, one pattern)
def simulate(cid: str, cycles: int = 8, inputs: dict | None = None, reset_first: bool = True) -> dict:
    v = get(cid)
    s: Sim = v.dec.s
    nl = v.nl
    inputs = {"ena": 1, "rst_n": 1, **(inputs or {})}
    env = {}
    for pn, p in nl.ports.items():
        if p["dir"] != "in":
            continue
        val = int(inputs.get(pn, 0))
        for k, b in enumerate(p["bits"]):
            if isinstance(b, int):
                env[b] = (val >> k) & 1
    q = {f.conns["Q"]: (1 if f.cell.ff["preset"] else 0) if reset_first else 0 for f in s.flops}
    regs = v.res["registers"]
    byid = {i.id: i for i in nl.insts}
    trace = []
    for c in range(cycles):
        vals = s.run({**env, **q, **{b: 0 for b in s.cuts}}, 1)
        row = {"cycle": c}
        for pn, p in nl.ports.items():
            if p["dir"] == "out":
                row[pn] = sum((Sim.val(vals, b, 1) & 1) << k for k, b in enumerate(p["bits"]))
        for rn, fids in regs.items():
            row[rn] = sum((vals.get(byid[f].conns["Q"], 0) & 1) << k for k, f in enumerate(fids))
        trace.append(row)
        q = {f.conns["Q"]: Sim.val(vals, f.conns.get("D", "0"), 1) & 1 for f in s.flops}
    return {"inputs": inputs, "trace": trace, "note": "Gate-level simulation of the netlist; one clock edge per row. Clocks are idealised (every flip-flop steps each cycle)."}


# ---------- critical path (estimate)
DELAY_NS = {"inv": 0.04, "clkinv": 0.04, "buf": 0.09, "clkbuf": 0.10, "nand2": 0.06, "nor2": 0.08, "and2": 0.13, "or2": 0.18,
            "xor2": 0.16, "xnor2": 0.15, "mux2": 0.20, "dlygate4sd3": 0.55, "dlymetal6s2s": 0.30}
CLK_Q, SETUP = 0.33, 0.12


def critical_path(cid: str) -> dict:
    v = get(cid)
    s, nl = v.dec.s, v.nl
    arr, frm = {}, {}
    for b in s.sources:
        arr[b] = CLK_Q if b in set(s.q_bits) else 0.0
    for i in s.order:
        ins = [x for p, x in i.conns.items() if p in i.cell.inputs and isinstance(x, int)]
        best = max(ins, key=lambda x: arr.get(x, 0.0), default=None)
        d = DELAY_NS.get(i.cell.base, 0.12 + 0.02 * len(i.cell.inputs))
        for p in i.cell.outputs:
            o = i.conns.get(p)
            if isinstance(o, int):
                arr[o] = arr.get(best, 0.0) + d
                frm[o] = (best, i.id)
    ends = [(f.conns.get("D"), f"flop {f.id}") for f in s.flops] + [(b, f"{n}[{k}]") for n, k, b in nl.out_bits()]
    ends = [(b, nm) for b, nm in ends if isinstance(b, int)]
    if not ends:
        return {"delay_ns_estimate": 0.0, "path": [], "label": "estimate"}
    b, nm = max(ends, key=lambda e: arr.get(e[0], 0.0))
    path = []
    while b in frm:
        b, cell = frm[b]
        path.append(cell)
    path.reverse()
    names = v.res["cell_lines"]
    return {"delay_ns_estimate": round(arr.get(max(ends, key=lambda e: arr.get(e[0], 0.0))[0], 0.0) + SETUP, 2),
            "ends_at": nm if isinstance(nm, str) else str(nm), "cells": path,
            "lines": sorted({names.get(c, 0) for c in path} - {0}),
            "label": "estimate from typical SKY130 HD cell delays (not timing sign-off)"}
