"""One chip end to end: metadata, decompile + proof, the author's source (answer key), and the result JSON."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

from ..paths import CHIPS_OUT, TT08, WARMUP
from . import lib, netlist, pipeline


def chip_dir(chip: str) -> Path:
    return TT08 / chip


def netlist_path(chip: str) -> Path:
    if chip == "warmup":
        return WARMUP / "01_netlist.v"
    return chip_dir(chip) / "netlist.v"


def info_field(chip: str, key: str) -> str:
    """One field of the chip's info.yaml ('' if absent). No YAML dependency: the fields are flat."""
    f = chip_dir(chip) / "info.yaml"
    m = re.search(rf"^\s*{key}:\s*\"?([^\"\n#]*)\"?", f.read_text(errors="replace"), re.M) if f.exists() else None
    return m.group(1).strip() if m else ""


def info(chip: str) -> dict:
    if chip == "warmup":
        return {"title": "Jane Street ASIC puzzle warm-up", "author": "Jane Street (asic-puzzle-2026)", "language": "Verilog",
                "description": "Shift registers, an adder and a comparator."}
    out = {"title": chip, "author": "", "language": "", "description": ""}
    return {k: info_field(chip, k) or default for k, default in out.items()}


def code_lines(text: str) -> int:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return sum(1 for l in text.splitlines() if l.split("//")[0].strip())


def author_source(chip: str) -> dict:
    if chip == "warmup":
        f = WARMUP / "00_source.v"
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


def author_check(chip: str, src: dict, top: str, timeout: int = 90, nl_text: str | None = None, want_cex: bool = False,
                 first_diff: bool = False, deadline: float | None = None, gate_verilog: str | None = None):
    """Is the shipped netlist the author's source? Equivalence first; else a bounded check from reset.
    Returns (check, detail) or, with want_cex, (check, detail, counterexample inputs or None).
    first_diff: the counterexample is taken at the FIRST clock step where the outputs differ (not the last), with the
    inputs of every step up to it under the private key '_seq' (review_edit uses it to write the explanation).
    deadline: a time.time() by which every Yosys run must be done (each run gets what is left, at most `timeout`).
    gate_verilog: compare against this Verilog module (same name and ports as `top`) instead of the gate netlist."""
    r = _author_check(chip, src, top, timeout, nl_text, first_diff, deadline, gate_verilog)
    return r if want_cex else r[:2]


FF_LATCH = ("$dlatch", "$adlatch", "$dlatchsr", "$sr", "$_DLATCH_", "$_SR_")
SIM_ONLY = [(r"`\s*(ifdef|ifndef|elsif|else|endif|define|undef|include)\b", "a `ifdef/`define/`include directive"),
            (r"\b(translate_off|translate_on|synthesis\s+off|pragma\s+protect)\b", "a translate_off/pragma comment"),
            (r"\binitial\b", "an `initial` block"),
            (r"\$(display|write|monitor|strobe|finish|stop|readmem\w*|fopen|random|urandom\w*|time)\b", "a simulation task ($display, $random, ...)"),
            (r"#\s*\d", "a # delay")]


def _bits_of(spec: str, widths: dict[str, tuple[int, int]]) -> list[tuple[str, int]] | None:
    """The wire bits of an RTLIL left-hand sigspec (`\\a`, `\\a [3]`, `\\a [7:4]`, `{ ... }`); None if it has a constant."""
    out = []
    for m in re.finditer(r"([\\$][^\s{}]+)(?:\s+\[(\d+)(?::(\d+))?\])?|(\d+'[01xzm-]*|-?\d+)", spec):
        if m.group(4):
            return None
        name = m.group(1)
        if m.group(2) is None:
            w, off = widths.get(name, (1, 0))
            out += [(name, off + i) for i in range(w)]
        else:
            hi, lo = int(m.group(2)), int(m.group(3) if m.group(3) is not None else m.group(2))
            out += [(name, i) for i in range(min(hi, lo), max(hi, lo) + 1)]
    return out


