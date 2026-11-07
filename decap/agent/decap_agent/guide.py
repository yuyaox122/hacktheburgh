"""Decap's AI guide (spec section 6.5): a pydantic-ai agent whose only source of facts is Decap's tools.

Provider-agnostic: DECAP_GUIDE_MODEL picks the model, e.g. 'anthropic:claude-sonnet-5-5',
'anthropic:claude-haiku-5-5', or an OpenAI model with the free JetBrains key. The guide reaches
the tools through the same MCP server outside agents use, so its calls land in the transcript too.
"""

from __future__ import annotations

import os
import time
from typing import Any

from fastmcp import FastMCP
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

from .transcript import Transcript

DEFAULT_MODEL = "anthropic:claude-sonnet-5-5"

RULES = """You are Decap's guide. Decap turns chips back into readable code and proves it.
- At most 110 words, plain language.
- Every chip id, cone id, number and verdict must come from a tool result in this conversation,
  quoted exactly. If you have not called a tool for it, call one; never guess.
- 'proven' only if a tool returned verdict 'proven'. 'tested' means tested, not proven. Never upgrade.
- If a result says source 'stub', say these are stub results.
- When asked to look for hidden logic: find_rare_triggers, then prove_equiv against 'author_source'."""


def build_guide(server: FastMCP, model: Any = None) -> Agent:
    return Agent(model or os.environ.get("DECAP_GUIDE_MODEL", DEFAULT_MODEL),
                 instructions=RULES, toolsets=[MCPToolset(server)], defer_model_check=True)


async def ask(question: str, server: FastMCP, transcript: Transcript | None = None, model: Any = None) -> str:
    agent = build_guide(server, model)
    t0 = time.perf_counter()
    result = await agent.run(question)
    usage = result.usage  # a property in pydantic-ai >= 2.66 (pinned in pyproject)
    if transcript is not None:
        transcript.record(
            "agent", question=question, answer=result.output,
            model=getattr(agent.model, "model_name", None) or str(agent.model),
            input_tokens=getattr(usage, "input_tokens", None), output_tokens=getattr(usage, "output_tokens", None),
            requests=getattr(usage, "requests", None), tool_calls=getattr(usage, "tool_calls", None),
            wall_s=round(time.perf_counter() - t0, 3))
    return result.output
