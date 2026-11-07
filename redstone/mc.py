"""Headless vanilla 1.21.11 server + RCON + a deterministic tick stepper, for checking redstone builds.

    python mc.py setup          # server/ with a flat void world, RCON on, then
    python mc.py start          # start in the background (logs in server/server.log), waits for RCON
    python mc.py cmd "<command>" ...
    python mc.py stop

Library use (see check.py):
    with MC() as mc:
        mc.place(blocks)            # [(x, y, z, "block[state]")] via /setblock (with block updates)
        mc.step(n)                  # advance exactly n game ticks while the world is frozen
        mc.test(x, y, z, "repeater[powered=true]") -> bool
"""
from __future__ import annotations

import os
import shutil
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRV = ROOT / os.environ.get("REDSTONE_SERVER_DIR", "server")
JAR = ROOT / "tools" / "server-1.21.11.jar"
PASSWORD = "redstone"
PORT = int(os.environ.get("REDSTONE_RCON_PORT", "25576"))
GAME_PORT = int(os.environ.get("REDSTONE_GAME_PORT", "25566"))
FLOOR_Y = -61                     # top block of the flat floor; builds start at FLOOR_Y + 1 = -60

PROPS = {
    "level-type": "minecraft\\:flat",
    "generator-settings": '{"layers":[{"block":"minecraft:bedrock","height":1},{"block":"minecraft:stone","height":3}],'
                          '"biome":"minecraft:the_void"}',
    "gamemode": "creative", "difficulty": "peaceful", "spawn-monsters": "false", "online-mode": "false",
    "enable-rcon": "true", "rcon.port": str(PORT), "rcon.password": PASSWORD, "spawn-protection": "0",
    "server-port": str(GAME_PORT), "generate-structures": "false", "max-tick-time": "-1", "view-distance": "10",
    "simulation-distance": "10", "level-name": "world", "motd": "redstone chip", "enable-command-block": "true",
    "pause-when-empty-seconds": "0", "function-permission-level": "4", "sync-chunk-writes": "false",
}


def java() -> str:
    for p in ("/opt/homebrew/opt/openjdk@21/bin/java", "/usr/local/opt/openjdk@21/bin/java"):
        if Path(p).exists():
            return p
    return shutil.which("java") or "java"


def setup() -> None:
    SRV.mkdir(exist_ok=True)
    (SRV / "eula.txt").write_text("eula=true\n")   # Mojang EULA: https://aka.ms/MinecraftEULA
    (SRV / "server.properties").write_text("".join(f"{k}={v}\n" for k, v in PROPS.items()))
    print("ready:", SRV)


def start(timeout: float = 180) -> None:
    if _rcon_up():
        print("already running"); return
    if not (SRV / "server.properties").exists():
        setup()
    log = open(SRV / "server.log", "ab")
    subprocess.Popen([java(), "-Xmx4G", "-jar", str(JAR), "nogui"], cwd=SRV, stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _rcon_up():
            print(f"up after {time.time() - t0:.0f}s"); return
        time.sleep(1)
    raise SystemExit("server did not come up; see server/server.log")


def _rcon_up() -> bool:
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=1).close(); return True
    except OSError:
        return False


def _read(s: socket.socket, n: int) -> bytes:
    b = b""
    while len(b) < n:
        c = s.recv(n - len(b))
        if not c:
            raise ConnectionError("rcon closed")
        b += c
    return b


class MC:
    """One persistent RCON connection."""

    def __init__(self):
        self.s = None
        self.k = 1

    def __enter__(self):
        self.s = socket.create_connection(("127.0.0.1", PORT), timeout=60)
        self._send(3, PASSWORD)
        if self._recv()[0] == -1:
            raise SystemExit("rcon auth failed")
        return self

    def __exit__(self, *a):
        self.s.close()

    def _send(self, typ: int, body: str) -> int:
        self.k += 1
        b = body.encode() + b"\x00\x00"
        self.s.sendall(struct.pack("<iii", len(b) + 8, self.k, typ) + b)
        return self.k

    def _recv(self):
        n = struct.unpack("<i", _read(self.s, 4))[0]
        d = _read(self.s, n)
        return struct.unpack("<ii", d[:8])[0], d[8:-2].decode(errors="replace")

    def cmd(self, c: str) -> str:
        self._send(2, c)
        return self._recv()[1]

    def cmds(self, cs: list[str]) -> list[str]:
        """Run many commands in order."""
        # Vanilla's RCON reads one packet per socket read, so pipelining drops packets: send one at a time.
        return [self.cmd(c) for c in cs]

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
        setup()
    elif a[0] == "start":
        start()
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
