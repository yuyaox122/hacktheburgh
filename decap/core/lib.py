"""SKY130 HD standard cells from per-cell Liberty JSON: functions, flip-flops, areas, Yosys models."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache

from ..paths import PDK

PREFIX = "sky130_fd_sc_hd__"
PHYSICAL = re.compile(r"^(fill|decap|tapvpwrvgnd|tap|diode|fakediode)_")
PG = ["VGND", "VNB", "VPB", "VPWR"]
TT_REPO = "TinyTapeout/tinytapeout-08"   # the chips' netlists, info.yaml and GDS
# Cells for planting triggers (find.plant) and for synthesising imported designs (compare.synthesise): always fetched.
# The order is the order of the Liberty file Yosys maps against.
SYNTH_CELLS = ["inv_1", "buf_1", "and2_1", "and3_1", "and4_1", "or2_1", "or3_1", "or4_1", "nand2_1", "nand3_1", "nor2_1",
               "nor3_1", "xor2_1", "xnor2_1", "mux2_1", "a21oi_1", "a21o_1", "o21ai_0", "o21a_1", "a22o_1", "o22a_1",
               "and2b_1", "or2b_1", "nand2b_1", "nor2b_1", "a211oi_1", "o211ai_1", "dfrtp_1", "dfxtp_1", "dfstp_2", "conb_1"]

# ---------- boolean expressions: ("var", name) | ("const", 0/1) | ("not", e) | ("and", [e…]) | ("or", [e…]) | ("xor", [e…]) | ("mux", s, a1, a0)


def parse_fn(s: str):
    toks = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[01]|[!&|^()']", s)
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def take():
        nonlocal pos
        pos += 1
        return toks[pos - 1]

    def p_or():
        xs = [p_xor()]
        while peek() == "|":
            take(); xs.append(p_xor())
        return xs[0] if len(xs) == 1 else ("or", xs)

    def p_xor():
        xs = [p_and()]
        while peek() == "^":
            take(); xs.append(p_and())
        return xs[0] if len(xs) == 1 else ("xor", xs)

    def p_and():
        xs = [p_un()]
        while peek() == "&" or (peek() not in (None, "|", "^", ")", "&")):
            if peek() == "&":
                take()
            xs.append(p_un())
        return xs[0] if len(xs) == 1 else ("and", xs)

    def p_un():
        if peek() == "!":
            take(); return ("not", p_un())
        t = take()
        if t == "(":
            e = p_or(); take(); return e
        if t in ("0", "1"):
            return ("const", int(t))
        e = ("var", t)
        if peek() == "'":
            take(); e = ("not", e)
        return e

    return p_or()


def ev(e, env: dict, mask: int) -> int:
    k = e[0]
    if k == "var":
        return env[e[1]]
    if k == "const":
        return mask if e[1] else 0
    if k == "not":
        return ~ev(e[1], env, mask) & mask
    if k == "and":
        r = mask
        for x in e[1]:
            r &= ev(x, env, mask)
        return r
    if k == "or":
        r = 0
        for x in e[1]:
            r |= ev(x, env, mask)
        return r
    if k == "xor":
        r = 0
        for x in e[1]:
            r ^= ev(x, env, mask)
        return r
    if k == "mux":
        s = ev(e[1], env, mask)
        return (s & ev(e[2], env, mask)) | (~s & mask & ev(e[3], env, mask))
    raise ValueError(e)


# Friendlier forms than Liberty's sum-of-products, for code people read.
FRIENDLY = {
    "xor2": "A ^ B", "xnor2": "~(A ^ B)", "xor3": "A ^ B ^ C", "xnor3": "~(A ^ B ^ C)",
    "mux2": "S ? A1 : A0", "mux2i": "~(S ? A1 : A0)",
    "mux4": "S1 ? (S0 ? A3 : A2) : (S0 ? A1 : A0)",
    "nand2": "~(A & B)", "nand3": "~(A & B & C)", "nand4": "~(A & B & C & D)",
    "nor2": "~(A | B)", "nor3": "~(A | B | C)", "nor4": "~(A | B | C | D)",
    "a21oi": "~((A1 & A2) | B1)", "a22oi": "~((A1 & A2) | (B1 & B2))", "a211oi": "~((A1 & A2) | B1 | C1)",
    "a31oi": "~((A1 & A2 & A3) | B1)", "a21boi": "~((A1 & A2) | ~B1_N)", "a221oi": "~((A1 & A2) | (B1 & B2) | C1)",
    "o21ai": "~((A1 | A2) & B1)", "o22ai": "~((A1 | A2) & (B1 | B2))", "o211ai": "~((A1 | A2) & B1 & C1)",
    "o31ai": "~((A1 | A2 | A3) & B1)", "o21bai": "~((A1 | A2) & ~B1_N)", "o2111ai": "~((A1 | A2) & B1 & C1 & D1)",
    "o41ai": "~((A1 | A2 | A3 | A4) & B1)", "o2bb2ai": "~((~A1_N | ~A2_N) & (B1 | B2))",
    "o21a": "(A1 | A2) & B1", "o22a": "(A1 | A2) & (B1 | B2)", "o211a": "(A1 | A2) & B1 & C1", "o31a": "(A1 | A2 | A3) & B1",
    "o21ba": "(A1 | A2) & ~B1_N", "o221a": "(A1 | A2) & (B1 | B2) & C1", "o2111a": "(A1 | A2) & B1 & C1 & D1",
    "o311a": "(A1 | A2 | A3) & B1 & C1", "o32a": "(A1 | A2 | A3) & (B1 | B2)", "o41a": "(A1 | A2 | A3 | A4) & B1",
    "o2bb2a": "(~A1_N | ~A2_N) & (B1 | B2)",
    "a21o": "(A1 & A2) | B1", "a22o": "(A1 & A2) | (B1 & B2)", "a211o": "(A1 & A2) | B1 | C1", "a31o": "(A1 & A2 & A3) | B1",
    "a21bo": "(A1 & A2) | ~B1_N", "a221o": "(A1 & A2) | (B1 & B2) | C1", "a2111o": "(A1 & A2) | B1 | C1 | D1",
    "a311o": "(A1 & A2 & A3) | B1 | C1", "a32o": "(A1 & A2 & A3) | (B1 & B2)", "a41o": "(A1 & A2 & A3 & A4) | B1",
    "a2bb2o": "(~A1_N & ~A2_N) | (B1 & B2)",
}

PLAIN = {
    "inv": "Inverter: the output is the opposite of the input.", "clkinv": "Clock inverter.",
    "buf": "Buffer: copies its input, with more drive strength.", "clkbuf": "Clock buffer: a strong copy of the clock.",
    "dlygate4sd3": "Delay buffer: copies its input a little later (fixes hold timing).",
    "dlymetal6s2s": "Delay buffer: copies its input a little later.",
    "conb": "Constant: ties a wire to 1 (HI) or 0 (LO).",
    "dfrtp": "Flip-flop: stores one bit, updates on the rising clock edge; RESET_B = 0 forces it to 0.",
    "dfstp": "Flip-flop: stores one bit, updates on the rising clock edge; SET_B = 0 forces it to 1.",
    "dfxtp": "Flip-flop: stores one bit, updates on the rising clock edge; no reset.",
    "mux2": "2-to-1 selector: S picks A1 (S = 1) or A0 (S = 0).",
    "xor2": "XOR: 1 when the two inputs differ.", "xnor2": "XNOR: 1 when the two inputs match.",
}


@dataclass
class Cell:
    name: str                      # e.g. "xor2_1"
    base: str                      # e.g. "xor2"
    inputs: list[str]
    outputs: dict[str, object]     # pin -> parsed function (combinational) or ("var","IQ")
    area: float
    ff: dict | None = None         # {"clk","d","clear","preset","q_state"} for flip-flops
    physical: bool = False
    fn_text: dict[str, str] = field(default_factory=dict)

    @property
    def seq(self) -> bool:
        return self.ff is not None

    def friendly(self, pin: str) -> str:
        if self.base in FRIENDLY and len(self.outputs) == 1:
            return FRIENDLY[self.base]
        t = self.fn_text.get(pin, "")
        return t.replace("!", "~")

    def plain(self) -> str:
        if self.base in PLAIN:
            return PLAIN[self.base]
        if self.physical:
            return "Physical cell (filler, tap, decoupling capacitor or diode). No logic."
        return " / ".join(f"{p} = {self.friendly(p)}" for p in self.outputs)


@lru_cache(maxsize=None)
def cell(name: str) -> Cell:
    base = name.rsplit("_", 1)[0]
    f = PDK / f"{name}.json"
    if PHYSICAL.match(name) or not f.exists():
        if not PHYSICAL.match(name):
            raise FileNotFoundError(f"no Liberty JSON for {name} (run python -m decap.ingest.fetch)")
        return Cell(name, base, [], {}, 0.0, physical=True)
    d = json.loads(f.read_text())
    pins = {k[4:]: v for k, v in d.items() if k.startswith("pin,")}
    ins = [p for p, v in pins.items() if v.get("direction") == "input"]
    outs = {p: v.get("function") for p, v in pins.items() if v.get("direction") == "output"}
    ffk = [k for k in d if k.startswith("ff,")]
    ff = None
    if ffk:
        g = d[ffk[0]]
        ff = {"clk": g["clocked_on"], "d": g["next_state"], "clear": g.get("clear"), "preset": g.get("preset"),
              "q_state": ffk[0].split(",")[1]}
    return Cell(name, base, ins, {p: parse_fn(fn) for p, fn in outs.items()}, float(d.get("area", 0.0)), ff,
                fn_text={p: fn for p, fn in outs.items()})


def yosys_models(types: list[str]) -> str:
    """Functional Verilog models Yosys can read: one module per cell type, power pins as plain inputs."""
    out = []
    for t in sorted(set(types)):
        c = cell(t)
        if c.physical:
            out.append(f"module {PREFIX}{t}(input VGND, input VNB, input VPB, input VPWR, input DIODE);\nendmodule\n")
            continue
        ports = [f"input {p}" for p in c.inputs + PG] + [f"output {p}" for p in c.outputs]
        body = []
        if c.ff:
            iq = c.ff["q_state"]
            clk, nxt = c.ff["clk"], c.ff["d"]
            body.append(f"  reg {iq};")
            if c.ff["clear"]:
                r = c.ff["clear"].lstrip("!")
                body.append(f"  always @(posedge {clk} or negedge {r}) if (!{r}) {iq} <= 1'b0; else {iq} <= {nxt};")
            elif c.ff["preset"]:
                r = c.ff["preset"].lstrip("!")
                body.append(f"  always @(posedge {clk} or negedge {r}) if (!{r}) {iq} <= 1'b1; else {iq} <= {nxt};")
            else:
                body.append(f"  always @(posedge {clk}) {iq} <= {nxt};")
            body += [f"  assign {p} = {c.fn_text[p].replace('!', '~')};" for p in c.outputs]
        else:
            body += [f"  assign {p} = {c.fn_text[p].replace('!', '~')};" for p in c.outputs]
        out.append(f"module {PREFIX}{t}({', '.join(ports)});\n" + "\n".join(body) + "\nendmodule\n")
    return "\n".join(out)


def cell_models(netlist_text: str) -> str:
    """cells.v for reading a gate-level netlist with Yosys: a model of every SKY130 HD cell it uses, plus an empty
    stub for each cell from another SKY130 library (e.g. sky130_ef_sc_hd__decap: no logic)."""
    types = sorted(set(re.findall(r"\b" + PREFIX + r"([a-z0-9_]+)\b", netlist_text)))
    other = sorted(set(re.findall(r"\b(sky130_(?!fd_)[a-z]+_sc_hd__[a-z0-9_]+)\b", netlist_text)))
    stubs = "".join(f"module {o}(input VGND, input VNB, input VPB, input VPWR);\nendmodule\n" for o in other)
    return yosys_models(types) + stubs


def yosys(*args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run Yosys with these arguments, output captured as text. Raises subprocess.TimeoutExpired."""
    return subprocess.run(["yosys", *args], capture_output=True, text=True, timeout=timeout)
