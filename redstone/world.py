"""Put a routed layout into a running server and drive it.

    build(mc, layout)            place every block (supports first, constants last), then settle
    Driver(mc, layout).set(v)    flip input levers;  .clock()  two-phase HOLD_M/HOLD_S sequence;  .read(bits)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from mc import MC, FLOOR_Y

ROOT = Path(__file__).resolve().parent
YB = FLOOR_Y + 1            # world y of layout y=0


def load(chip: str) -> dict:
    return json.loads((ROOT / "out" / chip / "layout.json").read_text())


def build(mc: MC, lay: dict, verbose: bool = True) -> None:
    x0, y0, z0, x1, y1, z1 = lay["bbox"]
    mc.forceload(x0 - 2, z0 - 2, x1 + 2, z1 + 6)
    time.sleep(2)                                     # let the forced chunks load before writing into them
    mc.clear(x0 - 1, YB, z0 - 1, x1 + 1, YB + 5, z1 + 5)
    mc.cmd(f"fill {x0 - 1} {FLOOR_Y} {z0 - 1} {min(x1 + 1, x0 + 31)} {FLOOR_Y} {z1 + 5} stone")
    for x in range(x0 - 1, x1 + 2, 32):
        mc.cmd(f"fill {x} {FLOOR_Y} {z0 - 1} {min(x + 31, x1 + 1)} {FLOOR_Y} {z1 + 5} stone")
    t0 = time.time()
    blocks = lay["blocks"]
    for i, (x, y, z, s) in enumerate(blocks):
        r = mc.cmd(f"setblock {x} {YB + y} {z} {s}")
        if r and "Changed" not in r and "Could not set" not in r:
            raise RuntimeError(f"setblock {x} {YB + y} {z} {s}: {r}")
        if verbose and i % 5000 == 0:
            print(f"  placed {i}/{len(blocks)} ({time.time() - t0:.0f}s)", flush=True)
    for x, y, z, s in lay["last"]:
        mc.cmd(f"setblock {x} {YB + y} {z} {s}")
    for x, y, z, text in lay.get("labels", []):
        mc.cmd(f'setblock {x} {YB + y} {z} oak_sign[rotation=0]{{front_text:{{messages:["{text}","","",""]}}}}')
    if verbose:
        print(f"  built {len(blocks) + len(lay['last'])} blocks in {time.time() - t0:.0f}s", flush=True)


class Driver:
    def __init__(self, mc: MC, lay: dict, live: bool = False):
        self.mc, self.lay, self.live = mc, lay, live
        self.state = {b: 0 for b in lay["inputs"]}
        self.settle = 2 * lay["settle_rt"] + 20           # game ticks

    def _lever(self, b, on):
        x, y, z = self.lay["inputs"][b]
        self.mc.cmd(f"setblock {x} {YB + y} {z} lever[face=floor,facing=north,powered={'true' if on else 'false'}]")
        self.state[b] = int(on)

    def wait(self, ticks=None):
        n = ticks or self.settle
        if self.live:
            time.sleep(n / 20)
        else:
            self.mc.step(n)

    def set(self, v: dict, settle=True):
        for b, on in v.items():
            if b in self.state and self.state[b] != int(on):
                self._lever(b, on)
        if settle:
            self.wait()

    def clock(self):
        for hm, hs in ((1, 1), (1, 0), (1, 1), (0, 1)):     # close master, open slave, close slave, open master
            self.set({"HOLD_M": hm, "HOLD_S": hs})

    def read(self, bits):
        out = {}
        probes = []
        for b in bits:
            o = self.lay["outputs"].get(b)
            if isinstance(o, int) or o is None:
                out[b] = int(o or 0)
            else:
                probes.append(b)
        for b in probes:
            x, y, z, st = self.lay["outputs"][b]
            out[b] = int(self.mc.test(x, YB + y, z, st))
        return out
