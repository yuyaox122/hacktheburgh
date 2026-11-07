"""Witness routes on top of the REAL core, until the core ships its own. Lives in decap/agent/, edits no core file.

    cd <checkout with decap/data> && DECAP_CORE_APP=decap_agent.core_witness:app PYTHONPATH=$PWD decap explain CHIP --cone-of 'uo_out[0]'

Adds, only where the core has no route with the same operation_id:
  get_region     what a region computes: the observable outputs it feeds (output-port bits, flip-flop D pins),
                 the ports and flip-flops they depend on (the only names a claim may use), the decompiled lines
  probe_truth    random vectors through the core's bit-parallel simulator (every vector for <= 14 input bits)
  propose_claim  FALLBACK checker: simulation, NOT Yosys. Verdicts: refuted (a real counterexample: the netlist
                 simulator disagrees), tested (no counterexample; `exhaustive` says whether every input of the claim
                 was tried), tautology, unparseable, restated. It never says 'proven': only core/claims.py does.

Proof setup copied from the plan: rst_n = 1, ena = 1, registers free (any value), one clock step for next(x).
Names: ports (ui_in, uio_in, uo_out, uio_out, uio_oe, with [i] or [hi:lo]), flip-flops q_<cell id> (e.g. q_c20),
and the decompiler's register names (e.g. acc0) as aliases for their flip-flops.
"""

from __future__ import annotations

import hashlib
import itertools
import random
import re
import subprocess
from pathlib import Path

from fastapi import Body, HTTPException
from pydantic import BaseModel, Field

from decap.api.main import ScopeIn, _get, app  # noqa: F401  (re-exported: DECAP_CORE_APP=decap_agent.core_witness:app)
from decap.api.scope import resolve, touched
from decap.core.sim import Sim

from .claim_expr import ClaimSyntaxError, Evaluator, is_complete, literals, names, output_bits, parse, ref_bits, unparse
from .stub_witness import SHAPES, _has_op, english

FIXED = {"rst_n": 1, "ena": 1, "clk": 0}
EXHAUSTIVE_BITS = 14
SOURCE = "fallback-sim"
KEYWORDS = {"module", "input", "output", "wire", "reg", "assign", "always", "posedge", "negedge", "if", "else", "begin",
            "end", "endmodule", "case", "endcase", "default"}


class ProbeIn(ScopeIn):
    n: int = Field(1024, ge=1, le=4096, description="Random input vectors to try (every vector if the region has <= 14 input bits).")
    seed: int = Field(1, description="Random seed, so a probe can be repeated.")
    fix: dict[str, int] = Field(default_factory=dict, description="Hold some region inputs at fixed values while the rest vary, e.g. {\"bit0\": 0, \"ui_in\": 37}. Names as the region lists them.")


class ClaimIn(ScopeIn):
    claim: str = Field(description="One Verilog expression about the region, e.g. \"uo_out[4:0] == ui_in[3:0] + ui_in[7:4]\". "
                                   "Names: ports and flip-flops exactly as get_region lists them; next(x) = x one clock "
                                   "later; 'A when B' means B -> A. rst_n = 1 and ena = 1 are assumed.")
    label: str = Field("", description="Known shape, if any: adder, incrementer, counter, comparator, mux, decoder, constant.")
    check: bool = Field(True, description="False: only parse and render the English (the confirm step), no verdict.")


