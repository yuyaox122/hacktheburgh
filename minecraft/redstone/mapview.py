"""Top-down picture of a routed build, no Minecraft needed: out/<chip>/map.png (standard library only).

    python -m minecraft.redstone.mapview <chip> [pixels per block, default 3]

Each (x, z) shows its highest block. Tiles are dark grey, wires are coloured by layer:
ground runs (y=0) brown, trunks (y=1..3) orange, spines (y=4..6) blue. Inputs (levers) are green and
outputs (lamps) yellow. North is up, as in the game's map.
"""
from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

from . import OUT

BG = (238, 234, 224)


def colour(state: str) -> tuple[int, int, int] | None:
    """Fixed colours for pads and constants; None means 'tile part or wire', decided by the caller."""
    if state.startswith("lever"):
        return (40, 170, 70)
    if state.startswith("redstone_lamp"):
        return (240, 200, 40)
    if state.startswith("redstone_block"):
        return (190, 30, 30)
    return None


def render(chip: str, px: int = 3) -> Path:
    lay = json.loads((OUT / chip / "layout.json").read_text())
    top: dict = {}
    for x, y, z, s in lay["blocks"] + [list(b) for b in lay["last"]]:
        if y < 0 or s.endswith("_concrete"):
            continue
        if (x, z) not in top or y > top[(x, z)][0]:
            top[(x, z)] = (y, s)
    # wire cells are the ones with a coloured support or floor under them
    wire = {(x, y + 1, z) for x, y, z, s in lay["blocks"] if s.endswith("_concrete")}
    xs = [k[0] for k in top]
    zs = [k[1] for k in top]
    x0, z0 = min(xs) - 2, min(zs) - 2
    w, h = max(xs) - x0 + 3, max(zs) - z0 + 3
    img = [[BG] * (w * px) for _ in range(h * px)]
    for (x, z), (y, s) in top.items():
        c = colour(s)
        if c is None:
            if (x, y, z) in wire:
                c = (120, 70, 40) if y == 0 else (230, 120, 30) if y <= 3 else (40, 90, 220)
            else:
                c = (70, 70, 78)          # tile part
        for i in range(px):
            row = img[(z - z0) * px + i]
            for j in range(px):
                row[(x - x0) * px + j] = c
    raw = b"".join(b"\x00" + bytes(v for p in row for v in p) for row in img)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w * px, h * px, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    out = OUT / chip / "map.png"
    out.write_bytes(png)
    return out


if __name__ == "__main__":
    print(render(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3))
