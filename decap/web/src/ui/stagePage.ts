// Stage page: a real chip in 3D (a Tiny Tapeout chip from its GDS, or the warm-up), its source, an inspector, and the live simulator.

import type { Design, DesignCell, IoField, Lib } from "../data/types";
import type { StageChip } from "../data/load";
import { Sim } from "../sim/sim";
import { Stage3D, LAYERS, type LayerKey } from "../view/stage3d";
import { usedArea } from "../view/geometry";
import { category, CATEGORY_LABEL, CATEGORY_VAR, describe, fullName, toWordsSafe } from "../view/describe";
import { parse, evalExpr, vars } from "../sim/expr";
import { $, $$, esc, hiVerilog } from "./dom";
import { app } from "./state";

const NBSP = " ";

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
  private waveLabel = new Map<string, string>();

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
      for (const f of [...this.io.in, ...this.io.out]) f.bits.forEach((b, k) => this.waveLabel.set(b, f.bits.length > 1 ? `${f.name}${k}` : f.name));
      this.waveSigs = [...this.io.in, ...this.io.out].flatMap((f) => f.bits);
    } else {
      this.waveSigs = [...this.inputs.filter((x) => x !== this.clk), ...this.outputs];
    }
    this.srcFile = Object.keys(design.source.files)[0] ?? "";
    this.srcLines = (design.source.files[this.srcFile] ?? "").split("\n");

    this.el = document.createElement("div");
    this.el.className = "stage";
    this.el.innerHTML = `
      <aside class="pane code-pane" aria-label="Source code">
        ${this.chips.length > 1 ? `<div class="pane-h chippick"><label for="stChip">Chip</label><select class="sel" id="stChip" aria-label="Chip shown on the Stage">${this.chips.map((c) =>
          `<option value="${esc(c.chip)}" ${c.chip === design.name ? "selected" : ""}>${esc(c.title || c.chip)} · ${c.logic_cells} cells</option>`).join("")}</select></div>` : ""}
        <div class="pane-h">Source <b>${esc(this.srcFile || "none")}</b><span class="grow"></span>${this.chips.length > 1 ? `<a class="btn sm" href="#/chip/${encodeURIComponent(design.name)}" title="Decompile, proof and Find for this chip">Chip page</a>` : ""}</div>
        <div class="pane-b"><ol class="code click" id="stSrc"></ol></div>
        <div class="sect"><p class="fine">${esc(design.source.mapping ?? "")}. Click a line to light its cells.</p></div>
      </aside>
      <main class="viewport" id="stView" aria-label="3D chip view">
        <dl class="overlay stats" id="stStats"></dl>
        <div class="overlay layers" id="stLayers">
          <button type="button" id="stLayersT" aria-expanded="true">Layers <span aria-hidden="true">▾</span></button>
          <div class="lbody" id="stLayersB">
            ${LAYERS.map((L) => `<label><input type="checkbox" data-layer="${L.key}" ${L.on ? "checked" : ""}> ${L.label} <i class="sw" style="background:${L.colour}"></i></label>`).join("")}
            <div class="ex"><span><label for="stExplode" style="cursor:default">Exploded view</label><kbd>X</kbd></span><input type="range" id="stExplode" min="0" max="100" value="0"></div>
            <div class="ex"><span>Colour cells by</span>
              <div class="seg" role="group" aria-label="Colour cells by"><button type="button" data-colour="function" aria-pressed="true">function</button><button type="button" data-colour="module" aria-pressed="false">module</button></div></div>
            <label><input type="checkbox" id="stHQ" checked> High quality (bloom)</label>
          </div>
        </div>
        <div class="overlay legend" id="stLegend"></div>
        <div class="overlay creditbar">${esc(design.credit ?? "")}</div>
      </main>
      <aside class="pane right" aria-label="Inspector">
        <div class="pane-h">Inspector<span class="grow"></span><span class="pickms" id="stPick"></span></div>
        <div class="pane-b"><div class="sect" id="stInsp"></div></div>
      </aside>
      <footer class="sim" aria-label="Simulator">
        <div class="ctrls">
          <button class="btn" id="stReset" type="button" title="Pulse rst_n low for one clock (R)">Reset</button>
          <button class="btn" id="stStep" type="button" title="One clock edge (.)">Step</button>
          <button class="btn primary" id="stRun" type="button" title="Run or pause (Space)">Run</button>
          <select class="sel" id="stSpeed" aria-label="Clock speed"><option value="900">slow</option><option value="450" selected>normal</option><option value="120">fast</option></select>
        </div>
        <div class="ins" id="stIns" aria-label="Inputs"></div>
        <div class="shiftin" ${this.io ? "hidden" : ""} title="Shift two 8-bit numbers into the chip's shift registers, most significant bit first">
          <span class="muted">Shift in</span>
          <input class="num" id="stA" type="number" min="0" max="255" value="250" aria-label="Number for A">
          <span class="muted">+</span>
          <input class="num" id="stB" type="number" min="0" max="255" value="246" aria-label="Number for B">
          <button class="btn sm" id="stShift" type="button">8 clocks</button>
        </div>
        <div class="readout" id="stRead"></div>
        <canvas class="wave" id="stWave" aria-label="Logic analyser: the last 40 clock cycles"></canvas>
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
      });
      this.bind();
      if (innerWidth < 1300) { $("#stLayersB", this.el).hidden = true; $("#stLayersT", this.el).setAttribute("aria-expanded", "false"); }
      this.renderSource(); this.renderInsp(); this.renderInputs(); this.renderRead(); this.renderStats(); this.renderLegend();
      this.view.setValues((n) => this.sim.get(n));
      // Tiny Tapeout tiles are mostly filler: open on the logic, not the whole die.
      if (this.io) setTimeout(() => this.view?.flyTo(this.design.cells.map((c) => c.id)), 400);
    }
    this.view.start();
  }
  unmount() { this.view?.stop(); this.pause(); }
  /** The chip on show (design.json name). */
  get chipName() { return this.design.name; }
  /** Drop this Stage for good (switching chips): frees the 3D view's WebGL context. */
  destroy() { this.pause(); this.view?.dispose(); this.view = null; }

  private bind() {
    const el = this.el;
    $$<HTMLInputElement>("[data-layer]", el).forEach((cb) => cb.addEventListener("change", () => this.view?.setLayer(cb.dataset.layer as LayerKey, cb.checked)));
    $<HTMLInputElement>("#stExplode", el).addEventListener("input", (e) => this.view?.setExplode(+(e.target as HTMLInputElement).value / 100));
    $("#stLayersT", el).addEventListener("click", () => {
      const b = $("#stLayersB", el); b.hidden = !b.hidden; $("#stLayersT", el).setAttribute("aria-expanded", String(!b.hidden));
    });
    $$<HTMLButtonElement>("[data-colour]", el).forEach((b) => b.addEventListener("click", () => {
      $$("[data-colour]", el).forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      this.view?.setColourBy(b.dataset.colour as "function" | "module"); this.renderLegend();
    }));
    $<HTMLInputElement>("#stHQ", el).addEventListener("change", (e) => this.view?.setQuality((e.target as HTMLInputElement).checked));
    $("#stReset", el).addEventListener("click", () => this.reset());
    $("#stStep", el).addEventListener("click", () => { this.pause(); this.step(); });
    $("#stRun", el).addEventListener("click", () => (this.running ? this.pause() : this.run()));
    $<HTMLSelectElement>("#stSpeed", el).addEventListener("change", (e) => { this.speed = +(e.target as HTMLSelectElement).value; });
    $("#stShift", el).addEventListener("click", () => this.shiftIn());
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
  run() {
    if (this.running) return;
    this.running = true; $("#stRun", this.el).textContent = "Pause";
    const loop = () => { if (!this.running) return; this.step(); this.timer = window.setTimeout(loop, this.speed); };
    loop();
  }
  pause() { this.running = false; clearTimeout(this.timer); const b = $("#stRun", this.el); if (b) b.textContent = "Run"; }
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
  private fieldVal(f: IoField) { return f.bits.reduce((s, n, i) => s + (this.sim.get(n) << i), 0); }
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
    const pk = $("#stPick", this.el);
    if (pk && this.view) pk.textContent = this.view.lastPickMs ? `pick ${this.view.lastPickMs.toFixed(1)} ms` : "";
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
    $("#stInsp", this.el).innerHTML = `
      <div class="insp-title"><div><b>${esc(this.srcFile)}:${ln}</b><small>${esc(this.srcLines[ln - 1]?.trim() ?? "")}</small></div><span class="pill gold">${set.size} cells</span></div>
      <p class="insp-role">${ids.length ? `These ${ids.length} cells are what this line became after synthesis.` : mod ? `This line is part of module <b>${esc(mod)}</b>${insts.length ? ` (used ${insts.length === 1 ? "once, as" : `${insts.length} times, as`} ${insts.map(esc).join(", ")})` : ""}. Its ${set.size} cells are lit.` : "No cells map to this line."}</p>
      ${this.cellLinks([...set])}`;
    this.bindLinks();
  }
  selectNet(net: string) {
    this.selected = null;
    const d = this.drivers.get(net), s = this.sinks.get(net) ?? [];
    const ids = new Set([...(d ? [d] : []), ...s.map((x) => x.id)]);
    this.view?.select(null); this.view?.setHighlight(ids, new Set([net]));
    const v = this.sim.get(net);
    $("#stInsp", this.el).innerHTML = `
      <div class="insp-title"><div><b>${esc(net)}</b><small>wire · ${s.length} load${s.length === 1 ? "" : "s"}</small></div><span class="bit${v ? " on" : ""}">${v}</span></div>
      <p class="insp-role">A wire carries one bit from the cell that drives it to every cell that reads it.</p>
      <dl class="kv"><dt>Driven by</dt><dd>${d ? `<button type="button" class="linkbtn" data-cell="${esc(d)}">${esc(d)}</button>` : this.inputs.includes(net) ? "input pin" : "nothing"}</dd>
      <dt>Read by</dt><dd>${s.map((x) => `<button type="button" class="linkbtn" data-cell="${esc(x.id)}">${esc(x.id)}</button>.${esc(x.pin)}`).join(" ") || "nothing"}</dd></dl>`;
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
    $("#stInsp", this.el).innerHTML = `
      <div class="insp-title"><div><b>Pin ${esc(name)}</b><small>${p.dir === "out" ? "output" : "input"} · at (${p.x.toFixed(1)}, ${p.y.toFixed(1)}) µm</small></div><span class="bit${v ? " on" : ""}">${v}</span></div>
      <p class="insp-role">${p.dir === "out" ? `Everything that can change ${esc(name)}, back to the flip-flops: its fan-in cone (“go to definition” for silicon).` : `Everything ${esc(name)} can reach before the next flip-flop: its fan-out cone (“find usages” for silicon).`}</p>
      ${p.dir === "in" && name !== this.clk ? `<div class="acts"><button class="btn sm" type="button" data-toggle="${esc(name)}">Toggle ${esc(name)} (now ${v})</button></div>` : ""}
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
    $("#stInsp", this.el).innerHTML = `<div class="insp-title"><div><b>${esc(p.type)}</b><small>${esc(fullName(p.type))}</small></div><span class="pill">physical</span></div>
      <p class="insp-role">${esc(describe(p.type, this.lib[p.type]))}</p>
      <dl class="kv"><dt>Position</dt><dd>(${p.x.toFixed(2)}, ${p.y.toFixed(2)}) µm</dd><dt>Width</dt><dd>${p.w.toFixed(2)} µm</dd></dl>`;
  }

  // ---------- rendering ----------
  private cellLinks(ids: string[]) {
    if (!ids.length) return "";
    return `<dl class="kv"><dt>Cells</dt><dd>${ids.slice(0, 80).map((id) => `<button type="button" class="linkbtn" data-cell="${esc(id)}">${esc(id)}</button>`).join(" ")}${ids.length > 80 ? " …" : ""}</dd></dl>`;
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
      return `<li data-ln="${ln}" class="${cls}"><span class="n">${ln}</span><span class="bk"></span><span class="t">${hiVerilog(t) || NBSP}</span>${count ? `<span class="badge">${count}</span>` : "<span></span>"}</li>`;
    }).join("");
    if (scroll) $("#stSrc li.active", this.el)?.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  private renderInsp() {
    const el = $("#stInsp", this.el), id = this.selected;
    if (!id) {
      el.innerHTML = `<p class="insp-role">${esc(this.design.intro ?? "This is a real chip: the warm-up design, placed and routed for SkyWater's 130 nm process. Every coloured block is a standard cell (a tiny pre-made circuit). The gold labels are its pins.")}</p>
        <p class="muted" style="font-size:13px">${this.design.hint ? esc(this.design.hint) : "Click any cell, wire or pin to see what it is. Press <b>Run</b> to clock it: wires carrying a 1 glow. Use <b>Shift in</b> to load two numbers; S lights up when they add to 496."}</p>
        <dl class="kv">${this.design.title ? `<dt>Chip</dt><dd>${esc(this.design.title)}</dd>` : ""}<dt>Design</dt><dd>${esc(this.design.name)}</dd><dt>Process</dt><dd>${esc(this.design.pdk)}</dd>
        <dt>Shown</dt><dd>${this.usedSize} µm, the part the logic uses</dd>
        <dt>Tile</dt><dd>${this.design.die.w} × ${this.design.die.h} µm (fixed slot; the rest is filler)</dd><dt>Logic cells</dt><dd>${this.logicCount}</dd>
        <dt>Flip-flops</dt><dd>${this.sim.flops.length}</dd><dt>Physical cells</dt><dd>${this.design.phys.length} (fill, tap, decap)</dd>
        <dt>Wires</dt><dd>${this.design.counts?.route_segments ?? "?"} segments, ${this.design.counts?.vias ?? "?"} vias</dd></dl>`;
      return;
    }
    const c = this.byId.get(id)!, L = this.lib[c.type];
    const cat = category(c.type, L);
    const pinRows = Object.entries(c.pins).map(([p, n]) => {
      const dir = L?.pins[p] ?? "?";
      const v = this.sim.get(n);
      return `<tr><td>${esc(p)}${dir === "out" ? " →" : ""}</td><td><button type="button" class="linkbtn" data-net="${esc(n)}">${esc(n)}</button></td><td><span class="bit${v ? " on" : ""}">${v}</span></td></tr>`;
    }).join("");
    const fns = L ? Object.entries(L.function) : [];
    const modInfo = c.module ? this.design.source.modules[c.module] : null;
    const srcLine = c.src ? this.srcLines[c.src - 1]?.trim() : null;
    el.innerHTML = `
      <div class="insp-title"><div><b>${esc(c.id)}</b><small>${esc(fullName(c.type))}</small></div><span class="pill" style="color:var(${CATEGORY_VAR[cat]});border-color:var(${CATEGORY_VAR[cat]})">${esc(CATEGORY_LABEL[cat])}</span></div>
      <p class="insp-role">${esc(describe(c.type, L))}</p>
      <dl class="kv">
        ${fns.map(([o, f]) => L?.ff && /^IQ(_N)?$/.test(f) ? `<dt>${esc(o)} =</dt><dd>${f === "IQ" ? "the stored bit" : "the stored bit, inverted"}</dd>` : `<dt>${esc(o)} =</dt><dd>${esc(f)}<br><span class="muted">${esc(toWordsSafe(f))}</span></dd>`).join("")}
        ${L?.ff ? `<dt>Stores</dt><dd>${this.sim.flopState(c.id)} <span class="muted">(next: ${esc(L.ff.next_state)} on ${esc(L.ff.clocked_on)} ↑${L.ff.clear ? `, cleared by ${esc(L.ff.clear)}` : ""})</span></dd>` : ""}
        <dt>Module</dt><dd>${c.module ? `${esc(c.module)}${c.inst ? ` <span class="muted">(instance ${esc(c.inst)})</span>` : ""}` : "none"}</dd>
        <dt>Source</dt><dd>${c.src ? `<button type="button" class="linkbtn" data-ln="${c.src}">${esc(this.srcFile)}:${c.src}</button><br><span class="muted">${esc(srcLine ?? "")}</span>` : modInfo ? `<span class="muted">not written by the designer: added by the layout tools (clock tree)</span>` : "none"}</dd>
        <dt>Size</dt><dd>${c.w.toFixed(2)} × ${this.design.rowHeight} µm${L?.area ? ` · ${L.area.toFixed(2)} µm²` : ""}</dd>
        <dt>Placed at</dt><dd>(${c.x.toFixed(2)}, ${c.y.toFixed(2)}) µm · ${esc(c.orient)}</dd>
      </dl>
      <table class="pins"><tr><th>Pin</th><th>Wire</th><th>Now</th></tr>${pinRows}</table>
      ${this.truthTable(c)}
      <div class="acts">
        ${(() => { const o = Object.entries(c.pins).find(([p]) => L?.pins[p] === "out"); return o ? `<button type="button" class="btn sm" data-cone="in" data-net="${esc(o[1])}">Fan-in cone</button><button type="button" class="btn sm" data-cone="out" data-net="${esc(o[1])}">Fan-out cone</button>` : ""; })()}
        <button type="button" class="btn sm" data-ask="Explain cell ${esc(c.id)} (${esc(c.type)}) in simple terms: what it does and why this chip needs it.">Ask the guide</button>
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
    return `<div><p class="fine" style="margin-bottom:4px">Truth table (the highlighted row is what it sees right now)</p><table class="tt"><tr>${ins.map((p) => `<th>${esc(p)}</th>`).join("")}<th>${esc(o)}</th></tr>${rows.join("")}</table></div>`;
  }

  private get logicCount() { return Number(this.design.counts?.logic_cells ?? this.design.cells.length); }
  private renderInputs() {
    const host = $("#stIns", this.el);
    if (this.io) {
      if (!host.querySelector("[data-io]")) {
        host.innerHTML = this.io.in.map((f) => `<label class="inbtn iofield" title="${esc(f.name)}: ${esc(f.bits.join(", "))}">${esc(f.name)}
          <input class="num" type="number" min="0" max="${(1 << f.bits.length) - 1}" data-io="${esc(f.name)}" value="0" aria-label="Input ${esc(f.name)}"></label>`).join("") +
          (this.clk ? `<span class="inbtn" id="stClkI" title="Clock: driven by Step / Run">${esc(this.clk)} <span class="bit">↑</span></span>` : "");
      }
      host.querySelectorAll<HTMLInputElement>("[data-io]").forEach((inp) => {
        const f = this.io!.in.find((x) => x.name === inp.dataset.io);
        if (f && document.activeElement !== inp) inp.value = String(this.fieldVal(f));
      });
      return;
    }
    host.innerHTML = this.inputs.filter((n) => n !== this.clk).map((n) => {
      const v = this.sim.get(n);
      return `<button type="button" class="inbtn${v ? " on" : ""}" data-in="${esc(n)}" title="Toggle input ${esc(n)}">${esc(n)} <span class="bit${v ? " on" : ""}">${v}</span></button>`;
    }).join("") + (this.clk ? `<span class="inbtn" title="Clock: driven by Step / Run">${esc(this.clk)} <span class="bit${this.sim.get(this.clk) ? " on" : ""}">↑</span></span>` : "");
  }
  private renderRead() {
    const busVal = (b: { bits: string[] }) => b.bits.reduce((s, n, i) => s + (this.sim.get(n) << i), 0);
    if (this.io) {
      $("#stRead", this.el).innerHTML = [
        ...this.io.out.map((f) => { const v = this.fieldVal(f); return `<div class="bus"><small>output</small><span class="big">${esc(f.name)} <b>${v}</b> <span class="muted">${v.toString(2).padStart(f.bits.length, "0")}</span></span></div>`; }),
        `<div class="bus"><small>cycle</small><span>${this.sim.cycles}</span></div>`,
      ].join("");
      return;
    }
    $("#stRead", this.el).innerHTML = [
      ...this.outputs.map((o) => `<div class="bus"><small>output</small><span class="big">${esc(o)} <span class="bit${this.sim.get(o) ? " on" : ""}">${this.sim.get(o)}</span></span></div>`),
      ...this.buses.map((b) => `<div class="bus"><small>${esc(b.name)}</small><span>${busVal(b)} <span class="muted">${busVal(b).toString(2).padStart(b.bits.length, "0")}</span></span></div>`),
      `<div class="bus"><small>cycle</small><span>${this.sim.cycles}${this.shiftQueue.length ? ` <span class="muted">(shifting, ${this.shiftQueue.length} left)</span>` : ""}</span></div>`,
    ].join("");
  }
  /** Width x height of the part of the tile the design uses (what the 3D view draws). */
  private get usedSize() {
    const v = usedArea(this.design);
    return `${(v.x1 - v.x0).toFixed(0)} × ${(v.y1 - v.y0).toFixed(0)}`;
  }
  private renderStats() {
    $("#stStats", this.el).innerHTML = `<dt>logic cells</dt><dd>${this.logicCount}</dd><dt>cell types</dt><dd>${new Set(this.design.cells.map((c) => c.type)).size}</dd>
      <dt>flip-flops</dt><dd>${this.sim.flops.length}</dd><dt>wires</dt><dd>${this.design.counts?.nets ?? this.design.counts?.nets_on_wires ?? "?"}</dd><dt>size</dt><dd>${this.usedSize} µm</dd>`;
  }
  private renderLegend() {
    const byModule = $("[data-colour='module']", this.el)?.getAttribute("aria-pressed") === "true";
    if (byModule && this.view) {
      const mods = [...new Set(this.design.cells.map((c) => c.module ?? "?"))];
      $("#stLegend", this.el).innerHTML = mods.map((m) => `<span><i class="sw" style="background:${this.view!.moduleColour(m)}"></i>${esc(m)}${m === this.design.source.top ? " (clock tree)" : ""}</span>`).join("");
      return;
    }
    const cats = [...new Set(this.design.cells.map((c) => category(c.type, this.lib[c.type])))];
    $("#stLegend", this.el).innerHTML = cats.map((k) => `<span><i class="sw" style="background:var(${CATEGORY_VAR[k]})"></i>${esc(CATEGORY_LABEL[k])}</span>`).join("") +
      `<span><i class="sw" style="background:var(--high)"></i>wire = 1</span>`;
  }
  private drawWave() {
    const cv = $<HTMLCanvasElement>("#stWave", this.el);
    if (!cv) return;
    const dpr = Math.min(devicePixelRatio, 2), w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) return;
    cv.width = w * dpr; cv.height = h * dpr;
    const g = cv.getContext("2d")!;
    g.scale(dpr, dpr); g.clearRect(0, 0, w, h);
    const sigs = this.waveSigs;
    const rowH = h / Math.max(1, sigs.length), lw = 44, n = 40, step = (w - lw) / n;
    g.font = "10px JetBrains Mono, monospace";
    sigs.forEach((s, r) => {
      const y0 = r * rowH, hi = y0 + rowH * 0.2, lo = y0 + rowH * 0.85;
      g.fillStyle = "#8995a9"; g.fillText(this.waveLabel.get(s) ?? s, 2, y0 + rowH * 0.7);
      g.strokeStyle = this.outputs.includes(s) ? "#ffd45e" : "#4f7fe0"; g.lineWidth = 1.3; g.beginPath();
      const hist = this.history;
      let prevY: number | null = null;
      hist.forEach((snap, i) => {
        const x = lw + (n - hist.length + i) * step, y = snap[s] ? hi : lo;
        if (prevY === null) g.moveTo(x, y); else { g.lineTo(x, prevY); g.lineTo(x, y); }
        g.lineTo(x + step, y); prevY = y;
      });
      g.stroke();
    });
  }

  /** Demo helper: the first cell whose type matches. */
  findCell(re: RegExp): string | null { return this.design.cells.find((c) => re.test(c.type))?.id ?? null; }

  /** Commands for the palette. */
  commands() {
    return [
      { label: "Stage: run or pause the clock", key: "Space", run: () => (this.running ? this.pause() : this.run()) },
      { label: "Stage: step one clock edge", key: ".", run: () => { this.pause(); this.step(); } },
      { label: "Stage: reset the chip", key: "R", run: () => this.reset() },
      this.io ? { label: "Stage: set the inputs to 9 and 8 and clock once", key: "", run: () => this.demoShow() }
        : { label: "Stage: shift in 250 + 246 (S lights at 496)", key: "", run: () => this.shiftIn(250, 246) },
      { label: "Stage: toggle exploded view", key: "X", run: () => this.toggleExplode() },
      { label: "Stage: fly to the whole die", key: "F", run: () => this.view?.flyTo(null) },
      ...this.design.cells.map((c) => ({ label: `Go to cell ${c.id}`, key: c.type, run: () => this.selectCell(c.id) })),
      ...this.srcLines.map((t, i) => ({ label: `Go to line ${i + 1}: ${t.trim()}`, key: "", run: () => this.selectLine(i + 1) })).filter((x) => !x.label.endsWith(": ")),
    ];
  }
  toggleExplode() {
    const x = this.view && this.view.exploded > 0.5 ? 0 : 0.85;
    this.view?.setExplode(x); $<HTMLInputElement>("#stExplode", this.el).value = String(Math.round(x * 100));
  }
  /** Context for the AI guide. */
  context() {
    const c = this.selected ? this.byId.get(this.selected) : null;
    return {
      page: "stage", design: this.design.name, credit: this.design.credit,
      selected: c ? { id: c.id, type: c.type, function: this.lib[c.type]?.function, pins: Object.fromEntries(Object.entries(c.pins).map(([p, n]) => [p, { net: n, value: this.sim.get(n) }])), module: c.module, src: c.src } : null,
      modules: this.design.source.modules, outputs: Object.fromEntries(this.outputs.map((o) => [o, this.sim.get(o)])), cycle: this.sim.cycles,
    };
  }
  highlightFromGuide(cells: string[]) {
    const ids = new Set(cells.filter((id) => this.byId.has(id)));
    if (!ids.size) return;
    this.view?.setHighlight(ids); this.view?.flyTo([...ids]);
  }
}
