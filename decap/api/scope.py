"""Region scope for the agent routes: agents aimed at a selected region of a visual chip.

Added by Ian's agent layer (board, 7 Nov 19:41: Yuyao asked Ian to add it to the core's agent routes).
A tool that accepts `scope` only works inside that region; results carry `touched` so the UI can light it up.

Blindness: `touched.cells` are the core's hashed cell ids (`c<k>`), never original instance names. Original
names would leak the planted trigger (find.plant names inserted cells `_9101_`, `_9138_`…, outliers next to a
chip's `_16_`…`_41_`). The web app maps ids to instance names (= GDS property 61) through the hidden
`/api/chips/{chip}/cell-names` route, which is not an agent tool.
"""
from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel, Field


class Scope(BaseModel):
    """Which part of the chip to work on. Leave everything empty for the whole chip."""
    cells: list[str] = Field(default_factory=list, description="Cell ids, e.g. ['c17', 'c18'] (as other tools return them).")
    nets: list[str] = Field(default_factory=list, description="Port bits or stripped net names, e.g. ['uo_out[0]', 'n12'].")
    cone_of: str = Field("", description="Everything that drives this, back to the previous flip-flops and inputs: a port bit "
                                         "('uo_out[0]'), a net ('n12'), a flag id ('f0') or a block id ('b3').")


class ScopeIn(BaseModel):
    scope: Scope = Field(default_factory=Scope, description="Region to work on (empty = whole chip).")


def is_empty(s: Scope) -> bool:
    return not (s.cells or s.nets or s.cone_of)


def _bit_names(nl) -> dict[str, int]:
    names = {name: b for b, name in nl.bitname.items()}
    for port, p in nl.ports.items():
        bits = p["bits"]
        for k, b in enumerate(bits):
            if isinstance(b, int):
                names[f"{port}[{k}]" if len(bits) > 1 else port] = b
    return names


def _cone(nl, bit: int) -> set[str]:
    """Combinational fan-in of a bit: walk drivers backwards, stopping at (and including) flip-flops; inputs end it."""
    drv = nl.driver()
    seen_bits, cells, stack = set(), set(), [bit]
    while stack:
        b = stack.pop()
        if b in seen_bits or b not in drv:
            continue
        seen_bits.add(b)
        inst, _ = drv[b]
        if inst.id in cells:
            continue
        cells.add(inst.id)
        if inst.cell.seq:
            continue
        stack += [x for p, x in inst.conns.items() if p in inst.cell.inputs and isinstance(x, int)]
    return cells


def resolve(v, scope: Scope) -> set[str] | None:
    """The cell ids a scope covers; None means the whole chip. A scope that matches nothing is a 404, never a widening."""
    if is_empty(scope):
        return None
    nl, r = v.nl, v.result
    ids = {i.id for i in nl.insts}
    out: set[str] = set()
    bad = [c for c in scope.cells if c not in ids]
    if bad:
        raise HTTPException(404, f"unknown cell id(s) {bad[:5]}; use ids like 'c17' from other tools")
    out |= set(scope.cells)
    names = _bit_names(nl)
    if scope.nets:
        drv = nl.driver()
        for n in scope.nets:
            if n not in names:
                raise HTTPException(404, f"unknown net {n!r}; use a port bit like 'uo_out[0]' or a net like 'n12'")
            b = names[n]
            if b in drv:
                out.add(drv[b][0].id)
            out |= {i.id for i in nl.insts if b in i.conns.values()}
    if scope.cone_of:
        t = scope.cone_of
        flag = next((f for f in v.flags if f["id"] == t), None)
        block = next((b for b in r["blocks"] if b["id"] == t), None)
        if flag:
            out |= set(flag["cone"])
        elif block:
            out |= set(block["cells"])
        elif t in names:
            out |= _cone(nl, names[t])
        else:
            raise HTTPException(404, f"cone_of {t!r} is not a port bit, net, flag id or block id")
    if not out:
        raise HTTPException(404, f"scope matches no cells: {scope.model_dump()}")
    return out


