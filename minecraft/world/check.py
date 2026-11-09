"""Drive the in-game chip over RCON and compare it with the Python reference simulator (and an optional intent).

    python -m minecraft.world.check tt_um_Electom_cla_4bits -n 40    # server running, basic:build done
"""
from __future__ import annotations

import argparse
import json
import random
import re
import time

from . import OUT, logic, plant
from ..common.fetch import netlist_path
from ..common.netlist import parse
from .server import rcon

INTENT = {   # optional end-to-end oracle per chip: inputs dict -> expected outputs dict
    "tt_um_Electom_cla_4bits": lambda v: (lambda s: {f"uo_out[{k}]": (s >> k) & 1 for k in range(5)})(
        sum(v[f"ui_in[{k}]"] << k for k in range(4)) + sum(v[f"ui_in[{k + 4}]"] << k for k in range(4)) + v["uio_in[0]"]),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("chip")
    ap.add_argument("-n", type=int, default=30)
    a = ap.parse_args()
    meta = json.loads((OUT / a.chip / "meta.json").read_text())
    nl = parse(netlist_path(a.chip))
    if meta.get("plant"):
        plant.plant(nl, meta["plant"]["n"], meta["plant"]["target"])
    prog = logic.build(nl)
    y, pz = meta["y0"], meta["panel_z"]
    lever = {p["bit"]: p["x"] for p in meta["panel"] if p["kind"] == "in"}
    lamp = {p["bit"]: p["x"] for p in meta["panel"] if p["kind"] == "out"}
    nets = meta["nets"]

    def set_lever(x, on):
        return f"setblock {x} {y + 1} {pz} minecraft:lever[face=floor,facing=south,powered={'true' if on else 'false'}]"

    rcon(set_lever(meta["auto_x"], False))           # manual clock
    ref = logic.RefSim(prog)
    rng = random.Random(1)
    bad = intent_bad = 0
    data_bits = [b for b in prog.inputs if b not in ("clk", "rst_n", "ena")]
    forced = [meta["plant"]["n"]] if meta.get("plant") else []
    for t in range(a.n + len(forced)):
        v = {b: rng.randint(0, 1) for b in data_bits} | {"rst_n": 1, "ena": 1}
        if t < len(forced):
            v |= {f"ui_in[{k}]": (forced[t] >> k) & 1 for k in range(8)} | {"uio_in[0]": 0}
        for clk in (0, 1):
            vv = v | {"clk": clk}
            rcon(*[set_lever(lever[b], vv[b]) for b in lever if b in vv])
            ref.step(vv)
            time.sleep(0.25)
        got = {}
        res = rcon(*[f"scoreboard players get {nets[b]} chip" for b in prog.outputs],
                   *[f"execute if block {lamp[b]} {y + 1} {pz} minecraft:redstone_lamp[lit=true]" for b in prog.outputs])
        k = len(prog.outputs)
        for b, r, l in zip(prog.outputs, res[:k], res[k:]):
            m = re.search(r"has (\d+)", r)
            got[b] = int(m.group(1)) if m else -1
            lit = int("passed" in l)
            if lit != got[b]:
                print("lamp mismatch", b, got[b], lit)
                bad += 1
        exp = {b: ref.out(b) for b in prog.outputs}
        if got != exp:
            bad += 1
            print("MISMATCH vs reference", v, got, exp)
        if a.chip in INTENT:
            want = INTENT[a.chip](v)
            if any(got.get(b) != x for b, x in want.items()):
                intent_bad += 1
                print("MISMATCH vs intent", v, got, want)
    rcon(set_lever(meta["auto_x"], True))
    print(f"{a.n} vectors: {bad} mismatches vs reference sim, {intent_bad} vs intent" +
          ("" if a.chip in INTENT else " (no intent oracle)"))


if __name__ == "__main__":
    main()
