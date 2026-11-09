"""The investigator (plan -> act -> remember -> report) and the terminal's routing, on the stub core, offline."""

import asyncio

from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from basic_agent import investigate as inv
from basic_agent.server import build_server
from basic_agent.stub_core import create_stub_app
from basic_agent.terminal import _intent
from basic_agent.transcript import Transcript


def test_intent_routing():
    assert _intent("is anything hidden in this chip?") == "hunt"
    assert _intent("what does b3 do?") == "explain"
    assert _intent("audit this chip") == "investigate"
    assert _intent("what does b2 do, then is anything hidden?") == "investigate"
    assert _intent("what is the slowest path?") == "investigate"
    assert _intent("hello") == "guide"
    assert _intent("red-team drill: plant a trigger") == "drill"
    # review of #95: questions about THIS chip's planted logic are hunts, not drills
    assert _intent("was a backdoor planted in this chip?") == "hunt"
    assert _intent("is anything planted here?") == "hunt"
    assert _intent("implanted trigger?") == "hunt"


def test_drill_catches_the_planted_value():
    from basic_agent.terminal import run_command
    res = asyncio.run(run_command(build_server(create_stub_app()), "stub_counter8", "red team drill"))
    assert res["intent"] == "drill" and res["caught"] and str(res["secret"]) in res["flag"]["condition"]


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
    monkeypatch.setattr("basic_agent.terminal._hunt", fake_hunt)
    model = _scripted([("plan", {"steps": ["hunt"]}), ("hunt", {"why": "check"})])
    res = asyncio.run(inv.investigate(build_server(create_stub_app()), "stub_counter8", "anything hidden?", model=model))
    assert "tested, not proven" in res["summary"]
    assert any("inconclusive" in f["text"] for f in res["findings"])
    assert inv.recall_memory("stub_counter8") == []         # 'tested' is not remembered as a fact


def test_plain_english_guard_rejects_invented_constants():
    from basic_agent.plain import Plain, unsupported_numbers
    material = "count0 <= (count0 == 6'd59) ? 6'd0 : count0 + 1'b1; ui_in == 8'h89"
    ok = Plain(headline="A counter that wraps at 59", explanation="It matches 137 on ui_in.", example="")
    bad = Plain(headline="A counter that wraps at 60", explanation="It fires at 200.", example="")
    assert unsupported_numbers(ok, material) == []          # 59 written plainly, 137 = 8'h89
    assert unsupported_numbers(bad, material) == [200]           # 60 next to 59 is a legit neighbour; 200 is invented


def test_uploaded_verilog_top_module_is_lined_up_with_the_chip():
    from basic_agent.source_check import retop
    src = "module helper(input a, output b); assign b = a; endmodule\nmodule my_adder(input [7:0] ui_in, output [7:0] uo_out); endmodule"
    text, was = retop(src, "tt_um_x")
    assert was == "my_adder" and "module tt_um_x" in text and "module helper" in text
    assert retop("module tt_um_x(); endmodule", "tt_um_x") == ("module tt_um_x(); endmodule", None)


def test_upload_check_routes_and_fails_soft_without_the_real_core():
    from basic_agent.terminal import run_command
    res = asyncio.run(run_command(build_server(create_stub_app()), "not_a_chip", "check my file",
                                  verilog="module m(); endmodule", filename="m.v"))
    assert res["intent"] == "check_source" and res["verdict"] == "unavailable" and res["detail"]


def test_best_attempt_beats_last_attempt():
    from basic_agent.scoping import best_attempt
    tries = [{"verdict": "partial", "attempt": 0}, {"verdict": "unparseable", "attempt": 1}, {"verdict": "refuted", "attempt": 2}]
    assert best_attempt(tries)["attempt"] == 0
    assert best_attempt(tries + [{"verdict": "proven", "attempt": 3}])["attempt"] == 3


