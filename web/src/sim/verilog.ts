// Minimal structural-Verilog netlist reader (flat SKY130 HD netlists: Yosys/OpenROAD output, TT08 shuttles).

import type { SimCell } from "./sim";

export interface ParsedNetlist {
  name: string;
  ports: Record<string, { dir: "in" | "out" | "inout"; width: number; msb: number; lsb: number }>;
  cells: SimCell[];
  assigns: [string, string][];
}

const PREFIX = "sky130_fd_sc_hd__";
const clean = (s: string) => s.replace(/\\/g, "").trim();

export function parseNetlist(text: string, top?: string): ParsedNetlist {
  text = text.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
  const mods = [...text.matchAll(/\bmodule\s+([A-Za-z_][\w$]*)([\s\S]*?)\bendmodule\b/g)];
  const m = (top ? mods.find((x) => x[1] === top) : mods.find((x) => x[2].includes(PREFIX))) ?? mods[0];
  if (!m) throw new Error("No module found");
  const body = m[2];
  const ports: ParsedNetlist["ports"] = {};
  for (const d of body.matchAll(/\b(input|output|inout)\s+(?:wire\s+|reg\s+)?(?:\[(\d+)\s*:\s*(\d+)\]\s*)?([^;()]+?)\s*[;)]/g)) {
    const dir = d[1] === "input" ? "in" : d[1] === "output" ? "out" : "inout";
    const msb = d[2] ? +d[2] : 0, lsb = d[3] ? +d[3] : 0;
    for (const n of d[4].split(",")) {
      const name = clean(n);
      if (/^[A-Za-z_][\w$]*$/.test(name)) ports[name] = { dir, width: Math.abs(msb - lsb) + 1, msb, lsb };
    }
  }
  const cells: SimCell[] = [];
  const re = new RegExp(`\\b${PREFIX}([a-z0-9_]+)\\s+(\\\\\\S+\\s|[A-Za-z_][\\w$]*)\\s*\\(([\\s\\S]*?)\\)\\s*;`, "g");
  for (const c of body.matchAll(re)) {
    const pins: Record<string, string> = {};
    for (const p of c[3].matchAll(/\.([A-Za-z0-9_]+)\s*\(([^()]*)\)/g)) {
      const net = clean(p[2]);
      if (net) pins[p[1]] = net;
    }
    cells.push({ id: clean(c[2]), type: c[1], pins });
  }
  const assigns: [string, string][] = [];
  for (const a of body.matchAll(/\bassign\s+([^=;]+?)\s*=\s*([^;]+?)\s*;/g)) assigns.push([clean(a[1]), clean(a[2])]);
  return { name: m[1], ports, cells, assigns };
}

/** Every bit of a port, as written in netlists: "ui_in[3]" for buses, "clk" for scalars. */
export function portBits(name: string, p: { width: number; msb?: number; lsb?: number }): string[] {
  if (p.width <= 1 && (p.msb ?? 0) === 0 && (p.lsb ?? 0) === 0) return [name];
  const lo = Math.min(p.msb ?? p.width - 1, p.lsb ?? 0);
  return Array.from({ length: p.width }, (_, i) => `${name}[${lo + i}]`);
}
