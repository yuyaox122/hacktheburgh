"""Export a real Tiny Tapeout chip as a Minecraft world: its GDS layout as blocks (KLayout), its netlist compiled
to scoreboard logic in a datapack that runs every tick, with a lever panel for the inputs and lamps for the outputs.

    build     chip -> datapack (out/<chip>/basic_chip) + meta.json       layout   GDS reader (KLayout)
    logic     netlist -> scoreboard commands, and RefSim, its Python twin  plant    add a hardware trojan
    server    the world server (grass field) + RCON                       check    in-game chip vs RefSim
    showcase  one world with this chip and two redstone builds next to it
    client / fly / shot.ps1   Windows tools to launch the game client and record a fly-through

Outputs go to minecraft/world/out/ (gitignored).
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out"
