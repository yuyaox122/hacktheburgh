"""Record a scripted fly-through of the built chip: camera path over RCON (tp every tick) + ffmpeg window capture.

    python -m minecraft.world.fly tt_um_Electom_cla_4bits out.mp4   # Windows; needs the server and client running
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import time
from pathlib import Path

from . import OUT
from .server import connect

HERE = Path(__file__).resolve().parent
TITLE = "Minecraft 1.21.11 - Multiplayer (3rd-party Server)"


def look(p, t):
    dx, dy, dz = t[0] - p[0], t[1] - p[1], t[2] - p[2]
    yaw = math.degrees(math.atan2(-dx, dz))
    pitch = -math.degrees(math.atan2(dy, math.hypot(dx, dz)))
    return yaw, pitch


def cr(p0, p1, p2, p3, u):
    """Catmull-Rom between p1 and p2."""
    return [0.5 * (2 * b + (-a + c) * u + (2 * a - 5 * b + 4 * c - d) * u * u + (-a + 3 * b - 3 * c + d) * u ** 3)
            for a, b, c, d in zip(p0, p1, p2, p3)]


def main():
    chip, out = sys.argv[1], Path(sys.argv[2])
    m = json.loads((OUT / chip / "meta.json").read_text())
    Y, PZ, SX, SZ = m["y0"], m["panel_z"], m["size"][0], m["size"][1]
    lv = {p["bit"]: p["x"] for p in m["panel"] if p["kind"] == "in"}
    ins = [p["x"] for p in m["panel"] if p["kind"] == "in"]
    pmid = (min(ins) + max(ins)) / 2
    tro = m.get("plant")
    tx, ty, tz = tro["xyz"] if tro else (SX / 2, Y + 30, SZ / 2)
    mid = SZ / 2
    # (seconds to reach this key, camera position, look target)
    keys = [
        (0, (SX * 0.15, Y + 95, SZ + 120), (SX * 0.5, Y, mid)),
        (5, (SX * 0.05, Y + 30, SZ + 40), (SX * 0.3, Y, mid)),
        (9, (-10, Y + 10, mid), (60, Y + 8, mid)),
        (14, (SX * 0.45, Y + 10, mid + 2), (SX * 0.6, Y + 6, mid)),
        (18, (pmid - 10, Y + 9, PZ - 22), (pmid, Y + 1, PZ + 4)),
        (24, (pmid + 4, Y + 8, PZ - 18), (pmid, Y + 1, PZ + 4)),
        (28, (tx + 30, ty + 8, tz + 45), (tx, ty - 6, tz - 10)),
        (34, (tx + 8, ty + 4, tz + 28), (tx, ty + 2, tz)),
    ]

    def lever(bit, on):
        return f"setblock {lv[bit]} {Y + 1} {PZ} minecraft:lever[face=floor,facing=south,powered={'true' if on else 'false'}]"

    r = connect()
    r("gamemode spectator bASIC")
    r(f"setblock {m['auto_x']} {Y + 1} {PZ} minecraft:lever[face=floor,facing=south,powered=true]")
    start = {f"ui_in[{k}]": (5 >> k) & 1 for k in range(4)} | {f"ui_in[{k + 4}]": (3 >> k) & 1 for k in range(4)}
    for b in lv:
        if b != "clk":
            r(lever(b, b == "rst_n" or start.get(b, 0)))
    n = tro["n"] if tro else 137
    target = {f"ui_in[{k}]": (n >> k) & 1 for k in range(8)} | {"uio_in[0]": 0}
    flips = [(19 + 0.6 * i, b, v) for i, (b, v) in enumerate(sorted(target.items())) if b in lv]
    r("tp bASIC {} {} {} {} {}".format(*keys[0][1], *look(keys[0][1], keys[0][2])))
    so = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(HERE / "shot.ps1"),
                         str(OUT / "_front.png")], capture_output=True, text=True).stdout.split()
    w, h = map(int, so[1].split("x"))          # window brought to the front; record that part of the desktop
    L, T = int(so[2]), int(so[3])
    w, h = w - 16, h - 68                       # skip the borders and the title bar
    L, T = L + 8, T + 60
    time.sleep(2)
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "gdigrab", "-framerate", "30", "-offset_x", str(L), "-offset_y", str(T),
                           "-video_size", f"{w - w % 2}x{h - h % 2}", "-i", "desktop", "-vf", "scale=1600:-2", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                           "-pix_fmt", "yuv420p", str(out)], stdin=subprocess.PIPE)
    time.sleep(1.0)
    T = keys[-1][0] + 2
    t0 = time.time()
    while True:
        t = time.time() - t0
        if t > T:
            break
        i = max(k for k in range(len(keys)) if keys[k][0] <= min(t, keys[-1][0]))
        j = min(i + 1, len(keys) - 1)
        u = 0 if i == j else (t - keys[i][0]) / (keys[j][0] - keys[i][0])
        u = u * u * (3 - 2 * u) if i in (0, len(keys) - 2) else u
        P = lambda k: keys[max(0, min(len(keys) - 1, k))]
        pos = cr(P(i - 1)[1], P(i)[1], P(j)[1], P(j + 1)[1], u)
        tgt = cr(P(i - 1)[2], P(i)[2], P(j)[2], P(j + 1)[2], u)
        yaw, pitch = look(pos, tgt)
        r(f"tp bASIC {pos[0]:.2f} {pos[1]:.2f} {pos[2]:.2f} {yaw:.1f} {pitch:.1f}")
        while flips and flips[0][0] <= t:
            _, b, v = flips.pop(0)
            r(lever(b, v))
        time.sleep(max(0, 0.05 - (time.time() - t0 - t)))
    ff.communicate(b"q")
    r.close()
    print("wrote", out)


if __name__ == "__main__":
    main()
