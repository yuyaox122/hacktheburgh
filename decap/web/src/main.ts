// Decap web app: hash router (#/stage, #/wall, #/chip/<id>, #/compare), shared chrome, keyboard shortcuts.

import "./styles.css";
import { fixtureUsed, health, loadBench, loadDesign, loadStageIndex, onFixtureChange } from "./data/load";
import { StagePage } from "./ui/stagePage";
import { WallPage } from "./ui/wallPage";
import { ChipPage } from "./ui/chipPage";
import { ComparePage } from "./ui/comparePage";
import { Guide } from "./ui/guide";
import { Palette } from "./ui/palette";
import { Demo } from "./ui/demo";
import { $, $$, esc, isTyping } from "./ui/dom";
import { app, type PaletteCommand } from "./ui/state";

const root = $("#app");
root.innerHTML = `
  <header class="top">
    <a class="brand" href="#/stage"><span class="die" aria-hidden="true"></span><b>Decap</b></a>
    <nav class="main" aria-label="Pages">
      <a href="#/stage" data-r="stage" title="Stage (1)">Stage</a>
      <a href="#/wall" data-r="wall" title="Proof wall (2)">Proof wall</a>
      <a href="#/chip" data-r="chip" title="Chip (3)">Chip</a>
      <a href="#/compare" data-r="compare" title="Compare (4)">Compare</a>
    </nav>
    <span class="spacer"></span>
    <span class="status" id="apiStatus" title="Backend status"><i></i> API</span>
    <button class="btn ghost sm" type="button" id="btnGuide" title="Guide (G)">Guide</button>
    <button class="btn ghost sm" type="button" id="btnDemo" title="Demo (D)">Demo</button>
    <button class="btn ghost sm" type="button" id="btnPal" title="Commands (Ctrl/⌘ K)">⌘K</button>
  </header>
  <div class="fixture-banner" id="fixBanner" hidden></div>
  <main class="page" id="page"></main>`;
const pageEl = $("#page");

const guide = new Guide();
let stage: StagePage | null = null;
let stageErr: string | null = null;
let wall: WallPage | null = null;
let chip: ChipPage | null = null;
let comparePg: ComparePage | null = null;
let current: { el: HTMLElement; unmount?: () => void } | null = null;