def sim_only(verilog: str) -> list[str]:
    """Constructs that make the Verilog simulate (or read) differently from the gates Yosys builds from it."""
    plain = re.sub(r"//[^\n]*|/\*.*?\*/", " ", verilog, flags=re.S)
    return sorted({why for rx, why in SIM_ONLY if re.search(rx, verilog if "translate" in rx else plain)})


def edit_structure(verilog: str, top: str, timeout: float = 30) -> dict:
    """What Yosys's equivalence check can't see, for one Verilog module (read, proc, flatten; then memory):
    {error} if it doesn't read, else {ports: {name: [dir, width]}, clocks: {(clock, edge): flip-flop bits},
    latches, undef (x/z constant bits), problems (Yosys `check` warnings), multi (bits assigned by 2+ `assign`s)}."""
    with tempfile.TemporaryDirectory() as td:
        Path(td, "e.v").write_text(verilog)
        Path(td, "s.ys").write_text(f"read_verilog -sv {td}/e.v\nhierarchy -top {top}\nproc; flatten\n"
                                    f"tee -q -o {td}/check.txt check\nwrite_rtlil {td}/e.il\nmemory\nwrite_json {td}/e.json\n")
        try:
            r = subprocess.run(["yosys", "-q", f"{td}/s.ys"], capture_output=True, text=True, timeout=max(1, timeout))
        except subprocess.TimeoutExpired:
            return {"error": f"timed out after {int(timeout)}s", "timeout": True}
        if r.returncode != 0:
            err = [l for l in (r.stderr + r.stdout).splitlines() if "ERROR" in l]
            return {"error": re.sub(r"/\S*/e\.v", "edited.v", err[0] if err else (r.stderr or r.stdout).strip()[-300:])[:300]}
        mod = json.loads(Path(td, "e.json").read_text())["modules"][top]
        il = Path(td, "e.il").read_text()
        problems = [re.sub(r"/\S*/e\.v", "edited.v", l.strip()) for l in Path(td, "check.txt").read_text().splitlines()
                    if l.startswith("Warning:")]
    ports = {n: [p["direction"], len(p["bits"])] for n, p in mod["ports"].items()}
    port_bit = {b: (n if len(p["bits"]) == 1 else f"{n}[{i}]") for n, p in mod["ports"].items()
                for i, b in enumerate(p["bits"]) if isinstance(b, int)}
    net_bit: dict[int, str] = {}
    for n, nn in sorted(mod["netnames"].items(), key=lambda x: (x[1].get("hide_name", 0), x[0])):
        for i, b in enumerate(nn["bits"]):
            if isinstance(b, int):
                net_bit.setdefault(b, n if len(nn["bits"]) == 1 else f"{n}[{i}]")
    clocks: dict[tuple[str, str], int] = {}
    latches = undef = 0
    for c in mod["cells"].values():
        conn, par = c.get("connections", {}), c.get("parameters", {})
        if "CLK" in conn and ("CLK_POLARITY" in par or c["type"].startswith("$_DFF")):
            b = conn["CLK"][0]
            pol = int(str(par["CLK_POLARITY"]), 2) if "CLK_POLARITY" in par else (0 if "_N" in c["type"] else 1)
            clk = port_bit.get(b) or ((f"internal {net_bit[b]}" if not net_bit.get(b, "$").startswith("$") else "internal (unnamed)")
                                      if isinstance(b, int) else f"constant {b}")
            k = (clk, "posedge" if pol else "negedge")
            clocks[k] = clocks.get(k, 0) + len(conn.get("Q", [b]))
        elif c["type"] in FF_LATCH:
            latches += 1
        undef += sum(1 for bits in conn.values() for x in bits if x in ("x", "z"))
    undef += sum(1 for nn in mod["netnames"].values() for x in nn["bits"] if x in ("x", "z"))
    widths: dict[str, tuple[int, int]] = {}
    for m in re.finditer(r"^  wire ((?:\w+ (?:\d+ )?)*)([\\$]\S+)$", il, re.M):
        w, o = re.search(r"width (\d+)", m.group(1)), re.search(r"offset (\d+)", m.group(1))
        widths[m.group(2)] = (int(w.group(1)) if w else 1, int(o.group(1)) if o else 0)
    seen: dict[tuple[str, int], int] = {}
    for m in re.finditer(r"^  connect (.*)$", il, re.M):   # module-level `assign`s (2 spaces; cell ports have 4)
        line = m.group(1)
        if line.startswith("{"):
            lhs = line[:line.index("}") + 1]
        else:
            lm = re.match(r"[\\$][^\s{}]+(?:\s+\[\d+(?::\d+)?\])?", line)
            lhs = lm.group(0) if lm else ""
        for bit in _bits_of(lhs, widths) or []:
            seen[bit] = seen.get(bit, 0) + 1
    multi = sorted(f"{n.lstrip(chr(92))}[{i}]" for (n, i), k in seen.items() if k > 1)
    return {"ports": ports, "clocks": clocks, "latches": latches, "undef": undef, "problems": problems, "multi": multi}


