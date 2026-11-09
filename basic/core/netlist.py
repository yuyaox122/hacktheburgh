"""Load a gate-level SKY130 netlist with Yosys and strip every internal name.

What survives stripping: cell types, which pins connect to which wires, and the top-level port names
(the Tiny Tapeout interface: ui_in, uo_out, uio_in, uio_out, uio_oe, ena, clk, rst_n). Cell ids are
assigned in an order derived from a hash of the original name, so the order leaks nothing.
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import lib

POWER_PORTS = {"VPWR": 1, "VGND": 0, "VDD": 1, "VSS": 0, "VPB": 1, "VNB": 0}


@dataclass
class Inst:
    id: str
    type: str
    conns: dict[str, int | str]   # pin -> bit id (int) or "0"/"1"

    @property
    def cell(self) -> lib.Cell:
        return lib.cell(self.type)


@dataclass
class Netlist:
    top: str
    ports: dict[str, dict]                 # name -> {"dir": "in"|"out", "bits": [bit | "0" | "1"]}
    insts: list[Inst]
    physical: int = 0                      # count of fill/tap/decap/diode cells dropped
    bitname: dict[int, str] = field(default_factory=dict)  # stripped name for every bit
    source_text: str = ""
    _orig: dict[str, str] = field(default_factory=dict, repr=False)  # cell id -> original instance name (not exported)

    # ----- derived views
    def driver(self) -> dict[int, tuple[Inst, str]]:
        d = {}
        for i in self.insts:
            for p in i.cell.outputs:
                b = i.conns.get(p)
                if isinstance(b, int):
                    d[b] = (i, p)
        return d

    def in_bits(self) -> list[tuple[str, int, int]]:
        return [(n, k, b) for n, p in self.ports.items() if p["dir"] == "in" for k, b in enumerate(p["bits"]) if isinstance(b, int)]

    def out_bits(self) -> list[tuple[str, int, int | str]]:
        return [(n, k, b) for n, p in self.ports.items() if p["dir"] == "out" for k, b in enumerate(p["bits"])]

    @property
    def flops(self) -> list[Inst]:
        return [i for i in self.insts if i.cell.seq]

    @property
    def logic(self) -> list[Inst]:
        return self.insts

    def area(self) -> float:
        return round(sum(i.cell.area for i in self.insts), 2)

    def stripped(self) -> dict:
        def nm(b):
            return b if isinstance(b, str) else self.bitname[b]
        return {
            "ports": {n: {"dir": p["dir"], "width": len(p["bits"])} for n, p in self.ports.items()},
            "cells": [{"id": i.id, "type": i.type, "pins": {p: nm(b) for p, b in sorted(i.conns.items())}} for i in self.insts],
        }


def _yosys_json(netlist_text: str, top: str | None) -> dict:
    with tempfile.TemporaryDirectory() as td:
        Path(td, "cells.v").write_text(lib.cell_models(netlist_text))
        Path(td, "net.v").write_text(netlist_text)
        script = f"read_verilog -lib {td}/cells.v\nread_verilog {td}/net.v\n" + \
                 (f"hierarchy -top {top}\n" if top else "hierarchy -auto-top\n") + f"flatten\nwrite_json {td}/out.json\n"
        r = lib.yosys("-q", "-p", script.replace("\n", "; "))
        if r.returncode:
            raise RuntimeError("yosys could not read the netlist:\n" + (r.stderr or r.stdout)[-2000:])
        return json.loads(Path(td, "out.json").read_text())


def load(path: str | Path | None = None, text: str | None = None, top: str | None = None) -> Netlist:
    text = text if text is not None else Path(path).read_text(errors="replace")
    j = _yosys_json(text, top)
    mods = {n: m for n, m in j["modules"].items() if not n.startswith(lib.PREFIX) and not m.get("attributes", {}).get("blackbox")}
    if top is None:
        top = next((n for n, m in mods.items() if m.get("attributes", {}).get("top")), None) or next(iter(mods))
    m = j["modules"][top]
    subst: dict[int, str] = {}
    ports = {}
    for pn, p in m["ports"].items():
        if pn in POWER_PORTS:
            for b in p["bits"]:
                if isinstance(b, int):
                    subst[b] = str(POWER_PORTS[pn])
            continue
        ports[pn] = {"dir": "in" if p["direction"] == "input" else "out", "bits": p["bits"]}

    def fix(b):
        return subst.get(b, b) if isinstance(b, int) else b

    for p in ports.values():
        p["bits"] = [fix(b) for b in p["bits"]]
    raw = []
    phys = 0
    for cname, c in m["cells"].items():
        t = c["type"]
        if re.match(r"sky130_(?!fd_)[a-z]+_sc_hd__", t):
            phys += 1
            continue
        if not t.startswith(lib.PREFIX):
            raise RuntimeError(f"contains a hard macro '{t}' whose netlist is not published, so bASIC cannot read inside it")
        t = t[len(lib.PREFIX):]
        ce = lib.cell(t)
        if ce.physical:
            phys += 1
            continue
        conns = {}
        for pin, bits in c["connections"].items():
            if pin in lib.PG or pin == "DIODE":
                continue
            conns[pin] = fix(bits[0]) if bits else "0"
        raw.append((cname, t, conns))
    # The "decap:" salt is the project's old name; changing it would renumber every cell id.
    raw.sort(key=lambda r: hashlib.sha1(("decap:" + r[0]).encode()).hexdigest())
    insts, orig = [], {}
    for k, (cname, t, conns) in enumerate(raw):
        insts.append(Inst(f"c{k}", t, conns))
        orig[f"c{k}"] = cname
    # conb cells are constants: fold their outputs into "0"/"1" everywhere
    const = {}
    for i in insts:
        if i.type.startswith("conb"):
            if isinstance(i.conns.get("HI"), int):
                const[i.conns["HI"]] = "1"
            if isinstance(i.conns.get("LO"), int):
                const[i.conns["LO"]] = "0"
    if const:
        insts = [i for i in insts if not i.type.startswith("conb")]
        for i in insts:
            i.conns = {p: const.get(b, b) if isinstance(b, int) else b for p, b in i.conns.items()}
        for p in ports.values():
            p["bits"] = [const.get(b, b) if isinstance(b, int) else b for b in p["bits"]]
    nl = Netlist(top, ports, insts, phys, source_text=text, _orig=orig)
    names = {}
    for n, p in ports.items():
        for k, b in enumerate(p["bits"]):
            if isinstance(b, int) and b not in names:
                names[b] = f"{n}[{k}]" if len(p["bits"]) > 1 else n
    k = 0
    for i in insts:
        for pin in sorted(i.conns):
            b = i.conns[pin]
            if isinstance(b, int) and b not in names:
                names[b] = f"n{k}"; k += 1
    nl.bitname = names
    return nl
