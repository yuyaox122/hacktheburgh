// Pure line bookkeeping for "Ideas" edits (no DOM, tested by a node script): kept edits are stored against the
// decompiled lines as bASIC wrote them ("original" numbers, which never change), while the agent sees and answers in
// the current text (kept edits applied). These helpers apply the edits and map line numbers between the two.

export type Range = [number, number];   // 1-based, inclusive
export interface Patch { lines: Range; replacement: string[] }   // `lines` in original numbers

export const overlaps = (a: Range, b: Range) => a[0] <= b[1] && b[0] <= a[1];

/** The first patch in `patches` that overlaps `r`, if any. */
export const clash = (r: Range, patches: Patch[]) => patches.find((p) => overlaps(p.lines, r)) ?? null;

export interface Applied {
  lines: string[];                       // the current text, one string per line
  /** Original line -> its current range (an edited line maps to its edit's whole replacement; empty when deleted). */
  toCur(ln: number): Range;
  /** Current range -> original range, or null when any of its lines comes from a kept edit. */
  toOrig(r: Range): Range | null;
}

/** Apply non-overlapping patches to the original lines. Throws if two patches overlap or one is out of range. */
export function applyPatches(original: string[], patches: Patch[]): Applied {
  const ps = [...patches].sort((a, b) => a.lines[0] - b.lines[0]);
  for (let i = 0; i < ps.length; i++) {
    const [a, b] = ps[i].lines;
    if (a < 1 || b < a || b > original.length) throw new Error(`Edit lines ${a}–${b} are outside the file (1–${original.length}).`);
    if (i && overlaps(ps[i - 1].lines, ps[i].lines)) throw new Error(`Edits at lines ${ps[i - 1].lines.join("–")} and ${a}–${b} overlap.`);
  }
  const lines: string[] = [];
  const origOf: (number | null)[] = [];   // current line (0-based) -> original line, null if it came from an edit
  const cur = new Map<number, Range>();
  let o = 1;
  for (const p of ps) {
    for (; o < p.lines[0]; o++) { lines.push(original[o - 1]); origOf.push(o); cur.set(o, [lines.length, lines.length]); }
    const start = lines.length + 1;
    for (const t of p.replacement) { lines.push(t); origOf.push(null); }
    for (; o <= p.lines[1]; o++) cur.set(o, [start, lines.length]);
  }
  for (; o <= original.length; o++) { lines.push(original[o - 1]); origOf.push(o); cur.set(o, [lines.length, lines.length]); }
  return {
    lines,
    toCur: (ln) => cur.get(Math.min(Math.max(ln, 1), original.length)) ?? [1, 0],
    toOrig: ([a, b]) => {
      if (a < 1 || b < a || b > lines.length) return null;
      for (let i = a; i <= b; i++) if (origOf[i - 1] == null) return null;
      return [origOf[a - 1]!, origOf[b - 1]!];
    },
  };
}

/** An original range (e.g. a block's lines) as a current range, never empty and inside the current text. */
export function curRange(ap: Applied, r: Range): Range {
  const n = Math.max(ap.lines.length, 1);
  let a = ap.toCur(r[0])[0], b = ap.toCur(r[1])[1];
  a = Math.min(Math.max(a, 1), n); b = Math.min(Math.max(b, a), n);
  return [a, b];
}

/**
 * Where an idea (answered in current numbers) lands in original numbers, or why it can't be applied.
 * `kept` are the edits already in the text; `pending` an applied edit still waiting for Keep / Undo.
 */
export function placeIdea(ap: Applied, lines: Range, kept: Patch[]): { orig: Range } | { error: string } {
  const orig = ap.toOrig(lines);
  if (!orig) return { error: "This idea rewrites lines you already changed. Undo that edit first, then ask again." };
  const c = clash(orig, kept);
  if (c) return { error: `This idea overlaps the edit you kept on lines ${c.lines[0]}–${c.lines[1]}. Undo that edit first.` };
  return { orig };
}

/** The full edited module: the original text with every patch applied (line endings kept as "\n"). */
export const editedText = (original: string, patches: Patch[]) => applyPatches(original.split("\n"), patches).lines.join("\n");
