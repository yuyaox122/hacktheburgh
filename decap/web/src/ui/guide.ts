// AI guide: a chat drawer. POST /api/chat with a compact context of what is on screen; apply returned highlights.

import { ApiError, chat } from "../data/load";
import { $, esc } from "./dom";
import { icon } from "./icons";
import { app } from "./state";

type Msg = { role: "user" | "assistant"; content: string };

const SUGGEST: Record<string, string[]> = {
  stage: ["What does this chip do?", "What is a flip-flop?", "Why are there clock buffers?", "Explain the selected cell"],
  wall: ["What does proven mean here?", "Why are some chips tested only?", "How was the test set chosen?"],
  chip: ["What does this chip do?", "Explain the selected line", "Is anything suspicious in here?", "Which blocks were not recognised?"],
  compare: ["Why do these chips match?", "How could my design use fewer cells?"],
};

export class Guide {
  readonly el = document.createElement("aside");
  private msgs: Msg[] = [];
  private ctrl: AbortController | null = null;
  constructor() {
    this.el.className = "drawer";
    this.el.setAttribute("aria-label", "AI guide");
    this.el.innerHTML = `
      <div class="pane-h">${icon.sparkle}Ask Decap<span class="grow"></span><button class="btn plain icon" type="button" id="gdClose" aria-label="Close" title="Close (Esc)">${icon.close}</button></div>
      <div class="msgs" id="gdMsgs" aria-live="polite"></div>
      <div class="chipsugg" id="gdSugg"></div>
      <form class="askform" id="gdForm"><input class="text" id="gdIn" autocomplete="off" placeholder="Ask about this chip" aria-label="Ask the guide"><button class="btn primary" type="submit">Send</button></form>`;
    document.body.append(this.el);
    $("#gdClose", this.el).addEventListener("click", () => this.close());
    $("#gdForm", this.el).addEventListener("submit", (e) => { e.preventDefault(); const i = $<HTMLInputElement>("#gdIn", this.el); const q = i.value.trim(); i.value = ""; if (q) this.ask(q); });
    $("#gdSugg", this.el).addEventListener("click", (e) => { const b = (e.target as HTMLElement).closest("button"); if (b) this.ask(b.textContent!); });
    this.note("Ask anything about what's on screen. Answers light up the cells and lines they mention.");
  }
  get isOpen() { return this.el.classList.contains("open"); }
  open(q = "") {
    this.el.classList.add("open");
    $("#gdSugg", this.el).innerHTML = (SUGGEST[app.route] ?? SUGGEST.stage).map((s) => `<button type="button">${esc(s)}</button>`).join("");
    if (q) this.ask(q); else $<HTMLInputElement>("#gdIn", this.el).focus();
  }
  close() {
    this.el.classList.remove("open"); this.ctrl?.abort();
    if (this.el.contains(document.activeElement)) (document.activeElement as HTMLElement).blur();
  }
  toggle() { this.isOpen ? this.close() : this.open(); }

  private bubble(cls: string, html: string) {
    const d = document.createElement("div"); d.className = `msg ${cls}`; d.innerHTML = html;
    $("#gdMsgs", this.el).append(d); d.scrollIntoView({ block: "end" });
    return d;
  }
  private note(t: string) { this.bubble("note", esc(t)); }

  async ask(q: string) {
    if (!this.isOpen) this.el.classList.add("open");
    this.msgs.push({ role: "user", content: q });
    this.bubble("me", esc(q));
    const wait = this.bubble("ai", `<span class="spin"></span>`);
    this.ctrl?.abort(); this.ctrl = new AbortController();
    try {
      const r = await chat(this.msgs.slice(-12), app.guideContext?.() ?? { page: app.route }, this.ctrl.signal);
      this.msgs.push({ role: "assistant", content: r.reply });
      const h = r.highlights ?? {};
      const hl = [...(h.cells?.length ? [`${h.cells.length} cell${h.cells.length === 1 ? "" : "s"}`] : []), ...(h.lines?.length ? [`line${h.lines.length === 1 ? "" : "s"} ${h.lines.join(", ")}`] : [])];
      wait.innerHTML = esc(r.reply) + (hl.length ? `<span class="hl">Lit ${esc(hl.join(" and "))}</span>` : "");
      if (h.cells?.length || h.lines?.length) app.guideHighlight?.(h);
    } catch (e) {
      this.msgs.pop();
      if ((e as Error).name === "AbortError") { wait.remove(); return; }
      const st = e instanceof ApiError ? e.status : 0;
      wait.className = "msg note";
      wait.textContent = st === 503
        ? "The guide is off on this server. Everything else works without it."
        : st === 404 || st === 0 || st >= 500
          ? "The guide needs the Decap API, which is offline. Everything else still works."
          : `The guide couldn't answer: ${(e as Error).message}`;
    }
  }
}
