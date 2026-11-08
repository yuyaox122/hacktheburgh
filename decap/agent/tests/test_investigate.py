"""The investigator (plan -> act -> remember -> report) and the terminal's routing, on the stub core, offline."""

import asyncio

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from decap_agent import investigate as inv
from decap_agent.server import build_server
from decap_agent.stub_core import create_stub_app
from decap_agent.terminal import _intent
from decap_agent.transcript import Transcript


def test_intent_routing():
    assert _intent("is anything hidden in this chip?") == "hunt"
    assert _intent("what does b3 do?") == "explain"
    assert _intent("audit this chip") == "investigate"
    assert _intent("what does b2 do, then is anything hidden?") == "investigate"
    assert _intent("what is the slowest path?") == "investigate"
    assert _intent("hello") == "guide"
    assert _intent("red-team drill: plant a trigger") == "drill"


def test_drill_catches_the_planted_value():
    from decap_agent.terminal import run_command
    res = asyncio.run(run_command(build_server(create_stub_app()), "stub_counter8", "red team drill"))
    assert res["intent"] == "drill" and res["caught"] and res["found_value"] == res["secret"]


def _scripted(calls):
    """A FunctionModel that makes one scripted tool call per request, then returns the report."""
    def fn(messages, info: AgentInfo):
        i = sum(1 for m in messages if isinstance(m, ModelResponse))
        if i < len(calls):
            name, args = calls[i]
            return ModelResponse(parts=[ToolCallPart(name, args)])
        out = info.output_tools[0].name
        return ModelResponse(parts=[ToolCallPart(out, {
            "summary": "No hidden logic here.", "findings": [{"text": "b9 is an adder", "evidence": "yosys", "block": "b9"}],
            "next_steps": ["explain b1"]})])
    return FunctionModel(fn)


def test_investigator_plans_acts_remembers_and_stays_honest(tmp_path, monkeypatch):
    monkeypatch.setattr(inv, "MEMORY", tmp_path / "memory")
    t = Transcript()
    model = _scripted([
        ("plan", {"steps": ["recall", "look for hidden logic", "report"]}),
        ("recall", {"why": "maybe already known"}),
        ("hunt", {"why": "user asked about hidden logic"}),
    ])
    server = build_server(create_stub_app(), transcript=t)

    async def plant():
        from fastmcp import Client
        async with Client(server) as c:
            await c.call_tool("plant_trigger", {"chip": "stub_counter8", "n": 137})
    asyncio.run(plant())
    res = asyncio.run(inv.investigate(server, "stub_counter8+t137", "audit: anything hidden?", transcript=t, model=model))

    kinds = [e["kind"] for e in t.events]
    assert kinds.index("plan") < kinds.index("thought")
    assert res["plan"] == ["recall", "look for hidden logic", "report"]
    assert {e.get("tool") for e in t.events if e["kind"] == "tool"} >= {"find_rare_triggers", "prove_equiv"}
    assert res["hunt"]["verdict"] == "found"              # the stub's planted trigger fails the proof
    assert inv.recall_memory("stub_counter8+t137")[0]["key"] == "hunt"
    # a 'yosys' finding about a block nobody explained this run is downgraded, never passed through
    assert res["findings"][0]["evidence"] == "unverified"


def test_inconclusive_hunt_is_corrected_in_the_report(tmp_path, monkeypatch):
    monkeypatch.setattr(inv, "MEMORY", tmp_path / "memory")

    async def fake_hunt(client, chip, transcript):
        return {"verdict": "inconclusive", "proof": "tested", "flags": [], "counterexample": None}
    monkeypatch.setattr("decap_agent.terminal._hunt", fake_hunt)
    model = _scripted([("plan", {"steps": ["hunt"]}), ("hunt", {"why": "check"})])
    res = asyncio.run(inv.investigate(build_server(create_stub_app()), "stub_counter8", "anything hidden?", model=model))
    assert "tested, not proven" in res["summary"]
    assert any("inconclusive" in f["text"] for f in res["findings"])
    assert inv.recall_memory("stub_counter8") == []         # 'tested' is not remembered as a fact
