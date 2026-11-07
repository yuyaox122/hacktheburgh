# Redstone backend: a real chip, built from real redstone

Compiles a Tiny Tapeout 08 chip's gate-level netlist into **actual redstone**: comparators, repeaters and dust, no command blocks or scoreboards. Each build is checked in a real vanilla 1.21.11 server against a simulation of the chip's own netlist. This is an add-on to #23 (Alex's `minecraft/`, which draws the real layout and runs the logic as a datapack).

## Status (verified in vanilla 1.21.11)

| Check | Result |
|---|---|
| Primitives (BUF, NOT, INH, OR, LATCH), every rotation, truth tables plus random transitions | all pass |
| Bus tiles (north-facing ports) | all pass |
| Techmap: netlist cells vs primitives, 2,000 random vectors (`sim.py`) | 0 mismatches on both chips |
| `tt_um_sign_addsub` (combinational): 105 primitives in the chip's 5 real rows + a pad row, 629 × 211 blocks, ~26k blocks, 35 spines, settles in 178 redstone ticks | **200/200 vectors** match |
| `tt_um_Electom_cla_4bits` (sequential): 5 flip-flops as two-phase latches, with reset; 4 real rows + a pad row, 659 × 222 blocks, ~33k blocks, 31 spines, settles in 139 rt | **60/60 cycles** match |
| Verilog path: `examples/mul2.v` (3×3 multiplier) vs iverilog on the source | **40/40 vectors** match |
| Verilog path: `examples/counter.v` (4-bit up/down counter, load, enable, async reset) vs iverilog | **40/40 cycles** match |

## How it works

1. **`chipdata.py`** fetches the TT08 netlist and GDS (Apache-2.0), plus the SKY130 Liberty JSON. It reads ports, cells, instance positions (GDS property 61) and pin labels.
2. **`techmap.py`** maps each cell, keeping the chip's cell boundaries:
   - buffers become wires, and tie cells become constants;
   - combinational cells go through Yosys `abc -g OR,ANDNOT` and become NOT / INH (a&!b) / OR;
   - each flip-flop becomes a master/slave LATCH pair, held by two global phase nets (`HOLD_M`, `HOLD_S`), with a local asynchronous reset or set.
