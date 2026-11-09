// The Explore page's inspector: HTML for whatever is selected (the chip, a cell, a source line, a wire, a pin or a
// physical cell). Pure functions of the design and the simulator's current values; the page binds the links.

import type { Design, DesignCell, Lib } from "../../api/types";
import { evalExpr, parse, vars } from "../../sim/expr";
import { category, CATEGORY_LABEL, CATEGORY_VAR, describe, fullName, toWordsSafe } from "../../lib/cells";
import { cap } from "../../lib/kinds";
import { esc } from "../../ui/dom";
import { icon } from "../../ui/icons";

/** A 0/1 chip that lights when the value is 1. */
const bit = (v: number) => `<span class="bit${v ? " on" : ""}">${v}</span>`;
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;

/** A cell's category as a short title: "Flip-flop", "Basic gate"... */
export const categoryTitle = (type: string, lib: Lib) => cap(CATEGORY_LABEL[category(type, lib[type])].replace(/ \(.*\)$/, ""));

/** Up to 80 cell ids as links (data-cell). */
export function cellLinks(ids: string[]): string {
  if (!ids.length) return "";
  return `<div class="group"><span class="eyebrow">Cells</span><div class="chips">${ids.slice(0, 80).map((id) => `<button type="button" class="linkbtn" data-cell="${esc(id)}">${esc(id)}</button>`).join("")}${ids.length > 80 ? `<span class="muted">and ${ids.length - 80} more</span>` : ""}</div></div>`;
}

