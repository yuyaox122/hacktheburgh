"""The Decap MCP server: every route of the Decap core API becomes an MCP tool (via FastMCP).

Which core? `DECAP_CORE_APP=module:attr` points at Yuyao's FastAPI app once it exists
(e.g. `decap.api.main:app`); without it we serve the stub in stub_core.py.
"""

from __future__ import annotations

import importlib
import os

from fastapi import FastAPI
from fastmcp import FastMCP

from .stub_core import create_stub_app
from .transcript import StrictArgsMiddleware, Transcript, TranscriptMiddleware

INSTRUCTIONS = (
    "Decap reads chips: it turns a gate-level netlist (names stripped) back into readable Verilog and "
    "formally proves the result with Yosys. Use these tools for every fact. Quote chip ids, cone ids, "
    "numbers and verdicts exactly as returned. 'proven' means a Yosys proof passed; 'tested' means random "
    "simulation only, NOT proven; never upgrade a verdict. If a result has source 'stub', say so. "
    "To look for hidden logic: find_rare_triggers -> decompile -> prove_equiv(against='author_source')."
)


def load_core_app() -> tuple[FastAPI, str]:
    spec = os.environ.get("DECAP_CORE_APP", "").strip()
    if not spec:
        return create_stub_app(), "stub"
    module, _, attr = spec.partition(":")
    return getattr(importlib.import_module(module), attr or "app"), spec


def build_server(app: FastAPI | None = None, transcript: Transcript | None = None) -> FastMCP:
    if app is None:
        app, _ = load_core_app()
    mcp = FastMCP.from_fastapi(app=app, name="decap", instructions=INSTRUCTIONS)
    if transcript is not None:
        mcp.add_middleware(TranscriptMiddleware(transcript))  # outermost: also records rejected calls
    mcp.add_middleware(StrictArgsMiddleware())
    return mcp
