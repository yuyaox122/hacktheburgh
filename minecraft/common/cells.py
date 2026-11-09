"""SKY130 HD standard cells from per-cell Liberty JSON (fetched into the data cache on demand).

    parse_fn / ev    Liberty boolean functions as expression trees (the decompiler's parser, basic/core/lib.py),
                     and their value for 0/1 inputs
    celldef(t)       pins and raw function strings (the redstone techmap feeds them to Yosys)
    cell(t)          parsed functions, flip-flop model and area (the datapack compiler and its reference sim)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from basic.core import lib as _lib
from basic.core.lib import parse_fn

from . import fetch

POWER = {"VPWR", "VGND", "VPB", "VNB"}


def ev(e, env: dict) -> int:
    """Value (0/1) of a parse_fn tree, with env mapping variable names to 0/1."""
    return _lib.ev(e, env, 1)


def liberty(t: str) -> dict:
    """The Liberty JSON of one cell type, fetched if it is not cached yet."""
    if not fetch.fetch_cell(t):
        raise SystemExit(f"no Liberty JSON for {t}")
    return json.loads(fetch.pdk_path(t).read_text())


def fetch_types(types: set[str]) -> list[str]:
    """Fetch the Liberty JSON of every type; returns the ones the PDK does not have."""
    return [t for t in sorted(types) if not fetch.fetch_cell(t)]


# ---------------------------------------------------------------- raw view (redstone)

@dataclass
class CellDef:
    type: str
    inputs: list[str]
    outputs: dict[str, str]            # pin -> Liberty function string (flops: "IQ")
    ff: dict | None                    # {"clocked_on", "next_state", "clear"?, "preset"?}


def celldef(t: str) -> CellDef:
    d = liberty(t)
    ins = [k[4:] for k, v in d.items() if k.startswith("pin,") and v.get("direction") == "input" and k[4:] not in POWER]
    outs = {k[4:]: v.get("function", "") for k, v in d.items() if k.startswith("pin,") and v.get("direction") == "output"}
    ff = next((v for k, v in d.items() if k.startswith("ff,")), None)
    if any(k.startswith("latch,") for k in d):
        raise SystemExit(f"{t}: latches are not supported")
    return CellDef(t, ins, outs, ff)


# ---------------------------------------------------------------- parsed view (world)

@dataclass
class CellModel:
    type: str
    inputs: list[str]
    outputs: dict[str, tuple]          # pin -> expr; for flops the expr is ("var", "<state>")
    ff: dict | None                    # {"state", "next", "clock", "clear"?, "preset"?} with exprs
    area: float


@lru_cache(None)
def cell(t: str) -> CellModel:
    if t.startswith("conb"):
        return CellModel(t, [], {"HI": ("const", 1), "LO": ("const", 0)}, None, 3.75)
    d = liberty(t)
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
    return CellModel(t, ins, outs, ff, float(d.get("area", 0)))
