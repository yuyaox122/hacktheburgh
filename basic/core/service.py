"""Chip state for the API: clean and planted variants, cached in memory, with the operations the routes call."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field

from .. import paths
from . import chip as C
from . import find, netlist, pipeline, prove
from .sim import Sim

SPLIT = json.loads(paths.SPLIT.read_text())

_lock = threading.RLock()


@dataclass
class Variant:
    id: str                    # "tt_um_mac" or "tt_um_mac+t137"
    base: str
    nl: object
    res: dict
    proof: dict
    dec: object
    result: dict               # chip result JSON (CONTRACTS §1)
    flags: list = field(default_factory=list)   # with private "_net"
    plant: dict | None = None
    text: str | None = None    # netlist text (planted copies)


_cache: dict[str, Variant] = {}


def split_of(chip: str) -> str:
    if chip == "warmup":
        return "warmup"
    return "dev" if chip in SPLIT["dev"] else "test" if chip in SPLIT["test"] else "user"


def known_chips() -> list[str]:
    return SPLIT["dev"] + SPLIT["test"] + (["warmup"] if C.netlist_path("warmup").exists() else [])


def parse_id(cid: str) -> tuple[str, int | None]:
    """'tt_um_mac+t137' -> ('tt_um_mac', 137): a planted copy's base chip and trigger value; a clean id -> (id, None)."""
    m = re.match(r"^(.+)\+t(\d+)$", cid)
    return (m.group(1), int(m.group(2))) if m else (cid, None)


def get(cid: str) -> Variant:
    with _lock:
        if cid in _cache:
            return _cache[cid]
    base, n = parse_id(cid)
    if base not in known_chips() and base not in _cache:
        raise KeyError(f"unknown chip {base!r}; call list_chips")
    if n is None:
        nl = netlist.load(C.netlist_path(base))
        res, proof, dec = pipeline.run(nl, return_decompiler=True)
        saved = paths.CHIPS_OUT / f"{base}.json"
        if saved.exists():
            result = json.loads(saved.read_text())
            # the saved result may predate a decompiler change: keep its author check, refresh the rest
            fresh = C.assemble(base, split_of(base), nl, res, proof, C.info(base), result.get("author_source") or C.author_source(base))
            result = {**result, **{k: fresh[k] for k in ("decompiled", "blocks", "coverage", "verdict", "proof", "netlist", "cell_lines", "stats")}}
        else:
            src = C.author_source(base)
            src["check"], src["check_detail"] = C.author_check(base, src, nl.top)
            result = C.assemble(base, split_of(base), nl, res, proof, C.info(base), src)
        fl = find.flags(dec, res)
        result["flags"] = public(fl)
        v = Variant(cid, base, nl, res, proof, dec, result, fl)
    else:
        return plant(base, n)
    with _lock:
        _cache[cid] = v
    return v


def public(fl):
    """Flags without their private keys (`_net`), as results and the API show them."""
    return [{k: x for k, x in f.items() if not k.startswith("_")} for f in fl]


def plant_variant(base: str, split: str, nl, res: dict, n: int, src: dict) -> Variant:
    """A copy of a chip with a trigger for n planted (find.plant), decompiled, proven and flagged like any chip.
    res: the clean decompile (its registers give a chip without an 8-bit input an operand); src: the result's
    author_source. Used by plant() below and by the benchmark's trojan test."""
    pnl, info = find.plant(nl, n, res=res)
    pres, proof, dec = pipeline.run(pnl, return_decompiler=True)
    result = C.assemble(base, split, pnl, pres, proof, C.info(base), src, variant="planted")
    fl = find.flags(dec, pres)
    result["flags"] = public(fl)
    result["plant"] = info
    return Variant(f"{base}+t{n}", base, pnl, pres, proof, dec, result, fl, info)


def plant(base: str, n: int) -> Variant:
    cid = f"{base}+t{n}"
    with _lock:
        if cid in _cache:
            return _cache[cid]
    clean = get(base)
    src = {k: v for k, v in clean.result["author_source"].items()}
    src["check"], src["check_detail"] = "unavailable", "call prove_equiv with against='author_source'"
    v = plant_variant(base, clean.result["split"], clean.nl, clean.res, n, src)
    v.result["variant_id"] = cid
    pnl = v.nl
    v.text = find.write_netlist(pnl, pnl.insts, pnl.ports, {i.id: pnl._orig.get(i.id, i.id) for i in pnl.insts})
    with _lock:
        _cache[cid] = v
    return v


