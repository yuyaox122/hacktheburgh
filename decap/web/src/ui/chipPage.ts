// Chip page: one chip result (CONTRACTS §1). Decompiled Verilog beside the author's source, the verdict and proof,
// block coverage, and the stripped netlist as a 2D graph. Find (plant a trigger) and Fix (remove and prove) live here.

import type { BenchChip, ChipResult, FixResult, Lib } from "../data/types";
import { fix as apiFix, loadBench, loadChip, loadLib, loadStageIndex, plant as apiPlant, type Source } from "../data/load";
import { NetGraph } from "./graph";
import { $, $$, esc, hiVerilog, toast } from "./dom";
import { app } from "./state";

const KIND_COLOUR: Record<string, string> = {
  adder: "var(--adder)", subtractor: "var(--adder)", counter: "var(--reg)", register: "var(--reg)", shift: "var(--reg)",
  comparator: "var(--detect)", mux: "var(--mux)", reduce: "var(--gate)", const: "var(--const)", copy: "var(--const)", logic: "var(--faint)",
};
const KIND_WORD: Record<string, string> = {
  adder: "adder", subtractor: "subtractor", counter: "counter", register: "register", shift: "shift register", comparator: "comparator",
  mux: "multiplexer", reduce: "reduction", const: "constant", copy: "wire copy", logic: "leftover logic",
};

type Tab = "proof" | "find" | "fix" | "blocks";

/** Three working page layouts (picked in the header, remembered per browser). The DOM is the same; CSS places the panes. */
const LAYOUTS = [
  { id: "split", name: "Split", tip: "Both sources side by side, netlist and details below" },
  { id: "focus", name: "Focus", tip: "Decompiled code full height; details and netlist in a sidebar" },
  { id: "graph", name: "Netlist", tip: "The netlist graph on top, both sources below, details on the right" },
];
const LAYOUT_KEY = "decap.chipLayout";
let layoutNow: string | null = null;
function chipLayout(): string {
  if (!layoutNow) { try { layoutNow = localStorage.getItem(LAYOUT_KEY); } catch { /* storage blocked */ } }
  return LAYOUTS.some((l) => l.id === layoutNow) ? layoutNow! : "split";
}

export class ChipPage {
  readonly el = document.createElement("div");
  private clean: ChipResult | null = null;
  private planted: ChipResult | null = null;
  private showPlanted = false;
  private source: Source = "static";
  private plantSource: Source | null = null;
  private lib: Lib = {};
  private graph: NetGraph | null = null;
  private selLine: number | null = null;
  private selCell: string | null = null;
  private selBlock: string | null = null;
  private file: string | null = null;
  private tab: Tab = "proof";
  private benchRow: BenchChip | null = null;
  private fixRes: { data: FixResult; source: Source } | null = null;
  private busy: "plant" | "fix" | null = null;
  private busyT0 = 0;
  private busyTimer = 0;
  private n: number;
  private err: string | null = null;

  constructor(readonly chip: string) {
    this.el.className = "chippage";
    this.n = app.judge.n ?? 137;
    this.el.innerHTML = `<div class="empty"><span class="spin"></span> Loading ${esc(chip)}…</div>`;
  }

  get result(): ChipResult | null { return this.showPlanted && this.planted ? this.planted : this.clean; }
  /** The author's source (the planted copy carries none: the author's code is the same). */
  private get author(): NonNullable<ChipResult["author_source"]> | null {
    const has = (x: ChipResult["author_source"] | undefined) => !!x && Object.keys(x.files ?? {}).length > 0;
    const a = has(this.result?.author_source) ? this.result!.author_source! : has(this.clean?.author_source) ? this.clean!.author_source! : null;
    return a;
  }
  private repoLink(a: NonNullable<ChipResult["author_source"]>) {
    if (!a.repo) return "";
    const label = `${a.repo}${a.commit ? `@${a.commit.slice(0, 7)}` : ""}`;
    return a.url ? `<a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(label)}</a>` : esc(label);
  }

