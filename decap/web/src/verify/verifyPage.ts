// Verify page (verify.html): the 4-bit CLA checked input by input on its real 3D layout.
// Step 1: the taped-out chip, every input combination against a + b + ci (and the bench's Yosys proof).
// Step 2: "rev B", the same layout with one gate changed (a seeded bug). Wrong answers build a heatmap of
// each cell's fault probability on the chip (see engine.ts for the model).

import "../styles.css";
import "./verify.css";
import { loadDesign } from "../data/load";
import type { Design, Lib } from "../data/types";
import { Stage3D } from "../view/stage3d";
import { Verifier, type Suspect, type Vec } from "./engine";

const CHIP = "tt_um_Electom_cla_4bits";
const BUG = { cell: "c53", to: "and2_1" };   // rev B: one OR gate in the carry logic became an AND
const SPEED = Number(new URLSearchParams(location.search).get("speed")) || 1;   // ?speed=2 runs twice as fast
const RATE = { 1: 70 * SPEED, 2: 46 * SPEED };  // inputs checked per second, per step

const root = document.getElementById("vf")!;
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));
const pct = (p: number) => (p >= 0.995 ? "99" : p < 0.01 && p > 0 ? "<1" : String(Math.round(p * 100)));
const fmtIns = (v: Record<string, number>) => `a=${v.a} b=${v.b} ci=${v.ci}`;
const sum = (v: Record<string, number>) => (v.co << 4) | v.s;

/** Heat ramp for fault probability, on a log scale so 1% still glows. */
const STOPS: [number, [number, number, number]][] = [[0, [36, 64, 140]], [0.35, [120, 70, 200]], [0.6, [255, 159, 10]], [0.82, [255, 69, 58]], [1, [255, 236, 220]]];
function heat(p: number): string {
  const t = Math.max(0, Math.min(1, 1 + Math.log10(Math.max(p, 1e-9)) / 3));
  let i = 0; while (i < STOPS.length - 2 && t > STOPS[i + 1][0]) i++;
  const [t0, a] = STOPS[i], [t1, b] = STOPS[i + 1], k = (t - t0) / (t1 - t0 || 1);
  return `#${a.map((x, j) => Math.round(x + (b[j] - x) * k).toString(16).padStart(2, "0")).join("")}`;
}
const GREEN = "#30d158", FLASH = "#ffffff";

root.innerHTML = `
  <main class="vf-view" id="vfView"></main>
  <header class="vf-top">
    <a class="vf-brand" href="/#/stage"><b><span>b</span>ASIC</b><i>Verify</i></a>
    <ol class="vf-steps">
      <li data-step="1"><b>1</b>4-bit CLA <small>as taped out</small></li>
      <li data-step="2"><b>2</b>CLA rev B <small>one change, unknown</small></li>
    </ol>
  </header>
  <aside class="vf-panel">
    <section class="vf-spec"><span class="ey">Spec</span><code>{co, s} = a + b + ci</code><span class="muted" id="vfChip"></span></section>
    <section class="vf-count">
      <div><b id="vfDone">0</b><span>/ <span id="vfTotal">512</span> inputs</span></div>
      <div class="vf-pf"><span class="ok"><b id="vfPass">0</b> right</span><span class="bad"><b id="vfFail">0</b> wrong</span></div>
      <canvas id="vfStrip" aria-label="Every input combination, in the order checked: green right, red wrong"></canvas>
      <div class="vf-now mono" id="vfNow">&nbsp;</div>
    </section>
    <section class="vf-sus" id="vfSusSec" hidden>
      <span class="ey">Fault probability <span class="muted">per cell</span></span>
      <ol id="vfSus"></ol>
      <div class="vf-ramp"><i></i><span>&lt;0.1%</span><span>1%</span><span>10%</span><span>100%</span></div>
    </section>
    <section class="vf-log" id="vfLogSec" hidden><span class="ey">Wrong answers</span><ul class="mono" id="vfLog"></ul></section>
    <section class="vf-insp" id="vfInsp" hidden></section>
    <p class="vf-note" id="vfNote"></p>
  </aside>
  <div class="vf-verdict" id="vfVerdict" hidden></div>
  <footer class="vf-ctrl">
    <button class="vf-btn primary" id="vfGo" type="button">Verify the 4-bit CLA</button>
    <button class="vf-btn" id="vfRevB" type="button" disabled>Introduce rev B</button>
    <button class="vf-btn plain" id="vfReplay" type="button" disabled>Replay</button>
  </footer>`;
