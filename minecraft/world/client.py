"""Launch the installed Minecraft 1.21.11 client straight into the local server (offline name "Decap"), for screenshots.

    python -m minecraft.world.client           # joins 127.0.0.1:25565; needs 1.21.11 installed once (official launcher)
    python -m minecraft.world.client <world>   # or opens a world from saves/ (Windows: uses %APPDATA%/.minecraft)
"""
from __future__ import annotations

import json
import os
import sys
import subprocess
from pathlib import Path

from . import OUT
from ..common.server import java

MC = Path(os.environ.get("APPDATA", "")) / ".minecraft"     # Windows
VER = "1.21.11"


def allowed(lib: dict) -> bool:
    ok = True
    for r in lib.get("rules", []):
        match = r.get("os", {}).get("name") in (None, "windows")
        if match:
            ok = r["action"] == "allow"
        elif r["action"] == "allow":
            ok = False
    return ok


def main():
    v = json.loads((MC / "versions" / VER / f"{VER}.json").read_text())
    cp = []
    for lib in v["libraries"]:
        if not allowed(lib):
            continue
        a = lib.get("downloads", {}).get("artifact")
        if a:
            p = MC / "libraries" / a["path"]
            if p.exists():
                cp.append(str(p))
    cp.append(str(MC / "versions" / VER / f"{VER}.jar"))
    args = [java(), "-Xmx4G", "-cp", os.pathsep.join(cp), v["mainClass"],
            "--username", "Decap", "--version", VER, "--gameDir", str(MC), "--assetsDir", str(MC / "assets"),
            "--assetIndex", v["assetIndex"]["id"], "--uuid", "00000000000000000000000000000dec", "--accessToken", "0",
            "--userType", "legacy", "--versionType", "release", "--width", "1600", "--height", "900",
            *(["--quickPlaySingleplayer", sys.argv[1]] if len(sys.argv) > 1   # a world in saves/
              else ["--quickPlayMultiplayer", "127.0.0.1:25565"])]
    subprocess.Popen(args, cwd=MC, stdout=open(OUT / "client.log", "w"), stderr=subprocess.STDOUT)
    print("client starting")


if __name__ == "__main__":
    main()
