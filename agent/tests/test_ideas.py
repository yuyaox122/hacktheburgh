"""Ideas (issue #68): the ideas agent with a scripted model (offline), and the core's review_edit on real chips."""

from __future__ import annotations

import asyncio
import os

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from basic_agent.server import build_server
from basic_agent.stub_core import create_stub_app
from basic_agent.transcript import result_payload
from basic_agent.witness_api import create_witness_app

VERILOG = """module tt_um_demo (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out
);
    wire t0 = ui_in[0] & ui_in[1];

    assign uo_out = {7'b0, t0};
endmodule
"""


def scripted(ideas: list[dict], seen: dict, reply: str = "Here are some ideas."):
    def fn(messages, info):
        seen["prompt"] = " ".join(str(p.content) for m in messages for p in m.parts if hasattr(p, "content"))
        seen["calls"] = seen.get("calls", 0) + 1
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"reply": reply, "ideas": ideas})])
    return FunctionModel(fn, model_name="scripted")


GOOD_SAME = {"title": "Name the AND", "why": "A clearer name.", "intent": "same", "lines": [5, 5],
             "replacement": ["    wire both_low_bits = ui_in[0] & ui_in[1];", "    wire t0 = both_low_bits;"]}
GOOD_CHANGE = {"title": "Use OR instead", "why": "Lights the LED when either switch is on.", "intent": "change",
               "lines": [5, 5], "replacement": ["    wire t0 = ui_in[0] | ui_in[1];"]}
OUT_OF_RANGE = {**GOOD_CHANGE, "title": "Past the end", "lines": [7, 40]}
HEADER = {**GOOD_CHANGE, "title": "Rename the port", "lines": [2, 2], "replacement": ["    input  wire [7:0] switches,"]}
ENDMODULE = {**GOOD_CHANGE, "title": "Swallow endmodule", "lines": [7, 8], "replacement": ["    assign uo_out = 0;"]}
NEW_MODULE = {**GOOD_CHANGE, "title": "Add a module", "lines": [6, 6], "replacement": ["endmodule", "module x();"]}
NOOP = {**GOOD_SAME, "title": "Nothing", "replacement": ["    wire t0 = ui_in[0] & ui_in[1];"]}
THIRD = {**GOOD_CHANGE, "title": "Invert", "replacement": ["    wire t0 = ~(ui_in[0] & ui_in[1]);"]}
FOURTH = {**GOOD_CHANGE, "title": "XOR", "replacement": ["    wire t0 = ui_in[0] ^ ui_in[1];"]}


def _client(model, tmp_path):
    return TestClient(create_witness_app(create_stub_app(), model=model, runs=tmp_path))


