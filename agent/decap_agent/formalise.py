"""The formaliser (witness-plan section 3, the judge's moment): a person's sentence -> one checkable claim.

    "LED 3 lights when switches 1 and 2 are on"  ->  (ui_in[1] & ui_in[2]) -> uo_out[3]

The LLM only translates. The checker's code-written English is what the judge confirms
([Yes, that's what I meant]) before anything is checked, so a mistranslation is caught by the person,
not hidden. A formal line that doesn't parse goes back to the LLM once with the parser's error.

`decap formalise CHIP "sentence" [--check]`, or `formalise()` from the web's backend. `evaluate()` scores it on 30
sloppy phrasings by logical equivalence with accepted readings (no chip needed).
"""

from __future__ import annotations

import itertools
import json
import os
import random
import time
from typing import Any

from fastmcp import Client, FastMCP
from pydantic import BaseModel, Field
from pydantic_ai import Agent

from decap.core.claim_expr import ClaimSyntaxError, Evaluator, names, parse

from .transcript import Transcript, result_payload

DEFAULT_MODEL = "anthropic:claude-sonnet-5-5"
PROMPT_VERSION = "formalise-v1"

# Tiny Tapeout pinout, used when no chip region is given (and by the evaluation)
TT_NAMES = {"ui_in": 8, "uio_in": 8, "uo_out": 8, "uio_out": 8, "uio_oe": 8}

DEFAULT_LEGEND = """How people talk about a Tiny Tapeout board:
- 'switch k', 'sw k', 'input k', 'DIP k' = ui_in[k] (numbered 0-7, as on the pin labels). 'the switches' or
  'the input' as a number = ui_in (8 bits). 'switches 0-3' = ui_in[3:0].
- 'LED k', 'output k', 'segment k' = uo_out[k]. 'the LEDs', 'the output' or 'the display' as a number = uo_out.
- 'bidirectional pins' = uio_in (as inputs) / uio_out (as outputs); 'bidirectional pins are inputs' = uio_oe == 0.
- on / up / high / lit = 1; off / down / low = 0."""

INSTRUCTIONS = """You turn a person's sentence about a chip into ONE claim that a formal checker can test. You only
translate: never judge whether the claim is true.

The claim is a Verilog expression over the names you are given (with [i] or [hi:lo] selects); next(x) is x one
clock later; 'A -> B' means if A then B; 'A when B' means B -> A. rst_n = 1 and ena = 1 are assumed.

Rules:
- Translate what the person said, not what you think the chip does. Keep their numbers.
- 'X when/if Y' is an implication (Y -> X). 'X exactly when / if and only if / is the same as Y' and descriptions of
  what a pin IS ('LED 2 is the AND of ...', 'led2 = ...') are equalities (X == Y).
- Verilog widths: an unsized number such as 1 or 2 is 32 bits wide and does not wrap. When the person means
  8-bit arithmetic ('the output is the input plus one'), size it: uo_out == ui_in + 8'd1.
- 'any switch is on' = (ui_in != 0); 'an odd number of' = ^x (XOR-reduce); 'the opposite of' = !x for one bit,
  ~x for a bus.
- List every reading choice you made in `assumptions` (e.g. "'switch 1' read as ui_in[1]"). If the sentence has two
  reasonable readings that differ (e.g. 'if' vs 'exactly when'), set ambiguous=true and give the other one in
  `alternative`."""


class Formal(BaseModel):
    claim: str = Field(description="The formal line, a Verilog expression over the given names.")
    assumptions: list[str] = Field(default_factory=list, description="Each reading choice made.")
    ambiguous: bool = Field(False, description="True if another reasonable reading differs.")
    alternative: str = Field("", description="The other reading as a formal line, if ambiguous.")


def build_formaliser(model: Any = None) -> Agent:
    from .cli_model import resolve
    return Agent(resolve(model, os.environ.get("DECAP_FORMALISE_MODEL", DEFAULT_MODEL)), instructions=INSTRUCTIONS,
                 output_type=Formal, defer_model_check=True)


def _names_text(region: dict | None) -> str:
    if not region:
        return ", ".join(f"{n}[{w}]" for n, w in TT_NAMES.items())
    rows = [f"{i['name']}[{i['width']}] (input)" for i in region.get("inputs", [])]
    rows += [f"{o['name']}[{o['width']}] (output)" for o in region.get("outputs", [])]
    return ", ".join(rows)


