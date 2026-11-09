// Headless checks of the staged data and the fixtures (no browser needed): npm run check-data
//  - design.json: the SPEC §8 counts, every logic cell inside the die on a legal row/site, no overlaps
//  - lib.json covers every cell; the simulator builds
//  - picking: three.js raycasts (the same code path the Stage uses) from the overview camera hit every logic cell
//  - fixtures and any synced chip JSONs follow CONTRACTS.md §1–2

import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import * as THREE from "three";
import type { Bench, ChipResult, Design, Lib } from "../src/data/types";
import { Sim } from "../src/sim/sim";
import { cellBox, OVERVIEW_DIR } from "../src/view/geometry";

const HERE = dirname(fileURLToPath(import.meta.url));
const pub = resolve(HERE, "../public/data");
let fails = 0;
const ok = (cond: boolean, msg: string) => { console.log(`${cond ? "ok  " : "FAIL"}  ${msg}`); if (!cond) fails++; };

if (!existsSync(join(pub, "design.json"))) { console.error("No public/data/design.json: run npm run stage-data"); process.exit(2); }
const d: Design = JSON.parse(readFileSync(join(pub, "design.json"), "utf8"));
const lib: Lib = JSON.parse(readFileSync(join(pub, "lib.json"), "utf8"));

console.log("== design.json ==");
ok(d.cells.length === 79, `79 logic cells (got ${d.cells.length})`);
ok(new Set(d.cells.map((c) => c.type)).size === 16, `16 cell types (got ${new Set(d.cells.map((c) => c.type)).size})`);
ok(d.counts?.components === 230, `230 DEF components (got ${d.counts?.components})`);
ok(d.cells.length + d.phys.length === 230, `logic + physical = 230 (got ${d.cells.length + d.phys.length})`);
ok(d.ports.length === 6 && (d.counts?.pins ?? 0) === 8, `8 pins: 6 signal + 2 power (got ${d.ports.length} signal, ${d.counts?.pins} total)`);
ok(d.counts?.nets === 84, `84 nets (got ${d.counts?.nets})`);
const want: Record<string, number> = { mux2_1: 16, dfrtp_2: 16, nor2_2: 8, and2_2: 7, xor2_2: 5, or2_2: 5, a31o_2: 5, nand2_2: 4, xnor2_2: 3, clkbuf_16: 3, and4bb_2: 2, o21bai_2: 1, and3_2: 1, a21o_2: 1, a21boi_2: 1, a21bo_2: 1 };
const got: Record<string, number> = {};
for (const c of d.cells) got[c.type] = (got[c.type] ?? 0) + 1;
ok(Object.entries(want).every(([t, n]) => got[t] === n), `per-type counts match SPEC §8 (${Object.entries(got).map(([t, n]) => `${n} ${t}`).join(", ")})`);
const core = d.core!;
const inDie = d.cells.every((c) => c.x >= 0 && c.y >= 0 && c.x + c.w <= d.die.w && c.y + d.rowHeight <= d.die.h);
const inCore = d.cells.every((c) => c.x >= core.x0 - 1e-6 && c.x + c.w <= core.x1 + 1e-6 && c.y >= core.y0 - 1e-6 && c.y + d.rowHeight <= core.y1 + 1e-6);
ok(inDie, `every logic cell inside the ${d.die.w}x${d.die.h} um die`);
ok(inCore, `every logic cell inside the core (${core.x0}..${core.x1} x ${core.y0}..${core.y1})`);
const rowYs = new Set((d.rows ?? []).map((r) => r.y.toFixed(3)));
ok([...d.cells, ...d.phys].every((c) => rowYs.has(c.y.toFixed(3))), `every component sits on one of the ${rowYs.size} rows`);
ok([...d.cells, ...d.phys].every((c) => Math.abs(((c.x - core.x0) / d.site) - Math.round((c.x - core.x0) / d.site)) < 1e-6), "every component is on the 0.46 um site grid");
let overlaps = 0;
const byRow = new Map<string, { x: number; w: number; id: string }[]>();
for (const c of [...d.cells.map((c) => ({ x: c.x, y: c.y, w: c.w, id: c.id })), ...d.phys.map((p, i) => ({ x: p.x, y: p.y, w: p.w, id: `phys${i}` }))]) {
  const k = c.y.toFixed(3); if (!byRow.has(k)) byRow.set(k, []); byRow.get(k)!.push(c);
}
for (const row of byRow.values()) { row.sort((a, b) => a.x - b.x); for (let i = 1; i < row.length; i++) if (row[i].x < row[i - 1].x + row[i - 1].w - 1e-6) overlaps++; }
ok(overlaps === 0, `no overlapping components (cell widths from Liberty area / 2.72 um), ${overlaps} overlaps`);
ok(d.routes.every((r) => r.pts.every(([x, y]) => x >= 0 && y >= 0 && x <= d.die.w && y <= d.die.h)), `${d.routes.length} route segments/vias inside the die`);
const nets = new Set(d.cells.flatMap((c) => Object.values(c.pins)));
const routed = new Set(d.routes.map((r) => r.net));
ok([...nets].every((n) => routed.has(n)), `every connected net has routing (${nets.size} nets, ${routed.size} routed)`);
const mods: Record<string, number> = {};
for (const c of d.cells) mods[c.module ?? "?"] = (mods[c.module ?? "?"] ?? 0) + 1;
console.log(`      module mapping: ${Object.entries(mods).map(([m, n]) => `${m} ${n}`).join(", ")}`);
const src = Object.values(d.source.files)[0]?.split("\n") ?? [];
ok(d.cells.every((c) => c.src == null || (c.src >= 1 && c.src <= src.length)), "every src line exists in the source file");

