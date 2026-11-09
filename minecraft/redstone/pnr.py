"""Place and route a primitive netlist as redstone, deterministically ("bus" fabric, DRC-clean by construction).

    python -m minecraft.redstone.pnr <chip> [--z 200]      # -> out/<chip>/layout.json

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

from . import OUT
from . import prims as P
from . import techmap as tm

PITCH = 6                      # tile pitch along x, one-row layout (Verilog designs with no real placement)
PITCH_2D = 8                   # tile pitch along x, 2-D layout: offset 6 of every slot is a free spine column
SPINE_DX = 6
UM_PER_SLOT = 1.0              # 2-D: one tile slot (8 blocks) per um of real x, so x is to scale at 8 blocks/um
TRACK = 4                      # trunk pitch along z
SPINE_Y = 6                    # spines (nets spanning several rows) run north-south on this layer
COLORS = ["red", "orange", "yellow", "lime", "green", "cyan", "light_blue", "blue", "purple", "magenta", "pink",
          "brown", "white", "light_gray", "gray", "black"]
TILE_DELAY = {"INH": 4, "NOT": 4, "OR": 2, "LATCH": 4, "IN": 1, "OUT": 1}
PORT_DX = {"A": 0, "OUT": 2, "B": 4}
# a spine column must be >= 2 blocks from every port column and outside every tile, in its own slot and the next
assert all(t.size[0] <= SPINE_DX for t in P.BUS.values())
assert all(min(abs(SPINE_DX - c), abs(SPINE_DX - (PITCH_2D + c))) >= 2 for c in PORT_DX.values())


@dataclass
class Unit:
    kind: str                   # INH | NOT | OR | LATCH | IN | OUT
    name: str                   # prim id or port bit
    x_real: float
    ins: dict[str, str] = field(default_factory=dict)       # tile port -> net
    out: str | None = None
    x: int = 0                  # world x of the tile's local origin (relative to x0)
    row: int = 0                # tile row, 0 = northmost
    cell: str = ""              # chip instance this primitive came from
    order: int = 0              # tie-break inside one cell


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


def _units(pn: tm.PrimNetlist) -> tuple[list[Unit], list[Unit]]:
    logic, pads = [], []
    for p in pn.prims:
        o = int(p.id[1:]) if p.id[1:].isdigit() else 0
        if p.kind == "NOT":
            u = Unit("NOT", p.id, p.x, {"B": p.ins["B"]}, p.out)
        elif p.kind == "LATCH":
            u = Unit("LATCH", p.id, p.x, {"A": p.ins["D"], "B": p.ins["H"]}, p.out)
        else:
            u = Unit(p.kind, p.id, p.x, {"A": p.ins["A"], "B": p.ins["B"]}, p.out)
        u.cell, u.order = p.cell, o
        logic.append(u)
    for b in pn.inputs:
        pads.append(Unit("IN", b, pn.pin_x.get(b, 0.0), {}, b))
    for b, n in pn.outputs.items():
        pads.append(Unit("OUT", b, pn.pin_x.get(b, 0.0), {"A": n}))
    return logic, pads


def _legalise(targets: list[float]) -> list[int]:
    """Integer slots for items kept in this order, at least 1 apart, as close as possible to their targets
    (minimum squared displacement: merge overlapping clusters, each sits at the mean of its members' targets)."""
    clusters = []                                       # [first index, count, sum of (target - offset)]
    for i, t in enumerate(targets):
        clusters.append([i, 1, t])
        while len(clusters) > 1:
            a, b = clusters[-2], clusters[-1]
            if round(a[2] / a[1]) + a[1] > round(b[2] / b[1]):
                clusters[-2:] = [[a[0], a[1] + b[1], a[2] + b[2] - b[1] * a[1]]]
            else:
                break
    slots = []
    for first, n, q in clusters:
        x = round(q / n)
        slots += [x + j for j in range(n)]
    return slots


def place(pn: tm.PrimNetlist) -> tuple[list[Unit], int, int]:
    """Returns (units, number of rows, tile pitch).

    With a real placement (TT08 chips): row 0 holds the IO pads at their pins' real x (the pins are all on the
    chip's north edge), rows 1.. are the chip's standard-cell rows in real order, north (higher GDS y) first.
    Within a row, tiles keep the real x order; x is to scale (one 8-block slot per um), legalised so tiles don't
    overlap. A cell's primitives stay together. Without one (Verilog): everything in one row, as before."""
    logic, pads = _units(pn)
    ys = sorted({p.y for p in pn.prims if p.y is not None}, reverse=True)
    if not ys or any(p.y is None for p in pn.prims):      # no (complete) real placement: one row
        units = logic + pads
        units.sort(key=lambda u: (u.x_real, u.kind != "OUT", u.name))
        for i, u in enumerate(units):
            u.x = i * PITCH
        return units, 1, PITCH
    row_of = {y: i + 1 for i, y in enumerate(ys)}
    y_of = {p.id: p.y for p in pn.prims}
    for u in logic:
        u.row = row_of[y_of[u.name]]
    # constant outputs have no wire: pack their lamps just west of the first real pad, in pin order
    const = sorted([u for u in pads if u.kind == "OUT" and u.ins["A"] in (tm.CONST0, tm.CONST1)],
                   key=lambda u: u.x_real)
    live = sorted([u for u in pads if u not in const], key=lambda u: (u.x_real, u.name))
    first = min(u.x_real for u in live) if live else 0.0
    for i, u in enumerate(const):
        u.x_real = first - (len(const) - i) * UM_PER_SLOT
    rows = [const + live] + [sorted([u for u in logic if u.row == r], key=lambda u: (u.x_real, u.cell, u.order))
                             for r in range(1, len(ys) + 1)]
    for r, us in enumerate(rows):
        for u, slot in zip(us, _legalise([u.x_real / UM_PER_SLOT for u in us])):
            u.row, u.x = r, slot * PITCH_2D
    return [u for us in rows for u in us], len(rows), PITCH_2D


def _tracks(nets: list[dict]) -> int:
    """Left-edge channel routing: nets whose spans are >= 3 apart share a track. Sets d['track']."""
    nets.sort(key=lambda d: (d["lo"], d["hi"]))
    end = []
    for d in nets:
        for k, e in enumerate(end):
            if d["lo"] >= e + 3:
                d["track"] = k
                end[k] = d["hi"]
                break
        else:
            d["track"] = len(end)
            end.append(d["hi"])
    return len(end)


def route(pn: tm.PrimNetlist, x0: int = 0, z_min: int = 200) -> Layout:
    """Rows + channels + spines (one row and one channel when there is no real placement):

      channel r (north of tile row r): one E-W trunk per net per row on y=3, built exactly like the one-row bus
      (each port owns a N-S column, ground run on y=0, staircase up to its trunk)
      spine (net with pins in several rows): N-S dust line on y=6 in a column no port uses (offset 6 of a
      slot); in every channel it serves it dips down to that channel's trunk by a 3-step staircase along z and
      climbs back up. y=6 sits 2 blocks of air above trunks and 5 above tiles, so it crosses them without contact.
    """
    units, R, pitch = place(pn)
    # pins of every net, by row
    drv, sinks = {}, defaultdict(list)
    const_ports = []                                   # (row, column) for constant-1 tile inputs
    for u in units:
        if u.out is not None and u.kind != "OUT":
            drv[u.out] = (u.row, u.x + PORT_DX["OUT"], u)
        for port, n in u.ins.items():
            col = u.x + PORT_DX[port]
            if n == tm.CONST1:
                const_ports.append((u.row, col))
            elif n != tm.CONST0:
                sinks[n].append((u.row, col, u, port))
    nets = []
    for n, ss in sinks.items():
        if n not in drv:
            raise RuntimeError(f"net {n} has sinks but no driver")
        pins = defaultdict(list)
        pins[drv[n][0]].append(drv[n][1])
        for r, c, _, _ in ss:
            pins[r].append(c)
        nets.append({"net": n, "drv": drv[n], "sinks": ss, "pins": dict(pins), "rows": sorted(pins)})

    # spine columns: one per multi-row net, chosen to keep its trunks short; a column can be reused by nets
    # whose channel ranges don't overlap
    used = defaultdict(list)                           # column -> [(first row, last row)]
    all_x = [u.x for u in units]
    cands = list(range(min(all_x) - 3 * pitch + SPINE_DX, max(all_x) + 4 * pitch, pitch))
    multi = sorted([d for d in nets if len(d["rows"]) > 1], key=lambda d: (-(d["rows"][-1] - d["rows"][0]), d["net"]))
    for d in multi:
        a, b = d["rows"][0], d["rows"][-1]
        mid = sorted(c for cs in d["pins"].values() for c in cs)
        mid = mid[len(mid) // 2]

        def cost(s):
            return (sum(max(cs + [s]) - min(cs + [s]) for cs in d["pins"].values()), abs(s - mid))
        free = [s for s in cands if all(b2 < a or a2 > b for a2, b2 in used[s])]
        if not free:
            raise RuntimeError(f"net {d['net']}: no free spine column")
        s = min(free, key=cost)
        used[s].append((a, b))
        d["spine"] = s

    # tracks, per channel
    chans = defaultdict(list)                          # row -> [per-channel dicts]
    for d in nets:
        d["seg"] = {}
        for r, cs in d["pins"].items():
            xs = cs + ([d["spine"]] if "spine" in d else [])
            seg = {"lo": min(xs), "hi": max(xs)}
            d["seg"][r] = seg
            chans[r].append(seg)
    T = [_tracks(chans[r]) for r in range(R)]

    # z: rows top-down; the northmost track of channel r sits 6 south of the previous row's tiles
    zt = []
    for r in range(R):
        north = z_min + 1 if r == 0 else zt[r - 1] + 3 + 6
        zt.append(north + TRACK * (T[r] - 1) + 6 if T[r] else north + 2)
    for d in nets:
        for r, seg in d["seg"].items():
            seg["zk"] = zt[r] - 6 - TRACK * seg["track"]

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
        z0 = zt[u.row]
        for x, y, z, s in t.blocks:
            if s == "redstone_block":
                last.append((x0 + u.x + x, Y + y, z0 + z, s))   # constants go in last so they update neighbours
                owner[(x0 + u.x + x, Y + y, z0 + z)] = f"tile:{u.name}"
            else:
                put(u.x + x, Y + y, z0 + z, s, f"tile:{u.name}")
        if u.kind == "IN":
            inputs[u.name] = (x0 + u.x + 2, Y, z0 + 1)
            labels.append((x0 + u.x + 2, Y, z0 + 3, u.name))
        if u.kind == "OUT":
            n = u.ins["A"]
            if n in (tm.CONST0, tm.CONST1):
                outputs[u.name] = int(n == tm.CONST1)
            else:
                outputs[u.name] = (x0 + u.x, Y, z0, "repeater[powered=true]")
            labels.append((x0 + u.x, Y, z0 + 3, u.name if n not in (tm.CONST0, tm.CONST1) else f"{u.name}\ntied {n[-1]}"))
    for r, col in const_ports:
        last.append((x0 + col, Y, zt[r] - 1, "redstone_block"))
        owner[(x0 + col, Y, zt[r] - 1)] = f"tile:const@{col},{r}"
    # a sign under each chip cell's group of tiles: instance name and cell type (as in the GDS and bASIC)
    if R > 1:
        seen = set()
        for u in units:
            if u.cell and u.cell not in seen:
                seen.add(u.cell)
                labels.append((x0 + u.x, Y, zt[u.row] + 4, f"{u.cell}\n{pn.cell_type.get(u.cell, '')}"))

    # wires
    rep_count, spine_cells = 0, 0
    net_delay = {}                                     # net -> {(unit, port): repeaters on the path}
    for i, d in enumerate(sorted(nets, key=lambda d: d["net"])):
        color = COLORS[i % len(COLORS)] + "_concrete"
        adj = defaultdict(set)

        def chain(cells):
            for p_, q_ in zip(cells, cells[1:]):
                adj[p_].add(q_)
                adj[q_].add(p_)

        def port_run(r, c, zk):
            return [(c, 0, z) for z in range(zt[r] - 1, zk + 2, -1)] + [(c, 1, zk + 2), (c, 2, zk + 1), (c, 3, zk)]

        rows = d["rows"]
        for r, seg in d["seg"].items():
            zk = seg["zk"]
            for c in d["pins"][r]:
                chain(port_run(r, c, zk))
            chain([(x, 3, zk) for x in range(seg["lo"], seg["hi"] + 1)])
            if "spine" in d:
                s = d["spine"]
                if r > rows[0]:                           # spine continues north: dip
                    chain([(s, 3, zk), (s, 4, zk - 1), (s, 5, zk - 2), (s, SPINE_Y, zk - 3)])
                if r < rows[-1]:                          # spine continues south
                    chain([(s, 3, zk), (s, 4, zk + 1), (s, 5, zk + 2), (s, SPINE_Y, zk + 3)])
        if "spine" in d:
            s = d["spine"]
            for r1, r2 in zip(rows, rows[1:]):
                run = [(s, SPINE_Y, z) for z in range(d["seg"][r1]["zk"] + 3, d["seg"][r2]["zk"] - 2)]
                chain(run)
                spine_cells += len(run)

        # orient as a tree from the driver's port
        dr, dc, _ = d["drv"]
        root = (dc, 0, zt[dr] - 1)
        tree = {root: {"parent": None, "kids": []}}
        order = [root]
        for cell in order:
            for nb in sorted(adj[cell]):
                if nb == tree[cell]["parent"]:
                    continue
                if nb in tree:
                    raise RuntimeError(f"net {d['net']}: wiring has a loop at {nb}")
                tree[nb] = {"parent": cell, "kids": []}
                tree[cell]["kids"].append(nb)
                order.append(nb)
        if len(tree) != len(adj) and adj:
            raise RuntimeError(f"net {d['net']}: wiring is not connected")
        # a repeater fits on a straight, flat, unbranched cell: same y as its parent and child, in line
        for cell, node in tree.items():
            par = node["parent"] or (cell[0], 0, cell[2] + 1)    # the driver's port is fed from the south
            node["elig"] = False
            node["flow"] = None
            if len(node["kids"]) == 1:
                kid = node["kids"][0]
                dv = tuple(k - c for k, c in zip(kid, cell))
                if par[1] == cell[1] == kid[1] and tuple(c - p for c, p in zip(cell, par)) == dv:
                    node["elig"] = True
                    node["flow"] = {(1, 0): "east", (-1, 0): "west", (0, 1): "south", (0, -1): "north"}[(dv[0], dv[2])]
        _check_tree(d["net"], tree)

        dist = {}                                        # cells to the next eligible cell or port, per branch
        for cell in reversed(order):
            dist[cell] = max([1 if tree[k]["elig"] else 1 + dist[k] for k in tree[cell]["kids"]], default=0)
        ahead = dist.__getitem__

        reps = set()
        stack = [(root, 15)]                             # driver tile's output repeater feeds the port at 15
        while stack:
            cell, sig = stack.pop()
            node = tree[cell]
            if node["elig"] and node["kids"] and sig - ahead(cell) < 1:
                reps.add(cell)
                sig_out = 15
            else:
                if sig < 1:
                    raise RuntimeError(f"net {d['net']}: signal dies at {cell}")
                sig_out = sig
            for kid in node["kids"]:
                stack.append((kid, sig_out if cell in reps else sig_out - 1))

        def count(cell):
            n = 0
            while cell is not None:
                n += cell in reps
                cell = tree[cell]["parent"]
            return n
        net_delay[d["net"]] = {(u.name, port): count((c, 0, zt[r] - 1)) for r, c, u, port in d["sinks"]}
        rep_count += len(reps)

        for cell, node in tree.items():
            x, y, z = cell
            put(x, Y + y, z, P.rep(node["flow"]) if cell in reps else "redstone_wire", d["net"])
            put(x, Y + y - 1, z, color, d["net"])          # support (or the recoloured floor under a ground run)

    _drc(owner, blocks)
    _shape_dust(blocks)
    settle = _sta(units, nets, net_delay)
    xs = [k[0] for k in blocks]
    zs = [k[2] for k in blocks]
    ys_ = [k[1] for k in blocks]
    shift = max([abs(u.x / pitch - u.x_real / UM_PER_SLOT) * UM_PER_SLOT for u in units
                 if R > 1 and u.row > 0], default=0.0)
    stats = {"units": len(units), "rows": R, "max_x_shift_um": round(shift, 1), "nets": len(nets), "spines": sum("spine" in d for d in nets),
             "tracks": T, "repeaters_in_wires": rep_count, "spine_cells": spine_cells,
             "blocks": len(blocks) + len(last), "settle_rt": settle,
             "size": (max(xs) - min(xs) + 1, max(zs) - min(zs) + 1)}
    return Layout(pn.chip, blocks, last, inputs, outputs, settle, stats,
                  (min(xs), -1, min(zs), max(xs), max(ys_), max(zs)), labels)


def _check_tree(net, tree):
    """Inside one net, two dust cells that could connect (side by side, at most one block apart in height)
    must be parent and child; otherwise the wire could short past its own repeaters and latch itself on."""
    for (x, y, z), node in tree.items():
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            for dy in (-1, 0, 1):
                nb = (x + dx, y + dy, z + dz)
                if nb in tree and node["parent"] != nb and tree[nb]["parent"] != (x, y, z):
                    raise RuntimeError(f"net {net}: {(x, y, z)} and {nb} touch but are not joined")


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


def _sta(units, nets, net_delay) -> int:
    """Longest input->output path in redstone ticks (latch outputs treated as sources)."""
    by_out = {u.out: u for u in units if u.out is not None and u.kind != "OUT"}
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
                best = max(best, arrive(n, depth + 1) + net_delay[n][(u.name, port)])
            t += best
        memo[net] = t
        return t

    worst = 0
    for u in units:
        if u.kind in ("OUT", "LATCH"):
            for port, n in u.ins.items():
                if n in (tm.CONST0, tm.CONST1):
                    continue
                worst = max(worst, arrive(n) + net_delay[n][(u.name, port)] + TILE_DELAY[u.kind])
    return worst


def save(lay: Layout, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    data = {"chip": lay.chip, "settle_rt": lay.settle_rt, "stats": lay.stats, "bbox": lay.bbox,
            "inputs": lay.inputs, "outputs": lay.outputs, "labels": lay.labels,
            "blocks": [[*k, s] for k, s in sorted(lay.blocks.items(), key=lambda kv: (kv[0][1], kv[0]))],
            "last": [list(b) for b in lay.last]}
    (out / "layout.json").write_text(json.dumps(data))


def route_chip(chip: str, z: int = 200) -> Layout:
    """Techmap a fetched chip and route it with its north edge at z; writes out/<chip>/layout.json."""
    pn = tm.build(chip)
    tm.prune(pn)
    lay = route(pn, z_min=z)
    print(lay.stats)
    save(lay, OUT / chip)
    return lay


if __name__ == "__main__":
    import sys
    route_chip(sys.argv[1], int(sys.argv[sys.argv.index("--z") + 1]) if "--z" in sys.argv else 200)
