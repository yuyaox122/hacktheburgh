"""Decompile + prove, with the retry loop: anything the proof rejects is rewritten as plain logic and proven again."""
from __future__ import annotations

import re
import time

from . import decompile as D
from . import prove as P
from .netlist import Netlist

MAX_RETRIES = 2


def _sinks_for(nl: Netlist, res: dict, unproven: list[str]) -> set:
    """Map unproven signal names back to the sink nets to rewrite structurally."""
    out = set()
    regs = res["registers"]
    byid = {i.id: i for i in nl.insts}
    for u in unproven:
        m = re.match(r"(\w+?)(?:\[(\d+)\])?$", u)
        if not m:
            continue
        name, idx = m.group(1), m.group(2)
        if name in regs:
            cids = regs[name]
            if idx is not None and int(idx) < len(cids):
                cids = [cids[int(idx)]]     # just that bit: it is pulled out and written as plain logic
            for cid in cids:
                d = byid[cid].conns.get("D")
                if isinstance(d, int):
                    out.add(d)
        elif name in nl.ports:
            bits = nl.ports[name]["bits"]
            k = int(idx) if idx is not None else 0
            if k < len(bits) and isinstance(bits[k], int):
                out.add(bits[k])
    return out


def run(nl: Netlist, timeout: int = 120, names: dict | None = None, return_decompiler: bool = False):
    """Returns (decompile result, proof result). Proof has 'attempts' and 'retried_sinks'."""
    t0 = time.time()
    structural: set = set()
    attempts = []
    res = proof = None
    for attempt in range(MAX_RETRIES + 2):
        full = attempt == MAX_RETRIES + 1
        if full:
            structural = {b for i in nl.flops for b in [i.conns.get("D")] if isinstance(b, int)} | \
                         {b for _, _, b in nl.out_bits() if isinstance(b, int)}
        dec = D.Decompiler(nl, structural_only=structural, names=names, plain=full)
        res = dec.decompile()
        proof = P.prove(nl, res["verilog"], res["proof_names"], timeout=timeout)
        attempts.append({"structural_sinks": len(structural), "points": proof["points"], "proven": proof["proven"],
                         "ok": proof["ok"], "unproven": proof["unproven"][:12], "error": proof.get("error")})
        if proof["ok"] or proof.get("error") and "timeout" in (proof.get("error") or ""):
            break
        if full:
            break
        new = _sinks_for(nl, res, proof["unproven"])
        if not new - structural:
            # nothing new to rewrite: jump to the all-structural attempt
            new = {b for i in nl.flops for b in [i.conns.get("D")] if isinstance(b, int)} | \
                  {b for _, _, b in nl.out_bits() if isinstance(b, int)}
        structural |= new
    proof["attempts"] = attempts
    if not proof["ok"]:
        cyc = P.bounded(nl, res["verilog"], timeout=timeout)
        proof["tested_cycles"] = cyc
    proof["seconds"] = round(time.time() - t0, 2)
    return (res, proof, dec) if return_decompiler else (res, proof)