/** Nothing selected: what the chip is and does, and its numbers. */
export function chipHtml(d: Design, n: { logic: number; flops: number; usedSize: string }): string {
  return `
    <div class="insp-title"><div><b>${esc(d.title ?? d.name)}</b><small>${d.author ? `By ${esc(d.author)}. ` : ""}Tiny Tapeout 8, SkyWater 130 nm</small></div></div>
    ${d.how ? `<div class="group"><span class="eyebrow">How it works</span><p class="insp-role strong">${esc(d.how)}</p></div>`
      : `<p class="insp-role">${esc(d.intro ?? "A real chip: the warm-up design, placed and routed for SkyWater's 130 nm process. Every coloured block is a standard cell, a tiny pre-made circuit.")}</p>`}
    ${d.try ? `<div class="callout"><b>Try it</b><span>${esc(d.try)}</span></div>` : ""}
    <div class="statgrid">
      <div class="stat"><b>${n.logic}</b><span>Logic cells</span></div>
      <div class="stat"><b>${n.flops}</b><span>Flip-flops</span></div>
      <div class="stat"><b>${d.counts?.nets ?? d.counts?.nets_on_wires ?? "?"}</b><span>Wires</span></div>
      <div class="stat"><b class="area">${n.usedSize}<small>µm</small></b><span>Logic area</span></div>
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
}

/** One logic cell: what it is, its function, pins with live values, truth table and actions. */
export function cellHtml(c: DesignCell, d: Design, lib: Lib, value: (net: string) => number, o: { flopState: string; srcLine: string | null }): string {
  const L = lib[c.type], cat = category(c.type, L);
  const pinRows = Object.entries(c.pins).map(([p, n]) => {
    const dir = L?.pins[p] ?? "?";
    return `<tr><td>${esc(p)}${dir === "out" ? `<span class="dir">out</span>` : ""}</td><td><button type="button" class="linkbtn" data-net="${esc(n)}">${esc(n)}</button></td><td>${bit(value(n))}</td></tr>`;
  }).join("");
  const fns = L ? Object.entries(L.function) : [];
  const modInfo = c.module ? d.source.modules[c.module] : null;
  const out = Object.entries(c.pins).find(([p]) => L?.pins[p] === "out");
  return `
    <div class="insp-title"><div><b class="mono">${esc(c.id)}</b><small>${esc(fullName(c.type))}</small></div><span class="pill" style="color:var(${CATEGORY_VAR[cat]})"><i class="sw" style="width:6px;height:6px;border-radius:50%;background:currentColor"></i>${esc(cap(CATEGORY_LABEL[cat]))}</span></div>
    <p class="insp-role">${esc(describe(c.type, L))}</p>
    ${fns.length ? `<div class="fnbox">${fns.map(([o, f]) => L?.ff && /^IQ(_N)?$/.test(f) ? `<div>${esc(o)} = ${f === "IQ" ? "stored bit" : "NOT stored bit"}</div>` : `<div>${esc(o)} = ${esc(f)}</div><span>${esc(toWordsSafe(f))}</span>`).join("")}</div>` : ""}
    <dl class="kv">
      ${L?.ff ? `<dt>Stores</dt><dd>${o.flopState}<span class="muted">Next ${esc(L.ff.next_state)} on ${esc(L.ff.clocked_on)} rising${L.ff.clear ? `, cleared by ${esc(L.ff.clear)}` : ""}</span></dd>` : ""}
      <dt>Module</dt><dd>${c.module ? `${esc(c.module)}${c.inst ? `<span class="muted">Instance ${esc(c.inst)}</span>` : ""}` : "None"}</dd>
      <dt>Source</dt><dd>${c.src ? `<button type="button" class="linkbtn" data-ln="${c.src}">Line ${c.src}</button><span class="muted mono">${esc(o.srcLine ?? "")}</span>` : modInfo ? `<span class="muted">Added by the layout tools (clock tree)</span>` : "None"}</dd>
      <dt>Size</dt><dd>${c.w.toFixed(2)} × ${d.rowHeight} µm${L?.area ? `<span class="muted">${L.area.toFixed(2)} µm²</span>` : ""}</dd>
      <dt>Position</dt><dd>${c.x.toFixed(2)}, ${c.y.toFixed(2)} µm<span class="muted">${esc(c.orient)}</span></dd>
    </dl>
    <div class="group"><span class="eyebrow">Pins</span><table class="pins"><tr><th>Pin</th><th>Wire</th><th>Now</th></tr>${pinRows}</table></div>
    ${truthTableHtml(c, lib, value)}
    <div class="acts">
      ${out ? `<button type="button" class="btn sm" data-cone="in" data-net="${esc(out[1])}" title="Every cell that can change this one">${icon.arrowIn}Fan-in</button><button type="button" class="btn sm" data-cone="out" data-net="${esc(out[1])}" title="Every cell this one can reach">Fan-out${icon.arrowOut}</button>` : ""}
      <button type="button" class="btn sm" data-agent-cell="${esc(c.id)}" title="The Witness agent explains the block this cell belongs to; Yosys checks it">${icon.cube}Explain block</button>
      <button type="button" class="btn sm" data-ask="Explain cell ${esc(c.id)} (${esc(c.type)}) in simple terms: what it does and why this chip needs it.">${icon.sparkle}Ask</button>
    </div>`;
}

/** Truth table for a one-output gate with up to 4 inputs; the row the simulator is on is highlighted. */
export function truthTableHtml(c: DesignCell, lib: Lib, value: (net: string) => number): string {
  const L = lib[c.type];
  if (!L || L.ff) return "";
  const outs = Object.entries(L.function);
  if (outs.length !== 1) return "";
  const [o, f] = outs[0];
  let e; try { e = parse(f); } catch { return ""; }
  const ins = [...vars(e)].sort();
  if (!ins.length || ins.length > 4) return "";
  const now = ins.map((p) => value(c.pins[p] ?? ""));
  const rows: string[] = [];
  for (let s = 0; s < 1 << ins.length; s++) {
    const b = ins.map((_, i) => (s >> (ins.length - 1 - i)) & 1);
    const env = Object.fromEntries(ins.map((p, i) => [p, b[i]]));
    const cur = b.every((x, i) => x === now[i]);
    rows.push(`<tr class="${cur ? "now" : ""}">${b.map((x) => `<td>${x}</td>`).join("")}<td class="out">${evalExpr(e, env)}</td></tr>`);
  }
  return `<div class="group"><span class="eyebrow">Truth table <span class="faint">Highlighted row is live</span></span><table class="tt"><tr>${ins.map((p) => `<th>${esc(p)}</th>`).join("")}<th>${esc(o)}</th></tr>${rows.join("")}</table></div>`;
}

/** A source line: the cells synthesis made from it (or its module's). */
export function lineHtml(o: { text: string; file: string; ln: number; cells: string[]; direct: number; mod: string | null; uses: number }): string {
  const n = o.cells.length;
  return `
    <div class="insp-title"><div><b class="mono">${esc(o.text || `Line ${o.ln}`)}</b><small>${esc(o.file)}, line ${o.ln}</small></div><span class="pill gold">${plural(n, "cell")}</span></div>
    <p class="insp-role">${o.direct ? `After synthesis, this line became the ${o.direct === 1 ? "cell" : `${o.direct} cells`} lit in the view.` : o.mod ? `Part of module <b>${esc(o.mod)}</b>${o.uses ? `, used ${o.uses === 1 ? "once" : `${o.uses} times`}` : ""}. Its cells are lit in the view.` : "No cells come from this line."}</p>
    ${cellLinks(o.cells)}`;
}

/** A wire: its value, its driver and its readers. */
export function netHtml(net: string, v: number, driver: string | undefined, readers: { id: string; pin: string }[], isInput: boolean): string {
  return `
    <div class="insp-title"><div><b class="mono">${esc(net)}</b><small>Read by ${plural(readers.length, "cell")}</small></div>${bit(v)}</div>
    <p class="insp-role">A wire carries one bit from the cell that drives it to every cell that reads it.</p>
    <dl class="kv"><dt>Driven by</dt><dd>${driver ? `<button type="button" class="linkbtn" data-cell="${esc(driver)}">${esc(driver)}</button>` : isInput ? "Input pin" : "Nothing"}</dd></dl>
    ${readers.length ? `<div class="group"><span class="eyebrow">Read by</span><div class="chips">${readers.map((x) => `<button type="button" class="linkbtn" data-cell="${esc(x.id)}">${esc(x.id)}.${esc(x.pin)}</button>`).join("")}</div></div>` : ""}`;
}

/** A top-level pin: its value and the cells in its cone (fan-in for an output, fan-out for an input). */
export function portHtml(p: Design["ports"][number], v: number, cone: string[], canToggle: boolean): string {
  const out = p.dir === "out", name = esc(p.name);
  return `
    <div class="insp-title"><div><b class="mono">${name}</b><small>${out ? "Output" : "Input"} at ${p.x.toFixed(1)}, ${p.y.toFixed(1)} µm</small></div>${bit(v)}</div>
    <p class="insp-role">${out ? `Lit: every cell that can change ${name}, back to the flip-flops. Like Go to Definition, for silicon.` : `Lit: every cell ${name} can reach before the next flip-flop. Like Find Usages, for silicon.`}</p>
    ${canToggle ? `<div class="acts"><button class="btn sm" type="button" data-toggle="${name}">Set to ${v ? 0 : 1}</button></div>` : ""}
    ${cellLinks(cone)}`;
}

/** Filler, tap or decap: no logic, just what it is for. */
export function physHtml(p: Design["phys"][number], lib: Lib): string {
  return `<div class="insp-title"><div><b class="mono">${esc(p.type)}</b><small>${esc(fullName(p.type))}</small></div><span class="pill">No logic</span></div>
    <p class="insp-role">${esc(describe(p.type, lib[p.type]))}</p>
    <dl class="kv"><dt>Position</dt><dd>${p.x.toFixed(2)}, ${p.y.toFixed(2)} µm</dd><dt>Width</dt><dd>${p.w.toFixed(2)} µm</dd></dl>`;
}
