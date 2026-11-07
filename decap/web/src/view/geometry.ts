// Pure geometry shared by the 3D Stage and the headless checks (no DOM, no WebGL).

import type { DesignCell } from "../data/types";

export const Y = { cellBot: 0.06, cellTop: 0.5, m1: 0.72, via: 0.84, m2: 0.96, via2: 1.1, m3: 1.24, via3: 1.4, m4: 1.56, m5: 1.9 };

/** Scene-space box of a logic cell body: scene x = DEF x, scene z = dieH - DEF y. */
export function cellBox(c: Pick<DesignCell, "x" | "y" | "w">, dieH: number, rowH: number) {
  const h = Y.cellTop - Y.cellBot;
  return { x: c.x + c.w / 2, y: Y.cellBot + h / 2, z: dieH - (c.y + rowH / 2), sx: c.w - 0.08, sy: h, sz: rowH - 0.5 };
}

/** Default overview camera direction (from the die centre). */
export const OVERVIEW_DIR = [0, 0.78, 0.62] as const;
