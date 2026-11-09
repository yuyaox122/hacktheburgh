"""LLM plumbing shared by the core's guide (core/guide.py) and the agent layer (decap_agent.cli_model).

- The default Claude model, and DECAP_GUIDE_MODEL in either spelling: bare ('claude-sonnet-5-5', what the Anthropic
  API takes) or with a provider prefix ('anthropic:claude-sonnet-5-5', what the agent layer's pydantic-ai takes).
- The local `claude` CLI (Claude Code login) as a one-shot model: the guide's fallback when there is no API key, and
  the agent layer's opt-in backend. Isolated: no tools, no MCP, no user/project settings (so no hooks fire), no saved
  session, run from a temp dir, with CLAUDE_CODE_* variables and ANTHROPIC_API_KEY removed (so the CLI uses its own
  login, not the key).
"""
from __future__ import annotations

import json
import os
import tempfile

DEFAULT_MODEL = "claude-sonnet-5-5"   # SPEC §6.5


def api_model(name: str | None) -> str:
    """A model id for the Anthropic API: 'anthropic:' dropped; another provider's id (or none) -> DEFAULT_MODEL."""
    if not name:
        return DEFAULT_MODEL
    if name.startswith("anthropic:"):
        return name.split(":", 1)[1]
    return DEFAULT_MODEL if ":" in name else name


def provider_model(name: str) -> str:
    """A model id for pydantic-ai: a bare Claude name gets its 'anthropic:' prefix; anything else is unchanged."""
    return f"anthropic:{name}" if ":" not in name and name.startswith("claude") else name


# ---------------------------------------------------------------------------------------------- the claude CLI
CLI_CWD = tempfile.gettempdir()


def cli_command(model: str, system: str, json_schema: dict | None = None) -> list[str]:
    """`claude -p` with no tools, JSON output, and (optionally) a JSON schema for structured output. The prompt
    goes on stdin."""
    args = ["claude", "-p", "--output-format", "json", "--model", model, "--tools", "", "--no-session-persistence",
            "--strict-mcp-config", "--setting-sources", "", "--system-prompt", system]
    if json_schema is not None:
        args += ["--json-schema", json.dumps(json_schema)]
    return args


def cli_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items()
            if not k.startswith("CLAUDE_CODE") and k not in ("CLAUDECODE", "ANTHROPIC_API_KEY")}


def cli_result(returncode: int, stdout: str | bytes, stderr: str | bytes) -> dict:
    """The CLI's JSON reply. RuntimeError if the process failed or the reply is an error."""
    if returncode != 0:
        out = stderr or stdout
        raise RuntimeError(f"claude CLI failed: {(out.decode() if isinstance(out, bytes) else out)[-400:]}")
    data = json.loads(stdout)
    if data.get("is_error"):
        raise RuntimeError(f"claude CLI error: {str(data.get('result'))[:400]}")
    return data
