// Verify page (#/verify, the "Verify" button in the header): the 4-bit CLA verified on its real 3D layout, the way it scales to big chips.
// Step 1: the taped-out chip. 1000 random input vectors at once (bit-parallel simulation), compared with
//         a + b + ci, giving a predicted failure rate over every input; the bench's Yosys proof backs it.
// Step 2: "rev B", the same layout with one gate changed (a seeded bug). Wrong outputs are traced back through
//         the netlist in one pass from the sampled failures; the top suspects are confirmed by re-simulation. The heatmap is each cell's
//         fault probability (engine.ts has the model).

import { loadChip, loadDesign } from "../api/core";
import { esc } from "../ui/dom";
import type { Design, Lib } from "../api/types";
import { Stage3D } from "../three/stage3d";
import { SAMPLES, verify, type Result } from "../sim/verify";

const CHIP = "tt_um_Electom_cla_4bits";
const BUG = { cell: "c53", to: "and2_1" };   // rev B: one OR gate in the carry logic became an AND
const SPEED = Number(new URLSearchParams(location.search).get("speed")) || 1;   // ?speed=0.5 slows it down
/** Animation beats (ms): simulate (forward wave), compare, trace back (backward wave), confirm. */
const T = { sim: 900 / SPEED, cmp: 350 / SPEED, trace: 1400 / SPEED, confirm: 600 / SPEED };

const root = document.createElement("div");
root.className = "vf";
const pct = (p: number) => (p >= 0.995 ? "99" : p < 0.01 && p > 0 ? "<1" : String(Math.round(p * 100)));
/** A rate as a percentage: one decimal, "<0.1" for tiny non-zero rates. */
const rate = (x: number) => (x > 0 && x < 0.001 ? "<0.1" : (x * 100).toFixed(1).replace(/\.0$/, ""));
const ms = (x: number) => (x < 0.1 ? "<0.1 ms" : `${x.toFixed(1)} ms`);

/** Heat ramp for fault probability, on a log scale so 1% still glows. */
const STOPS: [number, [number, number, number]][] = [[0, [36, 64, 140]], [0.35, [120, 70, 200]], [0.6, [255, 159, 10]], [0.82, [255, 69, 58]], [1, [255, 236, 220]]];
function heat(p: number): string {
  const t = Math.max(0, Math.min(1, 1 + Math.log10(Math.max(p, 1e-9)) / 3));
  let i = 0; while (i < STOPS.length - 2 && t > STOPS[i + 1][0]) i++;
  const [t0, a] = STOPS[i], [t1, b] = STOPS[i + 1], k = (t - t0) / (t1 - t0 || 1);
  return `#${a.map((x, j) => Math.round(x + (b[j] - x) * k).toString(16).padStart(2, "0")).join("")}`;
}
const GREEN = "#30d158", CLEAR = "#2c6e49", FLASH = "#ffffff";

