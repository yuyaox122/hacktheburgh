"""Build web/public/data/design.json (the Stage view) for a real Tiny Tapeout 8 chip, from its published GDS.

Tiny Tapeout publishes each project's GDS, LEF and gate-level netlist (Apache-2.0), but no DEF, so wires carry
no net names. This script recovers them from the geometry, a small connectivity extraction:
  1. Cells: every SREF in the top cell. Logic cells are matched to the netlist by instance name (GDS property 61)
     and keyed by Decap's cell ids; fill/tap/decap/diode become `phys`.
  2. Wires: every top-level rectangle on li1/met1-met4 and every via cut (mcon, via, via2, via3). Rectangles that
     touch on one layer, or meet through a via, are one conductor (union-find).
  3. Names: a conductor touching a cell's signal pin (the cell's li1 shape under its pin label) or a port label on
     met4 takes that pin's net from the netlist, in Decap's stripped names (n12, ui_in[0]) so the Stage simulator,
     the chip page and the decompile agree. Conductors that reach two different nets are reported (should be 0).
The source pane is Decap's own decompiled Verilog: each cell points at the line that accounts for it, and the
recognised blocks act as "modules" for colouring.

Shape: CONTRACTS.md §3 (same as ingest_def.py) plus `title`, `intro`, `hint` and `io` (named input/output fields
from the project's info.yaml pinout, for the simulator bar).

Usage: python -I ingest/ingest_gds.py [--chip tt_um_Electom_cla_4bits] [--out web/public/data/design.json]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import struct
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path as _Path

CHIP_NOTES: dict = json.loads((_Path(__file__).with_name("chip_notes.json")).read_text()) if _Path(__file__).with_name("chip_notes.json").is_file() else {}
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import netlist as NL  # noqa: E402

TT_REPO = "TinyTapeout/tinytapeout-08"
CONDUCTOR = {(67, 20): "li1", (68, 20): "met1", (69, 20): "met2", (70, 20): "met3", (71, 20): "met4", (71, 16): "met4"}
VIAS = {(67, 44): ("mcon", "li1", "met1"), (68, 44): ("via", "met1", "met2"), (69, 44): ("via2", "met2", "met3"),
        (70, 44): ("via3", "met3", "met4")}
PORT_LABEL = (71, 5)
PHYSICAL = re.compile(r"^(fill|decap|tapvpwrvgnd|diode|fakediode|tap)_")
PREFIX = re.compile(r"^sky130_(fd|ef)_sc_hd__")
POWER = {"VPWR", "VGND", "VPB", "VNB"}


# ---------------------------------------------------------------------------------------------- GDS reader
def _real8(b: bytes) -> float:
    sign = -1 if b[0] & 0x80 else 1
    exp = (b[0] & 0x7F) - 64
    mant = int.from_bytes(b[1:8], "big") / float(1 << 56)
    return sign * mant * 16.0 ** exp


def read_gds(path: Path) -> tuple[dict, float]:
    """{structure name: [element]}, and microns per database unit."""
    d = path.read_bytes()
    i, structs, cur, el, um = 0, {}, None, None, 0.001
    kinds = {0x08: "B", 0x09: "P", 0x0A: "S", 0x0B: "A", 0x0C: "T"}
    while i + 4 <= len(d):
        ln, rt = struct.unpack(">HB", d[i:i + 3])
        if ln < 4:
            break
        body = d[i + 4:i + ln]
        if rt == 0x03:
            um = _real8(body[8:16]) / 1e-6
        elif rt == 0x06:
            cur = structs.setdefault(body.rstrip(b"\0").decode(), [])
        elif rt in kinds:
            el = {"t": kinds[rt], "props": []}
        elif el is not None:
            if rt == 0x0D:
                el["layer"] = struct.unpack(">h", body)[0]
            elif rt in (0x0E, 0x16):
                el["dt"] = struct.unpack(">h", body)[0]
            elif rt == 0x10:
                v = struct.unpack(f">{len(body) // 4}i", body)
                el["xy"] = list(zip(v[0::2], v[1::2]))
            elif rt == 0x12:
                el["sname"] = body.rstrip(b"\0").decode()
            elif rt == 0x19:
                el["text"] = body.rstrip(b"\0").decode()
            elif rt == 0x2C:
                el["props"].append(body.rstrip(b"\0").decode(errors="replace"))
            elif rt == 0x1A:
                el["reflect"] = bool(struct.unpack(">H", body)[0] & 0x8000)
            elif rt == 0x1C:
                el["angle"] = _real8(body)
            elif rt == 0x11:
                if cur is not None:
                    cur.append(el)
                el = None
        i += ln
    return structs, um


def transform(pts, origin, reflect=False, angle=0.0):
    """GDS SREF placement: reflect about x, rotate, translate."""
    a = math.radians(angle or 0.0)
    c, s = round(math.cos(a)), round(math.sin(a))
    out = []
    for x, y in pts:
        if reflect:
            y = -y
        out.append((x * c - y * s + origin[0], x * s + y * c + origin[1]))
    return out


def bbox(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def inside(pt, poly) -> bool:
    """Point in polygon (boundary counts as inside)."""
    x, y = pt
    n, hit = len(poly), False
    for k in range(n):
        (x1, y1), (x2, y2) = poly[k], poly[(k + 1) % n]
        if min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2) and \
                (x2 - x1) * (y - y1) == (y2 - y1) * (x - x1):
            return True
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def touches(r, s) -> bool:
    return r[0] <= s[2] and s[0] <= r[2] and r[1] <= s[3] and s[1] <= r[3]


def rect_meets_poly(r, poly) -> bool:
    if not touches(r, bbox(poly)):
        return False
    corners = [(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3]), ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)]
    return any(inside(c, poly) for c in corners) or any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for x, y in poly)


class DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[a] = b


class Grid:
    """Rectangles bucketed on a coarse grid, for overlap queries."""

    def __init__(self, step=2000):
        self.step, self.b = step, defaultdict(list)

    def keys(self, r):
        s = self.step
        for gx in range(int(r[0] // s), int(r[2] // s) + 1):
            for gy in range(int(r[1] // s), int(r[3] // s) + 1):
                yield gx, gy

    def add(self, r, k):
        for key in self.keys(r):
            self.b[key].append(k)

    def near(self, r):
        seen = set()
        for key in self.keys(r):
            for k in self.b.get(key, ()):
                if k not in seen:
                    seen.add(k)
                    yield k


# ---------------------------------------------------------------------------------------------- inputs
def ensure_gds(chip: str) -> Path:
    p = ROOT / "data" / "gds" / f"{chip}.gds"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        raw = subprocess.run(["gh", "api", f"repos/{TT_REPO}/contents/projects/{chip}/{chip}.gds",
                              "-H", "Accept: application/vnd.github.raw"], capture_output=True, check=True).stdout
        p.write_bytes(raw)
    return p


def pinout(chip: str) -> dict[str, str]:
    """info.yaml pinout: {'ui[0]': 'a[0]', ...} (non-empty labels only). No YAML dependency: the block is flat."""
    f = ROOT / "data" / "tt08" / chip / "info.yaml"
    out = {}
    if f.exists():
        for m in re.finditer(r'^\s*(u[io]o?\[\d\]|uio\[\d\]):\s*"([^"]*)"', f.read_text(), re.M):
            if m.group(2).strip():
                out[m.group(1)] = m.group(2).strip()
    return out


def info_field(chip: str, key: str) -> str:
    f = ROOT / "data" / "tt08" / chip / "info.yaml"
    m = re.search(rf'^\s*{key}:\s*"?([^"\n]*)"?', f.read_text(), re.M) if f.exists() else None
    return m.group(1).strip() if m else ""


def io_fields(pins: dict[str, str], reads: set[str]) -> dict:
    """Group pinout labels into named numbers: a[0..3] on ui[0..3] -> {name: 'a', bits: ['ui_in[0]', ...]}."""
    fields = {"in": defaultdict(dict), "out": defaultdict(dict)}
    for pin, label in pins.items():
        m = re.match(r"(ui|uo|uio)\[(\d)\]", pin)
        lm = re.match(r"([A-Za-z_][\w ]*?)\s*(?:\[(\d+)\])?$", label)
        if not m or not lm:
            continue
        kind, k = m.group(1), int(m.group(2))
        if kind == "ui":
            net, d = f"ui_in[{k}]", "in"
        elif kind == "uo":
            net, d = f"uo_out[{k}]", "out"
        else:
            net, d = (f"uio_in[{k}]", "in") if f"uio_in[{k}]" in reads else (f"uio_out[{k}]", "out")
        name = lm.group(1).strip().replace(" ", "_")
        fields[d][name][int(lm.group(2) or 0)] = net
    return {d: [{"name": n, "bits": [bits[i] for i in sorted(bits)]} for n, bits in f.items()] for d, f in fields.items()}


# ---------------------------------------------------------------------------------------------- build
def build(chip: str) -> dict:
    res = json.loads((ROOT / "out" / "chips" / f"{chip}.json").read_text())
    nl = NL.load(ROOT / "data" / "tt08" / chip / "netlist.v")
    by_orig = {o.replace("\\", ""): cid for cid, o in nl._orig.items()}
    rcells = {c["id"]: c for c in res["netlist"]["cells"]}
    structs, um = read_gds(ensure_gds(chip))
    top = structs[chip]

    def U(v):
        return round(v * um, 3)

    # die and placement
    pr = [e for e in top if e["t"] == "B" and (e["layer"], e.get("dt")) == (235, 4)]
    die = bbox(pr[0]["xy"]) if pr else bbox([p for e in top if e["t"] == "B" for p in e["xy"]])
    ox, oy = die[0], die[1]

    def cell_box(sname):
        els = structs.get(sname, [])
        b = [e for e in els if e["t"] == "B" and (e["layer"], e.get("dt")) in ((235, 4), (236, 0))]
        return bbox(b[0]["xy"]) if b else bbox([p for e in els if e["t"] == "B" for p in e["xy"]])

    cells, phys, rows, pin_polys, missing = [], [], {}, [], []
    for e in top:
        if e["t"] != "S":
            continue
        typ = PREFIX.sub("", e["sname"])
        o = e["xy"][0]
        x0, y0, x1, y1 = bbox(transform([(b[0], b[1]) for b in [cell_box(e["sname"])[:2], cell_box(e["sname"])[2:]]],
                                        o, e.get("reflect", False), e.get("angle", 0.0)))
        orient = {(False, 0): "N", (True, 0): "FS", (False, 180): "S", (True, 180): "FN"}.get(
            (e.get("reflect", False), round(e.get("angle", 0.0) or 0) % 360), "N")
        rows.setdefault(U(y0 - oy), orient)
        name = (e["props"] or [""])[-1].replace("\\", "")
        cid = by_orig.get(name)
        if PHYSICAL.match(typ) or cid is None:
            if not PHYSICAL.match(typ):
                missing.append(name or typ)
            phys.append({"type": typ, "x": U(x0 - ox), "y": U(y0 - oy), "w": U(x1 - x0), "orient": orient})
            continue
        rc = rcells.get(cid)
        if rc is None and typ.startswith("conb"):   # tie-off cells: core.netlist folds them into constants "0"/"1"
            rc = {"id": cid, "type": typ, "pins": {"HI": "1", "LO": "0"}}
        if rc is None or rc["type"] != typ:
            missing.append(f"{name}:{typ}")
            continue
        cells.append({"id": cid, "type": typ, "x": U(x0 - ox), "y": U(y0 - oy), "w": U(x1 - x0), "orient": orient,
                      "pins": rc["pins"], "inst": name})
        # signal pins: the cell's li1 shapes under each pin label
        els = structs[e["sname"]]
        li1 = [p["xy"][:-1] for p in els if p["t"] == "B" and (p["layer"], p.get("dt")) == (67, 20)]
        for lab in (p for p in els if p["t"] == "T" and p.get("layer") == 67 and p.get("text") in rc["pins"]):
            for poly in li1:
                if inside(lab["xy"][0], poly):
                    pin_polys.append((transform(poly, o, e.get("reflect", False), e.get("angle", 0.0)), rc["pins"][lab["text"]]))

    # conductors and vias
    shapes = []   # (kind 'c'|'v', layer label, rect, (gds layer, dt))
    for e in top:
        if e["t"] != "B":
            continue
        key = (e["layer"], e.get("dt"))
        if key in CONDUCTOR:
            shapes.append(("c", CONDUCTOR[key], bbox(e["xy"]), key))
        elif key in VIAS:
            shapes.append(("v", VIAS[key][0], bbox(e["xy"]), key))
    by_layer = defaultdict(Grid)
    for k, (kind, lay, r, _) in enumerate(shapes):
        if kind == "c":
            by_layer[lay].add(r, k)
    dsu = DSU(len(shapes))
    for k, (kind, lay, r, key) in enumerate(shapes):
        if kind == "c":
            for j in by_layer[lay].near(r):
                if j != k and touches(r, shapes[j][2]):
                    dsu.union(k, j)
        else:
            _, below, above = VIAS[key]
            for L in (below, above):
                for j in by_layer[L].near(r):
                    if touches(r, shapes[j][2]):
                        dsu.union(k, j)

    votes = defaultdict(Counter)
    li1_grid, mcon_grid = by_layer["li1"], Grid()
    for k, (kind, lay, r, _) in enumerate(shapes):
        if kind == "v" and lay == "mcon":
            mcon_grid.add(r, k)
    comps_of_net, unwired = defaultdict(set), 0
    for poly, net in pin_polys:
        b = bbox(poly)
        hit = False
        for g in (li1_grid, mcon_grid):
            for j in g.near(b):
                if rect_meets_poly(shapes[j][2], poly):
                    votes[dsu.find(j)][net] += 1
                    comps_of_net[net].add(dsu.find(j))
                    hit = True
        if not hit:
            unwired += 1
    ports = []
    port_dirs = {n: p["dir"] for n, p in nl.ports.items()}
    for e in top:
        if e["t"] == "T" and (e.get("layer"), e.get("dt")) == PORT_LABEL:
            t = e["text"]
            base = t.split("[")[0]
            pt = e["xy"][0]
            for j in by_layer["met4"].near((pt[0], pt[1], pt[0], pt[1])):
                if touches(shapes[j][2], (pt[0], pt[1], pt[0], pt[1])):
                    votes[dsu.find(j)][t] += 1
                    break
            if base in port_dirs and t not in POWER:
                ports.append({"name": t, "dir": port_dirs[base], "x": U(pt[0] - ox), "y": U(pt[1] - oy)})
    net_of, conflicts = {}, 0
    for root, c in votes.items():
        named = [n for n in c if n not in POWER] or list(c)
        if len({n for n in named}) > 1 and not (set(named) <= POWER):
            conflicts += 1
        net_of[root] = max(named, key=lambda n: c[n])

    routes, power = [], []
    for k, (kind, lay, r, key) in enumerate(shapes):
        net = net_of.get(dsu.find(k), "")
        x0, y0, x1, y1 = (U(r[0] - ox), U(r[1] - oy), U(r[2] - ox), U(r[3] - oy))
        if kind == "v":
            if net not in POWER:
                routes.append({"net": net, "layer": lay, "pts": [[round((x0 + x1) / 2, 3), round((y0 + y1) / 2, 3)]]})
            continue
        if net in POWER:
            if lay == "met4" and key == (71, 20):
                horiz = (x1 - x0) >= (y1 - y0)
                power.append({"net": net, "layer": lay, "width": round(y1 - y0 if horiz else x1 - x0, 3),
                              "pts": [[x0, round((y0 + y1) / 2, 3)], [x1, round((y0 + y1) / 2, 3)]] if horiz else
                                     [[round((x0 + x1) / 2, 3), y0], [round((x0 + x1) / 2, 3), y1]]})
            continue
        if (x1 - x0) >= (y1 - y0):
            pts = [[x0, round((y0 + y1) / 2, 3)], [x1, round((y0 + y1) / 2, 3)]]
        else:
            pts = [[round((x0 + x1) / 2, 3), y0], [round((x0 + x1) / 2, 3), y1]]
        if pts[0] != pts[1]:
            routes.append({"net": net, "layer": lay, "pts": pts})

    # Decap's decompile as the source; recognised blocks as "modules"
    src_name = f"{chip}.v"
    lines = res["decompiled"]["verilog"]
    block_of = {}
    modules = {}
    for b in res["blocks"]:
        mname = f"{b['kind']} {b['id']}"
        modules[mname] = {"file": src_name, "line": b["lines"][0]}
        for cid in b["cells"]:
            block_of[cid] = mname
    for c in cells:
        c["src"] = res["cell_lines"].get(c["id"]) or None
        c["module"] = block_of.get(c["id"], "tie-off" if c["type"].startswith("conb") else chip)

    reads = {n for c in cells for p, n in c["pins"].items() if p in ("A", "B", "C", "D", "A1", "A2", "B1", "B2", "S",
                                                                     "A0", "A_N", "B_N", "C1", "D1")}
    title = info_field(chip, "title") or chip
    author = info_field(chip, "author")
    io = io_fields(pinout(chip), {n for c in cells for n in c["pins"].values()} & {f"uio_in[{k}]" for k in range(8)} | reads)
    # pins worth a label: the pinout's named bits (with short names: a0, s3, co) and the clock/reset
    short = {b: (f["name"] + str(k) if len(f["bits"]) > 1 else f["name"]) for f in io["in"] + io["out"] for k, b in enumerate(f["bits"])}
    ports = [dict(p, label=short.get(p["name"], p["name"])) for p in ports if p["name"] in short or p["name"] in ("clk", "rst_n")] or ports
    sig_nets = {n for c in cells for n in c["pins"].values()}
    named = sum(1 for r in routes if r["net"])
    out = {
        "name": chip, "title": title, "pdk": "sky130_fd_sc_hd",
        "die": {"w": U(die[2] - die[0]), "h": U(die[3] - die[1])},
        "core": {"x0": min(c["x"] for c in cells + phys), "y0": min(c["y"] for c in cells + phys),
                 "x1": max(c["x"] + c["w"] for c in cells + phys), "y1": max(c["y"] for c in cells + phys) + 2.72},
        "rowHeight": 2.72, "site": 0.46,
        "rows": [{"y": y, "orient": o} for y, o in sorted(rows.items())],
        "ports": sorted(ports, key=lambda p: (p["name"].split("[")[0], int((re.findall(r"\d+", p["name"]) or [0])[0]))),
        "cells": cells, "phys": phys, "routes": routes, "power": power,
        "source": {"files": {src_name: lines}, "modules": modules, "instances": {}, "top": chip,
                   "mapping": "Source is Decap's decompiled Verilog (proven equal to these gates): each cell points at the line that accounts for it"},
        "io": io,
        "credit": f"{title}{f' by {author}' if author else ''}: Tiny Tapeout 8, {chip} (Apache-2.0). Layout from the TT08 GDS.",
        "intro": f"This is a real chip: {title}{f' by {author}' if author else ''}, manufactured on Tiny Tapeout 8 in SkyWater's 130 nm process. "
                 "Every coloured block is a standard cell (a tiny pre-made circuit); the metal above it is the real wiring from the published layout.",
        "hint": "Click any cell, wire or pin to see what it is. Set the inputs below and press Step (one clock): wires carrying a 1 glow.",
        "author": author or None,
        # Hand-written plain-English notes (ingest/chip_notes.json): how the chip works, and one thing to try in the simulator.
        **{k: v for k, v in CHIP_NOTES.get(chip, {}).items() if k in ("how", "try")},
        "counts": {"components": len(cells) + len(phys), "logic_cells": sum(1 for c in cells if not c["type"].startswith("conb")), "route_segments": sum(1 for r in routes if len(r["pts"]) == 2),
                   "vias": sum(1 for r in routes if len(r["pts"]) == 1), "named_routes": named,
                   "nets_on_wires": len({r['net'] for r in routes if r['net']}), "signal_nets": len(sig_nets),
                   "conflicts": conflicts, "unmatched_cells": len(missing), "pins": len(pin_polys), "pins_unwired": unwired,
                   "split_nets": sorted(n for n, cs in comps_of_net.items() if len(cs) > 1 and n not in ("0", "1"))},
    }
    return out


DEFAULT = "tt_um_Electom_cla_4bits"
DATA_OUT = ROOT / "web" / "public" / "data"


def summary(d: dict) -> str:
    c = d["counts"]
    return (f"{d['name']}: die {d['die']['w']}x{d['die']['h']} um, {c['logic_cells']} logic cells, "
            f"{c['route_segments']} wire segments, {c['vias']} vias, {c['nets_on_wires']}/{c['signal_nets']} nets on wires, "
            f"{c['conflicts']} conflicts, {c['unmatched_cells']} unmatched cells, {c['pins_unwired']}/{c['pins']} pins unwired, "
            f"split nets: {c['split_nets']}")


def ok(d: dict) -> tuple[bool, str]:
    """A chip goes on the Stage only if extraction is clean: every cell matched, no net shorted or split."""
    c = d["counts"]
    if c["unmatched_cells"]:
        return False, f"{c['unmatched_cells']} cells not matched to the netlist"
    if c["conflicts"]:
        return False, f"{c['conflicts']} wires reach two different nets"
    if c["split_nets"]:
        return False, f"nets in pieces: {', '.join(c['split_nets'][:4])}"
    return True, ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chip", default=DEFAULT)
    ap.add_argument("--out", default=str(DATA_OUT / "design.json"))
    ap.add_argument("--all", action="store_true", help="every chip with a GDS and a Decap result -> data/designs/<chip>.json "
                                                       "+ designs/index.json; the default chip also -> design.json")
    a = ap.parse_args()
    if not a.all:
        d = build(a.chip)
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(d, separators=(",", ":")))
        print("design.json:", summary(d))
        return
    out = DATA_OUT / "designs"
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for g in sorted((ROOT / "data" / "gds").glob("*.gds")):
        chip = g.stem
        if not (ROOT / "out" / "chips" / f"{chip}.json").exists():
            print(f"skip {chip}: no Decap result")
            continue
        try:
            d = build(chip)
        except Exception as e:  # noqa: BLE001  (one bad chip must not stop the rest)
            print(f"skip {chip}: {type(e).__name__}: {e}")
            continue
        good, why = ok(d)
        print(("ok   " if good else "skip ") + summary(d) + ("" if good else f"  <- {why}"))
        if not good:
            continue
        (out / f"{chip}.json").write_text(json.dumps(d, separators=(",", ":")))
        index.append({"chip": chip, "title": d["title"], "logic_cells": d["counts"]["logic_cells"],
                      "flops": sum(1 for c in d["cells"] if c["type"].startswith(("df", "edf", "sdf"))),
                      "die": d["die"], "io": bool(d["io"]["in"] or d["io"]["out"])})
        if chip == DEFAULT:
            (DATA_OUT / "design.json").write_text(json.dumps(d, separators=(",", ":")))
    (out / "index.json").write_text(json.dumps({"default": DEFAULT, "chips": index}, indent=1))
    print(f"{len(index)} chips on the Stage -> {out}")


if __name__ == "__main__":
    main()
