"""Find: plant a trigger for the judge's number, flag rare-trigger logic blind, and remove-and-prove (SPEC §6.8, §6.6).

Plant: `out_bit |= (in_word == N)` built from SKY130 cells and spliced into the netlist. The planted
netlist is written out and re-read, so it gets fresh hashed cell ids like any other chip: nothing in the
stripped view marks the new cells.

Flag (blind): every net is simulated on uniform random inputs. Nets that are almost never 1 (or never
0) are checked exactly over their whole input space; those that fire for at most 1 in 2^k input values
(k = 8 by default) are flagged, top-most first, with the exact condition (e.g. `ui_in == 8'd137`).
Legitimate comparators (a counter hitting its limit) get flagged too, and we report that.
"""
from __future__ import annotations

import random
import re
import time

from . import lib, netlist, prove
from .netlist import Inst, Netlist

FLAG_K = 8


# ---------- writing a (modified) netlist back out as Verilog, original instance names kept
def write_netlist(nl: Netlist, insts: list[Inst], ports: dict, names: dict[str, str]) -> str:
    def net(b):
        if isinstance(b, str):
            return f"1'b{b}"
        return pin_name.get(b) or f"_n{b}"

    pin_name = {}
    for pn, p in ports.items():
        if p["dir"] == "in":
            for k, b in enumerate(p["bits"]):
                if isinstance(b, int):
                    pin_name.setdefault(b, f"{pn}[{k}]" if len(p["bits"]) > 1 else pn)
    lines = [f"module {nl.top} ("]
    decl = []
    for pn, p in ports.items():
        w = len(p["bits"])
        decl.append(f"  {'input' if p['dir'] == 'in' else 'output'} {'[' + str(w - 1) + ':0] ' if w > 1 else ''}{pn}")
    lines.append(",\n".join(decl) + ");")
    bits = set()
    for i in insts:
        bits |= {b for b in i.conns.values() if isinstance(b, int)}
    for b in sorted(bits):
        if b not in pin_name:
            lines.append(f"  wire _n{b};")
    for i in insts:
        pins = ", ".join(f".{p}({net(b)})" for p, b in sorted(i.conns.items()))
        nm = names[i.id].lstrip("\\")
        nm = nm if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", nm) else "\\" + nm + " "
        lines.append(f"  {lib.PREFIX}{i.type} {nm} ({pins}, .VPWR(1'b1), .VGND(1'b0), .VPB(1'b1), .VNB(1'b0));")
    for pn, p in ports.items():
        if p["dir"] == "out":
            for k, b in enumerate(p["bits"]):
                lines.append(f"  assign {pn}[{k}] = {net(b)};" if len(p["bits"]) > 1 else f"  assign {pn} = {net(b)};")
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


def _operand_port(nl: Netlist) -> str:
    ins = [(n, p) for n, p in nl.ports.items() if p["dir"] == "in" and n not in ("clk", "rst_n", "ena")]
    if "ui_in" in nl.ports:
        return "ui_in"
    return max(ins, key=lambda np: len(np[1]["bits"]))[0]


def _target_bit(nl: Netlist) -> tuple[str, int]:
    drv = nl.driver()
    outs = [(n, p) for n, p in nl.ports.items() if p["dir"] == "out"]
    outs.sort(key=lambda np: (np[0] != "uo_out", np[0]))
    for n, p in outs:
        for k, b in enumerate(p["bits"]):
            if isinstance(b, int) and b in drv:
                return n, k
    return outs[0][0], 0