def planted_flag(v: Variant) -> dict | None:
    """The flag that best matches the planted trigger (or, blind, the rarest flag)."""
    if not v.flags:
        return None
    if v.plant:
        ins = set(v.plant["inserted_cells"])
        best = max(v.flags, key=lambda f: len(set(f["cells"]) & ins))
        if set(best["cells"]) & ins:
            return best
    return v.flags[0]


def fix(cid: str) -> dict:
    v = get(cid)
    if v.plant is None:
        raise ValueError("this chip has no planted trigger; plant one first (plant_trigger)")
    f = planted_flag(v)
    if f is None:
        raise ValueError("no suspicious logic was flagged in this chip")
    clean = get(v.base)
    out = find.fix(v.nl, f, clean.nl, clean.res)
    fixed = out.pop("_fixed")
    r2, _ = pipeline.run(fixed)
    out["verilog"] = r2["verilog"]
    out["flag"] = {k: x for k, x in f.items() if not k.startswith("_")}
    out["author_check"] = clean.result["author_source"].get("check")
    return out


def prove_against(cid: str, against: str) -> dict:
    v = get(cid)
    if against == "author_source":
        src = v.result["author_source"]
        if not src.get("files"):
            return {"verdict": "unavailable", "detail": "no author source for this chip", "counterexample": None}
        check, detail, cex = C.author_check(v.base, src, v.nl.top, nl_text=v.text, want_cex=True)
        return {"verdict": C.check_verdict(check), "check": check, "detail": detail, "counterexample": cex}
    p = v.proof
    return {"verdict": C.verdict_of(p), "detail": v.result["proof"]["detail"], "points": p["points"], "proven": p["proven"],
            "control_caught": p.get("control_caught"), "counterexample": None}


# ---------- review an edited decompile (Ideas in the web app; review_edit for IDE agents)
REVIEW_SECONDS = 90          # one budget for the whole review, every Yosys run included
REVIEW_HEADLINE = {"same": "Same circuit (proven)",
                   "tested_same": "No difference found in 20 cycles (not proven)",
                   "changed": "Behaviour changed",
                   "broken": "Doesn't compile"}
BEFORE_HEADLINE = {"same": "Same as before this edit (proven)",
                   "tested_same": "No difference from before this edit in 20 cycles (not proven)",
                   "changed": "This edit changes the behaviour"}
H_PORTS = "Ports or module name changed"
H_WIRING = "Wiring problem"
H_UNDEF = "Undefined value (x or z)"
H_SIM = "Can't be checked: simulation-only code"
H_NOCHECK = "Yosys couldn't check this edit"

_structures: dict[tuple[str, str], dict] = {}


def _structure(text: str, top: str, timeout: float) -> dict:
    key = (top, hashlib.sha1(text.encode()).hexdigest())
    if key in _structures:
        return _structures[key]
    s = C.edit_structure(text, top, timeout)
    if not s.get("timeout"):
        with _lock:
            _structures[key] = s
            while len(_structures) > 256:
                _structures.pop(next(iter(_structures)))
    return s


def _module_name(verilog: str) -> str | None:
    t = re.sub(r"//[^\n]*|/\*.*?\*/|\(\*.*?\*\)", " ", verilog, flags=re.S)
    m = re.search(r"\bmodule\s+(\w+)", t)
    return m.group(1) if m else None


def _plain_problem(w: str) -> str:
    if "no driver" in w:
        return "a wire is used but nothing drives it"
    if "conflict" in w or "multiple" in w:
        return "a signal is driven from two places"
    if "logic loop" in w:
        return "a logic loop (a signal feeds back into itself with no register in between)"
    return w.removeprefix("Warning: ")


