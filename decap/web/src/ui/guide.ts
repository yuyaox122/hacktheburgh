// AI guide: a chat drawer. POST /api/chat with a compact context of what is on screen; apply returned highlights.

import { ApiError, chat } from "../data/load";
import { $, esc } from "./dom";
import { app } from "./state";

type Msg = { role: "user" | "assistant"; content: string };

const SUGGEST: Record<string, string[]> = {
  stage: ["What does this chip do?", "What is a flip-flop?", "Why are there clock buffers?", "Explain the selected cell"],
  wall: ["What does 'proven' mean here?", "Why are some chips amber?", "How was the test set chosen?"],
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
      <div class="pane-h">Guide <b>plain-English answers about what's on screen</b><span class="grow"></span><button class="btn sm" type="button" id="gdClose" aria-label="Close the guide">Close <kbd>Esc</kbd></button></div>
      <div class="msgs" id="gdMsgs" aria-live="polite"></div>
      <div class="chipsugg" id="gdSugg"></div>
      <form class="askform" id="gdForm"><input class="text" id="gdIn" autocomplete="off" placeholder="Ask about this chip…" aria-label="Ask the guide"><button class="btn primary" type="submit">Ask</button></form>`;
    document.body.append(this.el);
    $("#gdClose", this.el).addEventListener("click", () => this.close());
    $("#gdForm", this.el).addEventListener("submit", (e) => { e.preventDefault(); const i = $<HTMLInputElement>("#gdIn", this.el); const q = i.value.trim(); i.value = ""; if (q) this.ask(q); });
    $("#gdSugg", this.el).addEventListener("click", (e) => { const b = (e.target as HTMLElement).closest("button"); if (b) this.ask(b.textContent!); });
    this.note("Hi, I'm the guide. I can see what's on screen (the chip, your selection, the blocks and flags) and I'll light up whatever I talk about. I never make up ids or results.");
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
  private note(t: string) { this.bubble("ai", esc(t)); }

  async ask(q: string) {
    if (!this.isOpen) this.el.classList.add("open");
    this.msgs.push({ role: "user", content: q });
    this.bubble("me", esc(q));
    const wait = this.bubble("ai", `<span class="spin"></span> thinking…`);
    this.ctrl?.abort(); this.ctrl = new AbortController();
    try {
      const r = await chat(this.msgs.slice(-12), app.guideContext?.() ?? { page: app.route }, this.ctrl.signal);
      this.msgs.push({ role: "assistant", content: r.reply });
      const h = r.highlights ?? {};
      const hl = [...(h.cells?.length ? [`cells ${h.cells.slice(0, 12).join(", ")}${h.cells.length > 12 ? "…" : ""}`] : []), ...(h.lines?.length ? [`lines ${h.lines.join(", ")}`] : [])];
      wait.innerHTML = esc(r.reply) + (hl.length ? `<span class="hl">▸ highlighted ${esc(hl.join(" · "))}</span>` : "");
      if (h.cells?.length || h.lines?.length) app.guideHighlight?.(h);
    } catch (e) {
      this.msgs.pop();
      if ((e as Error).name === "AbortError") { wait.remove(); return; }
      const st = e instanceof ApiError ? e.status : 0;
      wait.className = "msg note";
      wait.textContent = st === 503
        ? "The AI guide is switched off: no LLM key is set on the server. Everything else in Decap works without it."
        : st === 404 || st === 0 || st >= 500
          ? "I can't reach the Decap API (is it running on port 8000?). The rest of the page still works."
          : `The guide couldn't answer: ${(e as Error).message}`;
    }
  }
}
