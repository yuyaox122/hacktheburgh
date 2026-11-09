"""SKY130 HD cell functions from per-cell Liberty JSON (fetched with gh into data/pdk/), as expression trees."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PDK = ROOT / "data" / "pdk"
PDK_REPO = "google/skywater-pdk-libs-sky130_fd_sc_hd"


@dataclass
class CellDef:
    type: str
    inputs: list[str]
    outputs: dict[str, tuple]          # pin -> expr; for flops the expr is ("var", "<state>")
    ff: dict | None                    # {"state", "next", "clock", "clear"?, "preset"?} with exprs
    area: float


def parse_fn(s: str):
    """Liberty boolean: ! & | ^ ( ) ' and juxtaposition as AND. Returns ("var",n)|("const",b)|("not",e)|(op,[e..])."""
    toks = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[01]|[!&|^()'*+]", s)
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def take():
        nonlocal pos
        pos += 1
        return toks[pos - 1]

    def p_or():
        xs = [p_xor()]
        while peek() in ("|", "+"):
            take(); xs.append(p_xor())
        return xs[0] if len(xs) == 1 else ("or", xs)

    def p_xor():
        xs = [p_and()]
        while peek() == "^":
            take(); xs.append(p_and())
        return xs[0] if len(xs) == 1 else ("xor", xs)

    def p_and():
        xs = [p_un()]
        while peek() in ("&", "*") or (peek() not in (None, "|", "+", "^", ")")):
            if peek() in ("&", "*"):
                take()
            xs.append(p_un())
        return xs[0] if len(xs) == 1 else ("and", xs)

    def p_un():
        if peek() == "!":
            take(); return ("not", p_un())
        t = take()
        if t == "(":
            e = p_or(); take()
        elif t in ("0", "1"):
            e = ("const", int(t))
        else:
            e = ("var", t)
        while peek() == "'":
            take(); e = ("not", e)
        return e

    return p_or()


def ev(e, env: dict) -> int:
    k = e[0]
    if k == "var":
        return env[e[1]]
    if k == "const":
        return e[1]
    if k == "not":
        return 1 - ev(e[1], env)
    vals = [ev(x, env) for x in e[1]]
    if k == "and":
        return int(all(vals))
    if k == "or":
        return int(any(vals))
    if k == "xor":
        return sum(vals) % 2
    raise ValueError(k)


def fetch(types: set[str]) -> list[str]:
    PDK.mkdir(parents=True, exist_ok=True)
    missing = []
    for t in sorted(types):
        out = PDK / f"{t}.json"
        if out.exists():
            continue
        base = t.rsplit("_", 1)[0]
        r = subprocess.run(["gh", "api", f"repos/{PDK_REPO}/contents/cells/{base}/sky130_fd_sc_hd__{t}__tt_025C_1v80.lib.json",
                            "-H", "Accept: application/vnd.github.raw"], capture_output=True)
        if r.returncode == 0:
            out.write_bytes(r.stdout)
        else:
            missing.append(t)
    return missing


@lru_cache(None)
def cell(t: str) -> CellDef:
    if t.startswith("conb"):
        return CellDef(t, [], {"HI": ("const", 1), "LO": ("const", 0)}, None, 3.75)
    d = json.loads((PDK / f"{t}.json").read_text())
    ins, outs, ff = [], {}, None
    for k, v in d.items():
        if k.startswith("ff,") or k.startswith("ff "):
            names = re.split(r"[ ,]+", k)[1:]
            ff = {"state": names[0], "next": parse_fn(v["next_state"]), "clock": parse_fn(v["clocked_on"])}
            if "clear" in v:
                ff["clear"] = parse_fn(v["clear"])
            if "preset" in v:
                ff["preset"] = parse_fn(v["preset"])
    for k, v in d.items():
        if not k.startswith("pin,"):
            continue
        p = k.split(",", 1)[1]
        if v.get("direction") == "input":
            ins.append(p)
        elif v.get("direction") == "output" and "function" in v:
            outs[p] = parse_fn(v["function"])
    return CellDef(t, ins, outs, ff, float(d.get("area", 0)))


def category(t: str) -> str:
    """Visual family of a cell type."""
    b = t.rsplit("_", 1)[0]
    if b.startswith(("df", "dl", "sdf", "edf")):
        return "flop"
    if b.startswith(("clkbuf", "clkinv", "buf", "inv", "dlygate", "dlymetal", "clkdly")):
        return "buf"
    if b.startswith(("xor", "xnor", "fa", "ha", "maj")):
        return "arith"
    if b.startswith("mux"):
        return "mux"
    if b.startswith("conb"):
        return "const"
    if re.match(r"(fill|decap|tapvpwrvgnd|tap|diode)", b):
        return "phys"
    return "logic"
