"""Headless vanilla 1.21.11 server for building and testing the chip world, plus a tiny RCON client.

    python server.py setup <chip>      # server/ dir, superflat world, datapack installed
    python server.py run               # run the server (blocking; start it in the background)
    python server.py rcon "<command>"  # run one command
    python server.py export <name>     # copy the built world into your Minecraft saves as <name>
"""
from __future__ import annotations

import os
import shutil
import socket
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRV = ROOT / "server"
TOOLS = ROOT / "tools"
JAR = TOOLS / "server-1.21.11.jar"
PASSWORD = "decap"
PORT = 25575

PROPS = {
    "level-type": "minecraft\\:flat",
    "generator-settings": '{"layers":[{"block":"minecraft:bedrock","height":1},{"block":"minecraft:stone","height":2},'
                          '{"block":"minecraft:black_concrete","height":1}],"biome":"minecraft:the_void"}',
    "gamemode": "creative", "difficulty": "peaceful", "spawn-monsters": "false", "online-mode": "false",
    "enable-rcon": "true", "rcon.port": str(PORT), "rcon.password": PASSWORD, "spawn-protection": "0",
    "generate-structures": "false", "max-tick-time": "-1", "view-distance": "16", "level-name": "world",
    "motd": "Decap chip in Minecraft", "enable-command-block": "true", "pause-when-empty-seconds": "0",
}


def java() -> str:
    for p in TOOLS.glob("jdk-21*/bin/java.exe"):
        return str(p)
    return "java"


def setup(chip: str) -> None:
    SRV.mkdir(exist_ok=True)
    (SRV / "eula.txt").write_text("eula=true\n")
    (SRV / "server.properties").write_text("".join(f"{k}={v}\n" for k, v in PROPS.items()))
    dst = SRV / "world" / "datapacks" / "decap_chip"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(ROOT / "out" / chip / "decap_chip", dst)
    print("ready:", dst)


def run() -> None:
    os.chdir(SRV)
    subprocess.run([java(), "-Xmx3G", "-jar", str(JAR), "nogui"])


def rcon(*cmds: str) -> list[str]:
    s = socket.create_connection(("127.0.0.1", PORT), timeout=30)

    def send(i, typ, body):
        b = body.encode() + b"\x00\x00"
        s.sendall(struct.pack("<iii", len(b) + 8, i, typ) + b)

    def recv():
        n = struct.unpack("<i", _read(s, 4))[0]
        d = _read(s, n)
        return struct.unpack("<ii", d[:8])[0], d[8:-2].decode(errors="replace")

    send(1, 3, PASSWORD)
    if recv()[0] == -1:
        raise SystemExit("rcon auth failed")
    out = []
    for k, c in enumerate(cmds, 2):
        send(k, 2, c)
        out.append(recv()[1])
    s.close()
    return out


def _read(s, n):
    b = b""
    while len(b) < n:
        x = s.recv(n - len(b))
        if not x:
            raise ConnectionError("rcon closed")
        b += x
    return b


def export(name: str) -> None:
    rcon("save-all flush")
    saves = Path(os.environ["APPDATA"]) / ".minecraft" / "saves" / name
    if saves.exists():
        raise SystemExit(f"{saves} already exists; pick another name")
    shutil.copytree(SRV / "world", saves, ignore=shutil.ignore_patterns("session.lock"))
    print("exported", saves)


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "setup":
        setup(sys.argv[2])
    elif cmd == "run":
        run()
    elif cmd == "rcon":
        for r in rcon(*sys.argv[2:]):
            print(r)
    elif cmd == "export":
        export(sys.argv[2])
