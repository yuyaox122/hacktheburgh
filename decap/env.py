"""Load KEY=VALUE secrets files into the environment (the API's decap/.env, the agent's ~/.config/decap/secrets.env)."""
from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | Path) -> list[str]:
    """Read KEY=VALUE lines into os.environ without overriding variables that are already set. Blank lines,
    `# comments`, inline `# comments`, an `export ` prefix and quotes around the value are handled; empty values are
    skipped. Values are never printed. Returns the names loaded (none if the file is missing)."""
    loaded = []
    try:
        text = Path(path).read_text()
    except OSError:
        return loaded
    for line in text.splitlines():
        k, sep, v = line.strip().partition("=")
        k = k.strip().removeprefix("export ").strip()
        if not sep or not k or k.startswith("#") or k in os.environ:
            continue
        v = v.split("#", 1)[0].strip().strip('"').strip("'")
        if v:
            os.environ[k] = v
            loaded.append(k)
    return loaded