def touched(v, cells) -> dict:
    """What a call worked on: cell ids, the nets on their pins, and the decompiled lines that account for them."""
    nl, r = v.nl, v.result
    cells = sorted(set(cells), key=lambda c: int(c[1:]) if c[1:].isdigit() else 0)
    keep = set(cells)
    bits = {b for i in nl.insts if i.id in keep for b in i.conns.values() if isinstance(b, int)}
    inv = {b: n for n, b in _bit_names(nl).items()}
    lines = sorted({r["cell_lines"].get(c) for c in cells} - {None, 0})
    return {"cells": cells, "nets": sorted(inv.get(b, f"n{b}") for b in bits), "lines": lines}


# =============================================================================================== Witness regions
# get_region / probe_truth (Ian's contract, board 7 Nov 20:32 + deltas 21:30; Yuyao OK'd adding them here 21:27).
# A region = the observable outputs its cells feed (output-port bits, flip-flop D pins) + the ports and flip-flops
# those depend on. Ports and flip-flops are the only names a claim may use: internal nets have no names after
# stripping. Flip-flops are q_<cell id>; the decompiler's register names (acc0) are aliases for their flip-flops.
import random  # noqa: E402
import re  # noqa: E402

from ..core import claims  # noqa: E402
from ..core.claims import DECOMPILER, netlist_sha  # noqa: E402,F401  (region records carry both)
from ..core.sim import Sim  # noqa: E402

FIXED = {**claims.FIXED, "clk": 0}   # the claim checker's setup (running, not in reset); the clock is not an input here
EXHAUSTIVE_BITS = 14
_KEYWORDS = {"module", "input", "output", "wire", "reg", "assign", "always", "posedge", "negedge", "if", "else",
             "begin", "end", "endmodule", "case", "endcase", "default"}
_NUM = re.compile(r"\d*'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ]+|\b\d+\b")


class Model:
    """Nameable signals of one chip variant, as bit lists into the core's simulator."""

    def __init__(self, v):
        self.v, nl = v, v.nl
        self.s: Sim = v.dec.s
        self.ports = {n: p for n, p in nl.ports.items() if n != "clk"}
        self.flop_by_id = {f.id: f for f in nl.flops}
        self.regs = {name: fids for name, fids in v.res.get("registers", {}).items() if all(f in self.flop_by_id for f in fids)}
        self.sig: dict[str, list] = {n: list(p["bits"]) for n, p in self.ports.items()}
        self.nxt: dict[str, list] = {}
        for fid, f in self.flop_by_id.items():
            self.sig[f"q_{fid}"] = [f.conns.get("Q", "0")]
            self.nxt[f"q_{fid}"] = [f.conns.get("D", "0")]
        for name, fids in self.regs.items():
            self.sig[name] = [self.flop_by_id[f].conns.get("Q", "0") for f in fids]
            self.nxt[name] = [self.flop_by_id[f].conns.get("D", "0") for f in fids]
        self.widths = {n: len(b) for n, b in self.sig.items()}
        self.in_bit = {b: (n, k) for n, k, b in nl.in_bits()}
        self.q_of = {f.conns["Q"]: fid for fid, f in self.flop_by_id.items() if isinstance(f.conns.get("Q"), int)}

    def bits_of(self, kind: str, name: str) -> list:
        return (self.nxt if kind == "next" else self.sig)[name]

    def nextable(self, name: str) -> bool:
        """next(name) is allowed: a register or a flip-flop q_<id>."""
        return name in self.nxt

    def fids(self, name: str) -> list[str]:
        """Flip-flop ids behind a register or q_<id> name, LSB first."""
        return self.regs[name] if name in self.regs else [name[2:]]

    def support(self, bits) -> set[int]:
        """Source bits (inputs, flip-flop Qs) these bits depend on through combinational logic; fixed pins excluded."""
        _, sup = self.s.cone([b for b in bits if isinstance(b, int)])
        return {b for b in sup if not (b in self.in_bit and self.in_bit[b][0] in FIXED)}

    def run(self, varying: list[int], n: int, seed: int, fixed: dict[int, int] | None = None,
            biased: bool = False) -> tuple[dict, int, bool]:
        """Simulate once: `varying` source bits get every combination (if few) or n random patterns; others fixed.
        biased=True uses the core's biased patterns (mostly-1 / mostly-0 mixes) so rare equalities get hit."""
        fixed = fixed or {}
        varying = [b for b in varying if b not in fixed]
        exhaustive = len(varying) <= EXHAUSTIVE_BITS
        if exhaustive:
            env, mask = Sim.exhaustive_env(varying) if varying else ({}, 1)
        elif biased:
            env, mask = self.s.random_env(n, seed=seed, bits=varying, biased=True)
        else:
            rng = random.Random(seed)
            mask = (1 << n) - 1
            env = {b: rng.getrandbits(n) for b in varying}
        for b, bit in fixed.items():
            env[b] = mask if bit else 0
        for b in self.s.sources:
            if b not in env:
                env[b] = mask if FIXED.get(self.in_bit.get(b, (None,))[0]) == 1 else 0
        return self.s.run(env, mask), mask, exhaustive

    @staticmethod
    def value(vals: dict, bits: list, j: int) -> int:
        out = 0
        for k, b in enumerate(bits):
            bit = (1 if b == "1" else 0) if isinstance(b, str) else (vals.get(b, 0) >> j) & 1
            out |= bit << k
        return out


