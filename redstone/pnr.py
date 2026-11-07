"""Place and route a primitive netlist as redstone, deterministically ("bus" fabric, DRC-clean by construction).

Layout (x east, z south, y up; y=0 is the first layer above the floor):

      trunk k (y=3) ════════════════════════   z_k = zt - 6 - 4k, one E-W dust line per net on 3-high
                         ╲ step y2            colored-concrete supports; nets whose x-spans don't overlap
                          ╲ step y1           share a track (left-edge channel routing)
      ground runs (y=0) │ │ │ │ │ │ │         each port owns its own column and runs N-S at floor level,
                        │ │ │ │ │ │ │         under the trunks (2 blocks of air between) until its track
      ports (z=zt-1)    A Y B A Y B A ...     every tile port is 2 apart along x
      tiles (zt..zt+3)  [INH][OR ][NOT] ...    tiles in one row, in the chip's real x order

Nothing crosses at the same height and no two nets come within one block of each other (26-neighbourhood),
so nets cannot interact. A staircase on solid blocks joins each column to its trunk (bidirectional in Java).
Repeaters go in by look-ahead so every port gets strength >= 1; they also make every net one-way.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import prims as P
import techmap as tm

PITCH = 6                      # tile pitch along x
TRACK = 4                      # trunk pitch along z
COLORS = ["red", "orange", "yellow", "lime", "green", "cyan", "light_blue", "blue", "purple", "magenta", "pink",
          "brown", "white", "light_gray", "gray", "black"]
TILE_DELAY = {"INH": 4, "NOT": 4, "OR": 2, "LATCH": 4, "IN": 1, "OUT": 1}


@dataclass
class Unit:
    kind: str                   # INH | NOT | OR | LATCH | IN | OUT
    name: str                   # prim id or port bit
    x_real: float
    ins: dict[str, str] = field(default_factory=dict)       # tile port -> net
    out: str | None = None
    x: int = 0                  # world x of the tile's local origin


@dataclass
class Layout:
    chip: str
    blocks: dict                # (x, y, z) -> state
    last: list                  # blocks to place last (constants), [(x, y, z, state)]
    inputs: dict                # bit -> lever position
    outputs: dict               # bit -> (x, y, z, probe state) or const value
    settle_rt: int
    stats: dict
    bbox: tuple
    labels: list = field(default_factory=list)


def place(pn: tm.PrimNetlist) -> list[Unit]:
    units = []
    for p in pn.prims:
        if p.kind in ("NOT",):
            units.append(Unit("NOT", p.id, p.x, {"B": p.ins["B"]}, p.out))
        elif p.kind == "LATCH":
            units.append(Unit("LATCH", p.id, p.x, {"A": p.ins["D"], "B": p.ins["H"]}, p.out))
        else:
            units.append(Unit(p.kind, p.id, p.x, {"A": p.ins["A"], "B": p.ins["B"]}, p.out))
    for b in pn.inputs:
        units.append(Unit("IN", b, pn.pin_x.get(b, 0.0), {}, b))
    for b, n in pn.outputs.items():
        units.append(Unit("OUT", b, pn.pin_x.get(b, 0.0), {"A": n}))
    units.sort(key=lambda u: (u.x_real, u.kind != "OUT", u.name))
    for i, u in enumerate(units):
        u.x = i * PITCH
    return units


def route(pn: tm.PrimNetlist, x0: int = 0, z_min: int = 200) -> Layout:
    units = place(pn)
    port_dx = {"A": 0, "OUT": 2, "B": 4}
    # nets: driver column + sink columns
    drv, sinks = {}, defaultdict(list)
    const_ports = []                                   # (column, value) for constant tile inputs
    for u in units:
        if u.out is not None and u.kind != "OUT":
            drv[u.out] = (u.x + port_dx["OUT"], u)
        for port, n in u.ins.items():
            col = u.x + port_dx[port]
            if n == tm.CONST1:
                const_ports.append((col, u))
            elif n == tm.CONST0:
                pass
            else:
                sinks[n].append((col, u, port))
    nets = []
    for n, ss in sinks.items():
        if n not in drv:
            raise RuntimeError(f"net {n} has sinks but no driver")
        cols = [drv[n][0]] + [c for c, _, _ in ss]
        nets.append({"net": n, "drv": drv[n][0], "sinks": [c for c, _, _ in ss], "lo": min(cols), "hi": max(cols),
                     "sink_units": ss, "drv_unit": drv[n][1]})
    # left-edge track assignment
    nets.sort(key=lambda d: (d["lo"], d["hi"]))
    track_end = []
    for d in nets:
        for k, end in enumerate(track_end):
            if d["lo"] >= end + 3:
                d["track"] = k
                track_end[k] = d["hi"]
                break
        else:
            d["track"] = len(track_end)
            track_end.append(d["hi"])
    T = len(track_end)
    zt = z_min + 6 + TRACK * (T - 1) + 1              # tile row z; trunks extend north down to >= z_min
    Y = 0
    blocks: dict = {}
    owner: dict = {}

    def put(x, y, z, state, who):
        k = (x0 + x, y, z)
        if k in blocks and blocks[k] != state:
            raise RuntimeError(f"block clash at {k}: {blocks[k]} vs {state} ({owner.get(k)} vs {who})")
        blocks[k] = state
        owner[k] = who

    # tiles
    last = []
    inputs, outputs, labels = {}, {}, []
    for u in units:
        t = P.BUS[u.kind]
        for x, y, z, s in t.blocks:
            if s == "redstone_block":
                last.append((x0 + u.x + x, Y + y, zt + z, s))   # constants go in last so they update neighbours
                owner[(x0 + u.x + x, Y + y, zt + z)] = f"tile:{u.name}"
            else:
                put(u.x + x, Y + y, zt + z, s, f"tile:{u.name}")
        if u.kind == "IN":
            inputs[u.name] = (x0 + u.x + 2, Y, zt + 1)
            labels.append((x0 + u.x + 2, Y, zt + 3, u.name))
        if u.kind == "OUT":
            n = u.ins["A"]
            if n in (tm.CONST0, tm.CONST1):
                outputs[u.name] = int(n == tm.CONST1)
            else:
                outputs[u.name] = (x0 + u.x, Y, zt, "repeater[powered=true]")
            labels.append((x0 + u.x, Y, zt + 3, u.name))
    for col, u in const_ports:
        last.append((x0 + col, Y, zt - 1, "redstone_block"))

    # wires
    rep_count = 0
    net_delay = {}                                     # net -> {sink column: repeaters on the path}
    for i, d in enumerate(nets):
        k = d["track"]
        zk = zt - 6 - TRACK * k
        color = COLORS[i % len(COLORS)] + "_concrete"
        lo, hi = d["lo"], d["hi"]
        # cell lists with flow direction and eligibility, as a tree rooted at the driver port
        # node: (x, y, z) ; children ; eligible ; flow
        tree = {}

        def add(cell, parent, eligible, flow):
            tree[cell] = {"parent": parent, "kids": [], "elig": eligible, "flow": flow}
            if parent is not None:
                tree[parent]["kids"].append(cell)

        c = d["drv"]
        prev = None
        for z in range(zt - 1, zk + 2, -1):          # ground run north: zt-1 .. zk+3
            add((c, 0, z), prev, z != zk + 3, "north"); prev = (c, 0, z)
        add((c, 1, zk + 2), prev, False, None); prev = (c, 1, zk + 2)
        add((c, 2, zk + 1), prev, False, None); prev = (c, 2, zk + 1)
        add((c, 3, zk), prev, False, None)
        root_j = (c, 3, zk)
        sink_cols = set(d["sinks"])
        for direction, rng in (("east", range(c + 1, hi + 1)), ("west", range(c - 1, lo - 1, -1))):
            prev = root_j
            for x in rng:
                junction = x in sink_cols
                add((x, 3, zk), prev, not junction, direction); prev = (x, 3, zk)
        for s in d["sinks"]:
            j = (s, 3, zk)
            if s == c:
                raise RuntimeError(f"net {d['net']}: sink and driver share column {s}")
            add((s, 2, zk + 1), j, False, None)
            add((s, 1, zk + 2), (s, 2, zk + 1), False, None)
            prev = (s, 1, zk + 2)
            for z in range(zk + 3, zt):                  # ground run south: zk+3 .. zt-1
                add((s, 0, z), prev, z != zk + 3, "south"); prev = (s, 0, z)

        # look-ahead distance (cells) from each node to the next eligible node / port on every branch
        def ahead(cell):
            best = 0
            for kid in tree[cell]["kids"]:
                best = max(best, 1 if tree[kid]["elig"] else 1 + ahead(kid))
            return best

        reps = set()
        stack = [((c, 0, zt - 1), 15)]                   # driver tile's output repeater feeds the port at 15
        while stack:
            cell, s = stack.pop()
            node = tree[cell]
            if node["elig"] and node["kids"] and s - ahead(cell) < 1 + 0:
                reps.add(cell)
                s_out = 15
            else:
                if s < 1:
                    raise RuntimeError(f"net {d['net']}: signal dies at {cell}")
                s_out = s
            for kid in node["kids"]:
                stack.append((kid, (s_out if cell in reps else s_out - 1)))
        # sinks need strength >= 1 at their port; checked by the walk above (s < 1 raises)

        # repeaters on each path to a sink, for timing
        def count(cell):
            n = 0
            while cell is not None:
                n += cell in reps
                cell = tree[cell]["parent"]
            return n
        net_delay[d["net"]] = {s: count((s, 0, zt - 1)) for s in d["sinks"]}
        rep_count += len(reps)

        for cell, node in tree.items():
            x, y, z = cell
            if cell in reps:
                put(x, Y + y, z, P.rep(node["flow"]), d["net"])
            else:
                put(x, Y + y, z, "redstone_wire", d["net"])
            if y > 0:
                put(x, Y + y - 1, z, color, d["net"])       # support
            else:
                put(x, Y - 1, z, color, d["net"])           # recolour the floor under the ground run
        d["zk"] = zk

    _drc(owner, blocks)
    _shape_dust(blocks)
    settle = _sta(pn, units, nets, net_delay)
    xs = [k[0] for k in blocks]
    zs = [k[2] for k in blocks]
    stats = {"units": len(units), "nets": len(nets), "tracks": T, "repeaters_in_wires": rep_count,
             "blocks": len(blocks) + len(last), "settle_rt": settle,
             "size": (max(xs) - min(xs) + 1, max(zs) - min(zs) + 1)}
    return Layout(pn.chip, blocks, last, inputs, outputs, settle, stats,
                  (min(xs), -1, min(zs), max(xs), 4, max(zs)), labels)


def _drc(owner, blocks):
    """No two different nets (or a net and a foreign tile) within one block of each other in 3-D."""
    by_net = defaultdict(list)
    for k, w in owner.items():
        by_net[w].append(k)
    for k, w in owner.items():
        if w.startswith("tile:"):
            continue
        x, y, z = k
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    o = owner.get((x + dx, y + dy, z + dz))
                    if o is None or o == w:
                        continue
                    if o.startswith("tile:"):
                        # a net may touch only the tile port it connects to: the port cell's tile neighbour
                        # directly south/north (dz=+-1, same x, same y) - anything else is a violation
                        if dx == 0 and dy == 0 and abs(dz) == 1:
                            continue
                        if y < 0 or y + dy < 0:               # floor under a ground run next to a tile's floor
                            continue
                    raise RuntimeError(f"DRC: {w} at {k} touches {o} at {(x + dx, y + dy, z + dz)}")


SOLID_SUPPORT = lambda s: s.endswith("_concrete") or s in ("stone", "redstone_block")


def _shape_dust(blocks):
    """Give every dust block its connection state explicitly (setblock does not compute shapes)."""
    def connects(state, d):
        if state.startswith("redstone_wire"):
            return True
        if state.startswith("repeater"):
            f = state.split("facing=")[1].split(",")[0].rstrip("]")
            return d in (f, P.OPP[f])
        if state.startswith(("comparator", "lever", "redstone_block", "redstone_torch")):
            return True
        return False

    upd = {}
    for k, s in blocks.items():
        if s != "redstone_wire":
            continue
        x, y, z = k
        sides = {}
        for d, (dx, dz) in P.DIRS.items():
            same = blocks.get((x + dx, y, z + dz))
            up = blocks.get((x + dx, y + 1, z + dz))
            down = blocks.get((x + dx, y - 1, z + dz))
            above = blocks.get((x, y + 1, z))
            if same and connects(same, P.OPP[d]):
                sides[d] = "side"
            elif up and up.startswith("redstone_wire") and not (above and SOLID_SUPPORT(above)):
                sides[d] = "up"
            elif down and down.startswith("redstone_wire") and not (same and SOLID_SUPPORT(same)):
                sides[d] = "side"
            else:
                sides[d] = "none"
        on = [d for d, v in sides.items() if v != "none"]
        if len(on) == 1:                                   # vanilla extends a one-sided wire into a line
            sides[P.OPP[on[0]]] = "side"
        if not on:
            sides = {d: "side" for d in sides}
        upd[k] = "redstone_wire[" + ",".join(f"{d}={sides[d]}" for d in ("north", "south", "east", "west")) + ",power=0]"
    blocks.update(upd)


def _sta(pn, units, nets, net_delay) -> int:
    """Longest input->output path in redstone ticks (latch outputs treated as sources)."""
    by_out = {u.out: u for u in units if u.out is not None and u.kind != "OUT"}
    col_of = {}
    for d in nets:
        for c, u, port in d["sink_units"]:
            col_of[(d["net"], u.name, port)] = c
    memo = {}

    def arrive(net, depth=0):
        if net in memo:
            return memo[net]
        if depth > 5000:
            raise RuntimeError("combinational loop in STA")
        u = by_out[net]
        t = TILE_DELAY[u.kind]
        if u.kind not in ("IN", "LATCH"):
            best = 0
            for port, n in u.ins.items():
                if n in (tm.CONST0, tm.CONST1):
                    continue
                c = col_of[(n, u.name, port)]
                best = max(best, arrive(n, depth + 1) + net_delay[n][c])
            t += best
        memo[net] = t
        return t

    worst = 0
    for u in units:
        if u.kind in ("OUT", "LATCH"):
            for port, n in u.ins.items():
                if n in (tm.CONST0, tm.CONST1):
                    continue
                c = col_of[(n, u.name, port)]
                worst = max(worst, arrive(n) + net_delay[n][c] + TILE_DELAY[u.kind])
    return worst


def save(lay: Layout, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    data = {"chip": lay.chip, "settle_rt": lay.settle_rt, "stats": lay.stats, "bbox": lay.bbox,
            "inputs": lay.inputs, "outputs": lay.outputs, "labels": lay.labels,
            "blocks": [[*k, s] for k, s in sorted(lay.blocks.items(), key=lambda kv: (kv[0][1], kv[0]))],
            "last": [list(b) for b in lay.last]}
    (out / "layout.json").write_text(json.dumps(data))


if __name__ == "__main__":
    import sys
    z = int(sys.argv[sys.argv.index("--z") + 1]) if "--z" in sys.argv else 200
    pn = tm.build(sys.argv[1])
    tm.prune(pn)
    lay = route(pn, z_min=z)
    print(lay.stats)
    save(lay, Path(__file__).resolve().parent / "out" / sys.argv[1])