console.log("== lib.json + simulator ==");
ok(d.cells.every((c) => lib[c.type] && Object.keys(c.pins).every((p) => lib[c.type].pins[p])), "lib.json has every cell type and pin");
const sim = new Sim(d.cells, lib);
ok(sim.unknownTypes.size === 0 && sim.flops.length === 16, `simulator builds: ${sim.cellById.size} cells, ${sim.flops.length} flip-flops, ${sim.order.length} levelised, ${sim.loops.length} in loops`);
// Functional spot check: shift 250 and 246 into A and B (MSB first); S must be 1 (250 + 246 = 496).
const step = (v: Record<string, number>) => { sim.apply(v); sim.apply({ clk: 1 }); sim.apply({ clk: 0 }); };
sim.apply({ rst_n: 0, en: 1, clk: 0 }); sim.apply({ rst_n: 1 });
for (let i = 7; i >= 0; i--) step({ A: (250 >> i) & 1, B: (246 >> i) & 1 });
const S1 = sim.get("S");
step({ A: 0, B: 0 });
ok(S1 === 1 && sim.get("S") === 0, `S = 1 after shifting in 250 + 246 = 496, and 0 after one more shift (got ${S1}, ${sim.get("S")})`);

console.log("== picking (three.js raycast, same geometry as the Stage) ==");
const box = new THREE.BoxGeometry(1, 1, 1);
const mesh = new THREE.InstancedMesh(box, new THREE.MeshBasicMaterial(), d.cells.length);
const phys = new THREE.InstancedMesh(box, new THREE.MeshBasicMaterial(), d.phys.length);
const M = new THREE.Matrix4(), q = new THREE.Quaternion();
d.cells.forEach((c, i) => { const b = cellBox(c, d.die.h, d.rowHeight); M.compose(new THREE.Vector3(b.x, b.y, b.z), q, new THREE.Vector3(b.sx, b.sy, b.sz)); mesh.setMatrixAt(i, M); });
d.phys.forEach((p, i) => { const b = cellBox(p, d.die.h, d.rowHeight); M.compose(new THREE.Vector3(b.x, 0.1, b.z), q, new THREE.Vector3(p.w - 0.05, 0.22, b.sz)); phys.setMatrixAt(i, M); });
const chip = new THREE.Group(); chip.position.set(-d.die.w / 2, 0, -d.die.h / 2); chip.add(mesh, phys); chip.updateMatrixWorld(true);
const ray = new THREE.Raycaster();
for (const [label, aspect, dir] of [["overview 16:10", 1.6, OVERVIEW_DIR], ["top-down 4:3", 1.33, [0, 1, 0.001]]] as const) {
  const cam = new THREE.PerspectiveCamera(36, aspect, 0.5, 3000);
  const vt = Math.tan((36 * Math.PI) / 360), ht = vt * aspect;
  const fit = Math.max((d.die.w + 16) / (2 * ht), ((d.die.h + 16) * 0.85) / (2 * vt), 7);
  cam.position.copy(new THREE.Vector3(...dir).normalize().multiplyScalar(fit * 1.05 + 4).add(new THREE.Vector3(0, 0.5, 0)));
  cam.lookAt(0, 0.5, 0); cam.updateMatrixWorld(true);
  let hits = 0; const times: number[] = []; const miss: string[] = [];
  d.cells.forEach((c, i) => {
    const b = cellBox(c, d.die.h, d.rowHeight);
    const p = new THREE.Vector3(b.x - d.die.w / 2, b.y + b.sy / 2, b.z - d.die.h / 2).project(cam);
    const t0 = performance.now();
    ray.setFromCamera(new THREE.Vector2(p.x, p.y), cam);
    const h = ray.intersectObjects([mesh], false)[0] ?? ray.intersectObjects([phys], false)[0];
    times.push(performance.now() - t0);
    if (h?.object === mesh && h.instanceId === i) hits++; else miss.push(c.id);
  });
  times.sort((a, b) => a - b);
  ok(hits === d.cells.length && times[times.length - 1] < 100, `${label}: ${hits}/${d.cells.length} cells picked at their own position; raycast median ${times[times.length >> 1].toFixed(2)} ms, max ${times[times.length - 1].toFixed(2)} ms${miss.length ? `; missed ${miss.join(", ")}` : ""}`);
}

