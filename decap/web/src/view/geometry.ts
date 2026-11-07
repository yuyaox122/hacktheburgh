// Pure geometry shared by the 3D Stage and the headless checks (no DOM, no WebGL).

import type { Design, DesignCell } from "../data/types";

export const Y = { cellBot: 0.06, cellTop: 0.5, m1: 0.72, via: 0.84, m2: 0.96, via2: 1.1, m3: 1.24, via3: 1.4, m4: 1.56, m5: 1.9 };

/** Scene-space box of a logic cell body: scene x = DEF x, scene z = dieH - DEF y. */
export function cellBox(c: Pick<DesignCell, "x" | "y" | "w">, dieH: number, rowH: number) {
  const h = Y.cellTop - Y.cellBot;
  return { x: c.x + c.w / 2, y: Y.cellBot + h / 2, z: dieH - (c.y + rowH / 2), sx: c.w - 0.08, sy: h, sz: rowH - 0.5 };
}

/** Default overview camera direction (from the die centre). */
export const OVERVIEW_DIR = [0, 0.78, 0.62] as const;

/** A rectangle in DEF microns. */
export type Rect = { x0: number; y0: number; x1: number; y1: number };

/**
 * The part of the die this design actually uses: its logic cells, their wires and its pins, plus a small margin.
 * Every Tiny Tapeout 1x1 design gets the same fixed 161 x 111.5 um tile and the tools fill whatever the logic
 * doesn't need with filler cells, so drawing the whole tile makes every chip look the same size.
 */
export function usedArea(d: Pick<Design, "die" | "cells" | "routes" | "ports" | "rowHeight">, margin = 4): Rect {
  const r = { x0: Infinity, y0: Infinity, x1: -Infinity, y1: -Infinity };
  const take = (x: number, y: number) => { r.x0 = Math.min(r.x0, x); r.x1 = Math.max(r.x1, x); r.y0 = Math.min(r.y0, y); r.y1 = Math.max(r.y1, y); };
  for (const c of d.cells) { take(c.x, c.y); take(c.x + c.w, c.y + d.rowHeight); }
  if (!Number.isFinite(r.x0)) return { x0: 0, y0: 0, x1: d.die.w, y1: d.die.h };
  for (const rt of d.routes) for (const [x, y] of rt.pts) take(x, y);
  for (const p of d.ports) take(p.x, p.y);
  return {
    x0: Math.max(0, r.x0 - margin), y0: Math.max(0, r.y0 - margin),
    x1: Math.min(d.die.w, r.x1 + margin), y1: Math.min(d.die.h, r.y1 + margin),
  };
}
