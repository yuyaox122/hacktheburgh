// Golden check (SPEC §6.4, P0 gate): the browser simulator (src/sim) vs iverilog on the same netlist.
//
// iverilog side: SKY130 HD functional models generated here straight from the per-cell Liberty JSON in
// data/pdk/ (independent of ingest/liberty.py and lib.json).
// TS side: src/sim/sim.ts driven by web/public/data/lib.json (what the app loads).
// Both start with every flip-flop at 0 and see the same seeded random stimulus. Each cycle we compare, before
// and after the rising clock edge, every output port bit and every flip-flop output.
//
// Usage: npm run golden [-- --cycles 2000 --tt 500 --seed 7]
//   Warm-up: must match on every sample (exit 1 otherwise). TT08 netlists in data/tt08/: reported too.

import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { Lib } from "../src/data/types";
import { parse, toVerilog } from "../src/sim/expr";
import { Sim } from "../src/sim/sim";
import { parseNetlist, portBits, type ParsedNetlist } from "../src/sim/verilog";

const HERE = dirname(fileURLToPath(import.meta.url));
const DECAP = resolve(HERE, "../..");
const PDK = join(DECAP, "data/pdk");
const args = process.argv.slice(2);
const opt = (k: string, d: number) => { const i = args.indexOf(`--${k}`); return i >= 0 ? +args[i + 1] : d; };
const CYCLES = opt("cycles", 2000), TT_CYCLES = opt("tt", 500), SEED = opt("seed", 20261107);

const libPath = join(DECAP, "web/public/data/lib.json");
if (!existsSync(libPath)) { console.error("Missing public/data/lib.json: run `npm run stage-data` first."); process.exit(2); }
const lib: Lib = JSON.parse(readFileSync(libPath, "utf8"));

function rng(seed: number) {
  let s = seed >>> 0;
  return () => { s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; };
}

const PG = ["VGND", "VNB", "VPB", "VPWR"];
function cellModel(t: string): string {
  const f = join(PDK, `${t}.json`);
  if (!existsSync(f)) return `module sky130_fd_sc_hd__${t}(input VGND, input VNB, input VPB, input VPWR, input DIODE);\nendmodule\n`;
  const d = JSON.parse(readFileSync(f, "utf8"));
  const ins: string[] = [], outs: [string, string][] = [];
  for (const [k, v] of Object.entries<any>(d)) {
    if (!k.startsWith("pin,")) continue;
    const p = k.slice(4);
    if (v.direction === "input") ins.push(p);
    else if (v.direction === "output") outs.push([p, v.function]);
  }
  const ports = [...[...ins, ...PG].map((p) => `input ${p}`), ...outs.map(([p]) => `output ${p}`)];
  const body: string[] = [];
  const ffKey = Object.keys(d).find((k) => k.startsWith("ff,"));
  if (ffKey) {
    const g = d[ffKey], [, iq, iqn] = ffKey.split(",");
    const v = (s: string) => toVerilog(parse(s));
    body.push(`  reg ${iq}; initial ${iq} = 1'b0;`, `  wire ${iqn} = ~${iq};`);
    body.push(`  wire clk_ = ${v(g.clocked_on)};`, `  wire clr_ = ${g.clear ? v(g.clear) : "1'b0"};`, `  wire pre_ = ${g.preset ? v(g.preset) : "1'b0"};`);
    body.push(`  always @(posedge clk_ or posedge clr_ or posedge pre_) if (clr_) ${iq} <= 1'b0; else if (pre_) ${iq} <= 1'b1; else ${iq} <= ${v(g.next_state)};`);
  }
  for (const [p, fn] of outs) body.push(`  assign ${p} = ${toVerilog(parse(fn))};`);
  return `module sky130_fd_sc_hd__${t}(${ports.join(", ")});\n${body.join("\n")}\nendmodule\n`;
}

const vname = (n: string) => (/^[A-Za-z_][\w$]*$/.test(n) ? n : `\\${n} `);

interface Result { name: string; samples: number; mismatches: number; first?: string; signals: number; cells: number; flops: number; skip?: string }

