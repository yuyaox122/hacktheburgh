"""Fetch everything bASIC needs into basic/data/ (gitignored). Needs `gh` logged in.

    python -m basic.ingest.fetch            # candidates from basic/bench/candidates.txt, warm-up, PDK cells
    python -m basic.ingest.fetch --seed DIR # copy already-downloaded netlists/meta from DIR first

Layout written:
    data/tt08/<chip>/netlist.v          gate-level netlist from TinyTapeout/tinytapeout-08
    data/tt08/<chip>/commit_id.json     the author's repo + commit (the answer key)
    data/tt08/<chip>/info.yaml
    data/tt08/<chip>/src/*.v            the author's source at that commit (answer key; never fed to the decompiler)
    data/pdk/<cell>.json                SKY130 HD per-cell Liberty JSON (tt_025C_1v80)
    data/warmup/*                       Jane Street asic-puzzle-2026 warm-up files (no licence: never commit)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..core.lib import PHYSICAL, SYNTH_CELLS, TT_REPO
from ..paths import CANDIDATES, DATA

PDK_REPO = "google/skywater-pdk-libs-sky130_fd_sc_hd"
WARMUP_REPO = "janestreet/asic-puzzle-2026"
WARMUP_FILES = ["00_source.v", "01_netlist.v", "02_netlist_with_power_rails.v", "03_post_place_and_route.def"]


def gh_raw(repo: str, path: str, ref: str | None = None) -> bytes | None:
    url = f"repos/{repo}/contents/{path}" + (f"?ref={ref}" if ref else "")
    r = subprocess.run(["gh", "api", url, "-H", "Accept: application/vnd.github.raw"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def gh_json(url: str):
    r = subprocess.run(["gh", "api", url], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else None


def save(path: Path, data: bytes | None) -> bool:
    if data is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def fetch_chip(chip: str, seed: Path | None) -> str:
    d = DATA / "tt08" / chip
    d.mkdir(parents=True, exist_ok=True)
    if seed:
        for src, dst in [(seed / "net" / f"{chip}.v", d / "netlist.v"), (seed / "meta" / f"{chip}.json", d / "commit_id.json"),
                         (seed / "meta" / f"{chip}.yaml", d / "info.yaml")]:
            if src.exists() and not dst.exists():
                shutil.copy(src, dst)
    if not (d / "netlist.v").exists():
        save(d / "netlist.v", gh_raw(TT_REPO, f"projects/{chip}/{chip}.v"))
    if not (d / "commit_id.json").exists():
        save(d / "commit_id.json", gh_raw(TT_REPO, f"projects/{chip}/commit_id.json"))
    if not (d / "info.yaml").exists():
        save(d / "info.yaml", gh_raw(TT_REPO, f"projects/{chip}/info.yaml"))
    msg = chip
    cid = d / "commit_id.json"
    if cid.exists() and not (d / "src").exists():
        meta = json.loads(cid.read_text())
        m = re.match(r"https://github.com/([^/]+/[^/]+?)(?:\.git)?/?$", meta.get("repo", ""))
        if m:
            repo, sha = m.group(1), meta["commit"]
            listing = gh_json(f"repos/{repo}/contents/src?ref={sha}") or []
            n = 0
            for f in listing:
                if f.get("type") == "file" and re.search(r"\.(v|sv|vh|svh)$", f["name"]):
                    n += save(d / "src" / f["name"], gh_raw(repo, f"src/{f['name']}", sha))
            msg += f" src:{n}"
    return msg


def used_cells(netlists: list[Path]) -> set[str]:
    s: set[str] = set()
    for p in netlists:
        s |= set(re.findall(r"sky130_fd_sc_hd__([a-z0-9_]+)\s", p.read_text(errors="replace")))
    return {c for c in s if not PHYSICAL.match(c)}


def fetch_cell(cell: str, seed_pdk: list[Path]) -> str | None:
    out = DATA / "pdk" / f"{cell}.json"
    if out.exists():
        return None
    for sp in seed_pdk:
        if (sp / f"{cell}.json").exists():
            shutil.copy(sp / f"{cell}.json", out)
            return None
    base = cell.rsplit("_", 1)[0]
    ok = save(out, gh_raw(PDK_REPO, f"cells/{base}/sky130_fd_sc_hd__{cell}__tt_025C_1v80.lib.json"))
    return None if ok else cell


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=Path, help="dir with net/<chip>.v and meta/<chip>.{json,yaml}")
    ap.add_argument("--seed-pdk", type=Path, action="append", default=[], help="dir with <cell>.json Liberty files")
    ap.add_argument("--no-warmup", action="store_true")
    a = ap.parse_args()
    chips = [l.split("#")[0].strip() for l in CANDIDATES.read_text().splitlines()]
    chips = [c for c in chips if c]
    (DATA / "pdk").mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(8) as ex:
        for m in ex.map(lambda c: fetch_chip(c, a.seed), chips):
            print("chip", m, flush=True)
    nets = [DATA / "tt08" / c / "netlist.v" for c in chips if (DATA / "tt08" / c / "netlist.v").exists()]
    if not a.no_warmup:
        for f in WARMUP_FILES:
            p = DATA / "warmup" / f
            if not p.exists() and not save(p, gh_raw(WARMUP_REPO, f"warmup/{f}")):
                print("warm-up missing:", f, file=sys.stderr)
        if (DATA / "warmup" / "01_netlist.v").exists():
            nets.append(DATA / "warmup" / "01_netlist.v")
    cells = sorted(used_cells(nets) | set(SYNTH_CELLS))
    with ThreadPoolExecutor(8) as ex:
        missing = [m for m in ex.map(lambda c: fetch_cell(c, a.seed_pdk), cells) if m]
    print(f"{len(chips)} chips, {len(cells)} cell types" + (f", missing Liberty: {missing}" if missing else ""))


if __name__ == "__main__":
    main()
