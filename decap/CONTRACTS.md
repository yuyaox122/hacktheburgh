# Decap contracts

The shapes every part of Decap agrees on. Change one only with a board post (`tools/board`), since other streams depend on it.

Folders: `ingest/` (Python, data in), `core/` (Python: netlist, simulator, decompiler, proofs, plant/flag/fix, compare), `bench/` (frozen split, benchmark runner), `api/` (FastAPI), `web/` (Vite + TypeScript + three.js). Data lives in the gitignored `data/` (inputs) and `out/` (results). `web/public/data/` is generated and gitignored.

## 1. Chip result (`out/chips/<chip>.json`, served as `/api/chip/<chip>`, copied to `web/public/data/chips/<chip>.json`)

```jsonc
{
  "chip": "tt_um_sign_addsub",
  "title": "Signed adder/subtractor", "author": "…", "language": "Verilog",   // from info.yaml
  "split": "dev" | "test" | "warmup" | "user",
  "variant": "clean" | "planted",
  "stats": { "logic_cells": 65, "flops": 0, "cell_types": 19, "area_um2": 512.3 },
  "decompiled": { "verilog": "module …", "lines": 14 },
  "blocks": [                     // recognised blocks, in source order
    { "id": "b3", "kind": "adder" | "subtractor" | "counter" | "register" | "shift" | "comparator" | "mux" | "reduce" | "const" | "copy" | "logic",
      "text": "assign y = a + b;", "lines": [5, 5], "width": 4, "cells": ["c17", "c18"], "recognised": true }
  ],
  "coverage": { "explained_cells": 50, "logic_cells": 65, "pct": 76.9 },   // cells inside recognised (non-"logic") blocks
  "verdict": "proven" | "tested" | "failed",
  "proof": { "method": "equiv", "points": 24, "proven": 24, "seconds": 1.3,
             "control_caught": true,          // a deliberately broken copy of the decompile was rejected
             "tested_cycles": 0,              // >0 when the verdict rests on random simulation only
             "detail": "short human-readable summary" },
  "author_source": { "repo": "owner/name", "commit": "sha", "url": "https://github.com/…/tree/sha/src",
                     "files": { "project.v": "…" }, "lines": 40,
                     "check": "proven" | "bounded" | "failed" | "unavailable", "check_detail": "BMC, 25 cycles from reset" },
  "netlist": {                    // the stripped netlist the decompiler saw: no internal names, only cell types and wires
    "ports": { "ui_in": {"dir": "in", "width": 8}, "uo_out": {"dir": "out", "width": 8}, … },
    "cells": [ { "id": "c17", "type": "xor2_1", "pins": { "A": "n12", "B": "ui_in[0]", "X": "n40" } } ]
  },
  "cell_lines": { "c17": 5 },     // decompiled line that accounts for each logic cell
  "flags": [                      // suspicious logic, found blind
    { "id": "f0", "kind": "rare-trigger", "line": 9, "signal": "uo_out[0]", "condition": "ui_in == 8'd137",
      "rate": "1/256", "cells": ["c90", "c91"], "cone": ["c90", "c91", "c4"] }
  ],
  "plant": { "n": 137, "operand": "ui_in", "target": "uo_out[0]", "inserted_cells": ["c90", "c91"] },   // planted variant only
  "variant_id": "tt_um_mac+t137"                                                                          // planted variant only
}
```

