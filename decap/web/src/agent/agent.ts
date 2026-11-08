// The Witness agent's front-end controller: start runs from anywhere (a Verilog line, a block, a 3D cell, a comment),
// stream them into the store, turn a proven claim into a suggested edit, and answer @agent comments.

import type { Block, ChipResult } from "../data/types";
import { explain, formalise, type Scope } from "./api";
import { claimToVerilog, store, uid, type Origin, type Run } from "./store";
import { verdictWords } from "./words";

const KIND_WORD: Record<string, string> = {
  adder: "adder", subtractor: "subtractor", counter: "counter", register: "register", shift: "shift register",
  comparator: "comparator", mux: "multiplexer", reduce: "reduction", const: "constant", copy: "wire copy", logic: "leftover logic",
};

const running = new Map<string, AbortController>();

/** The region a decompiled line belongs to: its block (cone of the block id), else the cells mapped to that line. */
export function regionForLine(r: ChipResult, ln: number): { scope: Scope; label: string; lines: [number, number]; block?: Block } | null {
  const b = r.blocks.find((x) => ln >= x.lines[0] && ln <= x.lines[1]);
  if (b) {
    return { scope: { cone_of: b.id }, block: b, lines: b.lines,
      label: `line ${ln} · block ${b.id} (${KIND_WORD[b.kind] ?? b.kind}, ${b.cells.length} cells)` };
  }
  const cells = Object.entries(r.cell_lines).filter(([, l]) => l === ln).map(([c]) => c);
  if (!cells.length) return null;
  return { scope: { cells }, lines: [ln, ln], label: `line ${ln} (${cells.length} cells)` };
}

export function regionForCell(r: ChipResult, cell: string) {
  const b = r.blocks.find((x) => x.cells.includes(cell));
  if (b) return regionForLine(r, b.lines[0]);
  const ln = r.cell_lines[cell];
  return ln ? regionForLine(r, ln) : { scope: { cells: [cell] }, lines: [0, 0] as [number, number], label: `cell ${cell}` };
}

export interface StartOpts {
  chip: string; scope: Scope; label: string; origin: Origin; line?: number; lines?: [number, number];
  suggestEdit?: boolean; verilog?: string;
  fresh?: boolean;   // skip the server's cache (Run again, or a region whose last run wasn't proven)
}

/** Start one region agent and stream it into the store. Resolves with the finished run. */
export async function runAgent(o: StartOpts): Promise<Run> {
  const key = `${o.chip}:${JSON.stringify(o.scope)}`;
  running.get(key)?.abort();
  const ctrl = new AbortController();
  running.set(key, ctrl);
  const run: Run = {
    id: uid(), chip: o.chip, scope: o.scope, label: o.label, origin: o.origin, line: o.line, lines: o.lines,
    status: "running", steps: [], attempts: [], cells: [], t0: Date.now(),
  };
  store.addRun(run);
  try {
    await explain(o.chip, o.scope, (e) => {
      if (e.kind === "tool") {
        run.steps.push({ tool: e.tool, args: e.args, cells: e.cells, lines: e.lines, wall_s: e.wall_s, is_error: e.is_error });
        if (!run.cells.length && e.cells?.length) run.cells = e.cells;
      } else if (e.kind === "claim") {
        run.attempts.push(e);
        if (e.cells?.length) run.cells = e.cells;
      } else if (e.kind === "done") {
        run.status = "done"; run.final = e.final; run.explained = e.explained; run.cached = e.cached; run.plain = e.plain ?? null; run.t1 = Date.now();
        if (e.cells?.length) run.cells = e.cells;
      } else if (e.kind === "error") {
        run.status = "error"; run.error = e.error; run.t1 = Date.now();
      }
      store.changed(o.chip);
    }, ctrl.signal, o.fresh);
    if (run.status === "running") { run.status = "error"; run.error = "The stream ended without a result."; }
  } catch (err) {
    run.status = "error"; run.error = (err as Error).name === "AbortError" ? "Cancelled." : (err as Error).message;
  } finally {
    running.delete(key);
    run.t1 ??= Date.now();
    store.changed(o.chip);
  }
  if (o.suggestEdit) suggestEditFrom(run, o.verilog ?? "");
  return run;
}