3. **`pnr.py`** places the chip in 2-D and routes it with a "bus" fabric that is correct by construction:
   - **Rows:** one tile row per real standard-cell row of the chip, in real order. Mirrored (FS) cells have their GDS origin at their top edge, so their row is origin y − 2.72 µm; without that correction two physical rows merge into one (north = higher GDS y, matching Alex's layout, where GDS y maps to −z). The IO pads get their own row along the north edge, at the pins' real x, because the real pins are all on the north edge. Constant outputs (tied pins, no wire) are packed as unlit lamps west of the first real pad.
   - **x is to scale, approximately:** one 8-block tile slot per µm of real x (so 8 blocks/µm), legalised with minimum displacement so tiles don't overlap. A cell with k primitives needs k slots but is only 1.4–3.7 µm wide, so legalising moves tiles up to 3 µm (sign_addsub) or 6.5 µm (cla_4bits) from their real x; `pnr.py` prints this as `max_x_shift_um`. A cell's primitives stay together, and a sign under each group names the instance and cell type (`_41_` / `a21oi_1`, the same names as the GDS and Decap).
   - **z is stretched:** a real row is 2.72 µm, but a tile row plus its routing channel needs 15–55 blocks, so rows keep their order but not their spacing.
   - **Channels:** north of every tile row, one east–west trunk per net on y=3. Nets whose spans don't overlap share a track (left-edge channel routing). Each port owns a north–south column at floor level, with a solid staircase up to its trunk.
   - **Spines:** a net with pins in several rows gets a north–south spine on y=6, in a column no port uses (offset 6 of every 8-block slot). In each channel it serves, the spine dips down to that channel's trunk by a 3-step staircase and climbs back up. y=6 runs 2 blocks of air above trunks and 5 above tiles, so it crosses them without touching.
   - **Checks:** look-ahead repeater insertion, a 3-D design-rule check (no two nets within 1 block), a same-net check (no two cells of one net touch unless they are joined), and explicit dust shapes.
   - **Timing:** static timing analysis gives the settle time.
   - Verilog designs have no real placement, so they get the old single row (pads and logic together).
4. **`world.py`, `check.py`, `demo.py`** place the build over RCON, drive the levers, read the lamps, and compare with `sim.CellSim`.

## Your own Verilog

```sh
python3 verilog.py examples/counter.v --z 800         # Yosys: synth -flatten, abc -g OR,ANDNOT, flops -> latch pairs
python3 check.py vchip examples/counter.v 40          # vanilla build vs iverilog running the original source
python3 demo.py chip counter                          # playable, with a CLOCK button
```

**Supported:**
- any synthesisable Verilog or SystemVerilog that Yosys flattens;
- one clock from an input pin;
- flops with optional async reset or set.

**Not supported yet:** latches, multiple clocks.

Memories become flops. Keep designs small: about 100 primitives makes a build of roughly 600 × 100 blocks.

## A tiny FPGA, in redstone

`examples/fpga.v` is a small FPGA:
- 2 logic blocks (CLBs). Each one is a 3-input lookup table plus an optional flip-flop.
- Each LUT input picks one of 4 signals: a data pin, its own flop, or its neighbour's flop.
- Feedback only goes through flops, so no bitstream can make a combinational loop.

Its 30-bit **bitstream is a bank of levers**. It goes through the normal Verilog path, so the FPGA itself becomes redstone: 126 primitives, 981 × 262 blocks, 149k blocks, settling in 317 redstone ticks.

| Check (vanilla 1.21.11) | Result |
|---|---|
| Random bitstream + random data every cycle, vs iverilog on `fpga.v` | **30/30** |
| Programmed as a full adder / 2-bit counter with enable / 2-bit shift register, vs a Python model | **16/16 each** |
| Live: adder loaded by `/function redstone:tiny_fpga/load_adder`, then pins flipped | 3/3 |

```sh
python3 verilog.py examples/fpga.v --z 1200 && python3 check.py vchip examples/fpga.v 30
python3 fpga.py bits counter                # the bitstream
python3 fpga.py check counter 16 --nobuild  # program the levers, compare with the model
python3 demo.py chip tiny_fpga && python3 fpga.py demo   # playable: /function redstone:tiny_fpga/load_<adder|counter|shift>
```

**Scaling:** the one-row layout grows quickly, because wire tracks grow with the logic.
- 4 CLBs would be 252 primitives and 568k blocks, settling in 558 rt.
- 8 CLBs would be 504 primitives and 2.2M blocks, settling in 1128 rt (about 56 s).

An FPGA is a regular grid, so a tiled layout (one CLB per tile, with short local wires) is the way to go bigger.

## Run it

```sh
python3 chipdata.py fetch tt_um_sign_addsub          # data/ (gitignored)
python3 sim.py tt_um_sign_addsub                     # techmap check, no Minecraft needed
python3 pnr.py tt_um_sign_addsub                     # out/<chip>/layout.json
python3 mapview.py tt_um_sign_addsub                 # out/<chip>/map.png, a top-down picture (no Minecraft needed)
python3 mc.py start                                  # headless check server (needs JDK 21 and tools/server-1.21.11.jar)
python3 check.py prims && python3 check.py chip tt_um_sign_addsub 50
python3 demo.py start && python3 demo.py chip tt_um_sign_addsub   # playable server on localhost:25565
```

The server jar is Mojang's vanilla `server.jar` for 1.21.11 (SHA-1 `64bb6d76…`), downloaded into `tools/`. It is not committed, and running it means accepting the Minecraft EULA.

**To play:** in Minecraft Java 1.21.11, go to Multiplayer → `localhost`. Then run `python3 demo.py op` once, so your player can use `/tick` and the functions below.

- **Inputs and outputs:** levers are the chip's input pins and lamps are its output pins, each labelled by a sign.
- **Teleport:** `/function redstone:goto/<chip>`, for example `redstone:goto/counter`, `redstone:goto/tt_um_sign_addsub` or `redstone:goto/tt_um_electom_cla_4bits`.
- **Fast-forward until the outputs settle:** `/function redstone:<chip>/settle`. At normal speed one change takes about 10–25 s.
- **Clock (sequential chips):**
  - The **CLOCK button** next to the levers plays one rising edge at normal speed (about 1–2 min).
  - `/function redstone:<chip>/clock_fast` does the same in about 1–2 s by sprinting the server.
- **Real layout next to it:** if Alex's `minecraft/` datapack is installed in the same world (`python build.py <chip> --install <world>`), his real-layout build sits at z −16..76, north of these builds (all at z ≥ 200).

## Limits (for now)

- Only one clock domain, driven straight from `clk` with no inverted or gated clocks.
- No latches or memories.
- On the playable server the two clock phases are levers, so stepping the clock means flipping `HOLD_M` up, `HOLD_S` down, `HOLD_S` up, then `HOLD_M` down.
- Rows are real and x is to scale within a few µm, but z is stretched to make room for the routing channels.
