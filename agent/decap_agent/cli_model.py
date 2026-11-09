"""An opt-in model backend that runs each LLM request through the local `claude` CLI (Claude Code login).

Use it when the API key is unavailable (e.g. its spend cap is reached): `--model cli:sonnet`, or DECAP_LLM=cli.
It never switches on by itself, because it spends the logged-in Claude account instead of the API key.

The command, its isolation and the reply parsing are the core guide's fallback's own (decap/core/llm.py): no tools, no
MCP, no user/project settings (so no hooks fire), no saved session, run from a temp dir, CLAUDE_CODE_* variables and
ANTHROPIC_API_KEY removed so the CLI uses its own login. Structured output comes from `--json-schema` (the agent's
output schema). The model only ever answers with the final output: it can't call look tools, which is fine because
the region and a first probe are already in the region agent's prompt.

The agent layer's default models are defined here too.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil

from pydantic_ai.messages import (ModelMessage, ModelResponse, RetryPromptPart, SystemPromptPart, TextPart, ToolCallPart,
                                  ToolReturnPart, UserPromptPart)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage

from decap.core import llm

SONNET = "anthropic:" + llm.DEFAULT_MODEL   # region agent, guide, formaliser, ideas
DEEPSEEK = "deepseek:deepseek-chat"         # investigator, repair, plain-English reader


def _transcript(messages: list[ModelMessage]) -> str:
    out = []
    for m in messages:
        for p in m.parts:
            if isinstance(p, SystemPromptPart):
                out.append(f"SYSTEM: {p.content}")
            elif isinstance(p, UserPromptPart):
                out.append(f"USER: {p.content if isinstance(p.content, str) else json.dumps(p.content, default=str)}")
            elif isinstance(p, ToolReturnPart):
                out.append(f"TOOL RESULT ({p.tool_name}): {json.dumps(p.content, default=str)[:4000]}")
            elif isinstance(p, RetryPromptPart):
                out.append(f"USER: your last answer was rejected: {p.content if isinstance(p.content, str) else json.dumps(p.content, default=str)}")
            elif isinstance(p, ToolCallPart):
                out.append(f"ASSISTANT: {json.dumps(p.args) if not isinstance(p.args, str) else p.args}")
            elif isinstance(p, TextPart):
                out.append(f"ASSISTANT: {p.content}")
    return "\n\n".join(out)


def cli_model(alias: str = "sonnet", timeout: float = 360) -> FunctionModel:
    if shutil.which("claude") is None:
        raise RuntimeError("the claude CLI is not installed")

    async def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not info.output_tools:
            raise RuntimeError("cli model needs a structured output type")
        out_tool = info.output_tools[0]
        args = llm.cli_command(alias, info.instructions or "", out_tool.parameters_json_schema)
        prompt = _transcript(messages) + "\n\nAnswer the last USER message with the JSON object only."
        proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, env=llm.cli_env(), cwd=llm.CLI_CWD)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"claude CLI timed out after {timeout}s")
        data = llm.cli_result(proc.returncode, stdout, stderr)
        if data.get("structured_output") is None:
            raise RuntimeError(f"claude CLI error: {str(data.get('result'))[:400]}")
        u = data.get("usage") or {}
        usage = RequestUsage(input_tokens=(u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                             + (u.get("cache_creation_input_tokens") or 0), output_tokens=u.get("output_tokens") or 0)
        return ModelResponse(parts=[ToolCallPart(out_tool.name, data["structured_output"])], usage=usage,
                             model_name=f"claude-cli:{alias}")

    return FunctionModel(fn, model_name=f"claude-cli:{alias}")


def resolve(model, default: str):
    """'cli:sonnet' as the model or the default (or DECAP_LLM=cli with no model given) -> the CLI backend; a bare
    Claude name (DECAP_GUIDE_MODEL=claude-sonnet-5-5, the core guide's spelling) gets its 'anthropic:' prefix;
    anything else passes through."""
    if model is None and os.environ.get("DECAP_LLM") == "cli":
        return cli_model(os.environ.get("DECAP_CLI_MODEL", "sonnet"))
    m = model or default   # DECAP_WITNESS_MODEL=cli:sonnet arrives here as `default`
    if isinstance(m, str) and m.startswith("cli:"):
        return cli_model(m.split(":", 1)[1] or "sonnet")
    return llm.provider_model(m) if isinstance(m, str) else m
