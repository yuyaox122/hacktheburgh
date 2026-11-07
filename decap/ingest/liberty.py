"""Build web/public/data/lib.json from the per-cell SKY130 HD Liberty JSON in data/pdk/.

Cell types written: every type some netlist uses (by default the warm-up netlist and every TT08 netlist under
data/tt08/), every type in the backend's chip results (out/chips/*.json, which include planted triggers), and
every cell the team has fetched into data/pdk/ (so a live /api/plant with new cell types still resolves).
Physical-only cells (fill, tap, decap, diode) have no Liberty JSON and are written with "phys": true.

Output shape (CONTRACTS.md §3):
  { "<type>": { "pins": {"A": "in", "Y": "out"}, "function": {"Y": "!(A&B)"}, "area": 6.256,
                "ff": null | {"clocked_on", "next_state", "clear"?, "preset"?, "state": ["IQ", "IQ_N"]} } }

Usage: python -I ingest/liberty.py [--pdk DIR] [--out FILE] [netlist.v ...]
"""
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # decap/
PHYS_PREFIXES = ("fill_", "decap_", "tapvpwrvgnd_", "diode_")
PG_PINS = {"VGND", "VNB", "VPB", "VPWR"}


def used_types(paths):
    types = set()
    for p in paths:
        with open(p) as f:
            types.update(re.findall(r"sky130_fd_sc_hd__([a-z0-9_]+)\s", f.read()))
    return types


def phys_width_sites(t):
    m = re.search(r"_(\d+)$", t)
    n = int(m.group(1)) if m else 1
    return 1 if t.startswith("tapvpwrvgnd") else n


def cell_entry(pdk, t):
    path = os.path.join(pdk, t + ".json")
    if not os.path.exists(path):
        if t.startswith(PHYS_PREFIXES):
            sites = phys_width_sites(t)
            return {"pins": {}, "function": {}, "area": round(sites * 0.46 * 2.72, 4), "ff": None, "phys": True}
        return None
    with open(path) as f:
        d = json.load(f)
    pins, func = {}, {}
    for k, v in d.items():
        if not k.startswith("pin,"):
            continue
        name = k[4:]
        if name in PG_PINS:
            continue
        direction = v.get("direction")
        pins[name] = "out" if direction == "output" else "in" if direction == "input" else "inout"
        if direction == "output" and v.get("function") is not None:
            func[name] = v["function"]
    ff = None
    for k, v in d.items():
        if k.startswith("ff,"):
            ff = {"clocked_on": v["clocked_on"], "next_state": v["next_state"], "state": k.split(",")[1:]}
            for opt in ("clear", "preset", "clear_preset_var1", "clear_preset_var2"):
                if opt in v:
                    ff[opt] = v[opt]
            break
    return {"pins": pins, "function": func, "area": d.get("area"), "ff": ff}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pdk", default=os.path.join(ROOT, "data", "pdk"))
    ap.add_argument("--out", default=os.path.join(ROOT, "web", "public", "data", "lib.json"))
    ap.add_argument("netlists", nargs="*")
    a = ap.parse_args()
    netlists = a.netlists or (
        [os.path.join(ROOT, "data", "warmup", "01_netlist.v")]
        + sorted(glob.glob(os.path.join(ROOT, "data", "tt08", "*", "netlist.v")))
    )
    netlists = [n for n in netlists if os.path.exists(n)]
    if not netlists:
        sys.exit("no netlists found (fetch data first)")
    types = used_types(netlists)
    for f in glob.glob(os.path.join(ROOT, "out", "chips", "*.json")):
        try:
            with open(f) as fh:
                types.update(c["type"] for c in json.load(fh).get("netlist", {}).get("cells", []))
        except (OSError, ValueError, KeyError, TypeError):
            pass
    types.update(os.path.basename(f)[:-5] for f in glob.glob(os.path.join(a.pdk, "*.json")))
    lib, missing = {}, []
    for t in sorted(types):
        e = cell_entry(a.pdk, t)
        if e is None:
            missing.append(t)
        else:
            lib[t] = e
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(lib, f, indent=1, sort_keys=True)
    logic = sum(1 for e in lib.values() if not e.get("phys"))
    print(f"lib.json: {len(lib)} cell types ({logic} logic, {len(lib) - logic} physical) from {len(netlists)} netlists -> {a.out}")
    if missing:
        print("WARNING: no Liberty JSON for:", ", ".join(missing))


if __name__ == "__main__":
    main()
