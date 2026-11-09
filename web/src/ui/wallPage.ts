// Proof wall: every benchmark chip as a tile (green proven, amber tested only, red failed), test vs dev shown apart.

import type { Bench, BenchChip, BenchSummary } from "../data/types";
import { loadBench, type Source } from "../data/load";
import { esc } from "./dom";

export class WallPage {
  readonly el = document.createElement("div");
  bench: Bench | null = null;
  source: Source | null = null;
  constructor() { this.el.className = "wall"; this.el.innerHTML = `<div class="empty"><span class="spin"></span>Loading results</div>`; }

  async load() {
    try {
      const { data, source } = await loadBench();
      this.bench = data; this.source = source;
      this.render();
    } catch (e) {
      this.el.innerHTML = `<div class="empty">${esc((e as Error).message)}</div>`;
    }
  }

  private tile(c: BenchChip) {
    const t = c.trojan;
    const word = c.verdict === "proven" ? "Proven" : c.verdict === "tested" ? "Tested only" : "Failed";
    const head = `<span class="top-row"><i class="dot"></i><span>${word}</span><span class="grow"></span>${c.coverage_pct != null ? `<span>${c.seconds.toFixed(1)} s</span>` : ""}</span>
      <span class="name"><span class="ttl">${esc(c.title || c.chip)}</span>${c.title ? `<b>${esc(c.chip)}</b>` : ""}</span>`;
    if (c.coverage_pct == null) {   // failed before decompiling (e.g. a hard macro): no stats, only the reason
      const err = (c as { error?: string }).error ?? "No result";
      return `<a class="tile ${c.verdict}" href="#/chip/${encodeURIComponent(c.chip)}">${head}<span class="err">${esc(err.replace(/^\w+Error: /, ""))}</span></a>`;
    }
    return `<a class="tile ${c.verdict}" href="#/chip/${encodeURIComponent(c.chip)}" title="${c.lines_decompiled} lines decompiled, ${c.lines_author ?? "?"} written by the author">${head}
      <span class="bar" title="${c.coverage_pct.toFixed(0)}% of logic cells in recognised blocks"><i style="width:${Math.min(100, c.coverage_pct)}%"></i></span>
      <span class="row"><span><strong>${c.coverage_pct.toFixed(0)}%</strong> readable</span><span><strong>${c.logic_cells}</strong> cells</span>${c.flops ? `<span><strong>${c.flops}</strong> flops</span>` : ""}</span>
      ${t ? `<span class="row"><span class="tag ${t.caught ? "good" : "bad"}">${t.caught ? "Trigger caught" : "Trigger missed"}</span>${t.clean_flags ? `<span>${t.clean_flags} false alarm${t.clean_flags > 1 ? "s" : ""}</span>` : ""}</span>` : ""}
    </a>`;
  }

  /** Scoreboard bar: proven, tested only and failed as one stacked bar. */
  private score(s: BenchSummary) {
    const n = s.n || 1, seg = (v: number | undefined, c: string, w: string) => (v ? `<i style="width:${(v / n) * 100}%;background:var(${c})" title="${v} ${w}"></i>` : "");
    return `<span class="scorebar">${seg(s.proven, "--add", "proven")}${seg(s.tested, "--amber", "tested only")}${seg(s.failed, "--del", "failed")}</span>`;
  }

  private render() {
    const b = this.bench!;
    const T = b.summary.test ?? { n: 0 }, D = b.summary.dev ?? { n: 0 }, tj = b.summary.trojan;
    const frac = (s: Partial<BenchSummary>) => (s.n ? `${s.proven ?? 0}<small style="font-size:.55em;color:var(--muted);font-weight:500"> of ${s.n}</small>` : "None");
    const test = b.chips.filter((c) => c.split === "test"), dev = b.chips.filter((c) => c.split === "dev"), other = b.chips.filter((c) => c.split !== "test" && c.split !== "dev");
    const order = (a: BenchChip, z: BenchChip) => ["proven", "tested", "failed"].indexOf(a.verdict) - ["proven", "tested", "failed"].indexOf(z.verdict) || a.logic_cells - z.logic_cells;
    const keys = `<span class="keys"><span><i style="background:var(--add)"></i>Proven</span><span><i style="background:var(--amber)"></i>Tested only</span><span><i style="background:var(--del)"></i>Failed</span></span>`;
    this.el.innerHTML = `
      <section class="headline">
        <span class="eyebrow">Benchmark</span>
        ${T.n ? `<h1><em>${T.proven ?? 0} of ${T.n}</em> chips proven.<br><span class="soft">Never seen before.</span></h1>` : `<h1>Held-out chips <span class="soft">not scored yet.</span></h1>`}
        <p>Real Tiny Tapeout chips, every name stripped, read back as Verilog. Proven means Yosys showed the code is exactly the same circuit.${T.n ? "" : ` So far ${D.proven ?? 0} of ${D.n ?? 0} dev chips are proven; those were used for tuning, so they do not count.`}</p>
      </section>
      <div class="metrics">
        <div class="metric"><small>Held-out chips proven</small><b>${frac(T)}</b>${T.n ? this.score(T as BenchSummary) : ""}<span>${T.n ? `${T.tested ?? 0} tested only, ${T.failed ?? 0} failed` : "Not scored yet"}</span></div>
        <div class="metric"><small>Readable</small><b>${((T.n ? T : D).coverage_pct ?? 0).toFixed(0)}%</b><span>Logic cells in recognised blocks${T.n ? "" : ", dev set"}</span></div>
        ${tj && tj.planted ? `<div class="metric"><small>Hidden triggers caught</small><b>${tj.caught}<small style="font-size:.55em;color:var(--muted);font-weight:500"> of ${tj.planted}</small></b><span>Blind, one random number per chip</span></div>
        <div class="metric"><small>False alarms</small><b>${tj.clean_flagged}<small style="font-size:.55em;color:var(--muted);font-weight:500"> of ${tj.clean}</small></b><span>Clean chips with any flag</span></div>` : ""}
      </div>
      <section><div class="split-h"><h2>Held-out test set</h2><span class="muted">Frozen ${esc(b.split.frozen)}. Sources opened only for scoring.</span>${keys}</div>
        <div class="tiles">${test.sort(order).map((c) => this.tile(c)).join("") || `<p class="muted">No test chips yet.</p>`}</div></section>
      <section><div class="split-h"><h2>Dev set</h2><span class="muted">Used for tuning, so not counted above. ${D.n ? `${D.proven ?? 0} of ${D.n} proven.` : ""}</span></div>
        <div class="tiles">${dev.sort(order).map((c) => this.tile(c)).join("") || `<p class="muted">No dev chips.</p>`}</div></section>
      ${other.length ? `<section><div class="split-h"><h2>Other</h2></div><div class="tiles">${other.map((c) => this.tile(c)).join("")}</div></section>` : ""}
      <p class="fine">Generated ${esc(b.generated)}.${this.source === "fixture" ? " Sample data for development, not results." : ""}</p>`;
  }

  commands() {
    return (this.bench?.chips ?? []).map((c) => ({ label: `Open ${c.title || c.chip}`, key: c.verdict, run: () => { location.hash = `#/chip/${encodeURIComponent(c.chip)}`; } }));
  }
  context() { return { page: "proof wall", summary: this.bench?.summary, chips: this.bench?.chips.map((c) => ({ chip: c.chip, split: c.split, verdict: c.verdict, cells: c.logic_cells })) }; }
}