Also present: `description`; `stats.physical_cells`; `proof.attempts` (1 = proven first time); flags carry `fires_on` ("1"/"0") and `reaches` (outputs/flops). `cell_lines` uses 0 for "no line". Planted chip files are `out/chips/<chip>__planted.json` (the benchmark's seeded N).

Cell ids `c<k>` and net names `n<k>` are stable for a given netlist file. Port bits are written `ui_in[3]`.

## 2. Benchmark (`out/bench.json`, `/api/bench`, `web/public/data/bench.json`)

```jsonc
{
  "generated": "2026-11-07T23:00:00Z",
  "decompiler_commit": "5dc6bfd",          // the code that produced these numbers ("+dirty" if uncommitted)
  "first_run": { "decompiler_commit": "911f15a", "summary": { … }, "note": "…" },   // the first scored test run, kept as measured
  "split": { "frozen": "…", "dev": ["…"], "test": ["…"] },
  "summary": {
    "test": { "n": 18, "proven": 15, "tested": 2, "failed": 1, "coverage_pct": 61.0 },
    "dev":  { "n": 9, "proven": 9, "tested": 0, "failed": 0, "coverage_pct": 70.2 },
    "trojan": { "planted": 18, "caught": 17, "clean": 18, "clean_flagged": 3 }
  },
  "chips": [
    { "chip": "…", "title": "…", "split": "test", "verdict": "proven", "logic_cells": 65, "flops": 0,
      "lines_decompiled": 14, "lines_author": 40, "coverage_pct": 76.9, "seconds": 2.1, "author_check": "proven",
      "trojan": { "n": 137, "caught": true, "flag_line": 9, "clean_flags": 0 } }
  ]
}
```

## 3. Explore design (`web/public/data/designs/<chip>.json` from `ingest/ingest_gds.py --all`; the local warm-up `design.json` from `ingest/ingest_def.py`)

Shape (the GDS version adds `title`, `intro`, `hint` and `io`): `{ name, pdk, die:{w,h}, rowHeight, site, ports:[{name,dir,x,y}], cells:[{id,type,x,y,w,orient,pins,module,src}], phys:[{type,x,y,w}], routes:[{net,layer,pts}], source:{files,modules} }`, plus `lib.json` from Liberty: `{ "<type>": { "pins": {"A":"in","Y":"out"}, "function": {"Y": "!(A&B)"}, "area": 6.256, "ff": null | {"clocked_on","next_state","clear"?,"preset"?} } }`.

## 4. API (`api/main.py`, FastAPI on :8000; Vite proxies `/api`)

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/health` | | `{ ok, yosys, llm }` |
| GET | `/api/bench` | | bench.json |
| GET | `/api/chip/{chip}` | | chip result (clean) |
| POST | `/api/decompile` | `{ chip }` or `{ netlist: "<verilog text>", name? }` | chip result |
| POST | `/api/plant` | `{ chip, n: 0–255 }` | chip result, `variant: "planted"`, with `plant` and `flags` |
| POST | `/api/fix` | `{ chip, n }` | `{ removed_cells, verdict, detail, seconds, verilog }`: the flagged cone removed, proven against the clean decompile and the author source where possible |
| POST | `/api/compare` | `{ verilog }`, `{ netlist }` or `{ chip }` | `{ fingerprint, neighbours: [{ chip, title, score, why, logic_cells, area_um2, blocks, excerpt }] }` |
| POST | `/api/chat` | `{ messages: [{role, content}], context }` | `{ reply, highlights: { cells: [], lines: [] } }`; 503 with `{ error }` when no LLM key is set |

Agent tools (operation_id = MCP tool name via `decap/agent`; docstrings are what agents read): `list_chips`, `strip_names`, `recover_blocks`, `decompile`, `prove_equiv` (`against`: netlist | author_source → counterexample), `simulate`, `find_rare_triggers`, `plant_trigger` (returns id `chip+tN`), `cone_of_influence` (flag, block or cell id), `remove_trigger`, `critical_path` (estimate), `compare_design`, `benchmark`, `review_edit` (below). Paths are under `/api/chips/{chip}/…`; see `api/main.py`. Web-only routes are hidden from the schema so they don't become tools.

Witness (`get_region`, `probe_truth` in `api/scope.py`, Ian's; `propose_claim` in `core/claims.py`): `POST /api/chips/{chip}/claim` with `{ claim, scope, label?, check = true }` returns `{ verdict, truth, formal, english, literals, counterexample?, refute_kind?, credit?: { cleared, complete, reason, literals, cone_literals }, touched, netlist_sha, decompiler, method, seconds, source: "yosys" }`.
- `verdict`: `proven` · `inductive` (true from reset, proven by k-induction) · `bounded` (holds 30 cycles from reset only) · `refuted` · `partial` · `restated` · `tautology` · `unparseable` · `timeout` · `unchecked` (`check: false`).
- `truth` says whether the claim holds (`proven`, `inductive`, `bounded`, `refuted`, `tautology`) separately from credit. A true claim (`proven`/`inductive`) shows that verdict only when `credit.cleared`; otherwise `partial` or `restated`.
- Setup: rst_n = 1, ena = 1, flip-flops cut (registers free), one clock step. `counterexample`: `{ input bus: int, q_<id>: bit, each name the claim used: value }`; `refute_kind`: `semantic` (also fails from reset) or `reset_only`.
- Credit needs all of: the claim names one of the region's outputs; it is complete (SAT finds no two different values of those output bits that both satisfy it); and it has a labelled shape or at most 1/3 of the region's `cone_literals`.
- Self-test: `python -m bench.claims_check`.

Review an edit (`review_edit`, `service.review_edit`; the web's Ideas, issue #68, and any IDE agent): `POST /api/chips/{chip}/review-edit` with `{ verilog, baseline? }` (the FULL edited module; same module name and port list; `baseline` = the full module before this edit, with edits already kept) returns `{ chip, verdict, headline, detail, counterexample, seconds, this_edit? }`. Works for planted ids (`chip+tN`, checked against their own planted gates).
- `verdict` (vs the chip's gates): `same` (proven equal; headline "Same circuit (proven)") · `tested_same` (no difference in 20 clock cycles from reset, not proven) · `changed` (`counterexample` `{ ui_in, uio_in, ena, rst_n: int, step: int }` = the inputs at the first clock step, counted from reset, where the outputs differ; `detail` lists the inputs of every step up to it; or headline "Behaviour changed: a register now uses a different clock or clock edge", no counterexample) · `broken`, with one of these headlines: "Doesn't compile" (the Yosys error line in `detail`), "Ports or module name changed", "Wiring problem" (an undriven wire, two drivers, a logic loop or a latch that the decompile doesn't have), "Undefined value (x or z)", "Can't be checked: simulation-only code" (`ifdef`/`include`, translate_off, `initial`, `$display`, `#` delays; refused before Yosys reads the text), "Yosys couldn't check this edit" (out of time, or the comparison itself failed).
- `this_edit` (only with a `baseline` that differs from the decompile): `{ verdict, headline, detail, counterexample }`, the same checks with the baseline in place of the chip's gates, so a refactor made after a kept behaviour change is judged on its own (headline "Same as before this edit (proven)", "This edit changes the behaviour: ..."). The web shows this as the edit's pill and the chip verdict under it.
- Method: first a structural check of the edited module against the decompile (or baseline), because equivalence and SAT can't see these: ports; Yosys `check` warnings; bits set by two `assign`s; latches; x/z constants; and the clocking (which clock port and edge each flip-flop uses; equiv/sat step every flip-flop once per step whatever its clock). Then `chip.author_check` with the edit as the source (equiv_make / equiv_simple / equiv_induct, else a 20-cycle bounded check from reset); a `tested_same` gets a second try with the decompiler's own proof (registers paired by name, broken control copy rejected). One 90 s budget for the whole review (`REVIEW_SECONDS`). `headline` and `detail` are written by code, never by an LLM.
- Measured: under 0.1 s for `same`, about 0.4 s (cla_4bits) / 0.7 s (tt_um_mac) for `changed`, 0.01 s for `broken`. Every unchanged saved decompile reviews `same` except tt_um_wokwi_407306064811090945 (`tested_same`), warmup (not a TT chip: "Yosys couldn't check this edit") and tt_um_wokwi_407852791999030273 / tt_um_yuri_panchul_adder_with_flow_control (the bounded check runs out of time: about 63 s, "Yosys couldn't check this edit").

Errors: FastAPI's `{ "detail": "…" }` with a 4xx/5xx status. Long calls (decompile, plant, fix) take up to ~60 s.
