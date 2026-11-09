"""Chip inputs for the redstone techmap: a Tiny Tapeout 08 project's netlist and GDS, and the SKY130 HD Liberty
JSON its cells need (all cached in decap/data/, see minecraft/common/fetch.py), read as ports, cells, pins, cell
positions by instance name and pin label positions.

    python -m minecraft.redstone.chipdata fetch tt_um_sign_addsub
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

from ..common import fetch as F
from ..common.cells import celldef
from ..common.netlist import Netlist, parse

CONST0, CONST1 = "1'b0", "1'b1"


def fetch(chip: str) -> Path:
    """Netlist, GDS, info.yaml and LICENSE, plus the Liberty JSON of every logic cell type it uses."""
    d = F.fetch_chip(chip)
    nl = read_netlist(chip)
    for t in sorted({c.type for c in nl.cells if not c.physical}):
        celldef(t)
    return d


def read_netlist(chip: str) -> Netlist:
    # VPWR / VGND port declarations count as (unused) inputs here, as they always have: the random test vectors
    # in sim.py and check.py include them.
    return parse(F.netlist_path(chip), power_ports=True, const=(CONST0, CONST1))


# ---------------------------------------------------------------- GDS (placement + pin labels)

def _gdsreal(b: bytes) -> float:
    if not any(b):
        return 0.0
    s = -1 if b[0] & 0x80 else 1
    return s * int.from_bytes(b[1:8], "big") / (1 << 56) * 16 ** ((b[0] & 0x7F) - 64)


def _clean(s: str) -> str:
    s = s.strip()
    return s[1:].strip() if s.startswith("\\") else s


def read_gds(chip: str) -> dict:
    """{'cells': {inst: (x_um, y_um)}, 'pins': {label: (x_um, y_um)}, 'die': (w, h), 'flipped': {inst}} from the
    top structure. (x, y) is the SREF origin; for a mirrored cell (STRANS reflection, the FS rows) that origin is the
    cell's TOP edge, so its row's bottom is y - the row height. Plain Python: no KLayout needed."""
    d = F.gds_path(chip).read_bytes()
    i, cur, el, dbu, structs, order = 0, None, None, 1e-9, {}, []
    while i < len(d):
        n, rt = struct.unpack(">HB", d[i:i + 3])
        p = d[i + 4:i + n]
        i += n
        if n == 0:
            break
        if rt == 0x03:
            dbu = _gdsreal(p[8:16])
        elif rt == 0x06:
            cur = {"srefs": [], "texts": [], "bnds": []}
            structs[p.rstrip(b"\0").decode()] = cur
            order.append(p.rstrip(b"\0").decode())
        elif rt in (0x0A, 0x0C, 0x08):
            el = {"k": rt}
        elif el is None:
            continue
        elif rt == 0x0D:
            el["layer"] = struct.unpack(">h", p)[0]
        elif rt == 0x10:
            v = struct.unpack(">%di" % (len(p) // 4), p)
            el["xy"] = list(zip(v[0::2], v[1::2]))
        elif rt == 0x12:
            el["sname"] = p.rstrip(b"\0").decode()
        elif rt == 0x19:
            el["s"] = p.rstrip(b"\0").decode()
        elif rt == 0x2C:
            el["prop"] = p.rstrip(b"\0").decode()
        elif rt == 0x1A:
            el["strans"] = struct.unpack(">H", p[:2])[0]
        elif rt == 0x11:
            if el["k"] == 0x0A:
                cur["srefs"].append(el)
            elif el["k"] == 0x0C:
                cur["texts"].append(el)
            elif el["k"] == 0x08 and el.get("layer") == 235:
                cur["bnds"].append(el)
            el = None
    top = structs[order[-1]]
    um = dbu * 1e6
    cells = {_clean(r["prop"]): (r["xy"][0][0] * um, r["xy"][0][1] * um) for r in top["srefs"] if "prop" in r}
    pins = {t["s"]: (t["xy"][0][0] * um, t["xy"][0][1] * um) for t in top["texts"] if "s" in t}
    xs = [x for b in top["bnds"] for x, _ in b["xy"]] or [0, 161000]
    ys = [y for b in top["bnds"] for _, y in b["xy"]] or [0, 111520]
    flipped = {_clean(r["prop"]) for r in top["srefs"] if "prop" in r and r.get("strans", 0) & 0x8000}
    return {"cells": cells, "pins": pins, "die": ((max(xs) - min(xs)) * um, (max(ys) - min(ys)) * um),
            "flipped": flipped}


if __name__ == "__main__":
    if sys.argv[1:2] == ["fetch"]:
        for c in sys.argv[2:]:
            print(fetch(c))
    else:
        print(__doc__)
