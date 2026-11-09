"""Witness timeline: a static page of real region-agent runs (DimOS-style look), built from transcripts.

    decap timeline runs/bench-dev-full.jsonl [--bench runs/bench-dev-full.json] [--ablation runs/ablation.json]
                   -o decap/agent/prototypes/witness-timeline.html

Every claim, verdict, counterexample and timing on the page comes from a transcript event (kind="claim") written by
`decap explain` / `decap bench`. Only Yosys-checked runs are shown unless --include-fallback.
"""

from __future__ import annotations

import html
import json
import math
from datetime import datetime, timezone
from pathlib import Path

VERDICT = {  # verdict -> (label, css class)
    "proven": ("Proven", "ok"), "inductive": ("Proven from reset", "ok"), "tested": ("Tested only", "info"),
    "refuted": ("Refuted", "bad"), "partial": ("True, incomplete", "warn"), "restated": ("True, no shorter", "mute"),
    "bounded": ("Holds 30 cycles only", "warn"), "tautology": ("Tautology", "mute"), "unparseable": ("Didn't parse", "mute"),
    "timeout": ("Timed out", "mute"), "no_claim": ("No claim", "mute"),
}
SAMPLES = [(8, "1,177"), (16, "301,802"), (24, "77,261,933"), (32, "19,779,055,339"), (64, "8.5 × 10¹⁹")]


def e(x) -> str:
    return html.escape(str(x), quote=True)


