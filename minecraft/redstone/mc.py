"""The two redstone servers (flat void worlds) and an RCON client with a deterministic tick stepper.

    python -m minecraft.redstone.mc setup        # minecraft/server/check/: void world, RCON on
    python -m minecraft.redstone.mc start        # start it in the background (log in server.log), wait for RCON
    python -m minecraft.redstone.mc cmd "<command>" ...
    python -m minecraft.redstone.mc stop

    CHECK  minecraft/server/check/  game 25566, RCON 25576: check.py freezes its ticks and steps them
    DEMO   minecraft/server/demo/   game 25565, RCON 25577: the playable world (demo.py, bridge.py)
Environment overrides (apply to both): REDSTONE_SERVER_DIR (folder under minecraft/server/, or absolute),
REDSTONE_RCON_HOST (another machine's server), REDSTONE_RCON_PORT, REDSTONE_GAME_PORT.

Library use (see check.py):
    with MC() as mc:                # MC(DEMO) for the demo server
        mc.place(blocks)            # [(x, y, z, "block[state]")] via /setblock (with block updates)
        mc.step(n)                  # advance exactly n game ticks while the world is frozen
        mc.test(x, y, z, "repeater[powered=true]") -> bool
"""
from __future__ import annotations

import os
import sys
import time

from ..common.rcon import Rcon
from ..common.server import SERVERS, Server

PASSWORD = "redstone"
FLOOR_Y = -61                     # top block of the flat floor; builds start at FLOOR_Y + 1 = -60


def _server(folder: str, rcon_port: int, game_port: int) -> Server:
    port = int(os.environ.get("REDSTONE_RCON_PORT", rcon_port))
    game = int(os.environ.get("REDSTONE_GAME_PORT", game_port))
    props = {
        "level-type": "minecraft\\:flat",
        "generator-settings": '{"layers":[{"block":"minecraft:bedrock","height":1},{"block":"minecraft:stone","height":3}],'
                              '"biome":"minecraft:the_void"}',
        "gamemode": "creative", "difficulty": "peaceful", "spawn-monsters": "false", "online-mode": "false",
        "enable-rcon": "true", "rcon.port": str(port), "rcon.password": PASSWORD, "spawn-protection": "0",
        "server-port": str(game), "generate-structures": "false", "max-tick-time": "-1", "view-distance": "10",
        "simulation-distance": "10", "level-name": "world", "motd": "redstone chip", "enable-command-block": "true",
        "pause-when-empty-seconds": "0", "function-permission-level": "4", "sync-chunk-writes": "false",
    }
    return Server(dir=SERVERS / os.environ.get("REDSTONE_SERVER_DIR", folder), props=props, port=port,
                  password=PASSWORD, host=os.environ.get("REDSTONE_RCON_HOST", "127.0.0.1"), game_port=game, xmx="4G")


CHECK = _server("check", 25576, 25566)
DEMO = _server("demo", 25577, 25565)


class MC(Rcon):
    """One persistent RCON connection to a redstone server (CHECK by default), with world helpers."""

    def __init__(self, server: Server = CHECK):
        super().__init__(server.host, server.port, server.password, timeout=60)

    # ---- world helpers ----
    def prepare(self) -> None:
        # 1.21.11 game rule names (snake_case since 1.21.11)
        for r in self.cmds(["gamerule max_block_modifications 10000000", "gamerule advance_time false",
                            "gamerule advance_weather false", "gamerule block_drops false",
                            "gamerule random_tick_speed 0", "gamerule max_command_sequence_length 10000000",
                            "tick freeze"]):
            if "Incorrect" in r or "Unknown" in r:
                raise RuntimeError(r)

    def forceload(self, x0: int, z0: int, x1: int, z1: int) -> None:
        cx0, cz0, cx1, cz1 = x0 >> 4, z0 >> 4, x1 >> 4, z1 >> 4
        cs = []
        for cx in range(cx0, cx1 + 1, 16):
            for cz in range(cz0, cz1 + 1, 16):
                cs.append(f"forceload add {cx * 16} {cz * 16} {min(cx + 15, cx1) * 16} {min(cz + 15, cz1) * 16}")
        for r in self.cmds(cs):
            if "rror" in r or "too many" in r.lower():
                raise RuntimeError(r)

    def clear(self, x0, y0, z0, x1, y1, z1) -> None:
        cs = []
        for x in range(x0, x1 + 1, 32):
            for z in range(z0, z1 + 1, 32):
                cs.append(f"fill {x} {y0} {z} {min(x + 31, x1)} {y1} {min(z + 31, z1)} air strict")
        self.cmds(cs)

    def place(self, blocks) -> None:
        """blocks: iterable of (x, y, z, state). Supports first, then components (so attachments survive)."""
        rs = self.cmds([f"setblock {x} {y} {z} {s}" for x, y, z, s in blocks])
        bad = [(b, r) for b, r in zip(blocks, rs) if r and not r.startswith("Changed the block") and "Could not set" not in r]
        if bad:
            raise RuntimeError(f"setblock failed: {bad[:3]}")

    def gametime(self) -> int:
        r = self.cmd("time query gametime")
        return int(r.rsplit(" ", 1)[-1])

    def step(self, n: int, timeout: float = 120) -> None:
        """Advance exactly n game ticks (world stays frozen otherwise)."""
        if n <= 0:
            return
        g0 = self.gametime()
        r = self.cmd(f"tick step {n}")
        if "rror" in r or "not" in r.lower() and "frozen" in r.lower():
            raise RuntimeError(r)
        t0 = time.time()
        while self.gametime() < g0 + n:
            if time.time() - t0 > timeout:
                raise TimeoutError(f"tick step {n} stalled")
            time.sleep(0.01)

    def test(self, x: int, y: int, z: int, state: str) -> bool:
        r = self.cmd(f"execute if block {x} {y} {z} {state}")
        return r.startswith("Test passed")

    def tests(self, probes) -> list[bool]:
        rs = self.cmds([f"execute if block {x} {y} {z} {s}" for x, y, z, s in probes])
        return [r.startswith("Test passed") for r in rs]


def main() -> None:
    a = sys.argv[1:]
    if not a:
        print(__doc__); return
    if a[0] == "setup":
        CHECK.setup()
        print("ready:", CHECK.dir)
    elif a[0] == "start":
        CHECK.start()
    elif a[0] == "stop":
        with MC() as mc:
            print(mc.cmd("stop"))
    elif a[0] == "cmd":
        with MC() as mc:
            for c in a[1:]:
                print(mc.cmd(c))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
