// Explore page (route #/stage): a real chip in 3D (a Tiny Tapeout chip from its GDS, or the warm-up), its source,
// an inspector, and the live simulator. Helpers: netlist.ts (fan-in/out), inspector.ts (the right pane's HTML),
// waveform.ts (the logic analyser), inputs.ts (custom inputs).

import type { Design, DesignCell, IoField, Lib } from "../../api/types";
import type { StageChip } from "../../api/core";
import { Sim } from "../../sim/sim";
import { Stage3D, LAYERS, type LayerKey } from "../../three/stage3d";
import { usedArea } from "../../three/geometry";
import { category, CATEGORY_LABEL, CATEGORY_VAR } from "../../lib/cells";
import { cap } from "../../lib/kinds";
import { $, $$, codeText, esc, wrapOn } from "../../ui/dom";
import { icon } from "../../ui/icons";
import { app } from "../../ui/state";
import * as stageAgent from "../../agent/stageAgent";
import { Connectivity } from "./netlist";
import { cellHtml, categoryTitle, chipHtml, lineHtml, netHtml, physHtml, portHtml } from "./inspector";
import { drawWave, type Lane } from "./waveform";
import { loadCustomInputs, parseCustomInput, saveCustomInputs } from "./inputs";

const SPEEDS: [number, string][] = [[900, "0.5×"], [450, "1×"], [120, "4×"]];

export class StagePage {
  readonly el: HTMLElement;
  view: Stage3D | null = null;
  sim: Sim;
  private net: Connectivity;
  private byId: Map<string, DesignCell>;
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
  /** Input pins the gates read that the author's pinout doesn't name (e.g. I2S-to-PWM names none of its 3). */
  private extraIns: string[] = [];
  /** Inputs the user defined: a name over any input bits, LSB first; saved per chip in this browser. */
  private custom: IoField[] = [];
  private insDirty = true;
  private netSet = new Set<string>();
  private waveSigs: string[];
  private lanes: Lane[];

  constructor(private design: Design, private lib: Lib, private chips: StageChip[] = []) {
    this.net = new Connectivity(design, lib);
    this.byId = this.net.byId;
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
    const covered = new Set([...(this.io?.in ?? []).flatMap((f) => f.bits), ...this.inputs]);
    const busIdx = (n: string) => { const m = /^(\w+)\[(\d+)\]$/.exec(n); return m ? [m[1], +m[2]] as const : [n, 0] as const; };
    this.extraIns = [...this.sim.names].filter((n) => /^(ui_in|uio_in)\[\d+\]$/.test(n) && !covered.has(n))
      .sort((a, b) => { const [x, i] = busIdx(a), [y, j] = busIdx(b); return x === y ? i - j : x < y ? -1 : 1; });
    this.netSet = new Set(this.sim.names);
    this.custom = loadCustomInputs(design.name).filter((f) => f.bits.every((b) => this.netSet.has(b)));
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
      const t = e.target as HTMLElement;
      if (t.closest("#stCiAdd")) { this.addCustomInput(); return; }
      const del = t.closest<HTMLElement>("[data-ci-del]");
      if (del) { e.preventDefault(); this.custom = this.custom.filter((f) => f.name !== del.dataset.ciDel); saveCustomInputs(this.design.name, this.custom); this.insDirty = true; this.after(); return; }
      const b = t.closest<HTMLElement>("[data-in]");
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
    const f = [...(this.io?.in ?? []), ...this.custom].find((x) => x.name === name);
    if (!f) return;
    const v = Math.max(0, Math.min((1 << f.bits.length) - 1, Math.floor(value) || 0));
    this.sim.apply(Object.fromEntries(f.bits.map((b, k) => [b, (v >> k) & 1])));
    this.after();
  }
  /** "+ Custom input": a name over any input bits ("ui_in[2]", "ui_in[7:4]", "ui_in[0], uio_in[3]"), LSB first. */
  private addCustomInput() {
    const name = ($<HTMLInputElement>("#stCiName", this.el).value || "").trim();
    const spec = ($<HTMLInputElement>("#stCiBits", this.el).value || "").trim();
    const r = parseCustomInput(name, spec, this.netSet, [...(this.io?.in ?? []), ...this.custom]);
    if ("error" in r) { $("#stCiMsg", this.el).textContent = r.error; return; }
    const bits = r.bits;
    this.custom = [...this.custom, { name, bits }];
    saveCustomInputs(this.design.name, this.custom);
    this.insDirty = true;
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
    const nb = this.net.neighbours(c);
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
    $("#stInsp", this.el).innerHTML = lineHtml({ text: this.srcLines[ln - 1]?.trim() ?? "", file: this.srcFile, ln, cells: [...set], direct: ids.length, mod, uses: insts.length });
    this.bindLinks();
  }
  selectNet(net: string) {
    this.selected = null;
    const d = this.net.driver(net), s = this.net.readers(net);
    const ids = new Set([...(d ? [d] : []), ...s.map((x) => x.id)]);
    this.view?.select(null); this.view?.setHighlight(ids, new Set([net]));
    this.head("Wire");
    $("#stInsp", this.el).innerHTML = netHtml(net, this.sim.get(net), d, s, this.inputs.includes(net));
    this.bindLinks();
  }
  selectPort(name: string) {
    const p = this.design.ports.find((x) => x.name === name);
    if (!p) return;
    const ids = p.dir === "out" ? this.net.fanIn(name) : this.net.fanOut(name);
    this.selected = null;
    this.view?.select(null); this.view?.setHighlight(ids);
    if (ids.size) this.view?.flyTo([...ids]);
    const v = this.sim.get(name);
    this.head(p.dir === "out" ? "Output pin" : "Input pin");
    $("#stInsp", this.el).innerHTML = portHtml(p, v, [...ids], p.dir === "in" && name !== this.clk);
    this.bindLinks();
    $$("[data-toggle]", this.el).forEach((b) => b.addEventListener("click", () => { this.sim.apply({ [name]: this.sim.get(name) ^ 1 }); this.after(); this.selectPort(name); }));
  }
  private showPhys(p: Design["phys"][number]) {
    this.selected = null; this.view?.select(null); this.view?.setHighlight(null);
    this.head("Physical cell");
    $("#stInsp", this.el).innerHTML = physHtml(p, this.lib);
  }

