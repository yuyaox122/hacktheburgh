// The Agent drawer (key A): every Witness run on the current chip, live. Each run shows what the agent looked at,
// each claim it made, the checker's verdict (with the input that breaks a wrong claim), and what you can do next.

import { $, esc } from "../ui/dom";
import { app } from "../ui/state";
import { icon } from "../ui/icons";
import { agentUp, command, type TerminalEvent } from "./api";
import { cexHtml, pill } from "./codeAgent";
import { runAgent, suggestEditFrom } from "./agent";
import { store, type Run } from "./store";
import { Terminal } from "./terminal";

const STEP: Record<string, (s: Run["steps"][number]) => string> = {
  get_region: () => "Read the region: its inputs, outputs and decompiled lines",
  probe_truth: (s) => `Simulated ${Number((s.args as { n?: number } | undefined)?.n ?? 1024).toLocaleString()} input vectors (evidence, not proof)`,
  propose_claim: () => "Sent the claim to Yosys",
};

export class AgentPanel {
  readonly el = document.createElement("aside");
  private chip: string | null = null;
  private raf = 0;
  private verilog = new Map<string, string>();   // chip -> decompiled Verilog (for "Suggest as edit")
  private term: Terminal;
  private runSeq = 0;

  constructor() {
    this.el.className = "drawer ag-drawer";
    this.el.setAttribute("aria-label", "Witness agent");
    this.el.innerHTML = `
      <div class="pane-h">${icon.cube}Agent<span class="sub">explains a region · Yosys decides</span><span class="grow"></span>
        <button class="btn plain sm" type="button" id="agClear" title="Forget this chip's runs, edits and comments">Clear</button>
        <button class="btn plain icon" type="button" id="agClose" aria-label="Close the agent panel" title="Close (Esc)">${icon.close}</button></div>
      <div class="ag-intro" id="agIntro"></div>
      <div class="ag-termbox">
        <div class="ag-term" id="agTerm" hidden></div>
        <div class="ag-status" id="agStatus"><span class="ag-state ready">ready</span><span class="ag-elapsed"></span></div>
        <form class="ag-cmd" id="agCmd"><input class="text" id="agCmdIn" autocomplete="off"
          placeholder="Type a message…" aria-label="Command the chip agent">
          <button class="btn primary" type="submit">Run</button></form>
        <div class="ag-suggest" id="agSuggest">
          <button type="button" class="ag-next" data-cmd="Audit this chip: what are its parts, and is the unrecognised logic doing anything odd?">Audit this chip</button>
          <button type="button" class="ag-next" data-cmd="Is anything hidden in this chip?">Is anything hidden?</button>
          <button type="button" class="ag-next" data-cmd="Red-team drill: plant a hidden trigger and find it blind">Red-team drill</button>
          <button type="button" class="ag-next" data-cmd="What is the slowest path, and what does the block on it do?">Slowest path</button>
          <button type="button" class="ag-next" data-cmd="What do we already know about this chip? Review memory and fill one gap.">What do we know?</button>
        </div>
      </div>
      <div class="ag-runs" id="agRuns" aria-live="polite"></div>`;
    document.body.append(this.el);
    this.term = new Terminal($("#agTerm", this.el), $("#agStatus", this.el));
    $("#agClose", this.el).addEventListener("click", () => this.close());
    $("#agClear", this.el).addEventListener("click", () => { if (this.chip) store.clear(this.chip); });
    $("#agCmd", this.el).addEventListener("submit", (e) => {
      e.preventDefault();
      const i = $<HTMLInputElement>("#agCmdIn", this.el), text = i.value.trim();
      if (text) { i.value = ""; void this.runCommand(text); }
    });
    this.el.addEventListener("click", (e) => this.onClick(e));
    store.on((chip) => { if (chip === this.chip) this.schedule(); });
    document.addEventListener("agent:open", () => this.open());
    void agentUp().then((ok) => { if (!ok) $("#agIntro", this.el).innerHTML = `<div class="callout bad">The agent service isn't reachable. Start it with <code>decap serve --port 8791</code> (decap/agent).</div>`; });
  }

  setChip(chip: string | null, verilog?: string) {
    this.chip = chip;
    if (chip && verilog) this.verilog.set(chip, verilog);
    this.schedule();
  }
  get isOpen() { return this.el.classList.contains("open"); }
  open() { this.el.classList.add("open"); this.schedule(); }
  close() { this.el.classList.remove("open"); }
  toggle() { this.isOpen ? this.close() : this.open(); }

  /** The terminal: run one natural-language command and stream its tool calls + result into the drawer. */
  private async runCommand(text: string) {
    if (!this.chip) return;
    this.open();
    const seq = ++this.runSeq;
    this.term.begin(text);
    try {
      await command(this.chip, text, (e: TerminalEvent) => { if (seq === this.runSeq) this.term.event(e); });
    } catch (err) {
      if (seq === this.runSeq) this.term.error((err as Error).message);
    }
  }

