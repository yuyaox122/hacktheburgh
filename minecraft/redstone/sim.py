"""Two reference simulators with one protocol, so they can be compared with each other and with the redstone:

  CellSim  the chip's own netlist, cell by cell, from the SKY130 Liberty functions (independent of techmap)
  PrimSim  the primitive network techmap produced (NOT / INH / OR / two-phase LATCH pairs)

Protocol: sim.set(inputs: {bit: 0/1}) settles combinationally; sim.clock() applies one rising clk edge (and for
PrimSim the HOLD_M/HOLD_S sequence the redstone clock driver uses); sim.read(bits) -> {bit: 0/1}.
All flip-flops / latches power on at 0, like redstone.

    python sim.py tt_um_sign_addsub [-n 2000]      # CellSim vs PrimSim on random vectors
"""
from __future__ import annotations

import random
import sys

import chipdata as cd
import techmap as tm


class CellSim:
    def __init__(self, chip: str):
        self.nl = cd.read_netlist(chip)
        self.alias = dict(self.nl.assigns)
        self.comb, self.flops = [], []
        for c in self.nl.cells:
            if c.physical:
                continue
            d = cd.celldef(c.type)
            fns = {o: cd.parse_fn(f) for o, f in d.outputs.items() if o in c.pins and d.ff is None}
            if d.ff:
                ff = {k: cd.parse_fn(v) for k, v in d.ff.items() if k in ("clear", "preset", "next_state")}
                self.flops.append((c, d, ff))
            else:
                self.comb.append((c, d, fns))
        self.v = {tm.CONST0: 0, tm.CONST1: 1}
        self.q = {c.name: 0 for c, _, _ in self.flops}
        self.ins = {}

    def _get(self, n):
        seen = 0
        while n in self.alias and n not in self.v:
            n = self.alias[n]; seen += 1
        return self.v.get(n, 0)

    def _settle(self):
        for _ in range(len(self.comb) + len(self.flops) + 5):
            before = dict(self.v)
            self.v.update(self.ins)
            for c, d, ff in self.flops:
                env = {p: self._get(n) for p, n in c.pins.items() if p in d.inputs}
                if "clear" in ff and cd.ev(ff["clear"], env):
                    self.q[c.name] = 0
                elif "preset" in ff and cd.ev(ff["preset"], env):
                    self.q[c.name] = 1
                self.v[c.pins["Q"]] = self.q[c.name]
            for c, d, fns in self.comb:
                env = {p: self._get(c.pins[p]) if p in c.pins else 0 for p in d.inputs}
                for o, f in fns.items():
                    self.v[c.pins[o]] = cd.ev(f, env)
            if self.v == before:
                return
        raise RuntimeError("CellSim did not settle (combinational loop?)")

    def set(self, inputs: dict):
        self.ins = dict(inputs)
        self.ins.setdefault("clk", 0)
        self._settle()

    def clock(self):
        d_now = {}
        for c, d, ff in self.flops:
            env = {p: self._get(n) for p, n in c.pins.items() if p in d.inputs}
            d_now[c.name] = cd.ev(ff["next_state"], env)
        for c, d, ff in self.flops:
            env = {p: self._get(n) for p, n in c.pins.items() if p in d.inputs}
            if not (("clear" in ff and cd.ev(ff["clear"], env)) or ("preset" in ff and cd.ev(ff["preset"], env))):
                self.q[c.name] = d_now[c.name]
        self._settle()

    def read(self, bits):
        return {b: self._get(b) for b in bits}


class PrimSim:
    def __init__(self, pn: tm.PrimNetlist):
        self.pn = pn
        self.v = {tm.CONST0: 0, tm.CONST1: 1}
        self.hold = {tm.HOLD_M: 0, tm.HOLD_S: 1}          # idle: clk low -> master open, slave holding
        self.ins = {}

    def _settle(self):
        for _ in range(4 * len(self.pn.prims) + 10):
            before = dict(self.v)
            self.v.update(self.ins)
            self.v.update(self.hold)
            for p in self.pn.prims:
                g = lambda q: self.v.get(p.ins[q], 0)
                if p.kind == "NOT":
                    self.v[p.out] = 1 - g("B")
                elif p.kind == "INH":
                    self.v[p.out] = int(g("A") and not g("B"))
                elif p.kind == "OR":
                    self.v[p.out] = int(g("A") or g("B"))
                elif p.kind == "LATCH":
                    if not g("H"):
                        self.v[p.out] = g("D")
                    else:
                        self.v.setdefault(p.out, 0)
            if self.v == before:
                return
        raise RuntimeError("PrimSim did not settle")

    def set(self, inputs: dict):
        self.ins = {k: v for k, v in inputs.items() if k != "clk"}
        self._settle()

    def clock(self):
        for hm, hs in ((1, 1), (1, 0), (1, 1), (0, 1)):     # close master, open slave, close slave, open master
            self.hold = {tm.HOLD_M: hm, tm.HOLD_S: hs}
            self._settle()

    def read(self, bits):
        out = {}
        for b in bits:
            n = self.pn.outputs.get(b, b)
            out[b] = self.v.get(n, 0) if n not in (tm.CONST0, tm.CONST1) else int(n == tm.CONST1)
        return out


def random_vectors(in_bits, n, seed=0, reset_bit="rst_n"):
    rng = random.Random(seed)
    vs = []
    for i in range(n):
        v = {b: rng.randint(0, 1) for b in in_bits}
        if reset_bit in v:
            v[reset_bit] = 0 if i < 2 else int(rng.random() > 0.05)     # reset for 2 cycles, rarely later
        if "ena" in v:
            v["ena"] = 1
        vs.append(v)
    return vs


def compare(chip: str, n: int = 2000) -> int:
    pn = tm.build(chip)
    tm.prune(pn)
    a, b = CellSim(chip), PrimSim(pn)
    real_in = [x for x in a.nl.bits("in") if x not in ("clk",)]
    outs = a.nl.bits("out")
    bad = 0
    for i, v in enumerate(random_vectors(real_in, n, seed=1)):
        a.set(v); b.set(v)
        if pn.sequential:
            a.clock(); b.clock()
        ra, rb = a.read(outs), b.read(outs)
        if ra != rb:
            bad += 1
            if bad <= 5:
                diff = {k: (ra[k], rb[k]) for k in outs if ra[k] != rb[k]}
                print(f"  vector {i}: cell vs prim differ {diff}")
    print(f"{chip}: {n} vectors, sequential={pn.sequential}, mismatches={bad}, prims={pn.stats['prims']}")
    return bad


if __name__ == "__main__":
    n = int(sys.argv[sys.argv.index("-n") + 1]) if "-n" in sys.argv else 2000
    sys.exit(1 if compare(sys.argv[1], n) else 0)
