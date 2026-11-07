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

  /** A tile shows the name, the verdict colour, cells and coverage; everything else is in the hover. */
  private tile(c: BenchChip) {
    const t = c.trojan;
    const word = c.verdict === "tested" ? "tested only" : c.verdict;
    const href = `#/chip/${encodeURIComponent(c.chip)}`;
    if (c.coverage_pct == null) {   // failed before decompiling (e.g. a hard macro): no stats, only the reason
      const err = ((c as { error?: string }).error ?? "no result").replace(/^\w+Error: /, "");
      return `<a class="tile ${c.verdict}" href="${href}" title="${esc(`${c.chip}\n${word}\n${err}`)}">
        <span class="ttl">${esc(c.title || c.chip)}</span><code>${esc(c.chip)}</code>
        <span class="foot"><span class="why">${esc(err)}</span></span></a>`;
    }
    const tip = [c.chip, word, `${c.logic_cells} cells${c.flops ? `, ${c.flops} flip-flops` : ""} · ${c.seconds.toFixed(1)} s`,
      `${c.lines_decompiled} lines decompiled vs ${c.lines_author ?? "?"} by the author`, `coverage ${c.coverage_pct.toFixed(1)}%`,
      ...(t ? [`planted trigger ${t.caught ? "caught" : "missed"}${t.clean_flags ? ` · ${t.clean_flags} false alarm${t.clean_flags > 1 ? "s" : ""}` : ""}`] : [])].join("\n");
    return `<a class="tile ${c.verdict}" href="${href}" title="${esc(tip)}">
      <span class="ttl">${esc(c.title || c.chip)}</span><code>${esc(c.chip)}</code>
      <span class="foot"><span>${c.logic_cells} cells</span>${t && !t.caught ? `<span class="miss">trigger missed</span>` : ""}
        <span class="cov"><span class="bar"><i style="width:${Math.min(100, c.coverage_pct)}%"></i></span>${c.coverage_pct.toFixed(0)}%</span></span>
    </a>`;
  }

  /** Big fraction, then a proven / tested / failed bar with its counts underneath. */
  private scoreCard(label: string, s: Partial<BenchSummary>, tip: string) {
    const n = s.n ?? 0, p = s.proven ?? 0, t = s.tested ?? 0, f = s.failed ?? 0;
    if (!n) return `<div class="metric" title="${esc(tip)}"><small>${esc(label)}</small><b>–</b></div>`;
    const seg = (k: number, cls: string) => (k ? `<i class="${cls}" style="flex:${k}"></i>` : "");
    return `<div class="metric" title="${esc(tip)}"><small>${esc(label)}</small><b>${p}<span>/${n}</span></b>
      <span class="stack">${seg(p, "p")}${seg(t, "t")}${seg(f, "f")}</span>
      <span class="counts"><span class="p">${p} proven</span>${t ? `<span class="t">${t} tested</span>` : ""}${f ? `<span class="f">${f} failed</span>` : ""}</span></div>`;
  }
  private numCard(label: string, big: string, tip: string, pct?: number) {
    return `<div class="metric" title="${esc(tip)}"><small>${esc(label)}</small><b>${big}</b>${pct != null ? `<span class="stack"><i class="c" style="flex:${pct}"></i><i style="flex:${100 - pct}"></i></span>` : ""}</div>`;
  }

  private render() {
    const b = this.bench!;
    const T = b.summary.test ?? { n: 0 }, D = b.summary.dev ?? { n: 0 }, tj = b.summary.trojan;
    const test = b.chips.filter((c) => c.split === "test"), dev = b.chips.filter((c) => c.split === "dev"), other = b.chips.filter((c) => c.split !== "test" && c.split !== "dev");
    const order = (a: BenchChip, z: BenchChip) => ["proven", "tested", "failed"].indexOf(a.verdict) - ["proven", "tested", "failed"].indexOf(z.verdict) || a.logic_cells - z.logic_cells;
    const cov = (T.n ? T : D).coverage_pct ?? 0;
    this.el.innerHTML = `
      <section class="headline">
        ${T.n ? `<h1><em>${T.proven ?? 0} of ${T.n}</em> proven on held-out chips</h1>` : `<h1>Held-out test set: <em style="color:var(--amber)">not scored yet</em></h1>`}
        <p>Real Tiny Tapeout chips, names stripped, read back as Verilog and checked by Yosys.</p>
      </section>
      <div class="metrics">
        ${this.scoreCard("Held-out test set", T, `Chips Decap never saw while being built. Green: Yosys proved the code is the same circuit. Amber: random tests only. Red: failed.${T.n ? ` Coverage ${(T.coverage_pct ?? 0).toFixed(1)}%.` : ""}`)}
        ${this.numCard(`Readable${T.n ? "" : " (dev)"}`, `${cov.toFixed(0)}%`, "Logic cells explained by recognised blocks (adders, counters, muxes…)", Math.min(100, cov))}
        ${tj && tj.planted ? this.numCard("Triggers caught", `${tj.caught}<span>/${tj.planted}</span>`, "Blind: one random number planted per chip, names stripped")
          + this.numCard("False alarms", `${tj.clean_flagged}<span>/${tj.clean}</span>`, "Clean copies with at least one flag") : ""}
        ${this.scoreCard("Dev set", D, `Chips we tuned on: not counted in the headline. Coverage ${(D.coverage_pct ?? 0).toFixed(1)}%.`)}
      </div>
      <section><div class="split-h"><h2>Test set</h2><span class="muted">held out · frozen ${esc(b.split.frozen.slice(0, 10))}</span></div>
        <div class="tiles">${test.sort(order).map((c) => this.tile(c)).join("") || `<p class="muted">No test chips yet.</p>`}</div></section>
      <section><div class="split-h"><h2>Dev set</h2><span class="muted">tuned on, not counted</span></div>
        <div class="tiles">${dev.sort(order).map((c) => this.tile(c)).join("") || `<p class="muted">No dev chips.</p>`}</div></section>
      ${other.length ? `<section><div class="split-h"><h2>Other</h2></div><div class="tiles">${other.map((c) => this.tile(c)).join("")}</div></section>` : ""}
      <p class="fine">Generated ${esc(b.generated)}${this.source === "fixture" ? " · FIXTURE data: made-up chips for development, not results" : this.source === "api" ? " · from the API" : ""}.</p>`;
  }

  commands() {
    return (this.bench?.chips ?? []).map((c) => ({ label: `Open chip ${c.chip}`, key: c.verdict, run: () => { location.hash = `#/chip/${encodeURIComponent(c.chip)}`; } }));
  }
  context() { return { page: "proof wall", summary: this.bench?.summary, chips: this.bench?.chips.map((c) => ({ chip: c.chip, split: c.split, verdict: c.verdict, cells: c.logic_cells })) }; }
}
