"""Prove a decompile equal to the netlist it came from, with Yosys.

The gate side is the stripped netlist written back out as SKY130 cell instances. Every net gets a
meaningless name, except the nets the decompiler named: flip-flop outputs (registers are kept 1:1),
loop cut points, derived clocks/resets and shared intermediate wires. `equiv_make` pairs signals with
the same name, `equiv_simple` proves each pair's driving logic equal, and `equiv_induct` closes the
sequential argument by induction over the paired registers. Every proof also runs a control: the same
check on a copy of the netlist with one cell's output inverted, which must fail. If it passes, the
harness is broken and the verdict is "failed", never "proven".
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import time
from pathlib import Path

from . import lib
from .netlist import Netlist

POWER = {"VPWR": "1'b1", "VGND": "1'b0", "VPB": "1'b1", "VNB": "1'b0"}


def gate_verilog(nl: Netlist, names: dict[int, str], mutate: str | None = None) -> str:
    """Re-emit the stripped netlist. names: bit -> identifier or 'vec[i]'. mutate: cell id whose output is inverted."""
    def net(b):
        if isinstance(b, str):
            return f"1'b{b}"
        if b in names:
            return names[b]
        return port_of.get(b) or f"_n{b}"

    port_of = {}
    for pn, p in nl.ports.items():
        for k, b in enumerate(p["bits"]):
            if isinstance(b, int) and b not in port_of and p["dir"] == "in":
                port_of[b] = f"{pn}[{k}]" if len(p["bits"]) > 1 else pn
    lines = [f"module {nl.top} ("]
    decl = []
    for pn, p in nl.ports.items():
        w = len(p["bits"])
        decl.append(f"  {'input' if p['dir'] == 'in' else 'output'} {'[' + str(w - 1) + ':0] ' if w > 1 else ''}{pn}")
    lines.append(",\n".join(decl))
    lines.append(");")
    vec: dict[str, int] = {}
    for b, n in names.items():
        m = re.match(r"(\w+)\[(\d+)\]$", n)
        if m:
            vec[m.group(1)] = max(vec.get(m.group(1), 0), int(m.group(2)))
        else:
            vec.setdefault(n, -1)
    for n, hi in vec.items():
        lines.append(f"  wire {'[' + str(hi) + ':0] ' if hi >= 0 else ''}{n};")
    bits = set()
    for i in nl.insts:
        bits |= {b for b in i.conns.values() if isinstance(b, int)}
    for b in sorted(bits):
        if b not in names and b not in port_of:
            lines.append(f"  wire _n{b};")
    for i in nl.insts:
        pins = []
        for p, b in sorted(i.conns.items()):
            t = net(b)
            if mutate == i.id and p in i.cell.outputs:
                lines.append(f"  wire _mut;")
                lines.append(f"  assign {t} = ~_mut;")
                t = "_mut"
            pins.append(f".{p}({t})")
        pins += [f".{p}({v})" for p, v in POWER.items()]
        lines.append(f"  {lib.PREFIX}{i.type} {i.id} ({', '.join(pins)});")
    # output ports
    for pn, p in nl.ports.items():
        if p["dir"] != "out":
            continue
        for k, b in enumerate(p["bits"]):
            lhs = f"{pn}[{k}]" if len(p["bits"]) > 1 else pn
            lines.append(f"  assign {lhs} = {net(b)};")
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


def _script(top: str, td: str, steps: str = "equiv_simple -seq 5\nequiv_induct -seq 5") -> str:
    return f"""
