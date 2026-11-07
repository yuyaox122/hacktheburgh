// Command palette (⌘K / Ctrl+K), ported from the prototype.

import { $, esc } from "./dom";
import type { PaletteCommand } from "./state";

export class Palette {
  readonly el = document.createElement("div");
  private items: (PaletteCommand & { s: number })[] = [];
  private at = 0;
  constructor(private source: () => PaletteCommand[]) {
    this.el.className = "palette"; this.el.hidden = true;
    this.el.setAttribute("role", "dialog"); this.el.setAttribute("aria-modal", "true"); this.el.setAttribute("aria-label", "Command palette");
    this.el.innerHTML = `<div class="box"><input id="palIn" autocomplete="off" placeholder="Type a command, a cell or a line…" aria-label="Command">
      <ul id="palList" role="listbox"></ul>
      <div class="foot"><span><kbd>↑</kbd><kbd>↓</kbd> choose</span><span><kbd>Enter</kbd> run</span><span><kbd>Esc</kbd> close</span><span><kbd>1</kbd>–<kbd>4</kbd> pages</span><span><kbd>G</kbd> guide</span><span><kbd>D</kbd> demo</span><span><kbd>Space</kbd> run/pause</span><span><kbd>.</kbd> step</span></div></div>`;
    document.body.append(this.el);
    const inp = $<HTMLInputElement>("#palIn", this.el);
    inp.addEventListener("input", () => { this.at = 0; this.render(); });
    inp.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); this.at = Math.min(this.items.length - 1, this.at + 1); this.render(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); this.at = Math.max(0, this.at - 1); this.render(); }
      else if (e.key === "Enter") { e.preventDefault(); this.run(this.at); }
      else if (e.key === "Escape") this.close();
    });
    $("#palList", this.el).addEventListener("click", (e) => { const li = (e.target as HTMLElement).closest<HTMLElement>("[data-i]"); if (li) this.run(+li.dataset.i!); });
    this.el.addEventListener("click", (e) => { if (e.target === this.el) this.close(); });
  }
  get isOpen() { return !this.el.hidden; }
  open() { this.el.hidden = false; const i = $<HTMLInputElement>("#palIn", this.el); i.value = ""; this.at = 0; this.render(); i.focus(); }
  close() { this.el.hidden = true; }
  private score(label: string, q: string) {
    if (!q) return 1;
    const l = label.toLowerCase(); q = q.toLowerCase();
    if (l.includes(q)) return 100 - l.indexOf(q);
    let i = 0; for (const ch of l) if (ch === q[i]) i++;
    return i === q.length ? 10 : 0;
  }
  private render() {
    const q = $<HTMLInputElement>("#palIn", this.el).value.trim();
    const all = this.source();
    this.items = all.map((c) => ({ ...c, s: this.score(c.label, q) })).filter((c) => c.s > 0 && (q || !/^(Go to|Decompiled line|Open chip)/.test(c.label))).sort((a, b) => b.s - a.s).slice(0, 40);
    this.at = Math.min(this.at, Math.max(0, this.items.length - 1));
    $("#palList", this.el).innerHTML = this.items.map((c, i) => `<li role="option" aria-selected="${i === this.at}" class="${i === this.at ? "on" : ""}" data-i="${i}">${esc(c.label)}${c.key ? `<small>${esc(c.key)}</small>` : ""}</li>`).join("") || `<li>No match</li>`;
    $("#palList .on", this.el)?.scrollIntoView({ block: "nearest" });
  }
  private run(i: number) { const c = this.items[i]; this.close(); c?.run(); }
}
