"""Witness bench driver (witness-plan section 4): the measured number.

    decap bench --split dev  [--max-regions 4] [--model cli:sonnet]     dry run (dev chips only; tune on these)
    decap bench --freeze-hash                                            print the hash to post before scoring
    decap bench --split test ...                                         ONLY after the freeze (02:30), never retuned

Regions are chosen by a fixed rule, before any test chip is seen: each block the decompiler's templates could not
name (recover_blocks: recognised = false), largest first, at most --max-regions per chip, scope {"cone_of": block id}.
One region agent per region (witness.explain_region); its attempt 0 is the FIRST DRAFT: made with a simulator and
no proof. That is the realistic rival the plan names.

Reported (summary):
  first_draft     how many first drafts were true / refuted (semantic | reset-only) / partial / restated / ...
  error_rate      semantic refutations / first drafts with a verdict, with a bootstrap 95% CI clustered by chip
  coverage        unrecognised cells in regions whose final claim was credited / all unrecognised cells
  compression     median cone_literals / literals over credited claims
  honesty         restated, partial, tautology, unparseable, timeout, no_claim counts and chips
Decision rule (fixed in the plan): if semantic first-draft error >= 10% with n >= 30 claims it is the headline,
otherwise coverage is.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import statistics
import time
from pathlib import Path
from typing import Any

from fastmcp import Client, FastMCP

from .transcript import Transcript, result_payload
from .witness import DONE, INSTRUCTIONS, PROMPT_VERSION, explain_region

PKG = Path(__file__).resolve().parent
TRUE = {"proven", "inductive"}


async def _call(server: FastMCP, tool: str, **args) -> Any:
    async with Client(server) as c:
        r = await c.call_tool(tool, args, raise_on_error=False)
        payload = result_payload(r)
        if r.is_error:
            raise RuntimeError(f"{tool} failed: {payload}")
        return payload


async def regions_for(server: FastMCP, chip: str, max_regions: int) -> tuple[list[dict], int]:
    """The fixed region rule. Returns (regions, unrecognised cells on the chip)."""
    blocks = (await _call(server, "recover_blocks", chip=chip))["blocks"]
    unrec = [b for b in blocks if not b.get("recognised")]
    size = lambda b: len(b["cells"]) if isinstance(b["cells"], list) else int(b["cells"])
    total = sum(size(b) for b in unrec)
    picked = sorted(unrec, key=lambda b: (-size(b), b["id"]))[:max_regions]
    return [{"block": b["id"], "kind": b["kind"], "cells": size(b), "scope": {"cone_of": b["id"]}} for b in picked], total


def bootstrap_ci(per_chip: dict[str, tuple[int, int]], reps: int = 10000, seed: int = 0) -> tuple[float, float] | None:
    """95% CI of sum(errors)/sum(claims), resampling chips with replacement (claims within a chip are correlated)."""
    chips = [c for c, (_, n) in per_chip.items() if n]
    if not chips:
        return None
    rng = random.Random(seed)
    stats = []
    for _ in range(reps):
        sample = [per_chip[rng.choice(chips)] for _ in chips]
        n = sum(x[1] for x in sample)
        stats.append(sum(x[0] for x in sample) / n if n else 0.0)
    stats.sort()
    return round(stats[int(0.025 * reps)], 4), round(stats[int(0.975 * reps) - 1], 4)


def summarise(rows: list[dict], unrec_cells: dict[str, int]) -> dict:
    firsts = [r for r in rows if r.get("first")]
    def kind(a: dict) -> str:
        t, v = a.get("truth"), a.get("verdict")
        if v == "no_claim":
            return "no_claim"
        if t == "refuted" or (t is None and v == "refuted"):
            return "refuted_semantic" if (a.get("refute_kind") or "semantic") == "semantic" else "refuted_reset_only"
        if v in ("partial", "restated", "tautology", "unparseable", "timeout", "bounded", "tested"):
            return v
        return "true_credited" if v in TRUE else f"other:{v}"
    first_kinds = [kind(r["first"]) for r in firsts]
    counts = {k: first_kinds.count(k) for k in sorted(set(first_kinds))}
    judged = [r for r in firsts if kind(r["first"]) not in ("no_claim", "unparseable", "timeout")]
    per_chip: dict[str, tuple[int, int]] = {}
    for r in judged:
        e, n = per_chip.get(r["chip"], (0, 0))
        per_chip[r["chip"]] = (e + (kind(r["first"]) == "refuted_semantic"), n + 1)
    n = sum(x[1] for x in per_chip.values())
    errors = sum(x[0] for x in per_chip.values())
    credited = [r for r in rows if r.get("explained")]
    covered = sum(r["cells"] for r in credited)
    total_unrec = sum(unrec_cells.values())
    ratios = [r["cone_literals"] / r["literals"] for r in credited if r.get("literals") and r.get("cone_literals")]
    honesty = {}
    for r in rows:
        k = kind(r["final"]) if r.get("final") else "error"
        if k not in ("true_credited", "tested"):
            honesty.setdefault(k, []).append(f"{r['chip']}:{r['block']}")
    rate = errors / n if n else None
    reset_only = sum(1 for r in firsts if r["first"].get("refute_kind") == "reset_only")
    return {
        "claims": n, "chips": len(per_chip), "first_draft": counts, "first_draft_reset_only": reset_only,
        "error_rate_semantic": round(rate, 4) if rate is not None else None,
        "error_ci95_clustered": bootstrap_ci(per_chip),
        "regions": len(rows), "explained_regions": len(credited),
        "coverage": {"credited_cells": covered, "unrecognised_cells": total_unrec,
                     "share": round(covered / total_unrec, 4) if total_unrec else None,
                     "no_gap_chips": sorted(c for c, v in unrec_cells.items() if v == 0)},
        "compression_median": round(statistics.median(ratios), 2) if ratios else None,
        "honesty": {k: {"count": len(v), "where": v} for k, v in sorted(honesty.items())},
        "headline": ("first-draft semantic error rate" if rate is not None and rate >= 0.10 and n >= 30
                     else "explained coverage"),
    }


def freeze_hash(model_name: str, split_path: Path | None, decompiler: str) -> dict:
    """SHA-256 over the agent code + prompts, the model id, the split and the decompiler commit (post before scoring)."""
    h = hashlib.sha256()
    parts = {}
    for f in sorted(PKG.glob("*.py")):
        data = f.read_bytes()
        h.update(f.name.encode() + data)
        parts[f.name] = hashlib.sha256(data).hexdigest()[:12]
    for label, value in (("model", model_name), ("prompt", PROMPT_VERSION), ("instructions", INSTRUCTIONS),
                         ("decompiler", decompiler)):
        h.update(f"{label}={value}".encode())
    if split_path and split_path.exists():
        h.update(split_path.read_bytes())
        parts["split.json"] = hashlib.sha256(split_path.read_bytes()).hexdigest()[:12]
    return {"sha256": h.hexdigest(), "model": model_name, "prompt": PROMPT_VERSION, "decompiler": decompiler, "files": parts}


async def run_bench(server: FastMCP, chips: list[str], *, model: Any = None, max_regions: int = 4, max_refine: int = 2,
                    transcript: Transcript | None = None, cache_dir: Path | None = None, out_path: Path | None = None,
                    concurrency: int = 3, log=print) -> dict:
    t0 = time.perf_counter()
    plan, unrec = [], {}
    for chip in chips:
        regions, total = await regions_for(server, chip, max_regions)
        unrec[chip] = total
        plan += [{"chip": chip, **r} for r in regions]
    log(f"[bench] {len(chips)} chips, {len(plan)} regions (max {max_regions}/chip)")
    sem = asyncio.Semaphore(concurrency)
    rows: list[dict] = []

    async def one(p: dict) -> dict:
        async with sem:
            try:
                run = await explain_region(p["chip"], p["scope"], server, model=model, transcript=transcript,
                                           max_refine=max_refine, cache_dir=cache_dir, register=False)
            except Exception as e:  # a crash is reported, never retried with changes
                row = {**p, "error": f"{type(e).__name__}: {str(e)[:300]}", "first": None, "final": None, "explained": False}
                log(f"[bench] {p['chip']}:{p['block']} ERROR {row['error'][:120]}")
                return row
        a0, af = run["attempts"][0], run["attempts"][-1]
        row = {**p, "first": {k: a0.get(k) for k in ("claim", "verdict", "truth", "refute_kind", "label")},
               "final": {k: af.get(k) for k in ("claim", "verdict", "truth", "refute_kind", "label", "english")},
               "attempts": len(run["attempts"]), "explained": run["explained"], "proof": run.get("proof"),
               "literals": (af.get("credit") or {}).get("literals"), "cone_literals": run.get("cone_literals"),
               "model": run["model"], "cached": run.get("cached", False),
               "tokens_in": sum(a.get("input_tokens") or 0 for a in run["attempts"]),
               "wall_s": round(sum(a.get("wall_s") or 0 for a in run["attempts"]), 2)}
        log(f"[bench] {p['chip']}:{p['block']} first={a0.get('verdict')}/{a0.get('truth')} final={af.get('verdict')} "
            f"attempts={row['attempts']} {row['wall_s']}s")
        return row

    rows = await asyncio.gather(*[one(p) for p in plan])
    result = {"chips": chips, "max_regions": max_regions, "prompt": PROMPT_VERSION,
              "summary": summarise(list(rows), unrec), "unrecognised_cells": unrec, "rows": list(rows),
              "wall_s": round(time.perf_counter() - t0, 1)}
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=1))
    return result


# ------------------------------------------------------------------------------------------------- ablation
def ablate(events: list[dict], modes=(("random", 1024), ("biased", 4096))) -> dict:
    """Re-check every claim an agent made (transcript kind='claim' events) with simulation instead of Yosys.

    Needs the real core importable (PYTHONPATH=<checkout>). No LLM calls. Answers: of the claims Yosys refuted,
    how many would plain random testing (Monte Carlo) or biased testing have passed?"""
    from .core_witness import ClaimIn, check_claim
    rows, seen = [], set()
    for e in events:
        if e.get("kind") != "claim" or not e.get("claim") or e.get("source") not in (None, "yosys", "core"):
            continue
        key = (e["chip"], json.dumps(e.get("scope"), sort_keys=True), e["claim"])
        if key in seen:
            continue
        seen.add(key)
        row = {"chip": e["chip"], "scope": e.get("scope"), "claim": e["claim"], "first_draft": e.get("first_draft"),
               "yosys": e.get("truth") or e.get("verdict")}
        for mode, n in modes:
            try:
                r = check_claim(e["chip"], ClaimIn(claim=e["claim"], scope=e.get("scope") or {}, label=e.get("label") or ""),
                                mode=mode, n=n)
                row[f"{mode}{n}"] = r.get("truth") or r.get("verdict")
            except Exception as ex:  # report, don't hide
                row[f"{mode}{n}"] = f"error: {type(ex).__name__}"
        if row["yosys"] == "refuted":
            row["sim_agrees_at_yosys_cex"] = _replay_cex(e)
        rows.append(row)
    refuted = [r for r in rows if r["yosys"] == "refuted"]
    comparable = [r for r in refuted if r.get("sim_agrees_at_yosys_cex") is True]
    out = {"claims": len(rows), "yosys_refuted": len(refuted),
           "comparable": len(comparable),
           "model_mismatch": [f"{r['chip']}: simulator disagrees with Yosys at its counterexample (e.g. a cut loop)"
                              for r in refuted if r.get("sim_agrees_at_yosys_cex") is not True],
           "rows": rows}
    for mode, n in modes:
        k = f"{mode}{n}"
        missed = [r for r in comparable if r[k] != "refuted"]
        out[f"{k}_passed_false_claims"] = f"{len(missed)}/{len(comparable)}"
        out[f"{k}_missed"] = [f"{r['chip']}: {r['claim'][:90]}" for r in missed]
        out[f"{k}_disagrees_with_proof"] = sum(1 for r in rows if r["yosys"] in TRUE and r[k] == "refuted")
    return out


def _replay_cex(e: dict) -> bool | None:
    """Evaluate the claim in the core's simulator at Yosys's counterexample. True: the simulator also finds it false
    there, so a sampling miss is a genuine miss. False: the two models disagree (not a fair comparison)."""
    from decap.api.main import _get
    from decap.api.scope import Model, model
    from decap.core.claim_expr import Evaluator, parse
    from decap.core.claim_expr import names as claim_names
    cex = e.get("counterexample") or {}
    try:
        m = model(_get(e["chip"]))
        fixed = {}
        for name, val in cex.items():
            if name.startswith("next(") or name not in m.sig:
                continue
            for k, b in enumerate(m.sig[name]):
                if isinstance(b, int):
                    fixed[b] = (int(val) >> k) & 1
        vals, _, _ = m.run([], 1, 0, fixed)
        node = parse(e["claim"])
        env = {(k, n): Model.value(vals, m.bits_of(k, n), 0) for k, n in claim_names(node)}
        return not Evaluator(m.widths).holds(node, env)
    except Exception:
        return None
