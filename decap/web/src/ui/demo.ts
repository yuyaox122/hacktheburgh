// Presenter mode (SPEC §9, 90 s): a floating bar that drives the pages in order.
// Before the timer: pick the judge's chip and number, and start planting in the background.

import { loadBench } from "../data/load";
import { $, esc } from "./dom";
import { icon } from "./icons";
import { app } from "./state";
import { startPlant } from "./chipPage";

export interface DemoHooks {
  go(route: string): void;
  stageShow(): void;     // Stage: select a gate, run the clock
  stageOverview(): void; // Stage: whole die, intro
  chipVariant(planted: boolean): void;
  chipFix(): void;
}

const STEPS = [
  { key: "hook", label: "Hook", t: "0 to 15 s", note: "Say: You'd never merge a pull request you can't read. Every chip is one. Software has decompilers; silicon has no View Source. Chips are built from parts you didn't make, AI is starting to write them, and you can't patch a chip." },
  { key: "stage", label: "Explore", t: "15 to 30 s", note: "Say: This is real silicon, a Tiny Tapeout chip, every cell and wire from its published layout. Every gate has a meaning, but nobody can read hundreds of them. Then click a gate, set 9 and 8, and watch the wires glow." },
  { key: "wall", label: "Results", t: "30 to 40 s", note: "Say: bASIC turned every one of these real Tiny Tapeout chips back into code. Green means a formal checker proved the code is exactly the same circuit." },
  { key: "read", label: "Read", t: "40 to 55 s", note: "The judge's chip: names stripped, blocks recovered, Verilog appears, Proven equivalent. Say: And here's what the author actually wrote." },
  { key: "find", label: "Find", t: "55 to 80 s", note: "Say: You picked a number, and we hid a trigger for it in this chip. Point at the red line: your number, as a line of code nobody wrote, and everything it can reach. Then the catch rate and false alarms." },
  { key: "close", label: "Close", t: "80 to 90 s", note: "Optional: Remove and prove. Say: You can't review what you can't read. Now you can read silicon." },
];

export class Demo {
  readonly el = document.createElement("div");
  at = -1;
  private status = "";
  constructor(private hooks: DemoHooks) {
    this.el.className = "demo"; this.el.hidden = true;
    this.el.setAttribute("aria-label", "Demo flow");
  }
  get isOpen() { return !this.el.hidden; }
  async open() {
    this.el.hidden = false;
    const bench = await loadBench().catch(() => null);
    const test = bench?.data.chips.filter((c) => c.split === "test") ?? [];
    if (!app.judge.chip) app.judge.chip = test.find((c) => c.verdict === "proven")?.chip ?? test[0]?.chip ?? null;
    this.el.innerHTML = `
      <b>Presenter</b>
      <div class="prep" title="Before the timer: let a judge pick a test chip and a number">
        <select class="sel" id="dmChip" aria-label="Judge's chip">${test.map((c) => `<option value="${esc(c.chip)}" ${c.chip === app.judge.chip ? "selected" : ""}>${esc(c.title || c.chip)}${c.verdict === "proven" ? "" : ` (${c.verdict})`}</option>`).join("")}</select>
        <input class="num" id="dmN" type="number" min="0" max="255" placeholder="0 to 255" value="${app.judge.n ?? ""}" aria-label="Judge's number">
        <button class="btn sm" type="button" id="dmPlant">Prepare</button>
      </div>
      <div class="steps seg" role="group" aria-label="Steps">${STEPS.map((s, i) => `<button type="button" data-step="${i}" ${i === this.at ? 'aria-current="step"' : ""}>${i + 1} ${esc(s.label)}</button>`).join("")}</div>
      <button class="btn plain icon" type="button" id="dmClose" aria-label="Close presenter" title="Close (D)">${icon.close}</button>
      <div class="note" id="dmNote">${this.at >= 0 ? `<span class="muted">${STEPS[this.at].t}</span>${esc(STEPS[this.at].note)}` : `<span class="muted" id="dmStatus">${esc(this.status || "Pick the judge's chip and number, press Prepare, then use the arrow keys.")}</span>`}</div>`;
    $("#dmChip", this.el).addEventListener("change", (e) => { app.judge.chip = (e.target as HTMLSelectElement).value; });
    $("#dmPlant", this.el).addEventListener("click", () => this.prep());
    $<HTMLInputElement>("#dmN", this.el).addEventListener("keydown", (e) => { if (e.key === "Enter") this.prep(); });
    $("#dmClose", this.el).addEventListener("click", () => this.close());
    this.el.querySelectorAll<HTMLElement>("[data-step]").forEach((b) => b.addEventListener("click", () => this.go(+b.dataset.step!)));
  }
  close() { this.el.hidden = true; }
  toggle() { this.isOpen ? this.close() : this.open(); }

  private prep() {
    const n = +$<HTMLInputElement>("#dmN", this.el).value, chip = $<HTMLSelectElement>("#dmChip", this.el).value;
    if (!chip || !(n >= 0 && n <= 255) || !Number.isInteger(n) || $<HTMLInputElement>("#dmN", this.el).value === "") { this.setStatus("Pick a chip and a whole number from 0 to 255."); return; }
    app.judge = { chip, n };
    const key = `${chip}:${n}`;
    const t0 = performance.now();
    if (!app.planted.has(key)) app.planted.set(key, startPlant(chip, n));
    this.setStatus(`Hiding ${n} in ${chip}`);
    app.planted.get(key)!.then(
      (r) => this.setStatus(`Ready: ${r.flags.length} flag${r.flags.length === 1 ? "" : "s"} in ${((performance.now() - t0) / 1000).toFixed(0)} s`),
      (e) => { app.planted.delete(key); this.setStatus(`Could not plant: ${(e as Error).message}. Use a saved copy and say so.`); },
    );
  }
  private setStatus(s: string) { this.status = s; const el = $("#dmStatus", this.el); if (el) el.textContent = s; }

  go(i: number) {
    if (i < 0 || i >= STEPS.length) return;
    this.at = i;
    this.el.querySelectorAll<HTMLElement>("[data-step]").forEach((b) => (+b.dataset.step! === i ? b.setAttribute("aria-current", "step") : b.removeAttribute("aria-current")));
    $("#dmNote", this.el).innerHTML = `<span class="muted">${STEPS[i].t}</span>${esc(STEPS[i].note)}${this.status ? ` <span class="muted" id="dmStatus">${esc(this.status)}</span>` : ""}`;
    const chip = app.judge.chip;
    const k = STEPS[i].key;
    if (k === "hook") { this.hooks.go("#/stage"); this.hooks.stageOverview(); }
    else if (k === "stage") { this.hooks.go("#/stage"); this.hooks.stageShow(); }
    else if (k === "wall") this.hooks.go("#/wall");
    else if (k === "read" && chip) { this.hooks.go(`#/chip/${encodeURIComponent(chip)}`); this.hooks.chipVariant(false); }
    else if (k === "find" && chip) {
      this.hooks.go(`#/chip/${encodeURIComponent(chip)}`);
      this.hooks.chipVariant(true);
    } else if (k === "close" && chip) this.hooks.chipFix();
  }
  next() { this.go(Math.min(STEPS.length - 1, this.at + 1)); }
  prev() { this.go(Math.max(0, this.at - 1)); }
}
