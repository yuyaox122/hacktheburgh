"""Read a Tiny Tapeout GDS with KLayout: every standard-cell instance (name from GDS property 61, type, box)
and the metal stack as rectangles, all in µm."""
from __future__ import annotations

import json
from pathlib import Path

import klayout.db as db

# SKY130 GDS layer numbers (drawing). Order = bottom to top.
LAYERS = {
    "li1": (67, 20), "mcon": (67, 44), "met1": (68, 20), "via": (68, 44), "met2": (69, 20),
    "via2": (69, 44), "met3": (70, 20), "via3": (70, 44), "met4": (71, 20), "via4": (71, 44), "met5": (72, 20),
}
RECURSIVE = {"met1"}          # also take shapes inside cells (the power rails along every row)
PR_BOUNDARY = (235, 4)


def _boxes(region: db.Region, dbu: float) -> list[list[float]]:
    out = []
    for poly in region.merged().each():
        for tz in poly.decompose_trapezoids():
            b = tz.bbox()
            out.append([round(b.left * dbu, 3), round(b.bottom * dbu, 3), round(b.right * dbu, 3), round(b.top * dbu, 3)])
    return out


def read(gds: str | Path) -> dict:
    ly = db.Layout()
    ly.read(str(gds))
    top = ly.top_cell()
    dbu = ly.dbu
    pr = ly.find_layer(*PR_BOUNDARY)
    cells = []
    for inst in top.each_inst():
        c = inst.cell
        name = inst.property(61)
        typ = c.name.split("__", 1)[-1]
        if not c.name.startswith("sky130_"):
            continue                        # vias and other non-cell instances
        box = c.bbox_per_layer(pr) if pr is not None and not c.bbox_per_layer(pr).empty() else c.bbox()
        h = box.height() * dbu
        if abs(((h - 0.48) / 2.72) - round((h - 0.48) / 2.72)) < 0.01 and h > 1:
            box = box.enlarged(-int(round(0.19 / dbu)), -int(round(0.24 / dbu)))   # drop the n-well overhang
        b = box.transformed(inst.cplx_trans)
        cells.append({"name": str(name) if name is not None else None, "type": typ,
                      "box": [round(b.left * dbu, 3), round(b.bottom * dbu, 3), round(b.right * dbu, 3), round(b.top * dbu, 3)]})
    layers = {}
    for lname, (l, d) in LAYERS.items():
        li = ly.find_layer(l, d)
        if li is None:
            continue
        if lname in RECURSIVE:
            reg = db.Region(top.begin_shapes_rec(li))
        else:
            reg = db.Region(top.shapes(li))
            # vias placed as via-cell instances (e.g. generated via stacks) are one level down
            for inst in top.each_inst():
                if not inst.cell.name.startswith("sky130_"):
                    sub = db.Region(inst.cell.begin_shapes_rec(li))
                    sub.transform(inst.cplx_trans)
                    reg += sub
        if not reg.is_empty():
            layers[lname] = _boxes(reg, dbu)
    pins = {}
    for li in ly.layer_indexes():
        for s in top.shapes(li).each():
            if s.is_text() and s.text.string not in ("VPWR", "VGND"):
                pins[s.text.string] = [round(s.text.x * dbu, 3), round(s.text.y * dbu, 3)]
    bb = top.dbbox()
    return {"name": top.name, "die": [round(bb.width(), 3), round(bb.height(), 3)], "cells": cells, "layers": layers,
            "pins": pins}


if __name__ == "__main__":
    import sys
    d = read(sys.argv[1])
    print(d["name"], d["die"], len(d["cells"]), {k: len(v) for k, v in d["layers"].items()})
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(d))