def plant(nl: Netlist, n: int, target: tuple[str, int] | None = None, res: dict | None = None) -> tuple[Netlist, dict]:
    """Splice `target |= (operand == n)` into nl. The operand is an 8-bit input port (ui_in on Tiny Tapeout
    chips); a chip without one (the warm-up) uses its widest register from the decompile `res`."""
    op = _operand_port(nl)
    obits = nl.ports[op]["bits"]
    if len(obits) < 8 and res and res.get("registers"):
        byid = {i.id: i for i in nl.insts}
        rname, fids = max(res["registers"].items(), key=lambda kv: len(kv[1]))
        if len(fids) >= 2:
            op, obits = rname, [byid[f].conns["Q"] for f in fids][:8]
    w = len(obits)
    n = n % (1 << w)
    tport, tk = target or _target_bit(nl)
    nxt = max([b for i in nl.insts for b in i.conns.values() if isinstance(b, int)] +
              [b for p in nl.ports.values() for b in p["bits"] if isinstance(b, int)]) + 1
    names = {i.id: nl._orig.get(i.id, i.id) for i in nl.insts}
    insts = list(nl.insts)
    new = []
    # new instance names in the chip's own style: unused numbers from inside its existing `_N_` range,
    # so the names don't mark the planted cells either
    used = {int(m.group(1)) for nm in names.values() for m in [re.fullmatch(r"\\?_(\d+)_", nm.strip())] if m}
    lo, hi = (min(used), max(used)) if used else (0, 64)
    pad = max((len(m.group(1)) for nm in names.values() for m in [re.fullmatch(r"\\?_(\d+)_", nm.strip())] if m), default=1)
    # "decap-plant" is the project's old name, kept so planted copies stay identical.
    rng = random.Random(f"decap-plant:{nl.top}:{n}")
    free = [k for k in range(lo, hi + 16) if k not in used]
    rng.shuffle(free)

    def add(t, conns):
        iid = f"p{len(new)}"
        num = free.pop() if free else hi + 1 + len(new)
        names[iid] = f"_{num:0{pad}d}_"
        inst = Inst(iid, t, conns)
        insts.append(inst); new.append(names[iid])
        return inst

    lits = []
    for k, b in enumerate(obits):
        if (n >> k) & 1:
            lits.append(b)
        else:
            add("inv_1", {"A": b, "Y": nxt}); lits.append(nxt); nxt += 1
    while len(lits) > 1:
        grp, lits = lits[:4], lits[4:]
        t = {1: "buf_1", 2: "and2_1", 3: "and3_1", 4: "and4_1"}[len(grp)]
        pins = {1: ["A"], 2: ["A", "B"], 3: ["A", "B", "C"], 4: ["A", "B", "C", "D"]}[len(grp)]
        add(t, {**dict(zip(pins, grp)), "X": nxt}); lits.append(nxt); nxt += 1
    trig = lits[0]
    ports = {pn: {"dir": p["dir"], "bits": list(p["bits"])} for pn, p in nl.ports.items()}
    orig = ports[tport]["bits"][tk]
    add("or2_1", {"A": orig, "B": trig, "X": nxt})
    ports[tport]["bits"][tk] = nxt
    text = write_netlist(nl, insts, ports, names)
    planted = netlist.load(text=text, top=nl.top)
    inserted = sorted([cid for cid, nm in planted._orig.items() if nm in set(new)], key=lambda c: int(c[1:]))
    return planted, {"n": n, "operand": op, "target": f"{tport}[{tk}]" if len(ports[tport]["bits"]) > 1 else tport,
                     "inserted_cells": inserted}


# ---------- blind rare-trigger flag
def flags(dec, res: dict, k: int = FLAG_K, max_exact: int = 18) -> list[dict]:
    nl, s = dec.nl, dec.s
    env, m = s.random_env(4096, seed=99)
    v = s.run(env, m)
    fanout: dict[int, list] = {}
    for i in nl.insts:
        for p, b in i.conns.items():
            if p in i.cell.inputs and isinstance(b, int):
                fanout.setdefault(b, []).append(i)
    cand = []
    for i in s.order:
        for p in i.cell.outputs:
            b = i.conns.get(p)
            if not isinstance(b, int) or b in dec.src:
                continue
            ones = bin(v.get(b, 0)).count("1")
            if 64 * ones > 4096 and 64 * (4096 - ones) > 4096:
                continue
            sup = dec.support([b], {})
            if len(sup) < k or len(sup) > max_exact:
                continue
            tt, order = dec.truth(b, {}, sup)
            space = 1 << len(order)
            c1 = bin(tt).count("1")
            rare1 = c1 * (1 << k) <= space and c1 > 0
            rare0 = (space - c1) * (1 << k) <= space and c1 < space
            if rare1 or rare0:
                cand.append((b, i, sup, c1 if rare1 else space - c1, space, rare0))
    cand.sort(key=lambda c: (-len(c[2]), c[0]))
    accepted, covered = [], set()
    for c in cand:
        b = c[0]
        if b in covered:
            continue
        cells, _ = s.cone([b])
        nets = {dec._out_bit(x) for x in cells}
        covered |= nets
        accepted.append(c)
    vlines = (res.get("verilog") or res["decompiled"]["verilog"]).splitlines()
    out = []
    for fi, (b, inst, sup, cnt, space, rare0) in enumerate(accepted):
        plain, dec.plain = dec.plain, False      # the flag states its condition even when the decompile is plain gates
        try:
            cond = dec.condition(b, {})
        finally:
            dec.plain = plain
        if cond:
            cond = ("raw", dec.rn(cond[1]), cond[2])
        text = cond[1] if cond else f"a rare condition over {len(sup)} bits"
        line = next((j + 1 for j, l in enumerate(vlines) if cond and cond[1] in l), 0) or res["cell_lines"].get(inst.id, 0)
        trig_cells = sorted(s.cone([b])[0], key=lambda c: int(c[1:]))
        reach, sinks = _fanout_cone(nl, b, fanout, s)
        sig = sinks[0] if sinks else nl.bitname.get(b, "")
        out.append({"id": f"f{fi}", "kind": "rare-trigger", "line": line, "signal": sig, "condition": text,
                    "rate": f"{cnt}/{space}" if cnt > 1 else f"1/{space}", "fires_on": "0" if rare0 else "1",
                    "cells": trig_cells, "cone": sorted(set(trig_cells) | reach, key=lambda c: int(c[1:])), "reaches": sinks[:8],
                    "_net": b})
    return out


