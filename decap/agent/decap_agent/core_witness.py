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

from .claim_expr import ClaimSyntaxError, Evaluator, is_complete, literals, names, output_bits, parse, ref_bits, unparse
from .stub_witness import SHAPES, _has_op, english

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


def check_claim(chip: str, body: ClaimIn) -> dict:
    v = _get(chip)
    m: Model = model(v)
    r = region_of(v, body.scope)
    base = {"chip": chip, "claim": body.claim, "label": body.label, "touched": r["touched"], "netlist_sha": netlist_sha(v),
            "decompiler": DECOMPILER, "source": SOURCE,
            "method": "netlist simulation, rst_n=1 ena=1, registers free (fallback checker, not Yosys)"}
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
        return {**base, "verdict": "tautology", **out, "reason": "true of any chip: it says nothing about this one"}

    sel = []
    for k, n, hi, lo in ref_bits(node):
        bits = m.bits_of(k, n)
        sel += bits if hi is None else bits[lo:hi + 1]
    sup = m.support(sel) | {b for b in sel if isinstance(b, int) and (b in m.in_bit or b in m.q_of)
                            and not (b in m.in_bit and m.in_bit[b][0] in FIXED)}
    varying = sorted(sup)
    vals, mask, exhaustive = m.run(varying, 4096, 7, biased=True)
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
    verdict = "tested" if cleared else "partial" if on_topic and complete is False else "restated"
    return {**base, "verdict": verdict, **out, "exhaustive": exhaustive, "vectors": mask.bit_length(),
            "credit": {"cleared": cleared, "complete": complete, "reason": reason, "literals": lits, "cone_literals": cone}}


if not any(getattr(r, "operation_id", None) == "propose_claim" for r in app.routes):
    @app.post("/api/chips/{chip}/claim", operation_id="propose_claim")
    def propose_claim(chip: str, body: ClaimIn) -> dict:
        """Submit one claim about the region to the checker. This is the FALLBACK checker (netlist simulation, not
        Yosys): refuted (with a counterexample the netlist really produces), tested (no counterexample found;
        'exhaustive' says whether every input was tried), partial (true but leaves the output open for some inputs),
        restated (no shorter than the decompile), tautology, unparseable. It never returns 'proven'."""
        return check_claim(chip, body)
