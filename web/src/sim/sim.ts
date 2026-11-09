// Levelised, zero-delay gate-level simulator driven by lib.json (Liberty functions).
//
// - Every cell output is a compiled boolean function of its input nets.
// - Flip-flops keep a Liberty state variable (IQ, IQ_N); their outputs are functions of that state.
// - settle(): evaluate the combinational logic in topological order, apply async clear/preset, then latch D
//   into every flop whose clock expression rose (all at once, using pre-edge values), and repeat until stable.
//   This also handles flops clocked by other flops (ripple dividers).
// - Combinational loops (rare) are iterated to a fixpoint, max 32 passes.

import type { Lib, LibCell } from "../data/types";
import { parse, toJs, vars, type Expr } from "./expr";

export interface SimCell {
  id: string;
  type: string;
  pins: Record<string, string>;
}

type Fn = (v: Uint8Array) => number;

interface Unit {
  cell: SimCell;
  outs: { slot: number; fn: Fn }[];
  deps: number[];
}
interface Flop {
  cell: SimCell;
  iq: number; // state slot
  iqn: number; // inverted state slot
  clk: Fn;
  d: Fn;
  clr: Fn | null;
  pre: Fn | null;
  both: number; // value when clear and preset are both active
  prevClk: number;
}

const exprCache = new Map<string, Expr>();
function expr(s: string): Expr {
  let e = exprCache.get(s);
  if (!e) { e = parse(s); exprCache.set(s, e); }
  return e;
}

export const isConstNet = (n: string) => /^(1'b[01]|1'h[01]|[01])$/.test(n);
const constVal = (n: string) => (/1$/.test(n) ? 1 : 0);

export class Sim {
  readonly slots = new Map<string, number>();
  readonly names: string[] = [];
  values: Uint8Array = new Uint8Array(0);
  readonly drivers = new Map<string, string>(); // net -> cell id
  readonly sinks = new Map<string, { id: string; pin: string }[]>();
  readonly cellById = new Map<string, SimCell>();
  readonly unknownTypes = new Set<string>();
  readonly flops: Flop[] = [];
  readonly order: Unit[] = [];
  readonly loops: Unit[] = [];
  readonly inputs: string[] = [];
  cycles = 0;
  toggles: Uint32Array = new Uint32Array(0);
  private constSlots: [number, number][] = [];

  constructor(cells: SimCell[], readonly lib: Lib, primaryInputs: string[] = []) {
    const units: Unit[] = [];
    for (const c of cells) {
      const L = lib[c.type];
      if (!L) { this.unknownTypes.add(c.type); continue; }
      if (L.phys) continue;
      this.cellById.set(c.id, c);
      for (const [pin, net] of Object.entries(c.pins)) {
        const dir = L.pins[pin];
        if (!dir) continue;
        if (dir === "out") this.drivers.set(net, c.id);
        else {
          if (!this.sinks.has(net)) this.sinks.set(net, []);
          this.sinks.get(net)!.push({ id: c.id, pin });
        }
        this.slot(net);
      }
      units.push(this.compileCell(c, L));
    }
    for (const n of primaryInputs) this.slot(n);
    this.values = new Uint8Array(this.names.length);
    this.toggles = new Uint32Array(this.names.length);
    for (const [n, s] of this.slots) if (isConstNet(n)) this.constSlots.push([s, constVal(n)]);
    for (const [s, v] of this.constSlots) this.values[s] = v;
    for (const f of this.flops) this.values[f.iqn] = 1; // power-on state: IQ = 0
    // Primary inputs = nets with no driver that are not constants or flop state.
    for (const n of this.names) if (!this.drivers.has(n) && !isConstNet(n) && !n.startsWith("\u0000")) this.inputs.push(n);
    this.levelise(units);
    this.evalComb();
    for (const f of this.flops) f.prevClk = f.clk(this.values);
    this.settle();
  }

  slot(name: string): number {
    let s = this.slots.get(name);
    if (s === undefined) { s = this.names.length; this.slots.set(name, s); this.names.push(name); }
    return s;
  }

  private compile(fnStr: string, map: (name: string) => number): Fn {
    const src = toJs(expr(fnStr), map);
    return new Function("v", `return ${src};`) as Fn;
  }