def _fanout_cone(nl, b, fanout, s):
    """Cells the net can influence before the next clock edge, and the outputs/registers it reaches."""
    names = {}
    for pn, p in nl.ports.items():
        if p["dir"] == "out":
            for k, x in enumerate(p["bits"]):
                if isinstance(x, int):
                    names.setdefault(x, f"{pn}[{k}]" if len(p["bits"]) > 1 else pn)
    reach, sinks, seen, stack = set(), [], set(), [b]
    while stack:
        x = stack.pop()
        if x in seen:
            continue
        seen.add(x)
        if x in names:
            sinks.append(names[x])
        for i in fanout.get(x, []):
            reach.add(i.id)
            if i.cell.seq:
                sinks.append(f"flop {i.id}")
                continue
            for p in i.cell.outputs:
                if isinstance(i.conns.get(p), int):
                    stack.append(i.conns[p])
    return reach, sinks


# ---------- remove and prove
def fix(planted: Netlist, flag: dict, clean: Netlist, clean_res: dict) -> dict:
    t0 = time.time()
    b = flag["_net"]
    inactive = "1" if flag.get("fires_on") == "0" else "0"
    names = {i.id: planted._orig.get(i.id, i.id) for i in planted.insts}
    insts = [Inst(i.id, i.type, {p: (inactive if x == b and p in i.cell.inputs else x) for p, x in i.conns.items()})
             for i in planted.insts]
    ports = {pn: {"dir": p["dir"], "bits": [inactive if x == b else x for x in p["bits"]]} for pn, p in planted.ports.items()}
    # drop the now-dead trigger cells
    insts = _drop_dead(insts, ports)
    removed = sorted({i.id for i in planted.insts} - {i.id for i in insts}, key=lambda c: int(c[1:]))
    text = write_netlist(planted, insts, ports, names)
    fixed = netlist.load(text=text, top=planted.top)
    # pair fixed nets with the clean decompile's named nets through original instance names
    clean_drv = clean.driver()
    by_orig = {nm: cid for cid, nm in fixed._orig.items()}
    fixed_by_id = {i.id: i for i in fixed.insts}
    mapped = {}
    for cb, nm in clean_res["proof_names"].items():
        d = clean_drv.get(cb)
        if not d:
            continue
        inst, pin = d
        fid = by_orig.get(clean._orig.get(inst.id))
        if fid and pin in fixed_by_id[fid].conns and isinstance(fixed_by_id[fid].conns[pin], int):
            mapped[fixed_by_id[fid].conns[pin]] = nm
    pr = prove.prove(fixed, clean_res["verilog"], mapped)
    return {"removed_cells": removed, "verdict": "proven" if pr["ok"] else ("failed" if not pr.get("error") else "failed"),
            "detail": (f"With the trigger removed ({len(removed)} cells), Yosys proved the chip equal to the clean decompile "
                       f"({pr['points']} paired signals), which is itself proven equal to the original netlist.")
            if pr["ok"] else f"Proof after removal: {pr['proven']}/{pr['points']} signals; {pr.get('error') or ''}",
            "seconds": round(time.time() - t0, 2), "proof": {k: pr[k] for k in ("points", "proven", "control_caught")},
            "_fixed": fixed}


def _drop_dead(insts, ports):
    live_out = {b for p in ports.values() if p["dir"] == "out" for b in p["bits"] if isinstance(b, int)}
    while True:
        used = set(live_out)
        for i in insts:
            used |= {b for p, b in i.conns.items() if p in i.cell.inputs and isinstance(b, int)}
        keep = [i for i in insts if i.cell.seq or any(isinstance(i.conns.get(p), int) and i.conns[p] in used for p in i.cell.outputs)]
        if len(keep) == len(insts):
            return keep
        insts = keep
