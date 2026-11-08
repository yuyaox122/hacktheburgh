"""Witness loop tests: claim syntax, the stub checker, and the region agent with a scripted model (offline)."""

from __future__ import annotations

import asyncio
import json
import os
import pytest
from fastmcp import Client
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from decap_agent.claim_expr import ClaimSyntaxError, Evaluator, literals, parse
from decap_agent.server import build_server
from decap_agent.stub_core import create_stub_app
from decap_agent.transcript import Transcript, load, result_payload
from decap_agent.witness import LOOK_TOOLS, explain_region

CHIP = "stub_counter8"
NEXT = {"cone_of": "next(count)"}
HIT = {"cone_of": "uo_out[1]"}


def run(coro):
    return asyncio.run(coro)


async def _claim(server, claim, scope=NEXT, label="", check=True):
    async with Client(server) as c:
        r = await c.call_tool("propose_claim", {"chip": CHIP, "scope": scope, "claim": claim, "label": label,
                                                "check": check}, raise_on_error=False)
        return result_payload(r)


def test_verilog_widths_unsized_one_does_not_wrap():
    ev = Evaluator({"count": 8, "en": 1})
    env = {("sig", "count"): 255, ("sig", "en"): 1, ("next", "count"): 0}
    assert not ev.holds(parse("next(count) == count + 1 when en"), env)
    assert ev.holds(parse("next(count) == count + 8'd1 when en"), env)
    assert literals(parse("uo_out[1] -> count[7] & count[6]")) == 3


@pytest.mark.parametrize("bad", ["hit ===", "", "count[", "next(3)"])
def test_bad_syntax_is_an_error(bad):
    with pytest.raises(ClaimSyntaxError):
        parse(bad)


def test_stub_checker_verdicts():
    server = build_server(create_stub_app())
    wrong = run(_claim(server, "next(count) == count + 1 when en", label="incrementer"))
    assert wrong["verdict"] == "refuted" and wrong["counterexample"] == {"count": 255, "en": 1, "next(count)": 0}
    partial = run(_claim(server, "next(count) == count + 8'd1 when en", label="incrementer"))
    assert partial["verdict"] == "partial" and not partial["credit"]["cleared"]      # silent when en = 0
    right = run(_claim(server, "next(count) == (en ? count + 8'd1 : count)", label="counter"))
    assert right["verdict"] == "proven" and right["credit"]["cleared"] and right["credit"]["complete"]
    assert run(_claim(server, "next(count) == count when !en"))["verdict"] == "partial"
    assert run(_claim(server, "count == count"))["verdict"] == "tautology"
    assert run(_claim(server, "hit == 1"))["verdict"] == "unparseable"          # internal nets are not nameable
    long = "uo_out[1] == (count[7] & count[6] & !count[5] & !count[4] & count[3] & !count[2] & !count[1] & !count[0])"
    restated = run(_claim(server, long, scope=HIT))
    assert restated["verdict"] == "restated" and not restated["credit"]["cleared"]
    short = run(_claim(server, "uo_out[1] == (count == 8'd200)", scope=HIT))
    assert short["verdict"] == "proven" and short["credit"]["cleared"]


def test_dry_run_only_renders_english():
    server = build_server(create_stub_app())
    out = run(_claim(server, "uo_out[1] == (count == 8'd200)", scope=HIT, check=False))
    assert out["verdict"] == "unchecked" and out["english"] and "counterexample" not in out


def test_probe_finds_the_rare_row():
    server = build_server(create_stub_app())

    async def go():
        async with Client(server) as c:
            return result_payload(await c.call_tool("probe_truth", {"chip": CHIP, "scope": HIT}))
    out = run(go())
    assert out["exhaustive"] and out["stats"]["uo_out[1]"]["rows_where_1"] == [{"count": 200}]


def scripted(claims: list[dict], seen: dict):
    """A model that probes once (holding en = 1), then returns the next scripted claim on each run."""
    state = {"i": 0}

    def fn(messages, info: AgentInfo) -> ModelResponse:
        seen.setdefault("tools", set()).update(t.name for t in info.function_tools)
        seen.setdefault("prompts", []).append(" ".join(str(p) for m in messages for p in getattr(m, "parts", [])))
        last = messages[-1].parts
        if not any(isinstance(p, ToolReturnPart) and p.tool_name == "probe_truth" for p in last):
            return ModelResponse(parts=[ToolCallPart("probe_truth", {"chip": CHIP, "scope": NEXT, "fix": {"en": 1}})])
        claim = claims[min(state["i"], len(claims) - 1)]
        state["i"] += 1
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, claim)])
    return FunctionModel(fn, model_name="scripted")


