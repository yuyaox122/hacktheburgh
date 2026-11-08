// Verification engine for the verify page, built to scale with gate count rather than with cells x patterns.
//
// 0. Sample: draw SAMPLES random input vectors (seeded, uniform over every input bit). Big chips have 2^n
//    inputs, far too many to try, so the page predicts from a sample instead of enumerating.
// 1. Bit-parallel simulation: every net holds W 32-bit words, one test vector per bit, so a single pass over
//    the netlist evaluates 32 vectors per word (1000 vectors = 32 words). Flops: one clock from reset.
// 2. Compare with the spec: per output bit, a mismatch mask. The wrong fraction predicts the failure rate
//    over the whole input space (Wilson 95% interval; rule of three when nothing fails).
// 3. Trace back (critical path tracing): one reverse pass per output pin. A gate passes observability to an
//    input where its Boolean difference is 1 (flipping that input flips the output). At fanout stems the
//    branches are OR-ed, which can over-approximate on reconvergent paths, so:
// 4. Confirm: the top suspects are re-simulated exactly with their output inverted (one more parallel pass each).
//
// Fault model (stated on screen): one cell computes the wrong value. A cell "explains" a wrong pattern when
// inverting its output there makes every output right. A cell that explains k of F wrong patterns gets
// likelihood (1-e)^k * e^(F-k) (e = 0.05), normalised over all candidate cells with a uniform prior.

import type { Design, Lib } from "../data/types";
import { parse, type Expr } from "../sim/expr";

const EPS = 0.05;
/** Random test vectors per run. The prediction's interval narrows with more, the cost grows linearly. */
export const SAMPLES = 1000;
const SEED = 0x5eed;
const CONFIRM = 5;
/** Cells that are wiring or clocking, not logic a bug could live in. */
const notCandidate = (t: string) => /^(conb|clkbuf|clkinv|decap|fill|tap)/.test(t);

type WordFn = (v: Int32Array, w: number) => number;
interface Gate {
  id: string; type: string; out: number; level: number;
  fn: WordFn;
  /** Per input net: Boolean difference (output flips when this input flips). */
  sens: { net: number; fn: WordFn }[];
  flop?: { state: number; d: WordFn; dNet: number };
}

export interface Suspect { id: string; type: string; explains: number; p: number; confirmed: boolean }
export interface Timing { simMs: number; traceMs: number; confirmMs: number }
export interface Result {
  /** Random vectors simulated, of `space` possible inputs. */
  patterns: number; space: number; words: number; wrong: number;
  /** Predicted failure rate over every input, with a 95% interval, and the failing inputs that implies. */
  rate: number; rateLo: number; rateHi: number; predictedWrong: number;
  /** Per pattern: true when every output matched the spec. */
  ok: boolean[];
  examples: { ins: Record<string, number>; got: number; want: number }[];
  suspects: Suspect[];
  /** Cells in display order (inputs to outputs) with their logic level. */
  levels: { id: string; level: number }[];
  maxLevel: number;
  /** Nets that carry a failure back towards its cause (any wrong pattern observable through them). */
  tracedNets: string[];
  /** Net values for one example pattern (wire glow). */
  sample: (net: string) => number;
  timing: Timing;
  gateEvals: number;
}

function wordJs(e: Expr, map: (name: string) => string): string {
  switch (e.op) {
    case "var": return map(e.name);
    case "const": return e.v ? "-1" : "0";
    case "not": return `(~${wordJs(e.a, map)})`;
    case "and": return `(${e.args.map((a) => wordJs(a, map)).join("&")})`;
    case "or": return `(${e.args.map((a) => wordJs(a, map)).join("|")})`;
    case "xor": return `(${e.args.map((a) => wordJs(a, map)).join("^")})`;
  }
}
const fnOf = (src: string) => new Function("v", "w", `return (${src})|0;`) as WordFn;

