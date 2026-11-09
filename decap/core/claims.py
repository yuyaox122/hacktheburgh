"""Witness claim checker: prove or refute one claim about a chip with Yosys (no LLM decides anything here).

A claim is a Verilog expression over the chip's ports, flip-flops (q_<cell id>) and the decompiler's register
names (aliases for their flip-flops), with next(x) = x one clock later and 'A when B' = B -> A. The grammar and
the code-written English are Ian's (core/claim_expr.py), shared with the agent, its stub and its fallback so all
of them read every claim the same way.

Setup (docs/archive/research/witness-plan.md): rst_n = 1, ena = 1, registers free. Every flip-flop is cut: its Q
becomes a free input q_<id>, and next(q_<id>) is its next-state function (D, enable, scan, clear/preset)
evaluated on the same inputs. So "proven" means: for EVERY input and EVERY register value, one clock step.

Order, as in Ian's fallback: unparseable -> tautology -> (free-register proof)
  proven                 -> credit: partial (doesn't fix the outputs it names) / restated (no shorter, no shape) / proven
  refuted with free regs -> BMC from reset, 30 cycles, real flip-flops:
                              fails -> refuted, refute_kind "semantic"
                              holds -> k-induction (sat -tempinduct): inductive (true from reset, proven) / bounded
Credit (Yuyao, 7 Nov): the claim must mention one of the region's outputs, mention EVERY output bit of the region
(ports by bit, next(reg)[k] = next(q_<flop k>); Ian's coverage rule, so a claim about one buffered copy of a flop
can't explain a whole region), pin those output bits down for every input (completeness: SAT finds no two different
output values that both satisfy it), and be a labelled shape or use at most 1/3 of the literals of the region's
decompiled lines.
"""
from __future__ import annotations

import dataclasses
import hashlib
import re
import subprocess
import tempfile
import time
from pathlib import Path

from . import lib
from .claim_expr import (SHAPES, ClaimSyntaxError, Evaluator, Node, _has_op, english, literals, names, output_bits, parse,
                         ref_bits, unparse)
from .prove import gate_verilog

FIXED = {"rst_n": 1, "ena": 1}
BMC_STEPS = 30
INDUCT_STEPS = 20
TIMEOUT = 60
SOURCE = "yosys"
METHOD = ("Yosys SAT: rst_n=1, ena=1, flip-flops cut (registers free), one clock step. If that fails: "
          f"{BMC_STEPS}-cycle BMC from reset, then k-induction (all flip-flops step together, so derived clocks are approximate).")

_ID = re.compile(r"[A-Za-z_]\w*")


# ---------------------------------------------------------------------------------------------- names
class Signals:
    """What a claim may name on one chip variant, and how each name maps to netlist bits."""

    def __init__(self, v):
        self.v, self.nl = v, v.nl
        self.ports = {n: p for n, p in self.nl.ports.items() if n != "clk"}
        self.flops = {f.id: f for f in self.nl.flops}
        self.regs = {r: list(f) for r, f in v.res.get("registers", {}).items() if f and all(x in self.flops for x in f)}
        self.widths = {n: len(p["bits"]) for n, p in self.ports.items()}
        self.widths |= {f"q_{fid}": 1 for fid in self.flops}
        self.widths |= {r: len(f) for r, f in self.regs.items()}
        self.outputs = {n for n, p in self.ports.items() if p["dir"] == "out"}

    def fids(self, name: str) -> list[str]:
        """Flip-flop ids behind a register or q_<id> name, LSB first."""
        return self.regs[name] if name in self.regs else [name[2:]]

    def nextable(self, name: str) -> bool:
        return name in self.regs or (name.startswith("q_") and name[2:] in self.flops)


def formal(sig: Signals, n: Node) -> Node:
    """Canonical form: register names replaced by their flip-flops ({q_c12, q_c11, ...}), so cached claims survive
    a renaming in the decompiler."""
    if n.op in ("sig", "next") and n.name in sig.regs:
        fids = sig.regs[n.name]
        lo, hi = (0, len(fids) - 1) if n.hi is None else (n.lo, n.hi)
        parts = [Node(n.op, name=f"q_{fids[k]}") for k in range(hi, lo - 1, -1)]
        return parts[0] if len(parts) == 1 else Node("{}", parts)
    if not n.args:
        return n
    return dataclasses.replace(n, args=[formal(sig, a) for a in n.args])


