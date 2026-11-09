"""A simulation checker for claims on the real core: the bench ablation's rival to Yosys (`decap bench --ablate`).

    check_claim(chip, ClaimIn(claim=..., scope=...), mode="biased" | "random", n=4096)

It was the fallback propose_claim until the core's checker (core/claims.py, Yosys) landed; propose_claim is the
core's now, and this module re-exports the core app unchanged (DECAP_CORE_APP=decap_agent.core_witness:app still
works). It decides by netlist simulation, NOT Yosys:
  refuted     a real counterexample: the netlist simulator disagrees with the claim
  tested      no counterexample; `exhaustive` says whether every input of the claim was tried
  partial     true on the vectors, but it doesn't pin down the output bits it names for every input
  restated    no shorter than the decompile (or says nothing about the region's outputs)
  tautology / unparseable
It never says 'proven': only core/claims.py does. Parsing, the English, the coverage rule and the credit rule are
the core checker's own (core/claim_expr.py, core/claims.py). Setup as in the plan: rst_n = 1, ena = 1, registers free.
"""

from __future__ import annotations

from decap.api.main import ClaimIn, _get, app  # noqa: F401  (re-exported: DECAP_CORE_APP=decap_agent.core_witness:app)
from decap.api.scope import FIXED, Model, model, region_of
from decap.core.claim_expr import ClaimSyntaxError, is_complete, is_tautology, names, output_bits, parse_checked, readings, ref_bits
from decap.core.claims import DECOMPILER, credit, netlist_sha, region_outputs, uncovered

SOURCE = "fallback-sim"


def check_claim(chip: str, body: ClaimIn, mode: str = "biased", n: int = 4096) -> dict:
    """mode: 'biased' (the core's mostly-1/mostly-0 patterns) or 'random' (plain Monte Carlo)."""
    v = _get(chip)
    m: Model = model(v)
    r = region_of(v, body.scope)
    base = {"chip": chip, "claim": body.claim, "label": body.label, "touched": r["touched"], "netlist_sha": netlist_sha(v),
            "decompiler": DECOMPILER, "source": SOURCE,
            "method": f"netlist simulation ({mode}, n={n}), rst_n=1 ena=1, registers free (fallback checker, not Yosys)"}
    try:
        node, ev = parse_checked(body.claim, m.widths, m.nextable)
    except ClaimSyntaxError as e:
        return {**base, "verdict": "unparseable", "error": str(e)}
    out = readings(node)
    if not body.check:
        return {**base, "verdict": "unchecked", **out}
    if is_tautology(ev, node):
        return {**base, "verdict": "tautology", "truth": "tautology", **out, "reason": "true of any chip: it says nothing about this one"}

    refs = sorted(names(node))
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
    want = region_outputs(r["outputs"])
    on_topic = bool(names(node) & want)
    missing = uncovered(m, node, r["outputs"]) if on_topic else []
    complete = (is_complete(ev, node, envs or [{}], output_bits(node, want, m.widths)) and not missing) if on_topic else False
    cleared, reason = credit(node, body.label, on_topic, missing, complete, cone,
                             unknown="too many output bits to check completeness here (the Yosys checker can)")
    verdict = "tested" if cleared else "partial" if on_topic and complete is False else "restated"
    return {**base, "verdict": verdict, "truth": "tested", **out, "exhaustive": exhaustive, "vectors": mask.bit_length(),
            "credit": {"cleared": cleared, "complete": complete, "reason": reason, "literals": lits, "cone_literals": cone}}