root.innerHTML = `
  <main class="vf-view" id="vfView"></main>
  <header class="vf-top">
    <ol class="vf-steps">
      <li data-step="1"><b>1</b>4-bit CLA <small>as taped out</small></li>
      <li data-step="2"><b>2</b>CLA rev B <small>one change, unknown</small></li>
    </ol>
  </header>
  <aside class="vf-panel">
    <section class="vf-spec"><span class="ey">Spec</span><code>{co, s} = a + b + ci</code><span class="muted" id="vfChip"></span></section>
    <section class="vf-count">
      <div><b id="vfDone">0</b><span>/ <span id="vfTotal">1000</span> random vectors</span></div>
      <small class="muted" id="vfSpace"></small>
      <div class="vf-pf"><span class="ok"><b id="vfPass">0</b> right</span><span class="bad"><b id="vfFail">0</b> wrong</span></div>
      <canvas id="vfStrip" aria-label="Each sampled vector, 32 per word: green right, red wrong"></canvas>
      <div class="vf-pred" id="vfPred" hidden></div>
    </section>
    <section><span class="ey">Pipeline</span><ol class="vf-pipe" id="vfPipe"></ol></section>
    <section class="vf-sus" id="vfSusSec" hidden>
      <span class="ey">Fault probability <span class="muted">per cell</span></span>
      <ol id="vfSus"></ol>
      <div class="vf-ramp"><i></i><span>&lt;0.1%</span><span>1%</span><span>10%</span><span>100%</span></div>
    </section>
    <section class="vf-log" id="vfLogSec" hidden><span class="ey">Wrong answers <span class="muted">(examples)</span></span><ul class="mono" id="vfLog"></ul></section>
    <section class="vf-insp" id="vfInsp" hidden></section>
    <p class="vf-note" id="vfNote"></p>
  </aside>
  <div class="vf-verdict" id="vfVerdict" hidden></div>
  <footer class="vf-ctrl">
    <button class="vf-btn primary" id="vfGo" type="button">Verify the 4-bit CLA</button>
    <button class="vf-btn" id="vfRevB" type="button" disabled>Introduce rev B</button>
    <button class="vf-btn plain" id="vfReplay" type="button" disabled>Replay</button>
  </footer>`;
const byId = <T extends HTMLElement = HTMLElement>(id: string) => root.querySelector(`#${id}`) as T;
const btn = (id: string) => byId<HTMLButtonElement>(id);

let design: Design, lib: Lib, view: Stage3D;
let bugCell: Design["cells"][number], bugFrom = "";
let proof: { method?: string; seconds?: number; proven?: boolean } | null = null;
let step: 1 | 2 = 1;
let R: Result | null = null;
let auto = false;   // #/verify/auto plays both steps hands-free (presenter mode)
let running = false, selected: string | null = null, shown = 0;
const level = new Map<string, number>();

interface Stage { key: string; name: string; detail: () => string; time: () => string }
const STAGES: Stage[] = [
  { key: "sim", name: `Simulate ${SAMPLES} random vectors`, detail: () => `${R!.patterns} of ${R!.space} possible inputs, as ${R!.words} words × 32 lanes, one pass over the netlist`, time: () => ms(R!.timing.simMs) },
  { key: "cmp", name: "Compare with the spec, predict", detail: () => `${R!.wrong} of ${R!.patterns} wrong → failure rate ${predText()}`, time: () => "" },
  { key: "trace", name: "Trace back from wrong outputs", detail: () => `one reverse pass per output pin through ${R!.levels.length} cells`, time: () => ms(R!.timing.traceMs) },
  { key: "confirm", name: "Confirm the top suspects", detail: () => `re-simulate the sample with each of ${R!.suspects.filter((s) => s.confirmed).length} cells inverted`, time: () => ms(R!.timing.confirmMs) },
];

function renderPipe(active: string | null, doneKeys: Set<string>) {
  const stages = step === 1 ? STAGES.slice(0, 2) : STAGES;
  byId("vfPipe").innerHTML = stages.map((s, i) => {
    const st = doneKeys.has(s.key) ? "done" : s.key === active ? "on" : "";
    return `<li class="${st}"><b>${doneKeys.has(s.key) ? "✓" : i + 1}</b><div><span>${s.name}</span>${st ? `<small>${esc(s.detail())}</small>` : ""}</div><em>${doneKeys.has(s.key) ? s.time() : ""}</em></li>`;
  }).join("");
}

