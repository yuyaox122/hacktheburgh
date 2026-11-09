"""The headless vanilla 1.21.11 server both halves use.

Mojang's server jar goes in minecraft/tools/server-1.21.11.jar (not committed; running it means accepting the
Minecraft EULA). Each server keeps its world in minecraft/server/<name>/. Both folders are gitignored.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .rcon import Rcon

MC_DIR = Path(__file__).resolve().parents[1]          # minecraft/
TOOLS = MC_DIR / "tools"
JAR = TOOLS / "server-1.21.11.jar"
SERVERS = MC_DIR / "server"


def java() -> str:
    """JDK 21: one unpacked into minecraft/tools/ (Windows), Homebrew's openjdk@21 (macOS), else java on PATH."""
    for p in TOOLS.glob("jdk-21*/bin/java.exe"):
        return str(p)
    for p in ("/opt/homebrew/opt/openjdk@21/bin/java", "/usr/local/opt/openjdk@21/bin/java"):
        if Path(p).exists():
            return p
    return shutil.which("java") or "java"


@dataclass
class Server:
    """One server folder, its server.properties and its RCON address."""
    dir: Path
    props: dict[str, str]          # server.properties, written as given (include the rcon.* keys)
    port: int                      # RCON port
    password: str
    host: str = "127.0.0.1"
    game_port: int = 25565
    xmx: str = "4G"

    def setup(self) -> None:
        """Create the folder, accept the EULA and write server.properties."""
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "eula.txt").write_text("eula=true\n")   # Mojang EULA: https://aka.ms/MinecraftEULA
        (self.dir / "server.properties").write_text("".join(f"{k}={v}\n" for k, v in self.props.items()))

    def up(self) -> bool:
        try:
            socket.create_connection((self.host, self.port), timeout=1).close()
            return True
        except OSError:
            return False

    def start(self, timeout: float = 180) -> None:
        """Start in the background (log in <dir>/server.log) and wait until RCON answers."""
        if self.up():
            print("already running"); return
        if not (self.dir / "server.properties").exists():
            self.setup()
            print("ready:", self.dir)
        log = open(self.dir / "server.log", "ab")
        subprocess.Popen([java(), f"-Xmx{self.xmx}", "-jar", str(JAR), "nogui"], cwd=self.dir, stdout=log, stderr=log,
                         stdin=subprocess.DEVNULL, start_new_session=True)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.up():
                print(f"up after {time.time() - t0:.0f}s"); return
            time.sleep(1)
        raise SystemExit(f"server did not come up; see {self.dir / 'server.log'}")

    def run(self) -> None:
        """Run in the foreground (blocks until the server stops)."""
        os.chdir(self.dir)
        subprocess.run([java(), f"-Xmx{self.xmx}", "-jar", str(JAR), "nogui"])

    def rcon(self, timeout: float = 60) -> Rcon:
        """An RCON connection to this server (not yet connected: use `with` or .connect())."""
        return Rcon(self.host, self.port, self.password, timeout)

    @property
    def datapacks(self) -> Path:
        return self.dir / "world" / "datapacks"