# ---------------------------------------------------------------------------------------------- claim -> Verilog
def vname(kind: str, name: str, alt: set | None = None) -> str:
    base = f"next__{name}" if kind == "next" else name
    return f"alt__{base}" if alt and (kind, name) in alt else base


def vexpr(n: Node, alt: set | None = None) -> str:
    """Verilog for a parsed claim. Unsized numbers become 32'd (unsigned), as claim_expr evaluates them."""
    op, a = n.op, n.args
    if op == "num":
        return f"{n.width}'d{n.value}"
    if op in ("sig", "next"):
        sel = "" if n.hi is None else (f"[{n.hi}]" if n.hi == n.lo else f"[{n.hi}:{n.lo}]")
        return vname(op, n.name, alt) + sel
    if op[0] in "ur" and len(a) == 1:
        return f"{op[1]}({vexpr(a[0], alt)})"
    if op == "->":
        return f"(!({vexpr(a[0], alt)}) || ({vexpr(a[1], alt)}))"
    if op == "?:":
        return f"(({vexpr(a[0], alt)}) ? ({vexpr(a[1], alt)}) : ({vexpr(a[2], alt)}))"
    if op == "{}":
        return "{" + ", ".join(vexpr(x, alt) for x in a) + "}"
    return f"({vexpr(a[0], alt)} {op} {vexpr(a[1], alt)})"


def _subst(fn: str, table: dict[str, str]) -> str:
    """A Liberty function (e.g. '(D&DE) | (IQ&!DE)') with its pin names replaced; '!' kept (1-bit operands)."""
    return _ID.sub(lambda m: f"({table[m.group()]})" if m.group() in table else m.group(), fn)


def chip_module(sig: Signals, node: Node, cut: bool) -> tuple[str, list[str]]:
    """The chip as Verilog plus the claim as output __ok. cut=True: flip-flops removed, Q = free input q_<id>.
    cut=False: real flip-flops (for BMC / induction from reset); __ok is forced true while rst_n is low."""
    nl = sig.nl
    port_of = {}
    for pn, p in nl.ports.items():
        for k, b in enumerate(p["bits"]):
            if isinstance(b, int) and b not in port_of and p["dir"] == "in":
                port_of[b] = f"{pn}[{k}]" if len(p["bits"]) > 1 else pn

    def net(b):
        return f"1'b{b}" if isinstance(b, str) else port_of.get(b) or f"_n{b}"

    insts = [i for i in nl.insts if not (cut and i.cell.seq)]
    text = gate_verilog(dataclasses.replace(nl, insts=insts), {})
    declared = {int(x) for x in re.findall(r"wire _n(\d+);", text)}
    ports, body = [], []

    def need(b):
        if isinstance(b, int) and b not in port_of and b not in declared:
            declared.add(b)
            body.append(f"  wire _n{b};")

    for fid, f in sig.flops.items():
        ff, q = f.cell.ff, f"q_{fid}"
        qs = ff["q_state"]
        if cut:
            ports.append(f"  input {q}")
            for p in f.cell.outputs:
                b = f.conns.get(p)
                if isinstance(b, int):
                    need(b)
                    body.append(f"  assign _n{b} = {_subst(f.cell.fn_text[p], {qs: q, qs + '_N': '~' + q})};")
        else:
            src = "1'b0"
            for p in f.cell.outputs:
                b = f.conns.get(p)
                if isinstance(b, int):
                    need(b)
                    fn = f.cell.fn_text[p].replace(" ", "")
                    if fn == qs:
                        src = net(b)
                        break
                    if fn in ("!" + qs, qs + "_N"):
                        src = f"~{net(b)}"
            body.append(f"  wire {q} = {src};")
        pins = {p: net(f.conns[p]) if p in f.conns else "1'b0" for p in f.cell.inputs}
        pins |= {qs: q, qs + "_N": "~" + q}
        d = _subst(ff["d"], pins)
        if ff.get("preset"):
            d = f"({_subst(ff['preset'], pins)}) ? 1'b1 : ({d})"
        if ff.get("clear"):
            d = f"({_subst(ff['clear'], pins)}) ? 1'b0 : ({d})"
        body.append(f"  wire d_{fid} = {d};")

    used = names(node)
    for kind, name in sorted(used):
        if kind == "sig" and name in sig.regs:
            fids = sig.regs[name]
            body.append(f"  wire [{len(fids) - 1}:0] {name} = {{{', '.join(f'q_{x}' for x in reversed(fids))}}};")
        elif kind == "next":
            fids = sig.fids(name)
            body.append(f"  wire [{len(fids) - 1}:0] next__{name} = {{{', '.join(f'd_{x}' for x in reversed(fids))}}};")
    claim = f"|({vexpr(node)})"
    body.append(f"  assign __ok = {claim if cut or 'rst_n' not in nl.ports else f'!rst_n || {claim}'};")
    ports.append("  output __ok")
    head = f"module {nl.top} (\n"
    text = text.replace(head, head + ",\n".join(ports) + ",\n", 1)
    text = text[: text.rindex("endmodule")] + "\n".join(body) + "\nendmodule\n"
    return text, sorted({i.type for i in insts})


