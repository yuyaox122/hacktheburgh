#!/usr/bin/env python3
"""Turn a bench JSON into the small Witness card Yuyao's Results page shows (issue #87).

    python witness_card.py bench.json <model> -o out/witness-summary.json

Fields: model, n, first_draft_proven, proven_after_refinement, coverage_pct, finished_at.
"""
import argparse
import json
from datetime import datetime, timezone

p = argparse.ArgumentParser()
p.add_argument("bench_json")
p.add_argument("model")
p.add_argument("-o", "--out", default="out/witness-summary.json")
a = p.parse_args()

d = json.load(open(a.bench_json))
s = d["summary"]
n = s.get("claims") or 0
fd = s.get("first_draft", {}).get("true_credited", 0)
regions = s.get("regions") or 0
card = {
    "model": a.model,
    "n": n,
    "first_draft_proven": round(fd / n, 4) if n else None,
    "proven_after_refinement": round(s.get("explained_regions", 0) / regions, 4) if regions else None,
    "coverage_pct": round((s.get("coverage", {}).get("share") or 0) * 100, 1),
    "finished_at": datetime.now(timezone.utc).isoformat(),
}
open(a.out, "w").write(json.dumps(card, indent=1) + "\n")
print(f"[witness-card] wrote {a.out}: {json.dumps(card)}")
