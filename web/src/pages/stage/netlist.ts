// Who drives and who reads each net of a placed design, and the cones that follow from it (fan-in, fan-out).

import type { Design, DesignCell, Lib } from "../../api/types";

export class Connectivity {
  readonly byId = new Map<string, DesignCell>();
  private drivers = new Map<string, string>();
  private sinks = new Map<string, { id: string; pin: string }[]>();

  constructor(design: Design, private lib: Lib) {
    for (const c of design.cells) {
      this.byId.set(c.id, c);
      const L = lib[c.type];
      for (const [p, n] of Object.entries(c.pins)) {
        if (L?.pins[p] === "out") this.drivers.set(n, c.id);
        else { if (!this.sinks.has(n)) this.sinks.set(n, []); this.sinks.get(n)!.push({ id: c.id, pin: p }); }
      }
    }
  }

  /** The cell driving `net`, if any. */
  driver(net: string) { return this.drivers.get(net); }
  /** Every cell pin that reads `net`. */
  readers(net: string) { return this.sinks.get(net) ?? []; }

  /** A cell and the cells next to it: what drives its inputs and what reads its outputs. */
  neighbours(c: DesignCell): Set<string> {
    const nb = new Set([c.id]), L = this.lib[c.type];
    for (const [p, n] of Object.entries(c.pins)) {
      if (L?.pins[p] === "out") for (const s of this.readers(n)) nb.add(s.id);
      else { const d = this.driver(n); if (d) nb.add(d); }
    }
    return nb;
  }

  /** Every cell that can change `net`, back to the flip-flops (like Go to Definition). */
  fanIn(net: string): Set<string> {
    const out = new Set<string>(), stack = [net];
    while (stack.length) {
      const n = stack.pop()!, id = this.driver(n);
      if (!id || out.has(id)) continue;
      out.add(id);
      const c = this.byId.get(id)!, L = this.lib[c.type];
      if (L?.ff) continue;
      for (const [p, m] of Object.entries(c.pins)) if (L?.pins[p] === "in") stack.push(m);
    }
    return out;
  }

  /** Every cell `net` can reach before the next flip-flop (like Find Usages). */
  fanOut(net: string): Set<string> {
    const out = new Set<string>(), stack = [net];
    while (stack.length) {
      for (const s of this.readers(stack.pop()!)) {
        if (out.has(s.id)) continue;
        out.add(s.id);
        const c = this.byId.get(s.id)!, L = this.lib[c.type];
        if (L?.ff) continue;
        for (const [p, m] of Object.entries(c.pins)) if (L?.pins[p] === "out") stack.push(m);
      }
    }
    return out;
  }
}
