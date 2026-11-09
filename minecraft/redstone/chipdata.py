"""Chip inputs: fetch a Tiny Tapeout 08 project (netlist + GDS) and the SKY130 HD Liberty JSON its cells need,
then read them: netlist (ports, cells, pins), GDS (cell positions by instance name, pin label positions).

    python chipdata.py fetch tt_um_sign_addsub
Data goes to data/ (gitignored). TT08 projects are Apache-2.0 per their LICENSE; the PDK is Apache-2.0.
"""
from __future__ import annotations

import json
import math
import re
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
TT_REPO = "TinyTapeout/tinytapeout-08"
PDK_REPO = "google/skywater-pdk-libs-sky130_fd_sc_hd"
PHYSICAL = re.compile(r"^(fill|decap|tapvpwrvgnd|tap|diode|fakediode)_")
POWER = {"VPWR", "VGND", "VPB", "VNB"}


def _gh_raw(repo: str, path: str) -> bytes | None:
    r = subprocess.run(["gh", "api", f"repos/{repo}/contents/{path}", "-H", "Accept: application/vnd.github.raw"],
                       capture_output=True)
    return r.stdout if r.returncode == 0 else None


def fetch(chip: str) -> Path:
    d = DATA / "tt08" / chip
    d.mkdir(parents=True, exist_ok=True)
    for name in (f"{chip}.v", f"{chip}.gds", "LICENSE", "info.yaml"):
        if not (d / name).exists():
            b = _gh_raw(TT_REPO, f"projects/{chip}/{name}")
            if b is None:
                raise SystemExit(f"could not fetch {chip}/{name}")
            (d / name).write_bytes(b)
    lic = (d / "LICENSE").read_text(errors="replace")[:200]
    if "Apache" not in lic and "MIT" not in lic and "BSD" not in lic:
        print(f"WARNING: {chip} licence is not Apache/MIT/BSD: {lic[:80]!r}")
    nl = read_netlist(chip)
    for t in sorted({c.type for c in nl.cells if not c.physical}):
        pdk_json(t)
    return d


def pdk_json(t: str) -> dict:
    p = DATA / "pdk" / f"{t}.json"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        base = re.sub(r"_\d+$", "", t)
        b = _gh_raw(PDK_REPO, f"cells/{base}/sky130_fd_sc_hd__{t}__tt_025C_1v80.lib.json")
        if b is None:
            raise SystemExit(f"no Liberty JSON for {t}")
        p.write_bytes(b)
    return json.loads(p.read_text())


# ---------------------------------------------------------------- netlist

@dataclass
class Cell:
    name: str
    type: str                      # e.g. "a21oi_1"
    pins: dict[str, str]           # pin -> net

    @property
    def physical(self) -> bool:
        return bool(PHYSICAL.match(self.type))


@dataclass
class Netlist:
    top: str
    ports: dict[str, dict]         # name -> {"dir": "in"|"out", "width": n}
    cells: list[Cell]
    assigns: dict[str, str] = field(default_factory=dict)

    def bits(self, direction: str) -> list[str]:
        out = []
        for n, p in self.ports.items():
            if p["dir"] == direction:
                out += [n] if p["width"] == 1 else [f"{n}[{i}]" for i in range(p["width"])]
        return out


def _clean(s: str) -> str:
    s = s.strip()
    return s[1:].strip() if s.startswith("\\") else s


def read_netlist(chip: str) -> Netlist:
    text = (DATA / "tt08" / chip / f"{chip}.v").read_text()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    top = re.search(r"\bmodule\s+(\S+)", text).group(1)
    ports = {}
    for m in re.finditer(r"\b(input|output)\s+(?:wire\s+)?(\[(\d+):(\d+)\]\s*)?([^;]+);", text):
        w = abs(int(m.group(3)) - int(m.group(4))) + 1 if m.group(2) else 1
        for n in m.group(5).split(","):
            ports[_clean(n)] = {"dir": "in" if m.group(1) == "input" else "out", "width": w}
    cells = []
    for m in re.finditer(r"\bsky130_fd_sc_hd__(\w+)\s+(\\?\S+)\s*\((.*?)\)\s*;", text, re.S):
        pins = {}
        for pm in re.finditer(r"\.(\w+)\s*\(([^()]*)\)", m.group(3)):
            if pm.group(1) not in POWER and pm.group(2).strip():
                pins[pm.group(1)] = _clean(pm.group(2)).replace(" ", "")
        cells.append(Cell(_clean(m.group(2)), m.group(1), pins))
    assigns = {}
    for m in re.finditer(r"\bassign\s+([^=]+?)\s*=\s*([^;]+);", text):
        assigns[_clean(m.group(1)).replace(" ", "")] = _clean(m.group(2)).replace(" ", "")
    return Netlist(top, ports, cells, assigns)


# ---------------------------------------------------------------- GDS (placement + pin labels)

