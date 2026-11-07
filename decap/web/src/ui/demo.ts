// Demo flow (SPEC §9, 90 s): a strip along the bottom that drives the pages in order.
// Before the timer: pick the judge's chip and number, and start planting in the background.

import { loadBench } from "../data/load";
import { $, esc } from "./dom";
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
  { key: "hook", label: "0 Hook", t: "0–15 s", note: "“You'd never merge a pull request you can't read. Every chip is one. Software has decompilers; silicon has no View Source. Chips are built from parts you didn't make, AI is starting to write them, and you can't patch a chip.”" },
  { key: "stage", label: "1 Stage", t: "15–30 s", note: "“This is real silicon (warm-up chip, credit Jane Street). Every gate has a meaning, but nobody can read hundreds of them.” Click a gate; it runs; wires glow." },
  { key: "wall", label: "2 Proof wall", t: "30–40 s", note: "“Decap turned every one of these real Tiny Tapeout chips back into code. Green means a formal checker proved the code is exactly the same circuit.”" },
  { key: "read", label: "3 Read", t: "40–55 s", note: "The judge's chip: names stripped, blocks recovered, Verilog appears, “Equivalence: proven”. “And here's what the author actually wrote.”" },
  { key: "find", label: "4 Find", t: "55–80 s", note: "“You picked a number, and we hid a trigger for it in this chip.” Point at the red line: “your number, as a line of code nobody wrote. And here's everything it can reach.” Then the catch rate and false alarms." },
  { key: "close", label: "5 Close", t: "80–90 s", note: "Optional: Remove and prove. “You can't review what you can't read. Now you can read silicon.”" },
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
      <b>Demo</b>
      <div class="prep" title="Before the timer: let a judge pick a test chip and a number">
        <select class="sel" id="dmChip" aria-label="Judge's chip">${test.map((c) => `<option value="${esc(c.chip)}" ${c.chip === app.judge.chip ? "selected" : ""}>${esc(c.chip)} (${c.verdict})</option>`).join("")}</select>
        <input class="num" id="dmN" type="number" min="0" max="255" placeholder="0–255" value="${app.judge.n ?? ""}" aria-label="Judge's number">
        <button class="btn sm" type="button" id="dmPlant">Plant now</button><span class="muted" id="dmStatus">${esc(this.status)}</span>
      </div>
      <div class="steps">${STEPS.map((s, i) => `<button type="button" data-step="${i}" ${i === this.at ? 'aria-current="step"' : ""}>${esc(s.label)}</button>`).join("")}</div>
      <div class="note" id="dmNote">${this.at >= 0 ? `<span class="muted">${STEPS[this.at].t}</span> ${esc(STEPS[this.at].note)}` : `<span class="muted">Pick the judge's chip and number, press Plant now, then <kbd>→</kbd> / <kbd>PgDn</kbd> to start.</span>`}</div>
      <button class="btn sm" type="button" id="dmClose" aria-label="Close the demo strip">✕</button>`;
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
    if (!chip || !(n >= 0 && n <= 255) || !Number.isInteger(n) || $<HTMLInputElement>("#dmN", this.el).value === "") { this.setStatus("Pick a chip and a whole number 0–255."); return; }
    app.judge = { chip, n };
    const key = `${chip}:${n}`;
    const t0 = performance.now();
    if (!app.planted.has(key)) app.planted.set(key, startPlant(chip, n));
    this.setStatus(`planting ${n} into ${chip}…`);
    app.planted.get(key)!.then(
      (r) => this.setStatus(`ready: ${r.flags.length} flag(s) in ${((performance.now() - t0) / 1000).toFixed(0)} s`),
      (e) => { app.planted.delete(key); this.setStatus(`plant failed: ${(e as Error).message}. Use a pre-planted copy and say so.`); },
    );
  }
  private setStatus(s: string) { this.status = s; const el = $("#dmStatus", this.el); if (el) el.textContent = s; }

  go(i: number) {
    if (i < 0 || i >= STEPS.length) return;
    this.at = i;
    this.el.querySelectorAll<HTMLElement>("[data-step]").forEach((b) => (+b.dataset.step! === i ? b.setAttribute("aria-current", "step") : b.removeAttribute("aria-current")));
    $("#dmNote", this.el).innerHTML = `<span class="muted">${STEPS[i].t}</span> ${esc(STEPS[i].note)}`;
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