def runs_from(events: list[dict], include_fallback: bool = False) -> list[list[dict]]:
    """Group kind='claim' events into region runs (attempt 0 starts a run); drop cache replays and duplicates."""
    runs, seen = [], set()
    for ev in events:
        if ev.get("kind") != "claim" or ev.get("cached"):
            continue
        if not include_fallback and ev.get("source") not in ("yosys",):
            continue
        if ev.get("attempt") == 0 or not runs:
            runs.append([])
        runs[-1].append(ev)
    out = []
    for r in runs:
        key = json.dumps([(a.get("chip"), a.get("scope"), a.get("claim")) for a in r], sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _english(text: str) -> str:
    if len(text) <= 220:
        return f'<p class="eng">{e(text)}</p>'
    return f'<details class="eng"><summary>Read it in English</summary><p>{e(text)}</p></details>'


def _cex(c: dict | None) -> str:
    if not c:
        return ""
    shown = {k: v for k, v in c.items() if not k.startswith("q_")}
    hidden = len(c) - len(shown)
    chips = []
    for k, v in shown.items():
        bits = f'<span class="bin">{int(v):08b}</span>' if isinstance(v, int) and v > 1 and k.split("(")[-1].startswith(("ui_in", "uio_in", "uo_out", "uio_out")) else ""
        chips.append(f'<span class="cx"><span class="cx-k">{e(k)}</span><span class="cx-v">{e(v)}</span>{bits}</span>')
    more = f'<span class="cx more">+{hidden} flip-flop bits</span>' if hidden else ""
    return f'<div class="cex"><span class="lbl">Breaks at</span>{"".join(chips)}{more}</div>'


def _attempt(a: dict) -> str:
    v = a.get("verdict") or "no_claim"
    label, cls = VERDICT.get(v, (v, "mute"))
    sub = ""
    if v == "refuted":
        sub = "also fails from reset" if (a.get("refute_kind") or "semantic") == "semantic" else "fails only off-reset"
    credit = a.get("credit") or {}
    focus = " focus" if v in ("proven", "inductive") and credit.get("cleared") else ""
    tag = "First draft" if a.get("first_draft") else f"Refine {a.get('attempt')}"
    ratio = ""
    if credit.get("cleared") and credit.get("literals") and credit.get("cone_literals"):
        ratio = f'<span class="ratio">{e(credit["literals"])} vs {e(credit["cone_literals"])} literals</span>'
    t = a.get("checker_s")
    timing = f'Yosys {t:.2f} s' if isinstance(t, (int, float)) else ""
    reason = credit.get("reason") if not focus and credit.get("reason") else ""
    return f'''
      <article class="att{focus}">
        <header class="att-h"><span class="tag">{e(tag)}</span><span class="pill {cls}">{e(label)}</span></header>
        <pre class="claim"><code>{e(a.get("claim") or "(no claim)")}</code></pre>
        {_english(a.get("english")) if a.get("english") and v not in ("unparseable",) else ""}
        {f'<p class="why">{e(a.get("error"))}</p>' if v == "unparseable" and a.get("error") else ""}
        {_cex(a.get("counterexample")) if v == "refuted" else ""}
        <footer class="att-f">{f'<span>{e(sub)}</span>' if sub else ""}{ratio}{f'<span>{e(reason)}</span>' if reason else ""}<span class="t">{e(timing)}</span></footer>
      </article>'''


def _run(r: list[dict], region_cells: dict) -> str:
    a0 = r[0]
    scope = a0.get("scope") or {}
    region = scope.get("cone_of") or ", ".join(scope.get("cells", [])[:3]) or "whole chip"
    cells = region_cells.get((a0.get("chip"), region))
    lits = next(((a.get("credit") or {}).get("cone_literals") for a in r if (a.get("credit") or {}).get("cone_literals")), None)
    meta = " · ".join(x for x in [f"{cells} cells" if cells else "", f"{lits} literals in the decompiled code" if lits else "",
                                    a0.get("model", "")] if x)
    final = r[-1].get("verdict")
    status = "explained" if final in ("proven", "inductive") and (r[-1].get("credit") or {}).get("cleared") else "open"
    arrows = '<span class="arrow" aria-hidden="true"></span>'.join(_attempt(a) for a in r)
    return f'''
    <section class="run {status}">
      <div class="run-h"><h3><span class="chip">{e(a0.get("chip", "").removeprefix("tt_um_"))}</span><span class="region">region {e(region)}</span></h3>
        <p class="meta">{e(meta)}</p></div>
      <div class="track">{arrows}</div>
    </section>'''


def _ablation(ab: dict | None) -> str:
    if not ab:
        return ""
    rows = [x for x in ab.get("rows", []) if x.get("yosys") == "refuted" and x.get("sim_agrees_at_yosys_cex") is True]
    def cell(v):
        passed = v != "refuted"
        return f'<td class="{"said-yes" if passed else "said-no"}">{"passed" if passed else "refuted"}</td>'
    body = "".join(f'<tr><td class="mono">{e(x["chip"].removeprefix("tt_um_"))}</td><td class="mono clip" title="{e(x["claim"])}">{e(x["claim"][:150] + ("…" if len(x["claim"]) > 150 else ""))}</td>'
                   f'<td class="said-no">refuted</td>{cell(x.get("random1024"))}{cell(x.get("biased4096"))}</tr>' for x in rows)
    mism = len(ab.get("model_mismatch", []))
    return f'''
      <div class="tbl-wrap"><table class="abl">
        <thead><tr><th>Chip</th><th>Agent's claim</th><th>Yosys</th><th>Random ×1024</th><th>Biased ×4096</th></tr></thead>
        <tbody>{body or '<tr><td colspan="5">No refuted claims in these runs yet.</td></tr>'}</tbody>
      </table></div>
      <p class="note">Every claim Yosys refuted is replayed in the simulator at Yosys's own counterexample first. Only claims
      where both agree it is false are compared{f"; {mism} excluded as a simulator mismatch (a cut ring-oscillator loop)" if mism else ""}.
      Simulation contradicted a proof {e(ab.get("random1024_disagrees_with_proof", 0))} times.</p>'''


def _samples() -> str:
    rows = []
    for k, label in SAMPLES:
        n = math.log(0.01) / math.log1p(-(2.0 ** -k))   # log1p: 1 - 2^-64 rounds to 1.0
        w = math.log10(n) / 20 * 100
        rows.append(f'<div class="bar-row"><span class="bk">{k}-bit trigger</span><span class="bar" style="--w:{w:.1f}%"></span>'
                    f'<span class="bv">{e(label)}</span></div>')
    return "".join(rows)


def render(events: list[dict], bench: dict | None = None, ablation: dict | None = None, include_fallback: bool = False) -> str:
    runs = runs_from(events, include_fallback)
    region_cells = {}
    for row in (bench or {}).get("rows", []):
        region_cells[(row["chip"], row["block"])] = row.get("cells")
    firsts = [r[0] for r in runs]
    def count(vs): return sum(1 for a in firsts if a.get("verdict") in vs)
    explained = sum(1 for r in runs if r[-1].get("verdict") in ("proven", "inductive") and (r[-1].get("credit") or {}).get("cleared"))
    comp = [(a.get("credit") or {}) for r in runs for a in r[-1:] if (a.get("credit") or {}).get("cleared")]
    ratios = sorted(c["cone_literals"] / c["literals"] for c in comp if c.get("literals"))
    med = f"{ratios[len(ratios) // 2]:.1f}×" if ratios else "–"
    passed = ""
    if ablation:
        passed = str(ablation.get("random1024_passed_false_claims", "–"))
    chips = sorted({a.get("chip") for a in firsts})
    stats = [("Region runs", len(runs)), ("First drafts proven", count({"proven", "inductive"})),
             ("First drafts refuted", count({"refuted"})), ("Explained after refining", explained),
             ("Median compression", med)] + ([("False claims random testing passed", passed)] if passed else [])
    stat_html = "".join(f'<div class="stat"><span class="sv">{e(v)}</span><span class="sl">{e(k)}</span></div>' for k, v in stats)
    when = datetime.now(timezone.utc).strftime("%a %d %b %Y, %H:%M UTC")
    body = "".join(_run(r, region_cells) for r in runs) or '<p class="note">No Yosys-checked runs in these transcripts yet.</p>'
    return TEMPLATE.format(stats=stat_html, runs=body, ablation=_ablation(ablation), samples=_samples(), when=e(when),
                           nchips=len(chips), nruns=len(runs))


def write(paths: list[Path], out: Path, bench: Path | None = None, ablation: Path | None = None,
          include_fallback: bool = False) -> Path:
    events = []
    for p in paths:
        events += [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
    b = json.loads(bench.read_text()) if bench and bench.exists() else None
    ab = json.loads(ablation.read_text()) if ablation and ablation.exists() else None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(events, b, ab, include_fallback))
    return out


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Witness</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap">
<style>
/* Layout: one dark instrument panel. Each region run is a left-to-right trace of attempts, stacked like probe channels;
   the only colour that isn't a verdict is the spectral ring around a claim Yosys proved. Single dark world (DimOS). */
:root {{
  color-scheme: dark;
  --bg: #07080b;
  --panel: #0d0f14;
  --line: #1c2029;
  --ink: #e8ebf1;
  --ink-2: #9aa3b4;
  --ink-3: #636c7d;
  --ok: #4fe3a0;
  --bad: #ff5d73;
  --warn: #f3b544;
  --info: #6fb6ff;
  --spectrum: conic-gradient(from var(--spin, 0deg), #ff3d6e, #ffb03b, #e6ff47, #3dffa1, #3dd6ff, #7b5cff, #ff3d6e);
  --display: "Geist", "Helvetica Neue", Arial, sans-serif;
  --body: "Geist", "Helvetica Neue", Arial, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, "SF Mono", Menlo, monospace;
}}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; }}
body {{
  background: var(--bg); color: var(--ink); font: 15px/1.55 var(--body);
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px));
  padding-block: 0 64px;
  background-image: linear-gradient(var(--line) 1px, transparent 1px), linear-gradient(90deg, var(--line) 1px, transparent 1px);
  background-size: 48px 48px; background-position: -1px -1px;
  background-attachment: fixed;
}}
body::before {{ content: ""; position: fixed; inset: 0; pointer-events: none;
  background: radial-gradient(1200px 600px at 70% -10%, rgba(123,92,255,.10), transparent 60%),
              radial-gradient(900px 500px at 0% 110%, rgba(61,214,255,.06), transparent 60%), linear-gradient(transparent, var(--bg) 85%); }}
