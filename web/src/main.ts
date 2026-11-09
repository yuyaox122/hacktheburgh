// bASIC web app: hash router (#/stage, #/wall, #/chip/<id>, #/compare, #/minecraft, #/verify), shared chrome, keyboard shortcuts.

import "./styles/index.css";
import { fixtureUsed, health, loadBench, loadDesign, loadStageIndex, onFixtureChange } from "./api/core";
import { StagePage } from "./pages/stage/stagePage";
import { WallPage } from "./pages/wallPage";
import { ChipPage } from "./pages/chipPage";
import { ComparePage } from "./pages/comparePage";
import { MinecraftPage } from "./pages/minecraftPage";
import { VerifyPage } from "./pages/verifyPage";
import { Guide } from "./ui/guide";
import { Palette } from "./ui/palette";
import { Demo } from "./ui/demo";
import { $, $$, css, esc, isTyping, setWrap, wrapOn } from "./ui/dom";
import { icon } from "./ui/icons";
import { app, type PaletteCommand } from "./ui/state";
import { AgentPanel } from "./agent/panel";
import { act, decorateCode } from "./agent/codeAgent";
import { explainCell } from "./agent/stageAgent";
import { store } from "./agent/store";

const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
const root = $("#app");
root.innerHTML = `
  <header class="top">
    <a class="brand" href="#/stage" aria-label="bASIC home">${icon.logo}<b><span class="b">b</span>ASIC</b></a>
    <nav class="main" aria-label="Pages">
      <a href="#/stage" data-r="stage" title="A real chip in 3D, live (1)">Explore</a>
      <a href="#/wall" data-r="wall" title="Every benchmark chip and its proof (2)">Results</a>
      <a href="#/chip" data-r="chip" title="One chip read back as Verilog (3)">Decompile</a>
      <a href="#/compare" data-r="compare" title="Find chips like your design (4)">Compare</a>
      <a href="#/minecraft" data-r="minecraft" title="Build Verilog in real Minecraft redstone (5)">Minecraft</a>
    </nav>
    <div class="actions">
      <span class="status" id="apiStatus"><i></i><span>Connecting</span></span>
      <button class="search" type="button" id="btnPal" aria-label="Search and commands">${icon.search}<span class="lbl">Search</span><span class="k">${isMac ? "⌘K" : "Ctrl K"}</span></button>
      <a class="btn plain" href="#/verify" id="btnVerify" title="Verify a chip: every input pattern at once, then trace a fault to its cell (V)">${icon.shield}<span>Verify</span></a>
      <button class="btn plain" type="button" id="btnAgent" title="Witness agent: explains a region in one line; Yosys decides (A)">${icon.cube}<span>Agent</span></button>
      <button class="btn plain" type="button" id="btnGuide" title="Ask about what's on screen (G)">${icon.sparkle}<span>Ask</span></button>
      <div class="menuwrap">
        <button class="btn plain icon" type="button" id="btnMore" aria-haspopup="menu" aria-expanded="false" aria-label="More">${icon.more}</button>
        <div class="menu" id="moreMenu" role="menu" hidden>
          <button type="button" role="menuitem" id="btnDemo">${icon.present}Presenter mode<span class="k">D</span></button>
          <button type="button" role="menuitem" id="btnBig">${icon.textSize}Larger text</button>
        </div>
      </div>
    </div>
  </header>
  <div class="fixture-banner" id="fixBanner" hidden></div>
  <main class="page" id="page"></main>`;
const pageEl = $("#page");