def _guard(ed: dict, ref: dict, ref_name: str):
    """What the equivalence check can't see: compare the edited module's structure with the reference's.
    Returns (verdict, headline, detail) when the edit can't be called the same circuit, else None."""
    if ed["ports"] != ref["ports"]:
        a, b = ed["ports"], ref["ports"]
        diff = ([f"missing {n}" for n in sorted(set(b) - set(a))] + [f"new {n}" for n in sorted(set(a) - set(b))]
                + [f"{n} is now {a[n][0]} [{a[n][1]} bits], was {b[n][0]} [{b[n][1]} bits]" for n in sorted(set(a) & set(b)) if a[n] != b[n]])
        return "broken", H_PORTS, (f"The port list differs from {ref_name} ({'; '.join(diff)}). Keep the module's ports "
                                   "exactly as they are; only the logic inside may change.")
    new = [w for w in ed["problems"] if w not in ref["problems"]] if len(ed["problems"]) > len(ref["problems"]) else []
    if new:
        return "broken", H_WIRING, (f"Yosys's `check` found {_plain_problem(new[0])}. A proof can't be trusted with that in "
                                    f"the design. Yosys said: {new[0]}")
    multi = sorted(set(ed["multi"]) - set(ref["multi"]))
    if multi:
        return "broken", H_WIRING, (f"{', '.join(multi[:6])}{' and more' if len(multi) > 6 else ''} "
                                    f"{'is' if len(multi) == 1 else 'are'} set by more than one `assign`. On a chip that is a "
                                    "short circuit; remove one of them.")
    if ed["latches"] > ref["latches"]:
        return "broken", H_WIRING, ("The edit creates a latch: an `always @*` block that doesn't set a value on every path, "
                                    "so the value is held by a level, not a clock. Set it in every branch (or give it a default).")
    if ed["undef"] > ref["undef"]:
        return "broken", H_UNDEF, ("The edit uses x or z (an unknown or floating value) where a 0 or 1 is needed. Yosys's check "
                                   "treats those as 0, so it could call it the same circuit when the chip may not behave that "
                                   "way. Use 0s and 1s.")
    ck, rk = ed["clocks"], ref["clocks"]
    fmt = lambda ks: ", ".join(f"{e} {c}" for c, e in ks)
    new_ck = sorted(set(ck) - set(rk))
    if any(not c.startswith("internal") for c, _ in new_ck):
        return "changed", "Behaviour changed: a register now uses a different clock or clock edge", (
            f"Some flip-flops now update on {fmt(new_ck)}; in {ref_name} they only use {fmt(sorted(rk)) or 'no clock'}. "
            "Yosys's equivalence check counts clock steps, not which clock edge drives a register, so bASIC checks the "
            "clocking separately: this is a different circuit.")
    if new_ck or (len(rk) > 1 and ck != rk):
        return "broken", H_NOCHECK, ("This chip uses more than one clock, and the edit changes which flip-flops use which one "
                                     f"(now: {fmt(sorted(ck))}). Yosys's check can't tell clocks apart, so it can't say "
                                     "whether this is the same circuit.")
    return None


