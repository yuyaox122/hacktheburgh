"""Decap's AI guide (spec section 6.5): a pydantic-ai agent whose only source of facts is Decap's tools.

Provider-agnostic: DECAP_GUIDE_MODEL picks the model, e.g. 'anthropic:claude-sonnet-5-5' (or the core guide's bare
'claude-sonnet-5-5'), 'anthropic:claude-haiku-5-5', or an OpenAI model with the free JetBrains key. The guide reaches
the tools through the same MCP server outside agents use, so its calls land in the transcript too.
"""

from __future__ import annotations

import os
import time
from typing import Any

from fastmcp import FastMCP
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

from .cli_model import SONNET as DEFAULT_MODEL
from .cli_model import resolve
from .transcript import Transcript

RULES = """You are Decap's guide. Decap turns chips back into readable code and proves it.
- At most 110 words, plain language.
- Every chip id, cone id, number and verdict must come from a tool result in this conversation,
  quoted exactly. If you have not called a tool for it, call one; never guess.
- 'proven' only if a tool returned verdict 'proven'. 'tested' means tested, not proven. Never upgrade.
- If a result says source 'stub', say these are stub results.
- When asked to look for hidden logic: find_rare_triggers, then prove_equiv against 'author_source'.
- Write for a sharp engineer new to this chip: the answer first, in hardware terms, then the evidence. Short
  sentences, plain words, no em dashes, no hype words (crucial, robust, seamless, essentially)."""


# Cross-chip and chip-changing tools. When the guide is answering about one chip (the website, the terminal) it
# doesn't get these: asked to "continue testing", it once ran `benchmark` and wandered onto other chips.
CROSS_CHIP = {"benchmark", "list_chips", "plant_trigger", "remove_trigger", "compare_design", "strip_names"}


def build_guide(server: FastMCP, model: Any = None, chip: str | None = None) -> Agent:
    tools = MCPToolset(server)
    if chip:
        tools = tools.filtered(lambda ctx, tool: tool.name not in CROSS_CHIP)
    return Agent(resolve(model, os.environ.get("DECAP_GUIDE_MODEL", DEFAULT_MODEL)),
                 instructions=RULES, toolsets=[tools], defer_model_check=True)


async def ask(question: str, server: FastMCP, transcript: Transcript | None = None, model: Any = None,
              chip: str | None = None) -> str:
    agent = build_guide(server, model, chip)
    t0 = time.perf_counter()
    prompt = (f"The user is looking at chip {chip!r}. Answer about this chip only, with its id in every tool call.\n\n"
              f"{question}") if chip else question
    result = await agent.run(prompt)
    usage = result.usage  # a property in pydantic-ai >= 2.66 (pinned in pyproject)
    if transcript is not None:
        transcript.record(
            "agent", question=question, answer=result.output,
            model=getattr(agent.model, "model_name", None) or str(agent.model),
            input_tokens=getattr(usage, "input_tokens", None), output_tokens=getattr(usage, "output_tokens", None),
            requests=getattr(usage, "requests", None), tool_calls=getattr(usage, "tool_calls", None),
            wall_s=round(time.perf_counter() - t0, 3))
    return result.output