const guide = new Guide();
const agentPanel = new AgentPanel();
app.onCodeRendered = (p) => decorateCode(p);
/** Repaint every agent result on whatever page shows this chip: code pane, netlist graph, 3D cells. */
function paintAgent(chipId: string) {
  if (app.route === "chip" && chip?.chip === chipId) { decorateCode(chip); chip.setAgentMarks(store.marks(chipId)); }
  if (app.route === "stage" && stage?.chipName === chipId) {
    const legend = store.runs(chipId).filter((r) => r.status === "done" && r.cells.length).slice(0, 5).map((r) => ({
      label: `${r.label}: ${r.explained ? "proven" : r.final ?? ""}`, colour: css(r.explained ? "--add" : r.final === "refuted" ? "--del" : "--amber"),
      cells: r.cells, claim: r.attempts[r.attempts.length - 1]?.claim }));
    stage.setAgentMarks(store.marks(chipId), legend);
  }
}
store.on(paintAgent);
let stage: StagePage | null = null;
let stageErr: string | null = null;
let wall: WallPage | null = null;
let chip: ChipPage | null = null;
let comparePg: ComparePage | null = null;
let mcPg: MinecraftPage | null = null;
let verifyPg: VerifyPage | null = null;
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
      d.innerHTML = `<h3>No chip layouts yet</h3><p>${esc(stageErr ?? "")}</p><p>Run <code>npm run stage-data</code> in <code>web/</code> to build the 3D layouts from the Tiny Tapeout GDS files.</p>`;
      show(d, true); return;
    }
    const s = stage;
    show(s.el, false, () => s.unmount());
    s.mount();
    app.guideContext = () => s.context();
    app.guideHighlight = (x) => s.highlightFromGuide(x.cells ?? []);
    agentPanel.setChip(s.chipName); paintAgent(s.chipName);
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
    app.guideContext = () => c.context(); app.guideHighlight = (x) => c.highlightFromGuide(x);
    app.pageCommands = () => [...c.commands(), ...agentCommands(c)];
    agentPanel.setChip(c.chip, c.result?.decompiled.verilog); paintAgent(c.chip);
  } else if (r === "compare") {
    if (!comparePg) { comparePg = new ComparePage(); await comparePg.load(); }
    show(comparePg.el, true);
    const c = comparePg;
    app.guideContext = () => c.context(); app.guideHighlight = undefined; app.pageCommands = () => [];
  } else if (r === "minecraft") {
    mcPg ??= new MinecraftPage();
    const m = mcPg;
    show(m.el, false, () => m.unmount());
    await m.mount(arg ? decodeURIComponent(arg) : null);
    app.guideContext = () => m.context(); app.guideHighlight = undefined; app.pageCommands = () => [];
  } else if (r === "verify") {
    verifyPg ??= new VerifyPage();
    const v = verifyPg;
    show(v.el, false, () => v.unmount());
    v.mount(arg ? decodeURIComponent(arg) : null);
    app.guideContext = undefined; app.guideHighlight = undefined; app.pageCommands = () => [];
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
  { label: "Explore a chip in 3D", key: "1", run: () => (location.hash = "#/stage") },
  { label: "Results", key: "2", run: () => (location.hash = "#/wall") },
  { label: "Decompile", key: "3", run: () => (location.hash = "#/chip") },
  { label: "Compare", key: "4", run: () => (location.hash = "#/compare") },
  { label: "Minecraft: build Verilog in redstone", key: "5", run: () => (location.hash = "#/minecraft") },
  { label: "Verify: check every input, trace a fault to its cell", key: "V", run: () => (location.hash = "#/verify") },
  { label: "Ask the guide", key: "G", run: () => { agentPanel.close(); guide.open(); } },
  { label: "Witness agent panel", key: "A", run: () => { guide.close(); agentPanel.open(); } },
  { label: "Presenter mode", key: "D", run: () => demo.toggle() },
  { label: "Larger text", key: "", run: () => document.body.classList.toggle("projector") },
  { label: "Wrap long code lines", key: "W", run: () => setWrap(!wrapOn()) },
  ...(app.pageCommands?.() ?? []),
]);

const menu = $("#moreMenu"), moreBtn = $("#btnMore");
const setMenu = (open: boolean) => { menu.hidden = !open; moreBtn.setAttribute("aria-expanded", String(open)); };
moreBtn.addEventListener("click", (e) => { e.stopPropagation(); setMenu(menu.hidden); });
addEventListener("click", (e) => { if (!menu.hidden && !menu.contains(e.target as Node)) setMenu(false); });
$("#btnGuide").addEventListener("click", () => { agentPanel.close(); guide.toggle(); });
$("#btnAgent").addEventListener("click", () => { guide.close(); agentPanel.toggle(); });
document.addEventListener("agent:open", () => guide.close());
$("#btnDemo").addEventListener("click", () => { setMenu(false); demo.toggle(); });
/** Palette: run the agent on each block of the chip page, leftover logic first. */
function agentCommands(c: ChipPage): PaletteCommand[] {
  const r = c.result;
  if (!r) return [];
  return [...r.blocks].sort((a, b) => Number(a.recognised) - Number(b.recognised) || b.cells.length - a.cells.length)
    .flatMap((b) => [{ label: `Agent: explain block ${b.id} (${b.kind}, ${b.cells.length} cells, line ${b.lines[0]})`, key: "",
      run: () => act(c, b.lines[0], "explain") },
    { label: `Agent: ideas for block ${b.id} (line ${b.lines[0]})`, key: "", run: () => act(c, b.lines[0], "ideas") }]);
}
// 3D Stage inspector: "Explain its block with the agent"
document.addEventListener("click", (e) => {
  const b = (e.target as HTMLElement).closest<HTMLElement>("[data-agent-cell]");
  if (!b || !stage) return;
  explainCell(stage.chipName, b.dataset.agentCell!);
});
$("#btnPal").addEventListener("click", () => palette.open());
$("#btnBig").addEventListener("click", () => { setMenu(false); document.body.classList.toggle("projector"); });

addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); palette.isOpen ? palette.close() : palette.open(); return; }
  if (palette.isOpen) return;
  if (e.key === "Escape") { if (!menu.hidden) setMenu(false); else if (agentPanel.isOpen) agentPanel.close(); else if (guide.isOpen) guide.close(); else if (app.route === "stage") stage?.selectCell(null); return; }
  if (isTyping() || e.metaKey || e.ctrlKey || e.altKey) return;
  if (demo.isOpen && (e.key === "PageDown" || e.key === "ArrowRight")) { e.preventDefault(); demo.next(); return; }
  if (demo.isOpen && (e.key === "PageUp" || e.key === "ArrowLeft")) { e.preventDefault(); demo.prev(); return; }
  const k = e.key;
  if (k === "1") location.hash = "#/stage";
  else if (k === "2") location.hash = "#/wall";
  else if (k === "3") location.hash = "#/chip";
  else if (k === "4") location.hash = "#/compare";
  else if (k === "5") location.hash = "#/minecraft";
  else if (k === "g" || k === "G") { e.preventDefault(); agentPanel.close(); guide.toggle(); }
  else if (k === "a" || k === "A") { e.preventDefault(); guide.close(); agentPanel.toggle(); }
  else if (k === "w" || k === "W") setWrap(!wrapOn());
  else if (k === "v" || k === "V") location.hash = "#/verify";
  else if (k === "d" || k === "D") demo.toggle();
  else if (app.route === "stage" && stage) {
    if (k === " " && !/^(BUTTON|A)$/.test((document.activeElement as HTMLElement)?.tagName)) { e.preventDefault(); stage.isRunning ? stage.pause() : stage.run(); }
    else if (k === ".") { stage.pause(); stage.step(); }
    else if (k === "r" || k === "R") stage.reset();
    else if (k === "x" || k === "X") stage.toggleExplode();
    else if (k === "f" || k === "F") stage.fit();
  } else if (app.route === "verify" && verifyPg && k === " " && !/^(BUTTON|A)$/.test((document.activeElement as HTMLElement)?.tagName)) {
    e.preventDefault(); verifyPg.space();
  }
});

function renderFixtureBanner() {
  const b = $("#fixBanner");
  b.hidden = fixtureUsed.size === 0;
  b.textContent = `Sample data on screen, for development only: ${[...fixtureUsed].join(", ")}. Run npm run sync-data or start the API for real results.`;
}
onFixtureChange(renderFixtureBanner);

health().then((h) => {
  const s = $("#apiStatus");
  s.innerHTML = `<i class="ok"></i><span>Live</span>`;
  s.title = `Connected to the bASIC API.${h.yosys === false ? " Yosys is missing, so proofs are off." : ""} The guide is ${h.llm ? "on" : "off"}.`;
}).catch(() => {
  const s = $("#apiStatus");
  s.innerHTML = `<i class="bad"></i><span>Offline</span>`;
  s.title = "Showing saved results. Live planting, Fix, Compare and the guide need the bASIC API.";
});

// Test hook (used by the headless browser check; harmless in production).
(window as unknown as { __decap: unknown }).__decap = { get stage() { return stage; }, get chip() { return chip; }, app, agent: { store, panel: agentPanel } };

setWrap(wrapOn());
// Wrap toggles live in page templates; one delegated listener serves them all.
addEventListener("click", (e) => { if ((e.target as HTMLElement).closest("[data-wrap]")) setWrap(!wrapOn()); });

route();
