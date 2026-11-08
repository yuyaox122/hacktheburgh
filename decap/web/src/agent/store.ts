// What the Witness agent has done on each chip: runs (live and finished), suggested and accepted edits to the decompiled
// Verilog, and comment threads on lines. Kept in localStorage per browser (a hackathon demo, not shared state), and
// broadcast to whichever view is open so the Verilog pane and the 3D Stage repaint together.

import type { Attempt, Plain, Scope, ToolStep } from "./api";

export type Origin = "line" | "edit" | "block" | "stage" | "comment" | "palette";

export interface Run {
  id: string;
  chip: string;
  scope: Scope;
  label: string;            // what the person pointed at, e.g. "line 22 · block b3 (leftover logic, 35 cells)"
  origin: Origin;
  line?: number;            // anchor in the decompiled Verilog
  lines?: [number, number]; // the statement(s) the region covers
  status: "running" | "done" | "error";
  steps: ToolStep[];
  attempts: Attempt[];
  final?: string;
  explained?: boolean;
  plain?: Plain | null;
  note?: string;            // e.g. how the agent aimed itself ("b3 touches 4 registers; explaining shreg0")
  cells: string[];
  error?: string;
  cached?: boolean;
  t0: number;
  t1?: number;
}

export interface Edit {
  id: string;
  chip: string;
  runId: string;
  lines: [number, number];  // decompiled lines it replaces (1-based, inclusive)
  original: string[];
  replacement: string[];
  claim: string;
  state: "suggested" | "accepted" | "dismissed";
  cells: string[];
}

export interface Comment {
  id: string;
  chip: string;
  line: number;
  author: "you" | "agent";
  text: string;
  ts: number;
  claim?: string;
  english?: string | null;
  verdict?: string;
  counterexample?: Record<string, number> | null;
  pending?: boolean;
}

interface Saved { runs: Run[]; edits: Edit[]; comments: Comment[] }
const KEY = "decap.agent.v1";

function load(): Saved {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) ?? "null") as Saved | null;
    if (s && Array.isArray(s.runs)) {
      // a run that was streaming when the page closed never finished: say so instead of spinning forever
      for (const r of s.runs) if (r.status === "running") { r.status = "error"; r.error = "Interrupted (the page was reloaded)."; }
      return { runs: s.runs, edits: s.edits ?? [], comments: s.comments ?? [] };
    }
  } catch { /* private window or blocked storage: start empty */ }
  return { runs: [], edits: [], comments: [] };
}

class Store {
  private s: Saved = load();
  private subs = new Set<(chip: string) => void>();

  on(f: (chip: string) => void) { this.subs.add(f); return () => this.subs.delete(f); }
  changed(chip: string) {
    try { localStorage.setItem(KEY, JSON.stringify(this.s)); } catch { /* storage full or blocked: keep in memory */ }
    for (const f of this.subs) f(chip);
  }

  runs(chip?: string) { return this.s.runs.filter((r) => !chip || r.chip === chip); }
  run(id: string) { return this.s.runs.find((r) => r.id === id); }
  addRun(r: Run) { this.s.runs.unshift(r); this.s.runs = this.s.runs.slice(0, 60); this.changed(r.chip); }

  edits(chip: string) { return this.s.edits.filter((e) => e.chip === chip && e.state !== "dismissed"); }
  addEdit(e: Edit) {
    this.s.edits = this.s.edits.filter((x) => !(x.chip === e.chip && x.lines[0] === e.lines[0] && x.state === "suggested"));
    this.s.edits.push(e); this.changed(e.chip);
  }
  setEdit(id: string, state: Edit["state"]) {
    const e = this.s.edits.find((x) => x.id === id);
    if (!e) return;
    if (state === "accepted") for (const o of this.s.edits) if (o.chip === e.chip && o !== e && o.state === "accepted" && overlaps(o.lines, e.lines)) o.state = "dismissed";
    e.state = state; this.changed(e.chip);
  }

  comments(chip: string, line?: number) { return this.s.comments.filter((c) => c.chip === chip && (line == null || c.line === line)); }
  addComment(c: Comment) { this.s.comments.push(c); this.changed(c.chip); return c; }
  updateComment(id: string, patch: Partial<Comment>) {
    const c = this.s.comments.find((x) => x.id === id);
    if (c) { Object.assign(c, patch); this.changed(c.chip); }
  }

