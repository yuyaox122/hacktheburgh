# How a chip is built, and how bASIC reads it back

A plain-English briefing for Ian, so you can explain the whole project to a judge who has never heard of Verilog.
Read this top to bottom; the last section says which chip to demo and why.

---

## Part 1 — How an ASIC chip is designed

### The four layers, from the designer's idea to the physical chip

A chip is designed as a stack of descriptions. Each layer is produced by the one above it, and each is less
readable than the last.

**1. Behaviour (Verilog, what a human writes).**
The designer writes the chip in a programming language called Verilog. It looks like code:

```verilog
always @(posedge clk) begin
  if (count == 59) count <= 0; else count <= count + 1;
end
```

This says "every clock tick, count up, and wrap back to 0 after 59". A person can read this. It's the *intent*.

**2. Gates (a netlist, what synthesis produces).**
A tool called a *synthesiser* turns the Verilog into a list of tiny building blocks called **standard cells**.
A standard cell is the smallest useful circuit: an AND gate, a flip-flop, an OR gate. The synthesiser's output is
a **netlist**: a flat list of "cell C17 is an AND gate, its input A comes from cell C3, its input B comes from the
pin ui_in[0], its output X goes to cell C42".

A netlist is hard for a person to read, not because it's complex, but because:
- every name is gone (the designer's `count` is now just "cell c78, cell c86, …"),
- there are hundreds of tiny cells with no grouping,
- the structure (this is a counter) has been flattened away.

**3. Layout (GDS, where the cells are placed and wired).**
A tool called *place and route* puts each cell at a physical position on the silicon and draws the metal wires
between them. The output is a **GDS file** — essentially a picture of the chip, layer by layer. This is what gets
sent to the factory (the *fab*).

**4. Silicon.**
The fab etches the pattern into a wafer of silicon. A real chip comes back. You can't open it and read it.

### The three things every chip has

- **Combinational logic** (gates like AND/OR/XOR) — computes a result from the current inputs, instantly. No memory.
- **Flip-flops** (also called registers) — the memory. Each flip-flop stores one bit, and only updates on a clock
  edge. A "register" is a bunch of flip-flops holding a multi-bit value (like `count`).
- **A clock** — a signal that ticks on and off. Every tick, all the flip-flops update at once, based on the
  combinational logic. This tick-tick-tick is what makes a chip *do* a sequence of things over time.

Nearly every chip is: **flip-flops (state) + gates (logic), stepping once per clock.**

### Tiny Tapeout's fixed shape

Tiny Tapeout is a service that puts hobbyist designs onto a shared chip, cheaply. Every design has to use the
**same pinout** — the same fixed set of input and output wires:

| Pin | What it is |
|---|---|
| `clk` | the clock |
| `rst_n` | reset (active low: it's "on" when 0, holds everything at 0) |
| `ena` | enable |
| `ui_in[7:0]` | 8 input bits the user can set |
| `uo_out[7:0]` | 8 output bits the user can read |
| `uio_in/out/oe[7:0]` | 8 bidirectional pins (can be input OR output, chosen by `uio_oe`) |

So every Tiny Tapeout chip is a box with at most 8 input wires, 8 output wires, and 8 flexible wires — plus a
clock. That small fixed shape is why we can read them all back the same way.

---

## Part 2 — What our project (bASIC) does, and how

### The one-sentence version

> A chip you can't read is a chip you can't trust. bASIC turns a chip back into readable Verilog, **proves** the
> Verilog is exactly the circuit (with Yosys), and then lets an AI agent explain it — but the AI is never allowed
> to be wrong, because every claim it makes has to pass a proof.

### The pipeline, stage by stage

```
 GDS / netlist  →  decompiler  →  readable Verilog  →  Yosys proof  →  Witness agent  →  web app
 (unreadable)     (recovers       (names, blocks,       (code = gates)    (explains,       (Explore, Decompile,
                  structure)       a person can read)                      hunts, fixes)     Verify, editor, terminal)
```

**1. Ingest.** We take the real chip's data — its GDS layout (the physical picture) and/or its gate-level
netlist — and load it.

**2. The decompiler (Yuyao's core).** This is the hard, non-AI part. It takes the name-less netlist and recovers
structure:
- It finds **flip-flops** and groups them into registers (this collection of 6 flops is one `count0`).
- It finds **blocks**: "these gates together form a counter that wraps at 59", "these form a shift register",
  "these form a comparator". It writes each block as one line of Verilog a person can read.
- It re-names things sensibly: `count0`, `data1`, `bit0`.
- The output is decompiled Verilog — the design's *intent*, reconstructed from its gates.

**3. Yosys (the proof).** Yosys is an open-source formal-verification tool. It doesn't just *test*; it **proves**,
mathematically, that the decompiled Verilog computes exactly the same thing as the original gates, for every
possible input and every state. This is the difference between "we tested it 1,000 times" and "it is provably
identical". Yosys can also return a **counterexample** — the exact input where two versions differ — which is
what makes our "find the hidden trigger" and "find the switched gate" demos work.

**4. Witness (the agent, Ian's layer).** An AI agent that reads and talks about the chip. The crucial rule:
**the agent proposes, Yosys decides.** The agent can say "this block is a counter", but the claim is only shown
as true if Yosys proves it. If the agent is wrong, Yosys hands back the input that breaks the claim. So:
- **Explain** — the agent explains any part of the chip in plain English, and the claim is proven (or refuted).
- **Hunt** — look for hidden logic (a backdoor) by proving the chip matches its author's source.
- **Fix** — if a design differs from the chip (one gate switched), find the gate and fix it, proven by Yosys.
- The agent can never bluff: its whole job is to write things Yosys then grades.

**5. The web app.** The parts you've been clicking:
- **Explore** — the real chip in 3D, each gate a coloured block; click a gate, run the clock, watch wires glow.
- **Decompile** — the readable Verilog beside the author's original, with "Proven equivalent".
- **Verify** — Alex's page: run 1,000 random inputs, spot a wrong one, trace it back to the exact faulty gate.
- **The editor** — edit the Verilog; "Check with Yosys" says what your edit does.
- **The Agent drawer / terminal** — type commands at the agent (`/explain`, `/hunt`, `/drill`, `/fault`).

### The honest pitch framing

- The decompiler and the proof use **no AI at all**. That's what makes them trustworthy.
- The AI is only the *explainer*, and it's kept honest by the proof.
- A "proven" result means Yosys proved it for every input. A "tested" result means random inputs passed, which is
  NOT a proof — we say so, because judges with formal-methods background will check exactly this.

---

## Part 3 — Which chips work (and the one to demo)

All **9 dev chips** decompile and prove successfully (the decompiler's verdict is "proven" on each). But for a
demo you want the one that is small, fully explained, and easy to narrate.

**The demo chip: `tt_um_Electom_cla_4bits` — the 4-bit adder.** Use this one.

- What it does: adds the two halves of its input plus a carry-in, and outputs the 5-bit sum.
  `acc0 = ui_in[3:0] + ui_in[7:4] + uio_in[0]`. That's the whole chip in one line.
- It's tiny: **59 logic cells**, and the decompiler explains **100% of them**.
- Its decompile is **proven**, and it's **proven equal to the author's source** (the strongest possible check).
- Everything works on it end to end, which is why the whole demo is built around it:
  - **Verify page** — rev B is this chip with one gate switched.
  - **Editor** — "Check with Yosys" and the Fix demo were rehearsed on it.
  - **Red-team drill** — plant a trigger, find it blind.
  - **Upload your own Verilog** — download the decompiled file, upload it back, "match" in under a second.

**The other dev chips** (all proven, for when you want variety): `tt_um_kapilan_alarm` (a clock with an alarm —
good "seconds/minutes/hours" story), `tt_um_JAC_EE_segdecode` (a seven-segment display decoder), `tt_um_mac`,
`tt_um_i2s_to_pwm_arghunter`, `tt_um_i2c_peripheral_stevej`, `tt_um_clock_divider_arghunter`,
`tt_um_fountaincoder_top_ad`, `tt_um_ring_divider`.

**The chips that don't work** (don't worry about these): the held-out test chips with unpublished hard macros
(`tt_um_micro_tiles_container` and `_group2`) can't be read because their internal design was never published. A
few held-out chips also make the agent's model time out on very large regions. None of that matters for the demo:
**`cla_4bits` is the one that works.**

---

## The 30-second version for a judge

1. "Software has View Source. Chips don't — once it's built you can't read it, and a bug or a backdoor is
   permanent."
2. "bASIC reads a real chip back into readable code, and **proves** the code is exactly the circuit, with a
   theorem prover called Yosys."
3. "Then an AI agent explains any part of it in plain English — but the agent can't bluff, because every claim it
   makes has to pass that proof. Wrong answers come back with the exact input that breaks them."
4. *(the demo)* "Watch: we hide a trigger at a secret number, and the agent finds it from the proof. Or: here's the
   chip with one gate switched — the agent finds the gate and fixes it, and Yosys confirms."
