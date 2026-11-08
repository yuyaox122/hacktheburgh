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


def test_plain_english_guard_rejects_invented_constants():
    from decap_agent.plain import Plain, unsupported_numbers
    material = "count0 <= (count0 == 6'd59) ? 6'd0 : count0 + 1'b1; ui_in == 8'h89"
    ok = Plain(headline="A counter that wraps at 59", explanation="It matches 137 on ui_in.", example="")
    bad = Plain(headline="A counter that wraps at 60", explanation="It fires at 200.", example="")
    assert unsupported_numbers(ok, material) == []          # 59 written plainly, 137 = 8'h89
    assert unsupported_numbers(bad, material) == [60, 200]


def test_uploaded_verilog_top_module_is_lined_up_with_the_chip():
    from decap_agent.source_check import retop
    src = "module helper(input a, output b); assign b = a; endmodule\nmodule my_adder(input [7:0] ui_in, output [7:0] uo_out); endmodule"
    text, was = retop(src, "tt_um_x")
    assert was == "my_adder" and "module tt_um_x" in text and "module helper" in text
    assert retop("module tt_um_x(); endmodule", "tt_um_x") == ("module tt_um_x(); endmodule", None)


def test_upload_check_routes_and_fails_soft_without_the_real_core():
    from decap_agent.terminal import run_command
    res = asyncio.run(run_command(build_server(create_stub_app()), "not_a_chip", "check my file",
                                  verilog="module m(); endmodule", filename="m.v"))
    assert res["intent"] == "check_source" and res["verdict"] == "unavailable" and res["detail"]