class Netlist {
  readonly nets = new Map<string, number>();
  readonly names: string[] = [];
  readonly gates: Gate[] = [];       // evaluation order (flops are sources)
  readonly W: number;
  constructor(readonly d: Design, lib: Lib, patterns: number) {
    this.W = Math.ceil(patterns / 32);
    const W = this.W;
    const net = (n: string) => { let i = this.nets.get(n); if (i === undefined) { i = this.names.length; this.nets.set(n, i); this.names.push(n); } return i; };
    const ref = (i: number) => `v[${i * W}+w]`;
    const raw: (Gate & { deps: number[] })[] = [];
    for (const c of d.cells) {
      const L = lib[c.type];
      if (!L || L.phys) continue;
      const outPin = Object.keys(L.function).find((p) => L.pins[p] === "out" && c.pins[p] !== undefined);
      if (!outPin) continue;
      const e = parse(L.function[outPin]);
      const stateNames = L.ff ? (L.ff.state ?? ["IQ", "IQ_N"]) : [];
      const st = L.ff ? net(`\u0000${c.id}:state`) : -1;
      const pinNet = (name: string) => (c.pins[name] === undefined ? net("1'b0") : net(c.pins[name]));
      const map = (over: Record<string, string> = {}) => (name: string) =>
        over[name] ?? (name === stateNames[0] ? ref(st) : name === stateNames[1] ? `(~${ref(st)})` : ref(pinNet(name)));
      const ins = Object.keys(c.pins).filter((p) => L.pins[p] === "in" && !L.ff);
      const g: Gate & { deps: number[] } = {
        id: c.id, type: c.type, out: net(c.pins[outPin]), level: 0,
        fn: fnOf(wordJs(e, map())),
        sens: ins.map((p) => ({ net: pinNet(p), fn: fnOf(`${wordJs(e, map({ [p]: "0" }))}^${wordJs(e, map({ [p]: "-1" }))}`) })),
        deps: L.ff ? [] : ins.map(pinNet),
      };
      if (L.ff) {
        const dPin = Object.keys(c.pins).find((p) => p === L.ff!.next_state) ?? "D";
        g.flop = { state: st, d: fnOf(wordJs(parse(L.ff.next_state), (n) => ref(pinNet(n)))), dNet: pinNet(dPin) };
      }
      raw.push(g);
    }
    // Topological order; flops are sources (their output is state).
    const byOut = new Map(raw.map((g) => [g.out, g]));
    const seen = new Map<Gate, number>();
    const visit = (g: Gate & { deps: number[] }) => {
      if (seen.get(g) !== undefined) return;
      seen.set(g, 1);
      for (const n of g.deps) { const p = byOut.get(n); if (p && !seen.has(p)) visit(p); }
      this.gates.push(g);
    };
    raw.forEach(visit);
    // Display level: longest path from the inputs, with each flop after its D logic.
    const lvl = new Map<number, number>();
    const levelOf = (g: Gate & { deps: number[] }, stack = new Set<Gate>()): number => {
      if (stack.has(g)) return 0;
      if (g.level) return g.level;
      stack.add(g);
      const deps = g.flop ? [g.flop.dNet] : g.deps;
      g.level = 1 + Math.max(0, ...deps.map((n) => { const p = byOut.get(n); return p ? levelOf(p, stack) : 0; }));
      stack.delete(g); lvl.set(g.out, g.level);
      return g.level;
    };
    raw.forEach((g) => levelOf(g));
    net("1'b0"); net("1'b1");
  }

  /** One clock from reset for every pattern at once; `flip` inverts that cell's output. */
  run(inputs: (v: Int32Array) => void, flip: string | null = null): Int32Array {
    const W = this.W, v = new Int32Array(this.names.length * W);
    const one = this.nets.get("1'b1")!;
    for (let w = 0; w < W; w++) v[one * W + w] = -1;
    inputs(v);
    const evalAll = () => { for (const g of this.gates) { const inv = g.id === flip ? -1 : 0; for (let w = 0; w < W; w++) v[g.out * W + w] = g.fn(v, w) ^ inv; } };
    evalAll();
    const flops = this.gates.filter((g) => g.flop);
    const next = flops.map((g) => { const a = new Int32Array(W); for (let w = 0; w < W; w++) a[w] = g.flop!.d(v, w); return a; });
    flops.forEach((g, k) => v.set(next[k], g.flop!.state * W));
    evalAll();
    return v;
  }
}

/** Number of possible inputs: 2^(named input bits). */
export const inputSpace = (d: Design) => 2 ** (d.io?.in ?? []).reduce((a, f) => a + f.bits.length, 0);

/** `n` random input vectors, uniform over every named input bit, reproducible from `seed` (mulberry32). */
export function sampleInputs(d: Design, n = SAMPLES, seed = SEED): Record<string, number>[] {
  let t = seed >>> 0;
  const rnd = () => { t = (t + 0x6d2b79f5) >>> 0; let x = Math.imul(t ^ (t >>> 15), 1 | t); x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x; return ((x ^ (x >>> 14)) >>> 0) / 4294967296; };
  const fs = d.io?.in ?? [];
  return Array.from({ length: n }, () => Object.fromEntries(fs.map((f) => [f.name, Math.floor(rnd() * 2 ** f.bits.length)])));
}

