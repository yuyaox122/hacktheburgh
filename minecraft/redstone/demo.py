"""The showroom: a separate server you can join and play in (ticks run normally; check.py freezes only its own server).

    python -m minecraft.redstone.demo start          # minecraft/server/demo/ on localhost:25565 (Java 1.21.11)
    python -m minecraft.redstone.demo prims          # build the verified primitives with levers and lamps
    python -m minecraft.redstone.demo chip <name>    # build a routed chip (out/<name>/layout.json), with goto + clock
    python -m minecraft.redstone.demo op             # make everyone online an operator (for /tick sprint and clocks)
    python -m minecraft.redstone.demo stop
"""
from __future__ import annotations

import sys

from . import place, prims
from ..common.datapack import sign as sign_state
from ..common.datapack import write_meta
from ..common.server import Server
from .mc import DEMO, FLOOR_Y, MC

Y = FLOOR_Y + 1
SPAWN = (0, Y, 196)
SHOW_Z = 330                       # primitive showroom, south of the chip
PACK = "redstone_chip"             # datapack for the goto / clock functions


def prep(mc: MC) -> None:
    for c in ["gamerule max_block_modifications 10000000", "gamerule advance_time false", "time set noon",
              "gamerule advance_weather false", "weather clear", "gamerule random_tick_speed 0",
              "gamerule spawn_mobs false", "tick unfreeze", f"setworldspawn {SPAWN[0]} {SPAWN[1]} {SPAWN[2]}"]:
        mc.cmd(c)
    mc.forceload(-64, 160, 191, 351)


def sign(x, y, z, lines, facing="south"):
    return (x, y, z, sign_state(lines, {"south": 0, "west": 4, "north": 8, "east": 12}[facing]))


def showroom(mc: MC) -> None:
    prep(mc)
    mc.clear(-8, Y, SHOW_Z - 12, 60, Y + 4, SHOW_Z + 14)
    blurb = {"BUF": ["BUF", "out = A"], "NOT": ["NOT", "out = not B", "comparator", "subtract mode"],
             "INH": ["INH", "out = A and not B", "comparator", "subtract mode"], "OR": ["OR", "out = A or B"],
             "LATCH": ["LATCH", "H=0: out follows D", "H=1: out holds"]}
    for i, name in enumerate(["BUF", "NOT", "INH", "OR", "LATCH"]):
        t = prims.TILES[name]
        ox, oz = 2 + 11 * i, SHOW_Z
        mc.place([(ox + x, Y + y, oz + z, s) for x, y, z, s in t.blocks])
        for pin, (x, y, z) in t.inputs.items():
            # a lever on the floor at each input port; a sign beside it names the pin
            mc.place([(ox + x, Y + y, oz + z, "lever[face=floor,facing=north,powered=false]")])
            sx, sz = (ox + x - 1, oz + z) if pin in ("A", "D") else (ox + x, oz + z - 1)
            mc.place([sign(sx, Y, sz, [pin])])
        ox_, oy_, oz_ = t.output
        mc.place([(ox + ox_, Y + oy_, oz + oz_, "redstone_lamp")])
        mc.place([sign(ox + 1, Y, oz + 5, blurb[name], "south")])
    mc.cmd(f'tellraw @a {{"text":"Redstone primitives built: flip the levers","color":"gold"}}')


def goto_function(lay: dict, srv: Server = DEMO) -> None:
    """/function redstone:goto/<chip> teleports you just south of the chip's levers and lamps, looking south over
    the build (in 2-D the levers and lamps are the northmost row, so they're right behind you)."""
    xs = [p[0] for p in lay["inputs"].values()]
    x, z = (min(xs) + max(xs)) // 2, max(p[2] for p in lay["inputs"].values()) + 5   # just south of the levers
    pack = srv.datapacks / PACK
    write_meta(pack, "redstone chip clock")
    fn = pack / "data" / "redstone" / "function" / "goto"
    fn.mkdir(parents=True, exist_ok=True)
    (fn / f"{lay['chip'].lower()}.mcfunction").write_text(
        f"tp @s {x} {Y} {z} facing {x} {Y - 8} {z + 30}\n"
        'tellraw @s {"text":"levers (inputs) and lamps (outputs) are just behind you, to the north","color":"gold"}\n')
    own = fn.parent / lay["chip"].lower()
    own.mkdir(parents=True, exist_ok=True)
    (own / "settle.mcfunction").write_text(f"tick sprint {2 * lay['settle_rt'] + 20}\n"
                                           'tellraw @a {"text":"fast-forwarded until the outputs settle","color":"yellow"}\n')


