"""A FALLBACK propose_claim on top of the real core, until core/claims.py (Yuyao's, Yosys) lands. Edits no core file.

    cd <checkout with decap/data> && DECAP_CORE_APP=decap_agent.core_witness:app PYTHONPATH=$PWD decap explain CHIP --cone-of 'uo_out[0]'

get_region and probe_truth are core routes now (decap/api/scope.py). This module only adds propose_claim, and only
if the core has none with that operation_id. It decides by netlist simulation, NOT Yosys:
  refuted     a real counterexample: the netlist simulator disagrees with the claim
  tested      no counterexample; `exhaustive` says whether every input of the claim was tried
  partial     true on the vectors, but it doesn't pin down the output bits it names for every input
  restated    no shorter than the decompile (or says nothing about the region's outputs)
  tautology / unparseable
It never says 'proven': only core/claims.py does. Setup as in the plan: rst_n = 1, ena = 1, registers free.
"""

from __future__ import annotations

import itertools

from pydantic import Field

from decap.api.main import ScopeIn, _get, app  # noqa: F401  (re-exported: DECAP_CORE_APP=decap_agent.core_witness:app)
from decap.api.scope import DECOMPILER, FIXED, Model, model, netlist_sha, region_of

from decap.core.claim_expr import (SHAPES, ClaimSyntaxError, Evaluator, _has_op, english, is_complete, literals, names,
                                   output_bits, parse, ref_bits, unparse)

SOURCE = "fallback-sim"


class ClaimIn(ScopeIn):
    claim: str = Field(description="One Verilog expression about the region, e.g. \"uo_out[4:0] == ui_in[3:0] + ui_in[7:4]\". "
                                   "Names: ports and flip-flops exactly as get_region lists them; next(x) = x one clock "
                                   "later; 'A when B' means B -> A. rst_n = 1 and ena = 1 are assumed.")
    label: str = Field("", description="Known shape, if any: adder, incrementer, counter, comparator, mux, decoder, constant.")
    check: bool = Field(True, description="False: only parse and render the English (the confirm step), no verdict.")


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


def uncovered(m: Model, node, outputs: list[dict]) -> list[str]:
    """Region output bits the claim never mentions (through any alias): a claim must explain the whole region."""
    mentioned = set()
    for kind, name, hi, lo in ref_bits(node):
        rng = range(m.widths[name]) if hi is None else range(lo, hi + 1)
        for k in rng:
            if kind == "sig" and name in m.ports:
                mentioned.add(("P", name, k))
            elif kind == "next" and name in m.regs:
                mentioned.add(("D", m.regs[name][k]))
            elif kind == "next" and name.startswith("q_"):
                mentioned.add(("D", name[2:]))
    missing = []
    for o in outputs:
        nm = o["name"]
        for k in o.get("bits_in_region", [0]):
            if o["kind"] == "port":
                key, label = ("P", nm, k), f"{nm}[{k}]"
            elif o["kind"] == "register_next":
                key, label = ("D", o["flops"][k][2:]), (f"{nm}[{k}]" if o["width"] > 1 else nm)
            else:
                key, label = ("D", nm[7:-1]), nm
            if key not in mentioned:
                missing.append(label)
    return missing


def check_claim(chip: str, body: ClaimIn, mode: str = "biased", n: int = 4096) -> dict:
    """mode: 'biased' (the core's mostly-1/mostly-0 patterns) or 'random' (plain Monte Carlo)."""
    v = _get(chip)
    m: Model = model(v)
    r = region_of(v, body.scope)
    base = {"chip": chip, "claim": body.claim, "label": body.label, "touched": r["touched"], "netlist_sha": netlist_sha(v),
            "decompiler": DECOMPILER, "source": SOURCE,
            "method": f"netlist simulation ({mode}, n={n}), rst_n=1 ena=1, registers free (fallback checker, not Yosys)"}
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
    if sum(m.widths[n] for _, n in refs) <= 16 and all(
            ev.holds(node, dict(zip(refs, vs))) for vs in itertools.product(*[range(1 << m.widths[n]) for _, n in refs])):
        return {**base, "verdict": "tautology", "truth": "tautology", **out, "reason": "true of any chip: it says nothing about this one"}

    sel = []
    for kind, name, hi, lo in ref_bits(node):   # not k/n: n is the vector count
        bits = m.bits_of(kind, name)
        sel += bits if hi is None else bits[lo:hi + 1]
    sup = m.support(sel) | {b for b in sel if isinstance(b, int) and (b in m.in_bit or b in m.q_of)
                            and not (b in m.in_bit and m.in_bit[b][0] in FIXED)}
    varying = sorted(sup)
    vals, mask, exhaustive = m.run(varying, n, 7, biased=(mode == "biased"))
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
                    fid, bit = m.q_of[b], (vals.get(b, 0) >> j) & 1
                    reg = next(((rn, fids.index(fid)) for rn, fids in m.regs.items() if fid in fids), None)
                    if reg:   # group register bits under the register's name
                        cex[reg[0]] = cex.get(reg[0], 0) | (bit << reg[1])
                    else:
                        cex[f"q_{fid}"] = bit
            for kind, name in refs:
                cex.setdefault(f"next({name})" if kind == "next" else name, env[(kind, name)])
            return {**base, "verdict": "refuted", "truth": "refuted", **out, "counterexample": cex,
                    "refute_kind": "unclassified (fallback: no reset BMC)"}

    lits, cone = out["literals"], r["cone_literals"]
    shape = SHAPES.get(body.label)
    label_ok = shape is not None and (body.label == "constant" or _has_op(node, shape))
    want = region_outputs(r["outputs"])
    on_topic = bool(names(node) & want)
    missing = uncovered(m, node, r["outputs"]) if on_topic else []
    complete = (is_complete(ev, node, envs or [{}], output_bits(node, want, m.widths)) and not missing) if on_topic else False
    cleared = on_topic and bool(complete) and (label_ok or lits * 3 <= cone)
    reason = ("says nothing about this region's outputs" if not on_topic
              else f"does not cover every output of the region: missing {', '.join(missing[:6])}" if missing
              else "true, but it does not say what the output is for every input" if complete is False
              else "too many output bits to check completeness here (the Yosys checker can)" if complete is None
              else f"labelled shape: {body.label}" if label_ok else f"{lits} literals vs {cone} in the decompiled lines"
              if cleared else "no shorter than the decompile and no known shape")
    verdict = "tested" if cleared else "partial" if on_topic and complete is False else "restated"
    return {**base, "verdict": verdict, "truth": "tested", **out, "exhaustive": exhaustive, "vectors": mask.bit_length(),
            "credit": {"cleared": cleared, "complete": complete, "reason": reason, "literals": lits, "cone_literals": cone}}


if not any(getattr(r, "operation_id", None) == "propose_claim" for r in app.routes):
    @app.post("/api/chips/{chip}/claim", operation_id="propose_claim")
    def propose_claim(chip: str, body: ClaimIn) -> dict:
        """Submit one claim about the region to the checker. This is the FALLBACK checker (netlist simulation, not
        Yosys): refuted (with a counterexample the netlist really produces), tested (no counterexample found;
        'exhaustive' says whether every input was tried), partial (true but leaves the output open for some inputs),
        restated (no shorter than the decompile), tautology, unparseable. It never returns 'proven'."""
        return check_claim(chip, body)