.wrap {{ position: relative; max-width: 1180px; margin: 0 auto; display: grid; gap: 56px; }}
.hero {{ padding-top: 64px; display: grid; gap: 20px; }}
.eyebrow {{ font: 500 12px/1 var(--mono); letter-spacing: .14em; text-transform: uppercase; color: var(--ink-3); }}
h1 {{ margin: 0; font: 700 clamp(56px, 11vw, 132px)/.9 var(--display); letter-spacing: -.045em;
  text-shadow: -2px 0 rgba(255,45,100,.55), 2px 0 rgba(45,200,255,.55); }}
.thesis {{ margin: 0; max-width: 62ch; font-size: clamp(17px, 2vw, 20px); color: var(--ink-2); text-wrap: balance; }}
.thesis b {{ color: var(--ink); font-weight: 600; }}
.stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1px; background: var(--line);
  border: 1px solid var(--line); }}
.stat {{ background: var(--panel); padding: 16px 18px; display: grid; gap: 6px; }}
.sv {{ font: 600 28px/1 var(--mono); font-variant-numeric: tabular-nums; }}
.sl {{ font-size: 12px; color: var(--ink-3); letter-spacing: .02em; }}
h2 {{ margin: 0 0 6px; font: 600 26px/1.15 var(--display); letter-spacing: -.02em; text-wrap: balance; }}
.sec-h {{ display: grid; gap: 6px; max-width: 70ch; }}
.sec-h p {{ margin: 0; color: var(--ink-2); }}
.steps {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: var(--line); border: 1px solid var(--line); }}
.step {{ background: var(--panel); padding: 16px 18px; display: grid; gap: 6px; align-content: start; }}
.step .n {{ font: 500 11px/1 var(--mono); color: var(--ink-3); letter-spacing: .12em; }}
.step b {{ font-weight: 600; }}
.step span:last-child {{ color: var(--ink-2); font-size: 14px; }}
.runs {{ display: grid; gap: 28px; }}
.run {{ display: grid; gap: 12px; }}
.run-h {{ display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px 16px;
  border-bottom: 1px solid var(--line); padding-bottom: 8px; }}
