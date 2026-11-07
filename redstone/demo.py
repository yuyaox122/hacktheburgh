"""The showroom: a separate server you can join and play in (ticks run normally; check.py freezes only its own server).

    python demo.py start          # server-demo/ on localhost:25565 (Minecraft Java 1.21.11 -> Multiplayer -> localhost)
    python demo.py prims          # build the verified primitives with levers and lamps
    python demo.py chip <name>    # build a routed chip (out/<name>/layout.json), with goto + clock functions
    python demo.py op             # make everyone online an operator (for /tick sprint and the clock functions)
    python demo.py stop
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("REDSTONE_SERVER_DIR", "server-demo")
os.environ.setdefault("REDSTONE_RCON_PORT", "25577")
os.environ.setdefault("REDSTONE_GAME_PORT", "25565")

import mc as mcmod  # noqa: E402  (reads the environment above)
import prims  # noqa: E402
from mc import MC, FLOOR_Y  # noqa: E402

Y = FLOOR_Y + 1
SPAWN = (0, Y, 196)
SHOW_Z = 330                       # primitive showroom, south of the chip


def prep(mc: MC) -> None:
    for c in ["gamerule max_block_modifications 10000000", "gamerule advance_time false", "time set noon",
              "gamerule advance_weather false", "weather clear", "gamerule random_tick_speed 0",
              "gamerule spawn_mobs false", "tick unfreeze", f"setworldspawn {SPAWN[0]} {SPAWN[1]} {SPAWN[2]}"]:
        mc.cmd(c)
    mc.forceload(-64, 160, 191, 351)


def sign(x, y, z, lines, facing="south"):
    msgs = ",".join('"' + s.replace('"', "'") + '"' for s in (lines + ["", "", "", ""])[:4])
    return (x, y, z, f'oak_sign[rotation={ {"south": 0, "west": 4, "north": 8, "east": 12}[facing] }]'
                     f'{{front_text:{{messages:[{msgs}]}}}}')


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


def goto_function(lay: dict) -> None:
    """/function redstone:goto/<chip> teleports you to the chip's input levers, looking north at the build."""
    xs = [p[0] for p in lay["inputs"].values()]
    x, z = (min(xs) + max(xs)) // 2, max(p[2] for p in lay["inputs"].values()) + 8
    fn = mcmod.SRV / "world" / "datapacks" / "redstone_chip" / "data" / "redstone" / "function" / "goto"
    fn.mkdir(parents=True, exist_ok=True)
    (fn / f"{lay['chip'].lower()}.mcfunction").write_text(f"tp @s {x} {Y + 6} {z} facing {x} {Y} {z - 30}\n")


def clock_button(mc: MC, lay: dict) -> None:
    """A datapack function that plays one rising clock edge (HOLD_M up, HOLD_S down, HOLD_S up, HOLD_M down,
    each after the settle time), and a button on a command block next to the input levers that runs it.
    The logic itself stays pure redstone: this only stands in for the board's clock source."""
    import json
    import world
    wait = 2 * lay["settle_rt"] + 20
    lev = lambda b, on: (f"setblock {lay['inputs'][b][0]} {world.YB + lay['inputs'][b][1]} {lay['inputs'][b][2]} "
                         f"lever[face=floor,facing=north,powered={'true' if on else 'false'}]")
    steps = [lev("HOLD_M", 1), lev("HOLD_S", 0), lev("HOLD_S", 1), lev("HOLD_M", 0)]
    pack = mcmod.SRV / "world" / "datapacks" / "redstone_chip"
    slug = lay["chip"].lower()
    fn = pack / "data" / "redstone" / "function" / slug
    fn.mkdir(parents=True, exist_ok=True)
    (pack / "pack.mcmeta").write_text(json.dumps({"pack": {"description": "redstone chip clock",
                                                           "min_format": [94, 0], "max_format": [94, 1]}}))
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
    mc.cmd(f'setblock {cx} {world.YB} {cz} command_block{{Command:"function redstone:{slug}/clock"}}')
    mc.cmd(f"setblock {cx} {world.YB + 1} {cz} stone_button[face=floor,facing=north]")
    mc.cmd(f'setblock {cx} {world.YB} {cz + 1} oak_sign[rotation=0]{{front_text:{{messages:["CLOCK","press the button","= one rising","edge of clk"]}}}}')


def main() -> None:
    a = sys.argv[1:] or ["help"]
    if a[0] == "start":
        mcmod.start()
    elif a[0] == "stop":
        with MC() as mc:
            print(mc.cmd("stop"))
    elif a[0] == "chip":
        import world
        lay = world.load(a[1])
        with MC() as mc:
            prep(mc)
            mc.cmd("tick freeze")
            world.build(mc, lay)
            drv = world.Driver(mc, lay)
            drv.set({b: 0 for b in lay["inputs"]}, settle=False)
            if "HOLD_M" in lay["inputs"]:
                drv.set({"HOLD_M": 0, "HOLD_S": 1}, settle=False)
            goto_function(lay)
            if "HOLD_M" in lay["inputs"]:
                clock_button(mc, lay)
            else:
                mc.cmd("reload")
            mc.cmd("tick unfreeze")
            xs = [p[0] for p in lay["inputs"].values()]
            sx, sy, sz = (min(xs) + max(xs)) // 2, Y, max(p[2] for p in lay["inputs"].values()) + 8
            mc.cmd(f"setworldspawn {sx} {sy} {sz}")
            mc.cmd(f'tellraw @a {{"text":"{a[1]} built in redstone: flip the levers (inputs), watch the lamps (outputs)","color":"gold"}}')
            print(f"built {a[1]}; spawn at {sx} {sy} {sz}; stats {lay['stats']}")
    elif a[0] == "op":
        with MC() as mc:
            online = mc.cmd("list").split(":", 1)[-1].strip()
            for name in [n.strip() for n in online.split(",") if n.strip()]:
                print(mc.cmd(f"op {name}"))
            print("online:", online or "nobody")
    elif a[0] == "prims":
        with MC() as mc:
            showroom(mc)
            print("built")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