def _review(v: Variant, verilog: str, ref_text: str, deadline: float, before: str | None = None) -> dict:
    """One verdict: the edited module vs the chip's gates, or (with `before`) vs the Verilog before this edit."""
    top = v.nl.top
    other = "the Verilog before this edit" if before is not None else "the chip's gates"
    heads = BEFORE_HEADLINE if before is not None else REVIEW_HEADLINE

    def done(verdict, detail, cex=None, head=None):
        head = head or heads.get(verdict) or REVIEW_HEADLINE[verdict]
        if verdict == "changed" and cex and "step" in cex and head == heads["changed"]:
            ins = ", ".join(f"{k} = {x}" for k, x in cex.items() if k in ("ui_in", "uio_in"))
            if cex["step"] == 1 and cex.get("rst_n") == 0:
                head += ": the outputs differ in the first cycle, while reset is held" + (f" ({ins})" if ins else "")
            elif cex["step"] == 1:
                head += f": the outputs differ straight away{f', with {ins}' if ins else ''}"
            else:
                head += f": the outputs differ at clock cycle {cex['step']}, counting from reset"
        return {"verdict": verdict, "headline": head, "detail": detail, "counterexample": cex}

    if not verilog.strip():
        return done("broken", "The edited Verilog is empty.")
    name = _module_name(verilog)
    if name is None:
        return done("broken", "No `module NAME (...);` header found. Send the whole module, header included.")
    if name != top:
        return done("broken", f"The module is called {name!r}, but this chip's top module is {top!r}. Keep the module name.",
                    head=H_PORTS)
    sim = sorted(set(C.sim_only(verilog)) - set(C.sim_only(ref_text)))
    if sim:   # checked before Yosys reads the text (also keeps `include from reading files on this machine)
        return done("broken", f"The edit uses {' and '.join(sim)}. Yosys leaves that out when it builds gates but a "
                              "simulator doesn't, so a proof would not cover what the .v file does. Remove it.", head=H_SIM)
    ed = _structure(verilog, top, deadline - time.time())
    if ed.get("timeout"):
        return done("broken", "Yosys ran out of time reading the edited Verilog. Try a smaller edit.", head=H_NOCHECK)
    if "error" in ed:
        return done("broken", f"Yosys could not read the edited Verilog: {ed['error']}")
    ref = _structure(ref_text, top, deadline - time.time())
    if "error" in ref:
        return done("broken", f"Yosys could not read {other}: {ref['error']}", head=H_NOCHECK)
    g = _guard(ed, ref, "the Verilog before this edit" if before is not None else "bASIC's decompiled Verilog")
    if g:
        return done(g[0], g[2], head=g[1])
    check, detail, cex = C.author_check(v.base, {"files": {"edited.v": verilog}}, top, nl_text=v.text, want_cex=True,
                                        first_diff=True, deadline=deadline, gate_verilog=before)
    if check == "proven":
        return done("same", f"Yosys proved the edited Verilog equal to {other} for every input and every register value "
                            "(equiv_make / equiv_simple / equiv_induct), and checked that the edit keeps the ports, the "
                            "clocking and the wiring sound.")
    left = deadline - time.time()
    if check == "bounded" and before is None and v.res.get("proof_names") and left > 20:
        # second try, the decompiler's own proof: flip-flops and named wires paired 1:1 by name, with a broken control copy
        pr = prove.prove(v.nl, verilog, v.res["proof_names"], timeout=max(5, int(left / 4)))
        if pr["ok"]:
            return done("same", f"Yosys proved the edited Verilog equal to the chip's gates: all {pr['points']} paired "
                                "signals (outputs and registers matched by name) are equal for every input and register "
                                "value, and a deliberately broken copy of the gates was rejected.")
    if check == "bounded":
        return done("tested_same", f"Yosys ran the edited Verilog and {other} from reset for 20 clock cycles with every "
                                   "possible input and found no difference, but could not prove them equal for all time. "
                                   "Treat it as tested, not proven.")
    if check == "failed":
        seq = (cex or {}).pop("_seq", None)
        return done("changed", f"Yosys found inputs where the edited Verilog and {other} give different outputs. "
                    + (f"Input sequence from reset: {_steps_text(seq)}." if seq else ""), cex)
    err = re.sub(r"\S*/src/", "", detail).replace("Yosys could not read the author source: ", "")
    if detail.startswith("Yosys could not read"):   # the edit itself read fine above: the comparison is what failed
        return done("broken", f"Yosys read the edited Verilog but could not compare it with {other}: {err}", head=H_NOCHECK)
    return done("broken", f"Yosys could not finish checking the edit ({err}; the review has a {REVIEW_SECONDS} s budget). "
                          "Try a smaller edit, or review it again.", head=H_NOCHECK)


def _steps_text(seq: list[dict]) -> str:
    rows = []
    for i, st in enumerate(seq, 1):
        vals = ", ".join(f"{k} = {v}" for k, v in st.items() if k not in ("ena", "rst_n"))
        rows.append(f"cycle {i}: " + ("reset held, " if st.get("rst_n") == 0 else "") + (vals or "inputs 0"))
    return "; ".join(rows)


def review_edit(cid: str, verilog: str, baseline: str | None = None) -> dict:
    """Check an edited copy of the decompiled Verilog against the chip's real gates with Yosys.
    baseline: the Verilog before this edit (other edits already kept); then `this_edit` also says what this edit alone
    does, compared with that text. The verdicts and every sentence here are written by this code (an LLM may have
    proposed the edit, never the verdict)."""
    t0 = time.time()
    deadline = t0 + REVIEW_SECONDS
    v = get(cid)
    dec = v.result["decompiled"]["verilog"]
    out = {"chip": cid, **_review(v, verilog, dec, deadline)}
    if baseline is not None and baseline.strip() and baseline.strip() != dec.strip():
        out["this_edit"] = _review(v, verilog, baseline, deadline, before=baseline)
    out["seconds"] = round(time.time() - t0, 2)
    return out


