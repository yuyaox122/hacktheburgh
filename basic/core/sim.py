"""Bit-parallel gate-level evaluation of a stripped netlist.

A value is a Python int whose bit j is the wire's value in test pattern j, so one pass evaluates
thousands of patterns. Sources are the input-port bits and the flip-flop outputs (Q); everything
else is computed from them through the combinational cells.
"""
from __future__ import annotations

import random
from functools import cached_property

from . import lib
from .netlist import Inst, Netlist


class CombLoop(Exception):
    pass


class Sim:
    def __init__(self, nl: Netlist):
        self.nl = nl
        self.drv = nl.driver()
        self.comb = [i for i in nl.insts if not i.cell.seq]
        self.flops = nl.flops
        self.q_bits = [f.conns["Q"] for f in self.flops if isinstance(f.conns.get("Q"), int)]
        self.in_bits = [b for _, _, b in nl.in_bits()]
        self.cuts: list[int] = []          # nets that break combinational loops (ring oscillators, latches)
        self.sources = list(dict.fromkeys(self.in_bits + self.q_bits))
        self.order = self._levelise()
        self.sources += self.cuts

    def _levelise(self) -> list[Inst]:
        known = set(self.sources)
        pending = list(self.comb)
        order = []
        while pending:
            nxt = []
            progressed = False
            for i in pending:
                if all(not isinstance(b, int) or b in known for p, b in i.conns.items() if p in i.cell.inputs):
                    order.append(i)
                    for p in i.cell.outputs:
                        if isinstance(i.conns.get(p), int):
                            known.add(i.conns[p])
                    progressed = True
                else:
                    nxt.append(i)
            if not progressed:
                # undriven inputs count as 0; anything else is a real loop
                undriven = {b for i in nxt for p, b in i.conns.items() if p in i.cell.inputs and isinstance(b, int)
                            and b not in known and b not in self.drv}
                if undriven:
                    known |= undriven
                    continue
                # cut the loop at the pending net most used by other pending cells
                outs = {i.conns[p] for i in nxt for p in i.cell.outputs if isinstance(i.conns.get(p), int)}
                use: dict[int, int] = {}
                for i in nxt:
                    for p, b in i.conns.items():
                        if p in i.cell.inputs and b in outs:
                            use[b] = use.get(b, 0) + 1
                if not use:
                    raise CombLoop(f"cannot order {len(nxt)} cells")
                cut = max(use, key=lambda b: (use[b], -b))
                self.cuts.append(cut)
                known.add(cut)
                continue
            pending = nxt
        return order

    def run(self, env: dict[int, int], mask: int, force: dict[int, int] | None = None) -> dict[int, int]:
        v = dict(env)
        if force:
            v.update(force)
        for i in self.order:
            c = i.cell
            pins = {}
            for p in c.inputs:
                b = i.conns.get(p, "0")
                pins[p] = (mask if b == "1" else 0) if isinstance(b, str) else v.get(b, 0)
            for p, fn in c.outputs.items():
                b = i.conns.get(p)
                if isinstance(b, int):
                    v[b] = force[b] if force and b in force else lib.ev(fn, pins, mask)
        return v

    @staticmethod
    def val(v: dict[int, int], b, mask: int) -> int:
        if isinstance(b, str):
            return mask if b == "1" else 0
        return v.get(b, 0)

    def random_env(self, n: int, seed: int = 1, bits: list[int] | None = None, biased: bool = False) -> tuple[dict[int, int], int]:
        """n random patterns. biased=True: a quarter each uniform, mostly-1 (p=7/8), mostly-0 (p=1/8),
        and per-pattern 'all ones but a few' / 'all zeros but a few', so rare AND/OR conditions get hit."""
        rng = random.Random(seed)
        mask = (1 << n) - 1
        bits = bits or self.sources
        if not biased:
            return {b: rng.getrandbits(n) for b in bits}, mask
        q = n // 4
        env = {}
        for b in bits:
            u = rng.getrandbits(q)
            hi = rng.getrandbits(q) | rng.getrandbits(q) | rng.getrandbits(q)
            lo = rng.getrandbits(q) & rng.getrandbits(q) & rng.getrandbits(q)
            mix = rng.getrandbits(n - 3 * q)
            env[b] = u | (hi << q) | (lo << 2 * q) | (mix << 3 * q)
        # last quarter: alternate dense / sparse patterns per position
        r = n - 3 * q
        dense = 0
        for j in range(r):
            if j % 2:
                dense |= 1 << j
        for b in bits:
            sp = rng.getrandbits(r) & rng.getrandbits(r) & rng.getrandbits(r) & rng.getrandbits(r)
            dn = ~(rng.getrandbits(r) & rng.getrandbits(r) & rng.getrandbits(r) & rng.getrandbits(r)) & ((1 << r) - 1)
            env[b] = (env[b] & ((1 << 3 * q) - 1)) | (((dn & dense) | (sp & ~dense & ((1 << r) - 1))) << 3 * q)
        return env, mask

    @staticmethod
    def exhaustive_env(bits: list[int]) -> tuple[dict[int, int], int]:
        n = 1 << len(bits)
        mask = (1 << n) - 1
        env = {}
        for k, b in enumerate(bits):
            # bit k of the pattern index
            block = (1 << (1 << k)) - 1
            pat = 0
            period = 1 << (k + 1)
            for start in range(1 << k, n, period):
                pat |= block << start
            env[b] = pat & mask
        return env, mask

    # ----- structure
    @cached_property
    def fanin(self) -> dict[int, list[int]]:
        f = {}
        for i in self.comb:
            ins = [b for p, b in i.conns.items() if p in i.cell.inputs and isinstance(b, int)]
            for p in i.cell.outputs:
                if isinstance(i.conns.get(p), int):
                    f[i.conns[p]] = ins
        return f

    def cone(self, bits) -> tuple[set[str], set[int]]:
        """Cells in the combinational cone of `bits`, and the source bits they depend on."""
        cells, sup, seen = set(), set(), set()
        src = set(self.sources)
        stack = [b for b in bits if isinstance(b, int)]
        while stack:
            b = stack.pop()
            if b in seen:
                continue
            seen.add(b)
            if b in src:
                sup.add(b)
                continue
            d = self.drv.get(b)
            if d is None or d[0].cell.seq:
                continue
            cells.add(d[0].id)
            stack.extend(self.fanin.get(b, []))
        return cells, sup
