"""Verilog -> redstone: synthesise any (small) Verilog design straight to the redstone primitives.

    python verilog.py examples/counter.v [--top counter] [--z 600] [--out DIR]   # -> out/<top>/layout.json
    python check.py vchip examples/counter.v 30                         # build in vanilla, compare with iverilog

Yosys: synth -flatten, flip-flops legalised to rising-edge D flops with optional active-high async reset or set,
then `abc -g OR,ANDNOT` (NOT is implicit). Each flop becomes the same two-phase LATCH pair as the chip path.
One clock only; it is driven by the HOLD_M / HOLD_S phase levers (or the clock button), not by a lever of its own.
The reference is iverilog running the ORIGINAL source, so the check does not trust our synthesis.
"""
from __future__ import annotations

import json
import random
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import techmap as tm

ROOT = Path(__file__).resolve().parent
CLOCK_NAMES = ("clk", "clock", "CLK")


def _bitnames(ports: dict) -> dict:
    names = {}
    for pn, p in ports.items():
        bits = p["bits"]
        for i, b in enumerate(bits):
            names[b] = pn if len(bits) == 1 else f"{pn}[{i}]"
    return names


def synth(path: Path, top: str | None = None) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        js = Path(tmp) / "out.json"
        topflag = f"-top {top}" if top else "-auto-top"
        script = (f"read_verilog -sv {path}; synth -flatten {topflag}; "
                  "dfflegalize -cell $_DFF_P_ 01 -cell $_DFF_PP0_ 01 -cell $_DFF_PP1_ 01; "
                  "abc -g OR,ANDNOT; opt_clean -purge; write_json " + str(js))
        r = subprocess.run(["yosys", "-q", "-p", script], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"yosys failed:\n{r.stderr[-1500:]}")
        mods = json.loads(js.read_text())["modules"]
    tops = [n for n, m in mods.items() if m.get("attributes", {}).get("top")] or list(mods)
    name = top or tops[0]
    return {"name": name, **mods[name]}