function setStep(n: 1 | 2) {
  step = n;
  document.querySelectorAll<HTMLElement>(".vf-steps li").forEach((li) => li.classList.toggle("on", +li.dataset.step! === n));
  // Rev B: the same layout with one gate swapped in place, so the 3D view and its labels show the changed chip.
  bugCell.type = n === 1 ? bugFrom : BUG.to;
  R = verify(design, lib);
  for (const l of R.levels) level.set(l.id, l.level);
  shown = 0; selected = null; view.select(null);
  byId("vfTotal").textContent = String(R.patterns);
  byId("vfSpace").textContent = `drawn at random from ${R.space} possible inputs (a big chip has 2^n, too many to try)`;
  byId("vfPred").hidden = true;
  byId("vfChip").textContent = n === 1 ? "tt_um_Electom_cla_4bits · Tiny Tapeout 8" : "Same layout, same pins. Something inside changed.";
  byId("vfSusSec").hidden = true; byId("vfLogSec").hidden = true; byId("vfInsp").hidden = true;
  byId("vfVerdict").hidden = true; byId("vfVerdict").className = "vf-verdict";
  byId("vfNote").textContent = n === 1
    ? `The real gate netlist from the GDS, one clock from reset. We don't try every input: ${R.patterns} random vectors predict the failure rate over all of them, the same way it works on a chip with 2^64 inputs. Each 32-bit word carries 32 vectors, so the cost is one pass over the gates per word.`
    : `Fault model: one cell computes the wrong value. A cell explains a wrong vector if inverting its output there fixes every output. Probabilities over ${R.suspects.length} logic cells from the ${R.wrong} sampled failures, uniform prior. Every stage is a pass over the netlist, so the work grows with gate count and sample size, not with the input space.`;
  renderCounts(0); drawStrip(0); renderPipe(null, new Set());
  view.setMarks(null); view.setHighlight(null);
}

function renderCounts(frac: number) {
  const k = Math.round(frac * R!.patterns);
  shown = k;
  const okUpTo = R!.ok.slice(0, k);
  byId("vfDone").textContent = String(k);
  byId("vfPass").textContent = String(okUpTo.filter(Boolean).length);
  byId("vfFail").textContent = String(okUpTo.length - okUpTo.filter(Boolean).length);
}

function drawStrip(frac: number) {
  const cv = byId<HTMLCanvasElement>("vfStrip"), dpr = devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  if (!w || !R) return;
  if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
  const g = cv.getContext("2d")!; g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, w, h);
  // One row per 32-bit word: the whole word lands at once, which is what bit-parallel simulation does.
  const rows = R.words, cols = 32, cw = w / cols, ch = h / rows, wordsDone = Math.floor(frac * rows + 1e-9);
  for (let i = 0; i < R.patterns; i++) {
    const r = Math.floor(i / 32);
    g.fillStyle = r >= wordsDone ? "rgba(255,255,255,.06)" : R.ok[i] ? GREEN : "#ff453a";
    g.fillRect((i % cols) * cw + 0.5, r * ch + 0.5, cw - 1, ch - 1);
  }
}

