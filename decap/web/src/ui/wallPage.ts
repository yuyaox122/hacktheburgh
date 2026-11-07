// Proof wall: every benchmark chip as a tile (green proven, amber tested only, red failed), test vs dev shown apart.

import type { Bench, BenchChip, BenchSummary } from "../data/types";
import { loadBench, type Source } from "../data/load";
import { esc } from "./dom";

export class WallPage {
  readonly el = document.createElement("div");
  bench: Bench | null = null;
  source: Source | null = null;
  constructor() { this.el.className = "wall"; this.el.innerHTML = `<div class="empty"><span class="spin"></span> Loading the benchmark…</div>`; }

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
    return `<a class="tile ${c.verdict}" href="#/chip/${encodeURIComponent(c.chip)}" title="${esc(c.chip)}: ${c.verdict}">
      <b>${esc(c.chip)}</b><span class="ttl">${esc(c.title ?? "")}</span>
      <span class="row"><span class="pill ${c.verdict}">${c.verdict === "tested" ? "tested only" : c.verdict}</span><span>${c.logic_cells} cells</span>${c.flops ? `<span>${c.flops} flops</span>` : ""}<span>${c.seconds.toFixed(1)} s</span></span>
      <span class="row"><span>${c.lines_decompiled} lines vs ${c.lines_author ?? "?"} by the author</span></span>
      <span class="bar" title="block coverage ${c.coverage_pct}%"><i style="width:${Math.min(100, c.coverage_pct)}%"></i></span>
      <span class="row"><span>coverage ${c.coverage_pct.toFixed(0)}%</span>${t ? `<span>${t.caught ? "trigger caught ✓" : "trigger missed ✗"}</span>${t.clean_flags ? `<span>${t.clean_flags} false alarm${t.clean_flags > 1 ? "s" : ""}</span>` : ""}` : ""}</span>
    </a>`;
  }

  private summary(s: BenchSummary) {
    if (!s?.n) return "not scored yet";
    return `${s.proven ?? 0} proven · ${s.tested ?? 0} tested only · ${s.failed ?? 0} failed · coverage ${(s.coverage_pct ?? 0).toFixed(1)}%`;
  }

  private render() {
    const b = this.bench!;
    const T = b.summary.test ?? { n: 0 }, D = b.summary.dev ?? { n: 0 }, tj = b.summary.trojan;
    const frac = (s: Partial<BenchSummary>) => (s.n ? `${s.proven ?? 0}/${s.n}` : "–");
    const test = b.chips.filter((c) => c.split === "test"), dev = b.chips.filter((c) => c.split === "dev"), other = b.chips.filter((c) => c.split !== "test" && c.split !== "dev");
    const order = (a: BenchChip, z: BenchChip) => ["proven", "tested", "failed"].indexOf(a.verdict) - ["proven", "tested", "failed"].indexOf(z.verdict) || a.logic_cells - z.logic_cells;
    this.el.innerHTML = `
      <section class="headline">
        ${T.n ? `<h1><em>${T.proven ?? 0} of ${T.n}</em> proven on held-out chips</h1>` : `<h1>Held-out test set: <em style="color:var(--amber)">not scored yet</em></h1>`}
        <p>${T.n ? "" : `So far: <b>${frac(D)}</b> dev chips proven (the dev set is what we tuned on, so it does not count). `}Real Tiny Tapeout chips Decap had never seen, every name stripped, read back as Verilog. Green means Yosys <b>proved</b> the code is the same circuit. Amber means tested on random inputs only. Red means it failed.</p>
      </section>
      <div class="metrics">
        <div class="metric"><small>Held-out test set</small><b>${frac(T)}</b><span>${this.summary(T as BenchSummary)}</span></div>
        <div class="metric"><small>Readable${T.n ? "" : " (dev set)"}</small><b>${((T.n ? T : D).coverage_pct ?? 0).toFixed(0)}%</b><span>of logic cells explained by recognised blocks</span></div>
        ${tj && tj.planted ? `<div class="metric"><small>Planted triggers caught</small><b>${tj.caught}/${tj.planted}</b><span>blind, one random number per chip</span></div>
        <div class="metric"><small>False alarms</small><b>${tj.clean_flagged}/${tj.clean}</b><span>clean copies with at least one flag</span></div>` : ""}
        <div class="metric"><small>Dev set (tuned on)</small><b>${frac(D)}</b><span>${this.summary(D as BenchSummary)}</span></div>
      </div>
      <section><div class="split-h"><h2>Test set: held out</h2><span class="muted">frozen ${esc(b.split.frozen)} · sources opened only for scoring</span></div>
        <div class="tiles">${test.sort(order).map((c) => this.tile(c)).join("") || `<p class="muted">No test chips yet.</p>`}</div></section>
      <section><div class="split-h"><h2>Dev set: used for tuning</h2><span class="muted">not counted in the headline</span></div>
        <div class="tiles">${dev.sort(order).map((c) => this.tile(c)).join("") || `<p class="muted">No dev chips.</p>`}</div></section>
      ${other.length ? `<section><div class="split-h"><h2>Other</h2></div><div class="tiles">${other.map((c) => this.tile(c)).join("")}</div></section>` : ""}
      <p class="fine">Generated ${esc(b.generated)}${this.source === "fixture" ? " · FIXTURE data: made-up chips for development, not results" : this.source === "api" ? " · from the API" : ""}.</p>`;
  }

  commands() {
    return (this.bench?.chips ?? []).map((c) => ({ label: `Open chip ${c.chip}`, key: c.verdict, run: () => { location.hash = `#/chip/${encodeURIComponent(c.chip)}`; } }));
  }
  context() { return { page: "proof wall", summary: this.bench?.summary, chips: this.bench?.chips.map((c) => ({ chip: c.chip, split: c.split, verdict: c.verdict, cells: c.logic_cells })) }; }
}
