"""Website <-> Minecraft bridge: the web app compiles Verilog to redstone, builds it in the demo server,
flips its levers and reads its lamps through this small HTTP server (stdlib only, port 8002).

    python -m minecraft.redstone.bridge     # serves http://127.0.0.1:8002/redstone/*  (Vite proxies /redstone)
REDSTONE_BRIDGE_HOST / REDSTONE_BRIDGE_PORT change the address; the demo server is mc.DEMO (see mc.py).

Routes (JSON in and out):
    GET  /redstone/health                      server reachable? LAN address for the Minecraft client
    GET  /redstone/builds                      every build the page can drive (prebuilt chips + web builds)
    GET  /redstone/presets                     ready-made designs in examples/ (bASIC's CLA with and without trigger 77, ...)
    POST /redstone/compile  {verilog, name}    or {chip, variant: clean|planted} or {preset}: Yosys -> redstone layout, plus a
                                               software check (redstone netlist vs iverilog on the source)
    POST /redstone/build/<id>                  place it in the world (one datapack function, about a second)
    GET  /redstone/layout/<id>                 the blocks, for the browser's 3-D view
    GET  /redstone/state/<id>                  levers and lamps read from the world, plus what the netlist predicts
    POST /redstone/set/<id>   {bits, sprint}   flip levers; sprint fast-forwards until the outputs settle
    POST /redstone/clock/<id> {sprint}         one rising clock edge (the HOLD_M / HOLD_S sequence)
    POST /redstone/camera/<id> {view}          teleport players: levers | overview | side
    POST /redstone/speed      {rate}           /tick rate (20 = normal)

Web builds go in their own lane at z >= 3000, west to east from x = 0, so they never touch the prebuilt chips
(z 200..1462) or Alex's real-layout build (z -16..76).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import OUT, demo, place, pnr, sim, verilog
from . import techmap as tm
from ..common.datapack import sign, write_meta
from .mc import DEMO, FLOOR_Y, MC

HERE = Path(__file__).resolve().parent
WEB = OUT / "web"
BASIC_CHIPS = HERE.parents[1] / "basic" / "out" / "chips"
PORT = int(os.environ.get("REDSTONE_BRIDGE_PORT", "8002"))
LANE_Z = 3000
GAP = 40
MAX_PRIMS = 600
MAX_BLOCKS = 450_000
CHECK_VECTORS = 200

# Builds already standing in the demo world (see README.md, "Demo world"). Driven from the page as they are.
PREBUILT = {
    "tt_um_sign_addsub": ("Signed adder/subtractor (real TT08 layout, 2-D)", "chip"),
    "tt_um_Electom_cla_4bits": ("4-bit carry-lookahead adder (real TT08 layout, 2-D)", "chip"),
    "counter": ("4-bit counter (examples/counter.v)", "verilog"),
    "tiny_fpga": ("Tiny FPGA: 2 CLBs, 30-lever bitstream (examples/fpga.v)", "verilog"),
}

# Ready-made designs in examples/, so the trojan demo works on a laptop without bASIC's generated data.
PRESETS = {
    "cla_trigger77": ("examples/cla_trigger77.v", "4-bit CLA adder, trigger 77 planted (decompiled by bASIC)",
                      {"chip": "tt_um_Electom_cla_4bits", "variant": "planted", "n": 77, "operand": "ui_in"}),
    "cla_clean": ("examples/cla_clean.v", "4-bit CLA adder, clean (decompiled by bASIC)",
                  {"chip": "tt_um_Electom_cla_4bits", "variant": "clean"}),
    "mul2": ("examples/mul2.v", "3x3 multiplier (examples/mul2.v)", {}),
    "counter_web": ("examples/counter.v", "4-bit up/down counter (examples/counter.v)", {}),
}

LOCK = threading.RLock()          # one RCON connection and one registry, shared by the handler threads
_mc: MC | None = None
_sims: dict[str, dict] = {}       # id -> {"sim": PrimSim, "ins": {bit: 0/1}}
_nets: dict[str, tm.PrimNetlist] = {}


class Fail(Exception):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


# ---------------------------------------------------------------- RCON

def rcon() -> MC:
    global _mc
    if _mc is None:
        try:
            _mc = MC(DEMO).connect()
        except (OSError, SystemExit) as e:
            _mc = None
            raise Fail(f"Minecraft demo server not reachable on RCON {DEMO.port} ({e}). "
                       "Start it with: python -m minecraft.redstone.demo start", 503)
    return _mc


def cmd(c: str) -> str:
    global _mc
    for attempt in (0, 1):
        try:
            return rcon().cmd(c)
        except (OSError, ConnectionError, socket.timeout):
            _mc = None
            if attempt:
                raise Fail("lost the RCON connection to the demo server", 503)
    return ""


# ---------------------------------------------------------------- registry

def _reg_path() -> Path:
    return WEB / "registry.json"


def registry() -> dict:
    try:
        reg = json.loads(_reg_path().read_text())
    except (OSError, ValueError):
        reg = {}
    for name, (title, kind) in PREBUILT.items():
        if name not in reg and (OUT / name / "layout.json").exists():
            reg[name] = {"id": name, "title": title, "kind": kind, "prebuilt": True, "built": True,
                         "dir": str(OUT / name)}
    return reg


def save_registry(reg: dict) -> None:
    WEB.mkdir(parents=True, exist_ok=True)
    keep = {k: v for k, v in reg.items() if not v.get("prebuilt")}
    _reg_path().write_text(json.dumps(keep, indent=1))


def entry(bid: str) -> dict:
    e = registry().get(bid)
    if not e:
        raise Fail(f"no build '{bid}'", 404)
    return e


def layout(e: dict) -> dict:
    return json.loads((Path(e["dir"]) / "layout.json").read_text())


def summary(e: dict) -> dict:
    lay = layout(e)
    ins = [b for b in lay["inputs"] if b not in (tm.HOLD_M, tm.HOLD_S)]
    x0, y0, z0, x1, y1, z1 = lay["bbox"]
    return {**{k: v for k, v in e.items() if k not in ("dir", "source")},
            "inputs": ins, "outputs": list(lay["outputs"]), "sequential": tm.HOLD_M in lay["inputs"],
            "settle_rt": lay["settle_rt"], "size": [x1 - x0 + 1, z1 - z0 + 1],
            "blocks": len(lay["blocks"]) + len(lay["last"]), "bbox": lay["bbox"], "stats": lay["stats"]}


def slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_")
    return s[:48] or "design"


# ---------------------------------------------------------------- netlists and the software check

def netlist(e: dict) -> tm.PrimNetlist:
    bid = e["id"]
    if bid not in _nets:
        if e["kind"] == "chip":
            pn = tm.build(bid)
            tm.prune(pn)
        else:
            src = e.get("source") or json.loads((OUT / bid / "verilog.json").read_text())["source"]
            pn = verilog.build(src)
        _nets[bid] = pn
    return _nets[bid]


def sim_state(e: dict) -> dict:
    """A PrimSim kept in step with the levers we flip, so the page can show what the lamps should read."""
    bid = e["id"]
    if bid not in _sims:
        pn = netlist(e)
        s = sim.PrimSim(pn)
        ins = {b: 0 for b in pn.inputs if b not in (tm.HOLD_M, tm.HOLD_S)}
        s.set(ins)
        _sims[bid] = {"sim": s, "ins": ins, "pn": pn}
    return _sims[bid]


def software_check(pn: tm.PrimNetlist, src: Path) -> dict | None:
    """Redstone netlist (PrimSim) vs iverilog running the ORIGINAL source, on random vectors.
    None when iverilog is not installed (the build still works; the page then shows no check badge)."""
    import shutil
    if not shutil.which("iverilog"):
        print("[bridge] iverilog not found: skipping the software check", file=sys.stderr)
        return None
    ins = [b for b in pn.inputs if b not in (tm.HOLD_M, tm.HOLD_S)]
    outs = list(pn.outputs)
    vecs = verilog.vectors(ins, CHECK_VECTORS)
    ref = verilog.IverilogRef(src, pn.chip, ins, outs, pn.stats.get("clock")).run(vecs)
    s = sim.PrimSim(pn)
    bad, first = 0, None
    for i, (v, want) in enumerate(zip(vecs, ref)):
        s.set(v)
        if pn.sequential:
            s.clock()
        got = s.read(outs)
        if got != want:
            bad += 1
            first = first if first is not None else i
    return {"vectors": len(vecs), "mismatches": bad, "first_mismatch": first,
            "against": "iverilog running the source", "sequential": pn.sequential}


# ---------------------------------------------------------------- compile

def basic_verilog(chip: str, variant: str) -> tuple[str, str]:
    name = chip + ("__planted" if variant == "planted" else "")
    p = BASIC_CHIPS / f"{name}.json"
    if not p.exists():
        raise Fail(f"bASIC has no result for {name} (looked in basic/out/chips)", 404)
    d = json.loads(p.read_text())
    v = (d.get("decompiled") or {}).get("verilog")
    if not v:
        raise Fail(f"{name} has no decompiled Verilog", 404)
    short = re.sub(r"^tt_um_", "", chip)
    return v, f"{short}_{variant}", d.get("plant") or {}


def shift(lay: pnr.Layout, dz: int) -> None:
    lay.blocks = {(x, y, z + dz): s for (x, y, z), s in lay.blocks.items()}
    lay.last = [(x, y, z + dz, s) for x, y, z, s in lay.last]
    lay.labels = [(x, y, z + dz, t) for x, y, z, t in lay.labels]
    lay.inputs = {b: (x, y, z + dz) for b, (x, y, z) in lay.inputs.items()}
    lay.outputs = {b: (o if isinstance(o, int) or o is None else (o[0], o[1], o[2] + dz, o[3]))
                   for b, o in lay.outputs.items()}
    x0, y0, z0, x1, y1, z1 = lay.bbox
    lay.bbox = (x0, y0, z0 + dz, x1, y1, z1 + dz)


def compile_design(body: dict) -> dict:
    if body.get("preset"):
        if body["preset"] not in PRESETS:
            raise Fail(f"no preset '{body['preset']}'", 404)
        path, title, origin = PRESETS[body["preset"]]
        body = {"verilog": (HERE / path).read_text(), "id": body["preset"], "title": title, "origin": origin}
    if body.get("chip"):
        variant = body.get("variant", "clean")
        if variant not in ("clean", "planted"):
            raise Fail("variant must be clean or planted")
        src_text, name, plant = basic_verilog(body["chip"], variant)
        origin = {"chip": body["chip"], "variant": variant, "n": plant.get("n"), "operand": plant.get("operand")}
        title = body.get("title") or f"{body['chip']} ({variant}, decompiled by bASIC)"
    else:
        src_text = body.get("verilog") or ""
        if not src_text.strip():
            raise Fail("send verilog, or chip + variant")
        if len(src_text) > 200_000:
            raise Fail("that Verilog is too long for a Minecraft build")
        name = body.get("name") or (re.search(r"\bmodule\s+(\w+)", src_text) or [None, "design"])[1]
        origin = body.get("origin") or {}
        title = body.get("title") or f"{name} (your Verilog)"
    bid = slug(body.get("id") or name)
    if bid in PREBUILT:
        bid += "_web"
    out = WEB / bid
    out.mkdir(parents=True, exist_ok=True)
    src = out / "source.v"
    src.write_text(src_text)
    digest = hashlib.sha256(src_text.encode()).hexdigest()[:12]

    t0 = time.time()
    try:
        pn = verilog.build(src)
    except SystemExit as e:                       # verilog.py reports unsupported designs with SystemExit
        raise Fail(str(e).replace(str(src), "your Verilog"))
    if len(pn.prims) > MAX_PRIMS:
        raise Fail(f"{len(pn.prims)} redstone gates is too big to build live (limit {MAX_PRIMS})")
    lay = pnr.route(pn, z_min=0)
    nblocks = len(lay.blocks) + len(lay.last)
    if nblocks > MAX_BLOCKS:
        raise Fail(f"{nblocks:,} blocks is too big to build live (limit {MAX_BLOCKS:,})")
    with LOCK:
        reg = registry()
        old = reg.get(bid)
        depth = lay.bbox[5] - lay.bbox[2] + 1
        # keep the old slot if the new build fits in it; otherwise take the next free z in the lane
        if old and old.get("slot") and depth <= old["slot"][1] - old["slot"][0]:
            z = old["slot"][0]
        else:
            ends = [e["slot"][1] for k, e in reg.items() if e.get("slot") and k != bid]
            z = max(ends + [LANE_Z - GAP]) + GAP
        shift(lay, z - lay.bbox[2] + 2)
        lay.chip = bid
        pnr.save(lay, out)
        check = software_check(pn, src)
        same = bool(old and old.get("built") and old.get("digest") == digest and old.get("slot", [None])[0] == z)
        e = {"id": bid, "title": title, "kind": "verilog", "prebuilt": False, "built": same,
             "dir": str(out), "source": str(src), "digest": digest, "origin": origin,
             "slot": [z, z + depth + 6], "check": check, "compile_s": round(time.time() - t0, 2),
             "prims": pn.stats}
        reg[bid] = e
        save_registry(reg)
        _nets[bid] = pn
        if not same:
            _sims.pop(bid, None)
    return summary(e)


# ---------------------------------------------------------------- the world

def build(bid: str) -> dict:
    with LOCK:
        reg = registry()
        e = reg.get(bid) or entry(bid)
        lay = layout(e)
        t0 = time.time()
        rcon()
        x0, y0, z0, x1, y1, z1 = lay["bbox"]
        YB = place.YB
        cmd("gamerule max_command_sequence_length 10000000")
        cmd("gamerule max_block_modifications 10000000")
        cmd("tick freeze")
        rcon().forceload(x0 - 2, z0 - 2, x1 + 2, z1 + 6)
        time.sleep(2)                                         # let the forced chunks load
        lines = []
        for x in range(x0 - 1, x1 + 2, 32):
            for zz in range(z0 - 1, z1 + 6, 32):
                xe, ze = min(x + 31, x1 + 1), min(zz + 31, z1 + 5)
                lines.append(f"fill {x} {YB} {zz} {xe} {YB + max(5, y1 + 2)} {ze} air strict")
                lines.append(f"fill {x} {FLOOR_Y} {zz} {xe} {FLOOR_Y} {ze} stone")
        lines += [f"setblock {x} {YB + y} {z} {s}" for x, y, z, s in lay["blocks"]]
        lines += [f"setblock {x} {YB + y} {z} {s}" for x, y, z, s in lay["last"]]
        for x, y, z, text in lay.get("labels", []):
            state = sign(text.split("\n"))
            lines.append(f"setblock {x} {YB + y} {z} {state}")
        for b, (x, y, z) in lay["inputs"].items():
            on = b == tm.HOLD_S                                 # idle clock: master open, slave holding
            lines.append(f"setblock {x} {YB + y} {z} lever[face=floor,facing=north,powered={str(on).lower()}]")
        pack = DEMO.datapacks / "redstone_web"
        fn = pack / "data" / "redstone" / "function" / "web"
        fn.mkdir(parents=True, exist_ok=True)
        write_meta(pack, "redstone web builds")
        (fn / f"build_{bid}.mcfunction").write_text("\n".join(lines) + "\n")
        lay["chip"] = bid
        demo.goto_function(lay)
        seq = tm.HOLD_M in lay["inputs"]
        if seq:
            demo.clock_button(rcon(), lay)                    # writes the clock functions, reloads, places a button
        else:
            cmd("reload")
        r = cmd(f"function redstone:web/build_{bid}")
        if "Unknown" in r or "rror" in r:
            raise Fail(f"build function failed: {r}", 500)
        if seq:                                               # the clock button was placed before the clear
            demo.clock_button(rcon(), lay)
        cmd("tick unfreeze")
        cmd(f"tick sprint {2 * lay['settle_rt'] + 20}")
        cmd(f'tellraw @a {{"text":"{e["title"]}: built from the website ({len(lines):,} commands)","color":"gold"}}')
        e["built"] = True
        e["built_at"] = time.time()
        reg[bid] = e
        save_registry(reg)
        _sims.pop(bid, None)
        return {**summary(e), "build_s": round(time.time() - t0, 1), "commands": len(lines)}


def state(bid: str) -> dict:
    with LOCK:
        e = entry(bid)
        lay = layout(e)
        YB = place.YB
        ins = [b for b in lay["inputs"] if b not in (tm.HOLD_M, tm.HOLD_S)]
        levers = {}
        for b in ins:
            x, y, z = lay["inputs"][b]
            levers[b] = int(cmd(f"execute if block {x} {YB + y} {z} lever[powered=true]").startswith("Test passed"))
        lamps = {}
        for b, o in lay["outputs"].items():
            if isinstance(o, int) or o is None:
                lamps[b] = int(o or 0)
            else:
                x, y, z, st = o
                lamps[b] = int(cmd(f"execute if block {x} {YB + y} {z} {st}").startswith("Test passed"))
        st_ = sim_state(e)
        if levers != st_["ins"]:          # levers flipped in the game, or a fresh bridge: follow the world's levers
            st_["ins"] = dict(levers)     # (flip-flop state can't be read back; Reset re-syncs a clocked design)
            st_["sim"].set(levers)
        expected = st_["sim"].read(list(lay["outputs"]))
        return {"id": bid, "inputs": levers, "outputs": lamps, "expected": expected,
                "settled": lamps == expected, "t": time.time()}


def set_bits(bid: str, body: dict) -> dict:
    with LOCK:
        e = entry(bid)
        lay = layout(e)
        bits = {b: int(bool(v)) for b, v in (body.get("bits") or {}).items()
                if b in lay["inputs"] and b not in (tm.HOLD_M, tm.HOLD_S)}
        for b, on in bits.items():
            x, y, z = lay["inputs"][b]
            cmd(f"setblock {x} {place.YB + y} {z} lever[face=floor,facing=north,powered={'true' if on else 'false'}]")
        st_ = sim_state(e)
        st_["ins"].update(bits)
        st_["sim"].set(st_["ins"])
        if body.get("sprint", True):
            cmd(f"tick sprint {2 * lay['settle_rt'] + 20}")
        return state(bid)


def clock(bid: str, body: dict) -> dict:
    with LOCK:
        e = entry(bid)
        lay = layout(e)
        if tm.HOLD_M not in lay["inputs"]:
            raise Fail("this design has no clock")
        slug_ = bid.lower()
        r = cmd(f"function redstone:{slug_}/{'clock_fast' if body.get('sprint', True) else 'clock'}")
        if "Unknown" in r:
            raise Fail(f"no clock function for {bid}; build it first ({r})")
        st_ = sim_state(e)
        st_["sim"].set(st_["ins"])
        st_["sim"].clock()
        return {"ok": True, "wait_s": (5 * (2 * lay["settle_rt"] + 20) + 40) / 20 if not body.get("sprint", True)
                else 2.0}


def camera(bid: str, body: dict) -> dict:
    with LOCK:
        lay = layout(entry(bid))
        x0, y0, z0, x1, y1, z1 = lay["bbox"]
        Y = place.YB
        cx, cz = (x0 + x1) // 2, (z0 + z1) // 2
        view = body.get("view", "levers")
        if view == "overview":
            h = max(40, (x1 - x0) // 3)
            c = f"tp @a {cx} {Y + h} {z0 - h // 2} facing {cx} {Y} {cz}"
        elif view == "side":
            c = f"tp @a {x0 - 25} {Y + 18} {cz} facing {cx} {Y} {cz}"
        else:
            xs = [p[0] for p in lay["inputs"].values()]
            x, z = (min(xs) + max(xs)) // 2, max(p[2] for p in lay["inputs"].values()) + 5
            c = f"tp @a {x} {Y + 3} {z} facing {x} {Y - 6} {z + 25}"
        return {"ok": True, "result": cmd(c)}


def health() -> dict:
    ip = ""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
    except OSError:
        pass
    try:
        with LOCK:
            players = cmd("list")
        ok = True
    except Fail as e:
        ok, players = False, str(e)
    return {"ok": ok, "players": players, "lan_ip": ip, "game_port": DEMO.game_port, "version": "Java 1.21.11"}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "redstone-bridge/1"

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[bridge] {self.command} {self.path} {args[1] if len(args) > 1 else ''}\n")

    def _json(self, code: int, obj) -> None:
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _route(self, method: str):
        path = self.path.split("?")[0].rstrip("/")
        m = re.fullmatch(r"/redstone/([a-z]+)(?:/([A-Za-z0-9_]+))?", path)
        if not m:
            raise Fail("not found", 404)
        what, bid = m.group(1), m.group(2)
        body = {}
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        if method == "GET":
            if what == "health":
                return health()
            if what == "presets":
                return [{"id": k, "title": t} for k, (_, t, _) in PRESETS.items()]
            if what == "builds":
                with LOCK:
                    return [summary(e) for e in registry().values()]
            if what == "layout" and bid:
                with LOCK:
                    e = entry(bid)
                    return {**summary(e), "layout": layout(e)}
            if what == "state" and bid:
                return state(bid)
        else:
            if what == "compile":
                return compile_design(body)
            if what == "build" and bid:
                return build(bid)
            if what == "set" and bid:
                return set_bits(bid, body)
            if what == "clock" and bid:
                return clock(bid, body)
            if what == "camera" and bid:
                return camera(bid, body)
            if what == "speed":
                rate = max(1, min(1000, int(body.get("rate", 20))))
                with LOCK:
                    return {"ok": True, "result": cmd(f"tick rate {rate}")}
        raise Fail("not found", 404)

    def _go(self, method):
        try:
            self._json(200, self._route(method))
        except Fail as e:
            self._json(e.code, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def do_GET(self):
        self._go("GET")

    def do_POST(self):
        self._go("POST")


def main() -> None:
    host = os.environ.get("REDSTONE_BRIDGE_HOST", "127.0.0.1")
    srv = ThreadingHTTPServer((host, PORT), Handler)
    print(f"redstone bridge on http://{host}:{PORT}/redstone/  (RCON {DEMO.port}, world {DEMO.dir})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
