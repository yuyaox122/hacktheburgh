// npm run mc-assets: copy the blockstates, models and textures the redstone view needs out of YOUR local Minecraft
// client jar into public/mc/ (gitignored: Mojang's assets never go in the repo). Without them the view keeps flat colours.
// Jar: MC_JAR, else the newest .minecraft/versions/*/*.jar. Optional MC_PACK=<resource pack .zip> overrides textures.
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { inflateRawSync } from "node:zlib";

const out = join(dirname(fileURLToPath(import.meta.url)), "..", "public", "mc");
const COLOURS = ["white", "orange", "magenta", "light_blue", "yellow", "lime", "pink", "gray", "light_gray", "cyan",
  "purple", "blue", "brown", "green", "red", "black"];
const BLOCKS = ["redstone_wire", "repeater", "comparator", "redstone_lamp", "lever", "redstone_block", "redstone_torch",
  "stone", "smooth_stone", "glass", ...COLOURS.map((c) => `${c}_concrete`)];

/** Minimal zip reader: name -> () => Buffer. */
function openZip(path) {
  const b = readFileSync(path);
  let e = b.length - 22;
  while (e >= 0 && b.readUInt32LE(e) !== 0x06054b50) e--;
  if (e < 0) throw new Error(`${path} is not a zip`);
  const n = b.readUInt16LE(e + 10);
  let p = b.readUInt32LE(e + 16);
  const files = new Map();
  for (let i = 0; i < n; i++) {
    const method = b.readUInt16LE(p + 10), size = b.readUInt32LE(p + 20), nl = b.readUInt16LE(p + 28);
    const xl = b.readUInt16LE(p + 30), cl = b.readUInt16LE(p + 32), off = b.readUInt32LE(p + 42);
    const name = b.toString("utf8", p + 46, p + 46 + nl);
    files.set(name, () => {
      const start = off + 30 + b.readUInt16LE(off + 26) + b.readUInt16LE(off + 28), raw = b.subarray(start, start + size);
      return method === 0 ? raw : inflateRawSync(raw);
    });
    p += 46 + nl + xl + cl;
  }
  return files;
}

function findJar() {
  if (process.env.MC_JAR) return process.env.MC_JAR;
  const roots = [join(process.env.APPDATA ?? "", ".minecraft"), join(homedir(), "Library", "Application Support", "minecraft"),
    join(homedir(), ".minecraft")];
  for (const r of roots) {
    const v = join(r, "versions");
    if (!existsSync(v)) continue;
    const vers = readdirSync(v).filter((d) => /^\d+\.\d+(\.\d+)?$/.test(d) && existsSync(join(v, d, `${d}.jar`)))
      .sort((a, b) => b.localeCompare(a, undefined, { numeric: true }));
    if (vers.length) return join(v, vers[0], `${vers[0]}.jar`);
  }
  return null;
}

const jarPath = findJar();
if (!jarPath || !existsSync(jarPath)) {
  console.error("No Minecraft client jar found. Launch the game once (any 1.20+ version) or set MC_JAR=/path/to/client.jar.");
  process.exit(1);
}
const jar = openZip(jarPath);
const pack = process.env.MC_PACK ? openZip(process.env.MC_PACK) : null;
const A = "assets/minecraft/";
const strip = (s) => s.replace(/^minecraft:/, "");
const json = (zip, p) => { const f = zip.get(p); return f ? JSON.parse(f().toString("utf8")) : null; };

// Resolve a model through its parents into { elements, faces with concrete texture names }.
const models = {}, textures = new Set();
function resolve(name) {
  name = strip(name);
  if (models[name]) return;
  const tex = {};
  let elements = null, m = json(jar, `${A}models/${name}.json`);
  while (m) {
    for (const [k, v] of Object.entries(m.textures ?? {})) if (!(k in tex)) tex[k] = v;
    if (!elements && m.elements) elements = m.elements;
    m = m.parent ? json(jar, `${A}models/${strip(m.parent)}.json`) : null;
  }
  const look = (t) => { for (let i = 0; i < 8 && t?.startsWith("#"); i++) { t = tex[t.slice(1)]; t = t?.sprite ?? t; } return t ? strip(t) : null; };
  for (const el of elements ?? []) for (const f of Object.values(el.faces ?? {})) {
    f.texture = look(f.texture);
    if (f.texture) textures.add(f.texture);
  }
  models[name] = { elements: elements ?? [] };
}

const blockstates = {};
for (const b of BLOCKS) {
  const bs = json(jar, `${A}blockstates/${b}.json`);
  if (!bs) { console.warn(`skip ${b}: not in this jar`); continue; }
  blockstates[b] = bs;
  const refs = [...Object.values(bs.variants ?? {}), ...(bs.multipart ?? []).map((p) => p.apply)].flat();
  for (const r of refs) resolve(r.model);
}

mkdirSync(join(out, "textures", "block"), { recursive: true });
let copied = 0;
for (const t of textures) {
  const p = `${A}textures/${t}.png`, f = pack?.get(p) ?? jar.get(p);
  if (!f) { console.warn(`missing texture ${t}`); continue; }
  writeFileSync(join(out, "textures", `${t}.png`), f());
  copied++;
}
writeFileSync(join(out, "assets.json"), JSON.stringify({ jar: jarPath.split(/[\\/]/).pop(), blockstates, models }));
writeFileSync(join(out, ".gitignore"), "*\n");
console.log(`public/mc: ${Object.keys(blockstates).length} blocks, ${Object.keys(models).length} models, ${copied} textures from ${jarPath}${pack ? ` + ${process.env.MC_PACK}` : ""}`);
