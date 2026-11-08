// "Fix with the agent" on the Verify page: from the ranked suspects to a third revision ("rev B fix") that passes.
//
// 1. Search (real computation, in the browser): for the top suspects, try every library cell with the same pins and a
//    different function (one drive strength each), re-run the engine on the same random vectors, keep 0-wrong ones.
// 2. Confirm: the survivor is checked on EVERY input combination (512 for a, b, ci): complete for this spec, not a sample.
// 3. Explain: the agent (decap serve, POST /witness/explain-fix) says what the cell does in the decompiled code and
//    why the new gate restores the spec. The verdict is the engine's and the exhaustive check's, never the model's.

import type { Design, Lib } from "../data/types";
import { allInputs, claSpec, verify, type Suspect } from "./engine";

export interface Try { cell: string; to: string; wrong: number }
export interface Fix {
  cell: string; from: string; to: string;
  tried: Try[];
  sampled: { n: number; wrong: number };
  exhaustive: { n: number; wrong: number };
}

/** Library types that could replace `type`: same pins and directions, a different function, the _1 drive only. */
export function alternatives(lib: Lib, type: string): string[] {
  const me = lib[type];
  if (!me) return [];
  const pins = JSON.stringify(Object.entries(me.pins).sort());
  const fn = JSON.stringify(me.function);
  const seen = new Set<string>();
  return Object.keys(lib).filter((t) => {
    const c = lib[t];
    if (!c || c.ff || !/_1$/.test(t) || JSON.stringify(Object.entries(c.pins).sort()) !== pins) return false;
    const f = JSON.stringify(c.function);
    if (f === fn || seen.has(f)) return false;
    seen.add(f);
    return true;
  });
}

/** Try single-cell replacements on the top suspects; return the first that makes every sampled vector right and every
 *  input right, with everything tried (for the page to show). Leaves the design as it found it. */
export function findFix(d: Design, lib: Lib, suspects: Suspect[], top = 3, spec = claSpec): Fix | { tried: Try[] } {
  const tried: Try[] = [];
  const pool = suspects.filter((s) => s.p > 1e-3).slice(0, top);
  for (const s of pool) {
    const cell = d.cells.find((c) => c.id === s.id);
    if (!cell) continue;
    const from = cell.type;
    for (const to of alternatives(lib, from)) {
      cell.type = to;
      try {
        const r = verify(d, lib, spec);
        tried.push({ cell: s.id, to, wrong: r.wrong });
        if (r.wrong === 0) {
          const all = verify(d, lib, spec, 0, allInputs(d));
          if (all.wrong === 0) {
            return { cell: s.id, from, to, tried, sampled: { n: r.patterns, wrong: 0 }, exhaustive: { n: all.patterns, wrong: 0 } };
          }
        }
      } finally {
        cell.type = from;
      }
    }
  }
  return { tried };
}

/** The agent's plain-English account of the fix (null if the agent service isn't running). */
export async function explainFix(chip: string, fix: Fix, wrongBefore: number, lib: Lib): Promise<string | null> {
  try {
    const res = await fetch("/witness/explain-fix", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chip, cell: fix.cell, from_type: fix.from, to_type: fix.to,
        from_function: lib[fix.from]?.function, to_function: lib[fix.to]?.function,
        wrong_before: wrongBefore, sampled: fix.sampled, exhaustive: fix.exhaustive }),
    });
    if (!res.ok) return null;
    return (await res.json()).text ?? null;
  } catch { return null; }
}
