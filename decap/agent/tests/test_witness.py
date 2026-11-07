"""Witness loop tests: claim syntax, the stub checker, and the region agent with a scripted model (offline)."""

from __future__ import annotations

import asyncio
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