/** Run one step as a timeline: forward wave, compare, (rev B) backward wave and confirm, verdict. */
function start() {
  if (running) return;
  setStep(step);
  running = true;
  btn("vfGo").disabled = true; btn("vfRevB").disabled = true; btn("vfReplay").disabled = true;
  view.flyTo(design.cells.map((c) => c.id));
  view.setValues(R!.sample);
  const sus = new Map(R!.suspects.map((s) => [s.id, s.p]));
  const traced = new Set(R!.tracedNets);
  const maxL = R!.maxLevel;
  let t0 = -1;
  const ends = { sim: T.sim, cmp: T.sim + T.cmp, trace: T.sim + T.cmp + (step === 2 ? T.trace : 0), confirm: T.sim + T.cmp + (step === 2 ? T.trace + T.confirm : 0) };
  const done = new Set<string>();
  let lastStage = "";
  const frame = (now: number) => {
    if (t0 < 0) t0 = now;   // the first frame starts the clock (rAF times can predate performance.now())
    const t = now - t0;
    const marks = new Map<string, string>();
    let stage = "sim";
    if (t < ends.sim) {
      // Forward wave: inputs to outputs, level by level; the pattern words land as it goes.
      const f = t / T.sim, front = f * (maxL + 1);
      for (const [id, l] of level) if (l <= front) marks.set(id, GREEN);
      renderCounts(f); drawStrip(f);
      view.setHighlight(new Set(marks.keys()));
    } else if (t < ends.cmp) {
      stage = "cmp"; done.add("sim");
      for (const id of level.keys()) marks.set(id, GREEN);
      renderCounts(1); drawStrip(1); showPred();
      view.setHighlight(null);
      if (R!.wrong) showExamples();
    } else if (step === 2 && t < ends.trace) {
      // Backward wave: from the output pins towards the inputs, revealing each cell's fault probability.
      stage = "trace"; done.add("sim"); done.add("cmp");
      const f = (t - ends.cmp) / T.trace, front = (1 - f) * (maxL + 1);
      for (const [id, l] of level) {
        if (l < front) { marks.set(id, GREEN); continue; }
        const p = sus.get(id) ?? 0;
        marks.set(id, p > 1e-3 ? heat(p) : CLEAR);
      }
      view.setHighlight(null, traced);
      byId("vfSusSec").hidden = false; renderSuspects(f);
    } else if (step === 2 && t < ends.confirm) {
      stage = "confirm"; done.add("trace");
      const pulse = Math.floor((t - ends.trace) / 150) % 2 === 0;
      for (const [id] of level) { const p = sus.get(id) ?? 0; marks.set(id, p > 1e-3 ? heat(p) : CLEAR); }
      if (pulse) for (const s of R!.suspects) if (s.confirmed) marks.set(s.id, FLASH);
      renderSuspects(1);
    } else {
      STAGES.forEach((s) => done.add(s.key));
      view.setMarks(finalMarks(sus));
      renderPipe(null, done);
      renderCounts(1); drawStrip(1); showPred(); if (R!.wrong) showExamples();
      finish(traced);
      return;
    }
    if (stage !== lastStage) { renderPipe(stage, done); lastStage = stage; }
    view.setMarks(marks);
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}

function finalMarks(sus: Map<string, number>) {
  const m = new Map<string, string>();
  for (const id of level.keys()) { const p = step === 2 ? sus.get(id) ?? 0 : 0; m.set(id, p > 1e-3 ? heat(p) : step === 2 ? CLEAR : GREEN); }
  return m;
}

/** Failure rate predicted from the sample: "12.5% (95%: 10.6–14.7%)", or "< 0.3% (95%)" when nothing failed. */
function predText() {
  return R!.wrong ? `${rate(R!.rate)}% (95%: ${rate(R!.rateLo)}–${rate(R!.rateHi)}%)` : `< ${rate(R!.rateHi)}% (95%)`;
}
function showPred() {
  const el = byId("vfPred"); el.hidden = false;
  el.innerHTML = R!.wrong
    ? `Predicted: <b>${rate(R!.rate)}%</b> of all inputs fail <span class="muted">(95%: ${rate(R!.rateLo)}–${rate(R!.rateHi)}%, about ${R!.predictedWrong} of ${R!.space})</span>`
    : `Predicted: <b>&lt; ${rate(R!.rateHi)}%</b> of inputs fail <span class="muted">(95%, from 0 of ${R!.patterns})</span>`;
}

function showExamples() {
  if (!byId("vfLogSec").hidden) return;
  byId("vfLogSec").hidden = false;
  const s = (x: Record<string, number>) => `a=${x.a} b=${x.b} ci=${x.ci}`;
  byId("vfLog").innerHTML = R!.examples.map((e) => `<li>${s(e.ins)} <span class="muted">→</span> <span class="bad">${e.got}</span> <span class="muted">want ${e.want}</span></li>`).join("");
}

/** The suspect list; during the backward wave, more of it fills in. */
function renderSuspects(frac: number) {
  const top = R!.suspects.filter((s) => s.p > 0.001).slice(0, 6);
  byId("vfSus").innerHTML = top.map((s) => `<li data-id="${s.id}" class="${s.id === selected ? "sel" : ""}">
      <span class="id mono">${esc(s.id)}</span><span class="ty mono">${esc(s.type)}</span>
      <span class="bar"><i style="width:${(s.p * 100 * Math.min(1, frac * 1.4)).toFixed(1)}%;background:${heat(s.p)}"></i></span>
      <b>${frac >= 1 ? pct(s.p) + "%" : "…"}</b><small>${s.confirmed && frac >= 1 ? "✓ " : ""}${s.explains}/${R!.wrong}</small></li>`).join("");
}

function finish(traced: Set<string>) {
  running = false;
  btn("vfReplay").disabled = false; btn("vfGo").disabled = false;
  btn("vfRevB").disabled = step === 2;
  btn("vfGo").textContent = step === 1 ? "Verify the 4-bit CLA" : "Back to the CLA";
  const V = byId("vfVerdict"); V.hidden = false;
  const total = R!.timing.simMs + R!.timing.traceMs + R!.timing.confirmMs;
  if (step === 1) {
    const ok = R!.wrong === 0;
    V.className = `vf-verdict ${ok ? "good" : "bad"}`;
    const y = proof?.proven ? `<p>Yosys equivalence against the author's source: <b>proven</b>${proof.seconds != null ? ` in ${proof.seconds.toFixed(2)} s` : ""}.</p>` : "";
    V.innerHTML = `<span class="ey">${ok ? "Predicted correct" : "Mismatch"}</span><h2>${R!.patterns - R!.wrong} / ${R!.patterns} random vectors correct</h2>
      <p>Predicted failure rate over all ${R!.space} inputs of a, b and ci: ${predText()}. Checked in ${ms(total)}.</p>${y}`;
    btn("vfRevB").disabled = false;
    view.setHighlight(null);
    if (auto) setTimeout(() => { setStep(2); start(); }, 2500);
  } else {
    byId("vfSusSec").hidden = false; renderSuspects(1);
    const s = R!.suspects[0], tied = R!.suspects.filter((x) => Math.abs(x.p - s.p) < 1e-6);
    V.className = "vf-verdict bad";
    V.innerHTML = `<span class="ey">Fault found</span><h2>${rate(R!.rate)}% of inputs predicted wrong</h2>
      <p>${R!.wrong} of ${R!.patterns} random vectors wrong: about ${R!.predictedWrong} of ${R!.space} inputs (95%: ${rate(R!.rateLo)}–${rate(R!.rateHi)}%).</p>
      <p>Most likely cell: <b class="mono">${tied.map((x) => esc(x.id)).join(", ")}</b> <span class="mono muted">${esc(s.type)}</span> at <b>${pct(s.p)}%</b>,
      explaining ${s.explains === R!.wrong ? "all " : ""}${s.explains} of the ${R!.wrong} sampled failures. Found in ${ms(total)}.</p>
      <p class="reveal" id="vfReveal" hidden>The seeded change: <b class="mono">${BUG.cell}</b> was an OR gate (${esc(bugFrom)}) in the taped-out chip and is an AND gate (${BUG.to}) in rev B. ${tied.some((x) => x.id === BUG.cell) ? `<span class="ok">Found ✓</span>` : `<span class="bad">Missed</span>`}</p>`;
    const hot = new Set(R!.suspects.filter((x) => x.p > 1e-3).map((x) => x.id));
    view.setHighlight(hot, new Set(design.cells.filter((c) => hot.has(c.id)).flatMap((c) => Object.values(c.pins)).filter((n) => traced.has(n))));
    const nets = new Set(design.cells.filter((c) => hot.has(c.id)).flatMap((c) => Object.values(c.pins)).filter((n) => traced.has(n)));
    setTimeout(() => view.setHighlight(hot, nets), 80);   // re-apply after the last animation frame
    view.flyTo([s.id]); select(s.id);
    setTimeout(() => { const r = document.getElementById("vfReveal"); if (r) r.hidden = false; }, 1500 / SPEED);
  }
}

function select(id: string | null) {
  selected = id; view.select(id);
  const box = byId("vfInsp");
  const c = id ? design.cells.find((x) => x.id === id) : null;
  if (!c || !R) { box.hidden = true; return; }
  const s = R.suspects.find((x) => x.id === id);
  const type = step === 2 && id === BUG.cell && !running && shown ? `${BUG.to} <span class="muted">(rev A: ${esc(bugFrom)})</span>` : esc(c.type);
  box.hidden = false;
  box.innerHTML = `<span class="ey">Cell <span class="mono">${esc(c.id)}</span> <span class="muted">· level ${level.get(c.id) ?? "?"}</span></span>
    <div class="mono">${type}</div>
    ${s ? `<div>${step === 2 && R.wrong ? `Fault probability <b>${pct(s.p)}%</b>, explains ${s.explains} of ${R.wrong} sampled failures${s.confirmed ? " (confirmed by re-simulation)" : ""}.` : "Logic cell: a candidate if anything goes wrong."}</div>` : `<div class="muted">Wiring or clock cell, not scored.</div>`}`;
  if (!byId("vfSusSec").hidden) renderSuspects(1);
}

async function boot() {
  ({ design, lib } = await loadDesign(CHIP));
  proof = await loadChip(CHIP).then(({ data }) => (data.verdict === "proven" ? { ...data.proof, proven: true } : null), () => null);
  bugCell = design.cells.find((c) => c.id === BUG.cell)!; bugFrom = bugCell.type;
  view = new Stage3D(byId("vfView"), design, lib, { onCell: (id) => { if (!running) select(id); }, onNet: () => {}, onPort: () => {}, onPhys: () => {} });
  view.start();
  setTimeout(() => view.flyTo(design.cells.map((c) => c.id)), 400);
  setStep(1);
  byId("vfGo").onclick = () => { setStep(1); start(); };
  byId("vfRevB").onclick = () => { setStep(2); start(); };
  byId("vfReplay").onclick = () => start();
  byId("vfSus").onclick = (e) => { const li = (e.target as HTMLElement).closest<HTMLElement>("li[data-id]"); if (li && !running) { select(li.dataset.id!); view.flyTo([li.dataset.id!]); } };
  addEventListener("keydown", (e) => {
    if (!root.isConnected || running || e.key !== " " || /^(INPUT|TEXTAREA|BUTTON|A)$/.test((document.activeElement as HTMLElement)?.tagName)) return;
    e.preventDefault(); (step === 1 && shown ? byId("vfRevB") : byId("vfGo")).click();
  });
  addEventListener("resize", () => drawStrip(shown / (R?.patterns || 1)));
}

/** Header button icon: a shield with a tick, in the style of ui/icons.ts. */
export const verifyIcon = `<svg class="ic" viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 1.8 13 3.6v4.1c0 3-2.1 5.4-5 6.5-2.9-1.1-5-3.5-5-6.5V3.6L8 1.8Z"/><path d="m5.6 8 1.7 1.7 3.2-3.4"/></svg>`;

let booted: Promise<void> | null = null;
/** The page for the app router: build the 3D view on first mount (it needs the element on screen). */
export const verifyPage = {
  el: root,
  mount(arg: string | null = null) {
    auto = arg === "auto";
    if (!booted) booted = boot().catch((e) => { root.insertAdjacentHTML("beforeend", `<div class="vf-err">Couldn't load the CLA layout (${esc(String(e?.message ?? e))}). Run <code>npm run stage-data</code> in decap/web.</div>`); });
    else view?.start();
    if (auto) void booted.then(() => setTimeout(() => { if (!running) { setStep(1); start(); } }, 1200));
    if (arg === "revb") void booted.then(() => setTimeout(() => { if (!running) { setStep(2); start(); } }, 1200));
  },
  unmount() { view?.stop(); },
};
