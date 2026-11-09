"""Check someone's own Verilog against a chip: "does this chip really implement my file?"

Upload a .v file on the Decompile or Explore page and Yosys checks it against the chip's shipped gate netlist,
the same check the core runs on the author's source (core/chip.py `author_check`, reused read-only):
equivalence first (equiv_make / equiv_simple / equiv_induct), else a 20-cycle bounded check from reset that
returns the input where the two differ.

Round trip worth showing: download the decompiled Verilog, upload it back, and it comes back proven.

Needs the real core (DECAP_CORE_APP=decap.api.main:app with the repo on PYTHONPATH); the stub has no netlist.
"""

from __future__ import annotations

import re

MAX_BYTES = 400_000
TT_PORTS = ("ui_in", "uo_out")


def retop(verilog: str, top: str) -> tuple[str, str | None]:
    """The check needs the chip's top module name. If the file doesn't define it, rename the module that has the
    Tiny Tapeout ports (or the only module) to it. Returns (text, the original name if renamed)."""
    mods = re.findall(r"\bmodule\s+([A-Za-z_]\w*)", verilog)
    if top in mods or not mods:
        return verilog, None
    pick = next((m for m in mods if all(p in _module_header(verilog, m) for p in TT_PORTS)), None)
    pick = pick or (mods[0] if len(mods) == 1 else None)
    if pick is None:
        return verilog, None
    return re.sub(rf"\bmodule\s+{re.escape(pick)}\b", f"module {top}", verilog, count=1), pick


def _module_header(verilog: str, name: str) -> str:
    m = re.search(rf"\bmodule\s+{re.escape(name)}\b(.*?);", verilog, re.S)
    body = re.search(rf"\bmodule\s+{re.escape(name)}\b(.*?)\bendmodule\b", verilog, re.S)
    return (m.group(1) if m else "") + (body.group(1)[:4000] if body else "")


def check_verilog(chip: str, verilog: str, filename: str = "upload.v") -> dict:
    """{verdict: proven | tested | failed | unavailable, detail, counterexample, renamed_from, top}."""
    if len(verilog.encode()) > MAX_BYTES:
        return {"verdict": "unavailable", "detail": f"File too large (over {MAX_BYTES // 1000} kB).", "counterexample": None}
    if "module" not in verilog:
        return {"verdict": "unavailable", "detail": "That file has no Verilog module in it.", "counterexample": None}
    try:
        from decap.core import chip as C
        from decap.core import service
    except ImportError:
        return {"verdict": "unavailable", "counterexample": None,
                "detail": "Checking your own Verilog needs the real core (start the agent with DECAP_CORE_APP set)."}
    try:
        v = service.get(chip)
    except KeyError as e:
        return {"verdict": "unavailable", "detail": str(e), "counterexample": None}
    top = v.nl.top
    text, renamed = retop(verilog, top)
    name = re.sub(r"[^\w.-]", "_", filename or "upload.v")
    if not name.endswith((".v", ".sv")):
        name += ".v"
    check, detail, cex = C.author_check(v.base, {"files": {name: text}}, top, nl_text=v.text, want_cex=True)
    verdict = {"proven": "proven", "bounded": "tested", "failed": "failed"}.get(check, "unavailable")
    return {"verdict": verdict, "check": check, "detail": detail, "counterexample": cex, "renamed_from": renamed, "top": top}
