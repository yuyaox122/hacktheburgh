"""One chip end to end: metadata, decompile + proof, the author's source (answer key), and the result JSON."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

from . import lib, netlist, pipeline

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "out"


def chip_dir(chip: str) -> Path:
    return DATA / "tt08" / chip


def netlist_path(chip: str) -> Path:
    if chip == "warmup":
        return DATA / "warmup" / "01_netlist.v"
    return chip_dir(chip) / "netlist.v"


def info(chip: str) -> dict:
    if chip == "warmup":
        return {"title": "Jane Street ASIC puzzle warm-up", "author": "Jane Street (asic-puzzle-2026)", "language": "Verilog",
                "description": "Shift registers, an adder and a comparator."}
    f = chip_dir(chip) / "info.yaml"
    out = {"title": chip, "author": "", "language": "", "description": ""}
    if f.exists():
        t = f.read_text(errors="replace")
        for k in out:
            m = re.search(rf"^\s*{k}:\s*\"?([^\"\n#]*)\"?", t, re.M)
            if m and m.group(1).strip():
                out[k] = m.group(1).strip()
    return out


def code_lines(text: str) -> int:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return sum(1 for l in text.splitlines() if l.split("//")[0].strip())


def author_source(chip: str) -> dict:
    if chip == "warmup":
        f = DATA / "warmup" / "00_source.v"
        files = {"00_source.v": f.read_text()} if f.exists() else {}
        return {"repo": "janestreet/asic-puzzle-2026", "commit": "", "url": "https://github.com/janestreet/asic-puzzle-2026/tree/main/warmup",
                "files": files, "lines": sum(code_lines(t) for t in files.values())}
    d = chip_dir(chip)
    meta = json.loads((d / "commit_id.json").read_text()) if (d / "commit_id.json").exists() else {}
    m = re.match(r"https://github.com/([^/]+/[^/]+?)(?:\.git)?/?$", meta.get("repo", ""))
    repo = m.group(1) if m else ""
    files = {}
    for f in sorted((d / "src").glob("*")) if (d / "src").exists() else []:
        if re.search(r"(^tb|_tb\.|test|formal|testbench)", f.name, re.I):
            continue
        if f.suffix in (".v", ".sv", ".vh", ".svh"):
            files[f.name] = f.read_text(errors="replace")
    return {"repo": repo, "commit": meta.get("commit", ""),
            "url": f"https://github.com/{repo}/tree/{meta.get('commit', '')}/src" if repo else "",
            "files": files, "lines": sum(code_lines(t) for t in files.values())}


def author_check(chip: str, src: dict, top: str, timeout: int = 90) -> tuple[str, str]:
    """Is the shipped netlist the author's source? Equivalence first; else a bounded check from reset."""
    files = {k: v for k, v in src.get("files", {}).items() if k.endswith((".v", ".sv"))}
    if not files:
        return "unavailable", "no author source"
    nl_text = netlist_path(chip).read_text(errors="replace")
    types = sorted(set(re.findall(r"\b" + lib.PREFIX + r"([a-z0-9_]+)\b", nl_text)))
    other = sorted(set(re.findall(r"\b(sky130_(?!fd_)[a-z]+_sc_hd__[a-z0-9_]+)\b", nl_text)))
    stubs = "".join(f"module {o}(input VGND, input VNB, input VPB, input VPWR);\nendmodule\n" for o in other)
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td, "src"); sd.mkdir()
        for k, v in files.items():
            (sd / k).write_text(v)
        for k, v in src.get("files", {}).items():
            if not k.endswith((".v", ".sv")):
                (sd / k).write_text(v)
        Path(td, "cells.v").write_text(lib.yosys_models(types) + stubs)
        Path(td, "net.v").write_text(nl_text)
        ports = "input [7:0] ui_in, output [7:0] uo_out, input [7:0] uio_in, output [7:0] uio_out, output [7:0] uio_oe, input ena, input clk, input rst_n"
        conn = ".clk(clk),.ena(ena),.rst_n(rst_n),.ui_in(ui_in),.uio_in(uio_in),.uio_oe(uio_oe),.uio_out(uio_out),.uo_out(uo_out)"
        if "VPWR" in nl_text.split(";", 30)[0] + nl_text[:3000]:
            conn += ",.VPWR(1'b1),.VGND(1'b0)"
        Path(td, "wrap.v").write_text(f"module gate_top({ports}); {top} u({conn}); endmodule\n")
        srcs = " ".join(f"{td}/src/{k}" for k in files)
        head = f"""read_verilog -sv -I {td}/src {srcs}
hierarchy -top {top}
proc; flatten; memory
rename {top} gold
design -stash gold
read_verilog {td}/cells.v {td}/net.v {td}/wrap.v
hierarchy -top gate_top; flatten; rename gate_top gate; design -stash gate
design -copy-from gold -as gold gold
design -copy-from gate -as gate gate
proc; opt_clean; async2sync
"""
        eq = head + "equiv_make gold gate equiv\nhierarchy -top equiv\nequiv_simple -seq 5\nequiv_induct -seq 5\nequiv_status -assert\n"
        bmc = head + ("miter -equiv -flatten -make_outputs gold gate miter\nhierarchy -top miter\n"
                      "sat -verify -prove trigger 0 -set-init-zero -set-at 1 in_rst_n 0 -seq 20 -timeout 60 miter\n")
        Path(td, "eq.ys").write_text(eq)
        Path(td, "bmc.ys").write_text(bmc)
        try:
            r = subprocess.run(["yosys", "-q", f"{td}/eq.ys"], capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0:
                return "proven", "Yosys equivalence (equiv_make / equiv_simple / equiv_induct), author source vs shipped netlist"
            if "ERROR" in r.stderr + r.stdout and "unproven" not in (r.stderr + r.stdout):
                err = [l for l in (r.stderr + r.stdout).splitlines() if "ERROR" in l]
                return "unavailable", ("Yosys could not read the author source: " + (err[0] if err else ""))[:300]
        except subprocess.TimeoutExpired:
            pass
        try:
            r = subprocess.run(["yosys", "-q", f"{td}/bmc.ys"], capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0:
                return "bounded", "No difference in 20 clock cycles from reset (bounded check; not a full proof)"
            if "failed" in (r.stderr + r.stdout).lower():
                return "failed", "Bounded check found an input sequence where they differ"
            return "unavailable", "bounded check did not finish"
        except subprocess.TimeoutExpired:
            return "unavailable", f"timed out after {timeout}s"


def decompile_chip(chip: str, split: str, nl: netlist.Netlist | None = None, author: bool = True) -> tuple[dict, object]:
    t0 = time.time()
    nl = nl or netlist.load(netlist_path(chip))
    res, proof, dec = pipeline.run(nl, return_decompiler=True)
    meta = info(chip)
    src = author_source(chip)
    check, detail = author_check(chip, src, nl.top) if author else ("unavailable", "not run")
    src["check"], src["check_detail"] = check, detail
    result = assemble(chip, split, nl, res, proof, meta, src)
    result["seconds"] = round(time.time() - t0, 2)
    return result, dec


def verdict_of(proof: dict) -> str:
    if proof.get("ok"):
        return "proven"
    if proof.get("tested_cycles"):
        return "tested"
    return "failed"


def assemble(chip, split, nl, res, proof, meta, src, variant="clean") -> dict:
    return {
        "chip": chip, "title": meta.get("title", chip), "author": meta.get("author", ""), "language": meta.get("language", ""),
        "description": meta.get("description", ""),
        "split": split, "variant": variant,
        "stats": {"logic_cells": len(nl.insts), "flops": len(nl.flops), "cell_types": len({i.type for i in nl.insts}),
                  "area_um2": nl.area(), "physical_cells": nl.physical},
        "decompiled": {"verilog": res["verilog"], "lines": code_lines(res["verilog"])},
        "blocks": res["blocks"],
        "coverage": res["coverage"],
        "verdict": verdict_of(proof),
        "proof": {"method": "equiv (equiv_make / equiv_simple / equiv_induct), flip-flops paired 1:1",
                  "points": proof["points"], "proven": proof["proven"], "seconds": proof["seconds"],
                  "control_caught": proof.get("control_caught"), "tested_cycles": proof.get("tested_cycles", 0),
                  "attempts": len(proof.get("attempts", [])),
                  "detail": _proof_detail(proof)},
        "author_source": src,
        "netlist": nl.stripped(),
        "cell_lines": res["cell_lines"],
        "flags": [],
    }


def _proof_detail(p: dict) -> str:
    if p.get("ok"):
        extra = f" after {len(p['attempts']) - 1} rewrite(s) of rejected parts as plain logic" if len(p.get("attempts", [])) > 1 else ""
        return f"Yosys proved all {p['points']} paired signals equal{extra}. A broken control copy was rejected."
    if p.get("error"):
        return f"Proof did not run: {p['error'][:200]}"
    return f"{p['proven']} of {p['points']} paired signals proven; unproven: {', '.join(p.get('unproven', [])[:6])}"


def save(result: dict, name: str | None = None) -> Path:
    (OUT / "chips").mkdir(parents=True, exist_ok=True)
    p = OUT / "chips" / f"{name or result['chip']}.json"
    p.write_text(json.dumps(result, indent=1))
    return p