export function cancelAll() { for (const c of running.values()) c.abort(); }

/** A credited, proven claim becomes a suggested replacement for the lines it explains. */
export function suggestEditFrom(run: Run, verilog: string): boolean {
  const last = run.attempts[run.attempts.length - 1];
  if (!run.explained || !last?.claim || !run.lines || run.lines[0] < 1) return false;
  const all = verilog.split("\n");
  const original = all.slice(run.lines[0] - 1, run.lines[1]);
  store.addEdit({
    id: uid(), chip: run.chip, runId: run.id, lines: run.lines, original, replacement: claimToVerilog(last.claim, original),
    claim: last.claim, state: "suggested", cells: run.cells,
  });
  return true;
}

/** A comment on a line. With `ask`, a claim is formalised + proved; a request ("check the logic") is explained. */
const REQUEST = /\b(check|explain|what|how|why|is this|optimize|optimise|understand|review|verify|look at|tell me|describe|does this|can you|fix|improve)\b/i;

export async function comment(chip: string, line: number, text: string, ask: boolean, scope: Scope | null) {
  store.addComment({ id: uid(), chip, line, author: "you", text, ts: Date.now() });
  if (!ask) return;
  const sentence = text.replace(/@agent\b/gi, "").trim();

  // A request/question ("check the logic of this part") → explain the region, not prove a vacuous tautology.
  if (REQUEST.test(sentence)) {
    if (!scope) return;
    const reply = store.addComment({ id: uid(), chip, line, author: "agent", text: "Explaining this part and asking Yosys to check the explanation…", ts: Date.now(), pending: true });
    try {
      const run = await runAgent({ chip, scope, label: `comment on line ${line}`, origin: "comment", line });
      const last = run.attempts[run.attempts.length - 1];
      store.updateComment(reply.id, {
        pending: false,
        text: run.plain ? `${run.plain.headline}. ${run.plain.explanation}${run.plain.verdict === "proven" || run.plain.verdict === "inductive" ? "" : ` (${verdictWords(run.plain.verdict)})`}`
          : run.explained ? (last.english ?? last.claim ?? "") : verdictWords(run.final ?? "", true),
        claim: last.claim ?? undefined, english: last.english, verdict: run.final, counterexample: last.counterexample ?? null,
      });
    } catch (e) {
      store.updateComment(reply.id, { pending: false, text: (e as Error).message });
    }
    return;
  }

  const reply = store.addComment({ id: uid(), chip, line, author: "agent", text: "Reading your comment as a claim and asking Yosys…", ts: Date.now(), pending: true });
  try {
    const f = await formalise(chip, sentence, scope ?? {}, true);
    if (f.error) {
      store.updateComment(reply.id, { pending: false, text: `I couldn't turn that into a checkable line: ${f.error}` });
      return;
    }
    const v = f.verdict;
    const verdict = v?.truth ?? v?.verdict ?? "unchecked";
    const said = verdict === "proven" || verdict === "inductive" ? "You're right. Yosys proved it for every input."
      : verdict === "refuted" ? "That's not quite what the chip does. Here is an input where it behaves differently."
      : verdictWords(verdict);
    const reading = f.assumptions?.length ? ` I read it as: ${f.assumptions.join("; ")}.` : "";
    store.updateComment(reply.id, {
      pending: false, text: said + reading + (f.ambiguous && f.alternative ? ` (Another reading: ${f.alternative})` : ""),
      claim: f.claim, english: v?.english ?? f.english, verdict, counterexample: v?.counterexample ?? null,
    });
  } catch (e) {
    store.updateComment(reply.id, { pending: false, text: (e as Error).message });
  }
}