async def formalise(text: str, *, server: FastMCP | None = None, chip: str | None = None, scope: dict | None = None,
                    legend: str = DEFAULT_LEGEND, model: Any = None, transcript: Transcript | None = None,
                    check: bool = False) -> dict:
    """English -> formal line (+ the checker's English for the confirm step). check=True also runs the checker."""
    agent = build_formaliser(model)
    t0 = time.perf_counter()
    region, client = None, None
    if server is not None and chip:
        client = Client(server)
        await client.__aenter__()
    try:
        async def call(tool, **args):
            r = await client.call_tool(tool, args, raise_on_error=False)
            return r.is_error, result_payload(r)

        if client:
            err, region = await call("get_region", chip=chip, scope=scope or {})
            if err:
                raise RuntimeError(f"get_region failed: {region}")
        prompt = f"Names you may use: {_names_text(region)}\n{legend}\n\nSentence: {text!r}"
        res = await agent.run(prompt)
        formal: Formal = res.output
        rendered, error = None, None
        for retry in range(2):
            if client:
                err, dry = await call("propose_claim", chip=chip, scope=scope or {}, claim=formal.claim, check=False)
                ok = not err and dry.get("verdict") != "unparseable"
                error = None if ok else (dry.get("error") if isinstance(dry, dict) else str(dry))
                rendered = dry.get("english") if ok else None
            else:
                try:
                    node = parse(formal.claim)
                    Evaluator(TT_NAMES).check_names(node)
                    error = None
                except ClaimSyntaxError as e:
                    error = str(e)
            if error is None or retry == 1:
                break
            res = await agent.run(f"That formal line does not parse: {error}. Fix it.", message_history=res.all_messages())
            formal = res.output
        out = {"text": text, "claim": formal.claim, "english": rendered, "assumptions": formal.assumptions,
               "ambiguous": formal.ambiguous, "alternative": formal.alternative, "error": error,
               "model": getattr(agent.model, "model_name", None) or str(agent.model), "prompt": PROMPT_VERSION,
               "wall_s": round(time.perf_counter() - t0, 3)}
        if check and client and error is None:
            _, verdict = await call("propose_claim", chip=chip, scope=scope or {}, claim=formal.claim)
            out["verdict"] = verdict
        if transcript is not None:
            transcript.record("formalise", chip=chip, scope=scope, **{k: v for k, v in out.items() if k != "verdict"},
                              checked=(out.get("verdict") or {}).get("verdict"))
        return out
    finally:
        if client:
            await client.__aexit__(None, None, None)


