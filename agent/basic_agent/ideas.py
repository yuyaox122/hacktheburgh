"""Ideas: the web app's "Ideas" button on a line of decompiled Verilog (issue #68, the JetBrains "Help the Developer" angle).

    person hovers a line -> Ideas (optionally types what they want)
      -> this agent proposes 2-3 concrete edits of that part: {title, why, intent, lines [a, b], replacement}
      -> the web applies one and calls the core's review_edit (Yosys vs the chip's real gates)
      -> same / tested_same / changed (+ counterexample) / broken, written by code

Same principle as Witness: the LLM only proposes. It never says whether an edit is safe; Yosys does, and the verdict
text is the core's, not the model's. This module also validates what the model returns (lines inside the module body,
the header and port list untouched, no no-ops) and drops the rest, so the web only ever sees edits it can apply.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

PROMPT_VERSION = "ideas-v2"
MAX_IDEAS = 3
FULL_MODULE_LINES = 400       # bigger files: only a window around the region goes into the prompt
WINDOW = 60
HISTORY_TURNS = 10

INSTRUCTIONS = """You help a developer change one part of a chip's Verilog. The Verilog was decompiled from the chip's
real gates and proven equal to them. The developer points at some lines (the region) and may say what they want.

Propose 2 or 3 concrete, different ideas for changing that part. Each idea is one edit: replace lines a..b (1-based,
inclusive, numbers as shown; always two numbers, [a, a] for one line) with `replacement`, a list of complete Verilog lines (indent them like the code around
them; an empty list deletes the lines). For each idea:
- `intent` is "same" for a refactor that should keep the behaviour exactly (clearer names via a new wire, a simpler
  expression, a comment, a named constant), or "change" for a deliberate change of behaviour (a feature, a fix, a
  different reset value).
- `title` is a few words; `why` is one or two plain-English sentences for someone new to chips.
- Keep the edit small and inside the module body, near the region. Never touch the `module ... ( ... );` header or the
  port list, and never add `module`/`endmodule`. A new signal must be declared in the replacement (wire/reg).
- Synthesisable Verilog-2005 only. Every name you use must exist in the module or be declared in your replacement.

