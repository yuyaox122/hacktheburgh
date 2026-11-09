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
import { plainHtml, verdictWords } from "./words";
import * as verilogIO from "./verilogIO";

const STEP: Record<string, (s: Run["steps"][number]) => string> = {
  get_region: () => "Read this part's inputs, outputs and code",
  probe_truth: (s) => `Tried ${Number((s.args as { n?: number } | undefined)?.n ?? 1024).toLocaleString()} random inputs to see what it does (a hint, not a proof)`,
  propose_claim: () => "Asked Yosys to prove a short summary",
  decompile: () => "Read the decompiled Verilog",
  recover_blocks: () => "Listed the chip's blocks",
};

const SLASH: Array<[string, string]> = [
  ["/explain b3", "explain a block or output"], ["/hunt", "look for hidden logic"], ["/drill", "red-team drill"],
  ["/fault", "fault drill: break a gate, fix it"], ["/audit", "plan and audit the chip"], ["/slowest", "slowest path"],
  ["/memory", "what we already know"], ["/check", "upload your Verilog"], ["/status", "recent sessions"],
  ["/stop", "stop the running command"], ["/clear", "clear the terminal"], ["/help", "list commands"],
];

export class AgentPanel {
  readonly el = document.createElement("aside");
  private chip: string | null = null;
  private raf = 0;
  private verilog = new Map<string, string>();   // chip -> decompiled Verilog (for "Suggest as edit")
  private term: Terminal;
  private runSeq = 0;
  private renderedChip: string | null | undefined = undefined;

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
        <div class="ag-status" id="agStatus"><span class="ag-state ready">ready</span><span class="ag-elapsed"></span>
          <button type="button" class="btn plain sm" id="agStop" hidden title="Stop this command">Stop</button></div>
        <form class="ag-cmd" id="agCmd"><input class="text" id="agCmdIn" autocomplete="off" list="agSlash"
          placeholder="Type a message, or / for commands" aria-label="Command the chip agent">
          <button class="btn primary" type="submit">Run</button></form>
        <div class="ag-suggest" id="agSuggest">
          <button type="button" class="ag-next" data-cmd="Audit this chip: what are its parts, and is the unrecognised logic doing anything odd?">Audit this chip</button>
          <button type="button" class="ag-next" data-cmd="Is anything hidden in this chip?">Is anything hidden?</button>
          <button type="button" class="ag-next" data-cmd="Red-team drill: plant a hidden trigger and find it blind">Red-team drill</button>
          <button type="button" class="ag-next" data-cmd="Fault drill: switch one gate, then find and fix it" title="Switch one gate at random, then the agent finds and fixes it; Yosys checks">Fault drill</button>
          <button type="button" class="ag-next" data-cmd="What is the slowest path, and what does the block on it do?">Slowest path</button>
          <button type="button" class="ag-next" data-cmd="What do we already know about this chip? Review memory and fill one gap.">What do we know?</button>
          <button type="button" class="ag-next" data-vio="up" title="Upload a .v file; Yosys checks whether this chip implements it">Check my Verilog…</button>
        </div>
      </div>
      <div class="ag-runs" id="agRuns" aria-live="polite"></div>
      <datalist id="agSlash">${SLASH.map(([c, d]) => `<option value="${c}">${d}</option>`).join("")}</datalist>`;
    document.body.append(this.el);
    this.term = new Terminal($("#agTerm", this.el), $("#agStatus", this.el));
    $("#agClose", this.el).addEventListener("click", () => this.close());
    $("#agStop", this.el).addEventListener("click", () => this.cancel("Stopped."));
    $("#agClear", this.el).addEventListener("click", () => { if (this.chip) store.clear(this.chip); });
    $("#agCmd", this.el).addEventListener("submit", (e) => {
      e.preventDefault();
      const i = $<HTMLInputElement>("#agCmdIn", this.el), text = i.value.trim();
      if (text) { i.value = ""; void this.runCommand(text); }
    });
    this.el.addEventListener("click", (e) => this.onClick(e));
    store.on((chip) => { if (chip === this.chip) this.schedule(); });
    document.addEventListener("agent:open", () => this.open());
    // Auto-collapse: any press outside the drawer folds it away (runs keep streaming; the Agent button pulses meanwhile).
    // The Agent button is skipped so its own toggle still works; triggers elsewhere re-open it on their click.
    document.addEventListener("pointerdown", (e) => {
      const t = e.target as Element | null;
      if (this.isOpen && t && !this.el.contains(t) && !t.closest("#btnAgent")) this.close();
    }, true);
    verilogIO.install();
    // Alex's verify view (or anything) can hand a faulty design to the agent: agent:repair -> ... -> agent:repaired
    document.addEventListener("agent:repair", (e) => {
      const d = (e as CustomEvent<{ chip: string; verilog: string; suspects?: unknown[]; filename?: string }>).detail;
      if (!d?.chip || !d.verilog) return;
      void this.runCommand(`Fix this design (${d.suspects?.length ?? 0} suspect${d.suspects?.length === 1 ? "" : "s"} from the fault localiser)`,
        { verilog: d.verilog, filename: d.filename ?? "faulty.v", repair: true, suspects: d.suspects }, d.chip);
    });
    document.addEventListener("agent:upload", (e) => {
      const d = (e as CustomEvent<{ chip: string; filename: string; text: string }>).detail;
      this.lastUpload = { chip: d.chip, filename: d.filename, verilog: d.text };
      void this.runCommand(`Check my file ${d.filename} (${d.text.split("\n").length} lines) against this chip`,
        { verilog: d.text, filename: d.filename }, d.chip);
    });
    void agentUp().then((ok) => { if (!ok) $("#agIntro", this.el).innerHTML = `<div class="callout bad">The agent service isn't reachable. Start it with <code>decap serve --port 8791</code> (decap/agent).</div>`; });
  }

  setChip(chip: string | null, verilog?: string) {
    if (chip !== this.chip) this.cancel();   // don't stream the old chip's result into the new chip's panel
    this.chip = chip;
    verilogIO.setChip(chip);
    if (chip && verilog) this.verilog.set(chip, verilog);
    this.schedule();
  }
  get isOpen() { return this.el.classList.contains("open"); }
  open() { this.el.classList.add("open"); this.schedule(); }
  close() { this.el.classList.remove("open"); }
  toggle() { this.isOpen ? this.close() : this.open(); }

  /** The terminal: run one natural-language command and stream its tool calls + result into the drawer. */
  private ctrl: AbortController | null = null;
  private lastUpload: { chip: string; filename: string; verilog: string } | null = null;

  /** Stop the running command (Stop button, a new command, or switching chips). */
  private cancel(say?: string) {
    if (!this.ctrl) return;
    this.ctrl.abort();
    this.ctrl = null;
    this.runSeq++;
    if (say) this.term.error(say);
    $("#agStop", this.el).hidden = true;
    document.getElementById("btnAgent")?.classList.remove("ag-running");
  }

  /** CLI-style slash commands in the terminal: each maps onto the same agent calls as the buttons (no shell). */
  private slash(text: string): boolean {
    const [cmd, ...rest] = text.slice(1).trim().split(/\s+/);
    const arg = rest.join(" ");
    const SAY: Record<string, string> = {
      hunt: "Is anything hidden in this chip?", drill: "Red-team drill: plant a hidden trigger and find it blind",
      fault: "Fault drill: switch one gate, then find and fix it", audit: "Audit this chip: what are its parts, and is the unrecognised logic doing anything odd?",
      slowest: "What is the slowest path, and what does the block on it do?", memory: "What do we already know about this chip? Review memory and fill one gap.",
    };
    if (cmd === "explain") { void this.runCommand(arg ? `what does ${arg} do?` : "explain this chip"); return true; }
    if (SAY[cmd]) { void this.runCommand(SAY[cmd]); return true; }
    if (cmd === "ask") { void this.runCommand(arg || "what does this chip do?"); return true; }
    if (cmd === "check") { (document.querySelector<HTMLElement>("[data-vio='up']"))?.click(); return true; }
    if (cmd === "stop") { this.cancel("Stopped."); return true; }
    if (cmd === "clear") { this.term.clear(); return true; }
    if (cmd === "status") { void this.status(); return true; }
    this.term.note(text, `Commands: ${SLASH.map((s) => s[0]).join("  ")}`, cmd === "help" ? "" : `unknown command /${cmd}. `);
    return true;
  }

  private async status() {
    try {
      const rows = await (await fetch("/witness/status")).json() as Array<{ id: string; chip?: string; kind?: string; status?: string; started_at?: string; command?: string }>;
      this.term.note("/status", rows.length ? rows.slice().reverse().map((r) =>
        `${(r.started_at ?? "").slice(11, 19)}  ${(r.status ?? "?").padEnd(8)} ${(r.kind ?? "").padEnd(11)} ${r.chip ?? ""}  ${(r.command ?? "").slice(0, 40)}`).join("\n") : "no agent sessions yet");
    } catch (e) { this.term.error(`/status: ${(e as Error).message}`); }
  }

  private async runCommand(text: string, upload?: { verilog: string; filename?: string; repair?: boolean; suspects?: unknown[] }, chip = this.chip) {
    if (!upload && text.startsWith("/")) { this.open(); this.slash(text); return; }
    if (!chip) return;
    this.open();
    this.cancel();
    const seq = ++this.runSeq;
    const ctrl = this.ctrl = new AbortController();
    $("#agStop", this.el).hidden = false;
    document.getElementById("btnAgent")?.classList.add("ag-running");
    this.term.begin(text);
    try {
      await command(chip, text, (e: TerminalEvent) => { if (seq === this.runSeq) this.term.event(e); }, ctrl.signal, upload);
    } catch (err) {
      if (seq === this.runSeq && (err as Error).name !== "AbortError") this.term.error((err as Error).message);
    } finally {
      if (this.ctrl === ctrl) { this.ctrl = null; $("#agStop", this.el).hidden = true; document.getElementById("btnAgent")?.classList.remove("ag-running"); }
    }
  }

  private schedule() { cancelAnimationFrame(this.raf); this.raf = requestAnimationFrame(() => this.render()); }

  private render() {
    const intro = $("#agIntro", this.el);
    if (!intro.querySelector(".bad")) {
      intro.innerHTML = `<p class="muted">Ask about this chip below, or hover a line of code and press <b>Explain</b> or <b>Ideas</b> (suggested edits, checked by Yosys when you apply them). The agent writes the explanation. Yosys decides whether it's true.</p>`;
    }
    const runs = this.chip ? store.runs(this.chip) : [];
    const host = $("#agRuns", this.el);
    const top = this.el.scrollTop;   // the whole drawer scrolls (agent.css), not just the runs list
    const sameChip = this.renderedChip === this.chip;
    this.renderedChip = this.chip;
    const before = new Set([...host.querySelectorAll<HTMLElement>("[data-run]")].map((a) => a.dataset.run));
    const open = new Set([...host.querySelectorAll<HTMLDetailsElement>("details[open]")].map((d) => `${d.closest<HTMLElement>("[data-run]")?.dataset.run}|${d.className}`));
    host.innerHTML = runs.length ? runs.map((r) => this.card(r)).join("")
      : `<div class="empty"><p>No agent runs on ${esc(this.chip ?? "this page")} yet.</p></div>`;
    host.querySelectorAll<HTMLDetailsElement>("details").forEach((d) => {
      if (open.has(`${d.closest<HTMLElement>("[data-run]")?.dataset.run}|${d.className}`)) d.open = true;
    });
    this.el.scrollTop = top;
    // A new run (e.g. Explain on a hovered line) scrolls into view instead of landing below the terminal.
    const fresh = sameChip && [...host.querySelectorAll<HTMLElement>("[data-run]")].find((a) => !before.has(a.dataset.run));
    if (fresh && this.isOpen) fresh.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  private card(r: Run): string {
    const secs = ((r.t1 ?? Date.now()) - r.t0) / 1000;
    const status = r.status === "running" ? `<span class="pill gold"><span class="spin"></span> working</span>`
      : r.status === "error" ? `<span class="pill bad">error</span>`
      : !r.explained && r.plain?.source === "code" ? `<span class="pill ok" title="Read from the decompiled code, which is proven equal to the gates">explained</span>`
      : pill(r.final);
    const steps = r.steps.filter((s) => s.tool !== "propose_claim").map((s) => `<li>${esc(STEP[s.tool]?.(s) ?? s.tool)}</li>`).join("");
    const atts = r.attempts.map((a) => `
      <div class="ag-att">
        <div class="ag-att-h"><span class="ag-tag">${a.first_draft ? "First try" : `Try ${a.attempt + 1}`}</span>${pill(a.verdict)}${a.checker_s != null ? `<span class="muted">Yosys ${a.checker_s.toFixed(2)} s</span>` : ""}</div>
        <code class="ag-claim">${esc(a.claim ?? "(no claim)")}</code>
        ${a.english ? `<p class="muted ag-clamp">${esc(a.english)}</p>` : ""}
        ${a.verdict === "refuted" ? cexHtml(a.counterexample) : ""}
        ${a.error ? `<p class="muted">${esc(a.error)}</p>` : ""}
        ${a.credit?.reason && a.verdict !== "proven" && a.verdict !== "inductive" ? `<p class="muted">${esc(a.credit.reason)}</p>` : ""}
        ${a.credit?.cleared ? `<p class="ag-ok">Shorter than the code: ${a.credit.literals} terms instead of ${a.credit.cone_literals}</p>` : ""}
      </div>`).join("");
    const canEdit = r.explained && r.lines && r.lines[0] > 0 && !store.edits(r.chip).some((e) => e.runId === r.id);
    return `<article class="ag-run ${r.status}${r.explained ? " explained" : ""}" data-run="${r.id}">
      <header><b>${esc(r.label)}</b>${status}<span class="muted">${secs.toFixed(1)} s${r.cached ? " · cached" : ""}</span></header>
      ${r.status === "running" ? `<ol class="ag-steps">${steps || `<li><span class="spin"></span> Reading this part of the chip…</li>`}</ol>` : ""}
      ${r.note ? `<p class="ag-why">${esc(r.note)}</p>` : ""}
      ${plainHtml(r.plain)}
      ${r.status === "done" && !r.explained ? `<p class="ag-why">${r.plain?.source === "code"
        ? `No shorter proven summary yet${r.attempts.length ? ` (Yosys checked ${r.attempts.length} tr${r.attempts.length === 1 ? "y" : "ies"})` : ""}. The explanation above is read from the decompiled code itself.`
        : r.plain ? "" : esc(verdictWords(r.final ?? "", true))}</p>` : ""}
      ${r.status !== "running" && (atts || steps) ? `<details class="ag-how"${r.plain ? "" : " open"}><summary>How it was checked (${r.attempts.length} attempt${r.attempts.length === 1 ? "" : "s"})</summary>${steps ? `<ol class="ag-steps">${steps}</ol>` : ""}${atts}</details>` : ""}
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
    const t = e.target as HTMLElement;
    if (t.closest("[data-repair-last]") && this.lastUpload) {
      const u = this.lastUpload;
      void this.runCommand(`Fix ${u.filename} so it matches this chip`, { verilog: u.verilog, filename: u.filename, repair: true }, u.chip);
      return;
    }
    if (t.closest("[data-fixed-dl]") && this.term.lastRepair) {
      verilogIO.saveText(`${this.term.lastRepair.chip}_repaired.v`, this.term.lastRepair.verilog);
      return;
    }
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
