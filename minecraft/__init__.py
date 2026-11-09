"""bASIC in Minecraft: a real Tiny Tapeout chip as a Minecraft world, two ways.

    minecraft.redstone   compile a chip's netlist (or any small Verilog) into real redstone, checked in vanilla
    minecraft.world      draw the chip's real layout (GDS) as blocks and run its logic as a datapack
    minecraft.common     shared by both: RCON, the vanilla server, chip and PDK data, netlist and Liberty readers

Run the scripts from the repository root, e.g. `python -m minecraft.redstone.bridge`. See minecraft/README.md.
"""