  // ---------- rendering ----------
  private bindLinks() {
    const root = $("#stInsp", this.el);
    $$<HTMLElement>("[data-cell]", root).forEach((b) => b.addEventListener("click", () => this.selectCell(b.dataset.cell!)));
    $$<HTMLElement>("[data-net]", root).forEach((b) => b.addEventListener("click", () => this.selectNet(b.dataset.net!)));
    $$<HTMLElement>("[data-ln]", root).forEach((b) => b.addEventListener("click", () => this.selectLine(+b.dataset.ln!)));
    $$<HTMLElement>("[data-cone]", root).forEach((b) => b.addEventListener("click", () => {
      const ids = b.dataset.cone === "in" ? this.net.fanIn(b.dataset.net!) : this.net.fanOut(b.dataset.net!);
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
      el.innerHTML = chipHtml(this.design, { logic: this.logicCount, flops: this.sim.flops.length, usedSize: this.usedSize });
      return;
    }
    const c = this.byId.get(id)!;
    this.head(categoryTitle(c.type, this.lib));
    el.innerHTML = cellHtml(c, this.design, this.lib, (n) => this.sim.get(n), {
      flopState: this.lib[c.type]?.ff ? String(this.sim.flopState(c.id)) : "", srcLine: c.src ? this.srcLines[c.src - 1]?.trim() ?? null : null });
    this.bindLinks();
  }

  private get logicCount() { return Number(this.design.counts?.logic_cells ?? this.design.cells.length); }
  private renderInputs() {
    const host = $("#stIns", this.el);
    const fieldHtml = (f: IoField, custom: boolean) => `<label class="field" title="${esc(f.name)}: ${esc(f.bits.join(", "))} (${f.bits.length} bit${f.bits.length === 1 ? "" : "s"}, 0 to ${(1 << f.bits.length) - 1})">${esc(f.name)}
          <input class="num" type="number" min="0" max="${(1 << f.bits.length) - 1}" data-io="${esc(f.name)}" value="0" aria-label="Input ${esc(f.name)}">${custom
            ? `<button type="button" class="linkbtn" data-ci-del="${esc(f.name)}" title="Remove this custom input" aria-label="Remove ${esc(f.name)}">×</button>` : ""}</label>`;
    const bitBtn = (n: string) => { const v = this.sim.get(n); return `<button type="button" class="inbtn${v ? " on" : ""}" data-in="${esc(n)}" title="Toggle ${esc(n)} (not named in the author's pinout)">${esc(n)} <span class="bit${v ? " on" : ""}">${v}</span></button>`; };
    const addForm = `<details class="ins-add" style="flex-basis:100%"><summary class="muted" style="cursor:pointer">+ Custom input</summary>
          <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:6px">
            <input class="text" id="stCiName" placeholder="Name, e.g. Serial data" style="width:9em" aria-label="Custom input name">
            <input class="text" id="stCiBits" placeholder="Bits, e.g. ui_in[2] or ui_in[7:4]" style="width:14em" aria-label="Custom input bits">
            <button type="button" class="btn sm" id="stCiAdd">Add</button><span class="muted" id="stCiMsg"></span></div></details>`;
    if (this.io) {
      const fields = [...this.io.in, ...this.custom];
      if (this.insDirty || !host.querySelector("[data-io],[data-in]")) {
        this.insDirty = false;
        host.innerHTML = fields.map((f) => fieldHtml(f, this.custom.includes(f))).join("") +
          (this.extraIns.length ? `<span class="eyebrow" style="flex-basis:100%;margin-top:6px">Other input pins</span>${this.extraIns.map(bitBtn).join("")}` : "") +
          (fields.length || this.extraIns.length ? "" : `<span class="muted">None</span>`) + addForm;
      }
      host.querySelectorAll<HTMLInputElement>("[data-io]").forEach((inp) => {
        const f = fields.find((x) => x.name === inp.dataset.io);
        if (f && document.activeElement !== inp) inp.value = String(this.fieldVal(f));
      });
      host.querySelectorAll<HTMLElement>("[data-in]").forEach((b) => {
        const v = this.sim.get(b.dataset.in!);
        b.classList.toggle("on", !!v);
        const bit = b.querySelector(".bit"); if (bit) { bit.textContent = String(v); bit.classList.toggle("on", !!v); }
      });
      return;
    }
    if (host.querySelector("#stCiName") && host.contains(document.activeElement)) return;   // don't wipe a half-typed custom input
    host.innerHTML = [...this.inputs.filter((n) => n !== this.clk), ...this.extraIns].map((n) => {
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
    if (cv) drawWave(cv, this.lanes, this.history);
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