# ---------------------------------------------------------------------------------------------- chip model
class Model:
    """Nameable signals of one chip variant, as bit lists into the core's simulator."""

    def __init__(self, v):
        self.v, nl = v, v.nl
        self.s: Sim = v.dec.s
        self.ports = {n: p for n, p in nl.ports.items() if n != "clk"}
        self.flop_by_id = {f.id: f for f in nl.flops}
        self.regs = {name: fids for name, fids in v.res.get("registers", {}).items() if all(f in self.flop_by_id for f in fids)}
        self.sig: dict[str, list] = {n: list(p["bits"]) for n, p in self.ports.items()}
        self.nxt: dict[str, list] = {}
        for fid, f in self.flop_by_id.items():
            self.sig[f"q_{fid}"] = [f.conns.get("Q", "0")]
            self.nxt[f"q_{fid}"] = [f.conns.get("D", "0")]
        for name, fids in self.regs.items():
            self.sig[name] = [self.flop_by_id[f].conns.get("Q", "0") for f in fids]
            self.nxt[name] = [self.flop_by_id[f].conns.get("D", "0") for f in fids]
        self.widths = {n: len(b) for n, b in self.sig.items()}
        self.in_bit = {b: (n, k) for n, k, b in nl.in_bits()}
        self.q_of = {f.conns["Q"]: fid for fid, f in self.flop_by_id.items() if isinstance(f.conns.get("Q"), int)}

    def bits_of(self, kind: str, name: str) -> list:
        return (self.nxt if kind == "next" else self.sig)[name]

    def support(self, bits) -> set[int]:
        """Source bits (inputs, flip-flop Qs) these bits depend on, through combinational logic."""
        ints = [b for b in bits if isinstance(b, int)]
        _, sup = self.s.cone(ints)
        return {b for b in sup if not (b in self.in_bit and self.in_bit[b][0] in FIXED)}

    def run(self, varying: list[int], n: int, seed: int, fixed: dict[int, int] | None = None) -> tuple[dict, int, bool]:
        """Simulate: `varying` source bits get every combination (if few) or n random patterns; the rest are fixed."""
        fixed = fixed or {}
        varying = [b for b in varying if b not in fixed]
        exhaustive = len(varying) <= EXHAUSTIVE_BITS
        if exhaustive:
            env, mask = Sim.exhaustive_env(varying) if varying else ({}, 1)
        else:
            rng = random.Random(seed)
            mask = (1 << n) - 1
            env = {b: rng.getrandbits(n) for b in varying}
        for b, bit in fixed.items():
            env[b] = mask if bit else 0
        for b in self.s.sources:
            if b not in env:
                port = self.in_bit.get(b, (None,))[0]
                env[b] = mask if FIXED.get(port) == 1 else 0
        return self.s.run(env, mask), mask, exhaustive

    @staticmethod
    def value(vals: dict, bits: list, j: int) -> int:
        out = 0
        for k, b in enumerate(bits):
            bit = (1 if b == "1" else 0) if isinstance(b, str) else (vals.get(b, 0) >> j) & 1
            out |= bit << k
        return out


_models: dict[str, Model] = {}


def model(chip: str) -> Model:
    v = _get(chip)
    if chip not in _models:
        _models[chip] = Model(v)
    return _models[chip]


