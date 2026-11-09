// Minecraft block models in Three.js: bake a block state ("repeater[facing=north,delay=1]") into one geometry, using the
// blockstates/models/textures that scripts/mc-assets.mjs copies out of the local client jar into /mc/. Vanilla look:
// per-face shade (top 1, N/S .8, E/W .6, bottom .5), nearest-neighbour pixels, cutout alpha, redstone tinted by power.

import * as THREE from "three";
import { getJson } from "../api/http";

interface Face { uv?: number[]; texture: string | null; tintindex?: number; rotation?: number }
interface Element { from: number[]; to: number[]; shade?: boolean; faces: Record<string, Face>;
  rotation?: { origin: number[]; axis: "x" | "y" | "z"; angle: number } }
interface Ref { model: string; x?: number; y?: number }
interface BlockState { variants?: Record<string, Ref | Ref[]>; multipart?: { when?: Cond; apply: Ref | Ref[] }[] }
type Cond = Record<string, string> | { OR: Cond[] } | { AND: Cond[] };
export interface McAssets { blockstates: Record<string, BlockState>; models: Record<string, { elements: Element[] }> }

/** Fetch /mc/assets.json; null when it was never generated (then the view keeps its flat colours).
 *  Dev server only: Mojang's textures must never ship on the public site (vite.config also strips dist/mc). */
export async function loadMcAssets(): Promise<McAssets | null> {
  if (!import.meta.env.DEV) return null;
  const d = await getJson<McAssets>("/mc/assets.json").catch(() => null);
  return d?.blockstates && d?.models ? d : null;
}

const DEFAULTS: Record<string, string> = { powered: "false", locked: "false", lit: "false", mode: "compare", delay: "1",
  facing: "north", face: "floor", power: "0", north: "none", south: "none", east: "none", west: "none" };

function parseState(s: string): { name: string; props: Record<string, string> } {
  const [head, rest] = s.replace(/^minecraft:/, "").split("[");
  const props = { ...DEFAULTS };
  for (const kv of (rest ?? "").replace("]", "").split(",")) { const [k, v] = kv.split("="); if (k && v) props[k] = v; }
  return { name: head, props };
}

function holds(c: Cond | undefined, p: Record<string, string>): boolean {
  if (!c) return true;
  if ("OR" in c) return (c.OR as Cond[]).some((x) => holds(x, p));
  if ("AND" in c) return (c.AND as Cond[]).every((x) => holds(x, p));
  return Object.entries(c as Record<string, string>).every(([k, v]) => String(v).split("|").includes(p[k]));
}

function refsFor(a: McAssets, state: string): Ref[] | null {
  const { name, props } = parseState(state);
  const bs = a.blockstates[name];
  if (!bs) return null;
  const first = (r: Ref | Ref[]) => (Array.isArray(r) ? r[0] : r);
  if (bs.variants) {
    for (const [key, r] of Object.entries(bs.variants)) {
      if (key === "" || key.split(",").every((kv) => { const [k, v] = kv.split("="); return props[k] === v; })) return [first(r)];
    }
    return null;
  }
  return (bs.multipart ?? []).filter((m) => holds(m.when, props)).map((m) => first(m.apply));
}

// Redstone dust colour by power (vanilla RedStoneWireBlock), lifted a little at 0 so idle wires still read on a dark floor.
function dustTint(power: number): [number, number, number] {
  const f = power / 15;
  const r = power === 0 ? 0.45 : f * 0.6 + 0.4;
  return [r, Math.max(0, Math.min(1, f * f * 0.7 - 0.5)), Math.max(0, Math.min(1, f * f * 0.6 - 0.7))];
}

