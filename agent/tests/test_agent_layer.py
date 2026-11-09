import asyncio
import json
import os

import pytest

from fastapi.testclient import TestClient
from fastmcp import Client
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from decap_agent import cli
from decap_agent.events import create_events_app
from decap_agent.guide import ask
from decap_agent.replay import replay
from decap_agent.server import build_server
from decap_agent.stub_core import create_stub_app
from decap_agent.transcript import Transcript, load, result_payload

EXPECTED_TOOLS = {"list_chips", "resolve_scope", "strip_names", "recover_blocks", "decompile", "prove_equiv",
                  "simulate", "find_rare_triggers", "plant_trigger", "cone_of_influence", "critical_path",
                  "get_region", "probe_truth", "propose_claim"}
PLANTED = "stub_counter8+t137"


def run(coro):
    return asyncio.run(coro)


async def calls(server, *steps):
    """Run (tool, args) steps in one client session; return the payloads."""
    out = []
    async with Client(server) as c:
        for tool, args in steps:
            r = await c.call_tool(tool, args, raise_on_error=False)
            out.append((r.is_error, result_payload(r)))
    return out


def test_tools_are_generated_from_core_routes():
    async def go():
        async with Client(build_server(create_stub_app())) as c:
            return {t.name: t for t in await c.list_tools()}
    tools = run(go())
    assert set(tools) == EXPECTED_TOOLS
    assert all((t.description or "").strip() for t in tools.values()), "docstrings must reach agents"


def test_arguments_reach_the_core():
    # Regression: an Optional body made FastMCP drop `against`, so a planted chip "proved" against its source.
    (_, planted), (_, vs_source), (_, vs_netlist), (_, sim) = run(calls(
        build_server(create_stub_app()),
        ("plant_trigger", {"chip": "stub_counter8", "n": 137}),
        ("prove_equiv", {"chip": "stub_counter8+t137", "against": "author_source"}),
        ("prove_equiv", {"chip": "stub_counter8+t137"}),
        ("simulate", {"chip": "stub_counter8", "cycles": 3, "start": 199}),
    ))
    assert planted["chip"] == "stub_counter8+t137"
    assert vs_source["verdict"] == "failed" and vs_source["counterexample"]["ui_in"] == 137
    assert vs_netlist["verdict"] == "proven" and vs_netlist["counterexample"] is None
    assert [s["count"] for s in sim["trace"]] == [199, 200, 201]
    assert [s["hit"] for s in sim["trace"]] == [0, 1, 0]


def test_region_scope_reaches_the_core_and_limits_the_work():
    # The headline feature: agents aimed at a selected region. A dropped nested `scope` would silently widen to the whole chip.
    results = run(calls(
        build_server(create_stub_app()),
        ("plant_trigger", {"chip": "stub_counter8", "n": 137}),
        ("find_rare_triggers", {"chip": PLANTED, "scope": {"cone_of": "hit"}}),
        ("find_rare_triggers", {"chip": PLANTED, "scope": {"cells": ["trg_3"]}}),
        ("find_rare_triggers", {"chip": PLANTED}),
        ("prove_equiv", {"chip": PLANTED, "against": "author_source", "scope": {"nets": ["hit"]}}),
        ("prove_equiv", {"chip": PLANTED, "against": "author_source", "scope": {"cone_of": "out"}}),
        ("resolve_scope", {"chip": PLANTED, "scope": {"nets": ["nope"]}}),
    ))
    (_, _), (_, hit_only), (_, trg_only), (_, whole), (_, legit), (_, out), (bad_err, _) = results
    assert [f["cone"] for f in hit_only["flagged"]] == ["c1"]
    assert [f["cone"] for f in trg_only["flagged"]] == ["c2"]
    assert [f["cone"] for f in whole["flagged"]] == ["c1", "c2"]
    assert trg_only["touched"]["cells"][0].startswith("trg_") and "cmp_0" not in trg_only["touched"]["cells"]
    assert legit["verdict"] == "proven"           # the author's source explains the 'hit' comparator
    assert out["verdict"] == "failed" and out["counterexample"]["ui_in"] == 137 and out["failing_cone"] == "c2"
    assert bad_err, "a scope that matches nothing must be an error, not the whole chip"


def test_transcript_carries_scope_and_touched_cells(tmp_path):
    t = Transcript(tmp_path / "t.jsonl")
    run(calls(build_server(create_stub_app(), t),
              ("plant_trigger", {"chip": "stub_counter8", "n": 5}),
              ("find_rare_triggers", {"chip": "stub_counter8+t5", "scope": {"cone_of": "trig"}})))
    last = load(tmp_path / "t.jsonl")[-1]
    assert last["scope"] == {"cone_of": "trig"}
    assert last["cells"] and all(c.startswith("trg_") for c in last["cells"])
    assert "trig" in last["nets"]


def test_unknown_arguments_are_rejected_not_silently_dropped(tmp_path):
    # FastMCP only warns and drops unknown args; a misspelled `scope` would silently search the whole chip.
    t = Transcript(tmp_path / "t.jsonl")
    [(is_error, payload)] = run(calls(build_server(create_stub_app(), t),
                                      ("find_rare_triggers", {"chip": "stub_counter8", "scop": {"cone_of": "hit"}})))
    text = json.dumps(payload)
    assert is_error and "scop" in text and "scope" in text, text
    assert load(tmp_path / "t.jsonl")[-1]["is_error"] is True


