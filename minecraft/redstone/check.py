"""Check redstone builds in a real vanilla 1.21.11 server (start it with `python -m minecraft.redstone.mc start`).

    python -m minecraft.redstone.check prims                    # every primitive, every rotation, every input sequence
    python -m minecraft.redstone.check chip <chip> [n]          # out/<chip>/layout.json vs the netlist (sim.CellSim)
    python -m minecraft.redstone.check vchip <file.v> [n]       # a Verilog build vs iverilog on the source
"""
from __future__ import annotations

import itertools
import json
import random
import sys
import time
from pathlib import Path

from . import OUT, place, prims, sim, verilog
from .mc import FLOOR_Y, MC

Y = FLOOR_Y + 1
ORIGIN = (8, 208)          # test area x 0..127, z 192..319 (force-loaded by `setup_area`)


def setup_area(mc: MC) -> None:
    mc.prepare()
    mc.forceload(0, 192, 127, 319)


def drive(mc: MC, pos, on: bool) -> None:
    x, y, z = pos
    mc.place([(x, y, z, "redstone_block" if on else "air")])


def place_tile(mc: MC, t: prims.Tile, ox: int, oz: int) -> None:
    mc.clear(ox - 6, Y, oz - 6, ox + 6, Y + 3, oz + 6)
    mc.place([(ox + x, Y + y, oz + z, s) for x, y, z, s in t.blocks])


def check_comb(mc: MC, base: prims.Tile, flows=("east", "south", "west", "north"), rounds: int = 3) -> list[str]:
    fails = []
    for flow in flows:
        t = prims.rotate(base, flow)
        ox, oz = ORIGIN
        place_tile(mc, t, ox, oz)
        names = sorted(t.inputs)
        seq = [dict(zip(names, bits)) for bits in itertools.product([0, 1], repeat=len(names))]
        rng = random.Random(1)
        order = seq + [rng.choice(seq) for _ in range(rounds * len(seq))]   # every value, then random transitions
        settle = 2 * base.delay_rt + 4
        for v in order:
            for n in names:
                x, y, z = t.inputs[n]
                drive(mc, (ox + x, Y + y, oz + z), bool(v[n]))
            mc.step(settle)
            px, py, pz, st = t.out_probe
            got = mc.test(ox + px, Y + py, oz + pz, st)
            want = bool(base.fn(v))
            if got != want:
                fails.append(f"{base.name} flow={flow} in={v} got={int(got)} want={int(want)}")
    return fails


def check_latch(mc: MC, flows=("east", "south", "west", "north"), base=prims.LATCH, dpin="D", hpin="H") -> list[str]:
    fails = []
    for flow in flows:
        t = prims.rotate(base, flow)
        ox, oz = ORIGIN
        place_tile(mc, t, ox, oz)
        D = tuple(a + b for a, b in zip((ox, Y, oz), t.inputs[dpin]))
        H = tuple(a + b for a, b in zip((ox, Y, oz), t.inputs[hpin]))
        px, py, pz, st = t.out_probe
        q = (ox + px, Y + py, oz + pz)
        # (d, h, expected q): transparent while h=0, holds while h=1
        script = [(1, 0, 1), (0, 0, 0), (1, 0, 1), (1, 1, 1), (0, 1, 1), (0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 1)]
        rng = random.Random(2)
        state = 1
        for _ in range(20):                 # random extra steps, model-checked
            d, h = rng.randint(0, 1), rng.randint(0, 1)
            if h == 0:
                state = d
            script.append((d, h, state))
        for d, h, want in script:
            # change H first when closing, D first when opening (the harness keeps D stable around the H edge)
            if h:
                drive(mc, H, True); mc.step(4); drive(mc, D, bool(d))
            else:
                drive(mc, D, bool(d)); mc.step(4); drive(mc, H, False)
            mc.step(10)
            got = mc.test(*q, st)
            if got != bool(want):
                fails.append(f"LATCH flow={flow} d={d} h={h} got={int(got)} want={want}")
    return fails