def _decompiler() -> str:
    try:
        return subprocess.run(["git", "log", "-1", "--format=%h", "--", "decap/core"], capture_output=True, text=True,
                              cwd=Path.cwd(), timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


DECOMPILER = _decompiler()


def netlist_sha(m: Model) -> str:
    raw = m.v.text if m.v.text else repr(sorted((i.id, i.cell.name if hasattr(i.cell, "name") else str(i.cell),
                                                 sorted(i.conns.items(), key=str)) for i in m.v.nl.insts))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------------------------- region
def region_of(chip: str, scope) -> dict:
    m = model(chip)
    v = m.v
    cells = resolve(v, scope)
    all_cells = None if cells is None else set(cells)
    observables = []   # (kind, name, bit index or None, bits)
    for pname, p in m.ports.items():
        if p["dir"] != "out":
            continue
        for k, b in enumerate(p["bits"]):
            observables.append(("sig", pname, k, [b]))
    for fid, f in m.flop_by_id.items():
        observables.append(("next", f"q_{fid}", None, [f.conns.get("D", "0")]))
    picked = []
    for kind, name, k, bits in observables:
        cone, _ = m.s.cone([b for b in bits if isinstance(b, int)])
        if all_cells is None or cone & all_cells or (kind == "next" and name[2:] in all_cells):
            if all_cells is None and not cone and not any(isinstance(b, int) for b in bits):
                continue   # tie-offs: nothing to explain
            picked.append((kind, name, k, bits, cone))
    if not picked:
        raise HTTPException(404, "the region feeds no output port or flip-flop")
    region_cells = set().union(*[c for *_, c in picked]) | (all_cells or set())
    sup = m.support([b for _, _, _, bits, _ in picked for b in bits])

    # outputs: output buses (with the bits in the region) and next() of registers / lone flops
    outs, seen = [], set()
    for kind, name, k, *_ in picked:
        if kind == "sig":
            if name not in seen:
                seen.add(name)
                ks = sorted(kk for kd, nm, kk, *_ in picked if kd == "sig" and nm == name)
                outs.append({"name": name, "width": m.widths[name], "kind": "port", "bits_in_region": ks})
    flops_next = {name[2:] for kind, name, *_ in picked if kind == "next"}
    used = set()
    for rname, fids in m.regs.items():
        if flops_next & set(fids):
            outs.append({"name": f"next({rname})", "width": len(fids), "kind": "register_next", "flops": [f"q_{f}" for f in fids],
                         "bits_in_region": [k for k, f in enumerate(fids) if f in flops_next]})
            used |= set(fids)
    for fid in sorted(flops_next - used, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0):
        outs.append({"name": f"next(q_{fid})", "width": 1, "kind": "flop_next"})

    ins, used = [], set()
    for pname, p in m.ports.items():
        if p["dir"] == "in" and pname not in FIXED and any(b in sup for b in p["bits"]):
            ins.append({"name": pname, "width": len(p["bits"]), "kind": "port",
                        "bits_used": [k for k, b in enumerate(p["bits"]) if b in sup]})
    sup_flops = {m.q_of[b] for b in sup if b in m.q_of}
    for rname, fids in m.regs.items():
        if sup_flops & set(fids):
            ins.append({"name": rname, "width": len(fids), "kind": "register", "flops": [f"q_{f}" for f in fids]})
            used |= set(fids)
    for fid in sorted(sup_flops - used, key=lambda x: int(x[1:]) if x[1:].isdigit() else 0):
        ins.append({"name": f"q_{fid}", "width": 1, "kind": "flop"})

    t = touched(v, region_cells) if region_cells else {"cells": [], "nets": [], "lines": []}
    text = v.result["decompiled"]["verilog"].splitlines()
    focus = [{"line": n, "text": text[n - 1].strip()} for n in expand_focus(text, t["lines"])]
    lits = count_literals([f["text"] for f in focus])
    return {"inputs": ins, "outputs": outs, "input_bits": len(sup), "focus": focus, "cone_literals": lits,
            "cells": len(region_cells), "touched": t, "_sup": sup, "_cells": region_cells}


def expand_focus(text: list[str], lines: list[int]) -> list[int]:
    """The core maps cells to a statement's FIRST line; include the rest of a multi-line statement."""
    keep = set()
    for n in lines:
        if not 0 < n <= len(text):
            continue
        keep.add(n)
        indent = len(text[n - 1]) - len(text[n - 1].lstrip())
        k = n + 1
        while k <= len(text) and text[k - 1].strip() and len(text[k - 1]) - len(text[k - 1].lstrip()) > indent:
            keep.add(k)
            k += 1
    return sorted(keep)


NUM = re.compile(r"\d*'[sS]?[bBoOdDhH][0-9a-fA-F_xXzZ]+|\b\d+\b")


def count_literals(lines: list[str]) -> int:
    return sum(len([w for w in re.findall(r"[A-Za-z_]\w*", NUM.sub(" ", t)) if w not in KEYWORDS]) for t in lines)


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


def _public(r: dict) -> dict:
    return {k: x for k, x in r.items() if not k.startswith("_")}


# ---------------------------------------------------------------------------------------------- routes
def _has(op: str) -> bool:
    return any(getattr(r, "operation_id", None) == op for r in app.routes)


if not _has("get_region"):
    @app.post("/api/chips/{chip}/region", operation_id="get_region")
    def get_region(chip: str, body: ScopeIn = Body(default_factory=ScopeIn)) -> dict:
        """Describe a region before making a claim about it: the outputs it feeds (output-port bits, flip-flops'
        next values), the ports and flip-flops they depend on (the only names a claim may use), the decompiled lines
        it covers and how many literals they have. rst_n = 1 and ena = 1 are assumed. Call this first."""
        m = model(chip)
        r = region_of(chip, body.scope)
        return {"chip": chip, **_public(r), "assumed": {"rst_n": 1, "ena": 1}, "netlist_sha": netlist_sha(m),
                "decompiler": DECOMPILER,
                "names_note": "Claims may use these ports (with [i] or [hi:lo]), register names, flip-flops q_<id>, "
                              "and next(x) for a register or flip-flop one clock later.", "source": SOURCE}

if not _has("probe_truth"):
    @app.post("/api/chips/{chip}/probe", operation_id="probe_truth")
    def probe_truth(chip: str, body: ProbeIn = Body(default_factory=ProbeIn)) -> dict:
        """Try many input vectors on the region and show what its outputs did (every vector if the region has few
        input bits, otherwise random). Free to call. Samples are evidence, not proof: a pattern that held on samples
        can still be false, and only propose_claim decides."""
        m = model(chip)
        r = region_of(chip, body.scope)
        fixed = {}
        for name, value in body.fix.items():
            if name not in m.sig or name in FIXED or any(o["name"] == name for o in r["outputs"]):
                raise HTTPException(400, f"fix: {name!r} is not an input of this region; use the names get_region lists")
            for k, b in enumerate(m.sig[name]):
                if isinstance(b, int):
                    fixed[b] = (value >> k) & 1
        vals, mask, exhaustive = m.run(sorted(r["_sup"]), body.n, body.seed, fixed)
        npat = mask.bit_length()
        in_names = [(i["name"], m.sig[i["name"]]) for i in r["inputs"]]
        out_names = []
        for o in r["outputs"]:
            nm = o["name"]
            out_names.append((nm, m.nxt[nm[5:-1]] if nm.startswith("next(") else m.sig[nm]))
        rows = [{"in": {n: Model.value(vals, b, j) for n, b in in_names},
                 "out": {n: Model.value(vals, b, j) for n, b in out_names}} for j in range(npat)]
        stats = {}
        for o in r["outputs"]:
            if o["kind"] != "port":
                continue
            for k in o["bits_in_region"]:
                b = m.sig[o["name"]][k]
                ones = bin(vals.get(b, 0) & mask).count("1") if isinstance(b, int) else (npat if b == "1" else 0)
                st = {"ones": ones, "of": npat}
                if 0 < ones <= max(1, npat // 16):
                    st["rows_where_1"] = [rows[j]["in"] for j in range(npat) if isinstance(b, int) and (vals[b] >> j) & 1][:4]
                stats[f"{o['name']}[{k}]"] = st
        shown = random.Random(body.seed).sample(rows, min(48, len(rows)))
        return {"chip": chip, "exhaustive": exhaustive, "vectors": npat, "input_bits": len(r["_sup"]), "fixed": body.fix,
                "samples": shown,
                "stats": stats, "assumed": {"rst_n": 1, "ena": 1, "registers": "free (any value)"},
                "note": "Simulation samples of the netlist: what the chip did on these inputs. Not a proof.",
                "touched": r["touched"], "source": SOURCE}

if not _has("propose_claim"):
    @app.post("/api/chips/{chip}/claim", operation_id="propose_claim")
    def propose_claim(chip: str, body: ClaimIn) -> dict:
        """Submit one claim about the region to the checker. This is the FALLBACK checker (netlist simulation, not
        Yosys): refuted (with a counterexample the netlist really produces), tested (no counterexample found;
        'exhaustive' says whether every input was tried), tautology, unparseable, restated (no shorter than the
        decompile). It never returns 'proven'."""
        m = model(chip)
        r = region_of(chip, body.scope)
        base = {"chip": chip, "claim": body.claim, "label": body.label, "touched": r["touched"],
                "netlist_sha": netlist_sha(m), "decompiler": DECOMPILER,
                "method": "netlist simulation, rst_n=1 ena=1, registers free (fallback checker, not Yosys)", "source": SOURCE}
        try:
            node = parse(body.claim)
            ev = Evaluator(m.widths)
            ev.check_names(node)
            for kind, name in names(node):
                if kind == "next" and name not in m.nxt:
                    raise ClaimSyntaxError(f"next() takes a register or flip-flop, not {name!r}")
        except ClaimSyntaxError as e:
            return {**base, "verdict": "unparseable", "error": str(e)}
        out = {"formal": unparse(node), "english": english(node), "literals": literals(node)}
        if not body.check:
            return {**base, "verdict": "unchecked", **out}
        refs = sorted(names(node))
        free = sum(m.widths[n] for _, n in refs)
        if free <= 16 and all(ev.holds(node, dict(zip(refs, vs)))
                              for vs in itertools.product(*[range(1 << m.widths[n]) for _, n in refs])):
            return {**base, "verdict": "tautology", **out, "reason": "true of any chip: it says nothing about this one"}
        sel = []
        for k, n, hi, lo in ref_bits(node):
            bits = m.bits_of(k, n)
            sel += bits if hi is None else bits[lo:hi + 1]
        sup = m.support(sel)
        # bits read directly (inputs, flip-flop Qs) are sources too
        sup |= {b for b in sel if isinstance(b, int) and (b in m.in_bit or b in m.q_of)
                and not (b in m.in_bit and m.in_bit[b][0] in FIXED)}
        varying = sorted(sup)
        vals, mask, exhaustive = m.run(varying, 4096, 7)
        envs = []
        for j in range(mask.bit_length()):
            env = {(k, n): Model.value(vals, m.bits_of(k, n), j) for k, n in refs}
            if len(envs) < 48 and (j * 7919) % 5 == 0:
                envs.append(env)
            if not ev.holds(node, env):
                cex = {}
                for b in varying:
                    if b in m.in_bit:
                        pn, kk = m.in_bit[b]
                        cex[pn] = cex.get(pn, 0) | (((vals.get(b, 0) >> j) & 1) << kk)
                    elif b in m.q_of:
                        cex[f"q_{m.q_of[b]}"] = (vals.get(b, 0) >> j) & 1
                for k, n in refs:
                    cex.setdefault(f"next({n})" if k == "next" else n, env[(k, n)])
                return {**base, "verdict": "refuted", **out, "counterexample": cex,
                        "refute_kind": "unclassified (fallback: no reset BMC)"}
        lits, cone = out["literals"], r["cone_literals"]
        shape = SHAPES.get(body.label)
        label_ok = shape is not None and (body.label == "constant" or _has_op(node, shape))
        want = region_outputs(r["outputs"])
        on_topic = bool(names(node) & want)
        complete = is_complete(ev, node, envs or [{}], output_bits(node, want, m.widths)) if on_topic else False
        cleared = on_topic and bool(complete) and (label_ok or lits * 3 <= cone)
        reason = ("says nothing about this region's outputs" if not on_topic
                  else "true, but it does not say what the output is for every input" if complete is False
                  else "too many output bits to check completeness here (the Yosys checker can)" if complete is None
                  else f"labelled shape: {body.label}" if label_ok else f"{lits} literals vs {cone} in the decompiled lines"
                  if cleared else "no shorter than the decompile and no known shape")
        verdict = "tested" if cleared else "partial" if on_topic and not complete else "restated"
        return {**base, "verdict": verdict, **out, "exhaustive": exhaustive, "vectors": mask.bit_length(),
                "credit": {"cleared": cleared, "complete": complete, "reason": reason, "literals": lits, "cone_literals": cone}}