const DIRS = ["down", "up", "north", "south", "west", "east"] as const;
// For each face: the 4 corners (as picks of from/to on x, y, z: 0 = from, 1 = to) and the axis each uv runs along.
const CORNERS: Record<string, number[][]> = {
  down: [[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 1]], up: [[0, 1, 0], [1, 1, 0], [1, 1, 1], [0, 1, 1]],
  north: [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], south: [[0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
  west: [[0, 0, 0], [0, 0, 1], [0, 1, 1], [0, 1, 0]], east: [[1, 0, 0], [1, 0, 1], [1, 1, 1], [1, 1, 0]],
};
const NORMAL: Record<string, [number, number, number]> = {
  down: [0, -1, 0], up: [0, 1, 0], north: [0, 0, -1], south: [0, 0, 1], west: [-1, 0, 0], east: [1, 0, 0],
};
// vanilla default uv of a point p (0..16) on each face, so a face's explicit uv maps linearly (and flips if reversed)
const DEFAULT_UV: Record<string, (p: number[]) => [number, number]> = {
  down: (p) => [p[0], 16 - p[2]], up: (p) => [p[0], p[2]], north: (p) => [16 - p[0], 16 - p[1]],
  south: (p) => [p[0], 16 - p[1]], west: (p) => [p[2], 16 - p[1]], east: (p) => [16 - p[2], 16 - p[1]],
};

function rot(v: THREE.Vector3, axis: "x" | "y" | "z", deg: number, o: THREE.Vector3) {
  if (!deg) return;
  v.sub(o).applyAxisAngle(new THREE.Vector3(axis === "x" ? 1 : 0, axis === "y" ? 1 : 0, axis === "z" ? 1 : 0), deg * Math.PI / 180).add(o);
}

function shadeOf(n: THREE.Vector3): number {
  const ax = Math.abs(n.x), ay = Math.abs(n.y), az = Math.abs(n.z);
  if (ay >= ax && ay >= az) return n.y > 0 ? 1 : 0.5;
  return az >= ax ? 0.8 : 0.6;
}

const lin = (c: number) => (c < 0.04045 ? c * 0.0773993808 : Math.pow(c * 0.9478672986 + 0.0521327014, 2.4));  // sRGB -> linear
const SKIP = new Set(["block/redstone_dust_overlay"]);      // vanilla draws it at alpha ~0; it only z-fights here

/** Bake a block state into one geometry with a group per texture. Block-local: x/z centred on 0, y from 0 (stands on its y). */
export function bakeState(a: McAssets, state: string): { geometry: THREE.BufferGeometry; textures: string[] } | null {
  const refs = refsFor(a, state);
  if (!refs?.length) return null;
  const { props } = parseState(state);
  const tint = parseState(state).name === "redstone_wire" ? dustTint(+props.power) : [1, 1, 1];
  const byTex = new Map<string, { pos: number[]; uv: number[]; col: number[] }>();
  const c8 = new THREE.Vector3(8, 8, 8);
  for (const ref of refs) {
    const model = a.models[ref.model.replace(/^minecraft:/, "")];
    for (const el of model?.elements ?? []) {
      for (const dir of DIRS) {
        const f = el.faces[dir];
        if (!f?.texture || SKIP.has(f.texture)) continue;
        const pts = CORNERS[dir].map((c) => [c[0] ? el.to[0] : el.from[0], c[1] ? el.to[1] : el.from[1], c[2] ? el.to[2] : el.from[2]]);
        const duv = pts.map(DEFAULT_UV[dir]);
        const us = duv.map((d) => d[0]), vs = duv.map((d) => d[1]);
        const [u0, u1, v0, v1] = [Math.min(...us), Math.max(...us), Math.min(...vs), Math.max(...vs)];
        const uv = f.uv ?? [u0, v0, u1, v1];
        const r = ((f.rotation ?? 0) / 90) & 3;
        const tuv = duv.map(([du, dv]) => {
          let s = u1 > u0 ? (du - u0) / (u1 - u0) : 0, t = v1 > v0 ? (dv - v0) / (v1 - v0) : 0;
          for (let i = 0; i < r; i++) [s, t] = [1 - t, s];
          return [(uv[0] + s * (uv[2] - uv[0])) / 16, 1 - (uv[1] + t * (uv[3] - uv[1])) / 16];
        });
        const vs3 = pts.map((p) => {
          const v = new THREE.Vector3(p[0], p[1], p[2]);
          if (el.rotation) rot(v, el.rotation.axis, el.rotation.angle, new THREE.Vector3(...el.rotation.origin));
          rot(v, "x", -(ref.x ?? 0), c8);
          rot(v, "y", -(ref.y ?? 0), c8);
          return v;
        });
        const n = new THREE.Vector3(...NORMAL[dir]);
        if (el.rotation) n.applyAxisAngle(new THREE.Vector3(el.rotation.axis === "x" ? 1 : 0, el.rotation.axis === "y" ? 1 : 0, el.rotation.axis === "z" ? 1 : 0), el.rotation.angle * Math.PI / 180);
        n.applyAxisAngle(new THREE.Vector3(1, 0, 0), -(ref.x ?? 0) * Math.PI / 180).applyAxisAngle(new THREE.Vector3(0, 1, 0), -(ref.y ?? 0) * Math.PI / 180);
        const sh = el.shade === false ? 1 : shadeOf(n);
        const k = f.tintindex !== undefined ? tint : [1, 1, 1];
        // wind counter-clockwise seen from outside the face
        const cross = new THREE.Vector3().subVectors(vs3[1], vs3[0]).cross(new THREE.Vector3().subVectors(vs3[2], vs3[0]));
        const order = cross.dot(n) >= 0 ? [0, 1, 2, 0, 2, 3] : [0, 2, 1, 0, 3, 2];
        let g = byTex.get(f.texture);
        if (!g) byTex.set(f.texture, (g = { pos: [], uv: [], col: [] }));
        for (const i of order) {
          g.pos.push(vs3[i].x / 16 - 0.5, vs3[i].y / 16, vs3[i].z / 16 - 0.5);
          g.uv.push(tuv[i][0], tuv[i][1]);
          g.col.push(lin(k[0] * sh), lin(k[1] * sh), lin(k[2] * sh));        // vanilla shades in sRGB
        }
      }
    }
  }
  if (!byTex.size) return null;
  const geometry = new THREE.BufferGeometry();
  const pos: number[] = [], uv: number[] = [], col: number[] = [], textures: string[] = [];
  for (const [t, g] of byTex) {
    geometry.addGroup(pos.length / 3, g.pos.length / 3, textures.length);
    textures.push(t); pos.push(...g.pos); uv.push(...g.uv); col.push(...g.col);
  }
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  geometry.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
  geometry.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
  geometry.computeBoundingSphere();
  return { geometry, textures };
}

/** One shared material per texture: crisp pixels, mipmapped at distance, cutout alpha like vanilla. */
export class McMaterials {
  private cache = new Map<string, THREE.Material>();
  private loader = new THREE.TextureLoader();
  constructor(private anisotropy = 1) {}
  get(name: string): THREE.Material {
    let m = this.cache.get(name);
    if (m) return m;
    const tex = this.loader.load(`/mc/textures/${name}.png`, (t) => {
      const img = t.image as HTMLImageElement;
      if (img.height > img.width) { t.repeat.set(1, img.width / img.height); t.offset.set(0, 1 - img.width / img.height); }  // animated strip: frame 0
    });
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.magFilter = THREE.NearestFilter;
    tex.minFilter = THREE.NearestMipmapLinearFilter;
    tex.anisotropy = this.anisotropy;
    m = new THREE.MeshBasicMaterial({ map: tex, vertexColors: true, alphaTest: 0.1, transparent: false });
    this.cache.set(name, m);
    return m;
  }
  dispose() { for (const m of this.cache.values()) { (m as THREE.MeshBasicMaterial).map?.dispose(); m.dispose(); } this.cache.clear(); }
}