_models: dict[str, Model] = {}


def model(v) -> Model:
    if v.id not in _models:
        _models[v.id] = Model(v)
    return _models[v.id]


def _expand_focus(text: list[str], lines: list[int]) -> list[int]:
    """cell_lines points at a statement's FIRST line; include the rest of a multi-line statement."""
    keep = set()
    for n in lines:
        if not 0 < n <= len(text):
            continue
        keep.add(n)
        indent = len(text[n - 1]) - len(text[n - 1].lstrip())
        k = n + 1
        while k <= len(text) and text[k - 1].strip() and len(text[k - 1]) - len(text[k - 1].lstrip()) > indent:
            keep.add(k)
            k += 1
    return sorted(keep)


def count_literals(lines: list[str]) -> int:
    """Identifiers in decompiled lines (numbers and keywords excluded): the yardstick for 'shorter than the decompile'."""
    return sum(len([w for w in re.findall(r"[A-Za-z_]\w*", _NUM.sub(" ", t)) if w not in _KEYWORDS]) for t in lines)


def region_of(v, scope: Scope) -> dict:
    """What a region computes. Keys starting with '_' are internal (support bits, cell set)."""
    m = model(v)
    cells = resolve(v, scope)
    sel = None if cells is None else set(cells)
    observables = [("sig", pn, k, [b]) for pn, p in m.ports.items() if p["dir"] == "out" for k, b in enumerate(p["bits"])]
    observables += [("next", f"q_{fid}", None, [f.conns.get("D", "0")]) for fid, f in m.flop_by_id.items()]
    picked = []
    for kind, name, k, bits in observables:
        cone, _ = m.s.cone([b for b in bits if isinstance(b, int)])
        if sel is None:
            if cone or any(isinstance(b, int) for b in bits):
                picked.append((kind, name, k, bits, cone))
        elif cone & sel or (kind == "next" and name[2:] in sel):
            picked.append((kind, name, k, bits, cone))
    if not picked:
        raise HTTPException(404, "the region feeds no output port or flip-flop")
    region_cells = set().union(*[c for *_, c in picked]) | (sel or set())
    sup = m.support([b for *_, bits, _ in picked for b in bits])

    outs = []
    for pname in dict.fromkeys(name for kind, name, *_ in picked if kind == "sig"):
        ks = sorted(k for kind, name, k, *_ in picked if kind == "sig" and name == pname)
        outs.append({"name": pname, "width": m.widths[pname], "kind": "port", "bits_in_region": ks})
    flops_next = {name[2:] for kind, name, *_ in picked if kind == "next"}
    used = set()
    for rname, fids in m.regs.items():
        if flops_next & set(fids):
            outs.append({"name": f"next({rname})", "width": len(fids), "kind": "register_next",
                         "flops": [f"q_{f}" for f in fids], "bits_in_region": [k for k, f in enumerate(fids) if f in flops_next]})
            used |= set(fids)
    for fid in sorted(flops_next - used, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0):
        outs.append({"name": f"next(q_{fid})", "width": 1, "kind": "flop_next"})

    ins, used = [], set()
    for pname, p in m.ports.items():
        if p["dir"] == "in" and pname not in FIXED and any(b in sup for b in p["bits"]):
            ins.append({"name": pname, "width": len(p["bits"]), "kind": "port",
                        "bits_used": [k for k, b in enumerate(p["bits"]) if b in sup]})
    sup_flops = {m.q_of[b] for b in sup if b in m.q_of}
    for rname, fids in m.regs.items():
        if sup_flops & set(fids):
            ins.append({"name": rname, "width": len(fids), "kind": "register", "flops": [f"q_{f}" for f in fids]})
            used |= set(fids)
    for fid in sorted(sup_flops - used, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0):
        ins.append({"name": f"q_{fid}", "width": 1, "kind": "flop"})

    t = touched(v, region_cells) if region_cells else {"cells": [], "nets": [], "lines": []}
    text = v.result["decompiled"]["verilog"].splitlines()
    focus = [{"line": n, "text": text[n - 1].strip()} for n in _expand_focus(text, t["lines"])]
    return {"inputs": ins, "outputs": outs, "input_bits": len(sup), "focus": focus,
            "cone_literals": count_literals([f["text"] for f in focus]), "cells": len(region_cells), "touched": t,
            "assumed": {"rst_n": 1, "ena": 1}, "netlist_sha": netlist_sha(v), "decompiler": DECOMPILER,
            "names_note": "Claims may use these ports (with [i] or [hi:lo]), register names, flip-flops q_<id>, and "
                          "next(x) for a register or flip-flop one clock later.",
            "_sup": sup, "_cells": region_cells}