REAL_CORE = os.environ.get("DECAP_REAL_CORE_ROOT")


@pytest.mark.skipif(not REAL_CORE, reason="set DECAP_REAL_CORE_ROOT to a 4-decap-build checkout with decap/data fetched")
def test_real_core_judge_moment(monkeypatch, capsys):
    monkeypatch.syspath_prepend(REAL_CORE)
    monkeypatch.setenv("DECAP_CORE_APP", "decap.api.main:app")
    assert cli.main(["--no-transcript", "demo", "--n", "137"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["condition"] == "ui_in == 8'd137" and out["netlist_vs_author_source"] == "failed"
    assert out["counterexample"]["ui_in"] == 137 and out["fix_verdict"] == "proven"


def test_clean_chip_flags_only_the_legitimate_comparator():
    [(_, res)] = run(calls(build_server(create_stub_app()), ("find_rare_triggers", {"chip": "stub_counter8"})))
    assert [f["likely"] for f in res["flagged"]] == ["legitimate"]


def test_every_result_says_stub():
    results = run(calls(build_server(create_stub_app()),
                        ("list_chips", {}), ("decompile", {"chip": "stub_counter8"}),
                        ("critical_path", {"chip": "stub_counter8"})))
    rows, dec, cp = (r for _, r in results)
    assert all(r["source"] == "stub" for r in rows) and dec["source"] == cp["source"] == "stub"


def test_unknown_chip_is_a_tool_error_and_is_recorded(tmp_path):
    t = Transcript(tmp_path / "t.jsonl")
    [(is_error, _)] = run(calls(build_server(create_stub_app(), t), ("decompile", {"chip": "nope"})))
    assert is_error
    last = load(tmp_path / "t.jsonl")[-1]
    assert last["tool"] == "decompile" and last["is_error"] is True


def test_demo_finds_the_judges_number(tmp_path, capsys):
    path = tmp_path / "demo.jsonl"
    assert cli.main(["--transcript", str(path), "demo", "--n", "42"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["found"] == "wire trig = (ui_in == 8'd42);"
    assert out["region"] == {"cone_of": "out"} and out["region_cells"] == 22
    assert out["decompile_vs_netlist"] == "proven" and out["netlist_vs_author_source"] == "failed"
    assert out["counterexample"]["ui_in"] == 42 and out["source"] == "stub"
    events = load(path)
    assert [e["tool"] for e in events][:4] == ["list_chips", "plant_trigger", "strip_names", "resolve_scope"]
    assert all(e["kind"] == "tool" and e["wall_s"] >= 0 for e in events)
    assert any(e["cells"] for e in events), "the 3D view needs cells to light up"


def test_replay_check_passes_and_detects_tampering(tmp_path, capsys):
    path = tmp_path / "demo.jsonl"
    cli.main(["--transcript", str(path), "demo", "--n", "7"])
    capsys.readouterr()
    events = load(path)
    assert {r["status"] for r in run(replay(events, build_server(create_stub_app()), check=True))} == {"ok"}
    tampered = [dict(e) for e in events]
    i = next(i for i, e in enumerate(tampered) if e["tool"] == "find_rare_triggers")
    tampered[i]["result"] = {"flagged": [], "source": "stub"}
    rows = run(replay(tampered, build_server(create_stub_app()), check=True))
    assert [r["status"] for r in rows if r["tool"] == "find_rare_triggers"] == ["mismatch"]


def test_guide_answers_from_tools_and_logs_usage(tmp_path):
    def scripted(messages, info):
        returns = [p for m in messages for p in getattr(m, "parts", []) if isinstance(p, ToolReturnPart)]
        if not returns:
            return ModelResponse(parts=[ToolCallPart(tool_name="plant_trigger", args={"chip": "stub_counter8", "n": 99})])
        if len(returns) == 1:
            return ModelResponse(parts=[ToolCallPart(tool_name="find_rare_triggers", args={"chip": "stub_counter8+t99"})])
        return ModelResponse(parts=[TextPart(content="Found ui_in == 8'd99 (stub results).")])

    t = Transcript(tmp_path / "t.jsonl")
    answer = run(ask("Plant 99 and find it.", build_server(create_stub_app(), t), t, model=FunctionModel(scripted)))
    assert "8'd99" in answer
    events = load(tmp_path / "t.jsonl")
    assert [e["tool"] for e in events if e["kind"] == "tool"] == ["plant_trigger", "find_rare_triggers"]
    agent = [e for e in events if e["kind"] == "agent"][-1]
    assert agent["requests"] == 3 and agent["input_tokens"] is not None


def test_events_endpoint_serves_the_transcript(tmp_path):
    t = Transcript(tmp_path / "t.jsonl")
    t.record("note", text="hello")
    client = TestClient(create_events_app(tmp_path / "t.jsonl"))
    assert client.get("/healthz").json()["exists"] is True
    assert client.get("/events.json").json()[0]["text"] == "hello"
