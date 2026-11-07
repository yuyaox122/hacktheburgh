# Decap: View Source for silicon

Decap turns a chip's gate-level netlist, with every internal name stripped, back into short Verilog a person can read. It then proves with Yosys that the Verilog is exactly the same circuit. Plan and pitch: [`docs/decap/SPEC.md`](../docs/decap/SPEC.md). Data shapes and API: [`CONTRACTS.md`](CONTRACTS.md).

## Results (frozen held-out test set, decompiler `5dc6bfd`)

| | Test (18 chips, never tuned on) | Dev (9 chips) |
|---|---|---|
| **Proven equal** by Yosys (a broken control copy is rejected on every proof) | **15** (+1 tested only, 2 failed) | 9 |
| Block coverage: logic cells explained by recognised blocks (mean) | 52.4% | 56.8% |
| Decompiled lines vs the author's source | 675 vs 1,769 | 555 vs 1,775 |
| Planted trigger found blind (judge-style `out \|= (ui_in == N)`) | **16 of 16** | 9 of 9 |
| False alarms on clean copies (legitimate rare comparators) | 3 of 16 chips | 4 of 9 chips |

- **The 2 failures** contain a hard macro (`tt_um_micro_test`) whose netlist isn't published, so there's nothing to read.
- **The 1 "tested" chip** is a DDR flop test. Its clock is a delay-line pulse generator that only works with real gate delays. In zero-delay logic its flops never clock, so no proof can apply. It matches the netlist for 20 cycles from reset, and we label it "tested, not proven".
- **First run, kept as measured:** the first scored run (`911f15a`, stored in `bench.json` → `first_run`) had 15 proven and 3 failed, and caught 15 of 16 triggers. Two generic fixes followed: undeclared derived-clock wires, and a missing condition text on plain decompiles. They changed one chip from failed to tested and caught one more trigger. The dev/test split (`bench/split.json`) was frozen before any tuning. Dev is every chip whose source an earlier session had already opened.

Example: the alarm-clock chip (`tt_um_kapilan_alarm`, dev) reads back as seconds, minutes and hours counters:

```verilog
else if (!bit0) count2 <= (count2 == 6'd59) ? 6'd0 : count2 + 1'b1;
else if (count2 == 6'd59 && !bit0) count0 <= (count0 == 6'd59) ? 6'd0 : count0 + 1'b1;
else if (count0 == 6'd59 && count2 == 6'd59 && !bit0) count1 <= (count1 == 5'd23) ? 5'd0 : count1 + 1'b1;
```

Plant the judge's number 137 into any chip and it comes back as a line nobody wrote, still proven equal to the gates:
`assign uo_out[0] = shreg3[1] | (ui_in == 8'd137);`. Checking against the author's source then fails with counterexample `ui_in = 137`.

## Run it

Needs `gh` (logged in), `yosys`, `iverilog`, Python 3.12+ and Node 22.

```bash
cd decap
python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn httpx anthropic
.venv/bin/python -m ingest.fetch          # TT08 netlists + authors' sources, SKY130 cells, warm-up → data/ (gitignored)
.venv/bin/python -m bench.run             # decompile + prove every chip, author check, trojan test → out/ (~2 min)
.venv/bin/uvicorn api.main:app --port 8000
cd web && npm install && npm run stage-data && npm run sync-data && npm run dev   # http://localhost:5173
```

- The guide chat needs `ANTHROPIC_API_KEY` on the server. Without it, the chat returns 503 and the rest works.
- For agents: `DECAP_CORE_APP=decap.api.main:app` (see `agent/`, Ian's layer).

## How it works

1. **Strip** (`core/netlist.py`): Yosys reads the netlist. Instance and net names are dropped, cell ids come from a hash, and only cell types, wires and port names remain.
2. **Decompile** (`core/decompile.py`), structure first, with flip-flops kept 1:1:
   - Flip-flops are grouped into registers by shared control.
   - Sync clears and enables are peeled off by exact Shannon expansion. That happens on real nets, or exhaustively over the outside bits when synthesis has dissolved the enable net.
   - What's left is matched against word-level templates by bit-parallel simulation: adders, subtractors, counters (including wrap-at-K), comparators, `A + B == K`, shifts, lookup tables.
   - Anything unmatched is written as small boolean logic.
3. **Prove** (`core/prove.py`, `core/pipeline.py`):
   - The stripped netlist is written back out with the decompiler's register names on its flip-flops.
   - Yosys `equiv_make` / `equiv_simple` / `equiv_induct` must prove every paired signal.
   - Every proof also runs a control (one cell inverted), which must fail.
   - Anything the proof rejects is rewritten as plain logic and proven again, at most twice; after that everything is written gate for gate.
4. **Find** (`core/find.py`):
   - **Plant:** a trigger `out |= (in == N)` is built from SKY130 cells and spliced in, then re-read with fresh ids.
   - **Flag:** nets that fire for at most 1 in 2^8 of their inputs are flagged, checked exhaustively, with the exact condition.
   - **Fix:** the trigger is tied off and the result proven equal to the clean decompile.
5. **Author check** (`core/chip.py`): the shipped netlist is checked against the author's source from `commit_id.json`. That's an equivalence proof if it goes through; otherwise a 20-cycle bounded check, which returns the counterexample input when they differ.
6. **Compare** (`core/compare.py`): imported Verilog is synthesised to SKY130 cells. Its block fingerprint is then ranked against the benchmark chips by cosine similarity.

## Folders

| Folder | What |
|---|---|
| `ingest/` | `fetch.py` (data), `liberty.py` → `lib.json`, `ingest_def.py` → `design.json` (warm-up 3D) |
| `core/` | netlist, simulator, decompiler, proof, find, chip, compare, guide, service |
| `bench/` | frozen split, candidates, runner |
| `api/` | FastAPI: web routes + agent tools |
| `web/` | Vite + TypeScript + three.js app (see `web/README.md`) |
| `agent/` | Ian's MCP / CLI agent layer |

Data rules: the Jane Street warm-up has no licence. It's fetched at setup and never committed, and the page credits it. Never feed their `puzzle.gds` to an AI tool.
