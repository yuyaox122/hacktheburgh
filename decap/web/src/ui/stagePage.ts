// Explore page (route #/stage): a real chip in 3D (a Tiny Tapeout chip from its GDS, or the warm-up), its source,
// an inspector, and the live simulator.

import type { Design, DesignCell, IoField, Lib } from "../data/types";
import type { StageChip } from "../data/load";
import { Sim } from "../sim/sim";
import { Stage3D, LAYERS, type LayerKey } from "../view/stage3d";
import { usedArea } from "../view/geometry";
import { category, CATEGORY_LABEL, CATEGORY_VAR, describe, fullName, toWordsSafe } from "../view/describe";
import { parse, evalExpr, vars } from "../sim/expr";
import { $, $$, codeText, css, esc, wrapOn } from "./dom";
import { icon } from "./icons";
import { app } from "./state";
import * as stageAgent from "../agent/stageAgent";

const SPEEDS: [number, string][] = [[900, "0.5×"], [450, "1×"], [120, "4×"]];
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** One lane of the logic analyser: a single wire, or a named number drawn as a bus. */
interface Lane { label: string; bits: string[]; out: boolean }

export class StagePage {
  readonly el: HTMLElement;
  view: Stage3D | null = null;
  sim: Sim;
  private byId = new Map<string, DesignCell>();
  private drivers = new Map<string, string>();
  private sinks = new Map<string, { id: string; pin: string }[]>();
  private selected: string | null = null;
  private selLines = new Set<number>();
  private selModule: string | null = null;
  private inputs: string[];
  private clk: string | null;
  private outputs: string[];
  private running = false;
  private timer = 0;
  private speed = 450;
  private history: Record<string, number>[] = [];
  private shiftQueue: Record<string, number>[] = [];
  private buses: { name: string; bits: string[] }[] = [];
  private srcFile: string;
  private srcLines: string[];
  /** Named numbers from the chip's pinout (Tiny Tapeout): the sim bar shows these instead of one button per pin. */
  private io: { in: IoField[]; out: IoField[] } | null;
  private waveSigs: string[];
  private lanes: Lane[];

  constructor(private design: Design, private lib: Lib, private chips: StageChip[] = []) {
    for (const c of design.cells) {
      this.byId.set(c.id, c);
      const L = lib[c.type];
      for (const [p, n] of Object.entries(c.pins)) {
        if (L?.pins[p] === "out") this.drivers.set(n, c.id);
        else { if (!this.sinks.has(n)) this.sinks.set(n, []); this.sinks.get(n)!.push({ id: c.id, pin: p }); }
      }
    }
    this.sim = new Sim(design.cells, lib);
    this.inputs = design.ports.filter((p) => p.dir === "in").map((p) => p.name);
    this.outputs = design.ports.filter((p) => p.dir === "out").map((p) => p.name);
    this.clk = this.inputs.find((n) => /^clk$/i.test(n)) ?? null;
    for (const n of new Set([...this.inputs, ...this.sim.names])) if (/^(rst_?n|en|ena)$/i.test(n)) this.sim.set(n, 1);
    this.sim.settle();
    // Named internal buses (a_reg[7:0], sum[8:0], ...) for the readout.
    const groups = new Map<string, number[]>();
    for (const n of this.sim.names) {
      const m = /^([A-Za-z_]\w*)\[(\d+)\]$/.exec(n);
      if (m) { if (!groups.has(m[1])) groups.set(m[1], []); groups.get(m[1])!.push(+m[2]); }
    }
    this.buses = [...groups].filter(([, b]) => b.length >= 2).slice(0, 4).map(([name, b]) => ({ name, bits: b.sort((x, y) => x - y).map((i) => `${name}[${i}]`) }));
    this.io = design.io && (design.io.in.length || design.io.out.length) ? design.io : null;
    if (this.io) {
      this.lanes = [...this.io.in.map((f) => ({ label: f.name, bits: f.bits, out: false })), ...this.io.out.map((f) => ({ label: f.name, bits: f.bits, out: true }))];
    } else {
      this.lanes = [...this.inputs.filter((x) => x !== this.clk).map((n) => ({ label: n, bits: [n], out: false })), ...this.outputs.map((n) => ({ label: n, bits: [n], out: true }))];
    }
    this.waveSigs = [...new Set(this.lanes.flatMap((l) => l.bits))];
    this.srcFile = Object.keys(design.source.files)[0] ?? "";
    this.srcLines = (design.source.files[this.srcFile] ?? "").split("\n");

    const many = this.chips.length > 1;
    this.el = document.createElement("div");
    this.el.className = "stage";
    this.el.innerHTML = `
      <aside class="pane code-pane" aria-label="Source code">
        <div class="chiphdr">
          ${many ? `<select class="sel" id="stChip" aria-label="Chip">${this.chips.map((c) =>
            `<option value="${esc(c.chip)}" ${c.chip === design.name ? "selected" : ""}>${esc(c.title || c.chip)}</option>`).join("")}</select>`
            : `<h3>${esc(design.title ?? design.name)}</h3>`}
          <div class="meta">${icon.code}<span class="file" title="${esc(this.srcFile)}">${esc(this.srcFile || "No source")}</span>
            ${design.source.mapping ? `<span title="${esc(design.source.mapping)}">${icon.info}</span>` : ""}
            <button class="btn plain icon" type="button" data-wrap aria-pressed="${wrapOn()}" title="Wrap long lines (W)" aria-label="Wrap long lines">${icon.wrap}</button>
            ${many ? `<a class="btn sm" href="#/chip/${encodeURIComponent(design.name)}" title="Read this chip back as Verilog, with its proof">Decompile</a>` : ""}</div>
        </div>
        <div class="pane-b"><ol class="code click" id="stSrc"></ol></div>
        <div class="hint">Click a line to light its cells.</div>
        <div class="resizer" id="stResize" title="Drag to resize, double-click to reset" aria-hidden="true"></div>
      </aside>
      <main class="viewport" id="stView" aria-label="3D chip view">
        <div class="overlay legend" id="stLegend"></div>
        <div class="overlay popover" id="stLayersB" hidden>
          <h5>Layers</h5>
          ${LAYERS.map((L) => `<label class="row"><i class="sw" style="background:${L.colour}"></i>${esc(L.label)}<input class="switch" type="checkbox" data-layer="${L.key}" ${L.on ? "checked" : ""}></label>`).join("")}
          <hr>
          <div class="slider"><span><label for="stExplode">Separate layers</label><kbd>X</kbd></span><input type="range" id="stExplode" min="0" max="100" value="0"></div>
          <label class="row">Bloom<input class="switch" type="checkbox" id="stHQ" checked></label>
        </div>
        <div class="overlay vbar" role="toolbar" aria-label="View">
          <button class="btn" type="button" id="stLayersT" aria-expanded="false" title="Layers">${icon.layers}Layers</button>
          <button class="btn" type="button" id="stExplodeT" title="Separate the layers (X)">${icon.explode}Separate</button>
          <button class="btn" type="button" id="stFit" title="Fit the logic in view (F)">${icon.fit}Fit</button>
          <span class="sep"></span>
          <div class="seg" role="group" aria-label="Colour cells by"><button type="button" data-colour="function" aria-pressed="true">Function</button><button type="button" data-colour="module" aria-pressed="false">Module</button></div>
        </div>
      </main>
      <aside class="pane right" aria-label="Inspector">
        <div class="pane-h"><span id="stInspH">About this chip</span><span class="grow"></span><button class="btn plain icon" type="button" id="stClear" title="Clear selection (Esc)" aria-label="Clear selection" hidden>${icon.close}</button></div>
        <div class="pane-b"><div class="sect insp" id="stInsp"></div></div>
      </aside>
      <footer class="sim" aria-label="Simulator">
        <section>
          <span class="eyebrow">Clock <span class="cycle" id="stCycle">0</span></span>
          <div class="ctrls">
            <button class="btn icon" id="stReset" type="button" title="Reset (R)" aria-label="Reset">${icon.reset}</button>
            <button class="playbtn" id="stRun" type="button" title="Run or pause (Space)" aria-label="Run">${icon.play}</button>
            <button class="btn icon" id="stStep" type="button" title="One clock (.)" aria-label="Step one clock">${icon.step}</button>
            <div class="seg" role="group" aria-label="Clock speed" id="stSpeed">${SPEEDS.map(([v, l]) => `<button type="button" data-speed="${v}" aria-pressed="${v === this.speed}">${l}</button>`).join("")}</div>
          </div>
        </section>
        <section>
          <span class="eyebrow">Inputs</span>
          <div class="ins" id="stIns" aria-label="Inputs"></div>
          <div class="shiftin" ${this.io ? "hidden" : ""} title="Shift two 8-bit numbers into the chip's shift registers, most significant bit first">
            <input class="num" id="stA" type="number" min="0" max="255" value="250" aria-label="Number for A">
            <span class="muted">+</span>
            <input class="num" id="stB" type="number" min="0" max="255" value="246" aria-label="Number for B">
            <button class="btn sm" id="stShift" type="button">Shift in</button>
          </div>
        </section>
        <section>
          <span class="eyebrow">Outputs</span>
          <div class="readout" id="stRead"></div>
        </section>
        <section>
          <span class="eyebrow">Waveform <span class="cycle muted" style="color:var(--faint)">last 40 clocks</span></span>
          <canvas class="wave" id="stWave" aria-label="Logic analyser: the last 40 clock cycles"></canvas>
        </section>
      </footer>`;
  }

