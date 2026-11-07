# Decap: View Source for silicon

Decap turns a chip's gate-level netlist, with every internal name stripped, back into short Verilog a person can read. It then proves with Yosys that the Verilog is exactly the same circuit. Plan and pitch: [`docs/decap/SPEC.md`](../docs/decap/SPEC.md). Data shapes and API: [`CONTRACTS.md`](CONTRACTS.md).

## Results (frozen held-out test set, decompiler `5dc6bfd`)

| | Test (18 chips, split frozen before tuning) | Dev (9 chips) |
|---|---|---|
| **Proven equal** by Yosys (a broken control copy is rejected on every proof) | **15** (+1 tested only, 2 failed) | 9 |
| Block coverage: logic cells explained by recognised blocks (mean per chip; weighted by cells) | 52.4%; 36.9% | 56.8%; 55.8% |
| Decompiled lines vs the author's source, chips with hand-written Verilog only | 480 vs 1,307 (9 chips, shorter on all 9) | 485 vs 1,775 (8 chips) |
| Planted trigger found blind (judge-style `out \|= (ui_in == N)`) | **16 of 16** | 9 of 9 |
| False alarms on clean copies (legitimate rare comparators) | 3 of 16 chips | 4 of 9 chips |

- **The 2 failures** contain a hard macro (`tt_um_micro_test`) whose netlist isn't published, so there's nothing to read.
- **The 1 "tested" chip** is a DDR flop test. Its clock is a delay-line pulse generator that only works with real gate delays. In zero-delay logic its flops never clock, so no proof can apply. It matches the netlist for 20 cycles from reset, and we label it "tested, not proven".
- **Line counts:** the raw totals in `bench.json` are 675 vs 1,769 (test) and 555 vs 1,775 (dev). They overstate the author side, so don't quote them:
  - 6 Wokwi (schematic) test chips have only Tiny Tapeout's generic 77-line `cells.v`.
  - `factory_test`'s source is skipped because its file name contains "test" (`core/chip.py` skips testbench files by name), so it counts 0 author lines.
  - `sign_addsub` counts two copies of its top module.
  - Some repos (`brailliance`, `yuri_panchul`) include modules the chip never uses.

  Even the like-for-like figure above still counts those unused modules and `sign_addsub`'s second copy of its top module.
- **Author check:** the shipped netlist is proven equal to the author's source on 6 of 18 test chips (3 of 9 dev). On test, the rest have no hand-written source, one Yosys can't read, or (`yuri_panchul`) a check that didn't finish. On dev, 5 more pass only the 20-cycle bounded check, and 1 has no source.
- **First run, kept as measured:** the first scored run (`911f15a`, stored in `bench.json` → `first_run`) had 15 proven and 3 failed, and caught 15 of 16 triggers (and missed the warm-up's). General fixes followed (`5b88a95`, `5dc6bfd`):
  - derived-clock wires written gate for gate;
  - hierarchical netlists flattened;
  - flags state their condition on plain decompiles;
  - the planter uses a register when a chip has no 8-bit input (the warm-up), and names new cells in the chip's own style.

  They changed one chip from failed to tested and caught one more trigger. The dev/test split (`bench/split.json`) was frozen before any tuning. Dev is every chip whose source an earlier session had already opened.

Example: the alarm-clock chip (`tt_um_kapilan_alarm`, dev) reads back as seconds, minutes and hours counters:

```verilog
else if (!bit0) count2 <= (count2 == 6'd59) ? 6'd0 : count2 + 1'b1;
else if (count2 == 6'd59 && !bit0) count0 <= (count0 == 6'd59) ? 6'd0 : count0 + 1'b1;
else if (count0 == 6'd59 && count2 == 6'd59 && !bit0) count1 <= (count1 == 5'd23) ? 5'd0 : count1 + 1'b1;
```

Plant the judge's number 137 into a chip and it comes back as a line nobody wrote, still proven equal to the gates. On `tt_um_mac` (dev):
`assign uo_out[0] = shreg3[1] | (ui_in == 8'd137);`. Checking against the author's source then fails: Yosys finds an input sequence where the chip and the author's code differ, and `ui_in = 137` is in that sequence. (Today the counterexample field reports the last time step, which is often a different value; see `_parse_cex` in `core/chip.py`.) That step needs an author source Yosys can read.

Two limits:
- If `uo_out[0]` is already 1 when `ui_in == N`, the trigger changes nothing. The flag card still names `ui_in == N`, but no line of the code shows it. This happens on 2 of 16 planted test copies. On a third (`wokwi_407306`, decompiled gate for gate) the trigger shows as an AND of the eight `ui_in` bits, not as `== N`.
- The blind flag only catches a trigger that is a single check in one clock cycle on 8 to 18 bits (like `ui_in == N`). Triggers spread over several cycles, or over more bits, are missed.

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