console.log("== chip results and bench (CONTRACTS §1–2) ==");
function checkChip(name: string, r: ChipResult) {
  const ids = new Set(r.netlist.cells.map((c) => c.id));
  const problems: string[] = [], notes: string[] = [];
  for (const k of ["chip", "split", "variant", "stats", "decompiled", "blocks", "coverage", "verdict", "proof", "netlist", "cell_lines", "flags"] as const) if (r[k] === undefined) problems.push(`missing ${k}`);
  const nLines = r.decompiled?.verilog.split("\n").length ?? 0;
  for (const b of r.blocks ?? []) { if (b.cells.some((c) => !ids.has(c))) problems.push(`block ${b.id} names unknown cells`); if (b.lines[0] < 1 || b.lines[1] > nLines) problems.push(`block ${b.id} lines out of range`); }
  let zero = 0;
  for (const [c, l] of Object.entries(r.cell_lines ?? {})) { if (!ids.has(c)) problems.push(`cell_lines names unknown ${c}`); if (l === 0) zero++; else if (l < 0 || l > nLines) problems.push(`cell_lines ${c} -> ${l} out of range`); }
  if (zero) notes.push(`${zero} cell_lines entries are 0 (treated as "no line")`);
  for (const f of r.flags ?? []) if ([...f.cells, ...f.cone].some((c) => !ids.has(c))) problems.push(`flag ${f.id} names unknown cells`);
  const unknown = [...new Set(r.netlist.cells.map((c) => c.type).filter((t) => !lib[t]))];
  if (unknown.length) problems.push(`types not in lib.json: ${unknown.join(", ")}`);
  else { const s = new Sim(r.netlist.cells, lib); if (s.unknownTypes.size) problems.push("sim failed"); }
  ok(problems.length === 0, `${name}: ${r.netlist.cells.length} cells, ${r.blocks.length} blocks, ${r.flags.length} flags, verdict ${r.verdict}${problems.length ? ` -- ${problems.join("; ")}` : ""}${notes.length ? ` (note: ${notes.join("; ")})` : ""}`);
}
const fx = resolve(HERE, "../src/fixtures");
for (const f of readdirSync(join(fx, "chips"))) checkChip(`fixture ${f}`, JSON.parse(readFileSync(join(fx, "chips", f), "utf8")));
const checkBench = (name: string, b: Bench) => {
  for (const split of ["test", "dev"] as const) {
    const t = b.chips.filter((c) => c.split === split), s = b.summary[split];
    ok(s.n === t.length && (s.n === 0 || s.proven === t.filter((c) => c.verdict === "proven").length), `${name}: summary.${split} matches its ${t.length} ${split} chips (${s.proven ?? 0} proven)`);
  }
  const missing = b.chips.filter((c) => existsSync(join(pub, "chips")) && name.startsWith("public") && !existsSync(join(pub, "chips", `${c.chip}.json`))).map((c) => c.chip);
  if (name.startsWith("public")) ok(missing.length === 0, `${name}: every bench chip has a chip JSON in public/data/chips${missing.length ? ` (missing ${missing.join(", ")})` : ""}`);
};
checkBench("fixture bench.json", JSON.parse(readFileSync(join(fx, "bench.json"), "utf8")));
if (existsSync(join(pub, "bench.json"))) checkBench("public/data/bench.json", JSON.parse(readFileSync(join(pub, "bench.json"), "utf8")));
if (existsSync(join(pub, "chips"))) for (const f of readdirSync(join(pub, "chips")).filter((f) => f.endsWith(".json"))) checkChip(`public/data/chips/${f}`, JSON.parse(readFileSync(join(pub, "chips", f), "utf8")));

console.log(fails ? `\n${fails} check(s) FAILED` : "\nall checks passed");
process.exit(fails ? 1 : 0);
