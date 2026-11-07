# Handover: redstone backend → next session, the 2-D layout

Written Sat 7 Nov 2026, about 22:00, at the end of the session that built `redstone/`. Read this first, then `README.md`.

**Next session's goal:** build each chip's redstone at the **real 2-D positions** of its gates (the chip's rows and x positions from the GDS) instead of today's single long row. Everything must still pass the vanilla check.

---

## 1. Where things are

| What | Where |
|---|---|
| Code | `redstone/`, on branch `28-redstone` |
| Worktree | `/Users/yuyao/Projects/hacktheburgh-redstone` (separate from the main checkout, which other Yuyao sessions use; **never switch branches in the main checkout**) |
| Issue / PR | #28 / **PR #33 (open, not merged; Yuyao decides)**. It also adds the `redstone/` row to AGENTS.md §4. |
| Ownership | `redstone/` is Yuyao's. `minecraft/` is **Alex's** (`@advcode118`, #23 / PR #29): don't edit it, ask on the board. |
| Data (gitignored) | `redstone/data/tt08/<chip>/` holds .v, .gds and LICENSE; `redstone/data/pdk/*.json` holds SKY130 Liberty. Fetch with `python3 chipdata.py fetch <chip>`. |
| Outputs (gitignored) | `redstone/out/<chip>/layout.json` (+ `verilog.json` for the Verilog path) |
| Server jar | `redstone/tools/server-1.21.11.jar` (Mojang vanilla, SHA-1 `64bb6d76…`, gitignored). JDK 21 is at `/opt/homebrew/opt/openjdk@21`. |

### Servers (both may still be running; `lsof -nP -iTCP -sTCP:LISTEN | grep java`)

| Server | Folder | Game port | RCON | Used for |
|---|---|---|---|---|
| check | `redstone/server/` | 25566 | 25576 | `check.py`; ticks are **frozen** and stepped |
| demo | `redstone/server-demo/` | **25565** (`localhost`) | 25577 | playable world; ticks run normally |

- Start: `python3 mc.py start` / `python3 demo.py start`.
- Stop: `python3 mc.py stop` / `python3 demo.py stop`.
- `demo.py` sets the env vars for the demo server itself. For ad-hoc RCON to the demo server, prefix with `REDSTONE_SERVER_DIR=server-demo REDSTONE_RCON_PORT=25577`.

### What the demo world contains

| Build | Location |
|---|---|
| Spawn signpost | (250, −60, 124) |
| Alex's real-layout CLA (his datapack, run unmodified) | z −16..76 |
| `tt_um_sign_addsub` | z 200..274 |
| Primitive showroom | z ≈ 318..344 |
| `tt_um_Electom_cla_4bits` | z 400..482 |
| Verilog `counter` | z ≈ 600..780 |

The demo world also has these functions:
- `redstone:goto/<chip>` teleports to a build.
- `redstone:<chip>/settle` fast-forwards until the outputs settle.
- `redstone:<chip>/clock` is the CLOCK button's function.
- `redstone:<chip>/clock_fast` (`/tick sprint`, needs op) clocks in about 1.3 s.

Yuyao **doesn't have Minecraft installed yet**. Once they're in, run `python3 demo.py op`.

---

## 2. What works (all verified in vanilla 1.21.11)

| Check | Result |
|---|---|
| Primitives NOT / INH (= a∧¬b) / OR / BUF / LATCH, every rotation | pass |
| Bus tiles (ports on the north face) | pass |
| `sim.py`: cell-level (Liberty) vs primitive-level, 2,000 vectors | 0 mismatches on both chips |
| `tt_um_sign_addsub` (combinational, 105 primitives) | 200/200 |
| `tt_um_Electom_cla_4bits` (5 flops as two-phase latches, local async reset) | 60/60 cycles |
| Verilog `examples/mul2.v` and `examples/counter.v`, vs iverilog on the source | 40/40 each |
| Live (ticks running): adder; CLA 3+5, 9+8+1, 15+15+1; counter reset / count / load / down | correct |

Commands to re-verify after any change:
```sh
python3 sim.py tt_um_sign_addsub && python3 sim.py tt_um_Electom_cla_4bits
python3 pnr.py tt_um_sign_addsub && python3 check.py chip tt_um_sign_addsub 50
python3 pnr.py tt_um_Electom_cla_4bits --z 400 && python3 check.py chip tt_um_Electom_cla_4bits 30
python3 verilog.py examples/counter.v --z 600 && python3 check.py vchip examples/counter.v 30
```

---

## 3. How it works today (pipeline and files)

```
chipdata.py   fetch + read: netlist (ports, cells, pins), GDS (instance -> (x, y) um via property 61; pin labels), Liberty
techmap.py    SKY130 cells -> Prim(kind NOT|INH|OR|LATCH, ins, out, cell, x)
              buffers -> wires; conb -> constants; abc -g OR,ANDNOT per cell type; flop -> LATCH pair + HOLD_M/HOLD_S
sim.py        CellSim (Liberty, independent) and PrimSim; random_vectors
verilog.py    any Verilog -> same Prims (Yosys synth -flatten, dfflegalize, abc); IverilogRef = independent reference
pnr.py        place (ONE row, sorted by real x, pitch 6) -> route ("bus") -> repeaters -> DRC -> dust shapes -> STA
world.py      build(mc, layout) via RCON setblock; Driver: levers, clock(), read()
check.py      prims | bus | chip <name> N | vchip <file.v> N   (frozen ticks, /tick step)
demo.py       start | chip <name> | prims | op | stop   (+ goto / settle / clock / clock_fast functions)
prims.py      tiles. Bus tiles (BUS dict): 5 wide x 4 deep, ports on the north face at x=0 (A), 2 (OUT), 4 (B)
```

**Today's bus router** (`pnr.route`), which is what you'll generalise:
- **Tiles:** one row at z = `zt..zt+3`. The port cells are at z = `zt−1`, and each port owns its own x column.
- **Trunks:** each net has a trunk at height y=3 along x, at z_k = `zt − 6 − 4k`. Left-edge track assignment lets nets share a track when their spans are at least 3 apart.
- **Climb or descent:** in the port's own column, ground dust at z_k+3, a step at y=1 on z_k+2, a step at y=2 on z_k+1, joining the trunk at z_k. Staircases on solid blocks carry signal both ways in Java.
- **Repeaters:** look-ahead insertion so every port gets strength ≥ 1. Never on steps, junctions or the cell just before a climb.
- **DRC:** no two different nets, or a net and a foreign tile, within the 26-neighbourhood (3×3×3). This rule is what makes it correct by construction.

### Gotchas we already paid for

- In 1.21.11, repeater and comparator `facing` points **toward the input** (`facing=west` means the signal flows east).
- **Vanilla RCON can't take pipelined packets.** Send one command at a time (`MC.cmds` does).
- **Game rules are snake_case** in 1.21.11: `max_block_modifications`, `advance_time`, `random_tick_speed`, `max_command_sequence_length`.
- **Floor:** bedrock plus 3 stone, so the floor top is at y=−61 and builds start at −60.
- **`setblock` doesn't compute dust shapes.** `pnr._shape_dust` writes them explicitly.
- **Placement order:** supports, then components. `redstone_block` constants go **last**, so their neighbours update. The world starts all-zero.
- **Unloaded chunks:** forceload before building. A position that isn't loaded fails silently in `execute if block` ("not loaded").
- **`/tick` needs permission level 3.** Command blocks have level 2, and a function containing `/tick` can't be called from one. That's why `clock` and `clock_fast` are separate.
- **A fresh `Driver`** must write every lever on its first `set`. It no longer assumes 0.
- **Never `pkill -f "tools/board wait"`.** Other sessions run board watchers on the same Mac.

---

## 4. The next goal: the 2-D layout

### Facts to design from (measured)

- **Rows:** the logic sits in only **2–3 real rows** at the top of the tile. `sign_addsub` uses rows at y = 95.20, 100.64 and 106.08 µm; `cla_4bits` at 100.64 and 106.08 µm. The row pitch is 2.72 µm and logic x spans about 29–143 µm.
- **Pins:** all on the **north edge** (y = 111.02 µm). Inputs (`ui_in`, `uio_in`, `rst_n`, `clk`, `ena`) are at x ≈ 97–147 µm; outputs at x ≈ 30–94 µm. The x positions are in `read_gds(chip)["pins"]`.
- **Primitives per cell:** at most 5–6, across 26–32 cells with logic.
- **Size mismatch:** a tile with its port runs needs about 6 blocks of x and about 4 + 6 + 4·T blocks of z per row (tile, channel, T trunks). At Alex's 4 blocks/µm a real row is only 11 blocks tall. **So "real 2-D" has to mean real rows and real x order or position, stretched.** Say so honestly in the demo.
- **Alex's convention**, to line up with his build: GDS x → +x at 4 blocks/µm; GDS y → −z (the north edge, where the pins are, is z ≈ 0); Y0 = −60. His build covers x −20..530, z −16..76. **Keep ours at z ≥ 200** (agreed with Alex).

### Recommended design: rows + channels + spines (deterministic, generalises today's bus)

1. **techmap:** add `y` (and a row index) to `Prim`, from `read_gds(...)["cells"][inst][1]`. Keep `x`. Pads get the pin's x, with y = the top edge.
2. **Placement:**
   - One **tile row per real chip row**, in real row order (north = higher GDS y, to match Alex's −z).
   - Within a row, sort a cell's primitives by real x and keep them contiguous.
   - Target x = `round(x_um * S)`, with S chosen so the busiest row fits (about 7–8 blocks/µm). Legalise left to right with a minimum tile pitch.
   - **Use tile pitch 8, not 6.** That frees the column at offset 6 in every tile slot for spines (step 4): it's 2 away from port column 4 and from the next tile's column 0.
   - IO pads go in their own row along the north edge, at the pins' real x.
3. **Channels:**
   - North of every tile row there's a channel of trunks, built exactly like today's bus (y=3 trunks, private port columns, climbs at z_k+1..3, track pitch 4, left-edge per channel).
   - A net whose pins are all in one row (driver and sinks) routes entirely in that row's channel, which is today's code.
4. **Spines** (multi-row nets):
   - A net with pins in several rows gets a **north–south spine on y=6**, in a free spine column (offset 6 in some tile slot near the net's x span).
   - In each channel the net touches, it has a short trunk segment on y=3 joining its pins in that row to the spine column. There the spine **descends from y=6 to y=3 by a 3-step staircase along z** inside the channel's climb rows, in its private spine column.
   - y=6 dust sits on y=5 supports, 2 blocks of air above y=3 trunks. Spines cross trunks and tiles with no contact, the same argument as ground runs passing under trunks today.
   - Spines must **not** pass over another channel's climb or staircase cells within 1 block. The DRC will tell you, and it's cheap to re-run.
5. **Repeaters:** extend the tree to include spine cells (straight runs at y=6 are eligible; steps and junctions aren't). The look-ahead insertion code works unchanged on the tree.
6. **DRC, STA, dust shapes:** reuse as-is. They work on any block set.
7. **Fallback if spines get messy:** a general 3-D A\* maze router on the same grid with the same 26-neighbourhood rule. Allowed moves: flat steps on y ∈ {0, 3, 6}, and 3-cell staircases between layers. Rip up and re-route on conflict. More general, riskier. The review said earlier tools died exactly here, so try the deterministic design first.

**Check:** after the 2-D build, re-run §2's commands. Both chips must still give **0 mismatches** in `check.py chip`, then rebuild the demo with `demo.py chip`.

**Display idea:** put a sign on each tile group with the cell's instance name and type (`_41_ a21oi_1`), so a player can match it to Alex's layout and to Decap's cell ids (the same names, GDS property 61).

### Other open items (lower priority)

- **Speed:** settle is 127–230 redstone ticks. Ideas:
  - drop the output repeater of comparator tiles (−1 rt per gate);
  - INH2 / NOR2 using both comparator sides (the review found 10–35 % fewer primitives; ABC needs a BUF cell in the Liberty file or it segfaults);
  - shorter trunks from better placement.
- **Clock domains:** segdecode has 3 domains (negedge flops clocked by data pins), which is rejected today.
- **Horizon** (Rust Minecraft, pinned commit `8d8c250`) as a faster checker. Skipped because 26.3 needs a licence. Notes are in the session scratchpad only.
- **PR #33 merge:** Yuyao's call. Post on the board (issue #1, `tools/board`) when merging, since it touches AGENTS.md.