WRONG = {"claim": "next(count) == count + 1 when en", "label": "incrementer", "gist": "counts up"}
RIGHT = {"claim": "next(count) == (en ? count + 8'd1 : count)", "label": "counter", "gist": "counts up while en, wrapping"}


def test_region_agent_refines_after_counterexample(tmp_path):
    t = Transcript(tmp_path / "w.jsonl")
    server = build_server(create_stub_app(), transcript=t)
    seen: dict = {}
    out = run(explain_region(CHIP, NEXT, server, model=scripted([WRONG, RIGHT], seen), transcript=t,
                             cache_dir=tmp_path / "cache"))
    assert [a["verdict"] for a in out["attempts"]] == ["refuted", "proven"]
    assert out["first_draft"] == "refuted" and out["final"] == "proven" and out["explained"]
    assert seen["tools"] <= LOOK_TOOLS and "propose_claim" not in seen["tools"]   # the LLM can only look
    assert any("REFUTED" in p and '"count": 255' in p for p in seen["prompts"])   # counterexample fed back
    claims = [e for e in load(t.path) if e["kind"] == "claim"]
    assert [c["first_draft"] for c in claims] == [True, False] and claims[0]["cells"]


def test_cache_replays_without_the_model(tmp_path):
    server = build_server(create_stub_app())
    run(explain_region(CHIP, NEXT, server, model=scripted([WRONG, RIGHT], {}), cache_dir=tmp_path))

    def boom(messages, info):
        raise AssertionError("model called on a cached region")
    t = Transcript(tmp_path / "again.jsonl")
    out = run(explain_region(CHIP, NEXT, server, model=FunctionModel(boom, model_name="scripted"), transcript=t,
                             cache_dir=tmp_path))
    assert out["cached"] and out["final"] == "proven"
    assert all(e["cached"] for e in load(t.path) if e["kind"] == "claim")


def test_refinements_are_capped():
    server = build_server(create_stub_app())
    out = run(explain_region(CHIP, NEXT, server, model=scripted([WRONG], {}), max_refine=2))
    assert len(out["attempts"]) == 3 and out["final"] == "refuted" and not out["explained"]


def test_probe_fix_holds_inputs():
    server = build_server(create_stub_app())

    async def go(fix):
        async with Client(server) as c:
            return await c.call_tool("probe_truth", {"chip": CHIP, "scope": NEXT, "fix": fix}, raise_on_error=False)
    held = result_payload(run(go({"en": 0})))
    assert held["fixed"] == {"en": 0} and all(r["out"]["next(count)"] == r["in"]["count"] for r in held["samples"])
    assert run(go({"nope": 1})).is_error


def test_formaliser_validates_and_renders(tmp_path):
    from decap_agent.formalise import equivalent, formalise
    outputs = iter([{"claim": "uo_out[1] == (count == 8'd200", "assumptions": []},          # broken: retried once
                    {"claim": "uo_out[1] == (count == 8'd200)", "assumptions": ["'the compare LED' = uo_out[1]"]}])

    def fn(messages, info):
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, next(outputs))])
    t = Transcript(tmp_path / "f.jsonl")
    out = run(formalise("the compare LED is on exactly when the count is 200", server=build_server(create_stub_app()),
                        chip=CHIP, scope=HIT, model=FunctionModel(fn), transcript=t, check=True))
    assert out["error"] is None and out["english"] and out["verdict"]["verdict"] == "proven"
    assert [e["kind"] for e in load(t.path)][-1] == "formalise"
    assert equivalent("(ui_in[1] & ui_in[2]) -> uo_out[3]", "!(ui_in[1] && ui_in[2]) || uo_out[3]")
    assert not equivalent("uo_out == ui_in + 1", "uo_out == ui_in + 8'd1")     # widths matter


REAL = os.environ.get("DECAP_REAL_CORE_ROOT")


@pytest.mark.skipif(not REAL, reason="set DECAP_REAL_CORE_ROOT to a checkout of main with decap/data fetched")
def test_real_chip_checker_catches_a_planted_bug(monkeypatch):
    monkeypatch.syspath_prepend(REAL)
    monkeypatch.chdir(REAL)
    from decap_agent.core_witness import app
    server = build_server(app)
    alarm = {"cone_of": "uio_out[7]"}

    async def go(claim):
        async with Client(server) as c:
            return result_payload(await c.call_tool("propose_claim", {"chip": "tt_um_kapilan_alarm", "scope": alarm,
                                                                      "claim": claim}, raise_on_error=False))
    good = run(go("(uio_out[7] == bit0) && (next(bit0) == (bit0 | ({count0, count1} == {uio_in[2:0], ui_in})))"))
    # the core's own checker (core/claims.py, Yosys) answers once it exists; the fallback only fills the gap
    assert (good["verdict"], good["source"]) in {("proven", "yosys"), ("tested", "fallback-sim")} and good["credit"]["complete"]
    bad = run(go("next(bit0) == (bit0 | ({count0, count1} == {uio_in[2:0], ui_in[7:1], 1'b0}))"))
    assert bad["verdict"] == "refuted" and "count0" in bad["counterexample"]
    assert run(go("next(bit0) == 1'b1 when bit0"))["verdict"] == "partial"


