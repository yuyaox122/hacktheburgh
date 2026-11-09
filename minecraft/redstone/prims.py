"""Redstone primitives: small fixed tiles with named ports, checked exhaustively in vanilla 1.21.11 (check.py).

Every tile is defined flowing east (+x) and can be rotated. Local axes: x east, z south, y up; y=0 sits on the
floor. Repeaters and comparators: `facing` points TOWARD the input (measured in 1.21.11: facing=west -> flows east).

Ports are cells just outside the tile. An input port is the block behind the tile's input repeater: the router
ends a net there (dust or a repeater pointing in). The output port is the block in front of the output repeater.
All inputs enter through a repeater (full strength 15, one-way), so comparators only ever see 0 or 15.

    INH(a, b) = a AND NOT b    comparator, subtract mode: rear = a, side = b
    NOT(b)    = INH(1, b)      rear = a redstone block (constant 15)
    OR(a, b)                   two repeaters merge into one dust
    LATCH(d, h)                repeater d, locked while h = 1 (hold), so transparent while h = 0
"""
from __future__ import annotations

from dataclasses import dataclass, field

DIRS = {"east": (1, 0), "south": (0, 1), "west": (-1, 0), "north": (0, -1)}
OPP = {"east": "west", "west": "east", "north": "south", "south": "north"}
CW = {"east": "south", "south": "west", "west": "north", "north": "east"}


@dataclass
class Tile:
    name: str
    blocks: list[tuple[int, int, int, str]]          # local (x, y, z, state); state may use {f:<dir>} for facing
    inputs: dict[str, tuple[int, int, int]]          # port name -> local cell outside the tile (driver goes here)
    output: tuple[int, int, int]                     # local cell in front of the output repeater
    out_probe: tuple[int, int, int, str]             # block + state that means "output is 1"
    fn: object                                       # python truth function over input dict
    size: tuple[int, int, int]                       # (x, y, z) footprint
    keepout: list[tuple[int, int, int]] = field(default_factory=list)   # cells outside the tile nothing may use
    delay_rt: int = 0


def rep(flow: str, delay: int = 1) -> str:
    return f"repeater[facing={OPP[flow]},delay={delay}]"


def cmp(flow: str, mode: str = "subtract") -> str:
    return f"comparator[facing={OPP[flow]},mode={mode}]"


# --- tiles, flowing east; 3 x 1 x 3, ports on the west (A) and north (B) faces, output on the east face ---

INH = Tile(
    "INH",
    blocks=[(0, 0, 1, rep("east")), (1, 0, 0, rep("south")), (1, 0, 1, cmp("east")), (2, 0, 1, rep("east"))],
    inputs={"A": (-1, 0, 1), "B": (1, 0, -1)},
    output=(3, 0, 1), out_probe=(2, 0, 1, "repeater[powered=true]"),
    fn=lambda v: v["A"] and not v["B"], size=(3, 1, 3), delay_rt=3,
)

NOT = Tile(
    "NOT",
    blocks=[(0, 0, 1, "redstone_block"), (1, 0, 0, rep("south")), (1, 0, 1, cmp("east")), (2, 0, 1, rep("east"))],
    inputs={"B": (1, 0, -1)},
    output=(3, 0, 1), out_probe=(2, 0, 1, "repeater[powered=true]"),
    fn=lambda v: not v["B"], size=(3, 1, 3), delay_rt=2,
    keepout=[(-1, 0, 1)],                        # the redstone block powers its west neighbour too
)

OR = Tile(
    "OR",
    blocks=[(0, 0, 1, rep("east")), (1, 0, 0, rep("south")), (1, 0, 1, "redstone_wire"), (2, 0, 1, rep("east"))],
    inputs={"A": (-1, 0, 1), "B": (1, 0, -1)},
    output=(3, 0, 1), out_probe=(2, 0, 1, "repeater[powered=true]"),
    fn=lambda v: v["A"] or v["B"], size=(3, 1, 3), delay_rt=2,
)

BUF = Tile(
    "BUF",
    blocks=[(0, 0, 1, rep("east")), (1, 0, 1, "redstone_wire"), (2, 0, 1, rep("east"))],
    inputs={"A": (-1, 0, 1)},
    output=(3, 0, 1), out_probe=(2, 0, 1, "repeater[powered=true]"),
    fn=lambda v: v["A"], size=(3, 1, 3), delay_rt=2,
)