def build(path: str | Path, top: str | None = None) -> tm.PrimNetlist:
    m = synth(Path(path), top)
    name = m["name"]
    pname = _bitnames(m["ports"])
    in_bits = [pname[b] for pn, p in m["ports"].items() if p["direction"] == "input" for b in p["bits"]]
    out_ports = {pn: p for pn, p in m["ports"].items() if p["direction"] == "output"}

    def net(b):
        if b == "0" or b == "x" or b == "z":
            return tm.CONST0
        if b == "1":
            return tm.CONST1
        return pname.get(b, f"n{b}")

    prims: list[tm.Prim] = []
    clocks = set()
    k = [0]

    def new(kind, ins, out):
        prims.append(tm.Prim(f"p{len(prims)}", kind, ins, out, "", 0.0))

    def local():
        k[0] += 1
        return f"_v{k[0]}"

    for cname, c in m["cells"].items():
        t, cn = c["type"], c["connections"]
        if t == "$_NOT_":
            new("NOT", {"B": net(cn["A"][0])}, net(cn["Y"][0]))
        elif t == "$_ANDNOT_":
            new("INH", {"A": net(cn["A"][0]), "B": net(cn["B"][0])}, net(cn["Y"][0]))
        elif t == "$_OR_":
            new("OR", {"A": net(cn["A"][0]), "B": net(cn["B"][0])}, net(cn["Y"][0]))
        elif t in ("$_DFF_P_", "$_DFF_PP0_", "$_DFF_PP1_"):
            clocks.add(net(cn["C"][0]))
            D, hm, hs = net(cn["D"][0]), tm.HOLD_M, tm.HOLD_S
            if t != "$_DFF_P_":
                R = net(cn["R"][0])
                d2 = local()
                new("INH" if t == "$_DFF_PP0_" else "OR", {"A": D, "B": R}, d2)
                hm2, hs2 = local(), local()
                new("INH", {"A": tm.HOLD_M, "B": R}, hm2)
                new("INH", {"A": tm.HOLD_S, "B": R}, hs2)
                D, hm, hs = d2, hm2, hs2
            mid = local()
            new("LATCH", {"D": D, "H": hm}, mid)
            new("LATCH", {"D": mid, "H": hs}, net(cn["Q"][0]))
        else:
            raise SystemExit(f"unsupported cell after synthesis: {t} ({cname}); latches/memories need flops")
    if len(clocks) > 1 or (clocks and next(iter(clocks)) not in in_bits):
        raise SystemExit(f"one clock from an input pin is supported; found {sorted(clocks)}")
    clock = next(iter(clocks)) if clocks else None
    outputs = {}
    for pn, p in out_ports.items():
        for b in p["bits"]:
            outputs[pname[b]] = net(b) if not isinstance(b, int) else _out_net(b, pname, m)
    # an output bit's net is the bit itself when a cell drives it; named nets were mapped through pname already
    alias = {}
    prims = tm._propagate(prims, alias)

    def root(n):
        while n in alias:
            n = alias[n]
        return n

    for p in prims:
        p.ins = {q: root(n) for q, n in p.ins.items()}
    outputs = {b: root(n) for b, n in outputs.items()}
    seq = clock is not None
    used = {n for p in prims for n in p.ins.values()} | set(outputs.values())
    inputs = [b for b in in_bits if b != clock and b in used]
    if seq:
        inputs += [tm.HOLD_M, tm.HOLD_S]
    _order(prims, inputs, outputs)
    pin_x = {b: 0.0 for b in outputs}
    top_x = max([p.x for p in prims] + [0.0]) + 1
    for i, b in enumerate(inputs):
        pin_x[b] = top_x + i * 0.01
    for i, b in enumerate(outputs):
        pin_x[b] = -1 + i * 0.001
    pn = tm.PrimNetlist(name, prims, inputs, outputs, pin_x, seq,
                        {"prims": len(prims), "by_kind": {k_: sum(p.kind == k_ for p in prims)
                                                          for k_ in ("NOT", "INH", "OR", "LATCH")},
                         "clock": clock})
    tm.prune(pn)
    return pn


def _out_net(b, pname, m):
    return pname[b]


def _order(prims, inputs, outputs):
    """No layout exists for plain Verilog: order gates by logic level (inputs at the right, outputs at the left,
    like a Tiny Tapeout tile), then by the mean position of their drivers, to keep wires short."""
    by_out = {p.out: p for p in prims}
    level = {}

    def lv(n, depth=0):
        if n not in by_out or depth > 10000:
            return 0
        p = by_out[n]
        if p.id in level:
            return level[p.id]
        level[p.id] = 0                                  # breaks loops through latches
        if p.kind == "LATCH":
            v = 1 + lv(p.ins["D"], depth + 1) if False else 1
        else:
            v = 1 + max([lv(x, depth + 1) for x in p.ins.values()] + [0])
        level[p.id] = v
        return v

    for p in prims:
        lv(p.out)
    top = max(level.values() or [0]) + 1
    pos = {}
    for p in sorted(prims, key=lambda p: level[p.id]):
        srcs = [pos[x] for x in p.ins.values() if x in pos]
        pos[p.out] = top - level[p.id] + (sum(srcs) / len(srcs) / 1000 if srcs else 0)
        p.x = pos[p.out]


