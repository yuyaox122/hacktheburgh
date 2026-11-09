"""The AI guide (SPEC §6.5): explains the chip in plain words, highlights what it mentions, never invents results.

The guide sees only what Decap computed: the decompiled Verilog, recognised blocks, flags and verdicts. It answers
in JSON so the page can highlight lines and cells. No key (or an API error) -> the route returns 503.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import anthropic

from . import llm

MODEL = llm.api_model(os.environ.get("DECAP_GUIDE_MODEL"))   # bare or 'anthropic:' (the agent layer's spelling)

RULES = """You are the guide inside Decap, a tool that turns a chip's gate-level netlist (all internal names stripped)
back into readable Verilog and proves it equal with Yosys.

Rules:
- At most 110 words, plain language for someone new to chips. Explain chip terms in a few words when you use them.
- Use only the facts in the context below. Never invent cell ids, line numbers, values or verification results.
  If the context does not say, say you don't know.
- "proven" means a Yosys equivalence proof passed. "tested" means simulation only, not proven. Never upgrade a verdict.
- Flags are blind guesses at suspicious logic; legitimate comparators get flagged too. Say so when relevant.
- Reply with JSON only: {"reply": "<text>", "lines": [<decompiled line numbers you mention>], "cells": [<cell ids you mention>]}"""


def _has_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def available() -> bool:
    # No API key: fall back to the local `claude` CLI (Claude Code login), unless DECAP_GUIDE_CLI=0.
    return _has_key() or (os.environ.get("DECAP_GUIDE_CLI", "1") != "0" and shutil.which("claude") is not None)


def _ask_cli(system: str, msgs: list[dict]) -> tuple[str, str]:
    """One-shot `claude -p` with no tools; the conversation is passed as a transcript."""
    convo = "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in msgs)
    alias = os.environ.get("DECAP_GUIDE_CLI_MODEL", "sonnet")
    out = subprocess.run(llm.cli_command(alias, system), input=f"{convo}\n\nReply to the last USER message, as JSON only.",
                         capture_output=True, text=True, timeout=120, env=llm.cli_env(), cwd=llm.CLI_CWD)
    data = llm.cli_result(out.returncode, out.stdout, out.stderr)
    return str(data.get("result", "")), "claude-cli:" + alias


def context_text(result: dict, selection: dict | None) -> str:
    v = result["decompiled"]["verilog"].splitlines()
    numbered = "\n".join(f"{k + 1:3d}| {l}" for k, l in enumerate(v))
    blocks = [{k: b[k] for k in ("id", "kind", "lines", "width", "recognised")} | {"cells": len(b["cells"])} for b in result["blocks"]]
    flags = [{k: f.get(k) for k in ("id", "line", "condition", "rate", "signal", "reaches")} for f in result.get("flags", [])]
    ctx = {
        "chip": result.get("variant_id") or result["chip"], "title": result.get("title"), "variant": result.get("variant"),
        "verdict": result["verdict"], "proof": result["proof"].get("detail"), "coverage": result["coverage"],
        "stats": result["stats"], "author_check": result.get("author_source", {}).get("check"),
        "blocks": blocks, "flags": flags, "plant": result.get("plant"), "selection": selection or {},
    }
    return f"Context (facts from Decap):\n{json.dumps(ctx)}\n\nDecompiled Verilog (line numbers on the left):\n{numbered}"


def page_context_text(page: dict) -> str:
    """No chip open (Stage, Results, Compare, Minecraft): the page's own facts, as the page sent them."""
    return ("Context (facts from the page the user is on; no single chip is open, so there are no decompiled lines "
            "or cells to highlight):\n" + json.dumps(page, default=str)[:12000])


def ask(result: dict | None, messages: list[dict], selection: dict | None = None, page: dict | None = None) -> dict:
    """Answer about one chip (result), or, with result=None, about the page the user is on (page = its context)."""
    msgs = [{"role": m["role"], "content": str(m["content"])} for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]
    if not msgs or msgs[-1]["role"] != "user":
        raise ValueError("the last message must be from the user")
    ctx = context_text(result, selection) if result is not None else page_context_text(page or {})
    if _has_key():
        text, model = _ask_api(ctx, msgs)
    else:
        text, model = _ask_cli(RULES + "\n\n" + ctx, msgs)
    return _parse(result, text, model)


def _ask_api(ctx: str, msgs: list[dict]) -> tuple[str, str]:
    client = anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=[{"type": "text", "text": RULES}, {"type": "text", "text": ctx}],
        messages=msgs,
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        return '{"reply": "The guide declined to answer that one."}', resp.model
    return "".join(b.text for b in resp.content if b.type == "text").strip(), resp.model


def _parse(result: dict | None, text: str, model: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0)) if m else {"reply": text}
    except json.JSONDecodeError:
        data = {"reply": text}
    known_cells = {c["id"] for c in result["netlist"]["cells"]} if result else set()
    n_lines = result["decompiled"]["verilog"].count("\n") + 1 if result else 0
    cells = [c for c in data.get("cells", []) if c in known_cells]
    lines = [int(l) for l in data.get("lines", []) if str(l).isdigit() and 1 <= int(l) <= n_lines]
    return {"reply": str(data.get("reply", text))[:1500], "highlights": {"cells": cells, "lines": lines}, "model": model}
