// 2D netlist graph of a stripped netlist (TT08 chips have no layout): a layered left-to-right schematic on canvas.
// Inputs on the left, outputs on the right; each cell sits one column right of whatever drives it.
// Flip-flops are placed after the logic feeding them; their outputs loop back (drawn as dashed back edges).

import type { ChipNetlist, Lib } from "../data/types";
import { category, CATEGORY_VAR, family, isOutputPin } from "../view/describe";
import { portBits } from "../sim/verilog";
import { css } from "./dom";

interface GNode { id: string; kind: "cell" | "in" | "out"; type: string; col: number; row: number; x: number; y: number; colour: string; label: string }
interface GEdge { from: GNode; to: GNode; net: string; back: boolean }

const NW = 46, NH = 16, CX = 96, RY = 24;

export class NetGraph {
  private nodes: GNode[] = [];
  private byId = new Map<string, GNode>();
  private edges: GEdge[] = [];
  private cv: HTMLCanvasElement;
  private tip: HTMLElement;
  private view = { s: 1, x: 0, y: 0 };
  private hl: Set<string> | null = null;
  private marks = new Map<string, string>(); // id -> colour (flag cone, removed, inserted)
  private selected: string | null = null;
  private hover: GNode | null = null;
  private raf = 0;
  private ro: ResizeObserver;
  private fitted = false;

  constructor(private host: HTMLElement, net: ChipNetlist, lib: Lib, private cb: { onCell(id: string | null): void; label?: (id: string) => string }) {
    this.cv = document.createElement("canvas");
    this.cv.setAttribute("aria-label", "Netlist graph: every logic cell, wired left to right from inputs to outputs");
    this.cv.tabIndex = 0;
    host.append(this.cv);
    this.tip = document.createElement("div"); this.tip.className = "overlay tip"; this.tip.hidden = true; host.append(this.tip);
    this.layout(net, lib);
    this.bind();
    this.ro = new ResizeObserver(() => { if (!this.fitted) this.fit(); this.draw(); });
    this.ro.observe(host);
  }

  private layout(net: ChipNetlist, lib: Lib) {
    const driver = new Map<string, string>(), sinks = new Map<string, string[]>();
    const cells = net.cells.filter((c) => !lib[c.type]?.phys && !/^(fill|decap|tapvpwrvgnd|diode)/.test(c.type));
    const isFF = (t: string) => !!lib[t]?.ff || /^(df|sdf|edf|dl)/.test(t);
    for (const c of cells) for (const [p, n] of Object.entries(c.pins)) {
      if (isOutputPin(c.type, p, lib)) driver.set(n, c.id);
      else { if (!sinks.has(n)) sinks.set(n, []); sinks.get(n)!.push(c.id); }
    }
    const cellById = new Map(cells.map((c) => [c.id, c]));
    const inBits: string[] = [], outBits: string[] = [];
    for (const [name, p] of Object.entries(net.ports)) {
      const bits = portBits(name, p);
      if (p.dir === "in") inBits.push(...bits.filter((b) => sinks.has(b)));
      else outBits.push(...bits.filter((b) => driver.has(b)));
    }
    // Levels.
    const level = new Map<string, number>();
    const visiting = new Set<string>();
    const lv = (id: string): number => {
      if (level.has(id)) return level.get(id)!;
      const c = cellById.get(id)!;
      if (visiting.has(id)) return 1;
      visiting.add(id);
      let m = 0;
      for (const [p, n] of Object.entries(c.pins)) {
        if (isOutputPin(c.type, p, lib)) continue;
        const d = driver.get(n);
        if (d && !isFF(cellById.get(d)!.type)) m = Math.max(m, lv(d));
      }
      visiting.delete(id);
      level.set(id, m + 1);
      return m + 1;
    };
    for (const c of cells) if (!isFF(c.type)) lv(c.id);
    for (const c of cells) if (isFF(c.type)) {
      let m = 0;
      for (const [p, n] of Object.entries(c.pins)) {
        if (isOutputPin(c.type, p, lib)) continue;
        const d = driver.get(n);
        if (d && !isFF(cellById.get(d)!.type)) m = Math.max(m, level.get(d) ?? 0);
      }
      level.set(c.id, m + 1);
    }
    const maxL = Math.max(0, ...level.values());
    const cols: GNode[][] = Array.from({ length: maxL + 2 }, () => []);
    const mk = (id: string, kind: GNode["kind"], type: string, col: number): GNode => {
      const cat = kind === "cell" ? category(type, lib[type]) : null;
      const n: GNode = { id, kind, type, col, row: 0, x: 0, y: 0, colour: kind === "cell" ? css(CATEGORY_VAR[cat!]) : css("--accent"), label: kind === "cell" ? family(type) : id.slice(id.indexOf(":") + 1) };
      cols[col].push(n); this.nodes.push(n); this.byId.set(id, n); return n;
    };
    for (const b of inBits) mk(`in:${b}`, "in", "port", 0);
    for (const c of cells) mk(c.id, "cell", c.type, level.get(c.id) ?? 1);
    for (const b of outBits) mk(`out:${b}`, "out", "port", maxL + 1);
    // Edges.
    for (const c of cells) for (const [p, n] of Object.entries(c.pins)) {
      if (isOutputPin(c.type, p, lib)) continue;
      const d = driver.get(n);
      const from = d ? this.byId.get(d) : this.byId.get(`in:${n}`);
      const to = this.byId.get(c.id)!;
      if (from) this.edges.push({ from, to, net: n, back: from.col >= to.col });
    }
    for (const b of outBits) { const d = this.byId.get(driver.get(b)!); if (d) this.edges.push({ from: d, to: this.byId.get(`out:${b}`)!, net: b, back: false }); }
    // Order rows: barycentre sweeps.
    cols.forEach((col) => col.forEach((n, i) => (n.row = i)));
    const nbr = new Map<GNode, GNode[]>();
    for (const e of this.edges) {
      if (!nbr.has(e.to)) nbr.set(e.to, []); nbr.get(e.to)!.push(e.from);
      if (!nbr.has(e.from)) nbr.set(e.from, []); nbr.get(e.from)!.push(e.to);
    }
    for (let pass = 0; pass < 6; pass++) {
      for (const col of pass % 2 ? [...cols].reverse() : cols) {
        for (const n of col) {
          const ns = nbr.get(n) ?? [];
          (n as GNode & { bc?: number }).bc = ns.length ? ns.reduce((s, m) => s + m.row, 0) / ns.length : n.row;
        }
        col.sort((a, b) => ((a as GNode & { bc?: number }).bc! - (b as GNode & { bc?: number }).bc!));
        col.forEach((n, i) => (n.row = i));
      }
    }
    // Tall columns wrap into side-by-side sub-columns so big chips stay landscape.
    const maxRows = Math.max(14, Math.round(Math.sqrt(this.nodes.length) * 1.3));
    const rowsOf = (col: GNode[]) => Math.min(col.length, maxRows);
    const tallest = Math.max(1, ...cols.map(rowsOf));
    let x = 0;
    cols.forEach((col) => {
      const sub = Math.max(1, Math.ceil(col.length / maxRows)), per = Math.ceil(col.length / sub);
      col.forEach((n, i) => {
        const k = Math.floor(i / per), r = i % per, len = Math.min(per, col.length - k * per);
        n.x = x + k * (NW + 14);
        n.y = (r - (len - 1) / 2) * RY + (tallest * RY) / 2;
      });
      x += (sub - 1) * (NW + 14) + CX;
    });
  }