const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;

let design: Design, lib: Lib, view: Stage3D;
let bugCell: Design["cells"][number], bugFrom = "";
let proof: { method?: string; seconds?: number; proven?: boolean } | null = null;
let step: 1 | 2 = 1;
let ver: Verifier | null = null;
let order: Record<string, number>[] = [];
let results: boolean[] = [];
let t0 = 0, done = 0, running = false, lastWires = 0;
let byX: string[] = [];                      // logic cells left to right: the sweep order
const flash = new Map<string, number>();
let selected: string | null = null;

function shuffled<T>(xs: T[], seed: number): T[] {
  let s = seed;
  const rnd = () => ((s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
  return xs.map((x) => [rnd(), x] as const).sort((a, b) => a[0] - b[0]).map((x) => x[1]);
}

function setStep(n: 1 | 2) {
  step = n;
  document.querySelectorAll<HTMLElement>(".vf-steps li").forEach((li) => li.classList.toggle("on", +li.dataset.step! === n));
  // Rev B: the same layout with one gate swapped in place, so the 3D view and its labels show the changed chip.
  bugCell.type = n === 1 ? bugFrom : BUG.to;
  const d = design;
  ver = new Verifier(d, lib);
  order = shuffled(ver.vecs, 7);
  results = []; done = 0; flash.clear(); select(null);
  $("vfTotal").textContent = String(order.length);
  $("vfChip").textContent = n === 1 ? "tt_um_Electom_cla_4bits · Tiny Tapeout 8" : "Same layout, same pins. Something inside changed.";
  $("vfSusSec").hidden = n === 1; $("vfLogSec").hidden = true; $("vfLog").innerHTML = "";
  $("vfVerdict").hidden = true; $("vfVerdict").className = "vf-verdict";
  $("vfNote").textContent = n === 1
    ? "Every input combination goes through the real gate netlist from the GDS, one clock each, and the outputs are compared with the spec."
    : `Fault model: one cell computes the wrong value. For each wrong answer we test every logic cell: would flipping its output make all outputs right? Probabilities are over ${ver.candidates.length} logic cells, uniform prior.`;
  paint(); drawStrip(); renderCounts(null); renderSuspects();
}

function start() {
  if (running) return;
  if (done >= order.length) setStep(step);
  running = true; t0 = performance.now() - (done / RATE[step]) * 1000;
  ($("vfGo") as HTMLButtonElement).disabled = true; ($("vfRevB") as HTMLButtonElement).disabled = true; ($("vfReplay") as HTMLButtonElement).disabled = true;
  view.flyTo(design.cells.map((c) => c.id));
  requestAnimationFrame(tick);
}

function tick(now: number) {
  const due = Math.min(order.length, Math.floor(((now - t0) / 1000) * RATE[step]));
  let last: Vec | null = null;
  while (done < due) {
    const r = ver!.check(order[done++]);
    results.push(r.vec.ok); last = r.vec;
    if (!r.vec.ok) { for (const id of r.hits) flash.set(id, now); logFail(r.vec, r.hits.length); }
  }
  if (last) {
    // Wires show the live signal values, refreshed a few times a second (every frame washes the view out in bloom).
    if (now - lastWires > 220 || done >= order.length) { view.setValues((n) => ver!.value(n)); lastWires = now; }
    renderCounts(last); drawStrip(); if (step === 2) renderSuspects();
  }
  paint(now);
  if (done < order.length) requestAnimationFrame(tick);
  else finish();
}

/** Cells left of the sweep are checked; on rev B, implicated cells show their fault probability. */
function paint(now = performance.now()) {
  const reached = Math.round((done / order.length) * byX.length);
  const seen = new Set(byX.slice(0, reached));
  const marks = new Map<string, string>();
  const sus = step === 2 && ver!.fails ? new Map(ver!.suspects().map((s) => [s.id, s.p])) : null;
  for (const id of byX) {
    const p = sus?.get(id) ?? 0;
    const f = flash.get(id);
    if (f !== undefined && now - f < 110) marks.set(id, FLASH);
    else if (p > 1e-3) marks.set(id, heat(p));
    else if (seen.has(id)) marks.set(id, GREEN);
  }
  view.setMarks(marks);
  const end = done >= order.length;
  // At the end of rev B only the implicated cells stay lit, so the hot spot reads at a glance.
  if (end && sus) {
    const hot = new Set([...marks].filter(([, c]) => c !== GREEN).map(([id]) => id));
    const nets = new Set(design.cells.filter((c) => hot.has(c.id)).flatMap((c) => Object.values(c.pins)));
    view.setHighlight(hot, nets);
  } else view.setHighlight(end ? null : new Set([...seen, ...marks.keys()]));
}

function renderCounts(v: Vec | null) {
  $("vfDone").textContent = String(done);
  $("vfPass").textContent = String(results.filter(Boolean).length);
  $("vfFail").textContent = String(results.filter((x) => !x).length);
  if (v) $("vfNow").innerHTML = `${fmtIns(v.ins)} → ${sum(v.got)} ${v.ok ? `<span class="ok">✓</span>` : `<span class="bad">✗ expected ${sum(v.expect)}</span>`}`;
}

function logFail(v: Vec, n: number) {
  $("vfLogSec").hidden = false;
  const li = document.createElement("li");
  li.innerHTML = `${fmtIns(v.ins)} <span class="muted">→</span> <span class="bad">${sum(v.got)}</span> <span class="muted">want ${sum(v.expect)} · ${n} cell${n === 1 ? "" : "s"} could explain</span>`;
  const ul = $("vfLog"); ul.prepend(li); while (ul.children.length > 5) ul.lastChild!.remove();
}

function renderSuspects() {
  const list = $("vfSus");
  if (!ver || !ver.fails) { list.innerHTML = `<li class="muted empty">No wrong answers yet: every cell at 0%.</li>`; return; }
  const top = ver.suspects().filter((s) => s.p > 0.001).slice(0, 6);
  list.innerHTML = top.map((s: Suspect) => `<li data-id="${s.id}" class="${s.id === selected ? "sel" : ""}">
      <span class="id mono">${esc(s.id)}</span><span class="ty mono">${esc(s.type)}</span>
      <span class="bar"><i style="width:${(s.p * 100).toFixed(1)}%;background:${heat(s.p)}"></i></span>
      <b>${pct(s.p)}%</b><small>${s.explains}/${ver!.fails}</small></li>`).join("");
}

function drawStrip() {
  const cv = $<HTMLCanvasElement>("vfStrip"), dpr = devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  if (cv.width !== w * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
  const g = cv.getContext("2d")!; g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
  const cols = 64, rows = Math.ceil(order.length / cols), cw = w / cols, ch = h / rows;
  for (let i = 0; i < order.length; i++) {
    g.fillStyle = i >= results.length ? "rgba(255,255,255,.06)" : results[i] ? GREEN : "#ff453a";
    g.fillRect((i % cols) * cw + 0.5, Math.floor(i / cols) * ch + 0.5, cw - 1, ch - 1);
  }
}

function finish() {
  running = false;
  ($("vfReplay") as HTMLButtonElement).disabled = false;
  ($("vfGo") as HTMLButtonElement).disabled = false;
  ($("vfRevB") as HTMLButtonElement).disabled = step === 2;
  ($("vfGo") as HTMLButtonElement).textContent = step === 1 ? "Verify the 4-bit CLA" : "Back to the CLA";
  paint();
  const V = $("vfVerdict"); V.hidden = false;
  if (step === 1) {
    const ok = ver!.fails === 0;
    V.className = `vf-verdict ${ok ? "good" : "bad"}`;
    const y = proof?.proven ? `<p>Yosys equivalence against the author's source: <b>proven</b>${proof.seconds != null ? ` in ${proof.seconds.toFixed(2)} s` : ""}${proof.method ? ` <span class="muted">(${esc(proof.method)})</span>` : ""}.</p>` : "";
    V.innerHTML = `<span class="ey">${ok ? "Verified" : "Mismatch"}</span><h2>${order.length - ver!.fails} / ${order.length} correct</h2>
      <p>Every combination of a, b and ci gives a + b + ci.</p>${y}`;
    ($("vfRevB") as HTMLButtonElement).disabled = false;
    if (location.hash === "#auto") setTimeout(() => $("vfRevB").click(), 3000);
  } else {
    const top = ver!.suspects();
    const s = top[0], tied = top.filter((x) => Math.abs(x.p - s.p) < 1e-6);
    V.className = "vf-verdict bad";
    V.innerHTML = `<span class="ey">Fault found</span><h2>${ver!.fails} / ${order.length} wrong</h2>
      <p>Most likely cell: <b class="mono">${tied.map((x) => esc(x.id)).join(", ")}</b> <span class="mono muted">${esc(s.type)}</span> at <b>${pct(s.p)}%</b>.
      It alone explains all ${s.explains} wrong answers.</p>
      <p class="reveal" id="vfReveal" hidden>The seeded change: <b class="mono">${BUG.cell}</b> was an OR gate (or2_1) in the taped-out chip and is an AND gate (and2_1) in rev B. ${tied.some((x) => x.id === BUG.cell) ? `<span class="ok">Found ✓</span>` : `<span class="bad">Missed</span>`}</p>`;
    view.flyTo([s.id]); select(s.id);
    setTimeout(() => { const r = document.getElementById("vfReveal"); if (r) r.hidden = false; }, 1800);
  }
}

function select(id: string | null) {
  selected = id; view?.select(id);
  const box = $("vfInsp");
  const c = id ? design.cells.find((x) => x.id === id) : null;
  if (!c || !ver) { box.hidden = true; renderSuspects(); return; }
  const s = ver.suspects().find((x) => x.id === id);
  const type = step === 2 && id === BUG.cell && done >= order.length ? `${BUG.to} <span class="muted">(rev A: ${bugFrom})</span>` : esc(c.type);
  box.hidden = false;
  box.innerHTML = `<span class="ey">Cell <span class="mono">${esc(c.id)}</span></span>
    <div class="mono">${type}</div>
    ${s ? `<div>${step === 2 && ver.fails ? `Fault probability <b>${pct(s.p)}%</b>, explains ${s.explains} of ${ver.fails} wrong answers.` : "Logic cell: a candidate if anything goes wrong."}</div>` : `<div class="muted">Wiring or clock cell, not scored.</div>`}`;
  renderSuspects();
}

async function boot() {
  ({ design, lib } = await loadDesign(CHIP));
  proof = await fetch(`/data/chips/${CHIP}.json`).then((r) => (r.ok ? r.json() : null)).then((j) => (j?.verdict === "proven" ? { ...j.proof, proven: true } : null)).catch(() => null);
  bugCell = design.cells.find((c) => c.id === BUG.cell)!; bugFrom = bugCell.type;
  byX = design.cells.filter((c) => lib[c.type] && !lib[c.type].phys).sort((a, b) => a.x - b.x).map((c) => c.id);
  view = new Stage3D($("vfView"), design, lib, {
    onCell: (id) => select(id), onNet: () => {}, onPort: () => {}, onPhys: () => {},
  });
  view.start();
  setTimeout(() => view.flyTo(design.cells.map((c) => c.id)), 400);
  setStep(1);
  $("vfGo").onclick = () => { if (step !== 1) setStep(1); start(); };
  $("vfRevB").onclick = () => { setStep(2); start(); };
  $("vfReplay").onclick = () => { setStep(step); start(); };
  $("vfSus").onclick = (e) => { const li = (e.target as HTMLElement).closest<HTMLElement>("li[data-id]"); if (li) { select(li.dataset.id!); view.flyTo([li.dataset.id!]); } };
  addEventListener("keydown", (e) => {
    if (e.key === " ") { e.preventDefault(); if (!running) (step === 1 && done >= order.length ? $("vfRevB") : $("vfGo")).click(); }
    if (e.key === "2" && !running) $("vfRevB").click();
    if (e.key === "1" && !running) $("vfGo").click();
  });
  addEventListener("resize", drawStrip);
  // #auto plays both steps hands-free (presenter mode); #revb jumps straight to the buggy chip.
  if (location.hash === "#auto") setTimeout(() => $("vfGo").click(), 1500);
  if (location.hash === "#revb") setTimeout(() => { setStep(2); start(); }, 1500);
}
boot().catch((e) => { root.insertAdjacentHTML("beforeend", `<div class="vf-err">Couldn't load the CLA layout (${esc(String(e?.message ?? e))}). Run <code>npm run stage-data</code> in decap/web.</div>`); });