def check_chip(mc: MC, chip: str, n: int) -> int:
    lay = place.load(chip)
    print(f"{chip}: {lay['stats']}")
    place.build(mc, lay)
    mc.cmd("tick rate 1000")                      # steps run as fast as the server can
    drv = place.Driver(mc, lay)
    drv.set({b: 0 for b in lay["inputs"]})
    drv.wait(2 * drv.settle)
    ref = sim.CellSim(chip)
    real_in = [b for b in ref.nl.bits("in") if b != "clk"]
    outs = [b for b in ref.nl.bits("out")]
    seq = "HOLD_M" in lay["inputs"]
    if seq:
        drv.set({"HOLD_M": 0, "HOLD_S": 1})
    bad = 0
    t0 = time.time()
    for i, v in enumerate(sim.random_vectors(real_in, n, seed=7)):
        ref.set(v)
        drv.set(v)
        if seq:
            ref.clock()
            drv.clock()
        want, got = ref.read(outs), drv.read(outs)
        ok = want == got
        bad += not ok
        word = lambda r: "".join(str(r[f"uo_out[{k}]"]) for k in range(7, -1, -1))
        print(f"  {i:3d} in={''.join(str(v[b]) for b in real_in if b in v)} uo_out want={word(want)} got={word(got)}"
              f" {'ok' if ok else 'MISMATCH ' + str({k: (want[k], got[k]) for k in outs if want[k] != got[k]})}",
              flush=True)
    print(f"{chip}: {n} vectors in vanilla 1.21.11, {bad} mismatches ({time.time() - t0:.0f}s)")
    return bad


def check_verilog(mc: MC, src: str, n: int) -> int:
    m = verilog.synth(Path(src))
    meta = json.loads((OUT / m["name"] / "verilog.json").read_text())
    lay = place.load(meta["top"])
    print(f"{meta['top']}: {lay['stats']}")
    place.build(mc, lay)
    mc.cmd("tick rate 1000")
    drv = place.Driver(mc, lay)
    drv.set({b: 0 for b in lay["inputs"]})
    seq = meta["clock"] is not None
    if seq:
        drv.set({"HOLD_M": 0, "HOLD_S": 1})
    drv.wait(2 * drv.settle)
    vecs = verilog.vectors(meta["inputs"], n)
    ref = verilog.IverilogRef(meta["source"], meta["top"], meta["inputs"], meta["outputs"], meta["clock"]).run(vecs)
    bad = 0
    t0 = time.time()
    for i, (v, want) in enumerate(zip(vecs, ref)):
        drv.set(v)
        if seq:
            drv.clock()
        got = drv.read(meta["outputs"])
        ok = got == want
        bad += not ok
        fmt = lambda r: "".join(str(r[b]) for b in meta["outputs"])
        print(f"  {i:3d} in={''.join(str(v[b]) for b in meta['inputs'])} want={fmt(want)} got={fmt(got)} "
              f"{'ok' if ok else 'MISMATCH'}", flush=True)
    print(f"{meta['top']}: {n} vectors in vanilla 1.21.11 vs iverilog on the source, {bad} mismatches "
          f"({time.time() - t0:.0f}s)")
    return bad


def main() -> None:
    what = sys.argv[1] if len(sys.argv) > 1 else "prims"
    with MC() as mc:
        setup_area(mc)
        if what in ("prims", "bus"):
            fails = []
            if what == "prims":
                for name in ("BUF", "NOT", "INH", "OR"):
                    f = check_comb(mc, prims.TILES[name])
                    print(f"{name}: {'ok' if not f else f'{len(f)} FAIL'}"); fails += f
                f = check_latch(mc)
                print(f"LATCH: {'ok' if not f else f'{len(f)} FAIL'}"); fails += f
            for name in ("NOT", "INH", "OR"):
                f = check_comb(mc, prims.BUS[name], flows=("east",))
                print(f"bus {name}: {'ok' if not f else f'{len(f)} FAIL'}"); fails += f
            f = check_latch(mc, flows=("east",), base=prims.B_LATCH, dpin="A", hpin="B")
            print(f"bus LATCH: {'ok' if not f else f'{len(f)} FAIL'}"); fails += f
            for x in fails[:20]:
                print("  ", x)
            sys.exit(1 if fails else 0)
        elif what == "vchip":
            n = int(sys.argv[3]) if len(sys.argv) > 3 else 20
            sys.exit(1 if check_verilog(mc, sys.argv[2], n) else 0)
        elif what == "chip":
            n = int(sys.argv[3]) if len(sys.argv) > 3 else 20
            sys.exit(1 if check_chip(mc, sys.argv[2], n) else 0)


if __name__ == "__main__":
    main()