function runOne(name: string, net: ParsedNetlist, netlistFile: string, cycles: number, seed: number): Result {
  const r = rng(seed);
  const inPorts = Object.entries(net.ports).filter(([n, p]) => p.dir === "in" && !["VPWR", "VGND"].includes(n));
  const outPorts = Object.entries(net.ports).filter(([, p]) => p.dir === "out");
  const clk = inPorts.find(([n]) => /^clk$/i.test(n))?.[0] ?? null;
  const foreign = [...new Set([...readFileSync(netlistFile, "utf8").matchAll(/^\s*([A-Za-z_]\w*)\s+\\?\S+\s*\(/gm)].map((m) => m[1]))]
    .filter((t) => !t.startsWith("sky130_") && !["module", "input", "output", "wire", "assign", "inout", "reg"].includes(t));
  const sim = new Sim(net.cells, lib);
  const base = { name, signals: 0, samples: 0, mismatches: 0, cells: sim.cellById.size, flops: sim.flops.length };
  if (foreign.length) return { ...base, skip: `not a flat netlist (instantiates ${foreign.slice(0, 2).join(", ")}${foreign.length > 2 ? ", …" : ""})` };
  if (sim.loops.length) return { ...base, skip: `combinational loop through ${sim.loops.length} cells (a ring oscillator: zero-delay simulators cannot agree on it)` };
  if (sim.unknownTypes.size) throw new Error(`${name}: unknown cell types ${[...sim.unknownTypes].join(", ")}`);
  const flopQs = sim.flops.flatMap((f) => Object.entries(f.cell.pins).filter(([p]) => lib[f.cell.type].pins[p] === "out").map(([, n]) => n));
  const watch = [...outPorts.flatMap(([n, p]) => portBits(n, p)), ...flopQs];

  // Stimulus: one vector per cycle (clk low while inputs change, then a rising edge).
  const vecs: Record<string, number>[] = [];
  for (let c = 0; c < cycles; c++) {
    const v: Record<string, number> = {};
    for (const [n, p] of inPorts) {
      if (n === clk) continue;
      const bits = portBits(n, p);
      for (const b of bits) {
        if (/^rst_?n$/i.test(n)) v[b] = c < 2 ? 0 : r() < 0.04 ? 0 : 1;
        else if (/^(rst|reset)$/i.test(n)) v[b] = c < 2 ? 1 : r() < 0.04 ? 1 : 0;
        else if (/^en(a)?$/i.test(n)) v[b] = r() < 0.75 ? 1 : 0;
        else v[b] = r() < 0.5 ? 1 : 0;
      }
    }
    vecs.push(v);
  }

  // iverilog testbench.
  const tb: string[] = ["`timescale 1ns/1ps", "module tb;"];
  for (const [n, p] of inPorts) tb.push(`  reg ${p.width > 1 ? `[${p.msb}:${p.lsb}] ` : ""}${n} = 0;`);
  for (const [n, p] of outPorts) tb.push(`  wire ${p.width > 1 ? `[${p.msb}:${p.lsb}] ` : ""}${n};`);
  const conns = Object.entries(net.ports).map(([n]) => (n === "VPWR" ? `.VPWR(1'b1)` : n === "VGND" ? `.VGND(1'b0)` : `.${n}(${n})`));
  tb.push(`  ${net.name} dut (${conns.join(", ")});`);
  const show = watch.map((b) => {
    const m = /^(\w+)\[(\d+)\]$/.exec(b);
    if (m && net.ports[m[1]]) return `${m[1]}[${m[2]}]`;
    if (net.ports[b]) return b;
    return `dut.${vname(b)}`;
  });
  const disp = `$display("%0d %s ${watch.map(() => "%b").join("")}", cyc, ph, ${show.join(", ")});`;
  tb.push("  integer cyc; reg [15:0] ph;", "  initial begin");
  vecs.forEach((v, c) => {
    // One input bit at a time (#1 apart), so designs that clock flops from data pins have no race.
    const sets = Object.entries(v).map(([b, x]) => `${b} = ${x}; #1;`).join(" ");
    tb.push(`    cyc = ${c}; ${sets} #5; ph = "lo"; ${disp}`);
    if (clk) tb.push(`    ${clk} = 1; #5; ph = "hi"; ${disp} ${clk} = 0; #1;`);
  });
  tb.push("    $finish;", "  end", "endmodule");

  const dir = mkdtempSync(join(tmpdir(), "decap-golden-"));
  const types = [...new Set(net.cells.map((c) => c.type))];
  const other = [...new Set([...readFileSync(netlistFile, "utf8").matchAll(/\b(sky130_(?!fd_sc_hd__)\w+__\w+)\s/g)].map((m) => m[1]))];
  const phys = other.map((m) => `module ${m}(input VGND, input VNB, input VPB, input VPWR);\nendmodule\n`);
  writeFileSync(join(dir, "cells.v"), [...types.map(cellModel), ...phys].join("\n"));
  writeFileSync(join(dir, "tb.v"), tb.join("\n"));
  execFileSync("iverilog", ["-g2012", "-o", join(dir, "sim.vvp"), "-s", "tb", join(dir, "tb.v"), join(dir, "cells.v"), netlistFile], { stdio: ["ignore", "pipe", "pipe"] });
  const out = execFileSync("vvp", ["-n", join(dir, "sim.vvp")], { maxBuffer: 1 << 28, timeout: 120_000 }).toString().split("\n").filter((l) => /^\d+ /.test(l));

  // TS side, same stimulus.
  const ts: string[] = [];
  const line = (c: number, ph: string) => `${c} ${ph.padStart(2)} ${watch.map((b) => sim.get(b)).join("")}`;
  vecs.forEach((v, c) => {
    for (const [b, x] of Object.entries(v)) sim.apply({ [b]: x });
    ts.push(line(c, "lo"));
    if (clk) { sim.apply({ [clk]: 1 }); ts.push(line(c, "hi")); sim.apply({ [clk]: 0 }); }
  });

  let mism = 0, first: string | undefined;
  const norm = (s: string) => s.replace(/\s+/g, " ").trim();
  for (let i = 0; i < Math.max(out.length, ts.length); i++) {
    if (norm(out[i] ?? "") !== norm(ts[i] ?? "")) { mism++; first ??= `iverilog: ${out[i]}\n      ts:       ${ts[i]}`; }
  }
  return { name, samples: ts.length, mismatches: mism, first, signals: watch.length, cells: sim.cellById.size, flops: sim.flops.length };
}

// Netlists whose behaviour depends on real gate delays; a zero-delay simulator cannot model them. Reported, not hidden.
const KNOWN: Record<string, string> = {
  tt_um_wokwi_407306064811090945: "clock doubler made from an inverter-chain glitch (ddr_input*.clkgen_2x): needs real gate delays",
};
const results: Result[] = [];
const warm = join(DECAP, "data/warmup/01_netlist.v");
if (!existsSync(warm)) { console.error("Missing data/warmup/01_netlist.v"); process.exit(2); }
results.push(runOne("warm-up (adder_demo)", parseNetlist(readFileSync(warm, "utf8")), warm, CYCLES, SEED));
const ttDir = join(DECAP, "data/tt08");
if (existsSync(ttDir) && TT_CYCLES > 0) {
  for (const chip of readdirSync(ttDir).sort()) {
    const f = join(ttDir, chip, "netlist.v");
    if (!existsSync(f)) continue;
    try { results.push(runOne(chip, parseNetlist(readFileSync(f, "utf8")), f, TT_CYCLES, SEED + results.length)); }
    catch (e) { const er = e as Error & { stderr?: Buffer }; results.push({ name: chip, samples: 0, mismatches: -1, first: String(er.stderr ?? er.message).slice(0, 400), signals: 0, cells: 0, flops: 0 }); }
  }
}

console.log("Golden check: TS simulator vs iverilog (SKY130 HD functional models from Liberty JSON)\n");
for (const r of results) {
  if (r.skip) { console.log(`SKIP  ${r.name.padEnd(44)} ${r.skip}`); continue; }
  const ok = r.mismatches === 0;
  console.log(`${ok ? "PASS" : KNOWN[r.name] ? "KNOWN" : "FAIL"}  ${r.name.padEnd(44)} ${String(r.cells).padStart(4)} cells ${String(r.flops).padStart(3)} flops  ${r.signals} signals x ${r.samples} samples  ${r.mismatches < 0 ? "error" : `${r.samples - r.mismatches}/${r.samples} match`}`);
  if (!ok && KNOWN[r.name]) console.log(`      ${KNOWN[r.name]}`);
  else if (!ok && r.first) console.log(`      ${r.first}`);
}
const w = results[0];
const tt = results.slice(1).filter((r) => !r.skip), ttPass = tt.filter((r) => r.mismatches === 0).length;
console.log(`\nWarm-up: ${w.samples - w.mismatches}/${w.samples} samples match (${CYCLES} random cycles, ${w.signals} signals each).`);
if (tt.length) console.log(`TT08: ${ttPass}/${tt.length} netlists match on every sample${tt.some((r) => r.mismatches && KNOWN[r.name]) ? " (the rest are KNOWN delay-dependent designs)" : ""} (${TT_CYCLES} cycles each; ${results.length - 1 - tt.length} skipped).`);
process.exit(w.mismatches === 0 ? 0 : 1);