  private ready: Promise<void> | null = null;
  /** Idempotent: every caller awaits the same load. */
  /** This chip has a 3D layout on the Stage (data/designs/<chip>.json). */
  private in3d = false;
  load(): Promise<void> { return (this.ready ??= this.doLoad()); }
  private async doLoad() {
    try {
      const [{ data, source }, lib, bench, stageIdx] = await Promise.all([loadChip(this.chip), loadLib(), loadBench().catch(() => null), loadStageIndex()]);
      this.in3d = !!stageIdx?.chips.some((c) => c.chip === this.chip);
      this.clean = data; this.source = source; this.lib = lib;
      this.benchRow = bench?.data.chips.find((c) => c.chip === this.chip) ?? null;
      this.file = Object.keys(data.author_source?.files ?? {})[0] ?? null;
      const key = `${this.chip}:${this.n}`;
      if (app.planted.has(key)) void this.adoptPlant(key, false); // started by the demo prep: keep the clean view for now
      this.render();
    } catch (e) {
      this.el.innerHTML = `<div class="empty"><p>${esc((e as Error).message)}</p><p><a href="#/wall">Back to the proof wall</a></p></div>`;
    }
  }

  private adoptPlant(key: string, show: boolean): Promise<void> {
    const p = app.planted.get(key)!;
    this.busy = "plant"; this.startTimer();
    return p.then((r) => {
      this.planted = r; this.plantSource = plantSources.get(key) ?? this.plantSource; this.err = null;
      if (show) { this.showPlanted = true; this.tab = "find"; }
    }).catch((e) => { this.err = `Planting failed: ${(e as Error).message}`; app.planted.delete(key); })
      .finally(() => {
        this.busy = null; this.stopTimer(); this.render();
        if (show && this.showPlanted) { this.lightFlags(); $("#cpSide .callout", this.el)?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
      });
  }
  /** Demo: show the Find result for the judge's number (plant if nobody has yet). */
  async showFind() {
    await this.load();
    const n = app.judge.n ?? this.n, key = `${this.chip}:${n}`;
    this.n = n;
    if (!app.planted.has(key)) { this.plant(n); return; }
    await this.adoptPlant(key, true);
  }
  /** Demo: the clean read-back with its proof. */
  async showClean() { await this.load(); this.tab = "proof"; this.setVariant(false); }

  // ---------- render ----------
  private render() {
    const r = this.result;
    if (!r) return;
    const a = this.author;
    const verdictWord = r.verdict === "tested" ? "tested only" : r.verdict;
    const statTip = `${r.stats.logic_cells} logic cells · ${r.stats.flops} flip-flops · ${r.stats.cell_types} cell types · ${r.stats.area_um2.toFixed(0)} µm²\n`
      + `coverage ${r.coverage.pct.toFixed(1)}% (${r.coverage.explained_cells}/${r.coverage.logic_cells} cells in recognised blocks)\n`
      + `${r.decompiled.lines} lines decompiled${a ? ` vs ${a.lines} by the author` : ""}`;
    this.el.dataset.layout = chipLayout();
    this.el.innerHTML = `
      <header class="chiphead">
        <h2>${esc(r.title || r.chip)} <small>${esc(r.chip)}</small></h2>
        <span class="verdict ${r.verdict}" title="${esc(r.proof.detail ?? "")}">${esc(verdictWord)}</span>
        ${r.split === "test" ? `<span class="pill" title="Held out: never seen while Decap was built">held out</span>` : `<span class="pill">${esc(r.split)}</span>`}
        ${this.planted ? `<div class="seg" role="group" aria-label="Which copy"><button type="button" data-variant="clean" aria-pressed="${!this.showPlanted}">clean</button><button type="button" data-variant="planted" aria-pressed="${this.showPlanted}">planted N = ${this.planted.plant?.n ?? "?"}${this.plantSource === "fixture" ? " (fixture)" : this.plantSource === "preplanted" ? " (pre-planted)" : ""}</button></div>` : ""}
        <div class="chipstats" title="${esc(statTip)}">
          <span><b>${r.stats.logic_cells}</b> cells</span>
          <span class="cov">coverage <span class="bar"><i style="width:${Math.min(100, r.coverage.pct)}%"></i></span><b>${r.coverage.pct.toFixed(0)}%</b></span>
          <span><b>${r.decompiled.lines}</b>${a ? ` / ${a.lines}` : ""} lines</span>
        </div>
        <span class="grow" style="flex:1"></span>
        ${this.source === "fixture" ? `<span class="pill warn">fixture data</span>` : ""}
        <div class="seg layoutsw" role="group" aria-label="Layout" title="Page layout">${LAYOUTS.map((l) => `<button type="button" data-layout="${l.id}" aria-pressed="${l.id === this.el.dataset.layout}" title="${esc(l.tip)}">${esc(l.name)}</button>`).join("")}</div>
        ${this.in3d ? `<a class="btn sm" href="#/stage/${encodeURIComponent(this.chip)}" title="This chip's layout in 3D, with a live simulator">View in 3D</a>` : ""}
        <button class="btn sm" type="button" id="cpGuide" title="Ask the guide (G)">Ask</button>
      </header>
      <div class="codes">
        <section class="pane p-dec" aria-label="Decompiled Verilog">
          <div class="pane-h">Decompiled <b>${r.variant === "planted" ? "planted copy" : "names stripped"}</b><span class="grow"></span>
            ${a ? `<button class="btn sm only-focus" type="button" data-auth="1" title="Show what the author wrote">Author's source</button>` : ""}<span>${r.decompiled.lines} lines</span></div>
          <div class="pane-b"><ol class="code click" id="cpCode"></ol></div>
        </section>
        <section class="pane p-auth" aria-label="Author source">
          <div class="pane-h">Author's source ${a ? `<span class="filetabs">${Object.keys(a.files).map((f) => `<button type="button" data-file="${esc(f)}" aria-pressed="${f === this.file}">${esc(f)}</button>`).join("")}</span>` : ""}<span class="grow"></span>
            ${a ? `<span class="pill ${a.check === "proven" ? "ok" : a.check === "bounded" ? "warn" : a.check === "failed" ? "bad" : ""}" title="${esc(`Shipped netlist checked against this source: ${a.check}${a.check_detail ? `. ${a.check_detail}` : ""}`)}">matches netlist: ${esc(a.check)}</span> ${this.repoLink(a)}
            <button class="btn sm only-focus" type="button" data-auth="0" title="Back to the decompiled Verilog">Decompiled</button>` : ""}</div>
          <div class="pane-b">${a && this.file ? `<ol class="code" id="cpAuthor"></ol>` : `<div class="empty">No author source for this chip.</div>`}</div>
        </section>
      </div>
      <div class="lower">
        <section class="pane p-graph" aria-label="Netlist graph">
          <div class="pane-h">Netlist <b>${r.netlist.cells.length} cells, no names</b><span class="grow"></span><button class="btn sm" type="button" id="cpFit" title="Fit (0)">Fit</button></div>
          <div class="graphwrap" id="cpGraph"><div class="overlay glegend" id="cpGLegend"></div></div>
        </section>
        <section class="pane p-side" aria-label="Details">
          <nav class="tabs" role="tablist">
            ${(["proof", "find", "fix", "blocks"] as Tab[]).map((t) => `<button type="button" role="tab" data-tab="${t}" aria-selected="${this.tab === t}">${{ proof: "Proof", find: "Find a trigger", fix: "Fix", blocks: `Blocks (${r.blocks.length})` }[t]}</button>`).join("")}
          </nav>
          <div class="pane-b" id="cpSide"></div>
        </section>
      </div>`;
    this.renderCode(); this.renderAuthor(); this.renderSide();
    this.graph?.destroy();
    this.graph = new NetGraph($("#cpGraph", this.el), r.netlist, this.lib, {
      onCell: (id) => this.selectCell(id),
      label: (id) => (r.cell_lines[id] ? `line ${r.cell_lines[id]}` : "unexplained"),
    });
    this.applyMarks();
    const gl = $("#cpGLegend", this.el);
    gl.innerHTML = `${r.flags.length ? `<span style="color:var(--del)">■ flagged cone</span>` : ""}${this.fixRes ? `<span style="color:var(--fall)">■ removed by Fix</span>` : ""}`;
    gl.hidden = !gl.innerHTML;
    $("#cpGraph", this.el).title = "Scroll to zoom, drag to pan, click a cell";
    this.bind();
  }

  private blockForLine(ln: number) { return this.result?.blocks.find((b) => ln >= b.lines[0] && ln <= b.lines[1]) ?? null; }

  private renderCode() {
    const r = this.result!;
    const lines = r.decompiled.verilog.split("\n");
    const flagLines = new Map(r.flags.map((f) => [f.line, f]));
    const hlCells = this.selCell ? new Set([this.selCell]) : null;
    const removed = new Set(this.fixRes?.data.removed_cells ?? []);
    $("#cpCode", this.el).innerHTML = lines.map((t, i) => {
      const ln = i + 1, b = this.blockForLine(ln), f = flagLines.get(ln);
      const cellsHere = Object.entries(r.cell_lines).filter(([, l]) => l === ln).map(([c]) => c);
      const active = this.selLine === ln || (this.selBlock && b?.id === this.selBlock) || (hlCells && cellsHere.some((c) => hlCells.has(c)));
      const isRemoved = this.showPlanted && cellsHere.length > 0 && cellsHere.every((c) => removed.has(c));
      const cls = [active ? "active" : "", f ? "flag" : "", isRemoved ? "removed" : ""].join(" ");
      const bar = b ? `background:${KIND_COLOUR[b.kind] ?? "var(--faint)"};opacity:${b.recognised ? 0.9 : 0.35}` : "";
      const badge = f ? `<span class="badge">flag: fires ${esc(f.rate)}</span>` : b && ln === b.lines[0] ? `<span class="badge" title="${b.cells.length} cells">${esc(KIND_WORD[b.kind] ?? b.kind)}${b.width ? ` · ${b.width}b` : ""}</span>` : "<span></span>";
      return `<li data-ln="${ln}" class="${cls}"><span class="n">${ln}</span><span class="bk" style="${bar}"></span><span class="t">${hiVerilog(t) || " "}</span>${badge}</li>`;
    }).join("");
  }

  private renderAuthor() {
    const a = this.author;
    const ol = $("#cpAuthor", this.el);
    if (!a || !this.file || !ol) return;
    ol.innerHTML = (a.files[this.file] ?? "").split("\n").map((t, i) => `<li><span class="n">${i + 1}</span><span class="bk"></span><span class="t">${hiVerilog(t) || " "}</span><span></span></li>`).join("");
  }

  private renderSide() {
    const r = this.result!, side = $("#cpSide", this.el);
    $$("[data-tab]", this.el).forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === this.tab)));
    if (this.tab === "proof") {
      const p = r.proof;
      side.innerHTML = `<div class="sect">
          <h4>${r.verdict === "proven" ? "Proven equivalent" : r.verdict === "tested" ? "Tested, not proven" : "Proof failed"} <span class="pill ${r.verdict}">${r.verdict}</span></h4>
          <p>${r.verdict === "proven" ? "A formal checker (Yosys) proved the decompiled Verilog is exactly the same circuit as the netlist, for every input and every state." : r.verdict === "tested" ? `No proof yet. The decompile matched the netlist on ${p.tested_cycles ?? "random"} random cycles, which is evidence, not proof.` : "The decompiled Verilog is not the same circuit. Shown in red, as measured."}</p>
          <dl class="kv"><dt>Method</dt><dd>${esc(p.method)}</dd>
            ${p.points != null ? `<dt>Points</dt><dd>${p.proven ?? "?"} / ${p.points} proven</dd>` : ""}
            ${p.seconds != null ? `<dt>Time</dt><dd>${p.seconds.toFixed(1)} s</dd>` : ""}
            ${p.control_caught != null ? `<dt>Control</dt><dd>${p.control_caught ? "a deliberately broken copy was rejected ✓" : "<span style='color:var(--del)'>the broken control was NOT caught</span>"}</dd>` : ""}
            ${p.tested_cycles ? `<dt>Random test</dt><dd>${p.tested_cycles} cycles</dd>` : ""}
            ${p.detail ? `<dt>Detail</dt><dd>${esc(p.detail)}</dd>` : ""}</dl>
        </div>
        <div class="sect"><h4>Readable?</h4>
          <p><b>${r.coverage.pct.toFixed(1)}%</b> of logic cells are explained by recognised blocks (${r.coverage.explained_cells} of ${r.coverage.logic_cells}); the rest is leftover logic, written out gate by gate.</p>
          <p class="muted">${r.decompiled.lines} lines decompiled${this.author ? `, against ${this.author.lines} lines in the author's source` : ""}.</p></div>
        ${this.author ? `<div class="sect"><h4>Answer key <span class="pill ${this.author.check === "proven" ? "ok" : this.author.check === "failed" ? "bad" : "warn"}">${esc(this.author.check)}</span></h4>
          <p>The shipped netlist checked against the author's source${this.author.repo ? ` at ${this.repoLink(this.author)}` : ""}${this.author.check_detail ? `: ${esc(this.author.check_detail)}` : ""}.</p></div>` : ""}`;
    } else if (this.tab === "blocks") {
      side.innerHTML = `<div class="sect"><p class="muted">Recognised blocks, in source order. Click one to light its lines and cells.</p><div class="blocklist">${r.blocks.map((b) => `
        <button type="button" class="blockrow${this.selBlock === b.id ? " on" : ""}" data-block="${esc(b.id)}"><i class="sw" style="background:${KIND_COLOUR[b.kind] ?? "var(--faint)"};opacity:${b.recognised ? 1 : 0.4}"></i>
          <span class="kind">${esc(KIND_WORD[b.kind] ?? b.kind)}${b.width ? ` · ${b.width}b` : ""}</span><code>${esc(b.text)}</code><span class="muted">${b.cells.length} cells</span></button>`).join("")}</div></div>`;
    } else if (this.tab === "find") {
      side.innerHTML = this.findHtml();
    } else {
      side.innerHTML = this.fixHtml();
    }
    this.bindSide();
  }

  private findHtml(): string {
    const p = this.planted, cleanFlags = this.clean?.flags ?? [];
    const t = this.benchRow?.trojan;
    const busy = this.busy === "plant";
    let res = "";
    if (p) {
      const hit = p.flags.find((f) => new RegExp(`(^|[^0-9])${p.plant?.n ?? -1}([^0-9]|$)`).test(f.condition));
      res = `${this.plantSource === "preplanted" && p.plant?.n !== this.n ? `<div class="callout warn"><b>Live planting is unavailable, so this is the pre-planted copy (N = ${p.plant?.n}), not your number.</b><span class="muted">${esc(this.err ?? "The Decap API did not answer.")}</span></div>` : ""}
        <div class="callout ${hit ? "good" : "bad"}">
          <b>${hit ? `Found it: ${this.plantSource === "preplanted" && p.plant?.n !== this.n ? "the planted number" : "your number"} ${p.plant?.n} came back as code.` : p.flags.length ? `Flagged ${p.flags.length} line(s), but none mentions ${p.plant?.n}.` : `Missed: nothing was flagged in the planted copy.`}</b>
          ${p.plant ? `<span class="muted">Planted: an <code>== ${p.plant.n}</code> trigger OR'd into ${esc(p.plant.target)}, ${p.plant.inserted_cells.length} cells, names stripped.${this.plantSource === "fixture" ? " (FIXTURE result, made up.)" : ""}</span>` : ""}</div>
        ${p.flags.map((f) => `<button type="button" class="flagcard" data-flag="${esc(f.id)}">
          <code>line ${f.line}: if (${esc(f.condition)})</code>
          <span class="row"><span>drives <b>${esc(f.signal)}</b></span><span>fires for ${esc(f.rate)} of inputs</span><span>${f.cells.length} cells, cone ${f.cone.length}</span><span>${esc(f.kind)}</span></span></button>`).join("")}`;
    }
    return `<div class="sect">
        <h4>Plant a trigger</h4>
        <p>Pick a number from 0 to 255. Decap splices a hidden trigger for it into this chip (an <code>== N</code> comparator OR'd into an output), strips every name, then reads the chip back blind and flags rare-firing logic.</p>
        <div class="acts"><label for="cpN">Judge's number</label><input class="num" id="cpN" type="number" min="0" max="255" value="${this.n}">
          <button class="btn primary" type="button" id="cpPlant" ${busy ? "disabled" : ""}>${busy ? `<span class="spin"></span> Planting… <span id="cpT">0</span> s` : "Plant and find"}</button>
          ${p ? `<button class="btn sm" type="button" id="cpCone">Light the cone</button>` : ""}</div>
        ${this.err ? `<div class="callout bad">${esc(this.err)}</div>` : ""}
        ${res}
      </div>
      <div class="sect"><h4>False alarms, shown honestly</h4>
        ${cleanFlags.length ? `<p>On the <b>clean</b> copy Decap also flags ${cleanFlags.length} line(s). These are legitimate rare comparators, not trojans; with the author's source they are separated exactly.</p>
          ${cleanFlags.map((f) => `<div class="flagcard" style="border-color:var(--line-2);background:var(--panel-2)"><code>line ${f.line}: if (${esc(f.condition)})</code><span class="row"><span>${esc(f.signal)}</span><span>fires ${esc(f.rate)}</span></span></div>`).join("")}`
        : `<p class="muted">The clean copy of this chip has no flags.</p>`}
        ${t ? `<p class="muted">Benchmark run: trigger for N=${t.n} ${t.caught ? `caught (line ${t.flag_line ?? "?"})` : "missed"}; ${t.clean_flags} flag(s) on the clean copy.</p>` : ""}
      </div>`;
  }

  private fixHtml(): string {
    const f = this.fixRes;
    if (!this.planted) return `<div class="sect"><h4>Remove and prove</h4><p class="muted">Plant a trigger first (the Find tab); Fix removes the flagged cone and proves the result.</p></div>`;
    const busy = this.busy === "fix";
    return `<div class="sect"><h4>Remove and prove</h4>
      <p>Delete the flagged cone from the planted netlist, then prove what is left is the same circuit as the clean chip (and the author's source, where possible).</p>
      <div class="acts"><button class="btn primary" type="button" id="cpFix" ${busy ? "disabled" : ""}>${busy ? `<span class="spin"></span> Removing and proving… <span id="cpT">0</span> s` : "Remove and prove"}</button></div>
      ${f ? `<div class="callout ${f.data.verdict === "proven" ? "good" : f.data.verdict === "tested" ? "warn" : "bad"}"><b>${f.data.verdict === "proven" ? "Proven: the chip is back to what the author wrote." : f.data.verdict === "tested" ? "Tested, not proven." : "The fix did not prove."}</b>
        <span>${esc(f.data.detail)}</span><span class="muted">${f.data.removed_cells.length} cells removed in ${f.data.seconds.toFixed(1)} s${f.source === "fixture" ? " · FIXTURE result" : ""}</span></div>
        <dl class="kv"><dt>Removed</dt><dd>${f.data.removed_cells.map((c) => `<button type="button" class="linkbtn" data-cell="${esc(c)}">${esc(c)}</button>`).join(" ")}</dd></dl>
        <details><summary class="muted">Fixed Verilog</summary><ol class="code">${f.data.verilog.split("\n").map((t, i) => `<li><span class="n">${i + 1}</span><span class="bk"></span><span class="t">${hiVerilog(t) || " "}</span><span></span></li>`).join("")}</ol></details>` : ""}
      ${this.err ? `<div class="callout bad">${esc(this.err)}</div>` : ""}</div>`;
  }

  // ---------- interaction ----------
  private bind() {
    $("#cpCode", this.el).addEventListener("click", (e) => {
      const li = (e.target as HTMLElement).closest<HTMLElement>("[data-ln]");
      if (li) this.selectLine(+li.dataset.ln!);
    });
    $$<HTMLElement>("[data-file]", this.el).forEach((b) => b.addEventListener("click", () => { this.file = b.dataset.file!; $$("[data-file]", this.el).forEach((x) => x.setAttribute("aria-pressed", String(x === b))); this.renderAuthor(); }));
    $$<HTMLElement>("[data-tab]", this.el).forEach((b) => b.addEventListener("click", () => { this.tab = b.dataset.tab as Tab; this.renderSide(); }));
    $$<HTMLElement>("[data-variant]", this.el).forEach((b) => b.addEventListener("click", () => this.setVariant(b.dataset.variant === "planted")));
    $("#cpFit", this.el).addEventListener("click", () => { this.graph?.fit(); this.graph?.draw(); });
    $("#cpGuide", this.el).addEventListener("click", () => app.askGuide?.(""));
    $$<HTMLElement>("[data-layout]", this.el).forEach((b) => b.addEventListener("click", () => {
      const id = b.dataset.layout!;
      try { localStorage.setItem(LAYOUT_KEY, id); } catch { /* private window: per-visit only */ }
      layoutNow = id;
      this.el.dataset.layout = id; this.el.classList.remove("show-author");
      $$("[data-layout]", this.el).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      requestAnimationFrame(() => { this.graph?.fit(); this.graph?.draw(); });
    }));
    $$<HTMLElement>("[data-auth]", this.el).forEach((b) => b.addEventListener("click", () => this.el.classList.toggle("show-author", b.dataset.auth === "1")));
  }
  private bindSide() {
    const side = $("#cpSide", this.el);
    $$<HTMLElement>("[data-block]", side).forEach((b) => b.addEventListener("click", () => this.selectBlock(b.dataset.block!)));
    $$<HTMLElement>("[data-flag]", side).forEach((b) => b.addEventListener("click", () => this.selectFlag(b.dataset.flag!)));
    $$<HTMLElement>("[data-cell]", side).forEach((b) => b.addEventListener("click", () => this.selectCell(b.dataset.cell!)));
    $("#cpPlant", side)?.addEventListener("click", () => this.plant());
    $<HTMLInputElement>("#cpN", side)?.addEventListener("keydown", (e) => { if (e.key === "Enter") this.plant(); });
    $("#cpCone", side)?.addEventListener("click", () => this.lightFlags());
    $("#cpFix", side)?.addEventListener("click", () => this.fix());
  }

  setVariant(planted: boolean) {
    if (planted && !this.planted) return;
    this.showPlanted = planted; this.selLine = null; this.selCell = null; this.selBlock = null;
    this.tab = planted ? "find" : this.tab === "find" || this.tab === "fix" ? "proof" : this.tab;
    this.render();
    if (planted) this.lightFlags();
  }

  selectLine(ln: number) {
    const r = this.result!;
    this.selLine = ln; this.selCell = null; this.selBlock = null;
    let ids = Object.entries(r.cell_lines).filter(([, l]) => l === ln).map(([c]) => c);
    if (!ids.length) { const b = this.blockForLine(ln); if (b) ids = b.cells; }
    this.renderCode();
    this.graph?.setHighlight(new Set(ids));
    this.graph?.select(null);
    if (ids.length) this.graph?.focus(ids);
  }
  selectCell(id: string | null) {
    const r = this.result!;
    this.selCell = id; this.selLine = id ? r.cell_lines[id] ?? null : null; this.selBlock = null;
    this.renderCode();
    this.graph?.select(id);
    this.graph?.setHighlight(id ? new Set([id]) : null);
    if (this.selLine) $(`#cpCode li[data-ln="${this.selLine}"]`, this.el)?.scrollIntoView({ block: "center", behavior: "smooth" });
    else if (id) toast(`${id} is not explained by any decompiled line.`);
  }
  selectBlock(bid: string) {
    const b = this.result!.blocks.find((x) => x.id === bid);
    if (!b) return;
    this.selBlock = bid; this.selLine = null; this.selCell = null;
    this.renderCode(); this.renderSide();
    this.graph?.setHighlight(new Set(b.cells)); this.graph?.focus(b.cells);
    $(`#cpCode li[data-ln="${b.lines[0]}"]`, this.el)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }
  selectFlag(fid: string) {
    const f = this.result!.flags.find((x) => x.id === fid);
    if (!f) return;
    this.selLine = f.line; this.selCell = null; this.selBlock = null;
    this.renderCode();
    this.graph?.setHighlight(new Set(f.cone)); this.graph?.focus(f.cone);
    $(`#cpCode li[data-ln="${f.line}"]`, this.el)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }
  lightFlags() {
    const r = this.result;
    if (!r || !r.flags.length) return;
    const cone = r.flags.flatMap((f) => f.cone);
    this.applyMarks();
    this.graph?.setHighlight(new Set(cone)); this.graph?.focus(cone);
    // Jump to the planted trigger (the flag naming the planted number), not whichever flag happens to come first.
    const n = r.plant?.n;
    const first = (n != null && r.flags.find((f) => new RegExp(`(^|[^0-9])${n}([^0-9]|$)`).test(f.condition))) || r.flags[0];
    this.selLine = first.line; this.renderCode();
    $(`#cpCode li[data-ln="${first.line}"]`, this.el)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }
  private applyMarks() {
    const r = this.result!, m = new Map<string, string>();
    for (const f of r.flags) for (const c of f.cone) m.set(c, "#ef6b73");
    if (this.showPlanted) for (const c of this.fixRes?.data.removed_cells ?? []) m.set(c, "#5ec8ff");
    this.graph?.setMarks(m);
  }

  private startTimer() {
    this.busyT0 = performance.now();
    clearInterval(this.busyTimer);
    this.busyTimer = window.setInterval(() => { const t = $("#cpT", this.el); if (t) t.textContent = ((performance.now() - this.busyT0) / 1000).toFixed(0); }, 500);
  }
  private stopTimer() { clearInterval(this.busyTimer); }

  plant(n?: number) {
    const v = n ?? +($<HTMLInputElement>("#cpN", this.el)?.value ?? this.n);
    if (!(v >= 0 && v <= 255 && Number.isInteger(v))) { toast("Pick a whole number from 0 to 255.", "bad"); return; }
    this.n = v; app.judge.n = v; this.err = null; this.fixRes = null;
    const key = `${this.chip}:${v}`;
    if (!app.planted.has(key)) app.planted.set(key, startPlant(this.chip, v));
    this.tab = "find"; this.busy = "plant"; this.renderSide();
    void this.adoptPlant(key, true);
  }
  async fix() {
    await this.load();
    if (!this.planted) { this.tab = "fix"; this.renderSide(); return; }
    if (!this.showPlanted) this.setVariant(true);
    this.busy = "fix"; this.err = null; this.renderSide(); this.startTimer();
    try { this.fixRes = await apiFix(this.chip, this.n); }
    catch (e) { this.err = `Fix failed: ${(e as Error).message}`; }
    this.busy = null; this.stopTimer();
    this.render(); this.tab = "fix"; this.renderSide();
  }

  context() {
    const r = this.result;
    if (!r) return { page: "chip", chip: this.chip };
    const lines = r.decompiled.verilog.split("\n");
    return {
      page: "chip", chip: r.chip, title: r.title, variant: r.variant, verdict: r.verdict, proof: r.proof, coverage: r.coverage,
      decompiled: r.decompiled.verilog,
      blocks: r.blocks.map((b) => ({ id: b.id, kind: b.kind, text: b.text, lines: b.lines, cells: b.cells.length, recognised: b.recognised })),
      flags: r.flags, plant: r.plant ?? null,
      selected: { line: this.selLine, text: this.selLine ? lines[this.selLine - 1] : null, cell: this.selCell, cell_type: this.selCell ? r.netlist.cells.find((c) => c.id === this.selCell)?.type : null },
    };
  }
  highlightFromGuide(h: { cells?: string[]; lines?: number[] }) {
    const ids = new Set(h.cells ?? []);
    for (const ln of h.lines ?? []) for (const [c, l] of Object.entries(this.result?.cell_lines ?? {})) if (l === ln) ids.add(c);
    if (h.lines?.length) { this.selLine = h.lines[0]; this.renderCode(); }
    if (ids.size) { this.graph?.setHighlight(ids); this.graph?.focus([...ids]); }
  }
  commands() {
    const r = this.result;
    return [
      { label: "Chip: plant the judge's number and find it", key: "", run: () => { this.tab = "find"; this.renderSide(); $("#cpN", this.el)?.focus(); } },
      { label: "Chip: light the flagged cone", key: "", run: () => this.lightFlags() },
      { label: "Chip: remove and prove (Fix)", key: "", run: () => { this.tab = "fix"; this.renderSide(); } },
      ...(r ? r.decompiled.verilog.split("\n").map((t, i) => ({ label: `Decompiled line ${i + 1}: ${t.trim()}`, key: "", run: () => this.selectLine(i + 1) })).filter((x) => !x.label.endsWith(": ")) : []),
    ];
  }
  destroy() { this.graph?.destroy(); this.stopTimer(); }
}

/** Start a plant in the background (the demo prep calls this before the timer starts). */
export function startPlant(chip: string, n: number): Promise<ChipResult> {
  return apiPlant(chip, n).then(({ data, source }) => { plantSources.set(`${chip}:${n}`, source); return data; });
}
export const plantSources = new Map<string, Source>();
