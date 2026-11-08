// The "Witness terminal": renders one natural-language command as a live, DimOS-style transcript.
// Right-aligned sender column with a ` │ ` separator, teal `▶` tool calls with muted `↳` results spliced
// beneath as they land, a `thinking…` spinner, and a bottom status line with a live elapsed timer.

import { esc } from "../ui/dom";
import { md } from "./md";
import { cexHtml, pill } from "./codeAgent";
import type { Plain, TerminalEvent } from "./api";
import { plainHtml } from "./words";

/** Format a seconds float as a short duration: "12ms", "1.24s", "3.5s". */
function fmtDur(s: number): string {
  if (!Number.isFinite(s) || s < 0) return "";
  if (s === 0) return "0ms";
  if (s < 0.001) return "<1ms";
  if (s < 1) return `${Math.round(s * 1000)}ms`;
  if (s < 10) return `${s.toFixed(2)}s`;
  return `${s.toFixed(1)}s`;
}

/** The terminal's `done` event is a loose bag (api.ts types it as `Record<string, unknown>`). */
interface DoneShape {
  intent?: string;
  verdict?: string;
  proof?: string;
  counterexample?: Record<string, number> | null;
  claim?: string | null;
  english?: string | null;
  plain?: Plain | null;
  answer?: string;
  flags?: unknown[];
  note?: string | null;
  summary?: string;
  findings?: Array<{ text: string; evidence: string; block?: string | null }>;
  next_steps?: string[];
  filename?: string;
  detail?: string;
  renamed_from?: string | null;
  top?: string;
  [k: string]: unknown;
}

/** A short human summary for a tool, derived from the finished command. `null` = no `↳` line. */
function summarizeTool(name: string, args: Record<string, unknown> | undefined, d: DoneShape): string | null {
  switch (name) {
    case "find_rare_triggers": {
      if (!Array.isArray(d.flags)) return null;
      const n = d.flags.length;
      return n === 0 ? "no flags" : n === 1 ? "1 flag" : `${n} flags`;
    }
    case "prove_equiv":
      return typeof d.proof === "string" ? d.proof : null;
    case "get_region": {
      const scope = (args?.scope ?? {}) as { cone_of?: unknown; cells?: unknown[] };
      if (typeof scope.cone_of === "string") return `cone ${scope.cone_of}`;
      if (Array.isArray(scope.cells) && scope.cells.length) return `${scope.cells.length} cells`;
      return "whole chip";
    }
    case "probe_truth":
      return `${Number(args?.n ?? 1024).toLocaleString()} vectors`;
    case "list_chips":
      return "chips";
    default:
      return null;
  }
}

export class Terminal {
  private el: HTMLElement;
  private state: HTMLElement;
  private elapsed: HTMLElement;
  private t0 = 0;
  private raf = 0;
  private busy = false;
  private pending: Array<{ name: string; row: HTMLElement; args?: Record<string, unknown> }> = [];

  lastRepair: { chip: string; verilog: string } | null = null;
  private stick = true;            // follow new lines only while the reader is at the bottom
  private jump: HTMLButtonElement;

  constructor(term: HTMLElement, status: HTMLElement) {
    this.el = term;
    this.state = status.querySelector<HTMLElement>(".ag-state") ?? status;
    this.elapsed = status.querySelector<HTMLElement>(".ag-elapsed") ?? status;
    this.jump = document.createElement("button");
    this.jump.type = "button";
    this.jump.className = "ag-jump";
    this.jump.textContent = "↓ latest";
    this.jump.hidden = true;
    this.jump.addEventListener("click", () => { this.stick = true; this.follow(); });
    term.insertAdjacentElement("afterend", this.jump);
    term.addEventListener("scroll", () => {
      this.stick = term.scrollHeight - term.scrollTop - term.clientHeight < 32;
      if (this.stick) this.jump.hidden = true;
    }, { passive: true });
  }

  /** Scroll to the newest line if the reader is following; otherwise offer a "↓ latest" button. */
  private follow() {
    if (this.stick) { this.el.scrollTop = this.el.scrollHeight; this.jump.hidden = true; }
    else this.jump.hidden = false;
  }

  /** Start a fresh transcript for one command. */
  begin(text: string) {
    this.stop();
    this.stick = true;
    this.jump.hidden = true;
    this.el.hidden = false;
    this.el.innerHTML = "";
    this.pending = [];
    this.busy = true;
    this.t0 = performance.now();
    this.line("you", esc(text), "ag-user");
    this.line("agent", `<span class="ag-thinkdots">thinking</span>`, "ag-think");
    this.setState("working");
    this.elapsed.textContent = "";
    this.raf = requestAnimationFrame(this.loop);
  }