def _parse_cex(text: str):
    """Input values from `sat -show-inputs` output, at the last time step (where the outputs differ)."""
    rows = re.findall(r"^\s+(\d+)\s+\\in_(\w+)\s+(-?\d+|-+)\s", text, re.M)
    if not rows:
        return None
    last = max(int(t) for t, _, _ in rows)
    out = {"step": last}
    for t, sig, val in rows:
        if int(t) == last and val.lstrip("-").isdigit():
            out[sig] = int(val)
    return out


def _parse_first_diff(text: str):
    """With `-show trigger`: the inputs at the first step where the miter's trigger is 1, plus every step's inputs."""
    steps: dict[int, dict] = {}
    for t, sig, val in re.findall(r"^\s+(\d+)\s+\\(in_\w+|trigger)\s+(-?\d+|-+)\s", text, re.M):
        if val.lstrip("-").isdigit():
            steps.setdefault(int(t), {})[sig.removeprefix("in_")] = int(val)
    hit = [t for t in sorted(steps) if steps[t].get("trigger") == 1]
    if not hit:
        return _parse_cex(text)
    k = hit[0]
    seq = [{x: v for x, v in steps[t].items() if x not in ("trigger", "clk")} for t in sorted(steps) if t <= k]
    return {"step": k, **seq[-1], "_seq": seq}