  private schedule() { cancelAnimationFrame(this.raf); this.raf = requestAnimationFrame(() => this.render()); }

  private render() {
    const intro = $("#agIntro", this.el);
    if (!intro.querySelector(".bad")) {
      intro.innerHTML = `<p class="muted">Hover a line of decompiled Verilog and press <b>Explain</b> or <b>Suggest edit</b>, select a cell in Explore and press <b>Explain block</b>, or write a comment and tick <b>Ask the agent</b>. The agent can only look; every verdict below comes from Yosys.</p>`;
    }
    const runs = this.chip ? store.runs(this.chip) : [];
    $("#agRuns", this.el).innerHTML = runs.length ? runs.map((r) => this.card(r)).join("")
      : `<div class="empty"><p>No agent runs on ${esc(this.chip ?? "this page")} yet.</p></div>`;
  }

  private card(r: Run): string {
    const secs = ((r.t1 ?? Date.now()) - r.t0) / 1000;
    const status = r.status === "running" ? `<span class="pill gold"><span class="spin"></span> working</span>`
      : r.status === "error" ? `<span class="pill bad">error</span>` : pill(r.final);
    const steps = r.steps.filter((s) => s.tool !== "propose_claim").map((s) => `<li>${esc(STEP[s.tool]?.(s) ?? s.tool)}</li>`).join("");
    const atts = r.attempts.map((a) => `
      <div class="ag-att">
        <div class="ag-att-h"><span class="ag-tag">${a.first_draft ? "First draft" : `Refine ${a.attempt}`}</span>${pill(a.verdict)}${a.checker_s != null ? `<span class="muted">Yosys ${a.checker_s.toFixed(2)} s</span>` : ""}</div>
        <code class="ag-claim">${esc(a.claim ?? "(no claim)")}</code>
        ${a.english ? `<p class="muted">${esc(a.english)}</p>` : ""}
        ${a.verdict === "refuted" ? cexHtml(a.counterexample) : ""}
        ${a.error ? `<p class="muted">${esc(a.error)}</p>` : ""}
        ${a.credit?.reason && a.verdict !== "proven" && a.verdict !== "inductive" ? `<p class="muted">${esc(a.credit.reason)}</p>` : ""}
        ${a.credit?.cleared ? `<p class="ag-ok">${a.credit.literals} literals instead of ${a.credit.cone_literals} in the decompiled code</p>` : ""}
      </div>`).join("");
    const canEdit = r.explained && r.lines && r.lines[0] > 0 && !store.edits(r.chip).some((e) => e.runId === r.id);
    return `<article class="ag-run ${r.status}${r.explained ? " explained" : ""}" data-run="${r.id}">
      <header><b>${esc(r.label)}</b>${status}<span class="muted">${secs.toFixed(1)} s${r.cached ? " · cached" : ""}</span></header>
      ${steps ? `<ol class="ag-steps">${steps}</ol>` : r.status === "running" ? `<ol class="ag-steps"><li><span class="spin"></span> Reading the region…</li></ol>` : ""}
      ${atts}
      ${r.error ? `<div class="callout bad">${esc(r.error)}</div>` : ""}
      <div class="ag-actions">
        ${r.cells.length ? `<button type="button" class="btn sm" data-run-act="cells">Show cells (${r.cells.length})</button>` : ""}
        <button type="button" class="btn sm" data-run-act="3d">View in 3D</button>
        ${canEdit ? `<button type="button" class="btn sm primary" data-run-act="edit">Suggest as edit</button>` : ""}
        ${r.status !== "running" ? `<button type="button" class="btn sm" data-run-act="again">Run again</button>` : ""}
      </div>
    </article>`;
  }

  private onClick(e: MouseEvent) {
    const cmd = (e.target as HTMLElement).closest<HTMLElement>("[data-cmd]");
    if (cmd?.dataset.cmd) { void this.runCommand(cmd.dataset.cmd); return; }
    const b = (e.target as HTMLElement).closest<HTMLElement>("[data-run-act]");
    if (!b) return;
    const run = store.run(b.closest<HTMLElement>("[data-run]")!.dataset.run!);
    if (!run) return;
    const act = b.dataset.runAct;
    if (act === "cells") app.guideHighlight?.({ cells: run.cells });
    else if (act === "3d") location.hash = `#/stage/${encodeURIComponent(run.chip)}`;
    else if (act === "edit") {
      if (suggestEditFrom(run, this.verilog.get(run.chip) ?? "")) location.hash = `#/chip/${encodeURIComponent(run.chip)}`;
    } else if (act === "again") {
      void runAgent({ chip: run.chip, scope: run.scope, label: run.label, origin: run.origin, line: run.line, lines: run.lines,
        fresh: true, suggestEdit: run.origin === "edit", verilog: this.verilog.get(run.chip) });
    }
  }
}
