"""Compile a Tiny Tapeout chip's netlist, or any small Verilog, into real redstone, checked in a vanilla server.

    chipdata  chip inputs (netlist, GDS positions, Liberty)      techmap   cells -> NOT / INH / OR / LATCH
    verilog   Verilog -> the same primitives (Yosys)              pnr       place and route -> layout.json
    sim       reference simulators                                 prims     the redstone tiles
    mc        the two servers + RCON                               place     put a layout in a world, drive it
    check     verify in vanilla       demo  playable server       bridge    HTTP API for the web app
    fpga      tiny FPGA bitstreams    mapview  top-down PNG of a layout

Outputs go to minecraft/redstone/out/ (gitignored). See README.md here.
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"