  mount() {
    const host = $("#stView", this.el);
    if (!this.view) {
      this.view = new Stage3D(host, this.design, this.lib, {
        onCell: (id) => this.selectCell(id, false),
        onNet: (n) => this.selectNet(n),
        onPort: (p) => this.selectPort(p),
        onPhys: (p) => this.showPhys(p),
        onHoverCell: (id, x, y) => stageAgent.hover(this.chipName, id, x, y),
      });
      this.bind();
      $<HTMLCanvasElement>("#stWave", this.el).style.height = `${Math.max(46, Math.min(112, this.lanes.length * 17))}px`;
      this.renderSource(); this.renderInsp(); this.renderInputs(); this.renderRead(); this.renderLegend();
      this.view.setValues((n) => this.sim.get(n));
      // Tiny Tapeout tiles are mostly filler: open on the logic, not the whole die.
      if (this.io) setTimeout(() => this.fit(), 400);
    }
    this.view.start();
  }
  unmount() { this.view?.stop(); this.pause(); stageAgent.hide(); }
  /** The chip on show (design.json name). */
  get chipName() { return this.design.name; }

  /** Witness agent results: tint their cells and list them in a legend over the 3D view. */
  setAgentMarks(marks: Map<string, string>, legend: { label: string; colour: string; cells: string[]; claim?: string | null }[]) {
    this.view?.setMarks(marks);
    const host = $("#stView", this.el);
    host.querySelector(".ag-legend")?.remove();
    if (!legend.length) return;
    const d = document.createElement("div");
    d.className = "ag-legend";
    d.innerHTML = `<b>Witness agent</b>${legend.map((l, i) => `<button type="button" data-ag-leg="${i}"><i style="background:${l.colour}"></i><span>${esc(l.label)}${l.claim ? `<br><code>${esc(l.claim)}</code>` : ""}</span></button>`).join("")}`;
    d.addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLElement>("[data-ag-leg]");
      if (!b) return;
      const ids = new Set(legend[+b.dataset.agLeg!].cells.filter((id) => this.byId.has(id)));
      if (ids.size) { this.view?.setHighlight(ids); this.view?.flyTo([...ids]); }
    });
    host.append(d);
  }
  /** Drop this Stage for good (switching chips): frees the 3D view's WebGL context. */
  destroy() { this.pause(); this.view?.dispose(); this.view = null; }
  /** Frame the logic (Tiny Tapeout chips) or the whole die (the warm-up). */
  fit() { this.view?.flyTo(this.io ? this.design.cells.map((c) => c.id) : null); }

  private bind() {
    const el = this.el;
    $$<HTMLInputElement>("[data-layer]", el).forEach((cb) => cb.addEventListener("change", () => this.view?.setLayer(cb.dataset.layer as LayerKey, cb.checked)));
    $<HTMLInputElement>("#stExplode", el).addEventListener("input", (e) => { this.view?.setExplode(+(e.target as HTMLInputElement).value / 100); this.syncExplode(); });
    const pop = $("#stLayersB", el), popBtn = $("#stLayersT", el);
    const setPop = (open: boolean) => { pop.hidden = !open; popBtn.setAttribute("aria-expanded", String(open)); popBtn.classList.toggle("on", open); };
    popBtn.addEventListener("click", (e) => { e.stopPropagation(); setPop(pop.hidden); });
    el.addEventListener("pointerdown", (e) => { if (!pop.hidden && !pop.contains(e.target as Node) && !popBtn.contains(e.target as Node)) setPop(false); });
    $("#stExplodeT", el).addEventListener("click", () => this.toggleExplode());
    $("#stFit", el).addEventListener("click", () => this.fit());
    $$<HTMLButtonElement>("[data-colour]", el).forEach((b) => b.addEventListener("click", () => {
      $$("[data-colour]", el).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      this.view?.setColourBy(b.dataset.colour as "function" | "module"); this.renderLegend();
    }));
    $<HTMLInputElement>("#stHQ", el).addEventListener("change", (e) => this.view?.setQuality((e.target as HTMLInputElement).checked));
    $("#stReset", el).addEventListener("click", () => this.reset());
    $("#stStep", el).addEventListener("click", () => { this.pause(); this.step(); });
    $("#stRun", el).addEventListener("click", () => (this.running ? this.pause() : this.run()));
    $$<HTMLButtonElement>("[data-speed]", el).forEach((b) => b.addEventListener("click", () => {
      this.speed = +b.dataset.speed!;
      $$("[data-speed]", el).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    }));
    $("#stShift", el).addEventListener("click", () => this.shiftIn());
    $("#stClear", el).addEventListener("click", () => this.selectCell(null));
    el.querySelector<HTMLSelectElement>("#stChip")?.addEventListener("change", (e) => { location.hash = `#/stage/${encodeURIComponent((e.target as HTMLSelectElement).value)}`; });
    $("#stSrc", el).addEventListener("click", (e) => {
      const li = (e.target as HTMLElement).closest<HTMLElement>("[data-ln]");
      if (li) this.selectLine(+li.dataset.ln!);
    });
    $("#stIns", el).addEventListener("input", (e) => {
      const f = (e.target as HTMLElement).closest<HTMLInputElement>("[data-io]");
      if (f) this.setField(f.dataset.io!, +f.value);
    });
    $("#stIns", el).addEventListener("click", (e) => {
      const b = (e.target as HTMLElement).closest<HTMLElement>("[data-in]");
      if (!b) return;
      const n = b.dataset.in!;
      this.sim.apply({ [n]: this.sim.get(n) ^ 1 });
      this.after();
    });
    new ResizeObserver(() => this.drawWave()).observe($("#stWave", el));
    this.bindResizer();
  }
  /** Drag the code pane's right edge to resize it (remembered per browser; double-click resets). */
  private bindResizer() {
    const KEY = "decap.codeW", bar = $("#stResize", this.el);
    const apply = (w: number | null) => {
      if (w == null) this.el.style.removeProperty("--code-w");
      else this.el.style.setProperty("--code-w", `${Math.round(Math.max(220, Math.min(w, innerWidth * 0.55)))}px`);
    };
    try { const w = Number(localStorage.getItem(KEY)); if (w) apply(w); } catch { /* storage blocked */ }
    bar.addEventListener("pointerdown", (e) => {
      e.preventDefault(); bar.setPointerCapture(e.pointerId); bar.classList.add("drag");
      const left = this.el.getBoundingClientRect().left;
      const move = (ev: PointerEvent) => apply(ev.clientX - left);
      const up = () => {
        bar.classList.remove("drag"); bar.removeEventListener("pointermove", move); bar.removeEventListener("pointerup", up);
        const w = parseFloat(this.el.style.getPropertyValue("--code-w"));
        try { if (w) localStorage.setItem(KEY, String(w)); } catch { /* storage blocked */ }
      };
      bar.addEventListener("pointermove", move); bar.addEventListener("pointerup", up);
    });
    bar.addEventListener("dblclick", () => { apply(null); try { localStorage.removeItem(KEY); } catch { /* storage blocked */ } });
  }

  // ---------- simulation ----------
  private after() {
    this.view?.setValues((n) => this.sim.get(n));
    this.renderInputs(); this.renderRead(); this.drawWave();
    if (this.selected) this.renderInsp();
  }
  step(extra: Record<string, number> = {}) {
    const q = this.shiftQueue.shift();
    const ins = { ...(q ?? {}), ...extra };
    for (const [k, v] of Object.entries(ins)) this.sim.set(k, v);
    if (this.clk) this.sim.clock(this.clk); else this.sim.settle();
    const snap: Record<string, number> = {};
    for (const n of this.waveSigs) snap[n] = this.sim.get(n);
    this.history.push(snap); if (this.history.length > 40) this.history.shift();
    this.after();
    if (!this.shiftQueue.length && q && !this.running) this.renderRead();
  }
  private setRunBtn() {
    const b = this.el.querySelector<HTMLElement>("#stRun");
    if (!b) return;
    b.innerHTML = this.running ? icon.pause : icon.play;
    b.classList.toggle("on", this.running);
    b.setAttribute("aria-label", this.running ? "Pause" : "Run");
  }
  run() {
    if (this.running) return;
    this.running = true; this.setRunBtn();
    const loop = () => { if (!this.running) return; this.step(); this.timer = window.setTimeout(loop, this.speed); };
    loop();
  }
  pause() { this.running = false; clearTimeout(this.timer); this.setRunBtn(); }
  get isRunning() { return this.running; }
  reset() {
    const rst = this.inputs.find((n) => /^rst_?n$/i.test(n));
    this.pause(); this.shiftQueue = [];
    if (rst) { this.step({ [rst]: 0 }); this.sim.apply({ [rst]: 1 }); } else { this.sim.zero(); }
    this.sim.cycles = 0; this.history = [];
    this.after();
  }
  /** Set a named input number (from the pinout) on its pins. */
  setField(name: string, value: number) {
    const f = this.io?.in.find((x) => x.name === name);
    if (!f) return;
    const v = Math.max(0, Math.min((1 << f.bits.length) - 1, Math.floor(value) || 0));
    this.sim.apply(Object.fromEntries(f.bits.map((b, k) => [b, (v >> k) & 1])));
    this.after();
  }
  private fieldVal(f: { bits: string[] }) { return f.bits.reduce((s, n, i) => s + (this.sim.get(n) << i), 0); }
  /** Demo: a visible computation. Pinout chips: the first two inputs get 9 and 8, then one clock; the warm-up: shift in 250 + 246. */
  demoShow() {
    if (!this.io) { this.shiftIn(250, 246); return; }
    this.io.in.forEach((f, i) => this.setField(f.name, i === 0 ? 9 : i === 1 ? 8 : 0));
    this.pause(); this.step();
  }
  /** Shift a and b into the A/B serial inputs, MSB first (8 clocks), with en = 1. */
  shiftIn(a?: number, b?: number) {
    const A = a ?? +$<HTMLInputElement>("#stA", this.el).value, B = b ?? +$<HTMLInputElement>("#stB", this.el).value;
    const ina = this.inputs.find((n) => n === "A"), inb = this.inputs.find((n) => n === "B"), en = this.inputs.find((n) => /^en$/i.test(n));
    if (!ina || !inb) return;
    this.shiftQueue = [];
    for (let i = 7; i >= 0; i--) this.shiftQueue.push({ [ina]: (A >> i) & 1, [inb]: (B >> i) & 1, ...(en ? { [en]: 1 } : {}) });
    if (!this.running) this.run();
    const stopAfter = () => { if (this.shiftQueue.length) setTimeout(stopAfter, this.speed); else setTimeout(() => this.pause(), this.speed * 0.5); };
    stopAfter();
  }

  // ---------- selection ----------
  /** Inspector heading and the clear button. */
  private head(title: string | null) {
    $("#stInspH", this.el).textContent = title ?? "About this chip";
    $("#stClear", this.el).hidden = !title;
  }
  selectCell(id: string | null, fly = true) {
    this.selected = id; this.selLines.clear(); this.selModule = null;
    if (!id) { this.view?.select(null); this.view?.setHighlight(null); this.renderInsp(); this.renderSource(); return; }
    const c = this.byId.get(id);
    if (!c) return;
    const nb = new Set([id]);
    const L = this.lib[c.type];
    for (const [p, n] of Object.entries(c.pins)) {
      if (L?.pins[p] === "out") for (const s of this.sinks.get(n) ?? []) nb.add(s.id);
      else { const d = this.drivers.get(n); if (d) nb.add(d); }
    }
    this.view?.select(id);
    this.view?.setHighlight(nb, new Set(Object.values(c.pins)));
    if (c.src) this.selLines.add(c.src);
    this.selModule = c.module;
    this.renderInsp(); this.renderSource(true);
    if (fly) this.view?.flyTo([id]);
  }
  selectLine(ln: number) {
    const ids = this.design.cells.filter((c) => c.src === ln).map((c) => c.id);
    this.selected = null; this.selLines = new Set([ln]);
    const mod = Object.entries(this.design.source.modules).filter(([, m]) => m.line <= ln).sort((a, b) => b[1].line - a[1].line)[0]?.[0] ?? null;
    this.selModule = mod;
    const set = new Set(ids.length ? ids : this.design.cells.filter((c) => c.module === mod).map((c) => c.id));
    this.view?.select(null); this.view?.setHighlight(set);
    if (set.size) this.view?.flyTo([...set]);
    this.renderSource();
    const insts = Object.entries(this.design.source.instances ?? {}).filter(([, m]) => m === mod).map(([i]) => i);
    this.head(`Line ${ln}`);
    $("#stInsp", this.el).innerHTML = `
      <div class="insp-title"><div><b class="mono">${esc(this.srcLines[ln - 1]?.trim() || `Line ${ln}`)}</b><small>${esc(this.srcFile)}, line ${ln}</small></div><span class="pill gold">${set.size} cell${set.size === 1 ? "" : "s"}</span></div>
      <p class="insp-role">${ids.length ? `After synthesis, this line became the ${ids.length === 1 ? "cell" : `${ids.length} cells`} lit in the view.` : mod ? `Part of module <b>${esc(mod)}</b>${insts.length ? `, used ${insts.length === 1 ? "once" : `${insts.length} times`}` : ""}. Its cells are lit in the view.` : "No cells come from this line."}</p>
      ${this.cellLinks([...set])}`;
    this.bindLinks();
  }
  selectNet(net: string) {
    this.selected = null;
    const d = this.drivers.get(net), s = this.sinks.get(net) ?? [];
    const ids = new Set([...(d ? [d] : []), ...s.map((x) => x.id)]);
    this.view?.select(null); this.view?.setHighlight(ids, new Set([net]));
    const v = this.sim.get(net);
    this.head("Wire");
    $("#stInsp", this.el).innerHTML = `
      <div class="insp-title"><div><b class="mono">${esc(net)}</b><small>Read by ${s.length} cell${s.length === 1 ? "" : "s"}</small></div><span class="bit${v ? " on" : ""}">${v}</span></div>
      <p class="insp-role">A wire carries one bit from the cell that drives it to every cell that reads it.</p>
      <dl class="kv"><dt>Driven by</dt><dd>${d ? `<button type="button" class="linkbtn" data-cell="${esc(d)}">${esc(d)}</button>` : this.inputs.includes(net) ? "Input pin" : "Nothing"}</dd></dl>
      ${s.length ? `<div class="group"><span class="eyebrow">Read by</span><div class="chips">${s.map((x) => `<button type="button" class="linkbtn" data-cell="${esc(x.id)}">${esc(x.id)}.${esc(x.pin)}</button>`).join("")}</div></div>` : ""}`;
    this.bindLinks();
  }
  selectPort(name: string) {
    const p = this.design.ports.find((x) => x.name === name);
    if (!p) return;
    const ids = p.dir === "out" ? this.fanIn(name) : this.fanOut(name);
    this.selected = null;
    this.view?.select(null); this.view?.setHighlight(ids);
    if (ids.size) this.view?.flyTo([...ids]);
    const v = this.sim.get(name);
    this.head(p.dir === "out" ? "Output pin" : "Input pin");
    $("#stInsp", this.el).innerHTML = `
      <div class="insp-title"><div><b class="mono">${esc(name)}</b><small>${p.dir === "out" ? "Output" : "Input"} at ${p.x.toFixed(1)}, ${p.y.toFixed(1)} µm</small></div><span class="bit${v ? " on" : ""}">${v}</span></div>
      <p class="insp-role">${p.dir === "out" ? `Lit: every cell that can change ${esc(name)}, back to the flip-flops. Like Go to Definition, for silicon.` : `Lit: every cell ${esc(name)} can reach before the next flip-flop. Like Find Usages, for silicon.`}</p>
      ${p.dir === "in" && name !== this.clk ? `<div class="acts"><button class="btn sm" type="button" data-toggle="${esc(name)}">Set to ${v ? 0 : 1}</button></div>` : ""}
      ${this.cellLinks([...ids])}`;
    this.bindLinks();
    $$("[data-toggle]", this.el).forEach((b) => b.addEventListener("click", () => { this.sim.apply({ [name]: this.sim.get(name) ^ 1 }); this.after(); this.selectPort(name); }));
  }
  private fanIn(net: string): Set<string> {
    const out = new Set<string>(), stack = [net];
    while (stack.length) {
      const n = stack.pop()!, id = this.drivers.get(n);
      if (!id || out.has(id)) continue;
      out.add(id);
      const c = this.byId.get(id)!, L = this.lib[c.type];
      if (L?.ff) continue; // stop at flip-flops
      for (const [p, m] of Object.entries(c.pins)) if (L?.pins[p] === "in") stack.push(m);
    }
    return out;
  }
  private fanOut(net: string): Set<string> {
    const out = new Set<string>(), stack = [net];
    while (stack.length) {
      const n = stack.pop()!;
      for (const s of this.sinks.get(n) ?? []) {
        if (out.has(s.id)) continue;
        out.add(s.id);
        const c = this.byId.get(s.id)!, L = this.lib[c.type];
        if (L?.ff) continue;
        for (const [p, m] of Object.entries(c.pins)) if (L?.pins[p] === "out") stack.push(m);
      }
    }
    return out;
  }
  private showPhys(p: Design["phys"][number]) {
    this.selected = null; this.view?.select(null); this.view?.setHighlight(null);
    this.head("Physical cell");
    $("#stInsp", this.el).innerHTML = `<div class="insp-title"><div><b class="mono">${esc(p.type)}</b><small>${esc(fullName(p.type))}</small></div><span class="pill">No logic</span></div>
      <p class="insp-role">${esc(describe(p.type, this.lib[p.type]))}</p>
      <dl class="kv"><dt>Position</dt><dd>${p.x.toFixed(2)}, ${p.y.toFixed(2)} µm</dd><dt>Width</dt><dd>${p.w.toFixed(2)} µm</dd></dl>`;
  }

  // ---------- rendering ----------
  private cellLinks(ids: string[]) {
    if (!ids.length) return "";
    return `<div class="group"><span class="eyebrow">Cells</span><div class="chips">${ids.slice(0, 80).map((id) => `<button type="button" class="linkbtn" data-cell="${esc(id)}">${esc(id)}</button>`).join("")}${ids.length > 80 ? `<span class="muted">and ${ids.length - 80} more</span>` : ""}</div></div>`;
  }
  private bindLinks() {
    const root = $("#stInsp", this.el);
    $$<HTMLElement>("[data-cell]", root).forEach((b) => b.addEventListener("click", () => this.selectCell(b.dataset.cell!)));
    $$<HTMLElement>("[data-net]", root).forEach((b) => b.addEventListener("click", () => this.selectNet(b.dataset.net!)));
    $$<HTMLElement>("[data-ln]", root).forEach((b) => b.addEventListener("click", () => this.selectLine(+b.dataset.ln!)));
    $$<HTMLElement>("[data-cone]", root).forEach((b) => b.addEventListener("click", () => {
      const ids = b.dataset.cone === "in" ? this.fanIn(b.dataset.net!) : this.fanOut(b.dataset.net!);
      this.view?.setHighlight(ids); this.view?.flyTo([...ids]);
    }));
    $$<HTMLElement>("[data-ask]", root).forEach((b) => b.addEventListener("click", () => app.askGuide?.(b.dataset.ask!)));
  }

  private renderSource(scroll = false) {
    const mod = this.selModule ? this.design.source.modules[this.selModule] : null;
    const modEnd = mod ? (this.srcLines.findIndex((l, i) => i >= mod.line - 1 && /^\s*endmodule/.test(l)) + 1) : 0;
    $("#stSrc", this.el).innerHTML = this.srcLines.map((t, i) => {
      const ln = i + 1;
      const cls = [this.selLines.has(ln) ? "active" : "", mod && ln >= mod.line && ln <= modEnd ? "mod" : ""].join(" ");
      const count = this.design.cells.filter((c) => c.src === ln).length;
      return `<li data-ln="${ln}" class="${cls}"><span class="n">${ln}</span><span class="bk"></span>${codeText(t)}${count ? `<span class="badge" title="${count} cell${count === 1 ? "" : "s"}">${count}</span>` : "<span></span>"}</li>`;
    }).join("");
    if (scroll) $("#stSrc li.active", this.el)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  private renderInsp() {
    const el = $("#stInsp", this.el), id = this.selected;
    if (!id) {
      this.head(null);
      const d = this.design;
      el.innerHTML = `
        <div class="insp-title"><div><b>${esc(d.title ?? d.name)}</b><small>${d.author ? `By ${esc(d.author)}. ` : ""}Tiny Tapeout 8, SkyWater 130 nm</small></div></div>
        ${d.how ? `<div class="group"><span class="eyebrow">How it works</span><p class="insp-role" style="color:var(--fg)">${esc(d.how)}</p></div>`
          : `<p class="insp-role">${esc(d.intro ?? "A real chip: the warm-up design, placed and routed for SkyWater's 130 nm process. Every coloured block is a standard cell, a tiny pre-made circuit.")}</p>`}
        ${d.try ? `<div class="callout"><b>Try it</b><span>${esc(d.try)}</span></div>` : ""}
        <div class="statgrid">
          <div class="stat"><b>${this.logicCount}</b><span>Logic cells</span></div>
          <div class="stat"><b>${this.sim.flops.length}</b><span>Flip-flops</span></div>
          <div class="stat"><b>${this.design.counts?.nets ?? this.design.counts?.nets_on_wires ?? "?"}</b><span>Wires</span></div>
          <div class="stat"><b class="area">${this.usedSize}<small>µm</small></b><span>Logic area</span></div>
        </div>
        <dl class="kv">
          <dt>Process</dt><dd>${esc(d.pdk)}</dd>
          <dt>Cell types</dt><dd>${new Set(d.cells.map((c) => c.type)).size}</dd>
          <dt>Tile</dt><dd>${d.die.w} × ${d.die.h} µm</dd>
          <dt>Filler and taps</dt><dd>${d.phys.length} cells</dd>
          <dt>Routing</dt><dd>${d.counts?.route_segments ?? "?"} segments, ${d.counts?.vias ?? "?"} vias</dd>
        </dl>
        <p class="fine">${d.how ? "Every coloured block in the view is a standard cell, a tiny pre-made circuit, and the metal above it is the real wiring. Select a cell, wire or pin to inspect it." : d.hint ? esc(d.hint) : "Select a cell, wire or pin to inspect it. Press Run to clock the chip; wires carrying a 1 glow."}</p>
        <p class="fine mono">${esc(d.name)}</p>
        ${d.credit ? `<p class="fine">${esc(d.credit)}</p>` : ""}`;
      return;
    }
    const c = this.byId.get(id)!, L = this.lib[c.type];
    const cat = category(c.type, L);
    this.head(cap(CATEGORY_LABEL[cat].replace(/ \(.*\)$/, "")));
    const pinRows = Object.entries(c.pins).map(([p, n]) => {
      const dir = L?.pins[p] ?? "?";
      const v = this.sim.get(n);
      return `<tr><td>${esc(p)}${dir === "out" ? `<span class="dir">out</span>` : ""}</td><td><button type="button" class="linkbtn" data-net="${esc(n)}">${esc(n)}</button></td><td><span class="bit${v ? " on" : ""}">${v}</span></td></tr>`;
    }).join("");
    const fns = L ? Object.entries(L.function) : [];
    const modInfo = c.module ? this.design.source.modules[c.module] : null;
    const srcLine = c.src ? this.srcLines[c.src - 1]?.trim() : null;
    const out = Object.entries(c.pins).find(([p]) => L?.pins[p] === "out");
    el.innerHTML = `
      <div class="insp-title"><div><b class="mono">${esc(c.id)}</b><small>${esc(fullName(c.type))}</small></div><span class="pill" style="color:var(${CATEGORY_VAR[cat]})"><i class="sw" style="width:6px;height:6px;border-radius:50%;background:currentColor"></i>${esc(cap(CATEGORY_LABEL[cat]))}</span></div>
      <p class="insp-role">${esc(describe(c.type, L))}</p>
      ${fns.length ? `<div class="fnbox">${fns.map(([o, f]) => L?.ff && /^IQ(_N)?$/.test(f) ? `<div>${esc(o)} = ${f === "IQ" ? "stored bit" : "NOT stored bit"}</div>` : `<div>${esc(o)} = ${esc(f)}</div><span>${esc(toWordsSafe(f))}</span>`).join("")}</div>` : ""}
      <dl class="kv">
        ${L?.ff ? `<dt>Stores</dt><dd>${this.sim.flopState(c.id)}<span class="muted">Next ${esc(L.ff.next_state)} on ${esc(L.ff.clocked_on)} rising${L.ff.clear ? `, cleared by ${esc(L.ff.clear)}` : ""}</span></dd>` : ""}
        <dt>Module</dt><dd>${c.module ? `${esc(c.module)}${c.inst ? `<span class="muted">Instance ${esc(c.inst)}</span>` : ""}` : "None"}</dd>
        <dt>Source</dt><dd>${c.src ? `<button type="button" class="linkbtn" data-ln="${c.src}">Line ${c.src}</button><span class="muted mono">${esc(srcLine ?? "")}</span>` : modInfo ? `<span class="muted">Added by the layout tools (clock tree)</span>` : "None"}</dd>
        <dt>Size</dt><dd>${c.w.toFixed(2)} × ${this.design.rowHeight} µm${L?.area ? `<span class="muted">${L.area.toFixed(2)} µm²</span>` : ""}</dd>
        <dt>Position</dt><dd>${c.x.toFixed(2)}, ${c.y.toFixed(2)} µm<span class="muted">${esc(c.orient)}</span></dd>
      </dl>
      <div class="group"><span class="eyebrow">Pins</span><table class="pins"><tr><th>Pin</th><th>Wire</th><th>Now</th></tr>${pinRows}</table></div>
      ${this.truthTable(c)}
      <div class="acts">
        ${out ? `<button type="button" class="btn sm" data-cone="in" data-net="${esc(out[1])}" title="Every cell that can change this one">${icon.arrowIn}Fan-in</button><button type="button" class="btn sm" data-cone="out" data-net="${esc(out[1])}" title="Every cell this one can reach">Fan-out${icon.arrowOut}</button>` : ""}
        <button type="button" class="btn sm" data-agent-cell="${esc(c.id)}" title="The Witness agent explains the block this cell belongs to; Yosys checks it">${icon.cube}Explain block</button>
        <button type="button" class="btn sm" data-ask="Explain cell ${esc(c.id)} (${esc(c.type)}) in simple terms: what it does and why this chip needs it.">${icon.sparkle}Ask</button>
      </div>`;
    this.bindLinks();
  }

  private truthTable(c: DesignCell): string {
    const L = this.lib[c.type];
    if (!L || L.ff) return "";
    const outs = Object.entries(L.function);
    if (outs.length !== 1) return "";
    const [o, f] = outs[0];
    let e; try { e = parse(f); } catch { return ""; }
    const ins = [...vars(e)].sort();
    if (!ins.length || ins.length > 4) return "";
    const now = ins.map((p) => this.sim.get(c.pins[p] ?? ""));
    const rows: string[] = [];
    for (let s = 0; s < 1 << ins.length; s++) {
      const b = ins.map((_, i) => (s >> (ins.length - 1 - i)) & 1);
      const env = Object.fromEntries(ins.map((p, i) => [p, b[i]]));
      const cur = b.every((x, i) => x === now[i]);
      rows.push(`<tr class="${cur ? "now" : ""}">${b.map((x) => `<td>${x}</td>`).join("")}<td class="out">${evalExpr(e, env)}</td></tr>`);
    }
    return `<div class="group"><span class="eyebrow">Truth table <span style="font-weight:400;color:var(--faint)">Highlighted row is live</span></span><table class="tt"><tr>${ins.map((p) => `<th>${esc(p)}</th>`).join("")}<th>${esc(o)}</th></tr>${rows.join("")}</table></div>`;
  }

  private get logicCount() { return Number(this.design.counts?.logic_cells ?? this.design.cells.length); }
  private renderInputs() {
    const host = $("#stIns", this.el);
    if (this.io) {
      if (!host.querySelector("[data-io]")) {
        host.innerHTML = this.io.in.map((f) => `<label class="field" title="${esc(f.name)}: ${f.bits.length} bit${f.bits.length === 1 ? "" : "s"}, 0 to ${(1 << f.bits.length) - 1}">${esc(f.name)}
          <input class="num" type="number" min="0" max="${(1 << f.bits.length) - 1}" data-io="${esc(f.name)}" value="0" aria-label="Input ${esc(f.name)}"></label>`).join("") || `<span class="muted">None</span>`;
      }
      host.querySelectorAll<HTMLInputElement>("[data-io]").forEach((inp) => {
        const f = this.io!.in.find((x) => x.name === inp.dataset.io);
        if (f && document.activeElement !== inp) inp.value = String(this.fieldVal(f));
      });
      return;
    }
    host.innerHTML = this.inputs.filter((n) => n !== this.clk).map((n) => {
      const v = this.sim.get(n);
      return `<button type="button" class="inbtn${v ? " on" : ""}" data-in="${esc(n)}" title="Toggle ${esc(n)}">${esc(n)} <span class="bit${v ? " on" : ""}">${v}</span></button>`;
    }).join("");
  }
  private renderRead() {
    const busVal = (b: { bits: string[] }) => this.fieldVal(b);
    $("#stCycle", this.el).textContent = `${this.sim.cycles}${this.shiftQueue.length ? `, shifting ${this.shiftQueue.length}` : ""}`;
    if (this.io) {
      $("#stRead", this.el).innerHTML = this.io.out.map((f) => {
        const v = this.fieldVal(f);
        return `<div class="bus"><small>${esc(f.name)}</small><span class="big">${v}${f.bits.length > 1 ? `<span class="bin">${v.toString(2).padStart(f.bits.length, "0")}</span>` : ""}</span></div>`;
      }).join("") || `<span class="muted">None</span>`;
      return;
    }
    $("#stRead", this.el).innerHTML = [
      ...this.outputs.map((o) => `<div class="bus"><small>${esc(o)}</small><span class="big">${this.sim.get(o)}</span></div>`),
      ...this.buses.map((b) => `<div class="bus"><small>${esc(b.name)}</small><span class="mid">${busVal(b)} <span class="muted">${busVal(b).toString(2).padStart(b.bits.length, "0")}</span></span></div>`),
    ].join("");
  }
  /** Width x height of the part of the tile the design uses (what the 3D view draws). */
  private get usedSize() {
    const v = usedArea(this.design);
    return `${(v.x1 - v.x0).toFixed(0)} × ${(v.y1 - v.y0).toFixed(0)}`;
  }
  private renderLegend() {
    const byModule = $("[data-colour='module']", this.el)?.getAttribute("aria-pressed") === "true";
    if (byModule && this.view) {
      const mods = [...new Set(this.design.cells.map((c) => c.module ?? "?"))];
      $("#stLegend", this.el).innerHTML = mods.map((m) => `<span><i class="sw" style="background:${this.view!.moduleColour(m)}"></i>${esc(m === this.design.source.top ? "Clock tree" : m)}</span>`).join("");
      return;
    }
    const cats = [...new Set(this.design.cells.map((c) => category(c.type, this.lib[c.type])))];
    $("#stLegend", this.el).innerHTML = cats.map((k) => `<span><i class="sw" style="background:var(${CATEGORY_VAR[k]})"></i>${esc(cap(CATEGORY_LABEL[k].replace(/ \(.*\)$/, "")))}</span>`).join("") +
      `<span><i class="sw" style="background:var(--high)"></i>Wire at 1</span>`;
  }
  /** Logic analyser: one lane per named number (a bus with its value written in) or per single wire (a square wave). */
  private drawWave() {
    const cv = this.el.querySelector<HTMLCanvasElement>("#stWave");
    if (!cv) return;
    const dpr = Math.min(devicePixelRatio, 2), w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) return;
    cv.width = w * dpr; cv.height = h * dpr;
    const g = cv.getContext("2d")!;
    g.scale(dpr, dpr); g.clearRect(0, 0, w, h);
    const lanes = this.lanes, n = 40;
    if (!lanes.length) return;
    const font = css("--font-body") || "system-ui";
    g.font = `500 10.5px ${font}`;
    const lw = Math.min(110, Math.max(36, ...lanes.map((l) => g.measureText(l.label).width + 12)));
    const rowH = h / lanes.length, step = (w - lw) / n;
    const hist = this.history, first = n - hist.length;
    const inCol = "rgba(235,235,245,0.55)", outCol = css("--high") || "#ffd60a", grid = "rgba(255,255,255,0.05)";
    // faint clock grid
    g.strokeStyle = grid; g.lineWidth = 1; g.beginPath();
    for (let i = 0; i <= n; i += 5) { const x = Math.round(lw + i * step) + 0.5; g.moveTo(x, 0); g.lineTo(x, h); }
    g.stroke();
    lanes.forEach((lane, r) => {
      const y0 = r * rowH, pad = Math.min(4, rowH * 0.18), hi = y0 + pad, lo = y0 + rowH - pad, mid = (hi + lo) / 2;
      const col = lane.out ? outCol : inCol;
      g.fillStyle = lane.out ? "rgba(245,245,247,0.9)" : "rgba(235,235,245,0.6)";
      g.font = `500 10.5px ${font}`; g.textBaseline = "middle";
      g.fillText(lane.label, 0, mid, lw - 8);
      if (!hist.length) return;
      g.strokeStyle = col; g.lineWidth = 1.3;
      if (lane.bits.length === 1) {
        g.beginPath();
        let prevY: number | null = null;
        hist.forEach((snap, i) => {
          const x = lw + (first + i) * step, y = snap[lane.bits[0]] ? hi : lo;
          if (prevY === null) g.moveTo(x, y); else { g.lineTo(x, prevY); g.lineTo(x, y); }
          g.lineTo(x + step, y); prevY = y;
        });
        g.stroke();
        return;
      }
      // bus: runs of equal value, drawn as a lane with crossings at each change and the value written inside
      const vals = hist.map((s) => lane.bits.reduce((acc, b, k) => acc + ((s[b] ?? 0) << k), 0));
      const runs: { v: number; a: number; b: number }[] = [];
      vals.forEach((v, i) => { const last = runs[runs.length - 1]; if (last && last.v === v) last.b = i + 1; else runs.push({ v, a: i, b: i + 1 }); });
      const k = Math.min(3, step / 2);
      g.font = `11px ${css("--font-mono") || "monospace"}`; g.textAlign = "center";
      for (const run of runs) {
        const xa = lw + (first + run.a) * step, xb = lw + (first + run.b) * step;
        g.beginPath();
        g.moveTo(xa, mid); g.lineTo(xa + k, hi); g.lineTo(xb - k, hi); g.lineTo(xb, mid); g.lineTo(xb - k, lo); g.lineTo(xa + k, lo); g.closePath();
        g.fillStyle = lane.out ? "rgba(255,214,10,0.12)" : "rgba(235,235,245,0.06)"; g.fill(); g.stroke();
        const label = String(run.v);
        if (xb - xa - 2 * k > g.measureText(label).width + 4) { g.fillStyle = lane.out ? outCol : "rgba(245,245,247,0.85)"; g.fillText(label, (xa + xb) / 2, mid + 0.5); }
      }
      g.textAlign = "start";
    });
  }

  /** Demo helper: the first cell whose type matches. */
  findCell(re: RegExp): string | null { return this.design.cells.find((c) => re.test(c.type))?.id ?? null; }

  /** Commands for the palette. */
  commands() {
    return [
      { label: "Run or pause the clock", key: "Space", run: () => (this.running ? this.pause() : this.run()) },
      { label: "Step one clock", key: ".", run: () => { this.pause(); this.step(); } },
      { label: "Reset the chip", key: "R", run: () => this.reset() },
      this.io ? { label: "Set the inputs to 9 and 8 and clock once", key: "", run: () => this.demoShow() }
        : { label: "Shift in 250 and 246 (S lights at 496)", key: "", run: () => this.shiftIn(250, 246) },
      { label: "Separate the layers", key: "X", run: () => this.toggleExplode() },
      { label: "Fit the chip in view", key: "F", run: () => this.fit() },
      ...this.chips.filter((c) => c.chip !== this.design.name).map((c) => ({ label: `Explore ${c.title || c.chip}`, key: `${c.logic_cells} cells`, run: () => { location.hash = `#/stage/${encodeURIComponent(c.chip)}`; } })),
      ...this.design.cells.map((c) => ({ label: `Go to cell ${c.id}`, key: c.type, run: () => this.selectCell(c.id) })),
      ...this.srcLines.map((t, i) => ({ label: `Go to line ${i + 1}: ${t.trim()}`, key: "", run: () => this.selectLine(i + 1) })).filter((x) => !x.label.endsWith(": ")),
    ];
  }
  private syncExplode() { this.el.querySelector("#stExplodeT")?.classList.toggle("on", !!this.view && this.view.exploded > 0.05); }
  toggleExplode() {
    const x = this.view && this.view.exploded > 0.5 ? 0 : 0.85;
    this.view?.setExplode(x); $<HTMLInputElement>("#stExplode", this.el).value = String(Math.round(x * 100));
    this.syncExplode();
  }
  /** Context for the AI guide. */
  context() {
    const c = this.selected ? this.byId.get(this.selected) : null;
    return {
      page: "stage", design: this.design.name, credit: this.design.credit,
      selected: c ? { id: c.id, type: c.type, function: this.lib[c.type]?.function, pins: Object.fromEntries(Object.entries(c.pins).map(([p, n]) => [p, { net: n, value: this.sim.get(n) }])), module: c.module, src: c.src } : null,
      // a clicked source line is a selection too ("Explain the selected cell" after clicking line 42 got nothing)
      selectedLines: [...this.selLines].slice(0, 8).map((ln) => ({ line: ln, text: (this.srcLines[ln - 1] ?? "").trim().slice(0, 300),
        cells: this.design.cells.filter((x) => x.src === ln).length })),
      modules: this.design.source.modules, outputs: Object.fromEntries(this.outputs.map((o) => [o, this.sim.get(o)])), cycle: this.sim.cycles,
    };
  }
  highlightFromGuide(cells: string[]) {
    const ids = new Set(cells.filter((id) => this.byId.has(id)));
    if (!ids.size) return;
    this.view?.setHighlight(ids); this.view?.flyTo([...ids]);
  }
}