  /** Cell colours for the 3D Stage and the netlist graph: the latest finished run per region decides. */
  marks(chip: string): Map<string, string> {
    const m = new Map<string, string>();
    for (const r of [...this.runs(chip)].reverse()) {
      if (r.status !== "done" || !r.cells.length) continue;
      const col = r.explained ? "#4cc38a" : r.final === "refuted" ? "#ef6b73" : "#e9a23b";
      for (const c of r.cells) m.set(c, col);
    }
    for (const e of this.edits(chip)) if (e.state === "accepted") for (const c of e.cells) m.set(c, "#5ec8ff");
    return m;
  }

  clear(chip: string) {
    this.s.runs = this.s.runs.filter((r) => r.chip !== chip);
    this.s.edits = this.s.edits.filter((r) => r.chip !== chip);
    this.s.comments = this.s.comments.filter((r) => r.chip !== chip);
    this.changed(chip);
  }
}

const overlaps = (a: [number, number], b: [number, number]) => a[0] <= b[1] && b[0] <= a[1];
export const uid = () => Math.random().toString(36).slice(2, 10);
export const store = new Store();

/** Top-level `&&` conjuncts of a claim (respects parentheses and braces). */
export function conjuncts(claim: string): string[] {
  const out: string[] = [];
  let depth = 0, start = 0;
  for (let i = 0; i < claim.length; i++) {
    const ch = claim[i];
    if (ch === "(" || ch === "{" || ch === "[") depth++;
    else if (ch === ")" || ch === "}" || ch === "]") depth--;
    else if (depth === 0 && claim.startsWith("&&", i)) { out.push(claim.slice(start, i)); start = i + 2; i++; }
  }
  out.push(claim.slice(start));
  return out.map((s) => strip(s.trim())).filter(Boolean);
}
function strip(s: string): string {
  while (s.startsWith("(") && s.endsWith(")") && balanced(s.slice(1, -1))) s = s.slice(1, -1).trim();
  return s;
}
function balanced(s: string): boolean {
  let d = 0;
  for (const ch of s) { if (ch === "(") d++; else if (ch === ")") { d--; if (d < 0) return false; } }
  return d === 0;
}
/** Split `lhs == rhs` at the top-level `==`. */
function equation(c: string): [string, string] | null {
  let depth = 0;
  for (let i = 0; i < c.length - 1; i++) {
    const ch = c[i];
    if ("({[".includes(ch)) depth++;
    else if (")}]".includes(ch)) depth--;
    else if (depth === 0 && c.startsWith("==", i) && c[i - 1] !== "!" && c[i - 1] !== "=" && c[i + 2] !== "=") {
      return [c.slice(0, i).trim(), c.slice(i + 2).trim()];
    }
  }
  return null;
}

/**
 * A proven, complete claim rewritten as Verilog for the region it explains. `next(r) == e` becomes the register's
 * update (its reset branch is kept from the decompiled lines); `port == e` becomes an assign when the original lines
 * drive that port, otherwise a proven note. Anything else stays as a `// proven:` comment.
 */
export function claimToVerilog(claim: string, original: string[]): string[] {
  const indent = (original.find((l) => l.trim()) ?? "    ").match(/^\s*/)![0] || "    ";
  const out: string[] = [`${indent}// Witness agent: proven by Yosys (complete for this region, rst_n = ena = 1)`];
  const text = original.join("\n");
  for (const c of conjuncts(claim)) {
    const eq = equation(c);
    const nx = eq?.[0].match(/^next\((\w+)\)$/);
    if (eq && nx) {
      const r = nx[1];
      const rst = text.match(new RegExp(`if\\s*\\(\\s*!rst_n\\s*\\)\\s*${r}\\s*<=\\s*([^;]+);`));
      const head = text.match(/always\s*@\s*\([^)]*\)/);
      if (head && rst) {
        out.push(`${indent}${head[0]}`, `${indent}    if (!rst_n) ${r} <= ${rst[1].trim()};`, `${indent}    else ${r} <= ${eq[1]};`);
      } else out.push(`${indent}// also proven: each clock ${r} <= ${eq[1]}  (its always block is on another line)`);
    } else if (eq && /^[A-Za-z_]\w*(\[\d+(:\d+)?\])?$/.test(eq[0])) {
      const port = eq[0].split("[")[0];
      if (new RegExp(`assign\\s+${port}\\b`).test(text)) out.push(`${indent}assign ${eq[0]} = ${eq[1]};`);
      else out.push(`${indent}// and ${eq[0]} == ${eq[1]} (proven; driven elsewhere)`);
    } else out.push(`${indent}// proven: ${c}`);
  }
  return out;
}
