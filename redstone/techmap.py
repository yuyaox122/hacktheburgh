"""Map a SKY130 gate-level netlist onto redstone primitives (NOT, INH = a&!b, OR, LATCH).

Per cell, keeping the chip's own cell boundaries:
  * buffers, clock buffers, delay cells, diodes  -> wires (net aliases)
  * conb (tie cell)                              -> constants, propagated away
  * combinational cells                          -> Yosys `abc -g OR,ANDNOT` on the Liberty function (cached per type)
  * flip-flops (dfxtp / dfrtp / dfstp)           -> two-phase master/slave LATCH pair. The hold lines are global
    nets HOLD_M / HOLD_S that the clock driver toggles without overlap. Reset/set is local and asynchronous:
    D' = INH(D, R) (or OR(D, S)) and both hold lines are released while R/S is active, so 0/1 flows through.
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import chipdata as cd

WIRE = re.compile(r"^(buf|clkbuf|dlygate|dlymetal|dlyb|bufbuf|probe_p|lpflow_.*buf)\w*_\d+$")
FLOP = re.compile(r"^df(xtp|rtp|stp|rtn|bbp|sbp|xbp|rbp|stp)_\d+$")
CONST0, CONST1 = "1'b0", "1'b1"
HOLD_M, HOLD_S = "HOLD_M", "HOLD_S"


@dataclass
class Prim:
    id: str
    kind: str                       # NOT | INH | OR | LATCH
    ins: dict[str, str]             # port -> net   (NOT: B; INH/OR: A,B; LATCH: D,H)
    out: str
    cell: str                       # instance it came from ("" for shared helpers)
    x: float                        # real x of that instance (um), for placement order


@dataclass
class PrimNetlist:
    chip: str
    prims: list[Prim]
    inputs: list[str]               # primary input bits (plus HOLD_M/HOLD_S if sequential)
    outputs: dict[str, str]         # output bit -> net (or CONST0/CONST1)
    pin_x: dict[str, float]         # port bit -> real x (um)
    sequential: bool = False
    stats: dict = field(default_factory=dict)


# ---------------------------------------------------------------- per-type mapping with Yosys

@lru_cache(maxsize=None)
def map_type(t: str) -> tuple[tuple, ...]:
    """Primitive network for one output of a combinational cell type.
    Returns ((out_pin, [(kind, {port: net}, out_net)...]),...) with nets named by cell pins or local _n<k>."""
    d = cd.celldef(t)
    v = f"module c({', '.join(['input ' + i for i in d.inputs] + ['output ' + o for o in d.outputs])});\n"
    for o, fn in d.outputs.items():
        v += f"  assign {o} = {fn.replace('!', '~')};\n"
    v += "endmodule\n"
    with tempfile.TemporaryDirectory() as tmp:
        src, js = Path(tmp) / "c.v", Path(tmp) / "c.json"
        src.write_text(v)
        r = subprocess.run(["yosys", "-q", "-p", f"read_verilog {src}; proc; techmap; opt -fast; "
                                                 f"abc -g OR,ANDNOT; opt_clean; write_json {js}"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"yosys failed on {t}: {r.stderr[-400:]}")
        m = json.loads(js.read_text())["modules"]["c"]
    bitname = {}
    for pn, p in m["ports"].items():
        for b in p["bits"]:
            if isinstance(b, int) and p["direction"] == "input":
                bitname[b] = pn
    outs = {pn: p["bits"][0] for pn, p in m["ports"].items() if p["direction"] == "output"}

    def nm(b):
        if b == "0":
            return CONST0
        if b == "1":
            return CONST1
        return bitname.get(b, f"_n{b}")

    prims = []
    for c in m["cells"].values():
        k = {"$_NOT_": "NOT", "$_ANDNOT_": "INH", "$_OR_": "OR"}.get(c["type"])
        if k is None:
            raise RuntimeError(f"{t}: unexpected gate {c['type']}")
        cn = c["connections"]
        ins = {"B": nm(cn["A"][0])} if k == "NOT" else {"A": nm(cn["A"][0]), "B": nm(cn["B"][0])}
        prims.append((k, ins, nm(cn["Y"][0])))
    # an output may be a direct input (e.g. a buffer function) or a constant
    out_alias = {o: nm(b) for o, b in outs.items()}
    return tuple((o, tuple(prims), out_alias[o]) for o in outs)


# ---------------------------------------------------------------- whole netlist

def build(chip: str) -> PrimNetlist:
    nl = cd.read_netlist(chip)
    g = cd.read_gds(chip)
    alias: dict[str, str] = {}

    def root(n: str) -> str:
        seen = 0
        while n in alias:
            n = alias[n]
            seen += 1
            if seen > 10000:
                raise RuntimeError("alias loop")
        return n

    for a, b in nl.assigns.items():
        alias[a] = b
    logic = [c for c in nl.cells if not c.physical]
    comb, flops = [], []
    for c in logic:
        base = c.type
        if WIRE.match(base):
            ins = [p for p in c.pins if cd.celldef(base).inputs and p in cd.celldef(base).inputs]
            outs = [p for p in c.pins if p in cd.celldef(base).outputs]
            alias[c.pins[outs[0]]] = c.pins[ins[0]]
        elif base.startswith("conb"):
            if "HI" in c.pins:
                alias[c.pins["HI"]] = CONST1
            if "LO" in c.pins:
                alias[c.pins["LO"]] = CONST0
        elif FLOP.match(base):
            flops.append(c)
        else:
            comb.append(c)

    prims: list[Prim] = []
    k = [0]

    def new(kind, ins, out, cell, x) -> None:
        prims.append(Prim(f"p{len(prims)}", kind, ins, out, cell, x))

    def local(cell: str) -> str:
        k[0] += 1
        return f"{cell}.n{k[0]}"

    xs = {c.name: g["cells"].get(c.name, (0, 0))[0] for c in logic}
    for c in comb:
        d = cd.celldef(c.type)
        for out_pin, plist, out_alias in map_type(c.type):
            if out_pin not in c.pins:
                continue                                   # unconnected output
            ren = {}

            def rn(n):
                if n in (CONST0, CONST1):
                    return n
                if n in d.inputs:
                    return c.pins.get(n, CONST0)
                if n not in ren:
                    ren[n] = local(c.name)
                return ren[n]

            for kind, ins, o in plist:
                new(kind, {p: rn(v) for p, v in ins.items()}, rn(o), c.name, xs[c.name])
            final = rn(out_alias)
            alias[c.pins[out_pin]] = final                  # the cell's output net is that primitive net

    # flip-flops: check the clock comes straight from the clk pin (through buffers only, no inverters)
    seq = bool(flops)
    for c in flops:
        d = cd.celldef(c.type)
        clk = root(c.pins["CLK"])
        if clk != "clk":
            raise SystemExit(f"{c.name}: clock net {c.pins['CLK']} -> {clk} is not the clk pin "
                             "(multi-clock / gated / negedge designs are not supported yet)")
        x = xs[c.name]
        D = c.pins["D"]
        hm, hs = HOLD_M, HOLD_S
        if "clear" in d.ff or "preset" in d.ff:
            pin = "RESET_B" if "clear" in d.ff else "SET_B"
            R = local(c.name)
            new("NOT", {"B": c.pins[pin]}, R, c.name, x)          # R = active-high reset/set
            d2 = local(c.name)
            if "clear" in d.ff:
                new("INH", {"A": D, "B": R}, d2, c.name, x)      # D' = D & !R
            else:
                new("OR", {"A": D, "B": R}, d2, c.name, x)       # D' = D | S
            D = d2
            hm2, hs2 = local(c.name), local(c.name)
            new("INH", {"A": HOLD_M, "B": R}, hm2, c.name, x)    # release both holds while R/S is active
            new("INH", {"A": HOLD_S, "B": R}, hs2, c.name, x)
            hm, hs = hm2, hs2
        m = local(c.name)
        new("LATCH", {"D": D, "H": hm}, m, c.name, x)
        new("LATCH", {"D": m, "H": hs}, c.pins["Q"], c.name, x)

    # resolve aliases everywhere, then propagate constants
    for p in prims:
        p.ins = {q: root(n) for q, n in p.ins.items()}
        p.out = root(p.out)
    prims = _propagate(prims, alias)
    for p in prims:
        p.ins = {q: root(n) for q, n in p.ins.items()}

    used = {n for p in prims for n in p.ins.values()}
    in_bits = [b for b in nl.bits("in") if b in used]
    outputs = {b: root(b) for b in nl.bits("out")}
    if seq:
        in_bits += [HOLD_M, HOLD_S]
    pin_x = {b: g["pins"][b][0] for b in list(nl.bits("in")) + list(nl.bits("out")) if b in g["pins"]}
    pin_x[HOLD_M] = pin_x.get("clk", 150.0) + 1.0
    pin_x[HOLD_S] = pin_x.get("clk", 150.0) + 2.0
    driven = {p.out for p in prims}
    for b, n in outputs.items():
        if n not in driven and n not in (CONST0, CONST1) and n not in in_bits and n not in nl.bits("in"):
            raise RuntimeError(f"output {b} -> {n} has no driver")
    stats = {"logic_cells": len(logic), "comb": len(comb), "flops": len(flops), "prims": len(prims),
             "by_kind": {k_: sum(p.kind == k_ for p in prims) for k_ in ("NOT", "INH", "OR", "LATCH")}}
    return PrimNetlist(chip, prims, in_bits, outputs, pin_x, seq, stats)


def _propagate(prims: list[Prim], alias: dict) -> list[Prim]:
    """Constant folding + identity removal until nothing changes. Writes eliminated nets into alias."""
    def root(n):
        while n in alias:
            n = alias[n]
        return n

    changed = True
    while changed:
        changed = False
        keep = []
        for p in prims:
            ins = {q: root(n) for q, n in p.ins.items()}
            a, b = ins.get("A"), ins.get("B")
            res = None                                  # net or constant this prim reduces to
            if p.kind == "NOT":
                if b in (CONST0, CONST1):
                    res = CONST1 if b == CONST0 else CONST0
            elif p.kind == "INH":
                if a == CONST0 or b == CONST1 or a == b:
                    res = CONST0
                elif b == CONST0:
                    res = a
                elif a == CONST1:
                    p.kind, p.ins = "NOT", {"B": b}
                    changed = True
            elif p.kind == "OR":
                if a == CONST1 or b == CONST1:
                    res = CONST1
                elif a == CONST0:
                    res = b
                elif b == CONST0 or a == b:
                    res = a
            elif p.kind == "LATCH":
                if ins["H"] == CONST1:
                    raise RuntimeError(f"{p.id}: latch held forever")
            if res is not None:
                alias[p.out] = res
                changed = True
            else:
                p.ins = {q: root(n) for q, n in p.ins.items()}
                keep.append(p)
        prims = keep
    # drop primitives nobody reads (dead logic), except those driving outputs: done by the caller's checks
    return prims


def prune(pn: PrimNetlist) -> None:
    need = set(pn.outputs.values())
    changed = True
    by_out = {p.out: p for p in pn.prims}
    live = set()
    stack = [n for n in need if n in by_out]
    while stack:
        n = stack.pop()
        if n in live:
            continue
        live.add(n)
        for m in by_out[n].ins.values():
            if m in by_out:
                stack.append(m)
    before = len(pn.prims)
    pn.prims = [p for p in pn.prims if p.out in live]
    used = {n for p in pn.prims for n in p.ins.values()}
    pn.inputs = [b for b in pn.inputs if b in used or b in pn.outputs.values()]
    pn.stats["pruned"] = before - len(pn.prims)
    pn.stats["prims"] = len(pn.prims)


if __name__ == "__main__":
    import sys
    pn = build(sys.argv[1])
    prune(pn)
    print(pn.stats)
    print("inputs", pn.inputs)
    print("outputs", {b: n for b, n in pn.outputs.items()})
