// Copy backend outputs (../out/bench.json, ../out/chips/*.json) into public/data/ so the app can load them statically.
import { copyFileSync, existsSync, mkdirSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, "../../out");
const dst = resolve(here, "../public/data");
mkdirSync(join(dst, "chips"), { recursive: true });
let n = 0;
if (existsSync(join(out, "bench.json"))) { copyFileSync(join(out, "bench.json"), join(dst, "bench.json")); n++; console.log("bench.json"); }
else console.log("no ../out/bench.json yet (the app falls back to /api/bench, then to the fixture)");
const chips = join(out, "chips");
if (existsSync(chips)) for (const f of readdirSync(chips).filter((f) => f.endsWith(".json"))) { copyFileSync(join(chips, f), join(dst, "chips", f)); n++; }
console.log(`copied ${n} file(s) into public/data/`);
