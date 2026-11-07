import asyncio
import json

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

EXPECTED_TOOLS = {"list_chips", "strip_names", "recover_blocks", "decompile", "prove_equiv", "simulate",
                  "find_rare_triggers", "plant_trigger", "cone_of_influence", "critical_path"}


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
    assert out["decompile_vs_netlist"] == "proven" and out["netlist_vs_author_source"] == "failed"
    assert out["counterexample"]["ui_in"] == 42 and out["source"] == "stub"
    events = load(path)
    assert [e["tool"] for e in events][:2] == ["list_chips", "plant_trigger"]
    assert all(e["kind"] == "tool" and e["wall_s"] >= 0 for e in events)


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
