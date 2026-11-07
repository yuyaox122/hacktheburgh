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
import tempfile

import anthropic

MODEL = os.environ.get("DECAP_GUIDE_MODEL", "claude-sonnet-5-5")   # SPEC §6.5

RULES = """You are the guide inside bASIC, a tool that turns a chip's gate-level netlist (all internal names stripped)
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
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_CODE") and k != "CLAUDECODE"}
    out = subprocess.run(
        ["claude", "-p", "--output-format", "json", "--model", os.environ.get("DECAP_GUIDE_CLI_MODEL", "sonnet"),
         "--tools", "", "--no-session-persistence", "--strict-mcp-config", "--setting-sources", "",
         "--system-prompt", system],
        input=f"{convo}\n\nReply to the last USER message, as JSON only.", capture_output=True, text=True,
        timeout=120, env=env, cwd=tempfile.gettempdir(),
    )
    if out.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {(out.stderr or out.stdout)[-400:]}")
    data = json.loads(out.stdout)
    if data.get("is_error"):
        raise RuntimeError(f"claude CLI error: {str(data.get('result'))[:400]}")
    return str(data.get("result", "")), "claude-cli:" + os.environ.get("DECAP_GUIDE_CLI_MODEL", "sonnet")


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


def ask(result: dict, messages: list[dict], selection: dict | None = None) -> dict:
    msgs = [{"role": m["role"], "content": str(m["content"])} for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]
    if not msgs or msgs[-1]["role"] != "user":
        raise ValueError("the last message must be from the user")
    if _has_key():
        text, model = _ask_api(result, msgs, selection)
    else:
        text, model = _ask_cli(RULES + "\n\n" + context_text(result, selection), msgs)
    return _parse(result, text, model)


def ask_page(page: dict, messages: list[dict]) -> dict:
    """A question asked where no single chip is open (Results, Compare): answer from what is on screen."""
    msgs = [{"role": m["role"], "content": str(m["content"])} for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]
    if not msgs or msgs[-1]["role"] != "user":
        raise ValueError("the last message must be from the user")
    ctx = "What is on screen (no single chip is open, so there are no lines or cells to cite):\n" + json.dumps(page, default=str)[:30000]
    text, model = _call([RULES, ctx], msgs) if _has_key() else _ask_cli(RULES + "\n\n" + ctx, msgs)
    return _parse({"netlist": {"cells": []}, "decompiled": {"verilog": ""}}, text, model)


def _ask_api(result: dict, msgs: list[dict], selection: dict | None) -> tuple[str, str]:
    return _call([RULES, context_text(result, selection)], msgs)


def _call(system: list[str], msgs: list[dict]) -> tuple[str, str]:
    client = anthropic.Anthropic()
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=[{"type": "text", "text": t} for t in system],
        messages=msgs,
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        return '{"reply": "The guide declined to answer that one."}', resp.model
    return "".join(b.text for b in resp.content if b.type == "text").strip(), resp.model


def _parse(result: dict, text: str, model: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0)) if m else {"reply": text}
    except json.JSONDecodeError:
        data = {"reply": text}
    known_cells = {c["id"] for c in result["netlist"]["cells"]}
    n_lines = result["decompiled"]["verilog"].count("\n") + 1
    cells = [c for c in data.get("cells", []) if c in known_cells]
    lines = [int(l) for l in data.get("lines", []) if str(l).isdigit() and 1 <= int(l) <= n_lines]
    return {"reply": str(data.get("reply", text))[:1500], "highlights": {"cells": cells, "lines": lines}, "model": model}