You do not decide whether an edit is safe. After the developer applies an idea, Yosys checks it against the chip's
gates and says whether it is the same circuit; so never say an idea is "proven", "equivalent" or "guaranteed".
`reply` is one or two short plain-English sentences to the developer (answer their question if they asked one)."""


class Idea(BaseModel):
    title: str = Field(description="A few words, e.g. 'Name the carry signal'.")
    why: str = Field(description="One or two plain-English sentences: what it does and why it helps.")
    intent: Literal["same", "change"] = Field(description="'same' = refactor that should keep behaviour; 'change' = "
                                                          "a deliberate behaviour change.")
    lines: list[int] = Field(description="[a, b]: the 1-based line range (inclusive) this edit replaces; [a, a] for one line.")
    replacement: list[str] = Field(description="The complete new lines that replace lines a..b (empty = delete them).")


class IdeasOut(BaseModel):
    reply: str = Field(description="One or two short plain-English sentences to the developer.")
    ideas: list[Idea] = Field(description="2 or 3 ideas, each one edit.")


class IdeasInputError(ValueError):
    """The request itself is wrong (empty text, lines outside it, no module header): the route answers 400."""


def build_ideas_agent(model: Any = None) -> Agent:
    from .cli_model import resolve
    from .witness import DEFAULT_MODEL
    return Agent(resolve(model, os.environ.get("BASIC_WITNESS_MODEL", DEFAULT_MODEL)), instructions=INSTRUCTIONS,
                 output_type=IdeasOut, defer_model_check=True)


def body_range(lines: list[str]) -> tuple[int, int]:
    """(first, last) 1-based lines an edit may touch: after the `module ... );` header, before `endmodule`."""
    text = "\n".join(lines)
    m = re.search(r"\bmodule\b[^;]*;", re.sub(r"//[^\n]*", lambda x: " " * len(x.group(0)), text), re.S)
    if not m:
        raise IdeasInputError("no `module NAME (...);` header found in the Verilog")
    first = text.count("\n", 0, m.end()) + 2
    ends = [i for i, l in enumerate(lines, 1) if re.match(r"\s*endmodule\b", l)]
    last = (ends[0] if ends else len(lines) + 1) - 1
    return first, last


def numbered(lines: list[str], a: int, b: int) -> str:
    return "\n".join(f"{n:4d}| {lines[n - 1]}" for n in range(a, b + 1))


def build_prompt(chip: str, line: int, region: tuple[int, int], lines: list[str], ask: str, history: list[dict]) -> str:
    a, b = region
    first, last = body_range(lines)
    if len(lines) <= FULL_MODULE_LINES:
        module = numbered(lines, 1, len(lines))
    else:
        lo, hi = max(first, a - WINDOW), min(len(lines), b + WINDOW)
        module = (numbered(lines, 1, first - 1) + f"\n    ... (lines {first}-{lo - 1} not shown)\n" + numbered(lines, lo, hi)
                  + (f"\n    ... (lines {hi + 1}-{len(lines)} not shown)" if hi < len(lines) else ""))
    talk = "\n".join(f"{'Developer' if h.get('role') == 'user' else 'You'}: {h.get('text', '')}"
                     for h in history[-HISTORY_TURNS:])
    return (f"Chip {chip!r}. Editable lines: {first}-{last} (the header and `endmodule` are fixed).\n"
            f"The developer points at line {line}; the region is lines {a}-{b}:\n{numbered(lines, a, b)}\n\n"
            f"The whole module:\n{module}\n\n"
            + (f"Conversation so far:\n{talk}\n\n" if talk else "")
            + (f"The developer asks: {ask.strip()}" if ask.strip() else "The developer asked nothing specific: suggest "
               "useful ideas for this part (at least one 'same' refactor and, if sensible, one 'change')."))


def validate(ideas: list[Idea], lines: list[str]) -> tuple[list[dict], list[str]]:
    """Keep ideas the web can apply; return (kept, reasons for the dropped ones)."""
    first, last = body_range(lines)
    kept, dropped, seen = [], [], set()
    for i in ideas:
        if len(i.lines) == 1:   # models often write [15] for a one-line edit
            i.lines = [i.lines[0], i.lines[0]]
        if len(i.lines) != 2:
            dropped.append(f"{i.title!r}: lines must be [a, b]")
            continue
        a, b = i.lines
        rep = [r.rstrip("\n") for r in i.replacement]
        if not (1 <= a <= b <= len(lines)):
            dropped.append(f"{i.title!r}: lines {a}-{b} are outside the file (1-{len(lines)})")
        elif a < first or b > last:
            dropped.append(f"{i.title!r}: lines {a}-{b} touch the module header or endmodule (editable: {first}-{last})")
        elif any(re.search(r"\b(module|endmodule|macromodule)\b", re.sub(r"//.*|/\*.*?\*/", "", r)) for r in rep):
            dropped.append(f"{i.title!r}: the replacement adds module/endmodule")
        elif any("\n" in r for r in rep):
            dropped.append(f"{i.title!r}: a replacement line contains a line break")
        elif rep == lines[a - 1:b]:
            dropped.append(f"{i.title!r}: the replacement changes nothing")
        else:
            key = (a, b, tuple(rep))
            if key in seen:
                dropped.append(f"{i.title!r}: duplicate")
                continue
            seen.add(key)
            kept.append({"id": "i" + hashlib.sha1(repr(key).encode()).hexdigest()[:8], "title": i.title.strip(),
                         "why": i.why.strip(), "intent": i.intent, "lines": [a, b], "replacement": rep})
        if len(kept) == MAX_IDEAS:
            break
    return kept, dropped


async def propose_ideas(chip: str, line: int, region: list[int], verilog: str, ask: str = "",
                        history: list[dict] | None = None, model: Any = None) -> dict:
    """One LLM call -> {reply, ideas (validated, at most 3), model, dropped, wall_s}. Raises IdeasInputError on bad
    input (before any model call); anything else comes from the model."""
    lines = verilog.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if not lines or not verilog.strip():
        raise IdeasInputError("verilog is empty: send the full text shown in the code pane")
    if len(region) != 2 or not (1 <= region[0] <= region[1] <= len(lines)):
        raise IdeasInputError(f"lines {region} are not a range inside the Verilog (1-{len(lines)})")
    if not (1 <= line <= len(lines)):
        raise IdeasInputError(f"line {line} is outside the Verilog (1-{len(lines)})")
    prompt = build_prompt(chip, line, (region[0], region[1]), lines, ask, history or [])   # checks the header first
    agent = build_ideas_agent(model)
    t0 = time.perf_counter()
    res = await agent.run(prompt)
    out: IdeasOut = res.output
    kept, dropped = validate(out.ideas, lines)
    reply = out.reply.strip()
    if not kept:
        reply = (reply + " " if reply else "") + "(None of the ideas could be applied here, so none are shown. Try asking again.)"
    elif dropped:   # the reply may count ideas that were dropped: say what is shown
        reply += f" ({len(dropped)} more {'idea' if len(dropped) == 1 else 'ideas'} couldn't be applied here, so {len(kept)} " \
                 f"{'is' if len(kept) == 1 else 'are'} shown.)"
    return {"reply": reply, "ideas": kept, "model": getattr(agent.model, "model_name", None) or str(agent.model),
            "dropped": dropped, "prompt": PROMPT_VERSION, "wall_s": round(time.perf_counter() - t0, 3)}