  private compileCell(c: SimCell, L: LibCell): Unit {
    let stateMap: Record<string, number> = {};
    if (L.ff) {
      const [sv = "IQ", svn = "IQ_N"] = L.ff.state ?? [];
      const iq = this.slot(`\u0000${c.id}:${sv}`), iqn = this.slot(`\u0000${c.id}:${svn}`);
      stateMap = { [sv]: iq, [svn]: iqn };
      const pinSlot = (name: string) => {
        const net = c.pins[name];
        return net === undefined ? this.slot("1'b0") : this.slot(net); // unconnected input = 0
      };
      const both = L.ff.clear_preset_var1 === "H" ? 1 : 0;
      this.flops.push({
        cell: c, iq, iqn,
        clk: this.compile(L.ff.clocked_on, pinSlot),
        d: this.compile(L.ff.next_state, pinSlot),
        clr: L.ff.clear ? this.compile(L.ff.clear, pinSlot) : null,
        pre: L.ff.preset ? this.compile(L.ff.preset, pinSlot) : null,
        both, prevClk: 0,
      });
    }
    const outs: Unit["outs"] = [];
    const deps = new Set<number>();
    for (const [pin, f] of Object.entries(L.function)) {
      const net = c.pins[pin];
      if (net === undefined || L.pins[pin] !== "out") continue;
      const map = (name: string) => {
        if (name in stateMap) return stateMap[name];
        const n = c.pins[name];
        return n === undefined ? this.slot("1'b0") : this.slot(n);
      };
      for (const vname of vars(expr(f))) deps.add(map(vname));
      outs.push({ slot: this.slot(net), fn: this.compile(f, map) });
    }
    return { cell: c, outs, deps: [...deps] };
  }

  private levelise(units: Unit[]) {
    const driverUnit = new Map<number, Unit>();
    for (const u of units) for (const o of u.outs) driverUnit.set(o.slot, u);
    const state = new Map<Unit, number>(); // 0 new, 1 visiting, 2 done
    const inLoop = new Set<Unit>();
    const visit = (u: Unit, stack: Unit[]) => {
      const st = state.get(u) ?? 0;
      if (st === 2) return;
      if (st === 1) { for (let k = stack.lastIndexOf(u); k < stack.length; k++) inLoop.add(stack[k]); return; }
      state.set(u, 1); stack.push(u);
      for (const d of u.deps) { const p = driverUnit.get(d); if (p) visit(p, stack); }
      stack.pop(); state.set(u, 2);
      this.order.push(u);
    };
    for (const u of units) visit(u, []);
    for (const u of inLoop) this.loops.push(u);
  }

  private evalComb() {
    const v = this.values;
    for (const u of this.order) for (const o of u.outs) v[o.slot] = o.fn(v);
    if (this.loops.length) {
      for (let pass = 0; pass < 32; pass++) {
        let changed = false;
        for (const u of this.loops) for (const o of u.outs) { const x = o.fn(v); if (v[o.slot] !== x) { v[o.slot] = x; changed = true; } }
        if (!changed) break;
        for (const u of this.order) for (const o of u.outs) v[o.slot] = o.fn(v);
      }
    }
  }

  private setState(f: Flop, x: number): boolean {
    if (this.values[f.iq] === x) return false;
    this.values[f.iq] = x; this.values[f.iqn] = x ^ 1;
    return true;
  }

  /** Propagate until nothing changes: comb logic, async clear/preset, clock edges. */
  settle(): void {
    const v = this.values;
    for (let round = 0; round < 64; round++) {
      this.evalComb();
      let changed = false;
      const rising: Flop[] = [];
      for (const f of this.flops) {
        const c = f.clk(v);
        if (c === 1 && f.prevClk === 0) rising.push(f);
        f.prevClk = c;
      }
      const dval = rising.map((f) => f.d(v));
      rising.forEach((f, k) => {
        const clr = f.clr ? f.clr(v) : 0, pre = f.pre ? f.pre(v) : 0;
        if (!clr && !pre && this.setState(f, dval[k])) changed = true;
      });
      for (const f of this.flops) {
        const clr = f.clr ? f.clr(v) : 0, pre = f.pre ? f.pre(v) : 0;
        if (clr || pre) if (this.setState(f, clr && pre ? f.both : clr ? 0 : 1)) changed = true;
      }
      if (!changed) { this.evalComb(); return; }
    }
    this.evalComb();
  }

  get(net: string): number {
    const s = this.slots.get(net);
    return s === undefined ? 0 : this.values[s];
  }
  set(net: string, x: number): void {
    const s = this.slots.get(net);
    if (s !== undefined) this.values[s] = x ? 1 : 0;
  }
  /** Set several inputs, then settle once. */
  apply(inputs: Record<string, number>): void {
    for (const [k, x] of Object.entries(inputs)) this.set(k, x);
    this.settle();
  }
  /** One full clock cycle on `clk`: rise, settle, fall, settle. Counts toggles for the activity view. */
  clock(clk: string, inputs: Record<string, number> = {}): void {
    const before = this.values.slice();
    this.apply({ ...inputs, [clk]: 1 });
    this.apply({ [clk]: 0 });
    for (let i = 0; i < before.length; i++) if (before[i] !== this.values[i]) this.toggles[i]++;
    this.cycles++;
  }
  /** Reset every flop to 0 and re-settle (used by the UI "power on"). */
  zero(): void {
    for (const f of this.flops) this.setState(f, 0);
    this.settle();
  }
  flopState(id: string): number | null {
    const f = this.flops.find((x) => x.cell.id === id);
    return f ? this.values[f.iq] : null;
  }
}