def test_ideas_route_drops_invalid_ideas(tmp_path):
    seen: dict = {}
    client = _client(scripted([OUT_OF_RANGE, HEADER, GOOD_SAME, ENDMODULE, NEW_MODULE, NOOP, GOOD_CHANGE], seen), tmp_path)
    r = client.post("/witness/ideas", json={"chip": "tt_um_demo", "line": 5, "lines": [5, 5], "verilog": VERILOG,
                                            "ask": "make it clearer",
                                            "history": [{"role": "user", "text": "what is t0?"},
                                                        {"role": "agent", "text": "the AND of two switches"}]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert [i["title"] for i in out["ideas"]] == ["Name the AND", "Use OR instead"]
    assert out["ideas"][0]["intent"] == "same" and out["ideas"][0]["lines"] == [5, 5] and out["ideas"][0]["id"]
    assert len({i["id"] for i in out["ideas"]}) == 2
    assert len(out["dropped"]) == 5 and out["model"] == "scripted" and out["reply"].startswith("Here are some ideas. (5 more ideas couldn't be applied")
    p = seen["prompt"]   # small prompt: numbered region, whole module, ask and history
    assert "   5|     wire t0 = ui_in[0] & ui_in[1];" in p and "make it clearer" in p and "the AND of two switches" in p
    assert "Editable lines: 5-7" in p
    assert list(tmp_path.glob("ideas-*.jsonl"))


def test_ideas_are_capped_at_three(tmp_path):
    out = _client(scripted([GOOD_SAME, GOOD_CHANGE, THIRD, FOURTH], {}), tmp_path).post(
        "/witness/ideas", json={"chip": "x", "line": 5, "lines": [5, 5], "verilog": VERILOG, "ask": ""}).json()
    assert len(out["ideas"]) == 3


def test_ideas_one_number_range_means_one_line(tmp_path):
    out = _client(scripted([{**GOOD_CHANGE, "lines": [5]}, {**THIRD, "lines": [4, 5, 6]}], {}), tmp_path).post(
        "/witness/ideas", json={"chip": "x", "line": 5, "lines": [5, 5], "verilog": VERILOG, "ask": ""}).json()
    assert [i["lines"] for i in out["ideas"]] == [[5, 5]] and out["dropped"] == ["'Invert': lines must be [a, b]"]


def test_ideas_with_nothing_applicable_says_so(tmp_path):
    out = _client(scripted([HEADER, NOOP], {}), tmp_path).post(
        "/witness/ideas", json={"chip": "x", "line": 5, "lines": [5, 5], "verilog": VERILOG, "ask": ""}).json()
    assert out["ideas"] == [] and "could be applied" in out["reply"]


def test_ideas_bad_requests_are_clear_errors(tmp_path):
    seen: dict = {}
    client = _client(scripted([GOOD_SAME], seen), tmp_path)
    r = client.post("/witness/ideas", json={"chip": "x", "line": 5, "lines": [5, 50], "verilog": VERILOG, "ask": ""})
    assert r.status_code == 400 and "not a range" in r.json()["detail"]
    r = client.post("/witness/ideas", json={"chip": "x", "line": 1, "lines": [1, 1], "verilog": "  ", "ask": ""})
    assert r.status_code == 400 and "empty" in r.json()["detail"]
    assert client.post("/witness/ideas", json={"chip": "x", "line": 5, "lines": [5], "verilog": VERILOG}).status_code == 422
    assert "calls" not in seen    # rejected before any model call

    def boom(messages, info):
        raise RuntimeError("model is down")
    r = _client(FunctionModel(boom, model_name="boom"), tmp_path).post(
        "/witness/ideas", json={"chip": "x", "line": 5, "lines": [5, 5], "verilog": VERILOG, "ask": ""})
    assert r.status_code == 502 and "model is down" in r.json()["detail"]


# ------------------------------------------------------------------------------------------------ real core
REAL = os.environ.get("BASIC_REAL_CORE_ROOT")
real = pytest.mark.skipif(not REAL, reason="set BASIC_REAL_CORE_ROOT to a checkout of main with basic/data fetched")
CLA = "tt_um_Electom_cla_4bits"


@pytest.fixture
def core(monkeypatch):
    monkeypatch.syspath_prepend(REAL)
    monkeypatch.chdir(REAL)
    from basic.api.main import app
    return app


def _review(core, chip, verilog):
    r = TestClient(core).post(f"/api/chips/{chip}/review-edit", json={"verilog": verilog})
    assert r.status_code == 200, r.text
    return r.json()


def _decompiled(core, chip):
    return TestClient(core).post(f"/api/chips/{chip}/decompile", json={}).json()["verilog"]


@real
def test_review_edit_on_a_real_chip(core):
    src = _decompiled(core, CLA)
    same = _review(core, CLA, src)
    assert same["verdict"] == "same" and same["headline"] == "Same circuit (proven)" and same["counterexample"] is None
    assert same["chip"] == CLA and isinstance(same["seconds"], float)

    flipped = src.replace("ui_in[3:0] + ui_in[7:4]", "ui_in[3:0] - ui_in[7:4]")
    assert flipped != src
    changed = _review(core, CLA, flipped)
    assert changed["verdict"] == "changed" and changed["headline"].startswith("Behaviour changed")
    cex = changed["counterexample"]
    assert cex and isinstance(cex["step"], int) and "ui_in" in cex and all(isinstance(v, int) for v in cex.values())
    assert "_seq" not in cex and "cycle" in changed["detail"]

    broken = _review(core, CLA, src.replace("assign uio_oe", "assign uio_oe ="))
    assert broken["verdict"] == "broken" and broken["headline"] == "Doesn't compile"
    assert "ERROR" in broken["detail"] and "/var/" not in broken["detail"] and "edited.v" in broken["detail"]

    renamed = _review(core, CLA, src.replace(f"module {CLA}", "module other"))
    assert renamed["verdict"] == "broken" and "module name" in renamed["detail"]
    narrowed = _review(core, CLA, src.replace("output wire [7:0] uo_out", "output wire [6:0] uo_out"))
    assert narrowed["verdict"] == "broken" and "port list" in narrowed["detail"]


@real
def test_review_edit_on_a_planted_chip(core):
    pid = f"{CLA}+t137"
    src = _decompiled(core, pid)
    assert "8'd137" in src
    assert _review(core, pid, src)["verdict"] == "same"
    cleaned = "\n".join(l.replace(" | (ui_in == 8'd137)", "") for l in src.split("\n"))
    assert cleaned != src
    out = _review(core, pid, cleaned)     # the trigger removed vs its own (planted) gates: behaviour changed
    assert out["verdict"] == "changed" and out["counterexample"]["ui_in"] == 137


@real
def test_review_edit_is_an_mcp_tool(core):
    async def go():
        async with Client(build_server(core)) as c:
            tools = {t.name: t for t in await c.list_tools()}
            r = await c.call_tool("review_edit", {"chip": CLA, "verilog": _decompiled(core, CLA)}, raise_on_error=False)
            return tools, r
    tools, r = asyncio.run(go())
    assert "review_edit" in tools and "Yosys" in tools["review_edit"].description
    assert "gates" in tools["review_edit"].description
    assert not r.is_error and result_payload(r)["verdict"] == "same"


# ------------------------------------------------------------------------------------------------ review fixes
def test_ideas_model_errors_are_502_even_when_valueerror(tmp_path):
    def bad_json(messages, info):     # the CLI model's json.loads failing raises a ValueError: still the model's fault
        import json
        json.loads("not json")
    r = _client(FunctionModel(bad_json, model_name="bad"), tmp_path).post(
        "/witness/ideas", json={"chip": "x", "line": 5, "lines": [5, 5], "verilog": VERILOG, "ask": ""})
    assert r.status_code == 502 and "JSONDecodeError" in r.json()["detail"]


def test_ideas_run_log_stays_inside_runs(tmp_path):
    runs = tmp_path / "a" / "runs"
    client = TestClient(create_witness_app(create_stub_app(), model=scripted([GOOD_SAME], {}), runs=runs))
    r = client.post("/witness/ideas", json={"chip": "../../../outside", "line": 5, "lines": [5, 5], "verilog": VERILOG})
    assert r.status_code == 200
    made = [p for p in tmp_path.rglob("*.jsonl")]
    assert made and all(p.parent == runs for p in made)


def test_ideas_block_comment_mentioning_module_is_kept_and_drops_are_counted(tmp_path):
    bc = {**GOOD_SAME, "title": "Comment", "replacement": ["    /* this module's AND */", "    wire t0 = ui_in[0] & ui_in[1];"]}
    out = _client(scripted([bc, HEADER], {}, reply="Three ways."), tmp_path).post(
        "/witness/ideas", json={"chip": "x", "line": 5, "lines": [5, 5], "verilog": VERILOG, "ask": ""}).json()
    assert [i["title"] for i in out["ideas"]] == ["Comment"]
    assert "1 more idea couldn't be applied here, so 1 is shown" in out["reply"]


def test_cli_model_from_the_default(monkeypatch):
    from basic_agent.cli_model import resolve
    monkeypatch.delenv("BASIC_LLM", raising=False)
    m = resolve(None, "cli:sonnet")       # BASIC_WITNESS_MODEL=cli:sonnet arrives as the default
    assert isinstance(m, FunctionModel) and m.model_name == "claude-cli:sonnet"
    assert resolve(None, "anthropic:x") == "anthropic:x"


A = "always @(posedge clk or negedge rst_n)"


def _never_same(out):
    assert out["verdict"] != "same" and "proven" not in out["headline"].lower(), out


@real
def test_review_edit_sees_clocking_changes(core):
    """equiv/sat ignore which clock (and which edge) drives a flip-flop: bASIC checks that separately."""
    for chip in (CLA, "tt_um_mac", f"{CLA}+t137"):
        src = _decompiled(core, chip)
        assert A in src
        for new in ("always @(negedge clk or negedge rst_n)", "always @(posedge ui_in[0] or negedge rst_n)",
                    "always @(posedge ena or negedge rst_n)"):
            out = _review(core, chip, src.replace(A, new, 1))
            _never_same(out)
            assert out["verdict"] == "changed" and "clock" in out["headline"], out


@real
def test_review_edit_sees_wiring_problems(core):
    src = _decompiled(core, CLA)
    zero = "assign uio_out = 8'b00000000;"
    cases = {
        "undriven": src.replace(zero, "wire u; assign uio_out = {7'b0, u};"),
        "typo": src.replace("{3'b000, acc0}", "{3'b000, acc_0}"),
        "self_loop": src.replace(zero, "wire loopy = ~loopy; assign uio_out = {7'b0, loopy};"),
        "loop": src.replace(zero, "wire a, b; assign a = ~b; assign b = a & ui_in[0]; assign uio_out = {7'b0, a};"),
        "two_drivers_logic": src.replace(zero, zero + " assign uio_out[0] = ~uio_in[0];"),
        "two_drivers_const": src.replace("assign uo_out = {3'b000, acc0};", "assign uo_out = {3'b000, acc0}; assign uo_out[7] = 1'b1;"),
        "latch": src.replace(zero, "reg [7:0] l; always @* if (ena) l = 8'd0; assign uio_out = l;"),
    }
    for k, t in cases.items():
        assert t != src, k
        out = _review(core, CLA, t)
        _never_same(out)
        assert out["verdict"] == "broken" and out["headline"] == "Wiring problem", (k, out)
    mac = _decompiled(core, "tt_um_mac")
    out = _review(core, "tt_um_mac", mac.replace("assign uio_oe = 8'b00000000;", "wire [7:0] oe_off; assign uio_oe = oe_off;"))
    assert out["headline"] == "Wiring problem", out


@real
def test_review_edit_refuses_undefined_and_simulation_only_code(core):
    src = _decompiled(core, CLA)
    for t in (src.replace("assign uio_oe = 8'b00000000;", "assign uio_oe = 8'bxxxxxxxx;"),
              src.replace("assign uio_out = 8'b00000000;", "assign uio_out = 8'bzzzzzzzz;"),
              src.replace("{3'b000, acc0}", "{3'bxxx, acc0}")):
        out = _review(core, CLA, t)
        _never_same(out)
        assert out["headline"].startswith("Undefined value"), out
    last = "assign uo_out = {3'b000, acc0};"
    for t in (src.replace(last, f"`ifdef SYNTHESIS\n{last}\n`else\nassign uo_out = 8'hff;\n`endif"),
              src.replace(last, last + "\n// synopsys translate_off\nassign uio_out = 8'hff;\n// synopsys translate_on"),
              src.replace("reg [4:0] acc0;", "reg [4:0] acc0;\n initial acc0 = 5'd7;"),
              src.replace(last, '`include "/etc/passwd"\n' + last)):
        out = _review(core, CLA, t)
        _never_same(out)
        assert out["headline"].startswith("Can't be checked"), out
        assert "root:" not in out["detail"]


@real
def test_review_edit_headers_and_reset_wording(core):
    src = _decompiled(core, CLA)
    hdr = src[:src.index(");") + 2]
    nonansi = src.replace(hdr, f"module {CLA} (clk, ena, rst_n, ui_in, uio_in, uio_oe, uio_out, uo_out);\n"
                               "    input clk, ena, rst_n; input [7:0] ui_in, uio_in; output [7:0] uio_oe, uio_out, uo_out;")
    assert _review(core, CLA, nonansi)["verdict"] == "same"
    out = _review(core, CLA, src.replace(f"module {CLA}", "module other"))
    assert out["headline"] == "Ports or module name changed"
    out = _review(core, CLA, src.replace("acc0 <= 5'd0;", "acc0 <= 5'd1;"))
    assert out["verdict"] == "changed" and "while reset is held" in out["headline"], out


@real
def test_review_edit_with_a_baseline_judges_this_edit_alone(core):
    src = _decompiled(core, CLA)
    kept = src.replace("ui_in[3:0] + ui_in[7:4]", "ui_in[3:0] - ui_in[7:4]")      # a deliberate change, already kept
    comment = kept.replace("assign uio_oe", "// output enables all off\n    assign uio_oe")
    r = TestClient(core).post(f"/api/chips/{CLA}/review-edit", json={"verilog": comment, "baseline": kept}).json()
    assert r["verdict"] == "changed"                       # vs the chip: still the kept change
    assert r["this_edit"]["verdict"] == "same" and r["this_edit"]["headline"] == "Same as before this edit (proven)"
    more = comment.replace("{3'b000, acc0}", "{3'b001, acc0}")
    r = TestClient(core).post(f"/api/chips/{CLA}/review-edit", json={"verilog": more, "baseline": kept}).json()
    assert r["this_edit"]["verdict"] == "changed" and r["this_edit"]["counterexample"]
    assert "this_edit" not in _review(core, CLA, comment)


@real
def test_review_budget_and_kept_counterexample(core, monkeypatch):
    import subprocess
    import time
    from basic.core import chip as C
    from basic.core import service as S
    v = S.get(CLA)
    flipped = v.result["decompiled"]["verilog"].replace("ui_in[3:0] + ui_in[7:4]", "ui_in[3:0] - ui_in[7:4]")
    # a deadline already spent: no Yosys run at all, and it says so
    check, detail, _ = C.author_check(v.base, {"files": {"e.v": flipped}}, v.nl.top, want_cex=True, deadline=time.time())
    assert check == "unavailable" and "time" in detail
    # a timeout in the optional "shorter trace" re-run keeps the difference already found
    real_run = subprocess.run

    def slow_short(cmd, *a, **k):
        if any(str(x).endswith("short.ys") for x in cmd):
            raise subprocess.TimeoutExpired(cmd, 1)
        return real_run(cmd, *a, **k)
    monkeypatch.setattr(C.subprocess, "run", slow_short)
    check, _, cex = C.author_check(v.base, {"files": {"e.v": flipped}}, v.nl.top, want_cex=True, first_diff=True)
    assert check == "failed" and cex and cex["step"] >= 1
