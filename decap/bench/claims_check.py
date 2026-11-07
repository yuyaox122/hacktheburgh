"""Self-test for the Witness claim checker (core/claims.py) on real chips: every verdict, plus controls.

    cd decap && .venv/bin/python -m bench.claims_check      # needs data/ (python -m ingest.fetch) and yosys

Each near-miss of a proven claim must come back refuted (or at most bounded), never proven: if one is proven,
the checker is vacuous and this exits non-zero.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import claims, service  # noqa: E402

ALARM = "tt_um_kapilan_alarm"
LATCH = "next(bit0) == (bit0 | ({count0, count1[4:0]} == {uio_in[2:0], ui_in}))"   # Ian's agent, first draft
CASES = [  # (chip, claim, expected truth or verdict, expected counterexample subset)
    (ALARM, "ui_in == ui_in", "tautology", None),
    (ALARM, "foo == 1", "unparseable", None),
    (ALARM, "next(ui_in) == 1", "unparseable", None),
    (ALARM, LATCH, "proven", None),
    (ALARM, "next(bit0) == bit0", "refuted", None),
    (ALARM, "count2 <= 6'd59", "inductive", None),                               # false for free registers, true from reset
    (ALARM, "next(count2) == count2 + 6'd1 when !bit0", "bounded", None),        # wraps at 59: beyond the 30-cycle BMC
    # controls: near-misses of LATCH
    (ALARM, LATCH.replace("ui_in}", "ui_in + 8'd1}"), "refuted", None),
    (ALARM, LATCH.replace("bit0 |", "bit0 &"), "refuted", None),
    (ALARM, LATCH.replace("uio_in[2:0], ui_in", "uio_in[3:0], ui_in[7:1]"), "refuted", None),
    ("tt_um_wokwi_408216451206371329", "uo_out == {6'b000000, (&ui_in), (^ui_in)}", "proven", None),
    ("tt_um_wokwi_408216451206371329", "uo_out == {6'b000000, (|ui_in), (^ui_in)}", "refuted", None),
    ("tt_um_Richard28277+t137", "uo_out[0] == (state0[0] | (ui_in == 8'd137))", "proven", None),
    ("tt_um_Richard28277+t137", "uo_out[0] == state0[0]", "refuted", {"ui_in": 137}),
]
PARTIAL = ("tt_um_Richard28277+t137", "uo_out[0] == 1 when ui_in == 8'd137")


def main() -> int:
    bad = 0
    for chip, claim, want, cex in CASES:
        v = service.plant(*chip.split("+t")[0:1], int(chip.split("+t")[1])) if "+t" in chip else service.get(chip)
        r = claims.check(v, claim)
        got = r.get("truth") or r["verdict"]
        ok = got == want and all(r.get("counterexample", {}).get(k) == x for k, x in (cex or {}).items())
        bad += not ok
        print(f"{'ok ' if ok else 'BAD'} {got:<11} (want {want:<11}) {r['seconds']:>5.2f}s  {chip}: {claim}")
    v = service.plant("tt_um_Richard28277", 137)
    r = claims.check(v, PARTIAL[1])
    ok = r["verdict"] == "partial" and r["credit"]["complete"] is False
    bad += not ok
    print(f"{'ok ' if ok else 'BAD'} {r['verdict']:<11} (want partial    ) {r['seconds']:>5.2f}s  {PARTIAL[0]}: {PARTIAL[1]}")
    region = {"outputs": [{"name": "next(bit0)", "kind": "register_next", "flops": []}], "cone_literals": 50, "touched": None}
    r = claims.check(service.get(ALARM), LATCH, region=region)
    ok = r["verdict"] == "proven" and r["credit"]["cleared"]
    bad += not ok
    print(f"{'ok ' if ok else 'BAD'} credit      {r['credit']}")
    print("all passed" if not bad else f"{bad} FAILED")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