def test_cli_backend_is_opt_in(monkeypatch):
    import shutil
    from decap_agent.cli_model import resolve
    monkeypatch.delenv("DECAP_LLM", raising=False)
    assert resolve(None, "anthropic:x") == "anthropic:x"            # never switches on by itself
    if shutil.which("claude"):
        assert resolve("cli:haiku", "anthropic:x").model_name == "claude-cli:haiku"
        monkeypatch.setenv("DECAP_LLM", "cli")
        assert resolve(None, "anthropic:x").model_name.startswith("claude-cli:")


@pytest.mark.skipif(not REAL, reason="set DECAP_REAL_CORE_ROOT to a checkout of main with decap/data fetched")
def test_real_chip_claim_must_cover_every_region_output(monkeypatch):
    monkeypatch.syspath_prepend(REAL)
    monkeypatch.chdir(REAL)
    from decap_agent.core_witness import ClaimIn, check_claim
    alarm = {"cone_of": "uio_out[7]"}
    easy = check_claim("tt_um_kapilan_alarm", ClaimIn(claim="uio_out[7] == bit0", scope=alarm))
    assert easy["verdict"] == "partial" and "next(bit0)" in easy["credit"]["reason"]   # skipped the alarm logic
    full = check_claim("tt_um_kapilan_alarm", ClaimIn(
        claim="(uio_out[7] == bit0) && (next(bit0) == (bit0 | ({count0, count1[4:0]} == {uio_in[2:0], ui_in})))", scope=alarm))
    assert full["verdict"] == "tested" and full["credit"]["cleared"]


def test_bench_scores_first_drafts_with_a_clustered_ci(tmp_path):
    from decap_agent.bench import run_bench

    def fn(messages, info):   # right on the counter region at once; wrong first on the compare region
        text = " ".join(str(p) for m in messages for p in getattr(m, "parts", []))
        if "next(count)" in text.split("Region outputs:")[-1].split("\n")[0]:
            claim = RIGHT
        elif "REFUTED" in text:
            claim = {"claim": "uo_out[1] == (count == 8'd200)", "label": "comparator"}
        else:
            claim = {"claim": "uo_out[1] == (count == 8'd100)", "label": "comparator"}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, claim)])
    res = run(run_bench(build_server(create_stub_app()), [CHIP], model=FunctionModel(fn, model_name="scripted"),
                        max_regions=3, out_path=tmp_path / "b.json", log=lambda m: None))
    s = res["summary"]
    assert s["regions"] == 3 and s["claims"] == 3
    assert s["first_draft"] == {"refuted_semantic": 1, "true_credited": 2}
    assert s["error_rate_semantic"] == round(1 / 3, 4) and s["error_ci95_clustered"] is not None
    assert s["explained_regions"] == 3 and s["headline"] == "explained coverage"     # n < 30
    assert (tmp_path / "b.json").exists()


def test_bench_refuses_held_out_split_without_freeze():
    from decap_agent import cli
    assert cli.main(["--no-transcript", "bench", "--split", "test"]) == 2


def test_transcript_lifts_touched_lines(tmp_path):
    t = Transcript(tmp_path / "t.jsonl")
    server = build_server(create_stub_app(), transcript=t)

    async def go():
        async with Client(server) as c:
            await c.call_tool("get_region", {"chip": CHIP, "scope": NEXT})
    run(go())
    ev = [e for e in load(t.path) if e["kind"] == "tool"][-1]
    assert "lines" in ev and ev["cells"]