.run-h h3 {{ margin: 0; display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: baseline; font-weight: 600; font-size: 17px; }}
.chip {{ font-family: var(--mono); }}
.region {{ font: 500 12px/1 var(--mono); color: var(--ink-3); letter-spacing: .06em; text-transform: uppercase; }}
.meta {{ margin: 0; font: 12px/1.4 var(--mono); color: var(--ink-3); }}
.track {{ display: flex; gap: 0; align-items: stretch; overflow-x: auto; padding: 4px 4px 8px; }}
.arrow {{ flex: 0 0 28px; align-self: center; height: 1px; background: var(--ink-3); position: relative; }}
.arrow::after {{ content: ""; position: absolute; right: 0; top: -4px; border: 4.5px solid transparent; border-left: 7px solid var(--ink-3); border-right: 0; }}
.att {{ flex: 0 1 380px; min-width: 260px; background: var(--panel); border: 1px solid var(--line); padding: 14px 16px;
  display: grid; gap: 10px; align-content: start; position: relative; }}
.att.focus {{ border-color: transparent; }}
.att.focus::before {{ content: ""; position: absolute; inset: -1px; padding: 1.5px; background: var(--spectrum);
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite: xor;
  mask: linear-gradient(#000 0 0) content-box exclude, linear-gradient(#000 0 0); pointer-events: none; }}
.att.focus::after {{ content: ""; position: absolute; inset: 8px; background: var(--spectrum); filter: blur(28px); opacity: .16;
  z-index: -1; }}
@property --spin {{ syntax: "<angle>"; inherits: true; initial-value: 0deg; }}
@media (prefers-reduced-motion: no-preference) {{ .att.focus {{ animation: spin 6s linear infinite; }} }}
@keyframes spin {{ to {{ --spin: 360deg; }} }}
.att-h {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; }}
.tag {{ font: 500 11px/1 var(--mono); letter-spacing: .12em; text-transform: uppercase; color: var(--ink-3); }}
.pill {{ font: 600 11px/1 var(--mono); letter-spacing: .06em; text-transform: uppercase; padding: 5px 8px; border: 1px solid currentColor; white-space: nowrap; }}
.pill.ok {{ color: var(--ok); }} .pill.bad {{ color: var(--bad); }} .pill.warn {{ color: var(--warn); }}
.pill.info {{ color: var(--info); }} .pill.mute {{ color: var(--ink-3); }}
.claim {{ margin: 0; font: 500 13px/1.5 var(--mono); white-space: pre-wrap; word-break: break-word; color: var(--ink);
  max-height: 13.5em; overflow: auto; }}
.eng {{ margin: 0; color: var(--ink-2); font-size: 14px; }}
details.eng summary {{ cursor: pointer; color: var(--ink-3); font: 12px/1.4 var(--mono); }}
details.eng p {{ margin: 6px 0 0; max-height: 14em; overflow: auto; }}
.why {{ margin: 0; color: var(--ink-3); font: 12px/1.4 var(--mono); }}
.cex {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }}
.lbl {{ font: 500 11px/1 var(--mono); letter-spacing: .1em; text-transform: uppercase; color: var(--bad); margin-right: 2px; }}
.cx {{ display: inline-flex; gap: 6px; align-items: baseline; font: 12px/1 var(--mono); padding: 5px 7px; background: rgba(255,93,115,.08);
  border: 1px solid rgba(255,93,115,.28); }}
.cx-k {{ color: var(--ink-2); }} .cx-v {{ color: var(--ink); font-weight: 600; font-variant-numeric: tabular-nums; }}
.bin {{ color: var(--ink-3); letter-spacing: .04em; }}
.cx.more {{ color: var(--ink-3); background: none; border-style: dashed; }}
.att-f {{ display: flex; flex-wrap: wrap; gap: 4px 12px; font: 12px/1.4 var(--mono); color: var(--ink-3); }}
.att-f .t {{ margin-left: auto; }}
.ratio {{ color: var(--ok); }}
.mc {{ display: grid; grid-template-columns: 1.2fr 1fr; gap: 32px; align-items: start; }}
.tbl-wrap {{ overflow-x: auto; border: 1px solid var(--line); }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }}
th {{ font: 500 11px/1.2 var(--mono); letter-spacing: .1em; text-transform: uppercase; color: var(--ink-3); background: var(--panel); }}
td.mono {{ font-family: var(--mono); }} td.clip {{ max-width: 380px; word-break: break-word; }}
td.said-yes {{ color: var(--warn); font-weight: 600; }} td.said-no {{ color: var(--bad); font-weight: 600; }}
.note {{ margin: 10px 0 0; color: var(--ink-3); font-size: 13px; max-width: 70ch; }}
.bars {{ display: grid; gap: 10px; padding: 16px; border: 1px solid var(--line); background: var(--panel); }}
.bar-row {{ display: grid; grid-template-columns: 100px 1fr auto; gap: 12px; align-items: center; font: 12px/1 var(--mono); }}
.bk {{ color: var(--ink-2); }}
.bar {{ height: 8px; width: var(--w); background: linear-gradient(90deg, #3dd6ff, #7b5cff 60%, #ff3d6e); }}
.bv {{ font-variant-numeric: tabular-nums; color: var(--ink); text-align: right; }}
.axis {{ font: 11px/1.3 var(--mono); color: var(--ink-3); margin: 4px 0 0; }}
footer.page {{ border-top: 1px solid var(--line); padding-top: 18px; display: grid; gap: 8px; color: var(--ink-3); font-size: 13px; }}
footer.page code {{ font-family: var(--mono); color: var(--ink-2); }}
a {{ color: var(--info); }}
a:focus-visible, .track:focus-visible {{ outline: 2px solid var(--info); outline-offset: 2px; }}
@media (max-width: 860px) {{ .mc {{ grid-template-columns: 1fr; }} .steps {{ grid-template-columns: 1fr 1fr; }} }}
@media (max-width: 560px) {{
  .track {{ flex-direction: column; overflow: visible; }}
  .att {{ flex-basis: auto; min-width: 0; }}
  .arrow {{ flex: 0 0 20px; width: 1px; height: 20px; align-self: flex-start; margin-left: 24px; }}
  .arrow::after {{ right: -4px; top: auto; bottom: 0; border: 4.5px solid transparent; border-top: 7px solid var(--ink-3); border-bottom: 0; }}
  .steps {{ grid-template-columns: 1fr; }}
  .bar-row {{ grid-template-columns: 84px 1fr; }} .bv {{ grid-column: 2; text-align: left; }}
}}
</style>
</head>
<body>
<main class="wrap">
  <header class="hero">
    <span class="eyebrow">Decap · HackTheBurgh XIII · agent layer</span>
    <h1>Witness</h1>
    <p class="thesis">An AI agent points at one region of a real Tiny Tapeout chip and explains it in one line.
      <b>The line only counts once Yosys proves it.</b> A wrong line comes back with the exact input that breaks it,
      and the agent gets two more tries.</p>
    <div class="stats">{stats}</div>
  </header>

  <section class="sec">
    <div class="sec-h"><h2>How one region is explained</h2></div>
    <div class="steps">
      <div class="step"><span class="n">1 · LOOK</span><b>Region + 1,024 vectors</b><span>Inputs, outputs and the decompiled code, plus simulated samples. Evidence, never a verdict.</span></div>
      <div class="step"><span class="n">2 · CLAIM</span><b>One Verilog line</b><span>The agent can only look. Its first claim is logged before any checker feedback.</span></div>
      <div class="step"><span class="n">3 · CHECK</span><b>Yosys, no LLM</b><span>Registers free, rst_n = ena = 1. Credit needs every output covered, complete, and ≤ ⅓ the literals or a known shape.</span></div>
      <div class="step"><span class="n">4 · REFINE</span><b>Counterexample back</b><span>Refuted, partial or restated claims return to the agent, at most twice.</span></div>
    </div>
  </section>

  <section class="sec runs">
    <div class="sec-h"><h2>Agent runs</h2><p>{nruns} region runs on {nchips} dev chips. Every claim, verdict and counterexample below is copied from the transcript; the spectral ring marks a claim Yosys proved and credited.</p></div>
    {runs}
  </section>

  <section class="sec">
    <div class="sec-h"><h2>Random testing said yes</h2><p>Ian's Monte Carlo question: what if we just ran lots of random inputs instead of proving? The same agent claims, re-checked by simulation.</p></div>
    <div class="mc">
      <div>{ablation}</div>
      <div>
        <div class="bars" role="img" aria-label="Random samples needed for a 99 percent chance of hitting one k-bit trigger value, log scale">{samples}</div>
        <p class="axis">Random samples for a 99% chance of hitting a single k-bit trigger value. Bar length is on a log scale.</p>
      </div>
    </div>
  </section>

  <footer class="page">
    <span>Generated {when} by <code>decap timeline</code> from <code>decap explain</code> / <code>decap bench</code> transcripts. Dev chips only: the held-out numbers come after the 02:30 freeze.</span>
    <span>Agent layer: FastMCP (Apache-2.0) + Pydantic AI (MIT), skills-over-MCP design borrowed from DimOS. Checker: Yuyao's <code>core/claims.py</code> on Yosys. 3D view: Tiny Tapeout Explorer (Apache-2.0).</span>
  </footer>
</main>
</body>
</html>
"""