  private bind() {
    let drag: { x: number; y: number; vx: number; vy: number; moved: boolean } | null = null;
    this.cv.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, vx: this.view.x, vy: this.view.y, moved: false }; this.cv.setPointerCapture(e.pointerId); });
    this.cv.addEventListener("pointermove", (e) => {
      if (drag) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        if (Math.hypot(dx, dy) > 4) drag.moved = true;
        if (drag.moved) { this.view.x = drag.vx + dx; this.view.y = drag.vy + dy; this.draw(); }
        return;
      }
      const n = this.hit(e);
      if (n !== this.hover) { this.hover = n; this.draw(); }
      this.cv.style.cursor = n ? "pointer" : "grab";
      if (n) {
        const r = this.host.getBoundingClientRect();
        this.tip.innerHTML = n.kind === "cell" ? `${n.id} <small>${n.type}</small>${this.cb.label ? ` · ${this.cb.label(n.id)}` : ""}` : `${n.kind === "in" ? "input" : "output"} ${n.id.slice(n.id.indexOf(":") + 1)}`;
        this.tip.hidden = false; this.tip.style.left = `${e.clientX - r.left}px`; this.tip.style.top = `${e.clientY - r.top}px`;
      } else this.tip.hidden = true;
    });
    this.cv.addEventListener("pointerup", (e) => {
      const d = drag; drag = null;
      if (d && !d.moved) { const n = this.hit(e); this.cb.onCell(n && n.kind === "cell" ? n.id : null); }
    });
    this.cv.addEventListener("pointerleave", () => { this.tip.hidden = true; if (this.hover) { this.hover = null; this.draw(); } });
    this.cv.addEventListener("wheel", (e) => {
      e.preventDefault();
      const r = this.cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
      const k = Math.exp(-e.deltaY * 0.0015), s = Math.max(0.08, Math.min(6, this.view.s * k));
      this.view.x = mx - ((mx - this.view.x) * s) / this.view.s; this.view.y = my - ((my - this.view.y) * s) / this.view.s; this.view.s = s;
      this.draw();
    }, { passive: false });
    this.cv.addEventListener("keydown", (e) => {
      if (e.key === "0") { this.fit(); this.draw(); }
      if (e.key === "+" || e.key === "=") { this.view.s *= 1.2; this.draw(); }
      if (e.key === "-") { this.view.s /= 1.2; this.draw(); }
    });
  }

  private hit(e: PointerEvent): GNode | null {
    const r = this.cv.getBoundingClientRect();
    const x = (e.clientX - r.left - this.view.x) / this.view.s, y = (e.clientY - r.top - this.view.y) / this.view.s;
    for (const n of this.nodes) if (Math.abs(x - n.x) <= NW / 2 + 2 && Math.abs(y - n.y) <= NH / 2 + 2) return n;
    return null;
  }

  fit(ids?: string[]) {
    const w = this.host.clientWidth, h = this.host.clientHeight;
    if (!w || !h || !this.nodes.length) return;
    const ns = ids?.length ? ids.map((i) => this.byId.get(i)).filter((n): n is GNode => !!n) : this.nodes;
    if (!ns.length) return;
    const x0 = Math.min(...ns.map((n) => n.x)) - NW, x1 = Math.max(...ns.map((n) => n.x)) + NW;
    const y0 = Math.min(...ns.map((n) => n.y)) - NH * 2, y1 = Math.max(...ns.map((n) => n.y)) + NH * 2;
    const s = Math.min(3, w / (x1 - x0), h / (y1 - y0));
    this.view = { s, x: w / 2 - ((x0 + x1) / 2) * s, y: h / 2 - ((y0 + y1) / 2) * s };
    this.fitted = !ids;
  }
  focus(ids: string[]) { if (ids.length) { this.fit(ids); this.draw(); } }

  setHighlight(ids: Set<string> | null) { this.hl = ids && ids.size ? ids : null; this.draw(); }
  setMarks(marks: Map<string, string>) { this.marks = marks; this.draw(); }
  select(id: string | null) { this.selected = id; this.draw(); }

  draw() {
    cancelAnimationFrame(this.raf);
    this.raf = requestAnimationFrame(() => this.paint());
  }
  private paint() {
    const dpr = Math.min(devicePixelRatio, 2), w = this.host.clientWidth, h = this.host.clientHeight;
    if (!w || !h) return;
    if (this.cv.width !== Math.round(w * dpr) || this.cv.height !== Math.round(h * dpr)) { this.cv.width = Math.round(w * dpr); this.cv.height = Math.round(h * dpr); }
    const g = this.cv.getContext("2d")!;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = css("--bg"); g.fillRect(0, 0, w, h);
    g.setTransform(dpr * this.view.s, 0, 0, dpr * this.view.s, dpr * this.view.x, dpr * this.view.y);
    const on = (id: string) => !this.hl || this.hl.has(id) || this.marks.has(id);
    const lit = (e: GEdge) => this.hl && this.hl.has(e.from.id) && this.hl.has(e.to.id);
    const line2 = css("--line-2"), accent = css("--accent");
    for (const pass of [0, 1]) {
      for (const e of this.edges) {
        const L = !!lit(e) || (this.selected !== null && (e.from.id === this.selected || e.to.id === this.selected));
        if ((pass === 1) !== L) continue;
        const ax = e.from.x + NW / 2, ay = e.from.y, bx = e.to.x - NW / 2, by = e.to.y;
        g.beginPath();
        if (e.back) {
          const lift = 26 + Math.abs(ax - bx) * 0.08;
          g.moveTo(ax, ay); g.bezierCurveTo(ax + 40, ay - lift, bx - 40, by - lift, bx, by);
          g.setLineDash([4, 4]);
        } else {
          const mx = (ax + bx) / 2; g.moveTo(ax, ay); g.bezierCurveTo(mx, ay, mx, by, bx, by); g.setLineDash([]);
        }
        g.strokeStyle = L ? accent : line2;
        g.globalAlpha = L ? 0.95 : this.hl ? 0.18 : 0.55;
        g.lineWidth = L ? 1.6 / Math.max(0.6, this.view.s) : 0.8 / Math.max(0.6, this.view.s);
        g.stroke();
      }
    }
    g.setLineDash([]); g.globalAlpha = 1;
    const showText = this.view.s > 0.55;
    g.font = "9px JetBrains Mono, monospace"; g.textAlign = "center"; g.textBaseline = "middle";
    for (const n of this.nodes) {
      const vis = on(n.id);
      g.globalAlpha = vis ? 1 : 0.22;
      const x = n.x - NW / 2, y = n.y - NH / 2;
      g.fillStyle = n.kind === "cell" ? n.colour : css("--panel-3");
      g.beginPath(); g.roundRect(x, y, NW, NH, n.kind === "cell" ? 3 : 8); g.fill();
      const mark = this.marks.get(n.id);
      if (mark || n.id === this.selected || n === this.hover || (this.hl?.has(n.id) && this.hl.size < this.nodes.length)) {
        g.lineWidth = (n.id === this.selected ? 2.4 : 1.6) / Math.max(0.6, this.view.s);
        g.strokeStyle = n.id === this.selected ? "#ffffff" : mark ?? (n === this.hover ? "#ffffff" : accent);
        g.stroke();
      }
      if (showText) {
        g.fillStyle = n.kind === "cell" ? "#0b0f15" : accent;
        g.fillText(n.label.length > 10 ? n.label.slice(0, 9) + "…" : n.label, n.x, n.y + 0.5);
      }
    }
    g.globalAlpha = 1;
  }
  destroy() { this.ro.disconnect(); cancelAnimationFrame(this.raf); }
}
