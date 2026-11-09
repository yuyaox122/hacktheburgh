"""Compile a gate-level netlist into scoreboard logic, plus a Python reference simulator with identical semantics.

Model (one Minecraft tick = one evaluation step):
  1. read inputs (levers)            2. evaluate combinational cells in topological order
  3. flip-flops: on a rising edge of their clock net Q <= D (all at once); async clear/preset win
  4. evaluate combinational cells again so outputs show the new state in the same tick
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..common import cells as lib
from ..common.netlist import Cell, Netlist

OBJ = "chip"


@dataclass
class Program:
    nets: dict[str, str]                       # net name -> score holder
    inputs: list[str]                          # input port bits that drive at least one cell
    outputs: list[str]                         # output port bits driven by logic
    order: list[Cell]                          # combinational cells, topological
    flops: list[Cell]
    loops_cut: list[str] = field(default_factory=list)
    driver: dict[str, tuple[Cell, str]] = field(default_factory=dict)


def _resolve(nl: Netlist, net: str) -> str:
    seen = set()
    while net in nl.aliases and net not in seen:
        seen.add(net)
        net = nl.aliases[net]
    return net


def build(nl: Netlist) -> Program:
    logic = nl.logic
    for c in logic:
        c.pins = {p: _resolve(nl, n) for p, n in c.pins.items()}
    driver = {}
    for c in logic:
        d = lib.cell(c.type)
        for p in d.outputs:
            if p in c.pins:
                driver[c.pins[p]] = (c, p)
    used = {n for c in logic for p, n in c.pins.items() if p in lib.cell(c.type).inputs}
    inputs = [b for b in nl.bits("in") if b in used]
    flops = [c for c in logic if lib.cell(c.type).ff]
    comb = [c for c in logic if not lib.cell(c.type).ff]
    const = {"0", "1"}
    changed = True
    while changed:                                            # nets fed only by tie cells (through buffers)
        changed = False
        for c in comb:
            d = lib.cell(c.type)
            if all(c.pins.get(p, "0") in const for p in d.inputs):
                for p in d.outputs:
                    if p in c.pins and c.pins[p] not in const:
                        const.add(c.pins[p]); changed = True
    outputs = [b for b in nl.bits("out") if _resolve(nl, b) in driver and _resolve(nl, b) not in const]
    known = set(inputs) | {"0", "1"} | {c.pins[p] for c in flops for p in lib.cell(c.type).outputs if p in c.pins}
    known |= {n for n in used if n not in driver}            # undriven nets read as 0
    order, pending, cut = [], list(comb), []
    while pending:
        nxt = []
        for c in pending:
            d = lib.cell(c.type)
            if all(c.pins.get(p, "0") in known for p in d.inputs):
                order.append(c)
                known |= {c.pins[p] for p in d.outputs if p in c.pins}
            else:
                nxt.append(c)
        if len(nxt) == len(pending):                          # combinational loop: cut one output
            c = nxt[0]
            o = [c.pins[p] for p in lib.cell(c.type).outputs if p in c.pins]
            known |= set(o)
            cut += o
        pending = nxt
    nets = {}
    for n in sorted({n for c in logic for n in c.pins.values()} | set(inputs) | {_resolve(nl, b) for b in outputs}):
        if n not in ("0", "1"):
            nets[n] = f"#n{len(nets)}"
    nets["0"], nets["1"] = "#k0", "#k1"
    return Program(nets, inputs, outputs, order, flops, cut, driver)


# ---------------------------------------------------------------- Minecraft emission

def _emit_expr(e, pins: dict, prog: Program, out: list[str], tmp: list[int]) -> str:
    k = e[0]
    if k == "var":
        return prog.nets[pins.get(e[1], "0")]
    if k == "const":
        return f"#k{e[1]}"
    t = f"#t{tmp[0]}"
    tmp[0] += 1
    if k == "not":
        x = _emit_expr(e[1], pins, prog, out, tmp)
        out.append(f"scoreboard players set {t} {OBJ} 1")
        out.append(f"scoreboard players operation {t} {OBJ} -= {x} {OBJ}")
        return t
    xs = [_emit_expr(x, pins, prog, out, tmp) for x in e[1]]
    out.append(f"scoreboard players operation {t} {OBJ} = {xs[0]} {OBJ}")
    op = {"and": "<", "or": ">", "xor": "+="}[k]
    for x in xs[1:]:
        out.append(f"scoreboard players operation {t} {OBJ} {op} {x} {OBJ}")
    if k == "xor":
        out.append(f"scoreboard players operation {t} {OBJ} %= #k2 {OBJ}")
    return t


def emit_comb(prog: Program) -> list[str]:
    out = []
    for c in prog.order:
        d = lib.cell(c.type)
        for p, e in d.outputs.items():
            if p not in c.pins:
                continue
            tmp = [0]
            r = _emit_expr(e, c.pins, prog, out, tmp)
            out.append(f"scoreboard players operation {prog.nets[c.pins[p]]} {OBJ} = {r} {OBJ}")
    return out


def emit_flops(prog: Program) -> list[str]:
    out = []
    for i, c in enumerate(prog.flops):
        d = lib.cell(c.type)
        ff = d.ff
        q_pins = [p for p in d.outputs if p in c.pins]
        state = f"#s{i}"
        clk = _emit_expr(ff["clock"], c.pins, prog, out, [100])
        nxt = _emit_expr(ff["next"], c.pins, prog, out, [200])
        # edge = clk & !prev ; next state held in #ns<i>
        out.append(f"scoreboard players operation #ns{i} {OBJ} = {state} {OBJ}")
        out.append(f"execute if score {clk} {OBJ} matches 1 if score #p{i} {OBJ} matches 0 run "
                   f"scoreboard players operation #ns{i} {OBJ} = {nxt} {OBJ}")
        out.append(f"scoreboard players operation #p{i} {OBJ} = {clk} {OBJ}")
    for i, c in enumerate(prog.flops):
        d = lib.cell(c.type)
        ff = d.ff
        out.append(f"scoreboard players operation #s{i} {OBJ} = #ns{i} {OBJ}")
        if "clear" in ff:
            r = _emit_expr(ff["clear"], c.pins, prog, out, [300])
            out.append(f"execute if score {r} {OBJ} matches 1 run scoreboard players set #s{i} {OBJ} 0")
        if "preset" in ff:
            r = _emit_expr(ff["preset"], c.pins, prog, out, [400])
            out.append(f"execute if score {r} {OBJ} matches 1 run scoreboard players set #s{i} {OBJ} 1")
        for p, e in d.outputs.items():
            if p in c.pins:
                # Q = IQ, Q_N = !IQ: evaluate the output function with the state variable bound to #s<i>
                out += _emit_state_fn(e, ff["state"], f"#s{i}", prog.nets[c.pins[p]])
    return out


def _emit_state_fn(e, state: str, holder: str, dst: str) -> list[str]:
    if e == ("var", state):
        return [f"scoreboard players operation {dst} {OBJ} = {holder} {OBJ}"]
    if e == ("not", ("var", state)):
        return [f"scoreboard players set {dst} {OBJ} 1", f"scoreboard players operation {dst} {OBJ} -= {holder} {OBJ}"]
    raise NotImplementedError(f"flop output function {e}")


# ---------------------------------------------------------------- Python reference

class RefSim:
    def __init__(self, prog: Program):
        self.p = prog
        self.v = {n: 0 for n in prog.nets}
        self.v["1"] = 1
        self.s = [0] * len(prog.flops)
        self.prev = [0] * len(prog.flops)

    def _comb(self):
        for c in self.p.order:
            d = lib.cell(c.type)
            env = {p: self.v.get(c.pins.get(p, "0"), 0) for p in d.inputs}
            for p, e in d.outputs.items():
                if p in c.pins:
                    self.v[c.pins[p]] = lib.ev(e, env)

    def step(self, inputs: dict[str, int]):
        for b, x in inputs.items():
            self.v[b] = x
        self._comb()
        ns = []
        for i, c in enumerate(self.p.flops):
            d = lib.cell(c.type)
            env = {p: self.v.get(c.pins.get(p, "0"), 0) for p in d.inputs}
            clk = lib.ev(d.ff["clock"], env)
            ns.append(lib.ev(d.ff["next"], env) if clk and not self.prev[i] else self.s[i])
            self.prev[i] = clk
        for i, c in enumerate(self.p.flops):
            d = lib.cell(c.type)
            env = {p: self.v.get(c.pins.get(p, "0"), 0) for p in d.inputs}
            self.s[i] = ns[i]
            if "clear" in d.ff and lib.ev(d.ff["clear"], env):
                self.s[i] = 0
            if "preset" in d.ff and lib.ev(d.ff["preset"], env):
                self.s[i] = 1
            for p, e in d.outputs.items():
                if p in c.pins:
                    self.v[c.pins[p]] = lib.ev(e, {d.ff["state"]: self.s[i]})
        self._comb()

    def out(self, bit: str) -> int:
        return self.v.get(bit, 0)
