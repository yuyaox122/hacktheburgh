"""Build web/public/data/design.json (the Stage view) from a placed-and-routed DEF plus its gate-level netlist.

Default input: the Jane Street asic-puzzle-2026 warm-up in data/warmup/ (no licence: the output goes to the
gitignored web/public/data/ only). Shape: CONTRACTS.md §3 / SPEC §6.3, all distances in microns:

  { name, pdk, die:{w,h}, core:{x0,y0,x1,y1}, rowHeight, site, rows:[{y,orient}],
    ports:[{name,dir,x,y}],
    cells:[{id,type,x,y,w,orient,pins:{pin:net},module,inst,src}],   # logic cells (pins from the netlist)
    phys:[{type,x,y,w,orient}],                                       # fill / tap / decap / diode
    routes:[{net,layer,pts}],                                         # wires: 2 points; vias: 1 point
    power:[{net,layer,width,pts}],                                    # met4/met5 straps
    source:{files:{name:text}, modules:{name:{file,line}}, instances:{inst:module}, mapping:"…"},
    credit, counts:{…} }

`src` is a line number in the module's source file. The warm-up netlist has no `src` attributes, so the
mapping is module-level: each cell points at its module's main statement (the `always` block for flip-flops,
the last `assign`/`<=` for logic). Clock-tree buffers belong to no source line (src null).

Usage: python -I ingest/ingest_def.py [--def F] [--netlist F] [--source F] [--pdk DIR] [--out F]
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WARM = os.path.join(ROOT, "data", "warmup")
SITE, ROW_H = 0.46, 2.72
PHYS_PREFIXES = ("fill_", "decap_", "tapvpwrvgnd_", "diode_")
PREFIX = "sky130_fd_sc_hd__"


def clean(name):
    return name.replace("\\", "")


def tokens(text):
    return re.findall(r"\(|\)|;|[^\s();]+", text)


def section(text, name):
    m = re.search(rf"^{name}\s+\d+\s*;(.*?)^END {name}", text, re.S | re.M)
    return m.group(1) if m else ""


def statements(body):
    """Split a DEF section into '- ...' statements (each ends with a ';' token)."""
    out, cur = [], []
    for t in tokens(body):
        if t == ";":
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(t)
    return out


def parse_vias(text):
    """Map custom via names (from the VIAS section) to a cut-layer label."""
    m = {}
    for st in statements(section(text, "VIAS")):
        if st and st[0] == "-" and "LAYERS" in st:
            i = st.index("LAYERS")
            m[st[1]] = st[i + 2]  # e.g. met1 via met2 -> "via"
    return m


def via_label(name, custom):
    if name in custom:
        return custom[name]
    for key, lab in (("L1M1", "mcon"), ("M1M2", "via"), ("M2M3", "via2"), ("M3M4", "via3"), ("M4M5", "via4")):
        if key in name:
            return lab
    return "via"


def parse_route(toks, net, dbu, custom, out):
    """Walk '+ ROUTED layer ( x y [e] ) ( x2 * ) VIA NEW layer …' token lists."""
    i, layer, prev = 0, None, None
    while i < len(toks):
        t = toks[i]
        if t in ("ROUTED", "NEW", "FIXED", "COVER"):
            layer, prev = toks[i + 1], None
            i += 2
            continue
        if t == "(":
            j = toks.index(")", i)
            vals = toks[i + 1 : j]
            x = prev[0] if vals[0] == "*" else int(vals[0]) / dbu
            y = prev[1] if vals[1] == "*" else int(vals[1]) / dbu
            if prev is not None and layer and (x, y) != prev:
                out.append({"net": net, "layer": layer, "pts": [[round(prev[0], 3), round(prev[1], 3)], [round(x, 3), round(y, 3)]]})
            prev = (x, y)
            i = j + 1
            continue
        if t == "RECT":  # small patch: '( dx1 dy1 dx2 dy2 )' around the last point; skip it
            j = toks.index(")", i)
            i = j + 1
            continue
        if t in ("+", "USE", "SIGNAL", "CLOCK", "SHAPE", "TAPER", "VIRTUAL", "MASK", "STYLE", "TAPERRULE", "NONDEFAULTRULE") or re.fullmatch(r"-?\d+", t):
            i += 1
            continue
        # anything else after a point is a via name
        if prev is not None:
            out.append({"net": net, "layer": via_label(t, custom), "pts": [[round(prev[0], 3), round(prev[1], 3)]]})
        i += 1


def parse_def(path):
    text = open(path).read()
    dbu = int(re.search(r"UNITS DISTANCE MICRONS (\d+)", text).group(1))
    da = re.search(r"DIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)", text)
    x0, y0, x1, y1 = (int(v) / dbu for v in da.groups())
    design = re.search(r"^DESIGN\s+(\S+)", text, re.M).group(1)
    rows = []
    for m in re.finditer(r"^ROW\s+\S+\s+\S+\s+(\d+)\s+(\d+)\s+(\S+)\s+DO\s+(\d+)\s+BY\s+\d+\s+STEP\s+(\d+)", text, re.M):
        rows.append({"x": int(m.group(1)) / dbu, "y": int(m.group(2)) / dbu, "orient": m.group(3), "sites": int(m.group(4)), "step": int(m.group(5)) / dbu})
    custom = parse_vias(text)

    comps = []
    for st in statements(section(text, "COMPONENTS")):
        if st[0] != "-":
            continue
        name, ctype = clean(st[1]), st[2]
        x = y = orient = None
        for k in ("PLACED", "FIXED"):
            if k in st:
                i = st.index(k)
                x, y, orient = int(st[i + 2]) / dbu, int(st[i + 3]) / dbu, st[i + 5]
        comps.append({"name": name, "type": ctype[len(PREFIX):] if ctype.startswith(PREFIX) else ctype, "x": x, "y": y, "orient": orient})

    pins = []
    for st in statements(section(text, "PINS")):
        if st[0] != "-":
            continue
        name = clean(st[1])
        d = st[st.index("DIRECTION") + 1] if "DIRECTION" in st else "INPUT"
        if "SPECIAL" in st or name in ("VPWR", "VGND"):
            continue
        for k in ("PLACED", "FIXED"):
            if k in st:
                i = st.index(k)
                pins.append({"name": name, "dir": {"INPUT": "in", "OUTPUT": "out"}.get(d, "inout"),
                             "x": int(st[i + 2]) / dbu, "y": int(st[i + 3]) / dbu})

    nets, routes = {}, []
    for st in statements(section(text, "NETS")):
        if st[0] != "-":
            continue
        net = clean(st[1])
        conns, i = [], 2
        while i < len(st) and st[i] == "(":
            j = st.index(")", i)
            conns.append((clean(st[i + 1]), st[i + 2]))
            i = j + 1
        nets[net] = conns
        if "ROUTED" in st:
            parse_route(st[st.index("ROUTED"):], net, dbu, custom, routes)

    power = []
    for st in statements(section(text, "SPECIALNETS")):
        if st[0] != "-":
            continue
        net = clean(st[1])
        toks = st
        for i, t in enumerate(toks):
            if t in ("ROUTED", "NEW") and i + 2 < len(toks):
                layer, width = toks[i + 1], toks[i + 2]
                if layer not in ("met4", "met5") or not width.isdigit() or int(width) == 0:
                    continue
                try:
                    k = toks.index("(", i)
                    a = toks[k + 1 : k + 3]
                    k2 = toks.index("(", k + 1)
                    b = toks[k2 + 1 : k2 + 3]
                except ValueError:
                    continue
                ax, ay = int(a[0]) / dbu, int(a[1]) / dbu
                bx = ax if b[0] == "*" else int(b[0]) / dbu
                by = ay if b[1] == "*" else int(b[1]) / dbu
                power.append({"net": net, "layer": layer, "width": int(width) / dbu, "pts": [[ax, ay], [bx, by]]})

    return {"design": design, "die": (x0, y0, x1, y1), "rows": rows, "comps": comps, "pins": pins, "nets": nets, "routes": routes, "power": power}


def parse_netlist(path):
    """Gate instances: {inst: (type, {pin: net})} from a flat structural Verilog netlist."""
    text = open(path).read()
    text = re.sub(r"//.*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    ident = r"(?:\\\S+\s|[A-Za-z_][A-Za-z0-9_$]*)"
    cells = {}
    for m in re.finditer(rf"\b{PREFIX}([a-z0-9_]+)\s+({ident})\s*\((.*?)\)\s*;", text, re.S):
        ctype, inst, body = m.group(1), clean(m.group(2).strip()), m.group(3)
        pins = {}
        for pm in re.finditer(r"\.([A-Za-z0-9_]+)\s*\(([^()]*)\)", body):
            net = clean(pm.group(2).strip())
            if net:
                pins[pm.group(1)] = net
        cells[inst] = (ctype, pins)
    return cells


def parse_source(path):
    """Modules, their line numbers, body ranges and the instances inside each one."""
    lines = open(path).read().split("\n")
    modules, cur = {}, None
    for n, line in enumerate(lines, 1):
        m = re.match(r"\s*module\s+([A-Za-z_][A-Za-z0-9_]*)", line)
        if m:
            cur = m.group(1)
            modules[cur] = {"line": n, "end": len(lines), "insts": {}}
            continue
        if cur and re.match(r"\s*endmodule", line):
            modules[cur]["end"] = n
            cur = None
            continue
        if cur:
            im = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", line)
            if im and im.group(1) in ("input", "output", "wire", "reg", "assign", "always", "if", "else", "module"):
                continue
            if im:
                modules[cur]["insts"][im.group(2)] = im.group(1)
    for name, mod in modules.items():
        body = list(range(mod["line"], mod["end"] + 1))
        mod["always"] = next((n for n in body if re.search(r"\balways\b", lines[n - 1])), None)
        stm = [n for n in body if re.search(r"\bassign\b|<=", lines[n - 1])]
        mod["stmt"] = stm[-1] if stm else None
    return lines, modules


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--def", dest="def_", default=os.path.join(WARM, "03_post_place_and_route.def"))
    ap.add_argument("--netlist", default=os.path.join(WARM, "01_netlist.v"))
    ap.add_argument("--source", default=os.path.join(WARM, "00_source.v"))
    ap.add_argument("--pdk", default=os.path.join(ROOT, "data", "pdk"))
    ap.add_argument("--out", default=os.path.join(ROOT, "web", "public", "data", "design.json"))
    ap.add_argument("--credit", default="Warm-up chip: Jane Street asic-puzzle-2026")
    a = ap.parse_args()
    for p in (a.def_, a.netlist):
        if not os.path.exists(p):
            sys.exit(f"missing {p} (run ingest/fetch.py first)")

    d = parse_def(a.def_)
    gates = parse_netlist(a.netlist)
    src_lines, modules = (parse_source(a.source) if os.path.exists(a.source) else ([], {}))
    src_name = os.path.basename(a.source)
    top = d["design"] if d["design"] in modules else None

    def resolve(inst_path):
        """'add0/_32_' -> ('adder8', 'add0'); top-level cells -> (top, '')."""
        parts = inst_path.split("/")[:-1]
        mod, ip = top, []
        for p in parts:
            nxt = modules.get(mod, {}).get("insts", {}).get(p) if mod else None
            ip.append(p)
            if nxt is None:
                return (mod, "/".join(ip))
            mod = nxt
        return (mod, "/".join(ip))

    def sites_of(t):
        if t.startswith(PHYS_PREFIXES):
            m = re.search(r"_(\d+)$", t)
            return 1 if t.startswith("tapvpwrvgnd") else int(m.group(1)) if m else 1
        f = os.path.join(a.pdk, t + ".json")
        if os.path.exists(f):
            area = json.load(open(f)).get("area") or 0
            return max(1, round(area / (SITE * ROW_H)))
        return 2

    cells, phys, warnings = [], [], []
    for c in d["comps"]:
        t = c["type"]
        w = round(sites_of(t) * SITE, 3)
        if t.startswith(PHYS_PREFIXES):
            phys.append({"type": t, "x": c["x"], "y": c["y"], "w": w, "orient": c["orient"]})
            continue
        g = gates.get(c["name"])
        if g is None:
            warnings.append(f"{c['name']}: not in netlist")
            pins = {}
        else:
            if g[0] != t:
                warnings.append(f"{c['name']}: DEF type {t} != netlist type {g[0]}")
            pins = g[1]
        mod, inst = resolve(c["name"])
        is_ff = t.startswith(("df", "sdf", "dl", "edf"))
        line = None
        if mod and mod != top:
            m = modules[mod]
            line = (m["always"] or m["stmt"]) if is_ff else (m["stmt"] or m["always"])
        cells.append({"id": c["name"], "type": t, "x": c["x"], "y": c["y"], "w": w, "orient": c["orient"],
                      "pins": pins, "module": mod, "inst": inst, "src": line})

    # Cross-check netlist pins against the DEF NETS connections.
    def_pins = {}
    for net, conns in d["nets"].items():
        for comp, pin in conns:
            if comp != "PIN":
                def_pins[(comp, pin)] = net
    mismatch = sum(1 for c in cells for p, n in c["pins"].items() if (c["id"], p) in def_pins and def_pins[(c["id"], p)] != n)
    if mismatch:
        warnings.append(f"{mismatch} pin->net assignments differ between the netlist and the DEF")

    x0, y0, x1, y1 = d["die"]
    rows = d["rows"]
    core = {"x0": min(r["x"] for r in rows), "y0": min(r["y"] for r in rows),
            "x1": max(r["x"] + r["sites"] * r["step"] for r in rows), "y1": max(r["y"] for r in rows) + ROW_H} if rows else None
    out = {
        "name": d["design"], "pdk": "sky130_fd_sc_hd", "die": {"w": x1 - x0, "h": y1 - y0}, "core": core,
        "rowHeight": ROW_H, "site": SITE, "rows": [{"y": r["y"], "orient": r["orient"]} for r in rows],
        "ports": d["pins"], "cells": cells, "phys": phys, "routes": d["routes"], "power": d["power"],
        "source": {
            "files": {src_name: "\n".join(src_lines)} if src_lines else {},
            "modules": {k: {"file": src_name, "line": v["line"]} for k, v in modules.items()},
            "instances": {inst: mod for v in modules.values() for inst, mod in v["insts"].items()},
            "top": top,
            "mapping": "module-level: the netlist has no src attributes, so each cell points at its module's main statement",
        },
        "credit": a.credit,
        "counts": {"components": len(d["comps"]), "logic_cells": len(cells), "phys_cells": len(phys),
                   "cell_types": len({c["type"] for c in cells}), "pins": len(d["pins"]) + 2, "nets": len(d["nets"]),
                   "route_segments": sum(1 for r in d["routes"] if len(r["pts"]) == 2),
                   "vias": sum(1 for r in d["routes"] if len(r["pts"]) == 1)},
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    c = out["counts"]
    print(f"design.json: {d['design']}, die {out['die']['w']}x{out['die']['h']} um, {c['components']} components "
          f"({c['logic_cells']} logic, {c['cell_types']} types, {c['phys_cells']} physical), {len(d['pins'])} signal pins, "
          f"{c['nets']} nets, {c['route_segments']} wire segments, {c['vias']} vias -> {a.out}")
    for w in warnings:
        print("WARNING:", w)


if __name__ == "__main__":
    main()