def test_timeline_renders_only_checked_runs(tmp_path):
    from decap_agent.timeline import render, runs_from
    ev = [
        {"kind": "claim", "chip": "tt_um_x", "scope": {"cone_of": "b3"}, "attempt": 0, "first_draft": True, "source": "yosys",
         "claim": "uo_out[0] == ui_in[0]", "verdict": "refuted", "truth": "refuted", "refute_kind": "semantic",
         "counterexample": {"ui_in": 137, "q_c4": 1, "uo_out[0]": 0}},
        {"kind": "claim", "chip": "tt_um_x", "scope": {"cone_of": "b3"}, "attempt": 1, "first_draft": False, "source": "yosys",
         "claim": "uo_out[0] == (ui_in == 8'd137)", "verdict": "proven", "truth": "proven", "checker_s": 0.07,
         "credit": {"cleared": True, "literals": 2, "cone_literals": 30}, "english": "Bit 0 of uo_out equals ui_in equals 137."},
        {"kind": "claim", "chip": "tt_um_y", "scope": {"cone_of": "b1"}, "attempt": 0, "first_draft": True,
         "source": "fallback-sim", "claim": "uo_out == 0", "verdict": "tested"},
    ]
    assert len(runs_from(ev)) == 1 and len(runs_from(ev, include_fallback=True)) == 2
    page = render(ev)
    assert page.count('class="att focus"') == 1 and "uo_out[0] == (ui_in == 8&#x27;d137)" in page
    assert "Breaks at" in page and "10001001" in page and "+1 flip-flop bits" in page
    assert "tt_um_y" not in page and "<title>Witness</title>" in page


def test_witness_api_streams_a_region_run(tmp_path):
    from fastapi.testclient import TestClient
    from decap_agent.witness_api import create_witness_app
    app = create_witness_app(create_stub_app(), model=scripted([WRONG, RIGHT], {}), runs=tmp_path, readings=False)
    client = TestClient(app)
    with client.stream("POST", "/witness/explain", json={"chip": CHIP, "scope": NEXT}) as r:
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    kinds = [e["kind"] for e in events]
    assert kinds[-1] == "done" and kinds.count("claim") == 2 and "tool" in kinds
    assert events[-1]["final"] == "proven" and events[-1]["first_draft"] == "refuted"
    assert all("result" not in e for e in events if e["kind"] == "tool")           # slim stream
    assert client.get("/witness/cache", params={"chip": CHIP}).json()[0]["final"] == "proven"


def test_witness_api_formalise_confirm_step(tmp_path):
    from fastapi.testclient import TestClient
    from decap_agent.witness_api import create_witness_app

    def fn(messages, info):
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {"claim": "uo_out[1] == (count == 8'd200)"})])
    client = TestClient(create_witness_app(create_stub_app(), model=FunctionModel(fn), runs=tmp_path))
    out = client.post("/witness/formalise", json={"chip": CHIP, "sentence": "LED 1 is on exactly when the count is 200",
                                                   "scope": HIT}).json()
    assert out["claim"] == "uo_out[1] == (count == 8'd200)" and out["english"] and "verdict" not in out


def test_claim_parser_is_tolerant_of_loose_model_output():
    from decap_agent.claim_expr import Evaluator, parse, unparse
    # comma -> &&, bit-select after a sub-expression, trailing prose stripped
    assert unparse(parse("next(a) == 1, next(b) == 0")) == "(next(a) == 1) && (next(b) == 0)"
    assert unparse(parse("uo_out == (ui_in + uio_in)[7:1]")) == "uo_out == ((ui_in + uio_in)[7:1])"
    assert unparse(parse("uo_out[7:1] == (ui_in + uio_in)[7:1]  (i.e. out = in + 1)")) == "uo_out[7:1] == ((ui_in + uio_in)[7:1])"
    # the slice is a real shift+mask, not a width truncation
    E = Evaluator({"a": 8, "b": 8})
    assert E.holds(parse("(a + b)[7:1] == 3'd4"), {("sig", "a"): 0, ("sig", "b"): 9})      # sum 9 -> slice 4
    assert not E.holds(parse("(a + b)[7:1] == 3'd4"), {("sig", "a"): 0, ("sig", "b"): 10})  # sum 10 -> slice 5
    # a trailing == expression must NOT be stripped as prose
    assert unparse(parse("uo_out[1] == (count == 8'd200)")) == "uo_out[1] == (count == 8'd200)"


def test_load_secrets_strips_inline_comments_and_quotes(tmp_path, monkeypatch):
    from decap_agent.cli import load_secrets
    f = tmp_path / "s.env"
    f.write_text('DEEPSEEK_API_KEY=sk-abc123\nDECAP_GUIDE_CLI=1  # keep the claude CLI on\nQUOTED="val"  # trailing\n#FULL=comment\n')
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DECAP_GUIDE_CLI", raising=False)
    monkeypatch.delenv("QUOTED", raising=False)
    loaded = load_secrets(str(f))
    assert "DEEPSEEK_API_KEY" in loaded and os.environ["DEEPSEEK_API_KEY"] == "sk-abc123"
    assert os.environ["DECAP_GUIDE_CLI"] == "1" and os.environ["QUOTED"] == "val"
    assert "FULL" not in os.environ and "FULL" not in loaded
