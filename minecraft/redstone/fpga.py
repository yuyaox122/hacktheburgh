"""Bitstreams for examples/fpga.v (tiny_fpga, N=2) and checks of programmed behaviour in vanilla.

    python -m minecraft.redstone.verilog minecraft/redstone/examples/fpga.v --z 1200   # the FPGA itself (once)
    python -m minecraft.redstone.fpga bits <program>                   # print the 30-bit bitstream
    python -m minecraft.redstone.fpga check <program> [n] [--nobuild]  # check server: load the bitstream into the
                                                                       # levers, compare with a Python model
    python -m minecraft.redstone.fpga demo                             # demo server: one function per program

A program is two CLBs. Each CLB: three input selections (0 = pin[i], 1 = own flop, 2 = neighbour's flop,
3 = pin[i+1 mod 3]), a LUT3 given as a Python function of (x0, x1, x2), and whether out[j] is the flop.
"""
from __future__ import annotations

import random
import sys

from . import place
from .mc import DEMO, MC

N = 2
CHIP = "tiny_fpga"
PIN, OWN_Q, NEXT_Q, PIN_NEXT = 0, 1, 2, 3


def clb(sel, fn, reg):
    return {"sel": sel, "lut": [int(bool(fn(x & 1, x >> 1 & 1, x >> 2 & 1))) for x in range(8)], "reg": reg}


PROGRAMS = {
    # out0 = a ^ b ^ cin, out1 = carry; a, b, cin = pin[0], pin[1], pin[2]
    "adder": {
        "about": ["full adder", "pins a b cin", "out0 = sum", "out1 = carry"],
        "clbs": [clb([PIN, PIN, PIN], lambda a, b, c: a ^ b ^ c, 0),
                 clb([PIN, PIN, PIN], lambda a, b, c: a + b + c >= 2, 0)],
        "model": lambda st, p: (st, [p[0] ^ p[1] ^ p[2], int(p[0] + p[1] + p[2] >= 2)]),
    },
    # 2-bit counter, counts on each clock while pin[2] (enable) is 1; out = q
    "counter": {
        "about": ["2-bit counter", "pin2 = enable", "out1 out0 = count", "press CLOCK"],
        "clbs": [clb([OWN_Q, PIN, PIN], lambda q0, _, en: q0 ^ en, 1),
                 clb([OWN_Q, NEXT_Q, PIN], lambda q1, q0, en: q1 ^ (q0 & en), 1)],
        "model": None,                                      # sequential: see _counter
    },
    # 2-bit shift register: pin[0] shifts in; out0 = q0, out1 = q1 (the previous q0)
    "shift": {
        "about": ["shift register", "pin0 = data in", "out0 -> out1", "press CLOCK"],
        "clbs": [clb([PIN, PIN, PIN], lambda d, _b, _c: d, 1),
                 clb([NEXT_Q, PIN, PIN], lambda q0, _b, _c: q0, 1)],
        "model": None,
    },
}


def bitstream(name: str) -> dict:
    bits = {}
    for j, c in enumerate(PROGRAMS[name]["clbs"]):
        word = c["lut"] + [s >> k & 1 for s in c["sel"] for k in (0, 1)] + [c["reg"]]
        for k, b in enumerate(word):
            bits[f"cfg[{15 * j + k}]"] = b
    return bits


def _step(name, st, p):
    """Model: next state and outputs after one clock with pins p (outputs read after the edge)."""
    if name == "counter":
        q = (st + p[2]) % 4
        return q, [q & 1, q >> 1]
    if name == "shift":
        q0, q1 = p[0], st & 1
        return q0 | q1 << 1, [q0, q1]
    raise KeyError(name)


def check(name: str, n: int, build: bool) -> int:
    lay = place.load(CHIP)
    seq = PROGRAMS[name]["model"] is None
    rng = random.Random(3)
    bad = 0
    with MC() as mc:
        if build:
            place.build(mc, lay)
        mc.cmd("tick rate 1000")
        drv = place.Driver(mc, lay)
        drv.set({b: 0 for b in lay["inputs"]} | {"HOLD_M": 0, "HOLD_S": 1})
        drv.set(bitstream(name) | {"rst_n": 0})           # program, and reset the flops
        drv.set({"rst_n": 1})
        st = 0
        for i in range(n):
            p = [rng.randint(0, 1) for _ in range(3)]
            drv.set({f"pin[{k}]": p[k] for k in range(3)})
            if seq:
                drv.clock()
                st, want = _step(name, st, p)
            else:
                _, want = PROGRAMS[name]["model"](st, p)
            got = drv.read([f"out[{k}]" for k in range(N)])
            got = [got[f"out[{k}]"] for k in range(N)]
            ok = got == want
            bad += not ok
            print(f"  {i:3d} pin={''.join(map(str, p))} want out={want} got={got} {'ok' if ok else 'MISMATCH'}", flush=True)
    print(f"{CHIP} programmed as {name}: {n} steps in vanilla 1.21.11, {bad} mismatches")
    return bad


def demo() -> None:
    """Demo server: redstone:tiny_fpga/load_<program> sets the bitstream levers, and a sign per program."""
    lay = place.load(CHIP)
    fn = DEMO.datapacks / "redstone_chip" / "data" / "redstone" / "function" / CHIP.lower()
    fn.mkdir(parents=True, exist_ok=True)
    for name, prog in PROGRAMS.items():
        lines = []
        for b, v in bitstream(name).items():
            x, y, z = lay["inputs"][b]
            lines.append(f"setblock {x} {place.YB + y} {z} lever[face=floor,facing=north,powered={'true' if v else 'false'}]")
        about = " / ".join(prog["about"])
        lines.append(f'tellraw @a {{"text":"tiny_fpga programmed: {about}","color":"gold"}}')
        (fn / f"load_{name}.mcfunction").write_text("\n".join(lines) + "\n")
    with MC(DEMO) as mc:
        print(mc.cmd("reload"))
    print("functions:", ", ".join(f"redstone:{CHIP.lower()}/load_{n}" for n in PROGRAMS))


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        print(__doc__)
    elif a[0] == "bits":
        bits = bitstream(a[1])
        print("".join(str(bits[f"cfg[{k}]"]) for k in range(15 * N - 1, -1, -1)), "(cfg[29] .. cfg[0])")
    elif a[0] == "check":
        n = int(a[2]) if len(a) > 2 and a[2].isdigit() else 16
        sys.exit(1 if check(a[1], n, "--nobuild" not in a) else 0)
    elif a[0] == "demo":
        demo()
