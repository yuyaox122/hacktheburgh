#!/bin/bash
# Held-out Witness bench, after the freeze (witness-plan sections 4 and 6). Never retunes; restarts reuse the cache.
#
#   FREEZE_SHA=<sha256 posted on the board> agent/scripts/overnight_bench.sh
#
# 1. waits until the tag 'bench-freeze' exists on origin, then checks out that tag in the core checkout
# 2. recomputes the freeze hash and ABORTS if it differs from FREEZE_SHA (code, prompts, model, split, decompiler)
# 3. runs 'decap bench --split test --frozen' under caffeinate; on a crash it restarts (finished regions come
#    from runs/cache, so nothing is re-asked and nothing is retuned), up to 3 tries
# Env: DECAP_CORE_ROOT (checkout with decap/data; default = the main repo, which has data symlinked in),
#      DECAP_WITNESS_MODEL (default deepseek:deepseek-chat; the key loads from ~/.config/decap/secrets.env).
set -u
CORE=${DECAP_CORE_ROOT:-$HOME/hacktheburgh/hacktheburgh}
AGENT=$(cd "$(dirname "$0")/.." && pwd)
MODEL=${DECAP_WITNESS_MODEL:-cli:sonnet}
PY="$AGENT/../.venv/bin/python"     # the repo's one venv (uv pip install -e . at the root)
DECAP="$AGENT/../.venv/bin/decap"

until git -C "$CORE" ls-remote --tags origin bench-freeze | grep -q bench-freeze; do
  echo "[overnight] waiting for tag bench-freeze ($(date +%H:%M))"; sleep 60
done
git -C "$CORE" fetch -q origin --tags && git -C "$CORE" switch -q --detach bench-freeze || exit 1

cd "$CORE" || exit 1
export DECAP_CORE_APP=decap.api.main:app PYTHONPATH="$CORE"
GOT=$("$PY" "$DECAP" --no-transcript bench --freeze-hash --model "$MODEL" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["sha256"])')
if [ -n "${FREEZE_SHA:-}" ] && [ "$GOT" != "$FREEZE_SHA" ]; then
  echo "[overnight] ABORT: freeze hash $GOT != posted $FREEZE_SHA (code, prompt, model, split or decompiler changed)" >&2
  exit 3
fi
echo "[overnight] freeze hash $GOT ${FREEZE_SHA:+matches the posted one}"

OUT="$AGENT/runs/bench-test-$(date -u +%Y%m%d-%H%M%S)"
for try in 1 2 3; do
  caffeinate -i "$PY" "$DECAP" --transcript "$OUT.jsonl" bench --split test --frozen --max-regions 4 --model "$MODEL" \
    --concurrency "${DECAP_BENCH_CONC:-1}" --out "$OUT.json" && { \
      mkdir -p "$AGENT/out"; "$PY" "$AGENT/scripts/witness_card.py" "$OUT.json" "$MODEL" -o "$AGENT/out/witness-summary.json"; \
      echo "[overnight] done: $OUT.json"; exit 0; }
  echo "[overnight] bench crashed (try $try); restarting from the cache" >&2
done
exit 1
