"""One world, three exhibits: the real layout (this folder) next to two real-redstone builds (minecraft.redstone).

    python -m minecraft.world.build tt_um_Electom_cla_4bits --plant 137
    python -m minecraft.world.server setup tt_um_Electom_cla_4bits
    python -m minecraft.world.server run              # (in the background)
    python -m minecraft.world.showcase                # builds everything into that server

Layout (x east, z south), all on a grass field:
    z  -40 ..   76   real layout of the CLA (GDS as blocks, logic as a datapack), with its lever panel on the north edge
    z  200 ..  340   the same CLA in real redstone (the redstone router's default z)
    z  400 ..  553   tt_um_sign_addsub in real redstone (routed at z 400 so it does not overlap)
Teleports: /function decap:view, decap:overview, decap:tour/<n>, redstone:goto/<chip>.
The redstone builds go in through minecraft.redstone.demo, pointed at this server.
"""
from __future__ import annotations

import argparse
import json
import random
import time

from . import OUT, server
from .build import Y0, fill
from ..common.datapack import text_display
from ..redstone import OUT as REDSTONE_OUT
from ..redstone import chipdata, demo, pnr

FLOOR = Y0 - 1
REDSTONE_CHIPS = {"tt_um_Electom_cla_4bits": 200, "tt_um_sign_addsub": 400}   # chip -> z of its north edge
TIME = 18000                    # midnight: stars, glowing wires; decap:tick gives everyone night vision


def redstone_layouts() -> dict[str, dict]:
    """Route each redstone chip at its z (cached in minecraft/redstone/out/<chip>/layout.json)."""
    lays = {}
    for chip, z in REDSTONE_CHIPS.items():
        lj = REDSTONE_OUT / chip / "layout.json"
        lay = json.loads(lj.read_text()) if lj.exists() else None
        if not lay or min(b[2] for b in lay["blocks"]) < z:
            print(chipdata.fetch(chip))
            pnr.route_chip(chip, z)
            lay = json.loads(lj.read_text())
        lays[chip] = lay
    return lays