def public(r: dict) -> dict:
    return {k: x for k, x in r.items() if not k.startswith("_")}


def probe(v, scope: Scope, n: int, seed: int, fix: dict[str, int]) -> dict:
    """Simulation samples of a region (rst_n = ena = 1, registers free): rows, per-output-bit stats, rare rows."""
    m = model(v)
    r = region_of(v, scope)
    fixed = {}
    for name, value in fix.items():
        if name not in m.sig or name in FIXED or any(o["name"] == name for o in r["outputs"]):
            raise HTTPException(400, f"fix: {name!r} is not an input of this region; use the names get_region lists")
        for k, b in enumerate(m.sig[name]):
            if isinstance(b, int):
                fixed[b] = (value >> k) & 1
    vals, mask, exhaustive = m.run(sorted(r["_sup"]), n, seed, fixed)
    npat = mask.bit_length()
    in_names = [(i["name"], m.sig[i["name"]]) for i in r["inputs"]]
    out_names = [(o["name"], m.nxt[o["name"][5:-1]] if o["name"].startswith("next(") else m.sig[o["name"]]) for o in r["outputs"]]
    rows = [{"in": {nm: Model.value(vals, b, j) for nm, b in in_names},
             "out": {nm: Model.value(vals, b, j) for nm, b in out_names}} for j in range(npat)]
    stats = {}
    for o in r["outputs"]:
        if o["kind"] != "port":
            continue
        for k in o["bits_in_region"]:
            b = m.sig[o["name"]][k]
            ones = bin(vals.get(b, 0) & mask).count("1") if isinstance(b, int) else (npat if b == "1" else 0)
            st = {"ones": ones, "of": npat}
            if isinstance(b, int) and 0 < ones <= max(1, npat // 16):
                st["rows_where_1"] = [rows[j]["in"] for j in range(npat) if (vals[b] >> j) & 1][:4]
            stats[f"{o['name']}[{k}]"] = st
    return {"exhaustive": exhaustive, "vectors": npat, "input_bits": len(r["_sup"]), "fixed": fix,
            "samples": random.Random(seed).sample(rows, min(48, len(rows))), "stats": stats,
            "assumed": {"rst_n": 1, "ena": 1, "registers": "free (any value)"},
            "note": "Simulation samples of the netlist: what the chip did on these inputs. Not a proof.",
            "touched": r["touched"]}