def _gdsreal(b: bytes) -> float:
    if not any(b):
        return 0.0
    s = -1 if b[0] & 0x80 else 1
    return s * int.from_bytes(b[1:8], "big") / (1 << 56) * 16 ** ((b[0] & 0x7F) - 64)


def read_gds(chip: str) -> dict:
    """{'cells': {inst: (x_um, y_um)}, 'pins': {label: (x_um, y_um)}, 'die': (w, h), 'flipped': {inst}} from the
    top structure. (x, y) is the SREF origin; for a mirrored cell (STRANS reflection, the FS rows) that origin is the
    cell's TOP edge, so its row's bottom is y - the row height."""
    d = (DATA / "tt08" / chip / f"{chip}.gds").read_bytes()
    i, cur, el, dbu, structs, order = 0, None, None, 1e-9, {}, []
    while i < len(d):
        n, rt = struct.unpack(">HB", d[i:i + 3])
        p = d[i + 4:i + n]
        i += n
        if n == 0:
            break
        if rt == 0x03:
            dbu = _gdsreal(p[8:16])
        elif rt == 0x06:
            cur = {"srefs": [], "texts": [], "bnds": []}
            structs[p.rstrip(b"\0").decode()] = cur
            order.append(p.rstrip(b"\0").decode())
        elif rt in (0x0A, 0x0C, 0x08):
            el = {"k": rt}
        elif el is None:
            continue
        elif rt == 0x0D:
            el["layer"] = struct.unpack(">h", p)[0]
        elif rt == 0x10:
            v = struct.unpack(">%di" % (len(p) // 4), p)
            el["xy"] = list(zip(v[0::2], v[1::2]))
        elif rt == 0x12:
            el["sname"] = p.rstrip(b"\0").decode()
        elif rt == 0x19:
            el["s"] = p.rstrip(b"\0").decode()
        elif rt == 0x2C:
            el["prop"] = p.rstrip(b"\0").decode()
        elif rt == 0x1A:
            el["strans"] = struct.unpack(">H", p[:2])[0]
        elif rt == 0x11:
            if el["k"] == 0x0A:
                cur["srefs"].append(el)
            elif el["k"] == 0x0C:
                cur["texts"].append(el)
            elif el["k"] == 0x08 and el.get("layer") == 235:
                cur["bnds"].append(el)
            el = None
    top = structs[order[-1]]
    um = dbu * 1e6
    cells = {_clean(r["prop"]): (r["xy"][0][0] * um, r["xy"][0][1] * um) for r in top["srefs"] if "prop" in r}
    pins = {t["s"]: (t["xy"][0][0] * um, t["xy"][0][1] * um) for t in top["texts"] if "s" in t}
    xs = [x for b in top["bnds"] for x, _ in b["xy"]] or [0, 161000]
    ys = [y for b in top["bnds"] for _, y in b["xy"]] or [0, 111520]
    flipped = {_clean(r["prop"]) for r in top["srefs"] if "prop" in r and r.get("strans", 0) & 0x8000}
    return {"cells": cells, "pins": pins, "die": ((max(xs) - min(xs)) * um, (max(ys) - min(ys)) * um),
            "flipped": flipped}


# ---------------------------------------------------------------- Liberty functions

def parse_fn(s: str):
    """Liberty boolean (! & | ^ ( ) and juxtaposition) -> ('var',n) | ('const',b) | ('not',e) | (op,[...])."""
    toks = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[01]|[!&|^()'*+]", s)
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def take():
        pos[0] += 1
        return toks[pos[0] - 1]

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
            e = ("const", t == "1")
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
        return int(e[1])
    if k == "not":
        return 1 - ev(e[1], env)
    vs = [ev(x, env) for x in e[1]]
    if k == "and":
        return int(all(vs))
    if k == "or":
        return int(any(vs))
    r = 0
    for v in vs:
        r ^= v
    return r


@dataclass
class CellDef:
    type: str
    inputs: list[str]
    outputs: dict[str, str]            # pin -> Liberty function string (flops: "IQ")
    ff: dict | None                    # {"clocked_on", "next_state", "clear"?, "preset"?}


def celldef(t: str) -> CellDef:
    d = pdk_json(t)
    ins = [k[4:] for k, v in d.items() if k.startswith("pin,") and v.get("direction") == "input" and k[4:] not in POWER]
    outs = {k[4:]: v.get("function", "") for k, v in d.items() if k.startswith("pin,") and v.get("direction") == "output"}
    ff = next((v for k, v in d.items() if k.startswith("ff,")), None)
    if any(k.startswith("latch,") for k in d):
        raise SystemExit(f"{t}: latches are not supported")
    return CellDef(t, ins, outs, ff)


if __name__ == "__main__":
    if sys.argv[1:2] == ["fetch"]:
        for c in sys.argv[2:]:
            print(fetch(c))
    else:
        print(__doc__)