def _author_check(chip, src, top, timeout, nl_text, first_diff=False, deadline=None, gate_verilog=None):
    files = {k: v for k, v in src.get("files", {}).items() if k.endswith((".v", ".sv"))}
    if not files:
        return "unavailable", "no author source", None
    def left():   # seconds this Yosys run may take: `timeout`, or less when the caller's deadline is near
        return timeout if deadline is None else min(timeout, deadline - time.time())
    nl_text = gate_verilog or nl_text or netlist_path(chip).read_text(errors="replace")
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
        hdr = re.search(r"module\s+\S+\s*\(([^;]*)\)\s*;", nl_text)
        if hdr and re.search(r"\bVPWR\b", hdr.group(1)):
            conn += ",.VPWR(1'b1),.VGND(1'b0)"
        Path(td, "wrap.v").write_text(f"module gate_top({ports}); {top} u({conn}); endmodule\n")
        srcs = " ".join(f"{td}/src/{k}" for k in files)
        if gate_verilog is not None:   # the other side is a Verilog module (e.g. the text before an edit), not gates
            Path(td, "base.v").write_text(gate_verilog)
            gate = f"read_verilog -sv {td}/base.v\nhierarchy -top {top}\nproc; flatten; memory\nrename {top} gate; design -stash gate\n"
        else:
            gate = f"read_verilog {td}/cells.v {td}/net.v {td}/wrap.v\nhierarchy -top gate_top; flatten; rename gate_top gate; design -stash gate\n"
        head = f"""read_verilog -sv -I {td}/src {srcs}
hierarchy -top {top}
proc; flatten; memory
rename {top} gold
design -stash gold
{gate}design -copy-from gold -as gold gold
design -copy-from gate -as gate gate
proc; opt_clean; async2sync
"""
        eq = head + "equiv_make gold gate equiv\nhierarchy -top equiv\nequiv_simple -seq 5\nequiv_induct -seq 5\nequiv_status -assert\n"
        def bmc(seq):
            return head + ("miter -equiv -flatten -make_outputs gold gate miter\nhierarchy -top miter\n"
                           f"sat -verify -prove trigger 0 -set-init-zero -set-at 1 in_rst_n 0 -seq {seq} -timeout 60 -show-inputs"
                           + (" -show trigger" if first_diff else "") + " miter\n")
        Path(td, "eq.ys").write_text(eq)
        Path(td, "bmc.ys").write_text(bmc(20))
        try:
            if left() < 1:
                raise subprocess.TimeoutExpired("yosys", 0)
            r = subprocess.run(["yosys", "-q", f"{td}/eq.ys"], capture_output=True, text=True, timeout=left())
            if r.returncode == 0:
                return "proven", "Yosys equivalence (equiv_make / equiv_simple / equiv_induct), author source vs shipped netlist", None
            if "ERROR" in r.stderr + r.stdout and "unproven" not in (r.stderr + r.stdout):
                err = [l for l in (r.stderr + r.stdout).splitlines() if "ERROR" in l]
                return "unavailable", ("Yosys could not read the author source: " + (err[0] if err else ""))[:300], None
        except subprocess.TimeoutExpired:
            pass
        try:
            if left() < 1:
                return "unavailable", f"ran out of time ({timeout}s budget)", None
            r = subprocess.run(["yosys", f"{td}/bmc.ys"], capture_output=True, text=True, timeout=left())
            if r.returncode == 0:
                return "bounded", "No difference in 20 clock cycles from reset (bounded check; not a full proof)", None
            if re.search(r"proof did fail|failed", r.stderr + r.stdout, re.I):
                cex = _parse_first_diff(r.stdout) if first_diff else _parse_cex(r.stdout)
                if first_diff and cex and cex.get("step", 0) > 1:   # look for a shorter trace: easier to read
                    for n in range(1, min(cex["step"], 6)):
                        if left() < 2:
                            break
                        Path(td, "short.ys").write_text(bmc(n))
                        try:   # optional: a timeout here keeps the difference already found
                            s = subprocess.run(["yosys", f"{td}/short.ys"], capture_output=True, text=True, timeout=left())
                        except subprocess.TimeoutExpired:
                            break
                        if s.returncode != 0 and re.search(r"proof did fail|failed", s.stdout + s.stderr, re.I):
                            cex = _parse_first_diff(s.stdout) or cex
                            break
                return "failed", "Bounded check found an input sequence where the netlist and the author's source differ", cex
            return "unavailable", "bounded check did not finish", None
        except subprocess.TimeoutExpired:
            return "unavailable", f"timed out after {timeout}s", None


def decompile_chip(chip: str, split: str, nl: netlist.Netlist | None = None, author: bool = True) -> tuple[dict, object]:
    t0 = time.time()
    nl = nl or netlist.load(netlist_path(chip))
    res, proof, dec = pipeline.run(nl, return_decompiler=True)
    meta = info(chip)
    src = author_source(chip)
    check, detail = author_check(chip, src, nl.top) if author else ("unavailable", "not run")
    src["check"], src["check_detail"] = check, detail
    result = assemble(chip, split, nl, res, proof, meta, src)
    result["_registers"] = res["registers"]
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
    CHIPS_OUT.mkdir(parents=True, exist_ok=True)
    p = CHIPS_OUT / f"{name or result['chip']}.json"
    p.write_text(json.dumps({k: v for k, v in result.items() if not k.startswith("_")}, indent=1))
    return p
