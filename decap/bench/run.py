"""Run the benchmark: decompile + prove every chip in the frozen split, check the author's source, and
run the trojan test (one planted copy with a seeded random N, plus the clean copy) on each.

    python -m decap.bench.run                 # dev + test + warm-up
    python -m decap.bench.run --split test    # only the held-out test set
    python -m decap.bench.run --chips tt_um_mac,tt_um_ring_divider

Writes decap/out/chips/<chip>.json (clean result, CONTRACTS.md §1) and decap/out/bench.json (§2).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone

from ..core import chip as C
from ..core import find, netlist, pipeline
from ..paths import BENCH_FIRST_RUN, BENCH_JSON, DECAP, SPLIT


def trojan_n(chip: str) -> int:
    return int(hashlib.sha256(f"decap-trojan:{chip}".encode()).hexdigest(), 16) % 256


def public_flags(fl):
    return [{k: v for k, v in f.items() if not k.startswith("_")} for f in fl]


def caught(fl, info) -> tuple[bool, int | None]:
    ins = set(info["inserted_cells"])
    for f in fl:
        cells = set(f["cells"])
        if cells and len(cells & ins) * 2 >= len(cells) and f"'d{info['n']}" in f["condition"]:
            return True, f["line"]
    return False, None


def run_chip(chip: str, split: str) -> dict:
    t0 = time.time()
    try:
        nl = netlist.load(C.netlist_path(chip))
        result, dec = C.decompile_chip(chip, split, nl)
        fl = find.flags(dec, result)
        result["flags"] = public_flags(fl)
        C.save(result)
        row = {"chip": chip, "title": result["title"], "split": split, "verdict": result["verdict"],
               "logic_cells": result["stats"]["logic_cells"], "flops": result["stats"]["flops"],
               "lines_decompiled": result["decompiled"]["lines"], "lines_author": result["author_source"]["lines"],
               "coverage_pct": result["coverage"]["pct"], "seconds": result["seconds"],
               "author_check": result["author_source"]["check"], "language": result["language"],
               "points": result["proof"]["points"], "attempts": result["proof"]["attempts"]}
        # trojan test
        n = trojan_n(chip)
        planted, info = find.plant(nl, n, res={"registers": result.get("_registers", {})})
        pres, pproof, pdec = pipeline.run(planted, return_decompiler=True)
        pfl = find.flags(pdec, pres)
        ok, line = caught(pfl, info)
        prow = C.assemble(chip, split, planted, pres, pproof, C.info(chip), {"files": {}, "lines": 0, "check": "unavailable",
                                                                             "check_detail": "planted copy"}, variant="planted")
        prow["flags"] = public_flags(pfl)
        prow["plant"] = info
        C.save(prow, f"{chip}__planted")
        row["trojan"] = {"n": info["n"], "target": info["target"], "caught": ok, "flag_line": line,
                         "planted_verdict": C.verdict_of(pproof), "planted_flags": len(pfl), "clean_flags": len(fl)}
        row["seconds_total"] = round(time.time() - t0, 2)
        return row
    except Exception as e:
        return {"chip": chip, "split": split, "verdict": "failed", "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc()[-1500:], "seconds": round(time.time() - t0, 2)}


def summarise(rows, key):
    rs = [r for r in rows if r["split"] == key]
    if not rs:
        return {"n": 0}
    cov = [r["coverage_pct"] for r in rs if "coverage_pct" in r]
    return {"n": len(rs), "proven": sum(r["verdict"] == "proven" for r in rs), "tested": sum(r["verdict"] == "tested" for r in rs),
            "failed": sum(r["verdict"] == "failed" for r in rs), "coverage_pct": round(sum(cov) / len(cov), 1) if cov else 0.0,
            "lines_decompiled": sum(r.get("lines_decompiled", 0) for r in rs), "lines_author": sum(r.get("lines_author", 0) for r in rs),
            "author_proven": sum(r.get("author_check") == "proven" for r in rs)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "test", "all"], default="all")
    ap.add_argument("--chips", default="")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--no-warmup", action="store_true")
    a = ap.parse_args()
    split = json.loads(SPLIT.read_text())
    todo = []
    if a.chips:
        names = a.chips.split(",")
        todo = [(c, "dev" if c in split["dev"] else "test" if c in split["test"] else "warmup" if c == "warmup" else "user") for c in names]
    else:
        if a.split in ("dev", "all"):
            todo += [(c, "dev") for c in split["dev"]]
        if a.split in ("test", "all"):
            todo += [(c, "test") for c in split["test"]]
        if not a.no_warmup and a.split == "all" and C.netlist_path("warmup").exists():
            todo.append(("warmup", "warmup"))
    prev = {}
    if BENCH_JSON.exists():
        prev = {r["chip"]: r for r in json.loads(BENCH_JSON.read_text()).get("chips", [])}
    rows = []
    with ProcessPoolExecutor(a.jobs) as ex:
        for row in ex.map(run_chip, [c for c, _ in todo], [s for _, s in todo]):
            rows.append(row)
            t = row.get("trojan", {})
            print(f"{row['split']:6s} {row['chip']:44s} {row['verdict']:7s} cov {row.get('coverage_pct', 0):5.1f}% "
                  f"lines {row.get('lines_decompiled', '-')}/{row.get('lines_author', '-')} author {row.get('author_check', '-'):11s} "
                  f"trojan N={t.get('n')} caught={t.get('caught')} clean_flags={t.get('clean_flags')} {row.get('error', '')}", flush=True)
    for r in rows:
        prev[r["chip"]] = r
    allrows = sorted(prev.values(), key=lambda r: ({"test": 0, "dev": 1, "warmup": 2}.get(r["split"], 3), r["chip"]))
    tro = [r for r in allrows if r["split"] == "test" and "trojan" in r]
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=DECAP).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "core"], capture_output=True, text=True, cwd=DECAP).stdout.strip()
    bench = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "decompiler_commit": sha + ("+dirty" if dirty else ""),
        "split": split,
        "summary": {"test": summarise(allrows, "test"), "dev": summarise(allrows, "dev"),
                    "trojan": {"planted": len(tro), "caught": sum(r["trojan"]["caught"] for r in tro),
                               "clean": len(tro), "clean_flagged": sum(1 for r in tro if r["trojan"]["clean_flags"]),
                               "clean_flags_total": sum(r["trojan"]["clean_flags"] for r in tro), "k": find.FLAG_K}},
        "chips": allrows,
    }
    if BENCH_FIRST_RUN.exists():
        f = json.loads(BENCH_FIRST_RUN.read_text())
        bench["first_run"] = {"decompiler_commit": f.get("decompiler_commit"), "generated": f.get("generated"),
                              "summary": f["summary"],
                              "note": "The first scored run of the frozen test set. Later runs include fixes made after "
                                      "seeing its failures (derived-clock wires, hierarchical netlists); both are reported."}
    BENCH_JSON.write_text(json.dumps(bench, indent=1))
    print(json.dumps(bench["summary"], indent=1))


if __name__ == "__main__":
    main()