  /** Feed one streamed event into the transcript. */
  event(e: TerminalEvent) {
    const k = (e as { kind: string }).kind;
    if (k === "plan") this.plan(e as unknown as { steps?: string[]; revision?: number });
    else if (k === "thought") this.thought(e as unknown as { step?: string; why?: string });
    else if (k === "memory") this.memory(e as unknown as { fact?: { key?: string; verdict?: string } });
    else if (k === "tool") this.tool(e as Parameters<Terminal["tool"]>[0]);
    else if (k === "claim") this.claim(e as { verdict?: string | null });
    else if (e.kind === "done") this.done(e as unknown as DoneShape);
    else if (e.kind === "error") this.error(e.error);
  }

  /** The investigator's plan (and any later revision): a numbered checklist the steps tick off. */
  private plan(e: { steps?: string[]; revision?: number }) {
    this.hideThinking();
    const steps = (e.steps ?? []).map((s, i) => `<li><span class="ag-n">${i + 1}</span>${esc(s)}</li>`).join("");
    this.line("plan", `<span class="ag-plan-h">${e.revision ? "revised plan" : "plan"}</span><ol class="ag-plan">${steps}</ol>`, "ag-planline");
    this.showThinking();
  }

  /** Why the agent is taking its next step — the visible reasoning between tool calls. */
  private thought(e: { step?: string; why?: string }) {
    this.hideThinking();
    this.line("think", `<b>${esc(e.step ?? "")}</b> — ${esc(e.why ?? "")}`, "ag-thought");
    this.showThinking();
  }

  /** A fact Yosys decided, written to chip memory so the next question starts from it. */
  private memory(e: { fact?: { key?: string; verdict?: string } }) {
    this.line("mem", `remembered <b>${esc(e.fact?.key ?? "")}</b> · ${esc(e.fact?.verdict ?? "")}`, "ag-mem");
  }

  /** A tool finished (the stream only reports a tool once it has returned). */
  private tool(e: { tool: string; args?: Record<string, unknown>; wall_s?: number; is_error?: boolean; summary?: string }) {
    this.hideThinking();
    const meta = typeof e.wall_s === "number" ? fmtDur(e.wall_s) : "";
    const scope = (e.args?.scope ?? null) as { cone_of?: unknown } | null;
    const tgt = scope && typeof scope.cone_of === "string" ? ` <span class="ag-arg">${esc(scope.cone_of)}</span>` : "";
    const row = this.line("agent", `▶ ${esc(e.tool)}${tgt}`, e.is_error ? "ag-tool err" : "ag-tool", meta);
    if (e.summary) this.spliceResult(row, e.summary);
    else this.pending.push({ name: e.tool, row, args: e.args });
    this.showThinking();
  }

  /** A claim attempt: the checker's verdict on the most recent `propose_claim`. */
  private claim(e: { verdict?: string | null }) {
    this.hideThinking();
    const i = this.pending.findIndex((p) => p.name === "propose_claim");
    if (i >= 0) {
      this.spliceResult(this.pending[i].row, String(e.verdict ?? "checked"));
      this.pending.splice(i, 1);
    }
    // the region tools of a finished attempt won't get a better summary; settle them now so they don't pile up
    this.pending = this.pending.filter((p) => {
      if (p.name !== "get_region" && p.name !== "probe_truth") return true;
      const s = summarizeTool(p.name, p.args, {});
      if (s) this.spliceResult(p.row, s);
      return false;
    });
    this.showThinking();
  }

  private done(d: DoneShape) {
    this.hideThinking();
    for (const p of this.pending) {
      const s = summarizeTool(p.name, p.args, d);
      if (s) this.spliceResult(p.row, s);
    }
    this.pending = [];
    this.outcome(d);
    this.elapsed.textContent = fmtDur((performance.now() - this.t0) / 1000);
    this.setState("done");
    this.stop();
  }

  error(msg: string) {
    this.hideThinking();
    this.line("agent", esc(msg), "ag-err");
    this.elapsed.textContent = fmtDur((performance.now() - this.t0) / 1000);
    this.setState("error");
    this.stop();
  }