def test_block_scope_is_narrowed_to_the_blocks_own_register():
    from basic_agent.scoping import narrow_scope
    region = {"outputs": [{"name": "next(data0)", "kind": "register_next", "flops": ["q_c1"]},
                          {"name": "next(shreg0)", "kind": "register_next", "flops": ["q_c7", "q_c8"]},
                          {"name": "next(state0)", "kind": "register_next", "flops": ["q_c9"]}]}
    blocks = {"blocks": [{"id": "b3", "text": "shreg0 <= {shreg0[14:0], ui_in[2]};"}]}

    async def call(tool, **args):
        return region if tool == "get_region" else blocks
    scope, why = asyncio.run(narrow_scope(call, "chip", {"cone_of": "b3"}))
    assert scope == {"cells": ["c7", "c8"]} and "shreg0" in why
    assert asyncio.run(narrow_scope(call, "chip", {"cone_of": "uo_out[0]"})) == ({"cone_of": "uo_out[0]"}, None)


def test_claims_can_select_bits_of_next_and_use_helper_wires():
    from basic.core.claim_expr import expand_temps, parse, temp_defs, temps_used, unparse
    assert unparse(parse("next(shreg0)[0] == shreg0[1]")) == "next(shreg0[0]) == shreg0[1]"
    assert unparse(parse("next(x[7:4])[1] == 1'b1")) == "next(x[5]) == 1'd1"
    d = temp_defs("wire t4 = a & b;\n wire t5 = (q | t4);")
    assert temps_used(["y <= t5;"], d) == ["t4", "t5"]
    assert expand_temps("n == t5", d) == "n == ((q | (a & b)))"


def test_mux_claim_precedence_is_fixed_to_what_the_writer_meant():
    from basic.core.claim_expr import fix_mux_precedence
    assert fix_mux_precedence("next(d) == (u == 1) ? d : s") == "next(d) == ((u == 1) ? d : s)"
    assert fix_mux_precedence("next(a) == b && next(d) == c ? d : s") == "(next(a) == b) && (next(d) == (c ? d : s))"
    assert fix_mux_precedence("next(d) == (c ? d : s)") == "next(d) == (c ? d : s)"   # already right: unchanged
    assert fix_mux_precedence("not a claim ((") == "not a claim (("


def test_heldout_chips_are_refused_to_interactive_agents(monkeypatch):
    from basic_agent import heldout
    monkeypatch.setattr(heldout, "_cache", {"tt_um_secret"})
    monkeypatch.delenv("BASIC_HELDOUT_OK", raising=False)
    assert "held-out" in heldout.refusal("decompile", {"chip": "tt_um_secret"})
    assert "held-out" in heldout.refusal("prove_equiv", {"chip": "tt_um_secret+t9", "against": "author_source"})
    assert heldout.refusal("benchmark", {})
    assert heldout.refusal("decompile", {"chip": "tt_um_dev"}) is None
    monkeypatch.setenv("BASIC_HELDOUT_OK", "1")                       # the frozen bench
    assert heldout.refusal("decompile", {"chip": "tt_um_secret"}) is None


def test_guard_is_live_in_the_mcp_server(monkeypatch):
    from fastmcp import Client
    from basic_agent import heldout
    monkeypatch.setattr(heldout, "_cache", {"stub_counter8"})
    monkeypatch.delenv("BASIC_HELDOUT_OK", raising=False)

    async def go():
        async with Client(build_server(create_stub_app())) as c:
            return await c.call_tool("decompile", {"chip": "stub_counter8"}, raise_on_error=False)
    r = asyncio.run(go())
    assert r.is_error and "held-out" in str(r.content[0].text)


def test_real_split_file_is_found():
    from basic_agent.heldout import SPLIT
    assert SPLIT.exists()