/** 95% interval for a rate from k of n: Wilson, or the rule of three (0 to 3/n) when k = 0. */
function interval(k: number, n: number): [number, number] {
  if (!k) return [0, Math.min(1, 3 / n)];
  const z = 1.96, p = k / n, d = 1 + (z * z) / n, c = (p + (z * z) / (2 * n)) / d, h = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / d;
  return [Math.max(0, c - h), Math.min(1, c + h)];
}

/** The spec: a + b + ci, as {s, co}. */
export function claSpec(ins: Record<string, number>): Record<string, number> {
  const t = (ins.a ?? 0) + (ins.b ?? 0) + (ins.ci ?? 0);
  return { s: t & 15, co: t >> 4 };
}

const popcount = (x: number) => { x -= (x >>> 1) & 0x55555555; x = (x & 0x33333333) + ((x >>> 2) & 0x33333333); return (((x + (x >>> 4)) & 0x0f0f0f0f) * 0x01010101) >>> 24; };

/** Every input combination (for small input spaces): a check over these is complete, not sampled. */
export function allInputs(d: Design): Record<string, number>[] {
  const fs = d.io?.in ?? [];
  const n = inputSpace(d);
  return Array.from({ length: n }, (_, k) => {
    let r = k;
    return Object.fromEntries(fs.map((f) => { const v = r & ((1 << f.bits.length) - 1); r >>>= f.bits.length; return [f.name, v]; }));
  });
}

