# Redstone backend: a real chip, built from real redstone

Compiles a Tiny Tapeout 08 chip's gate-level netlist into **actual redstone**: comparators, repeaters and dust, no command blocks or scoreboards. Each build is checked in a real vanilla 1.21.11 server against a simulation of the chip's own netlist. This is an add-on to #23 (Alex's `minecraft/`, which draws the real layout and runs the logic as a datapack).

## Status (verified in vanilla 1.21.11)

| Check | Result |
|---|---|
| Primitives (BUF, NOT, INH, OR, LATCH), every rotation, truth tables plus random transitions | all pass |
| Bus tiles (north-facing ports) | all pass |
| Techmap: netlist cells vs primitives, 2,000 random vectors (`sim.py`) | 0 mismatches on both chips |
| `tt_um_sign_addsub` (combinational): 105 primitives, 833 × 74 blocks, ~32k blocks | **200/200 vectors** match |
| `tt_um_Electom_cla_4bits` (sequential): 5 flip-flops as two-phase latches, with reset | **60/60 cycles** match |
| Verilog path: `examples/mul2.v` (3×3 multiplier) vs iverilog on the source | **40/40 vectors** match |
| Verilog path: `examples/counter.v` (4-bit up/down counter, load, enable, async reset) vs iverilog | **40/40 cycles** match |

## How it works

1. **`chipdata.py`** fetches the TT08 netlist and GDS (Apache-2.0), plus the SKY130 Liberty JSON. It reads ports, cells, instance positions (GDS property 61) and pin labels.
2. **`techmap.py`** maps each cell, keeping the chip's cell boundaries:
   - buffers become wires, and tie cells become constants;
   - combinational cells go through Yosys `abc -g OR,ANDNOT` and become NOT / INH (a&!b) / OR;
   - each flip-flop becomes a master/slave LATCH pair, held by two global phase nets (`HOLD_M`, `HOLD_S`), with a local asynchronous reset or set.
3. **`pnr.py`** builds the "bus" fabric, which is correct by construction:
   - **Tiles:** one row of tiles in the chip's real x order, every port on the north face, 2 blocks apart.
   - **Trunks:** one east–west trunk per net, 3 blocks up. Nets whose spans don't overlap share a track (left-edge channel routing).
   - **Columns:** each port owns a north–south column at floor level, with a solid staircase up to its trunk.
   - **Checks:** look-ahead repeater insertion, a 3-D design-rule check (no two nets within 1 block), and explicit dust shapes.
   - **Timing:** static timing analysis gives the settle time.
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

## Run it

```sh
python3 chipdata.py fetch tt_um_sign_addsub          # data/ (gitignored)
python3 sim.py tt_um_sign_addsub                     # techmap check, no Minecraft needed
python3 pnr.py tt_um_sign_addsub                     # out/<chip>/layout.json
python3 mc.py start                                  # headless check server (needs JDK 21 and tools/server-1.21.11.jar)
python3 check.py prims && python3 check.py chip tt_um_sign_addsub 50
python3 demo.py start && python3 demo.py chip tt_um_sign_addsub   # playable server on localhost:25565
```

The server jar is Mojang's vanilla `server.jar` for 1.21.11 (SHA-1 `64bb6d76…`), downloaded into `tools/`. It is not committed, and running it means accepting the Minecraft EULA.

**To play:** in Minecraft Java 1.21.11, go to Multiplayer → `localhost`.
- Levers are the chip's input pins and lamps are its output pins, each labelled by a sign.
- After you flip a lever, the outputs take about 20 s to settle at normal speed. `/tick rate 100` makes that 5× faster.

## Limits (for now)

- Only one clock domain, driven straight from `clk` with no inverted or gated clocks.
- No latches or memories.
- On the playable server the two clock phases are levers, so stepping the clock means flipping `HOLD_M` up, `HOLD_S` down, `HOLD_S` up, then `HOLD_M` down.
- The build is one long row, not the chip's 2-D placement. Cell order follows the chip's real x position.