def test_number_guard_allows_neighbours_of_constants():
    from basic_agent.plain import Plain, unsupported_numbers
    m = "count0 <= (count0 == 6'd59) ? 6'd0 : count0 + 1'b1;"
    assert unsupported_numbers(Plain(headline="", explanation="It goes 0 to 59, so 60 steps; 58 then 59.", example=""), m) == []
    assert unsupported_numbers(Plain(headline="", explanation="It fires at 137.", example=""), m) == [137]


def test_hunt_without_author_source_is_unchecked_not_tested(monkeypatch):
    from basic_agent import terminal

    async def fake_call(client, tool, **args):
        return (False, {"flagged": []}) if tool == "find_rare_triggers" else (False, {"verdict": "unavailable", "detail": "no author source"})
    monkeypatch.setattr(terminal, "_call", fake_call)
    res = asyncio.run(terminal._hunt(None, "chip", None))
    assert res["verdict"] == "unchecked" and "random" not in (res["note"] or "") and "no author source" in res["note"]


def test_drill_is_not_fooled_by_a_last_step_counterexample(monkeypatch):
    """Real core: the proof fails, but its counterexample is the last trace step (ui_in=23), not N."""
    from basic_agent import terminal
    import secrets
    monkeypatch.setattr(secrets, "randbelow", lambda k: 162)          # N = 163

    async def fake_call(client, tool, **args):
        if tool == "plant_trigger":
            return False, {"chip": "tt_um_mac+t163", "inserted_cells": 9}
        if tool == "find_rare_triggers":
            return False, {"flagged": [{"id": "f0", "condition": "ui_in == 8'd163", "line": 12}]}
        return False, {"verdict": "failed", "counterexample": {"step": 20, "ui_in": 23, "ena": 0}}
    monkeypatch.setattr(terminal, "_call", fake_call)
    res = asyncio.run(terminal._drill(None, "tt_um_mac", None))
    assert res["caught"] and res["flag"]["id"] == "f0"


def test_lifecycle_keeps_stopped(tmp_path):
    from basic_agent import lifecycle
    p = tmp_path / "s.json"
    sid = lifecycle.start("c", "hunt", "x", sessions_path=p)
    lifecycle.stop(sid, sessions_path=p)
    lifecycle.finish(sid, "done", sessions_path=p, verdict="clean")
    assert lifecycle.get(sid, sessions_path=p)["status"] == "stopped"


SRC = """module m(input [7:0] ui_in, output [7:0] uo_out);
    wire t1 = ui_in[0] & ui_in[1];
    assign uo_out = {7'b0, t1 | ui_in[2]};
endmodule"""


def test_fault_drill_routes_and_injects_one_operator():
    from basic_agent.repair import inject_fault
    assert _intent("fault drill: switch one gate") == "fault"
    f = inject_fault(SRC, seed=3)
    changed = [i for i, (a, b) in enumerate(zip(SRC.split("\n"), f["verilog"].split("\n"))) if a != b]
    assert changed == [f["line"] - 1] and f["before"] != f["after"]


def test_mutation_search_finds_the_fix_that_the_checker_proves():
    from basic_agent.repair import candidate_fixes, inject_fault, mutation_search
    f = inject_fault(SRC, seed=3)
    assert any(c[1].strip() == f["before"] for c in candidate_fixes(f["verilog"], [f["line"]]))

    async def verify(text, label, quiet=False):            # the "chip" is SRC: proven iff identical
        return {"verdict": "proven" if text == SRC else "failed"}
    hit = asyncio.run(mutation_search(f["verilog"], verify, [f["line"]]))
    assert hit and hit["line"] == f["line"] and hit["verilog"] == SRC


def test_gate_level_candidates_swap_cell_type_only():
    from basic_agent.repair import candidate_fixes
    v = "  sky130_fd_sc_hd__and2_1 c53 (.A(n1), .B(n2), .X(n3));"
    outs = {c[1].strip() for c in candidate_fixes(v, [1])}
    assert "sky130_fd_sc_hd__or2_1 c53 (.A(n1), .B(n2), .X(n3));" in outs and len(outs) == 5
