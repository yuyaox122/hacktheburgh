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