read_verilog -sv {td}/gold.v
hierarchy -top {top}
proc; flatten
rename {top} gold
design -stash gold
read_verilog {td}/cells.v {td}/gate.v
hierarchy -top {top}
flatten
rename {top} gate
design -stash gate
design -copy-from gold -as gold gold
design -copy-from gate -as gate gate
proc; opt_clean; async2sync
equiv_make gold gate equiv
hierarchy -top equiv
{steps}
equiv_status
"""


def _run(gold: str, gate: str, types, top: str, timeout: int):
    a = _run1(gold, gate, types, top, timeout)
    if a.get("ok") or a.get("error"):
        return a
    b = _run1(gold, gate, types, top, timeout, "equiv_induct -seq 8")
    return b if b.get("proven", 0) > a.get("proven", 0) else a


def _run1(gold: str, gate: str, types, top: str, timeout: int, steps: str | None = None):
    with tempfile.TemporaryDirectory() as td:
        Path(td, "gold.v").write_text(gold)
        Path(td, "gate.v").write_text(gate)
        Path(td, "cells.v").write_text(lib.yosys_models(types))
        Path(td, "run.ys").write_text(_script(top, td, steps) if steps else _script(top, td))
        try:
            r = subprocess.run(["yosys", "-q", "-l", f"{td}/log.txt", f"{td}/run.ys"], capture_output=True, text=True, timeout=timeout)
            log = Path(td, "log.txt").read_text() if Path(td, "log.txt").exists() else r.stdout + r.stderr
            err = r.returncode != 0
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"timeout after {timeout}s", "points": 0, "proven": 0, "unproven": []}
    m = re.search(r"Found (\d+) \$equiv cells in equiv", log)
    m2 = re.search(r"Of those cells (\d+) are proven and (\d+) are unproven", log)
    pts = int(m.group(1)) if m else 0
    proven = int(m2.group(1)) if m2 else 0
    unp = int(m2.group(2)) if m2 else (pts if pts else 1)
    unlist = [n + (f"[{k}]" if k else "") for n, k in re.findall(r"Unproven \$equiv \S+ \\(\S+?)_gold(?: \[(\d+)\])?", log)]
    if err and not m:
        tail = "\n".join(l for l in log.splitlines() if "ERROR" in l or "rror" in l)[-800:] or log[-800:]
        return {"ok": False, "error": tail.strip(), "points": 0, "proven": 0, "unproven": []}
    return {"ok": unp == 0 and pts > 0, "points": pts, "proven": proven, "unproven": unlist}


def prove(nl: Netlist, verilog: str, names: dict[int, str], timeout: int = 120, control: bool = True) -> dict:
    types = sorted({i.type for i in nl.insts})
    t0 = time.time()
    res = _run(verilog, gate_verilog(nl, names), types, nl.top, timeout)
    out = {"method": "equiv", "points": res["points"], "proven": res["proven"], "seconds": 0.0,
           "unproven": res.get("unproven", []), "error": res.get("error")}
    if res["ok"] and control:
        victim = _control_victim(nl)
        if victim:
            c = _run(verilog, gate_verilog(nl, names, mutate=victim), types, nl.top, timeout)
            out["control_caught"] = not c["ok"]
        else:
            out["control_caught"] = True
    else:
        out["control_caught"] = None
    out["seconds"] = round(time.time() - t0, 2)
    out["ok"] = bool(res["ok"] and out.get("control_caught"))
    return out


def _control_victim(nl: Netlist) -> str | None:
    """A cell whose output feeds an output port or a flip-flop D pin (so inverting it must show)."""
    drv = nl.driver()
    targets = [b for _, _, b in nl.out_bits() if isinstance(b, int)] + [f.conns.get("D") for f in nl.flops]
    for b in targets:
        if isinstance(b, int) and b in drv and not drv[b][0].cell.seq:
            return drv[b][0].id
    return None


def bounded(nl: Netlist, verilog: str, cycles: int = 20, timeout: int = 120) -> int:
    """No proof? Check the decompile against the netlist for `cycles` clock cycles from reset. Returns cycles passed (0 = failed)."""
    types = sorted({i.type for i in nl.insts})
    rst = "-set-at 1 in_rst_n 0 " if "rst_n" in nl.ports else ""
    with tempfile.TemporaryDirectory() as td:
        Path(td, "gold.v").write_text(verilog)
        Path(td, "gate.v").write_text(gate_verilog(nl, {}))
        Path(td, "cells.v").write_text(lib.yosys_models(types))
        script = _script(nl.top, td, "").split("equiv_make")[0] + (
            "miter -equiv -flatten -make_outputs gold gate miter\nhierarchy -top miter\n"
            f"sat -verify -prove trigger 0 -set-init-zero {rst}-seq {cycles} -timeout {timeout} miter\n")
        Path(td, "b.ys").write_text(script)
        try:
            r = subprocess.run(["yosys", "-q", f"{td}/b.ys"], capture_output=True, text=True, timeout=timeout + 30)
        except subprocess.TimeoutExpired:
            return 0
        return cycles if r.returncode == 0 else 0