def free_module(sig: Signals, node: Node, alt: set | None = None, out_bits: list | None = None) -> str:
    """The claim over free variables only (no chip): for the tautology check, and with alt + out_bits for
    completeness (two output values that both satisfy the claim)."""
    ins = []
    for kind, name in sorted(names(node)):
        w = sig.widths[name] if kind == "sig" else len(sig.fids(name))
        rng = f"[{w - 1}:0] " if w > 1 else ""
        ins.append(f"  input {rng}{vname(kind, name)}")
        if alt and (kind, name) in alt:
            ins.append(f"  input {rng}{vname(kind, name, alt)}")
    if not alt:
        ok = f"|({vexpr(node)})"
    else:
        diff = " || ".join(f"({vname(k, n)}{'[%d]' % b if _w(sig, k, n) > 1 else ''} != "
                           f"{vname(k, n, alt)}{'[%d]' % b if _w(sig, k, n) > 1 else ''})" for k, n, b in out_bits)
        ok = f"!((|({vexpr(node)})) && (|({vexpr(node, alt)})) && ({diff}))"
    return "module claimfree (\n" + ",\n".join(ins + ["  output __ok"]) + f"\n);\n  assign __ok = {ok};\nendmodule\n"


def _w(sig: Signals, kind: str, name: str) -> int:
    return sig.widths[name] if kind == "sig" else len(sig.fids(name))


# ---------------------------------------------------------------------------------------------- Yosys
ROW = re.compile(r"^\s*(?:(\d+)\s+)?\\(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")


def _yosys(gate: str, top: str, types: list[str], sat: str, timeout: int, seq: bool = False) -> dict:
    with tempfile.TemporaryDirectory() as td:
        Path(td, "gate.v").write_text(gate)
        Path(td, "cells.v").write_text(lib.yosys_models(types) if types else "")
        Path(td, "run.ys").write_text(
            f"read_verilog {td}/cells.v {td}/gate.v\nhierarchy -top {top}\nproc\nflatten\n"
            f"{'async2sync' + chr(10) if seq else ''}opt_clean\nsat {sat}\n")
        try:
            r = subprocess.run(["yosys", "-q", "-l", f"{td}/log.txt", f"{td}/run.ys"], capture_output=True, text=True,
                               timeout=timeout + 15)
        except subprocess.TimeoutExpired:
            return {"status": "timeout"}
        log = Path(td, "log.txt").read_text() if Path(td, "log.txt").exists() else r.stdout + r.stderr
    if r.returncode != 0:
        err = (r.stderr or r.stdout or log).strip().splitlines()
        return {"status": "error", "error": (err[-1] if err else "yosys failed")[:400]}
    if "timeout" in log.lower() and "SUCCESS" not in log and "FAIL" not in log:
        return {"status": "timeout"}
    if re.search(r"Induction step proven: SUCCESS|no model found: SUCCESS|PASSED", log):
        return {"status": "proven", "log": log}
    rows: dict[str, list[tuple[int, str]]] = {}
    tail = log[log.rfind("FAIL"):] if "FAIL" in log else log
    for line in tail.splitlines():
        m = ROW.match(line)
        if m and m.group(3).lstrip("-").isdigit():
            rows.setdefault(m.group(2), []).append((int(m.group(1) or 0), m.group(3)))
    if "FAIL" in log or "failed" in log:
        return {"status": "failed", "rows": rows, "log": log}
    return {"status": "error", "error": "could not read the Yosys result"}