/** Load the Stage for a chip (null = the current one, else the default); switching chips frees the old 3D view. */
function loadStage(want: string | null): Promise<void> {
  return loadStageIndex().then(async (idx) => {
    const target = want ?? stage?.chipName ?? idx?.default ?? null;
    if (stage && (!target || stage.chipName === target)) return;
    const { design, lib } = await loadDesign(target);
    if (stage && stage.chipName === design.name) return;
    stage?.destroy();
    stage = new StagePage(design, lib, idx?.chips ?? []);
    stageErr = null;
  }).catch((e) => { stageErr = (e as Error).message; });
}
let stageReady = loadStage(location.hash.match(/^#\/stage\/(.+)/)?.[1] ? decodeURIComponent(location.hash.match(/^#\/stage\/(.+)/)![1]) : null);

function show(el: HTMLElement, scroll: boolean, unmount?: () => void) {
  current?.unmount?.();
  pageEl.replaceChildren(el);
  pageEl.classList.toggle("scroll", scroll);
  current = { el, unmount };
}

async function route() {
  const h = location.hash || "#/stage";
  const [, r = "stage", arg] = h.match(/^#\/([^/]+)(?:\/(.+))?/) ?? [];
  app.route = r;
  $$("nav.main a").forEach((a) => (a.dataset.r === r ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  if (r === "stage") {
    stageReady = loadStage(arg ? decodeURIComponent(arg) : null);
    await stageReady;
    if (!stage) {
      const d = document.createElement("div");
      d.className = "empty";
      d.innerHTML = `<h3>No Stage data yet</h3><p>${esc(stageErr ?? "")}</p><p>Run <code>npm run stage-data</code> in <code>decap/web</code> to build <code>public/data/design.json</code> (a Tiny Tapeout chip, from its GDS) and <code>lib.json</code> (SKY130 Liberty).</p>`;
      show(d, true); return;
    }
    const s = stage;
    show(s.el, false, () => s.unmount());
    s.mount();
    app.guideContext = () => s.context();
    app.guideHighlight = (x) => s.highlightFromGuide(x.cells ?? []);
    app.pageCommands = () => s.commands();
  } else if (r === "wall") {
    wall ??= new WallPage();
    show(wall.el, true);
    await wall.load();
    const w = wall;
    app.guideContext = () => w.context(); app.guideHighlight = undefined; app.pageCommands = () => w.commands();
  } else if (r === "chip") {
    let id = arg ? decodeURIComponent(arg) : app.judge.chip ?? chip?.chip ?? null;
    if (!id) { const b = await loadBench().catch(() => null); id = b?.data.chips.find((c) => c.split === "test")?.chip ?? b?.data.chips[0]?.chip ?? null; }
    if (!id) { const d = document.createElement("div"); d.className = "empty"; d.textContent = "No chips yet."; show(d, true); return; }
    if (!arg) { history.replaceState(null, "", `#/chip/${encodeURIComponent(id)}`); }
    if (!chip || chip.chip !== id) { chip?.destroy(); chip = new ChipPage(id); show(chip.el, false); await chip.load(); }
    else show(chip.el, false);
    const c = chip;
    app.guideContext = () => c.context(); app.guideHighlight = (x) => c.highlightFromGuide(x); app.pageCommands = () => c.commands();
  } else if (r === "compare") {
    if (!comparePg) { comparePg = new ComparePage(); await comparePg.load(); }
    show(comparePg.el, true);
    const c = comparePg;
    app.guideContext = () => c.context(); app.guideHighlight = undefined; app.pageCommands = () => [];
  } else { location.hash = "#/stage"; }
}
addEventListener("hashchange", route);

app.askGuide = (q) => guide.open(q);

const demo = new Demo({
  go: (r) => { if (location.hash !== r) location.hash = r; },
  stageOverview: () => { stageReady.then(() => { stage?.selectCell(null); stage?.view?.flyTo(null); stage?.view?.startIntro(); }); },
  stageShow: () => {
    stageReady.then(() => {
      if (!stage) return;
      const g = stage.findCell(/^xor2/);
      if (g) stage.selectCell(g);
      stage.demoShow();
    });
  },
  chipVariant: (planted) => {
    const tryIt = (k = 0) => {
      if (chip && app.route === "chip") void (planted ? chip.showFind() : chip.showClean());
      else if (k < 50) setTimeout(() => tryIt(k + 1), 100);
    };
    tryIt();
  },
  chipFix: () => { if (chip) chip.fix(); },
});
root.append(demo.el);

const palette = new Palette((): PaletteCommand[] => [
  { label: "Go to Stage (a real chip in 3D)", key: "1", run: () => (location.hash = "#/stage") },
  { label: "Go to Proof wall", key: "2", run: () => (location.hash = "#/wall") },
  { label: "Go to Chip", key: "3", run: () => (location.hash = "#/chip") },
  { label: "Go to Compare", key: "4", run: () => (location.hash = "#/compare") },
  { label: "Open the AI guide", key: "G", run: () => guide.open() },
  { label: "Demo flow (SPEC §9)", key: "D", run: () => demo.toggle() },
  { label: "Bigger text (projector)", key: "", run: () => document.body.classList.toggle("projector") },
  ...(app.pageCommands?.() ?? []),
]);

$("#btnGuide").addEventListener("click", () => guide.toggle());
$("#btnDemo").addEventListener("click", () => demo.toggle());
$("#btnPal").addEventListener("click", () => palette.open());

addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); palette.isOpen ? palette.close() : palette.open(); return; }
  if (palette.isOpen) return;
  if (e.key === "Escape") { if (guide.isOpen) guide.close(); else if (app.route === "stage") stage?.selectCell(null); return; }
  if (isTyping() || e.metaKey || e.ctrlKey || e.altKey) return;
  if (demo.isOpen && (e.key === "PageDown" || e.key === "ArrowRight")) { e.preventDefault(); demo.next(); return; }
  if (demo.isOpen && (e.key === "PageUp" || e.key === "ArrowLeft")) { e.preventDefault(); demo.prev(); return; }
  const k = e.key;
  if (k === "1") location.hash = "#/stage";
  else if (k === "2") location.hash = "#/wall";
  else if (k === "3") location.hash = "#/chip";
  else if (k === "4") location.hash = "#/compare";
  else if (k === "g" || k === "G") { e.preventDefault(); guide.toggle(); }
  else if (k === "d" || k === "D") demo.toggle();
  else if (app.route === "stage" && stage) {
    if (k === " " && !/^(BUTTON|A)$/.test((document.activeElement as HTMLElement)?.tagName)) { e.preventDefault(); stage.isRunning ? stage.pause() : stage.run(); }
    else if (k === ".") { stage.pause(); stage.step(); }
    else if (k === "r" || k === "R") stage.reset();
    else if (k === "x" || k === "X") stage.toggleExplode();
    else if (k === "f" || k === "F") stage.view?.flyTo(null);
  }
});

function renderFixtureBanner() {
  const b = $("#fixBanner");
  b.hidden = fixtureUsed.size === 0;
  b.textContent = `FIXTURE data on screen (made up, for development): ${[...fixtureUsed].join(", ")}. Real results appear once decap/out is synced (npm run sync-data) or the API is running.`;
}
onFixtureChange(renderFixtureBanner);

health().then((h) => {
  app.api = true; app.llm = !!h.llm;
  $("#apiStatus").innerHTML = `<i class="ok"></i> API`;
  $("#apiStatus").title = `API online${h.yosys === false ? " (no Yosys: live proofs off)" : ""} · guide ${h.llm ? "on" : "off"}`;
}).catch(() => {
  app.api = false;
  $("#apiStatus").innerHTML = `<i class="bad"></i> API offline`;
  $("#apiStatus").title = "Static data only. Start it with: npm run dev:all (or npm run api)";
});

// Test hook (used by the headless browser check; harmless in production).
(window as unknown as { __decap: unknown }).__decap = { get stage() { return stage; }, get chip() { return chip; }, app };

route();