# ------------------------------------------------------------------------------------------------ evaluation
# (sentence, accepted readings). Several readings = the sentence is honestly ambiguous; any of them counts.
CASES: list[tuple[str, list[str]]] = [
    ("LED 3 lights when switches 1 and 2 are on", ["(ui_in[1] & ui_in[2]) -> uo_out[3]", "uo_out[3] == (ui_in[1] & ui_in[2])"]),
    ("led 0 is on exactly when switch 0 is on", ["uo_out[0] == ui_in[0]"]),
    ("the output is the input plus one", ["uo_out == ui_in + 8'd1"]),
    ("all the LEDs are off when all switches are off", ["(ui_in == 0) -> (uo_out == 0)"]),
    ("LED 7 is never on", ["uo_out[7] == 0"]),
    ("if switch 4 is off, the leds copy the switches", ["!ui_in[4] -> (uo_out == ui_in)"]),
    ("LED 2 is the AND of switch 0 and switch 1", ["uo_out[2] == (ui_in[0] & ui_in[1])"]),
    ("led2 = sw0 xor sw1", ["uo_out[2] == (ui_in[0] ^ ui_in[1])"]),
    ("the low nibble of the output is the sum of the two input nibbles", ["uo_out[3:0] == ui_in[3:0] + ui_in[7:4]"]),
    ("the output shows the bigger of the two nibbles",
     ["uo_out == ((ui_in[3:0] > ui_in[7:4]) ? ui_in[3:0] : ui_in[7:4])",
      "uo_out[3:0] == ((ui_in[3:0] > ui_in[7:4]) ? ui_in[3:0] : ui_in[7:4])"]),
    ("led 5 turns on whenever any switch is on", ["(ui_in != 0) -> uo_out[5]", "uo_out[5] == (ui_in != 0)"]),
    ("LED 1 is on iff an odd number of switches are on", ["uo_out[1] == ^ui_in"]),
    ("the outputs are the inputs inverted", ["uo_out == ~ui_in"]),
    ("when switch 7 is up, the display shows 0", ["ui_in[7] -> (uo_out == 0)"]),
    ("output equals input times two", ["uo_out == ui_in << 1"]),
    ("LED 0 and LED 1 are never both on", ["!(uo_out[0] & uo_out[1])"]),
    ("led 4 is the opposite of switch 4", ["uo_out[4] == !ui_in[4]"]),
    ("if switches 0-3 read 5 then led 0 lights", ["(ui_in[3:0] == 5) -> uo_out[0]"]),
    ("the top 4 leds mirror the bottom 4 switches", ["uo_out[7:4] == ui_in[3:0]"]),
    ("when the input is 137, LED 1 comes on", ["(ui_in == 137) -> uo_out[1]"]),
    ("LED 1 only lights for input 137", ["uo_out[1] -> (ui_in == 137)", "uo_out[1] == (ui_in == 137)"]),
    ("output = input", ["uo_out == ui_in"]),
    ("led3 is high if either sw2 or sw5 is high but not both",
     ["uo_out[3] == (ui_in[2] ^ ui_in[5])", "(ui_in[2] ^ ui_in[5]) -> uo_out[3]"]),
    ("the display never goes above 9", ["uo_out <= 9"]),
    ("uo_out[0] is ui_in[0] or ui_in[1]", ["uo_out[0] == (ui_in[0] | ui_in[1])"]),
    ("the bidirectional pins are all inputs", ["uio_oe == 0"]),
    ("led 6 is on when switch 6 is on and switch 7 is off",
     ["(ui_in[6] & !ui_in[7]) -> uo_out[6]", "uo_out[6] == (ui_in[6] & !ui_in[7])"]),
    ("the output is the input with its bits reversed",
     ["uo_out == {ui_in[0], ui_in[1], ui_in[2], ui_in[3], ui_in[4], ui_in[5], ui_in[6], ui_in[7]}"]),
    ("LED 7 shows whether the two nibbles are equal", ["uo_out[7] == (ui_in[3:0] == ui_in[7:4])"]),
    ("the leds count the switches that are on",
     ["uo_out == ui_in[0] + ui_in[1] + ui_in[2] + ui_in[3] + ui_in[4] + ui_in[5] + ui_in[6] + ui_in[7]",
      "uo_out == {7'd0, ui_in[0]} + ui_in[1] + ui_in[2] + ui_in[3] + ui_in[4] + ui_in[5] + ui_in[6] + ui_in[7]"]),
]


def equivalent(a: str, b: str, widths: dict[str, int] = TT_NAMES, samples: int = 20000) -> bool:
    """Same truth value for every assignment of the names (exhaustive up to 16 bits, else edge values + random)."""
    na, nb = parse(a), parse(b)
    ev = Evaluator(widths)
    ev.check_names(na)
    ev.check_names(nb)
    refs = sorted(names(na) | names(nb))
    bits = sum(widths[n] for _, n in refs)
    if bits <= 16:
        space = itertools.product(*[range(1 << widths[n]) for _, n in refs])
    else:
        rng = random.Random(5)
        edges = [0, 1, 0x7F, 0x80, 0xFE, 0xFF]
        space = itertools.chain(itertools.product(*[edges for _ in refs]),
                                ([rng.getrandbits(widths[n]) for _, n in refs] for _ in range(samples)))
    return all(ev.holds(na, dict(zip(refs, vs))) == ev.holds(nb, dict(zip(refs, vs))) for vs in space)


async def evaluate(model: Any = None, cases=CASES, concurrency: int = 6) -> dict:
    import asyncio
    sem = asyncio.Semaphore(concurrency)

    async def one(text, accepted):
        async with sem:
            out = await formalise(text, model=model)
        try:
            ok = out["error"] is None and any(equivalent(out["claim"], ref) for ref in accepted)
        except ClaimSyntaxError as e:
            ok, out["error"] = False, str(e)
        return {"text": text, "claim": out["claim"], "ok": ok, "ambiguous": out["ambiguous"],
                "assumptions": out["assumptions"], "error": out["error"], "wall_s": out["wall_s"]}

    rows = await asyncio.gather(*[one(t, a) for t, a in cases])
    walls = sorted(r["wall_s"] for r in rows)
    return {"model": model or os.environ.get("DECAP_FORMALISE_MODEL", DEFAULT_MODEL), "prompt": PROMPT_VERSION,
            "correct": sum(r["ok"] for r in rows), "of": len(rows), "median_s": walls[len(walls) // 2],
            "wrong": [r for r in rows if not r["ok"]], "rows": rows}


if __name__ == "__main__":  # python -m decap_agent.formalise [model]
    import asyncio
    import sys
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    res = asyncio.run(evaluate(sys.argv[1] if len(sys.argv) > 1 else None))
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1))
