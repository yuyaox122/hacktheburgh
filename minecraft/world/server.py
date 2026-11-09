"""The world server (a grass field, RCON on 25575) for building and testing the chip world.

    python -m minecraft.world.server setup <chip>      # minecraft/server/world/, superflat world, datapack installed
    python -m minecraft.world.server run               # run the server (blocking; start it in the background)
    python -m minecraft.world.server rcon "<command>"  # run one command
    python -m minecraft.world.server export <name>     # copy the built world into your Minecraft saves as <name>
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from . import OUT
from ..common.datapack import install
from ..common.rcon import Rcon
from ..common.server import SERVERS, Server

PASSWORD = "decap"
PORT = 25575

PROPS = {
    "level-type": "minecraft\\:flat",
    # a grass field (plains sky and grass colour); the chip sits on its own black "wafer" pad
    "generator-settings": '{"layers":[{"block":"minecraft:bedrock","height":1},{"block":"minecraft:stone","height":2},'
                          '{"block":"minecraft:grass_block","height":1}],"biome":"minecraft:plains"}',
    "gamemode": "creative", "difficulty": "peaceful", "spawn-monsters": "false", "online-mode": "false",
    "enable-rcon": "true", "rcon.port": str(PORT), "rcon.password": PASSWORD, "spawn-protection": "0",
    "generate-structures": "false", "max-tick-time": "-1", "view-distance": "16", "level-name": "world",
    "motd": "Decap chip in Minecraft", "enable-command-block": "true", "pause-when-empty-seconds": "0",
    "function-permission-level": "4",   # functions may use /tick sprint (redstone settle/clock_fast)
}
SERVER = Server(dir=SERVERS / "world", props=PROPS, port=PORT, password=PASSWORD, xmx="3G")
SRV = SERVER.dir


def setup(chip: str) -> None:
    SERVER.setup()
    print("ready:", install(OUT / chip / "decap_chip", SRV / "world"))


def connect() -> Rcon:
    """A persistent RCON connection (camera paths send a command every tick)."""
    return SERVER.rcon(timeout=30).connect()


def rcon(*cmds: str) -> list[str]:
    r = connect()
    out = [r(c) for c in cmds]
    r.close()
    return out


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
        SERVER.run()
    elif cmd == "rcon":
        for r in rcon(*sys.argv[2:]):
            print(r)
    elif cmd == "export":
        export(sys.argv[2])