export function verify(d: Design, lib: Lib, spec = claSpec, samples = SAMPLES, patterns?: Record<string, number>[]): Result {
  const pats = patterns ?? sampleInputs(d, samples);
  const P = pats.length;
  const N = new Netlist(d, lib, P), W = N.W;   // compiling the netlist is one-off set-up, not timed
  const inBits = (d.io?.in ?? []).flatMap((f) => f.bits.map((b, i) => ({ f: f.name, i, net: N.nets.get(b) })));
  const outBits = (d.io?.out ?? []).flatMap((f) => f.bits.map((b, i) => ({ f: f.name, i, net: N.nets.get(b) ?? -1 })));
  const ctrl = d.ports.filter((p) => p.dir === "in" && /^(rst_?n|ena?)$/i.test(p.name)).map((p) => N.nets.get(p.name)).filter((x): x is number => x !== undefined);
  const setInputs = (v: Int32Array) => {
    for (const n of ctrl) for (let w = 0; w < W; w++) v[n * W + w] = -1;
    pats.forEach((p, k) => { for (const b of inBits) if (b.net !== undefined && (p[b.f] >> b.i) & 1) v[b.net * W + (k >> 5)] |= 1 << (k & 31); });
  };
  // Expected output words from the spec.
  const want = outBits.map(() => new Int32Array(W));
  pats.forEach((p, k) => { const e = spec(p); outBits.forEach((o, j) => { if ((e[o.f] >> o.i) & 1) want[j][k >> 5] |= 1 << (k & 31); }); });
  const tail = P % 32 ? (1 << (P % 32)) - 1 : -1;
  const valid = (w: number) => (w === W - 1 ? tail : -1);

  const t0 = performance.now();
  const v = N.run(setInputs);
  const mm = outBits.map((o, j) => { const a = new Int32Array(W); for (let w = 0; w < W; w++) a[w] = (o.net < 0 ? 0 : v[o.net * W + w] ^ want[j][w]) & valid(w); return a; });
  const fail = new Int32Array(W);
  mm.forEach((a) => a.forEach((x, w) => (fail[w] |= x)));
  const simMs = performance.now() - t0;
  let wrong = 0; fail.forEach((x) => (wrong += popcount(x)));

  // Trace back: per output pin, observability of every net (reverse topological order).
  const t1 = performance.now();
  const cands = N.gates.filter((g) => !notCandidate(g.type));
  const explain = new Map(cands.map((g) => [g.id, new Int32Array(W).fill(-1)]));
  const traced = new Uint8Array(N.names.length);
  if (wrong) {
    outBits.forEach((o, j) => {
      if (o.net < 0) return;
      const obs = new Int32Array(N.names.length * W);
      for (let w = 0; w < W; w++) obs[o.net * W + w] = -1;
      for (let gi = N.gates.length - 1; gi >= 0; gi--) {
        const g = N.gates[gi];
        for (let w = 0; w < W; w++) {
          const ob = obs[g.out * W + w];
          if (!ob) continue;
          if (g.flop) obs[g.flop.dNet * W + w] |= ob;
          else for (const s of g.sens) obs[s.net * W + w] |= ob & s.fn(v, w);
        }
      }
      // Flops were evaluated as sources, so give their D logic a second sweep.
      for (let gi = N.gates.length - 1; gi >= 0; gi--) {
        const g = N.gates[gi];
        if (g.flop) continue;
        for (let w = 0; w < W; w++) { const ob = obs[g.out * W + w]; if (ob) for (const s of g.sens) obs[s.net * W + w] |= ob & s.fn(v, w); }
      }
      for (const g of cands) { const e = explain.get(g.id)!; for (let w = 0; w < W; w++) e[w] &= ~(obs[g.out * W + w] ^ mm[j][w]); }
      for (let n = 0; n < N.names.length; n++) for (let w = 0; w < W; w++) if (obs[n * W + w] & mm[j][w]) { traced[n] = 1; break; }
    });
  }
  const count = (id: string) => { const e = explain.get(id)!; let k = 0; for (let w = 0; w < W; w++) k += popcount(e[w] & fail[w]); return k; };
  const ks = new Map(cands.map((g) => [g.id, wrong ? count(g.id) : 0]));
  const traceMs = performance.now() - t1;

  // Confirm the top suspects: invert the cell's output and re-simulate the sampled vectors.
  const t2 = performance.now();
  const confirmed = new Set<string>();
  if (wrong) {
    const top = [...ks].sort((a, b) => b[1] - a[1]).slice(0, CONFIRM);
    for (const [id] of top) {
      const fv = N.run(setInputs, id);
      let k = 0;
      for (let w = 0; w < W; w++) {
        let okw = -1;
        outBits.forEach((o, j) => { if (o.net >= 0) okw &= ~(fv[o.net * W + w] ^ want[j][w]); });
        k += popcount(okw & fail[w]);
      }
      ks.set(id, k); confirmed.add(id);
    }
  }
  const confirmMs = performance.now() - t2;

  const logL = cands.map((g) => { const k = ks.get(g.id)!; return k * Math.log(1 - EPS) + (wrong - k) * Math.log(EPS); });
  const m = Math.max(...logL), wts = logL.map((x) => Math.exp(x - m)), z = wts.reduce((a, b) => a + b, 0);
  const suspects = cands.map((g, i) => ({ id: g.id, type: g.type, explains: ks.get(g.id)!, p: wrong ? wts[i] / z : 0, confirmed: confirmed.has(g.id) }))
    .sort((a, b) => b.p - a.p);

  const ok = pats.map((_, k) => !((fail[k >> 5] >>> (k & 31)) & 1));
  const val = (out: Int32Array | null, k: number) => outBits.reduce((acc, o, j) => acc | ((((out ? out[o.net * W + (k >> 5)] : want[j][k >> 5]) >>> (k & 31)) & 1) << j), 0);
  const examples: Result["examples"] = [], seenEx = new Set<string>();   // distinct inputs (random draws can repeat)
  for (let k = 0; k < P && examples.length < 3; k++) {
    const key = JSON.stringify(pats[k]);
    if (!ok[k] && !seenEx.has(key)) { seenEx.add(key); examples.push({ ins: pats[k], got: val(v, k), want: val(null, k) }); }
  }
  const sampleK = Math.max(0, ok.findIndex((x) => !x));
  const sample = (n: string) => { const i = N.nets.get(n); return i === undefined ? 0 : (v[i * W + (sampleK >> 5)] >>> (sampleK & 31)) & 1; };
  const levels = [...N.gates].sort((a, b) => a.level - b.level).map((g) => ({ id: g.id, level: g.level }));
  const space = inputSpace(d), [rateLo, rateHi] = interval(wrong, P);
  return {
    patterns: P, space, words: W, wrong, rate: wrong / P, rateLo, rateHi, predictedWrong: Math.round((wrong / P) * space), ok, examples, suspects, levels, maxLevel: Math.max(1, ...levels.map((l) => l.level)),
    tracedNets: N.names.filter((_, i) => traced[i]), sample,
    timing: { simMs, traceMs, confirmMs }, gateEvals: N.gates.length * W * 2,
  };
}
