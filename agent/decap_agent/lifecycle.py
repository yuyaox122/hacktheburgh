"""Terminal lifecycle: turn the bASIC agent from a one-shot command into a runtime.

Mirrors DimOS's `run --daemon` / `status` / `log -f` / `stop`. A *session* is one entry in a
`runs/sessions.json` file:

    {id, run_id, chip, kind, command, transcript, started_at, status}

  kind    hunt | explain | guide | serve
  status  running | done | error | stopped

`decap status`, `decap log -f <id>` and `decap stop <id>` read/write this file. Registration is
done by `run_command` (terminal.py) and `explain_region` (witness.py); the `serve` daemon registers
itself in cli.py. The registry lives next to the run's transcript (so ad-hoc/test transcripts get
their own `sessions.json`), defaulting to `runs/sessions.json` when there is no transcript (serve).
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from decap.paths import RUNS

from .transcript import Transcript

SESSIONS = RUNS / "sessions.json"


def _registry(transcript: Transcript | None = None, sessions_path: Path | None = None) -> Path:
    if sessions_path is not None:
        return Path(sessions_path)
    if transcript is not None and transcript.path is not None:
        return Path(transcript.path).parent / "sessions.json"
    return SESSIONS


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []


def _save(path: Path, sessions: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(sessions, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _update(session_id: str, patch: dict[str, Any], sessions_path: Path) -> bool:
    rows = _load(sessions_path)
    for s in rows:
        if s.get("id") == session_id:
            s.update(patch)
            _save(sessions_path, rows)
            return True
    return False


def start(chip: str | None, kind: str, command: str | None = None, *,
          transcript: Transcript | None = None, run_id: str | None = None,
          sessions_path: Path | None = None) -> str:
    """Register a running session and return its id."""
    sid = uuid.uuid4().hex[:12]
    row: dict[str, Any] = {
        "id": sid,
        "run_id": run_id or (transcript.run_id if transcript is not None else None),
        "chip": chip,
        "kind": kind,
        "command": command,
        "transcript": str(transcript.path) if transcript is not None and transcript.path else None,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "status": "running",
    }
    reg = _registry(transcript, sessions_path)
    rows = _load(reg)
    rows.append(row)
    _save(reg, rows)
    return sid


def finish(session_id: str, status: str = "done", *, transcript: Transcript | None = None,
           sessions_path: Path | None = None, **done: Any) -> None:
    """Mark a session done/error/stopped; extra kwargs (intent, verdict, error) land on the record.
    A session someone stopped stays 'stopped' (its result is still recorded)."""
    reg = _registry(transcript, sessions_path)
    cur = next((s for s in _load(reg) if s.get("id") == session_id), None)
    if cur is not None and cur.get("status") == "stopped":
        status = "stopped"
    _update(session_id, {"status": status, **done}, reg)


def stop(session_id: str, sessions_path: Path | None = None) -> bool:
    """Mark a session stopped (a registry change only; nothing is killed)."""
    return _update(session_id, {"status": "stopped"}, _registry(None, sessions_path))


def get(session_id: str, sessions_path: Path | None = None) -> dict[str, Any] | None:
    for s in _load(_registry(None, sessions_path)):
        if s.get("id") == session_id:
            return s
    return None


def list_sessions(sessions_path: Path | None = None) -> list[dict[str, Any]]:
    """Running first, then recent done, each newest-first."""
    rows = _load(_registry(None, sessions_path))
    running = [s for s in rows if s.get("status") == "running"]
    rest = [s for s in rows if s.get("status") != "running"]
    key = lambda s: s.get("started_at", "")
    return sorted(running, key=key, reverse=True) + sorted(rest, key=key, reverse=True)


def format_session(s: dict[str, Any]) -> str:
    return (f"{s.get('id', '?')}  {s.get('chip') or '-'}  {s.get('kind') or '-'}  "
            f"{s.get('started_at', '')}  {s.get('status', '?')}")


def format_event(ev: dict[str, Any]) -> str:
    kind = str(ev.get("kind", "?"))
    if kind == "tool":
        detail = str(ev.get("tool", "?"))
    elif kind == "claim":
        detail = f"{ev.get('attempt', '?')} {ev.get('verdict', '?')}"
    elif kind == "done":
        detail = " ".join(str(x) for x in (ev.get("intent"), ev.get("verdict")) if x not in (None, ""))
    elif kind == "note":
        detail = str(ev.get("intent") or ev.get("text") or "")
    elif kind == "agent":
        detail = str(ev.get("question") or ev.get("answer") or "")[:80]
    else:
        detail = ""
    return f"{kind.ljust(5)} {detail}".rstrip()


def _emit_final(out: Callable[[str], None], s: dict[str, Any]) -> None:
    status = s.get("status", "?")
    if status == "done":
        parts = [str(x) for x in (s.get("intent"), s.get("verdict")) if x not in (None, "")]
        out("done  " + " ".join(parts) if parts else "done")
    else:
        out(str(status))


def follow(session_id: str, *, follow: bool = True, sessions_path: Path | None = None,
           poll_s: float = 0.25, out: Callable[[str], None] = print) -> int:
    """Print a session's transcript (compact lines); with follow=True keep tailing until it ends."""
    s = get(session_id, sessions_path)
    if s is None:
        out(f"[decap] no session {session_id}")
        return 1
    path = s.get("transcript")
    if not path:
        out(f"[decap] session {session_id} ({s.get('kind')}) has no transcript to follow")
        return 0
    path = Path(path)
    pos = 0
    while True:
        if path.exists():
            with path.open(encoding="utf-8") as f:
                f.seek(pos)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out(format_event(json.loads(line)))
                    except ValueError:
                        continue
                pos = f.tell()
        s = get(session_id, sessions_path)
        if s is None or s.get("status") != "running":
            if s is not None:
                _emit_final(out, s)
            return 0
        if not follow:
            return 0
        time.sleep(poll_s)
