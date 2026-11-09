"""Witness routes on the stub chip: get_region, probe_truth, propose_claim (contract proposed on the board, 7 Nov 20:32).

The stub checker decides claims by exhaustive simulation of `stub_counter8`, which is honest only
because the stub chip is tiny. Real chips go to the core's checker (core/claims.py + Yosys, Yuyao's).
Every result carries "source": "stub".

stub_counter8, as the claim checker sees it:
    inputs  en (1 bit), ui_in[7:0]          rst_n = 1 and ena = 1 are assumed, as on the real checker
    register count[7:0] = flip-flops q_ff_7 ... q_ff_0 (`count` is a display alias)
    next(count) = en ? count + 1 : count    (8-bit, wraps at 255)
    uo_out[1] = (count == 200)              the compare
    uo_out[0] = uo_out[1] | (ui_in == N)    only on a copy with a planted trigger for N
    uo_out[7:2] = 0
"""

from __future__ import annotations

import itertools
import random

from decap.core.claim_expr import (ClaimSyntaxError, is_complete, is_tautology, names, output_bits, parse_checked, readings,
                                   shape_ok)

SOURCES = {"en": 1, "ui_in": 8, "count": 8}
FLOPS = [f"q_ff_{k}" for k in range(8)]
WIDTHS = {"en": 1, "ui_in": 8, "count": 8, "uo_out": 8, **{f: 1 for f in FLOPS}}
COMPARE = 200

# What each stub cone looks like to a region agent.
REGION = {
    "c0": {"inputs": ["en", "count"], "outputs": ["next(count)"], "literals": 40,
           "focus": ["if (en) count <= count + 8'd1;"]},
    "c1": {"inputs": ["count"], "outputs": ["uo_out[1]"], "literals": 16,
           "focus": [f"assign uo_out[1] = (count == 8'd{COMPARE});"]},
    "c2": {"inputs": ["ui_in"], "outputs": ["uo_out[0]"], "literals": 16,
           "focus": ["assign uo_out[0] = uo_out[1] | trig;  // trig: an 8-input AND tree over ui_in"]},
}


def _deps(kind: str, name: str, planted: bool) -> set[str]:
    if kind == "next":
        return {"count", "en"}
    if name in FLOPS or name == "count":
        return {"count"}
    if name == "uo_out":
        return {"count", "ui_in"} if planted else {"count"}
    return {name}


def chip_values(src: dict[str, int], n: int | None) -> dict:
    """Every nameable value for one assignment of the sources."""
    count, en, ui = src.get("count", 0), src.get("en", 0), src.get("ui_in", 0)
    hit = int(count == COMPARE)
    trig = int(n is not None and ui == n)
    nxt = (count + 1) & 0xFF if en else count
    env = {("sig", "en"): en, ("sig", "ui_in"): ui, ("sig", "count"): count,
           ("sig", "uo_out"): (hit | trig) | (hit << 1), ("next", "count"): nxt}
    for k, f in enumerate(FLOPS):
        env[("sig", f)] = (count >> k) & 1
        env[("next", f)] = (nxt >> k) & 1
    return env


def _assignments(srcs: list[str]):
    for vals in itertools.product(*[range(1 << SOURCES[s]) for s in srcs]):
        yield dict(zip(srcs, vals))


def get_region(cones: list[str]) -> dict:
    inputs, outputs, focus, lits = [], [], [], 0
    for c in cones:
        r = REGION[c]
        inputs += [i for i in r["inputs"] if i not in inputs]
        outputs += [o for o in r["outputs"] if o not in outputs]
        focus += r["focus"]
        lits += r["literals"]

    def describe(name: str) -> dict:
        if name == "count":
            return {"name": "count", "width": 8, "kind": "flop", "flops": FLOPS[::-1], "label": "count"}
        if name.startswith("next("):
            return {"name": name, "width": 8, "kind": "flop_next", "label": "count, one clock later"}
        base = name.split("[")[0]
        return {"name": name, "width": 1 if "[" in name else WIDTHS[base], "kind": "port"}

    return {"inputs": [describe(i) for i in inputs], "outputs": [describe(o) for o in outputs],
            "focus": focus, "cone_literals": lits,
            "assumed": {"rst_n": 1, "ena": 1}, "netlist_sha": "stub", "decompiler": "stub",
            "names_note": "Claims may use ports (en, ui_in, uo_out with [i] or [hi:lo]), the register alias "
                          "'count' or its flip-flops q_ff_7..q_ff_0, and next(x) for x one clock later."}


def _out_value(name: str, env: dict) -> int:
    if name.startswith("next("):
        return env[("next", "count")]
    base, _, sel = name.partition("[")
    v = env[("sig", base)]
    return (v >> int(sel.rstrip("]"))) & 1 if sel else v


