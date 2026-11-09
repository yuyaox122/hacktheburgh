// "+ Custom input" on the simulator bar: a name over any input bits, LSB first, saved per chip in this browser.

import type { IoField } from "../../api/types";

const KEY = (chip: string) => `basic.customInputs.${chip}`;

export function loadCustomInputs(chip: string): IoField[] {
  try { const v = JSON.parse(localStorage.getItem(KEY(chip)) ?? "[]"); return Array.isArray(v) ? v.filter((f) => f && typeof f.name === "string" && Array.isArray(f.bits)) : []; }
  catch { return []; }
}
export function saveCustomInputs(chip: string, fields: IoField[]) {
  try { localStorage.setItem(KEY(chip), JSON.stringify(fields)); } catch { /* private mode: custom inputs last for this visit only */ }
}

/**
 * Read a custom input: `spec` is "ui_in[2]", "ui_in[7:4]" or "ui_in[0], uio_in[3]". Returns the bits (LSB first), or
 * the sentence to show when it can't be added.
 */
export function parseCustomInput(name: string, spec: string, nets: Set<string>, taken: IoField[]): { bits: string[] } | { error: string } {
  const bits: string[] = [];
  for (const part of spec.split(",").map((x) => x.trim()).filter(Boolean)) {
    const m = /^(\w+)\[(\d+)(?::(\d+))?\]$/.exec(part);
    if (!m) return { error: `Can't read "${part}": use ui_in[2] or ui_in[7:4]` };
    const hi = +m[2], lo = m[3] !== undefined ? +m[3] : hi;
    for (let k = Math.min(lo, hi); k <= Math.max(lo, hi); k++) bits.push(`${m[1]}[${k}]`);
  }
  const missing = bits.filter((b) => !nets.has(b));
  if (!name || !bits.length) return { error: "Give it a name and at least one bit" };
  if (missing.length) return { error: `This chip's gates don't use ${missing.slice(0, 3).join(", ")}` };
  if (bits.length > 16) return { error: "At most 16 bits" };
  if (taken.some((f) => f.name === name)) return { error: `"${name}" already exists` };
  return { bits };
}