def _sets(sig: Signals, at_reset: bool) -> str:
    s = ""
    for p, val in FIXED.items():
        if p in sig.nl.ports:
            if p == "rst_n" and at_reset:
                s += " -set-at 1 rst_n 0"
            else:
                s += f" -set {p} {val}"
    return s


def counterexample(sig: Signals, rows: dict, used: set) -> dict:
    """{input bus: int, q_<id>: bit, each name the claim used: value} from Yosys' -show table (first time step)."""
    val = {k: int(v[0][1]) for k, v in rows.items() if v}
    cex = {p: val[p] for p, port in sig.ports.items() if port["dir"] == "in" and p not in FIXED and p in val}
    cex |= {k: v for k, v in val.items() if k.startswith("q_")}
    for kind, name in sorted(used):
        key = vname(kind, name)
        if key in val:
            cex[f"next({name})" if kind == "next" else name] = val[key]
    return cex


# ---------------------------------------------------------------------------------------------- the checker
def _decompiler() -> str:
    try:
        return subprocess.run(["git", "log", "-1", "--format=%h", "--", "."], capture_output=True, text=True,
                              cwd=Path(__file__).parent, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


DECOMPILER = _decompiler()


def netlist_sha(v) -> str:
    """Same hash as decap_agent.core_witness, so caches keyed on either agree."""
    raw = v.text if v.text else repr(sorted((i.id, i.cell.name if hasattr(i.cell, "name") else str(i.cell),
                                             sorted(i.conns.items(), key=str)) for i in v.nl.insts))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def region_outputs(outputs: list[dict]) -> set[tuple[str, str]]:
    want = set()
    for o in outputs:
        nm = o["name"]
        if nm.startswith("next("):
            want.add(("next", nm[5:-1]))
            want |= {("next", f) for f in o.get("flops", [])}
        else:
            want.add(("sig", nm))
    return want


def uncovered(sig: Signals, node: Node, outputs: list[dict]) -> list[str]:
    """Region output bits the claim never mentions, through any alias (as Ian's fallback: core_witness.uncovered)."""
    said = set()
    for kind, name, hi, lo in ref_bits(node):
        rng = range(sig.widths[name]) if hi is None else range(lo, hi + 1)
        if kind == "sig" and name in sig.ports:
            said |= {("P", name, k) for k in rng}
        elif kind == "next" and sig.nextable(name):
            fids = sig.fids(name)
            said |= {("D", fids[k]) for k in rng if k < len(fids)}
    missing = []
    for o in outputs:
        nm = o["name"]
        for k in o.get("bits_in_region", [0]):
            if o["kind"] == "port":
                key, lab = ("P", nm, k), f"{nm}[{k}]"
            elif o["kind"] == "register_next":
                flops = o.get("flops") or [f"q_{f}" for f in sig.fids(nm[5:-1])]
                key, lab = ("D", flops[k][2:]), (f"{nm}[{k}]" if len(flops) > 1 else nm)
            else:
                key, lab = ("D", nm[7:-1]), nm
            if key not in said:
                missing.append(lab)
    return missing


def check(v, claim: str, label: str = "", region: dict | None = None, do_check: bool = True,
          timeout: int = TIMEOUT) -> dict:
    """Check one claim. region: get_region's output for the claim's scope (outputs, cone_literals, touched);
    None = the whole chip (every output port and register)."""
    t0 = time.time()
    sig = Signals(v)
    reg = region or {}
    base = {"chip": v.id, "claim": claim, "label": label, "touched": reg.get("touched"),
            "netlist_sha": reg.get("netlist_sha") or netlist_sha(v), "decompiler": reg.get("decompiler") or DECOMPILER,
            "method": METHOD, "source": SOURCE}

    def done(**kw):
        return {**base, **kw, "seconds": round(time.time() - t0, 2)}

    try:
        node = parse(claim)
        ev = Evaluator(sig.widths)
        ev.check_names(node)
        for kind, name in names(node):
            if kind == "next" and not sig.nextable(name):
                raise ClaimSyntaxError(f"next() takes a register or flip-flop, not {name!r}")
            if kind == "sig" and name in FIXED:
                raise ClaimSyntaxError(f"{name} is fixed at {FIXED[name]} for every claim; leave it out")
    except ClaimSyntaxError as e:
        return done(verdict="unparseable", error=str(e))
    out = {"formal": unparse(formal(sig, node)), "english": english(node), "literals": literals(node)}
    if not do_check:
        return done(verdict="unchecked", **out)
    used = names(node)

    # 1. tautology: true of any chip
    t = _yosys(free_module(sig, node), "claimfree", [], f"-prove __ok 1 -timeout {timeout}", timeout)
    if t["status"] == "proven":
        return done(verdict="tautology", truth="tautology", **out, reason="true of any chip: it says nothing about this one")
    if t["status"] == "error":
        return done(verdict="unparseable", error=f"Yosys could not read the claim: {t['error']}", **out)

    # 2. registers free, one clock step
    gate, types = chip_module(sig, node, cut=True)
    show = ",".join(sorted({vname(k, n) for k, n in used} | {f"q_{f}" for f in sig.flops}))
    r = _yosys(gate, sig.nl.top, types, f"-prove __ok 1{_sets(sig, False)} -show-inputs -show {show} -timeout {timeout}", timeout)
    if r["status"] in ("timeout", "error"):
        return done(verdict="timeout" if r["status"] == "timeout" else "unparseable", **out, **({"error": r["error"]} if "error" in r else {}))
    truth, extra = "proven", {}
    if r["status"] == "failed":
        cex = counterexample(sig, r["rows"], used)
        if not sig.flops:
            return done(verdict="refuted", truth="refuted", **out, counterexample=cex, refute_kind="semantic")
        # 3. from reset: BMC, then induction
        seq_gate, seq_types = chip_module(sig, node, cut=False)
        b = _yosys(seq_gate, sig.nl.top, seq_types,
                   f"-seq {BMC_STEPS} -prove __ok 1{_sets(sig, True)} -show-inputs -show {show} -timeout {timeout}",
                   timeout, seq=True)
        if b["status"] == "failed":
            return done(verdict="refuted", truth="refuted", **out, counterexample=cex, refute_kind="semantic",
                        note=f"also fails within {BMC_STEPS} cycles of reset")
        if b["status"] != "proven":
            return done(verdict="timeout", truth="refuted_free", **out, counterexample=cex, refute_kind="reset_only?",
                        error=b.get("error", "BMC timed out"))
        k = _yosys(seq_gate, sig.nl.top, seq_types,
                   f"-tempinduct -prove __ok 1{_sets(sig, True)} -maxsteps {INDUCT_STEPS} -timeout {timeout}", timeout, seq=True)
        extra = {"counterexample": cex, "refute_kind": "reset_only",
                 "note": "false for some register values the chip never reaches from reset"}
        if k["status"] != "proven":
            return done(verdict="bounded", truth="bounded", **out, **extra,
                        reason=f"holds for {BMC_STEPS} cycles from reset, not proven beyond")
        truth = "inductive"

    # 4. credit
    lits, cone = out["literals"], (region or {}).get("cone_literals")
    if region is not None:
        want = region_outputs(region["outputs"])
    else:
        want = {("sig", o) for o in sig.outputs} | {("next", r) for r in sig.regs} | {("next", f"q_{f}") for f in sig.flops}
    on_topic = bool(used & want)
    missing = uncovered(sig, node, region["outputs"]) if on_topic and region is not None else []
    complete = False
    if on_topic and not missing:
        ob = output_bits(node, want, sig.widths)   # next(x) has x's width
        alt = {(k, n) for k, n, _ in ob}
        c = _yosys(free_module(sig, node, alt, ob), "claimfree", [], f"-prove __ok 1 -timeout {timeout}", timeout)
        complete = True if c["status"] == "proven" else False if c["status"] == "failed" else None
    shape = SHAPES.get(label)
    label_ok = shape is not None and (label == "constant" or _has_op(node, shape))
    short = cone is not None and lits * 3 <= cone
    cleared = on_topic and complete is True and (label_ok or short)
    reason = ("says nothing about this region's outputs" if not on_topic
              else f"does not cover every output of the region: missing {', '.join(missing[:6])}" if missing
              else "true, but it does not say what the output is for every input" if complete is False
              else "completeness check timed out" if complete is None
              else f"labelled shape: {label}" if label_ok
              else f"{lits} literals vs {cone} in the decompiled lines" if short
              else "no shorter than the decompile and no known shape")
    verdict = truth if cleared else "partial" if on_topic and complete is False else "restated"
    return done(verdict=verdict, truth=truth, **out, **extra,
                credit={"cleared": cleared, "complete": complete, **({"missing": missing} if missing else {}), "reason": reason, "literals": lits, "cone_literals": cone})