# ---------- simulation (gate level, one pattern)
def simulate(cid: str, cycles: int = 8, inputs: dict | None = None, reset_first: bool = True) -> dict:
    v = get(cid)
    s: Sim = v.dec.s
    nl = v.nl
    inputs = {"ena": 1, "rst_n": 1, **(inputs or {})}
    env = {}
    for pn, p in nl.ports.items():
        if p["dir"] != "in":
            continue
        val = int(inputs.get(pn, 0))
        for k, b in enumerate(p["bits"]):
            if isinstance(b, int):
                env[b] = (val >> k) & 1
    q = {f.conns["Q"]: (1 if f.cell.ff["preset"] else 0) if reset_first else 0 for f in s.flops}
    regs = v.res["registers"]
    byid = {i.id: i for i in nl.insts}
    trace = []
    for c in range(cycles):
        vals = s.run({**env, **q, **{b: 0 for b in s.cuts}}, 1)
        row = {"cycle": c}
        for pn, p in nl.ports.items():
            if p["dir"] == "out":
                row[pn] = sum((Sim.val(vals, b, 1) & 1) << k for k, b in enumerate(p["bits"]))
        for rn, fids in regs.items():
            row[rn] = sum((vals.get(byid[f].conns["Q"], 0) & 1) << k for k, f in enumerate(fids))
        trace.append(row)
        q = {f.conns["Q"]: Sim.val(vals, f.conns.get("D", "0"), 1) & 1 for f in s.flops}
    return {"inputs": inputs, "trace": trace, "note": "Gate-level simulation of the netlist; one clock edge per row. Clocks are idealised (every flip-flop steps each cycle)."}


# ---------- critical path (estimate)
DELAY_NS = {"inv": 0.04, "clkinv": 0.04, "buf": 0.09, "clkbuf": 0.10, "nand2": 0.06, "nor2": 0.08, "and2": 0.13, "or2": 0.18,
            "xor2": 0.16, "xnor2": 0.15, "mux2": 0.20, "dlygate4sd3": 0.55, "dlymetal6s2s": 0.30}
CLK_Q, SETUP = 0.33, 0.12


def critical_path(cid: str) -> dict:
    v = get(cid)
    s, nl = v.dec.s, v.nl
    arr, frm = {}, {}
    for b in s.sources:
        arr[b] = CLK_Q if b in set(s.q_bits) else 0.0
    for i in s.order:
        ins = [x for p, x in i.conns.items() if p in i.cell.inputs and isinstance(x, int)]
        best = max(ins, key=lambda x: arr.get(x, 0.0), default=None)
        d = DELAY_NS.get(i.cell.base, 0.12 + 0.02 * len(i.cell.inputs))
        for p in i.cell.outputs:
            o = i.conns.get(p)
            if isinstance(o, int):
                arr[o] = arr.get(best, 0.0) + d
                frm[o] = (best, i.id)
    ends = [(f.conns.get("D"), f"flop {f.id}") for f in s.flops] + [(b, f"{n}[{k}]") for n, k, b in nl.out_bits()]
    ends = [(b, nm) for b, nm in ends if isinstance(b, int)]
    if not ends:
        return {"delay_ns_estimate": 0.0, "path": [], "label": "estimate"}
    b, nm = max(ends, key=lambda e: arr.get(e[0], 0.0))
    path = []
    while b in frm:
        b, cell = frm[b]
        path.append(cell)
    path.reverse()
    names = v.res["cell_lines"]
    return {"delay_ns_estimate": round(arr.get(max(ends, key=lambda e: arr.get(e[0], 0.0))[0], 0.0) + SETUP, 2),
            "ends_at": nm if isinstance(nm, str) else str(nm), "cells": path,
            "lines": sorted({names.get(c, 0) for c in path} - {0}),
            "label": "estimate from typical SKY130 HD cell delays (not timing sign-off)"}
