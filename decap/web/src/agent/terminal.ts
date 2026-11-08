// The "Witness terminal": renders one natural-language command as a live, DimOS-style transcript.
// Right-aligned sender column with a ` │ ` separator, teal `▶` tool calls with muted `↳` results spliced
// beneath as they land, a `thinking…` spinner, and a bottom status line with a live elapsed timer.

import { esc } from "../ui/dom";
import { cexHtml, pill } from "./codeAgent";
import type { TerminalEvent } from "./api";

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
  answer?: string;
  flags?: unknown[];
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

  constructor(term: HTMLElement, status: HTMLElement) {
    this.el = term;
    this.state = status.querySelector<HTMLElement>(".ag-state") ?? status;
    this.elapsed = status.querySelector<HTMLElement>(".ag-elapsed") ?? status;
  }

  /** Start a fresh transcript for one command. */
  begin(text: string) {
    this.stop();
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
    if (e.kind === "tool") this.tool(e);
    else if (e.kind === "claim") this.claim(e);
    else if (e.kind === "done") this.done(e as unknown as DoneShape);
    else if (e.kind === "error") this.error(e.error);
  }

  /** A tool finished (the stream only reports a tool once it has returned). */
  private tool(e: { tool: string; args?: Record<string, unknown>; wall_s?: number; is_error?: boolean }) {
    this.hideThinking();
    const meta = typeof e.wall_s === "number" ? fmtDur(e.wall_s) : "";
    const row = this.line("agent", `▶ ${esc(e.tool)}`, e.is_error ? "ag-tool err" : "ag-tool", meta);
    this.pending.push({ name: e.tool, row, args: e.args });
  }

  /** A claim attempt: the checker's verdict on the most recent `propose_claim`. */
  private claim(e: { verdict?: string | null }) {
    this.hideThinking();
    const i = this.pending.findIndex((p) => p.name === "propose_claim");
    if (i >= 0) {
      this.spliceResult(this.pending[i].row, String(e.verdict ?? "checked"));
      this.pending.splice(i, 1);
    }
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
    if (d.intent === "hunt") {
      const found = d.verdict === "found";
      block.innerHTML = found
        ? `<span class="pill warn">found</span><span class="ag-sub">hidden logic — equivalence with the author's source fails</span>${cexHtml(d.counterexample ?? null)}`
        : `<span class="pill ok">clean</span><span class="ag-sub">matches the author's source — no hidden logic</span>`;
    } else if (d.intent === "explain") {
      block.innerHTML = `${pill(String(d.verdict))}` +
        (d.claim ? `<code>${esc(String(d.claim))}</code>` : "") +
        (d.english ? `<span class="ag-sub">${esc(String(d.english))}</span>` : "") +
        (d.verdict === "refuted" ? cexHtml(d.counterexample ?? null) : "");
    } else {
      block.innerHTML = `<span class="ag-answer">${esc(String(d.answer ?? ""))}</span>`;
    }
    this.el.append(block);
  }

  private line(who: string, body: string, cls: string, meta = ""): HTMLElement {
    const div = document.createElement("div");
    div.className = `ag-line ${cls}`;
    div.innerHTML = `<span class="ag-who">${esc(who)}</span><span class="ag-sep">│</span><span class="ag-body">${body}</span>${meta ? `<span class="ag-meta">${meta}</span>` : ""}`;
    this.el.append(div);
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