def forceload(x0, z0, x1, z1, op="add") -> list[str]:
    """forceload, split into strips of at most 256 chunks (the command's limit)."""
    cz0, cz1 = z0 >> 4, z1 >> 4
    w = max(1, 256 // (cz1 - cz0 + 1))
    return [f"forceload {op} {cx * 16} {z0} {min(x1, (cx + w) * 16 - 1)} {z1}" for cx in range(x0 >> 4, (x1 >> 4) + 1, w)]


def bbox(lay: dict) -> tuple[int, int, int, int]:
    xs = [b[0] for b in lay["blocks"]] + [p[0] for p in lay["inputs"].values()]
    zs = [b[2] for b in lay["blocks"]] + [p[2] for p in lay["inputs"].values()]
    return min(xs), min(zs), max(xs), max(zs)


def tdisp(x, y, z, text, color, s, bold=False) -> str:
    return text_display(x, y, z, text, color, s, bold, tag="showcase")


def pad(x0, z0, x1, z1, inner: str, rim: str, m: int = 4) -> list[str]:
    """A floor pad: a rim band, then the inner floor, flush with the grass. Only grass is replaced, so a re-run
    never swaps the block under a lever or dust that is already built (that would pop it off as an item)."""
    g = " replace minecraft:grass_block"
    return fill(x0 - 1, FLOOR, z0 - 1, x1 + 1, FLOOR, z1 + 1, inner, g) + fill(x0 - m, FLOOR, z0 - m, x1 + m, FLOOR, z1 + m, rim, g)


def lamps(x0, z0, x1, z1, m: int = 3, step: int = 12) -> list[str]:
    """Sea lanterns set into the rim every few blocks, so the pads read as lit stages."""
    out = []
    for x in range(x0 - m, x1 + m + 1, step):
        out += [f"setblock {x} {FLOOR} {z0 - m} sea_lantern", f"setblock {x} {FLOOR} {z1 + m} sea_lantern"]
    for z in range(z0 - m, z1 + m + 1, step):
        out += [f"setblock {x0 - m} {FLOOR} {z} sea_lantern", f"setblock {x1 + m} {FLOOR} {z} sea_lantern"]
    return out


def trees(keep_out: list[tuple[int, int, int, int]], area: tuple[int, int, int, int], n: int, seed: int = 7) -> list[str]:
    """Scatter trees and flowers over the field, outside every exhibit (plus a margin) and its view lanes."""
    rnd = random.Random(seed)
    x0, z0, x1, z1 = area
    out, placed = [], 0
    while placed < n:
        x, z = rnd.randint(x0, x1), rnd.randint(z0, z1)
        if any(a - 14 <= x <= c + 14 and b - 14 <= z <= d + 14 for a, b, c, d in keep_out):
            continue
        kind = rnd.choice(["oak", "oak", "birch", "cherry", "fancy_oak", "spruce"])
        flower = f"place feature minecraft:flower_plain {x + rnd.randint(-5, 5)} {Y0} {z + rnd.randint(-5, 5)}"
        # place needs the chunk loaded: load it just for this tree
        out += [f"forceload add {x} {z}", f"place feature minecraft:{kind} {x} {Y0} {z}", flower, f"forceload remove {x} {z}"]
        placed += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chip", default="tt_um_Electom_cla_4bits", help="the real-layout chip (built by build.py)")
    a = ap.parse_args()
    meta = json.loads((OUT / a.chip / "meta.json").read_text())
    sx, sz = meta["size"]
    r = server.connect()

    # 1. the field: always midnight, no weather or mobs, nothing grows or decays
    for c in [f"time set {TIME}", "weather clear", "gamerule advance_time false", "gamerule advance_weather false",
              "gamerule spawn_mobs false", "gamerule random_tick_speed 0", "gamerule max_block_modifications 10000000",
              "kill @e[type=text_display,tag=showcase]"]:
        r(c)

    # 2. the real layout (this datapack): a dark "wafer" pad under the die and its panel
    for c in forceload(-48, -48, sx + 48, sz + 48):
        r(c)
    for c in pad(0, -24, sx - 1, sz - 1, "black_concrete", "polished_deepslate", 6) + lamps(0, -24, sx - 1, sz - 1, 5):
        r(c)
    r("function decap:build")
    for _ in range(300):
        if "has 1" in r(f"scoreboard players get #built chip"):
            break
        time.sleep(1)
    else:
        raise SystemExit("decap:build did not finish")
    r("function decap:env")
    r(f"time set {TIME}")
    boxes = [(-6, -30, sx + 5, sz + 5)]

    # 3. the redstone builds, through minecraft.redstone.demo, pointed at this server
    lays = redstone_layouts()
    for chip, lay in lays.items():
        x0, z0, x1, z1 = bbox(lay)
        for c in forceload(x0 - 8, z0 - 8, x1 + 8, z1 + 8):
            r(c)
        for c in pad(x0, z0, x1, z1, "smooth_stone", "polished_andesite", 5) + lamps(x0, z0, x1, z1, 4):
            r(c)
        boxes.append((x0 - 6, z0 - 6, x1 + 6, z1 + 6))
    r.close()
    for chip in lays:
        demo.build_chip(chip, server.SERVER)
    r = server.connect()
    for chip, lay in lays.items():
        x0, z0, x1, z1 = bbox(lay)
        cx = (x0 + x1) // 2
        st = lay["stats"]
        name = "the same CLA" if chip == a.chip else chip
        r(tdisp(cx, Y0 + 24, z0 - 6, f"{name}, in real redstone", "red", 8, True))
        r(tdisp(cx, Y0 + 20, z0 - 6, f"{chip}  ·  {st['size'][0]} × {st['size'][1]} blocks  ·  "
                f"{st['blocks']:,} redstone parts  ·  {st['repeaters_in_wires']} repeaters", "white", 3))
        r(tdisp(cx, Y0 + 17.5, z0 - 6, "no command blocks: verified against the netlist in vanilla", "gray", 2.2))

    # 4. backdrop: trees and flowers around the exhibits, a daylight sky, and a tour
    xs = [b[0] for b in boxes] + [b[2] for b in boxes]
    zs = [b[1] for b in boxes] + [b[3] for b in boxes]
    area = (min(xs) - 70, min(zs) - 70, max(xs) + 70, max(zs) + 70)
    for c in trees(boxes, area, 450):
        r(c)
    for b in boxes:   # re-add: removing a tree's chunk may have dropped part of an exhibit (its logic must keep running)
        for c in forceload(*b):
            r(c)
    tour = server.SERVER.datapacks / "decap_chip" / "data" / "decap" / "function" / "tour"
    tour.mkdir(parents=True, exist_ok=True)
    # each stop looks down at about 35 degrees from just outside the exhibit, close enough to stay inside render distance
    stops = [(sx // 2, Y0 + 75, -110, sx // 2, Y0, sz // 2)]                        # 1: real layout from the north
    for b in boxes[1:]:
        cx, cz = (b[0] + b[2]) // 2, (b[1] + b[3]) // 2
        stops.append((cx, Y0 + 95, b[1] - 110, cx, Y0, cz))                         # 2, 3: each redstone build
    mx, mz = (min(xs) + max(xs)) // 2, (min(zs) + max(zs)) // 2
    stops.append((mx - 40, Y0 + 230, mz - 40, mx, Y0, mz))                          # 4: aerial over the middle
    for i, (x, y, z, fx, fy, fz) in enumerate(stops, 1):
        (tour / f"{i}.mcfunction").write_text(f"tp @s {x} {y} {z} facing {fx} {fy} {fz}\n")
    r("kill @e[type=item]")   # nothing should drop, but never leave loose items lying on the exhibits
    r("reload")
    r(f"time set {TIME}")
    r(f"setworldspawn {stops[0][0]} {Y0} {stops[0][2] + 30}")
    r('tellraw @a {"text":"Showcase ready: /function decap:tour/1 .. /' + str(len(stops)) + '","color":"gold"}')
    print("showcase built; tour stops:", len(stops))


if __name__ == "__main__":
    main()
