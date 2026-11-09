from basic_agent import lifecycle
from basic_agent.transcript import Transcript


def test_lifecycle_registers_and_lists_running_first(tmp_path):
    t1 = Transcript(tmp_path / "a.jsonl")
    t2 = Transcript(tmp_path / "b.jsonl")
    sessions = tmp_path / "sessions.json"
    sid1 = lifecycle.start("tt_um_x", "hunt", "is anything hidden?", transcript=t1, sessions_path=sessions)
    sid2 = lifecycle.start("tt_um_x", "explain", None, transcript=t2, sessions_path=sessions)
    lifecycle.finish(sid2, "done", transcript=t2, sessions_path=sessions, intent="explain", verdict="proven")

    rows = lifecycle.list_sessions(sessions)
    assert [r["status"] for r in rows] == ["running", "done"]
    assert rows[0]["id"] == sid1 and rows[1]["id"] == sid2
    assert lifecycle.get(sid1, sessions)["kind"] == "hunt"
    assert lifecycle.get(sid2, sessions)["verdict"] == "proven"


def test_follow_prints_compact_lines(tmp_path):
    t = Transcript(tmp_path / "a.jsonl")
    sessions = tmp_path / "sessions.json"
    sid = lifecycle.start("tt_um_x", "hunt", "x", transcript=t, sessions_path=sessions)
    t.record("tool", tool="get_region")
    t.record("claim", attempt=0, verdict="proven")
    lifecycle.finish(sid, "done", transcript=t, sessions_path=sessions, intent="hunt", verdict="clean")

    out: list[str] = []
    rc = lifecycle.follow(sid, follow=True, sessions_path=sessions, out=out.append)
    assert rc == 0
    assert out == ["tool  get_region", "claim 0 proven", "done  hunt clean"]
