"""Plant a hardware trojan in a netlist: `if (ui_in == N) <target> |= 1`, built from SKY130 cells (like Decap's /plant)."""
from __future__ import annotations

from . import logic
from ..common import cells as lib
from ..common.netlist import Cell, Netlist

PREFIX = "trojan_"


def pick_target(nl: Netlist, n: int) -> str:
    """An output bit that is normally 0 when ui_in == n, so the trojan visibly changes the answer."""
    prog = logic.build(nl)
    sim = logic.RefSim(prog)
    v = {b: 0 for b in prog.inputs} | {f"ui_in[{k}]": (n >> k) & 1 for k in range(8)} | {"rst_n": 1, "ena": 1}
    sim.step(v | {"clk": 0})
    sim.step(v | {"clk": 1})
    for b in prog.outputs:
        if sim.out(b) == 0:
            return b
    return prog.outputs[0]


def plant(nl: Netlist, n: int, target: str | None = None) -> tuple[str, list[str]]:
    target = target or pick_target(nl, n)
    lib.fetch_types({"inv_1", "and4_1", "and2_1", "or2_1"})
    pre = f"{target}__pre"
    for c in nl.logic:                                 # the original driver now drives an internal net
        for p in lib.cell(c.type).outputs:
            if c.pins.get(p) == target:
                c.pins[p] = pre
    lits, new = [], []
    for k in range(8):
        bit = f"ui_in[{k}]"
        if (n >> k) & 1:
            lits.append(bit)
        else:
            new.append(Cell(f"{PREFIX}inv{k}", "inv_1", {"A": bit, "Y": f"{PREFIX}nb{k}"}))
            lits.append(f"{PREFIX}nb{k}")
    new.append(Cell(f"{PREFIX}and_lo", "and4_1", {"A": lits[0], "B": lits[1], "C": lits[2], "D": lits[3], "X": f"{PREFIX}lo"}))
    new.append(Cell(f"{PREFIX}and_hi", "and4_1", {"A": lits[4], "B": lits[5], "C": lits[6], "D": lits[7], "X": f"{PREFIX}hi"}))
    new.append(Cell(f"{PREFIX}trigger", "and2_1", {"A": f"{PREFIX}lo", "B": f"{PREFIX}hi", "X": f"{PREFIX}fire"}))
    new.append(Cell(f"{PREFIX}payload", "or2_1", {"A": pre, "B": f"{PREFIX}fire", "X": target}))
    nl.cells += new
    return target, [c.name for c in new]
