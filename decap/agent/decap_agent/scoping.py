"""Aim the region agent at the thing the person pointed at, and report its best result, on any chip.

Two general problems showed up on bigger chips (e.g. the I2S-to-PWM receiver):

1. A block id is too wide a scope. `cone_of: "b3"` (a 16-bit shift register) resolves to the block's cells, and the
   core's region is every register whose next-state logic touches any of them: data0, data1, shreg0 AND the 7-bit
   state machine state0. The coverage rule then demands one claim for the whole receiver, which the agent can't
   write, so a simple shift register came back "refuted" after five tries.
   Fix: when a block's region has more than one register output, narrow the scope to the block's OWN register(s),
   the ones its decompiled line assigns (`shreg0 <= ...`), by scoping to their flip-flop cells. The core then gives
   exactly `next(shreg0)`. No core change: `{"cells": [...]}` is an ordinary scope.

2. The run reported its LAST attempt. If try 2 was a true partial fact and try 5 was refuted, people saw
   "refuted". Fix: `best_attempt` ranks attempts (proven > partial > bounded > ...). The bench keeps using `final`
   unchanged; the website shows the best.
"""

from __future__ import annotations

import re
from typing import Any

RANK = {"proven": 0, "inductive": 0, "partial": 1, "bounded": 2, "tested": 3, "restated": 4, "refuted": 5,
        "tautology": 6, "unparseable": 7, "timeout": 8, "no_claim": 9}
ASSIGNS = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*<=", re.M)


def best_attempt(attempts: list[dict]) -> dict:
    """The most informative attempt: lowest rank wins, later wins ties (it saw more feedback)."""
    if not attempts:
        return {}
    return min(reversed(attempts), key=lambda a: RANK.get(str(a.get("verdict")), 10))


async def narrow_scope(call, chip: str, scope: dict) -> tuple[dict, str | None]:
    """(scope to use, a note for the transcript or None). `call(tool, **args)` returns a payload or raises.

    Only block-id scopes are narrowed, and only when the block's region has more than one register output and the
    block's own register(s) can be found among them. Anything else is returned unchanged."""
    target = scope.get("cone_of") if isinstance(scope, dict) else None
    if not (isinstance(target, str) and re.fullmatch(r"b\d+", target)):
        return scope, None
    try:
        region = await call("get_region", chip=chip, scope=scope)
        outs = [o for o in region.get("outputs", []) if o.get("kind") == "register_next"]
        if len(region.get("outputs", [])) <= 1 or not outs:
            return scope, None
        blocks = await call("recover_blocks", chip=chip)
        block = next((b for b in blocks.get("blocks", []) if b.get("id") == target), None)
        own = set(ASSIGNS.findall(block.get("text") or "")) if block else set()
        mine = [o for o in outs if o["name"][5:-1] in own]          # "next(shreg0)" -> "shreg0"
        if not mine:
            return scope, None
        cells = [f[2:] for o in mine for f in o.get("flops", []) if f.startswith("q_")]
        if not cells:
            return scope, None
        names = ", ".join(o["name"][5:-1] for o in mine)
        return {"cells": cells}, (f"{target} touches {len(region['outputs'])} registers; explaining its own "
                                  f"register {names} ({len(cells)} flip-flops)")
    except Exception:   # narrowing is an optimisation; never block the explanation on it
        return scope, None


def mcp_caller(client: Any):
    """Adapt a fastmcp Client to the `call(tool, **args)` shape, raising on tool errors."""
    from .transcript import result_payload

    async def call(tool: str, **args):
        r = await client.call_tool(tool, args, raise_on_error=False)
        if getattr(r, "is_error", False):
            raise RuntimeError(f"{tool} failed")
        return result_payload(r)
    return call
