"""Every file-system location the Python code uses (core, API, ingest, bench and the agent layer), in one place."""
from __future__ import annotations

from pathlib import Path

CORE = Path(__file__).resolve().parent      # basic/: the core package
REPO = CORE.parent

# inputs, fetched by `python -m basic.ingest.fetch` (gitignored)
DATA = CORE / "data"
PDK = DATA / "pdk"                             # SKY130 HD per-cell Liberty JSON
TT08 = DATA / "tt08"                           # <chip>/netlist.v, info.yaml, commit_id.json, src/
WARMUP = DATA / "warmup"                       # Jane Street warm-up (no licence: never commit)
GDS = DATA / "gds"

# results, written by `python -m basic.bench.run` (gitignored)
OUT = CORE / "out"
CHIPS_OUT = OUT / "chips"
BENCH_JSON = OUT / "bench.json"
BENCH_FIRST_RUN = OUT / "bench_first_run.json"

# the frozen benchmark split (never edit) and the candidate list it was drawn from
SPLIT = CORE / "bench" / "split.json"
CANDIDATES = CORE / "bench" / "candidates.txt"

ENV_FILE = CORE / ".env"                      # the AI key for the guide (gitignored)
WEB_DATA = REPO / "web" / "public" / "data"    # generated data the web app loads (gitignored)
RUNS = REPO / "agent" / "runs"                 # agent transcripts, caches and sessions (gitignored)