def clock_button(mc: MC, lay: dict, srv: Server = DEMO) -> None:
    """A datapack function that plays one rising clock edge (HOLD_M up, HOLD_S down, HOLD_S up, HOLD_M down,
    each after the settle time), and a button on a command block next to the input levers that runs it.
    The logic itself stays pure redstone: this only stands in for the board's clock source."""
    YB = place.YB
    wait = 2 * lay["settle_rt"] + 20
    lev = lambda b, on: (f"setblock {lay['inputs'][b][0]} {YB + lay['inputs'][b][1]} {lay['inputs'][b][2]} "
                         f"lever[face=floor,facing=north,powered={'true' if on else 'false'}]")
    steps = [lev("HOLD_M", 1), lev("HOLD_S", 0), lev("HOLD_S", 1), lev("HOLD_M", 0)]
    pack = srv.datapacks / PACK
    slug = lay["chip"].lower()
    fn = pack / "data" / "redstone" / "function" / slug
    fn.mkdir(parents=True, exist_ok=True)
    write_meta(pack, "redstone chip clock")
    # first let the current inputs settle through the logic, then play the edge
    (fn / "clock.mcfunction").write_text(
        'tellraw @a {"text":"clock: settling inputs, then one rising edge ...","color":"yellow"}\n'
        + f"schedule function redstone:{slug}/clock_1 {wait}t\n")
    # /tick needs permission level 3, which command blocks do not have, so the fast version is separate
    (fn / "clock_fast.mcfunction").write_text(f"tick sprint {5 * wait + 40}\nfunction redstone:{slug}/clock\n")
    (fn / "settle.mcfunction").write_text(f"tick sprint {wait}\n"
                                          'tellraw @a {"text":"fast-forwarded until the outputs settle","color":"yellow"}\n')
    (fn / "clock_1.mcfunction").write_text(steps[0] + f"\nschedule function redstone:{slug}/clock_2 {wait}t\n")
    (fn / "clock_2.mcfunction").write_text(steps[1] + f"\nschedule function redstone:{slug}/clock_3 {wait}t\n")
    (fn / "clock_3.mcfunction").write_text(steps[2] + f"\nschedule function redstone:{slug}/clock_4 {wait}t\n")
    (fn / "clock_4.mcfunction").write_text(steps[3] + '\ntellraw @a {"text":"clock: done (outputs settle in a few s)","color":"yellow"}\n')
    print(mc.cmd("reload"))
    x, y, z = lay["inputs"]["HOLD_S"]
    cx, cz = x + 6, z + 6
    mc.cmd(f'setblock {cx} {YB} {cz} command_block{{Command:"function redstone:{slug}/clock"}}')
    mc.cmd(f"setblock {cx} {YB + 1} {cz} stone_button[face=floor,facing=north]")
    mc.cmd(f"setblock {cx} {YB} {cz + 1} {sign_state(['CLOCK', 'press the button', '= one rising', 'edge of clk'])}")


def build_chip(chip: str, srv: Server = DEMO) -> None:
    """Build out/<chip>/layout.json in a running server, with its goto (and clock) functions; spawn moves there."""
    lay = place.load(chip)
    with MC(srv) as mc:
        prep(mc)
        mc.cmd("tick freeze")
        place.build(mc, lay)
        drv = place.Driver(mc, lay)
        drv.set({b: 0 for b in lay["inputs"]}, settle=False)
        if "HOLD_M" in lay["inputs"]:
            drv.set({"HOLD_M": 0, "HOLD_S": 1}, settle=False)
        goto_function(lay, srv)
        if "HOLD_M" in lay["inputs"]:
            clock_button(mc, lay, srv)
        else:
            mc.cmd("reload")
        mc.cmd("tick unfreeze")
        xs = [p[0] for p in lay["inputs"].values()]
        sx, sy, sz = (min(xs) + max(xs)) // 2, Y, max(p[2] for p in lay["inputs"].values()) + 5
        mc.cmd(f"setworldspawn {sx} {sy} {sz}")
        mc.cmd(f'tellraw @a {{"text":"{chip} built in redstone: flip the levers (inputs), watch the lamps (outputs)","color":"gold"}}')
        print(f"built {chip}; spawn at {sx} {sy} {sz}; stats {lay['stats']}")


def main() -> None:
    a = sys.argv[1:] or ["help"]
    if a[0] == "start":
        DEMO.start()
    elif a[0] == "stop":
        with MC(DEMO) as mc:
            print(mc.cmd("stop"))
    elif a[0] == "chip":
        build_chip(a[1])
    elif a[0] == "op":
        with MC(DEMO) as mc:
            online = mc.cmd("list").split(":", 1)[-1].strip()
            for name in [n.strip() for n in online.split(",") if n.strip()]:
                print(mc.cmd(f"op {name}"))
            print("online:", online or "nobody")
    elif a[0] == "prims":
        with MC(DEMO) as mc:
            showroom(mc)
            print("built")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
