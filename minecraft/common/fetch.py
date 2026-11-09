"""Chip inputs, fetched with `gh` (logged in) into the same cache the decompiler uses (decap/data/, gitignored):

    tt08/<chip>/netlist.v     gate-level netlist   from TinyTapeout/tinytapeout-08 (Apache-2.0 per project LICENSE)
    tt08/<chip>/info.yaml     title, author, pinout
    tt08/<chip>/LICENSE
    gds/<chip>.gds            layout
    pdk/<cell>.json           SKY130 HD per-cell Liberty JSON (tt_025C_1v80), from the Apache-2.0 PDK

Set MINECRAFT_DATA_DIR to use another folder with the same layout.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("MINECRAFT_DATA_DIR") or REPO / "decap" / "data")
TT_REPO = "TinyTapeout/tinytapeout-08"
PDK_REPO = "google/skywater-pdk-libs-sky130_fd_sc_hd"


def gh_raw(repo: str, path: str) -> bytes | None:
    r = subprocess.run(["gh", "api", f"repos/{repo}/contents/{path}", "-H", "Accept: application/vnd.github.raw"],
                       capture_output=True)
    return r.stdout if r.returncode == 0 else None


def chip_dir(chip: str) -> Path:
    return DATA / "tt08" / chip


def netlist_path(chip: str) -> Path:
    return chip_dir(chip) / "netlist.v"


def gds_path(chip: str) -> Path:
    return DATA / "gds" / f"{chip}.gds"


def info_path(chip: str) -> Path:
    return chip_dir(chip) / "info.yaml"


def fetch_chip(chip: str) -> Path:
    """Download whatever is missing of the chip's netlist, GDS, info.yaml and LICENSE. Returns its tt08/ folder."""
    lic = chip_dir(chip) / "LICENSE"
    files = {f"{chip}.v": netlist_path(chip), f"{chip}.gds": gds_path(chip), "info.yaml": info_path(chip), "LICENSE": lic}
    for name, dst in files.items():
        if dst.exists():
            continue
        print("fetching", f"{chip}/{name}", file=sys.stderr)
        b = gh_raw(TT_REPO, f"projects/{chip}/{name}")
        if b is None and name == "LICENSE":
            print(f"WARNING: {chip} has no LICENSE file", file=sys.stderr)
            continue
        if b is None:
            raise SystemExit(f"could not fetch {chip}/{name} (is `gh` logged in?)")
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b)
    text = lic.read_text(errors="replace")[:200] if lic.exists() else ""
    if lic.exists() and "Apache" not in text and "MIT" not in text and "BSD" not in text:
        print(f"WARNING: {chip} licence is not Apache/MIT/BSD: {text[:80]!r}")
    return chip_dir(chip)


def pdk_path(cell_type: str) -> Path:
    return DATA / "pdk" / f"{cell_type}.json"


def fetch_cell(cell_type: str) -> bool:
    """Download one cell's Liberty JSON if it is missing. False when the PDK has no such cell."""
    p = pdk_path(cell_type)
    if p.exists():
        return True
    base = re.sub(r"_\d+$", "", cell_type)
    b = gh_raw(PDK_REPO, f"cells/{base}/sky130_fd_sc_hd__{cell_type}__tt_025C_1v80.lib.json")
    if b is None:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b)
    return True
