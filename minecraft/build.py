"""Compile a Tiny Tapeout chip (netlist + GDS) into a vanilla Minecraft 1.21.11 datapack.

    python build.py tt_um_Electom_cla_4bits            # -> out/<chip>/decap_chip (datapack folder)
    python build.py <chip> --scale 3 --full             # whole tile instead of the logic region

In game: /function decap:build  (draws the chip),  /function decap:view  (teleports you to the panel).
The chip then runs every tick: levers drive the inputs, lamps show the outputs, every gate lights up when its output is 1.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
from pathlib import Path

import cells as lib
import layout as lay
import logic
import netlist as nlmod

ROOT = Path(__file__).resolve().parent
OBJ = logic.OBJ
Y0 = -60                      # top of the default superflat ground is -61

FAMILY = {                    # off block, on block
    "logic": ("yellow_concrete", "ochre_froglight"),
    "arith": ("lime_concrete", "verdant_froglight"),
    "flop": ("purple_concrete", "pearlescent_froglight"),
    "mux": ("orange_concrete", "shroomlight"),
    "buf": ("light_blue_concrete", "sea_lantern"),
    "const": ("white_concrete", "white_concrete"),
}
PHYS = {"decap": "gray_concrete", "tapvpwrvgnd": "light_gray_concrete", "diode": "brown_concrete"}
# layer -> (y offset, thickness, block)
STACK = {
    "li1": (4, 1, "cyan_stained_glass"), "mcon": (5, 1, "light_gray_concrete"),
    "met1": (6, 1, "blue_stained_glass"), "via": (7, 2, "iron_block"),
    "met2": (9, 1, "magenta_stained_glass"), "via2": (10, 2, "iron_block"),
    "met3": (12, 1, "red_stained_glass"), "via3": (13, 2, "iron_block"),
    "met4": (15, 1, "yellow_stained_glass"), "via4": (16, 2, "iron_block"),
    "met5": (18, 1, "orange_stained_glass"),
}
PANEL_Z = -7                  # panel row, north of the die edge (pins are on the north edge)


def mc(b: str) -> str:
    return b if ":" in b else "minecraft:" + b


class Grid:
    """µm -> block coordinates. GDS x -> +x, GDS y -> -z (north is the top of the chip)."""

    def __init__(self, x0: float, y0: float, x1: float, y1: float, s: float):
        self.x0, self.y0, self.x1, self.y1, self.s = x0, y0, x1, y1, s

    def span(self, a: float, b: float) -> tuple[int, int] | None:
        lo, hi = math.ceil(a * self.s - 0.5), math.ceil(b * self.s - 0.5) - 1
        if hi < lo:
            lo = hi = math.floor((a + b) / 2 * self.s)
        return lo, hi

    def box(self, b) -> tuple[int, int, int, int] | None:
        x1, y1, x2, y2 = max(b[0], self.x0), max(b[1], self.y0), min(b[2], self.x1), min(b[3], self.y1)
        if x2 <= x1 or y2 <= y1:
            return None
        xs = self.span(x1 - self.x0, x2 - self.x0)
        zs = self.span(self.y1 - y2, self.y1 - y1)
        return xs[0], zs[0], xs[1], zs[1]

    @property
    def size(self) -> tuple[int, int]:
        return math.ceil((self.x1 - self.x0) * self.s), math.ceil((self.y1 - self.y0) * self.s)


def fill(x1, y1, z1, x2, y2, z2, block, extra="") -> list[str]:
    """fill, split so each command stays under the 32768-block limit."""
    out = []
    dx, dy = x2 - x1 + 1, y2 - y1 + 1
    step = max(1, 32768 // (dx * dy)) if dx * dy <= 32768 else 1
    if dx * dy > 32768:
        xs = max(1, 32768 // dy)
        for xa in range(x1, x2 + 1, xs):
            out += fill(xa, y1, z1, min(x2, xa + xs - 1), y2, z2, block, extra)
        return out
    for za in range(z1, z2 + 1, step):
        out.append(f"fill {x1} {y1} {za} {x2} {y2} {min(z2, za + step - 1)} {mc(block)}{extra}")
    return out


def pinout(info_yaml: Path) -> dict[str, str]:
    if not info_yaml.exists():
        return {}
    out = {}
    for k, v in re.findall(r"^\s*(u[io]o?\[\d+\]|uio\[\d+\]):\s*\"([^\"]*)\"", info_yaml.read_text(), flags=re.M):
        out[k] = v
    return out


def title_of(info_yaml: Path) -> str:
    m = re.search(r"^\s*title:\s*\"([^\"]*)\"", info_yaml.read_text(), flags=re.M) if info_yaml.exists() else None
    return m.group(1) if m else ""


def author_of(info_yaml: Path) -> str:
    m = re.search(r"^\s*author:\s*\"([^\"]*)\"", info_yaml.read_text(), flags=re.M) if info_yaml.exists() else None
    return m.group(1) if m else ""


def label_for(bit: str, pins: dict[str, str]) -> str:
    m = re.fullmatch(r"(ui_in|uo_out|uio_in|uio_out)\[(\d+)\]", bit)
    if m:
        key = {"ui_in": "ui", "uo_out": "uo", "uio_in": "uio", "uio_out": "uio"}[m.group(1)] + f"[{m.group(2)}]"
        if pins.get(key):
            return pins[key]
    return bit


def groups(bits: list[str], pins: dict[str, str]) -> dict[str, list[tuple[str, int]]]:
    """Pinout labels like a[0..3] -> {"a": [(bit, 0), ...]} for the sidebar."""
    g: dict[str, list[tuple[str, int]]] = {}
    for b in bits:
        lab = label_for(b, pins)
        m = re.fullmatch(r"([A-Za-z_]\w*)\[(\d+)\]", lab)
        name, k = (m.group(1), int(m.group(2))) if m else (lab, 0)
        g.setdefault(name, []).append((b, k))
    return g


def compile_chip(chip: str, scale: float, full: bool, out_dir: Path) -> dict:
    d = ROOT / "data" / chip
    nl = nlmod.parse(d / f"{chip}.v")
    missing = lib.fetch({c.type for c in nl.logic})
    if missing:
        raise SystemExit(f"no Liberty data for {missing}")
    prog = logic.build(nl)
    lj = d / "layout.json"
    L = json.loads(lj.read_text()) if lj.exists() else lay.read(d / f"{chip}.gds")
    lj.write_text(json.dumps(L))
    pins = pinout(d / "info.yaml")
    title = title_of(d / "info.yaml") or chip
    author = author_of(d / "info.yaml")

    logic_names = {c.name for c in nl.logic}
    placed = {c["name"]: c for c in L["cells"] if c["name"]}
    lc = [placed[n] for n in logic_names if n in placed]
    W, H = L["die"]
    if full or not lc:
        g = Grid(0, 0, W, H, scale)
    else:
        m = 8.0
        g = Grid(max(0, min(c["box"][0] for c in lc) - m), max(0, min(c["box"][1] for c in lc) - m),
                 min(W, max(c["box"][2] for c in lc) + m), H, scale)
    SX, SZ = g.size

    build: list[str] = []
    # forceload the whole area (+ panel) so build and tick work wherever the player is
    fz0 = PANEL_Z - 6
    for xa in range(-16, SX + 16, 16 * 15):
        build.append(f"forceload add {xa} {fz0} {min(SX + 16, xa + 16 * 15 - 1)} {SZ + 8}")
    blocks: list[str] = []
    blocks += fill(-12, Y0, fz0, SX + 12, Y0 + 24, SZ + 8, "air")
    blocks += fill(-12, Y0 - 1, fz0, SX + 12, Y0 - 1, SZ + 8, "polished_blackstone")
    blocks += fill(0, Y0 - 1, 0, SX - 1, Y0 - 1, SZ - 1, "black_concrete")
    # cells
    display = []                       # (holder, x1, z1, x2, z2, off, on)
    by_name = {c.name: c for c in nl.logic}
    for c in L["cells"]:
        b = g.box(c["box"])
        if not b:
            continue
        x1, z1, x2, z2 = b
        base = c["type"].rsplit("_", 1)[0]
        cat = lib.category(c["type"])
        if cat == "phys":
            blk = next((v for k, v in PHYS.items() if base.startswith(k)), None)
            if blk:
                blocks += fill(x1, Y0, z1, x2, Y0, z2, blk)
            continue
        off, on = FAMILY.get(cat, FAMILY["logic"])
        blocks += fill(x1, Y0, z1, x2, Y0 + 1, z2, off)
        tx2 = x2 - 1 if x2 > x1 else x2
        blocks += fill(x1, Y0 + 2, z1, tx2, Y0 + 2, z2, off)
        cell = by_name.get(c["name"])
        if cell and cat != "const":
            outs = [p for p in lib.cell(cell.type).outputs if p in cell.pins]
            if outs:
                display.append((prog.nets[cell.pins[outs[0]]], x1, z1, tx2, z2, off, on))
    # metal stack
    for lname, boxes in L["layers"].items():
        if lname not in STACK:
            continue
        dy, th, blk = STACK[lname]
        for bx in boxes:
            b = g.box(bx)
            if b:
                blocks += fill(b[0], Y0 + dy - 1, b[1], b[2], Y0 + dy - 2 + th, b[3], blk)

    # panel: one lever per input pin, one lamp per output pin, at the pin's x on the north edge
    def pin_x(bit: str, fallback: int) -> int:
        p = L["pins"].get(bit)
        if p and g.x0 <= p[0] <= g.x1:
            return g.span(p[0] - g.x0, p[0] - g.x0)[0]
        return fallback

    used_x: set[int] = set()

    def free_x(x: int) -> int:
        while any(abs(x - u) < 2 for u in used_x):
            x += 2
        used_x.add(x)
        return x

    panel = []          # (kind, bit, x)
    for i, b in enumerate(prog.inputs):
        panel.append(("in", b, free_x(pin_x(b, -10 - 3 * i))))
    for i, b in enumerate(prog.outputs):
        panel.append(("out", b, free_x(pin_x(b, SX + 4 + 3 * i))))
    auto_x = free_x(min(x for _, _, x in panel) - 6)
    labels = []
    for kind, b, x in panel:
        blocks.append(f"fill {x} {Y0} {PANEL_Z} {x} {Y0} {PANEL_Z} {mc('polished_andesite')}")
        # trace from the panel to the die edge
        blocks += fill(x, Y0 - 1, PANEL_Z + 1, x, Y0 - 1, -1, "light_gray_concrete")
        if kind == "in":
            on = "true" if b in ("rst_n", "ena") else "false"
            blocks.append(f"setblock {x} {Y0 + 1} {PANEL_Z} minecraft:lever[face=floor,facing=south,powered={on}]")
        else:
            blocks.append(f"setblock {x} {Y0 + 1} {PANEL_Z} minecraft:redstone_lamp")
        labels.append((x, label_for(b, pins), b, kind))
    blocks.append(f"setblock {auto_x} {Y0} {PANEL_Z} minecraft:polished_andesite")
    blocks.append(f"setblock {auto_x} {Y0 + 1} {PANEL_Z} minecraft:lever[face=floor,facing=south,powered=true]")
    labels.append((auto_x, "auto clock", "", "auto"))

    # ---------------- tick
    nets = prog.nets
    tick = [f"execute unless score #built {OBJ} matches 1 run return 0"]
    has_clk = "clk" in prog.inputs
    for kind, b, x in panel:
        if kind == "in":
            tick.append(f"execute store success score {nets[b]} {OBJ} if block {x} {Y0 + 1} {PANEL_Z} minecraft:lever[powered=true]")
    if has_clk:
        tick.append(f"execute store success score #auto {OBJ} if block {auto_x} {Y0 + 1} {PANEL_Z} minecraft:lever[powered=true]")
        tick.append(f"execute if score #auto {OBJ} matches 1 run function decap:clock")
    tick += logic.emit_comb(prog)
    tick += logic.emit_flops(prog)
    tick += logic.emit_comb(prog)
    for h, x1, z1, x2, z2, off, on in display:
        tick.append(f"execute if score {h} {OBJ} matches 1 run fill {x1} {Y0 + 2} {z1} {x2} {Y0 + 2} {z2} {mc(on)} replace {mc(off)}")
        tick.append(f"execute if score {h} {OBJ} matches 0 run fill {x1} {Y0 + 2} {z1} {x2} {Y0 + 2} {z2} {mc(off)} replace {mc(on)}")
    for kind, b, x in panel:
        h = nets[logic._resolve(nl, b)]
        tick.append(f"execute if score {h} {OBJ} matches 1 run fill {x} {Y0 - 1} {PANEL_Z + 1} {x} {Y0 - 1} -1 minecraft:sea_lantern replace minecraft:light_gray_concrete")
        tick.append(f"execute if score {h} {OBJ} matches 0 run fill {x} {Y0 - 1} {PANEL_Z + 1} {x} {Y0 - 1} -1 minecraft:light_gray_concrete replace minecraft:sea_lantern")
        if kind == "out":
            tick.append(f"execute if score {h} {OBJ} matches 1 run setblock {x} {Y0} {PANEL_Z} minecraft:redstone_block")
            tick.append(f"execute if score {h} {OBJ} matches 0 run setblock {x} {Y0} {PANEL_Z} minecraft:polished_andesite")
    # sidebar: named values from the pinout
    side = []
    for name, bits in list(groups(prog.inputs, pins).items()) + list(groups(prog.outputs, pins).items()):
        if name in ("clk", "rst_n", "ena"):
            continue
        side.append(name)
        tick.append(f"scoreboard players set {name} show 0")
        for b, k in bits:
            tick.append(f"execute if score {nets[logic._resolve(nl, b)]} {OBJ} matches 1 run scoreboard players add {name} show {1 << k}")

    load = [f"scoreboard objectives add {OBJ} dummy",
            f"scoreboard objectives add show dummy {json.dumps({'text': title, 'color': 'gold'})}",
            "scoreboard objectives setdisplay sidebar show",
            f"scoreboard players set #k0 {OBJ} 0", f"scoreboard players set #k1 {OBJ} 1", f"scoreboard players set #k2 {OBJ} 2"]
    clock = [f"scoreboard players add #cc {OBJ} 1",                       # 8-tick period: 2.5 Hz
             f"execute if score #cc {OBJ} matches 8.. run scoreboard players set #cc {OBJ} 0",
             f"execute if score #cc {OBJ} matches 0..3 run scoreboard players set #clk {OBJ} 0",
             f"execute if score #cc {OBJ} matches 4..7 run scoreboard players set #clk {OBJ} 1",
             f"scoreboard players operation {nets['clk']} {OBJ} = #clk {OBJ}"] if has_clk else []
    cx, cz = SX // 2, SZ // 2
    view = [f"tp @s {auto_x - 4} {Y0 + 12} {PANEL_Z - 14} -35 35"]
    lab = ["kill @e[type=text_display,tag=decap]"]
    for x, text, bit, kind in labels:
        col = {"in": "aqua", "out": "gold", "auto": "gray"}[kind]
        lab.append(f"summon text_display {x + 0.5} {Y0 + 2.4} {PANEL_Z + 0.5} {{Tags:[\"decap\"],billboard:\"center\","
                   f"text:{json.dumps({'text': text, 'color': col})},background:0}}")
    lab.append(f"summon text_display {cx} {Y0 + 22} {PANEL_Z} {{Tags:[\"decap\"],billboard:\"center\","
               f"transformation:{{left_rotation:[0f,0f,0f,1f],right_rotation:[0f,0f,0f,1f],translation:[0f,0f,0f],scale:[6f,6f,6f]}},"
               f"text:{json.dumps({'text': title, 'color': 'gold', 'bold': True})},background:0}}")
    sub = f"{author} · Tiny Tapeout 08 · {len(nl.logic)} cells · compiled from the real netlist + GDS"
    lab.append(f"summon text_display {cx} {Y0 + 19.5} {PANEL_Z} {{Tags:[\"decap\"],billboard:\"center\","
               f"transformation:{{left_rotation:[0f,0f,0f,1f],right_rotation:[0f,0f,0f,1f],translation:[0f,0f,0f],scale:[2.5f,2.5f,2.5f]}},"
               f"text:{json.dumps({'text': sub, 'color': 'white'})},background:0}}")
    env = ["time set midnight", "weather clear", "gamerule advance_time false", "gamerule advance_weather false",
           "gamerule spawn_mobs false", f"setworldspawn {auto_x - 4} {Y0 + 1} {PANEL_Z - 14}"]   # 1.21.11 rule names
    build_fn = build + ["schedule function decap:build_blocks 40t"]
    blocks_fn = blocks + [f"scoreboard players set #built {OBJ} 1", "function decap:labels", "function decap:env",
                          "say Chip built. Run /function decap:view"]

    pack = out_dir / "decap_chip"
    if pack.exists():
        shutil.rmtree(pack)
    fdir = pack / "data" / "decap" / "function"
    fdir.mkdir(parents=True)
    (pack / "pack.mcmeta").write_text(json.dumps({"pack": {"description": f"Decap: {title} compiled to Minecraft",
                                                           "min_format": [94, 0], "max_format": [94, 1]}}, indent=1))
    tags = pack / "data" / "minecraft" / "tags" / "function"
    tags.mkdir(parents=True)
    (tags / "load.json").write_text(json.dumps({"values": ["decap:load"]}))
    (tags / "tick.json").write_text(json.dumps({"values": ["decap:tick"]}))
    for name, lines in {"load": load, "build": build_fn, "build_blocks": blocks_fn, "tick": tick, "clock": clock,
                        "view": view, "labels": lab, "env": env}.items():
        (fdir / f"{name}.mcfunction").write_text("\n".join(lines) + "\n")
    meta = {"chip": chip, "title": title, "scale": scale, "size": [SX, SZ], "y0": Y0, "panel_z": PANEL_Z,
            "panel": [{"kind": k, "bit": b, "x": x, "label": label_for(b, pins)} for k, b, x in panel],
            "auto_x": auto_x, "nets": nets, "inputs": prog.inputs, "outputs": prog.outputs, "sidebar": side,
            "counts": {"build_blocks": len(blocks), "tick": len(tick), "cells": len(nl.logic), "display": len(display)}}
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=1))
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("chip")
    ap.add_argument("--scale", type=float, default=3.0, help="blocks per µm")
    ap.add_argument("--full", action="store_true", help="whole tile, not just the logic region")
    ap.add_argument("--install", type=Path, help="world folder to copy the datapack into")
    a = ap.parse_args()
    out = ROOT / "out" / a.chip
    out.mkdir(parents=True, exist_ok=True)
    m = compile_chip(a.chip, a.scale, a.full, out)
    print(json.dumps({k: m[k] for k in ("title", "size", "counts", "inputs", "outputs", "sidebar")}))
    if a.install:
        dst = a.install / "datapacks" / "decap_chip"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(out / "decap_chip", dst)
        print("installed", dst)


if __name__ == "__main__":
    main()