  private outcome(d: DoneShape) {
    const block = document.createElement("div");
    block.className = "ag-outcome";
    if (d.intent === "repair") {
      const ok = d.verdict === "fixed";
      const diff = (d.diff ?? []) as Array<{ line: number; before: string[]; after: string[] }>;
      const inj = d.injected as { line: number; before: string; after: string } | undefined;
      block.innerHTML = `<span class="pill ${ok ? "ok" : d.verdict === "already matches" ? "ok" : "bad"}">${esc(String(d.verdict))}</span>` +
        `<span class="ag-sub">${ok ? "Yosys proved the repaired design equal to the chip's gates." :
          d.verdict === "already matches" ? "This design already matches the chip: nothing to fix." :
          d.verdict === "unchecked" ? esc(String(d.detail ?? "The design couldn't be checked.")) :
          "No edit the agent tried made Yosys say \"match\"."}</span>` +
        (diff.length ? `<div class="ag-diff">${diff.map((r) => r.before.map((t) => `<div class="del"><span>−</span>${esc(t.trim())}   <i>line ${r.line}</i></div>`).join("") +
          r.after.map((t) => `<div class="add"><span>+</span>${esc(t.trim())}</div>`).join("")).join("")}</div>` : "") +
        (d.why ? `<span class="ag-answer">${esc(String(d.why))}</span>` : "") +
        (d.method ? `<span class="ag-sub">${esc(String(d.method))}</span>` : "") +
        (inj ? `<span class="ag-sub">Reveal: we had switched line ${inj.line} from <code>${esc(inj.before)}</code> to <code>${esc(inj.after)}</code>. ` +
          `${d.found_injected ? "The agent fixed that exact line." : "The agent fixed it a different way; Yosys still proved it equal to the chip."}</span>` : "") +
        (ok ? `<div class="ag-nexts"><button type="button" class="ag-next" data-fixed-dl>Download fixed .v</button></div>` : "");
      this.lastRepair = ok ? { chip: String(d.chip ?? ""), verilog: String(d.verilog ?? "") } : null;
      document.dispatchEvent(new CustomEvent("agent:repaired", { detail: { chip: d.chip, verdict: d.verdict, verilog: d.verilog, diff, proof: d.proof } }));
    } else if (d.intent === "check_source") {
      const v = String(d.verdict), file = esc(String(d.filename ?? "your file"));
      const cex = d.counterexample as Record<string, number> | null | undefined;
      const said: Record<string, string> = {
        proven: `<b>${file}</b> matches this chip. Yosys proved your code and the chip's gates do the same thing for every input, in every state.`,
        tested: `No difference in the first 20 clock cycles after reset. That isn't a proof: Yosys couldn't show they match forever, and some states (a counter near its limit, say) take longer than 20 cycles to reach.`,
        failed: `<b>${file}</b> and this chip behave differently. Yosys found an input sequence from reset where their outputs differ${cex?.step != null ? ` at clock step ${esc(cex.step)}` : ""}.`,
        unavailable: esc(String(d.detail ?? "The check couldn't run.")),
      };
      block.innerHTML = `<span class="pill ${v === "proven" ? "ok" : v === "failed" ? "bad" : "warn"}">${v === "proven" ? "match" : v === "failed" ? "differs" : v === "tested" ? "no difference found" : "couldn't check"}</span>` +
        `<span class="ag-answer">${said[v] ?? esc(v)}</span>` +
        (d.renamed_from ? `<span class="ag-sub">Your top module ${esc(String(d.renamed_from))} was matched up with the chip's ${esc(String(d.top))}.</span>` : "") +
        (v === "failed" ? `<div class="ag-nexts"><button type="button" class="ag-next" data-repair-last>Fix it with the agent</button></div>` : "") +
        (v === "failed" && cex ? `${cexHtml(Object.fromEntries(Object.entries(cex).filter(([k]) => k !== "step")))}<span class="ag-sub">Inputs at that step. On clocked logic the cause can be an earlier input that a register is still holding.</span>` : "");
    } else if (d.intent === "drill") {
      const ok = d.verdict === "caught";
      const f = d.flag as { condition?: string; line?: number } | null | undefined;
      block.innerHTML = `<span class="pill ${ok ? "ok" : "bad"}">${ok ? "caught" : esc(String(d.verdict))}</span>` +
        `<span class="ag-sub">${ok ? "We hid a trigger in a copy of this chip. The hunting tools never got the value, and they found it." : "The planted trigger was not recovered."}</span>` +
        (f?.condition ? `<code>${esc(f.condition)}${f.line ? `   // line ${esc(f.line)}` : ""}</code>` : "") +
        `<span class="ag-answer">${esc(String(d.note ?? ""))}</span>` +
        (d.error ? `<span class="ag-sub">${esc(String(d.error))}</span>` : "");
    } else     if (d.intent === "hunt") {
      block.innerHTML = d.verdict === "found"
        ? `<span class="pill warn">found</span><span class="ag-sub">hidden logic — equivalence with the author's source fails</span>${cexHtml(d.counterexample ?? null)}`
        : d.verdict === "clean"
          ? `<span class="pill ok">clean</span><span class="ag-sub">proven equivalent to the author's source: no hidden logic</span>`
        : d.verdict === "unchecked"
          ? `<span class="pill">not checked</span><span class="ag-sub">${esc(d.note ?? "nothing could be checked against the author's source")}</span>`
          : `<span class="pill warn">inconclusive</span><span class="ag-sub">${esc(d.note ?? "only random testing passed; that is not a proof")}` +
            `${Array.isArray(d.flags) && d.flags.length ? ` · ${d.flags.length} rare condition${d.flags.length > 1 ? "s" : ""} still open` : ""}</span>`;
    } else if (d.intent === "investigate") {
      const tag: Record<string, string> = { yosys: "ok", proof: "ok", structure: "", estimate: "warn", memory: "", unverified: "bad" };
      const fs = (d.findings ?? []).map((f) =>
        `<li><span class="pill sm ${tag[f.evidence] ?? ""}">${esc(f.evidence)}</span>${f.block ? `<b>${esc(f.block)}</b> ` : ""}<span class="ag-ftext">${md(f.text)}</span></li>`).join("");
      const nx = (d.next_steps ?? []).map((n) => `<button type="button" class="ag-next" data-cmd="${esc(n)}">${esc(n)}</button>`).join("");
      block.innerHTML = `<div class="ag-answer">${md(d.summary ?? "")}</div>` +
        (fs ? `<ul class="ag-findings">${fs}</ul>` : "") +
        (nx ? `<div class="ag-nexts"><span class="ag-sub">next</span>${nx}</div>` : "");
    } else if (d.intent === "explain" && d.plain) {
      block.innerHTML = `${pill(String(d.verdict))}${plainHtml(d.plain)}` +
        (d.claim ? `<details class="ag-claimbox"><summary>the checked claim</summary><code>${esc(String(d.claim))}</code></details>` : "");
    } else if (d.intent === "explain") {
      block.innerHTML = `${pill(String(d.verdict))}` +
        (d.claim ? `<details class="ag-claimbox"${String(d.claim).length < 160 ? " open" : ""}><summary>claim (${String(d.claim).length} chars)</summary><code>${esc(String(d.claim))}</code></details>` : "") +
        (d.english ? `<span class="ag-sub ag-clamp">${esc(String(d.english))}</span>` : "") +
        (d.verdict === "refuted" ? cexHtml(d.counterexample ?? null) : "");
    } else {
      block.innerHTML = `<div class="ag-answer">${md(String(d.answer ?? ""))}</div>`;
    }
    this.el.append(block);
  }

  private line(who: string, body: string, cls: string, meta = ""): HTMLElement {
    const div = document.createElement("div");
    div.className = `ag-line ${cls}`;
    div.innerHTML = `<span class="ag-who">${esc(who)}</span><span class="ag-sep">│</span><span class="ag-body">${body}</span>${meta ? `<span class="ag-meta">${meta}</span>` : ""}`;
    this.el.append(div);
    this.follow();
    return div;
  }

  private spliceResult(row: HTMLElement, summary: string) {
    const r = document.createElement("div");
    r.className = "ag-line ag-result";
    r.innerHTML = `<span class="ag-who"></span><span class="ag-sep"></span><span class="ag-body">↳ ${esc(summary)}</span>`;
    row.insertAdjacentElement("afterend", r);
  }

  private hideThinking() {
    this.el.querySelector(".ag-think")?.remove();
  }

  private showThinking() {
    if (!this.busy || this.el.querySelector(".ag-think")) return;
    this.line("agent", `<span class="ag-thinkdots">thinking</span>`, "ag-think");
    this.follow();
  }

  private setState(word: string) {
    this.state.className = `ag-state ${word}`;
    this.state.textContent = word;
  }

  private loop = () => {
    if (!this.busy) return;
    this.elapsed.textContent = fmtDur((performance.now() - this.t0) / 1000);
    this.raf = requestAnimationFrame(this.loop);
  };

  private stop() {
    this.busy = false;
    cancelAnimationFrame(this.raf);
  }
}