def probe(cones: list[str], planted: int | None, n: int, seed: int, fix: dict | None = None) -> dict:
    region = get_region(cones)
    fix = fix or {}
    bad = [k for k in fix if k not in SOURCES]
    if bad:
        from fastapi import HTTPException
        raise HTTPException(400, f"fix: unknown input(s) {bad}; use the region's inputs")
    srcs = [i["name"] for i in region["inputs"] if i["name"] not in fix]
    bits = sum(SOURCES[s] for s in srcs)
    exhaustive = bits <= 12
    if exhaustive:
        rows_in = [{**a, **fix} for a in _assignments(srcs)]
    else:
        rng = random.Random(seed)
        rows_in = [{**{s: rng.getrandbits(SOURCES[s]) for s in srcs}, **fix} for _ in range(n)]
    outs = [o["name"] for o in region["outputs"]]
    rows = [{"in": r, "out": {o: _out_value(o, chip_values(r, planted)) for o in outs}} for r in rows_in]
    stats = {}
    for o in outs:
        vals = [r["out"][o] for r in rows]
        if o.endswith("]") and not o.startswith("next("):
            ones = sum(vals)
            st = {"ones": ones, "of": len(vals)}
            if 0 < ones <= max(1, len(vals) // 16):
                st["rows_where_1"] = [r["in"] for r in rows if r["out"][o]][:4]
            stats[o] = st
        else:
            stats[o] = {"distinct": len(set(vals)), "of": len(vals)}
    shown = random.Random(seed).sample(rows, min(64, len(rows)))
    return {"exhaustive": exhaustive, "vectors": len(rows), "samples": shown, "stats": stats,
            "assumed": {"rst_n": 1, "ena": 1}, "fixed": fix,
            "note": "Simulation samples: what the chip did on these inputs. Not a proof."}


def check(claim: str, cones: list[str], planted: int | None, label: str = "", run: bool = True) -> dict:
    """The stub checker: exhaustive simulation over every source the claim depends on."""
    try:
        node, ev = parse_checked(claim, WIDTHS, lambda name: name in FLOPS or name == "count")
    except ClaimSyntaxError as e:
        return {"verdict": "unparseable", "error": str(e)}
    out = readings(node)
    if not run:
        return {"verdict": "unchecked", **out}

    # tautology: true whatever the chip does (every name free); skipped when that space is too big
    if is_tautology(ev, node):
        return {"verdict": "tautology", "truth": "tautology", **out, "reason": "true of any chip: it says nothing about this one"}
    refs = sorted(names(node))

    srcs = sorted(set().union(*[_deps(k, n, planted is not None) for k, n in refs]))
    for a in _assignments(srcs):
        env = chip_values(a, planted)
        if not ev.holds(node, env):
            cex = dict(a)
            for k, n in refs:
                key = f"next({n})" if k == "next" else n
                if key not in cex:
                    cex[key] = env[(k, n)]
            return {"verdict": "refuted", "truth": "refuted", **out, "counterexample": cex, "refute_kind": "semantic"}

    cone_lits = get_region(cones)["cone_literals"] if cones else None
    label_ok = shape_ok(label, node)
    short = cone_lits is not None and out["literals"] * 3 <= cone_lits
    want = {("next", "count")} | {("next", f) for f in FLOPS} if "c0" in cones else set()
    if {"c1", "c2"} & set(cones):
        want.add(("sig", "uo_out"))
    on_topic = not cones or bool(names(node) & want)
    if not on_topic:
        return {"verdict": "restated", "truth": "proven", **out, "credit": {"cleared": False, "reason": "says nothing about this region's outputs",
                                                         "literals": out["literals"], "cone_literals": cone_lits}}
    rng = random.Random(3)
    envs = [chip_values(a, planted) for a in rng.sample(list(_assignments(srcs)), min(48, 1 << sum(SOURCES[x] for x in srcs)))]
    need = {o for c in cones for o in REGION[c]["outputs"]}
    said = {f"next({n})" if k == "next" else n for k, n in names(node)} | {f"next(count)" for k, n in names(node)
                                                                             if k == "next" and n in FLOPS}
    missing = sorted(o for o in need if o not in said and o.split("[")[0] not in said)
    complete = is_complete(ev, node, envs, output_bits(node, want, WIDTHS | {"count": 8})) and not missing
    if not complete:
        return {"verdict": "partial", "truth": "proven", **out, "credit": {"cleared": False, "complete": complete,
                "reason": f"does not cover every output of the region: missing {', '.join(missing)}" if missing
                          else "true, but it does not say what the output is for every input",
                "literals": out["literals"], "cone_literals": cone_lits}}
    if label_ok or short:
        reason = f"labelled shape: {label}" if label_ok else f"{out['literals']} literals vs {cone_lits} in the decompiled cone"
        return {"verdict": "proven", "truth": "proven", **out, "credit": {"cleared": True, "complete": True, "reason": reason,
                                                       "literals": out["literals"], "cone_literals": cone_lits}}
    return {"verdict": "restated", "truth": "proven", **out,
            "credit": {"cleared": False, "reason": "proven, but no shorter than the decompile and no known shape",
                       "literals": out["literals"], "cone_literals": cone_lits}}