# Latch: D enters a repeater; a hold repeater points into its side. Hold = 1 freezes D's repeater.
LATCH = Tile(
    "LATCH",
    blocks=[(0, 0, 1, rep("east")), (1, 0, 0, rep("south")), (1, 0, 1, rep("east")), (2, 0, 1, rep("east"))],
    inputs={"D": (-1, 0, 1), "H": (1, 0, -1)},
    output=(3, 0, 1), out_probe=(2, 0, 1, "repeater[powered=true]"),
    fn=None, size=(3, 1, 3), delay_rt=3,
)

TILES = {t.name: t for t in (INH, NOT, OR, BUF, LATCH)}


def rotate(t: Tile, flow: str) -> Tile:
    """Rotate a tile defined flowing east so it flows `flow`. Rotation is about the tile's local origin cell."""
    turns = ["east", "south", "west", "north"].index(flow)

    def p(c):
        x, y, z = c[:3]
        for _ in range(turns):
            x, z = -z, x
        return (x, y, z) + tuple(c[3:])

    def s(state: str) -> str:
        if "facing=" not in state:
            return state
        head, rest = state.split("facing=", 1)
        d, tail = rest.split(",", 1) if "," in rest else (rest.rstrip("]"), "")
        for _ in range(turns):
            d = CW[d]
        return f"{head}facing={d}" + (f",{tail}" if tail else "]")

    return Tile(t.name, [p(b)[:3] + (s(b[3]),) for b in t.blocks], {k: p(v) for k, v in t.inputs.items()},
                p(t.output), p(t.out_probe)[:3] + (t.out_probe[3],), t.fn, t.size, [p(k) for k in t.keepout],
                t.delay_rt)


# ---------------------------------------------------------------- bus tiles (v2): every port on the north face
#
# 5 wide (x 0..4) x 4 deep (z 0..3), y = 0. Ports are the cells at z = -1: A at x=0, OUT at x=2, B at x=4,
# so neighbouring ports are 2 apart and each owns its own column northwards. Inputs enter through a repeater
# flowing south; the output leaves through a repeater flowing north.

_A_IN = [(0, 0, 0, rep("south"))]
_B_IN = [(4, 0, 0, rep("south")), (4, 0, 1, "redstone_wire")]
_OUT = [(2, 0, 0, rep("north"))]
_A_TO_REAR = [(0, 0, 1, "redstone_wire"), (0, 0, 2, "redstone_wire"), (0, 0, 3, "redstone_wire"),
              (1, 0, 3, "redstone_wire"), (2, 0, 3, "redstone_wire")]
BUS_PORTS = {"A": (0, 0, -1), "OUT": (2, 0, -1), "B": (4, 0, -1)}


def _bus(name, blocks, inputs, fn, delay):
    return Tile(name, blocks, {p: BUS_PORTS[p] for p in inputs}, BUS_PORTS["OUT"],
                (2, 0, 0, "repeater[powered=true]"), fn, (5, 1, 4), delay_rt=delay)


B_INH = _bus("INH", _A_IN + _A_TO_REAR + [(2, 0, 2, rep("north")), (2, 0, 1, cmp("north"))]
             + _B_IN + [(3, 0, 1, rep("west"))] + _OUT, ["A", "B"], lambda v: v["A"] and not v["B"], 4)
B_NOT = _bus("NOT", [(2, 0, 2, "redstone_block"), (2, 0, 1, cmp("north"))]
             + _B_IN + [(3, 0, 1, rep("west"))] + _OUT, ["B"], lambda v: not v["B"], 4)
B_OR = _bus("OR", _A_IN + [(0, 0, 1, "redstone_wire"), (1, 0, 1, "redstone_wire"), (2, 0, 1, "redstone_wire")]
            + _B_IN + [(3, 0, 1, "redstone_wire")] + _OUT, ["A", "B"], lambda v: v["A"] or v["B"], 2)
B_LATCH = _bus("LATCH", _A_IN + _A_TO_REAR + [(2, 0, 2, "redstone_wire"), (2, 0, 1, rep("north"))]
               + _B_IN + [(3, 0, 1, rep("west"))] + _OUT, ["A", "B"], None, 4)    # A = D, B = H (hold)
B_IN = Tile("IN", [(2, 0, 1, "lever[face=floor,facing=north,powered=false]")] + _OUT, {}, BUS_PORTS["OUT"],
            (2, 0, 0, "repeater[powered=true]"), None, (5, 1, 4), delay_rt=1)
B_OUT = Tile("OUT", _A_IN + [(0, 0, 1, "redstone_lamp")], {"A": BUS_PORTS["A"]}, (0, 0, 1),
             (0, 0, 0, "repeater[powered=true]"), None, (5, 1, 4), delay_rt=1)
BUS = {"INH": B_INH, "NOT": B_NOT, "OR": B_OR, "LATCH": B_LATCH, "IN": B_IN, "OUT": B_OUT}
