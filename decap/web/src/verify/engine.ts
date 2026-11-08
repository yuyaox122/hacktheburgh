// Verification engine for the verify page: runs every input combination through the real gate netlist
// (the same zero-delay simulator as the Explore page), checks each answer against the spec, and, when answers
// are wrong, scores every logic cell as the fault's likely location.
//
// Fault model (stated on screen): one cell computes the wrong value. For each failing input we ask, cell by
// cell, "if this cell's output were the opposite here, would every output be right?". A cell that explains
// k of F failures gets likelihood (1-e)^k * e^(F-k) (e = 0.05); posteriors are normalised over all candidate
// cells with a uniform prior. Cells the pins can't tell apart share the probability.

import { Sim } from "../sim/sim";
import type { Design, Lib } from "../data/types";

export interface Vec { ins: Record<string, number>; expect: Record<string, number>; got: Record<string, number>; ok: boolean }
export interface Suspect { id: string; type: string; explains: number; p: number }

const EPS = 0.05;
const INV = "~inv";
/** Cells that are wiring or clocking, not logic a bug could live in. */
const notCandidate = (t: string) => /^(conb|clkbuf|clkinv|decap|fill|tap)/.test(t);

const fieldBits = (d: Design, dir: "in" | "out") => (d.io?.[dir] ?? []).map((f) => ({ name: f.name, bits: f.bits }));

/** The spec: a + b + ci, as {s, co}. */
export function claSpec(ins: Record<string, number>): Record<string, number> {
  const t = (ins.a ?? 0) + (ins.b ?? 0) + (ins.ci ?? 0);
  return { s: t & 15, co: t >> 4 };
}

function libWithInverses(lib: Lib): Lib {
  const out: Lib = { ...lib };
  for (const [t, L] of Object.entries(lib)) {
    if (L.phys) continue;
    out[t + INV] = { ...L, function: Object.fromEntries(Object.entries(L.function).map(([p, f]) => [p, `!(${f})`])) };
  }
  return out;
}

class Runner {
  private sim: Sim;
  private clk: string | null;
  constructor(private d: Design, cells: Design["cells"], lib: Lib) {
    this.sim = new Sim(cells, lib);
    const ins = d.ports.filter((p) => p.dir === "in").map((p) => p.name);
    this.clk = ins.find((n) => /^clk$/i.test(n)) ?? null;
    for (const n of new Set([...ins, ...this.sim.names])) if (/^(rst_?n|en|ena)$/i.test(n)) this.sim.set(n, 1);
    this.sim.settle();
  }
  run(ins: Record<string, number>): Record<string, number> {
    for (const f of fieldBits(this.d, "in")) f.bits.forEach((b, i) => this.sim.set(b, (ins[f.name] >> i) & 1));
    if (this.clk) this.sim.clock(this.clk); else this.sim.settle();
    return this.outputs();
  }
  get(net: string) { return this.sim.get(net); }
  private outputs(): Record<string, number> {
    const out: Record<string, number> = {};
    for (const f of fieldBits(this.d, "out")) out[f.name] = f.bits.reduce((a, b, i) => a | (this.sim.get(b) << i), 0);
    return out;
  }
}

/** Every combination of the chip's named inputs (a, b, ci: 512 for the CLA). */
export function allInputs(d: Design): Record<string, number>[] {
  const fs = fieldBits(d, "in");
  const total = fs.reduce((a, f) => a + f.bits.length, 0);
  const vecs: Record<string, number>[] = [];
  for (let x = 0; x < 1 << total; x++) {
    const v: Record<string, number> = {};
    let sh = 0;
    for (const f of fs) { v[f.name] = (x >> sh) & ((1 << f.bits.length) - 1); sh += f.bits.length; }
    vecs.push(v);
  }
  return vecs;
}

const same = (a: Record<string, number>, b: Record<string, number>) => Object.keys(b).every((k) => a[k] === b[k]);

export class Verifier {
  readonly vecs: Record<string, number>[];
  readonly candidates: Design["cells"];
  private chip: Runner;
  private lib2: Lib;
  private flipped = new Map<string, Runner>();
  readonly explains = new Map<string, number>();
  fails = 0;

  constructor(private d: Design, lib: Lib, private spec = claSpec) {
    this.vecs = allInputs(d);
    this.chip = new Runner(d, d.cells, lib);
    this.lib2 = libWithInverses(lib);
    this.candidates = d.cells.filter((c) => lib[c.type] && !lib[c.type].phys && !notCandidate(c.type));
    for (const c of this.candidates) this.explains.set(c.id, 0);
  }

  /** A net's value on the chip after the last checked input (drives the wire glow). */
  value(net: string) { return this.chip.get(net); }

  private flip(id: string): Runner {
    let r = this.flipped.get(id);
    if (!r) {
      r = new Runner(this.d, this.d.cells.map((c) => (c.id === id ? { ...c, type: c.type + INV } : c)), this.lib2);
      this.flipped.set(id, r);
    }
    return r;
  }

  /** Check one input; on a failure, return the cells that would explain it. */
  check(ins: Record<string, number>): { vec: Vec; hits: string[] } {
    const expect = this.spec(ins), got = this.chip.run(ins), ok = same(got, expect);
    const hits: string[] = [];
    if (!ok) {
      this.fails++;
      for (const c of this.candidates) if (same(this.flip(c.id).run(ins), expect)) { hits.push(c.id); this.explains.set(c.id, this.explains.get(c.id)! + 1); }
    }
    return { vec: { ins, expect, got, ok }, hits };
  }

  /** Posterior fault probability per candidate cell, highest first. */
  suspects(): Suspect[] {
    if (!this.fails) return this.candidates.map((c) => ({ id: c.id, type: c.type, explains: 0, p: 0 }));
    const F = this.fails;
    const logL = this.candidates.map((c) => { const k = this.explains.get(c.id)!; return k * Math.log(1 - EPS) + (F - k) * Math.log(EPS); });
    const m = Math.max(...logL);
    const w = logL.map((x) => Math.exp(x - m)), z = w.reduce((a, b) => a + b, 0);
    return this.candidates.map((c, i) => ({ id: c.id, type: c.type, explains: this.explains.get(c.id)!, p: w[i] / z }))
      .sort((a, b) => b.p - a.p);
  }
}

/** A copy of the design with one cell swapped for another gate type: the seeded bug for the demo. */
export function seedBug(d: Design, cellId: string, newType: string): Design {
  return { ...d, name: d.name + "__revB", title: (d.title ?? d.name) + " rev B", cells: d.cells.map((c) => (c.id === cellId ? { ...c, type: newType } : c)) };
}