class IverilogRef:
    """Reference outputs from iverilog running the original source with a generated testbench."""

    def __init__(self, path: str | Path, top: str, in_bits: list[str], out_bits: list[str], clock: str | None):
        self.path, self.top, self.in_bits, self.out_bits, self.clock = Path(path), top, in_bits, out_bits, clock

    def run(self, vectors: list[dict]) -> list[dict]:
        def ref(b):
            return b.replace("[", "[").replace("]", "]")
        ports = {}
        for b in self.in_bits + self.out_bits + ([self.clock] if self.clock else []):
            n = b.split("[")[0]
            i = int(b.split("[")[1][:-1]) if "[" in b else 0
            ports[n] = max(ports.get(n, 0), i + 1)
        decl = []
        ins = {b.split("[")[0] for b in self.in_bits} | ({self.clock} if self.clock else set())
        for n, w in ports.items():
            kind = "reg" if n in ins else "wire"
            decl.append(f"  {kind} [{w - 1}:0] {n};" if w > 1 or "[" in "".join(
                b for b in self.in_bits + self.out_bits if b.startswith(n + "[")) else f"  {kind} {n};")
        conn = ", ".join(f".{n}({n})" for n in ports)
        body = []
        if self.clock:
            body.append(f"    {self.clock} = 0;")
        for v in vectors:
            for b in self.in_bits:
                body.append(f"    {ref(b)} = 1'b{int(v.get(b, 0))};")
            body.append("    #5;")
            if self.clock:
                body.append(f"    {self.clock} = 1; #5; {self.clock} = 0;")
            body.append("    #5; $display(\"%b\", {" + ", ".join(ref(b) for b in self.out_bits) + "});")
        tb = "`timescale 1ns/1ps\nmodule tb;\n" + "\n".join(decl) + f"\n  {self.top} dut({conn});\n" \
             "  initial begin\n" + "\n".join(body) + "\n    $finish;\n  end\nendmodule\n"
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "tb.v").write_text(tb)
            exe = Path(tmp) / "sim"
            r = subprocess.run(["iverilog", "-g2012", "-o", str(exe), str(self.path), str(Path(tmp) / "tb.v")],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise SystemExit(f"iverilog failed:\n{r.stderr[-1500:]}")
            out = subprocess.run(["vvp", "-n", str(exe)], capture_output=True, text=True).stdout.split()
        lines = [l for l in out if re.fullmatch(r"[01xz]+", l)]
        res = []
        for l in lines:
            res.append({b: int(c == "1") for b, c in zip(self.out_bits, l)})
        return res


def reset_info(in_bits):
    for b in in_bits:
        if b in ("rst_n", "reset_n", "resetn", "rstn"):
            return b, 0
    for b in in_bits:
        if b in ("rst", "reset"):
            return b, 1
    return None, None


def vectors(in_bits, n, seed=7):
    rng = random.Random(seed)
    rb, active = reset_info(in_bits)
    vs = []
    for i in range(n):
        v = {b: rng.randint(0, 1) for b in in_bits}
        if rb:
            on = i < 2 or rng.random() < 0.04
            v[rb] = active if on else 1 - active
        for e in ("ena", "en", "enable"):
            if e in v:
                v[e] = int(rng.random() < 0.8)
        vs.append(v)
    return vs


if __name__ == "__main__":
    import pnr
    src = sys.argv[1]
    top = sys.argv[sys.argv.index("--top") + 1] if "--top" in sys.argv else None
    z = int(sys.argv[sys.argv.index("--z") + 1]) if "--z" in sys.argv else 200
    pn = build(src, top)
    print(pn.chip, pn.stats)
    lay = pnr.route(pn, z_min=z)
    lay.chip = pn.chip
    print(lay.stats)
    # --out <dir>: Decap's decompiled modules keep the chip's name, so the default out/<module>/ would overwrite
    # that chip's 2-D layout; refuse that unless --out says where to go.
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else ROOT / "out" / pn.chip
    if "--out" not in sys.argv and (out / "layout.json").exists() and not (out / "verilog.json").exists():
        raise SystemExit(f"{out} holds a chip layout (not from Verilog); pass --out <dir> to write somewhere else")
    pnr.save(lay, out)
    meta = {"source": str(Path(src).resolve()), "top": pn.chip, "clock": pn.stats.get("clock"),
            "inputs": [b for b in pn.inputs if b not in (tm.HOLD_M, tm.HOLD_S)], "outputs": list(pn.outputs)}
    (out / "verilog.json").write_text(json.dumps(meta))
