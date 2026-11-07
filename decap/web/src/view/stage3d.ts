// The Stage: a real placed-and-routed die in 3D (ported from docs/decap/prototype.html, driven by design.json).
// Scene units are microns. Scene x = DEF x, scene z = die height - DEF y (so the layout reads as drawn).

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import type { Design, DesignCell, Lib } from "../data/types";
import { category, CATEGORY_VAR, type Category } from "./describe";
import { css, reduceMotion } from "../ui/dom";
import { cellBox, OVERVIEW_DIR, Y } from "./geometry";

export type LayerKey = "pkg" | "cell" | "m1" | "via" | "m2" | "m3" | "m4" | "power";
export const LAYERS: { key: LayerKey; label: string; colour: string; on: boolean }[] = [
  { key: "power", label: "power straps (met4/5)", colour: "var(--met4)", on: false },
  { key: "m4", label: "met4", colour: "var(--met4)", on: true },
  { key: "m3", label: "met3", colour: "var(--met3)", on: true },
  { key: "m2", label: "met2", colour: "var(--met2)", on: true },
  { key: "via", label: "vias", colour: "#c9d3e3", on: true },
  { key: "m1", label: "met1 + rails", colour: "var(--met1)", on: true },
  { key: "cell", label: "cells", colour: "var(--adder)", on: true },
  { key: "pkg", label: "package", colour: "var(--accent)", on: true },
];
type Group = "pkg" | "sub" | "cell" | "m1" | "via" | "m2" | "via2" | "m3" | "via3" | "m4" | "power";
const EXPLODE_IDX: Record<Group, number> = { pkg: 0, sub: 0, cell: 1, m1: 2, via: 3, m2: 4, via2: 5, m3: 6, via3: 7, m4: 8, power: 9 };
const WIRE: Record<string, { g: Group; y: number; w: number; v: string }> = {
  met1: { g: "m1", y: Y.m1, w: 0.2, v: "--met1" },
  met2: { g: "m2", y: Y.m2, w: 0.2, v: "--met2" },
  met3: { g: "m3", y: Y.m3, w: 0.34, v: "--met3" },
  met4: { g: "m4", y: Y.m4, w: 0.34, v: "--met4" },
};
const VIA: Record<string, { g: Group; y: number; s: number }> = {
  mcon: { g: "m1", y: Y.m1 - 0.06, s: 0.19 },
  via: { g: "via", y: Y.via, s: 0.24 },
  via2: { g: "via2", y: Y.via2, s: 0.3 },
  via3: { g: "via3", y: Y.via3, s: 0.36 },
  via4: { g: "via3", y: Y.via3, s: 0.36 },
};
const isClockNet = (n: string) => /^(clk|clknet_)/.test(n);

export interface StageCallbacks {
  onCell(id: string | null): void;
  onNet(net: string): void;
  onPort(name: string): void;
  onPhys(p: Design["phys"][number]): void;
  onHover?(text: string | null): void;
}

interface Seg { net: string; x1: number; z1: number; x2: number; z2: number; layer: string }

const M4 = new THREE.Matrix4(), V3 = new THREE.Vector3(), S3 = new THREE.Vector3(), Q4 = new THREE.Quaternion();
function setBox(mesh: THREE.InstancedMesh, i: number, x: number, y: number, z: number, sx: number, sy: number, sz: number) {
  V3.set(x, y, z); S3.set(sx, sy, sz); M4.compose(V3, Q4, S3); mesh.setMatrixAt(i, M4);
}
const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

export class Stage3D {
  readonly W: number; readonly H: number; readonly RH: number;
  renderer!: THREE.WebGLRenderer;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(36, 1, 0.5, 3000);
  private controls!: OrbitControls;
  private labels!: CSS2DRenderer;
  private composer!: EffectComposer;
  private bloom!: UnrealBloomPass;
  private chip = new THREE.Group();
  private G = {} as Record<Group, THREE.Group>;
  private mLogic!: THREE.InstancedMesh; private mTop!: THREE.InstancedMesh; private mPhys!: THREE.InstancedMesh;
  private mPoly!: THREE.InstancedMesh; private mDiff!: THREE.InstancedMesh;
  private wires = new Map<string, { base: THREE.InstancedMesh; glow: THREE.InstancedMesh; segs: Seg[]; glowSegs: Seg[] }>();
  private selBox!: THREE.LineSegments; private hovBox!: THREE.LineSegments;
  private bond: { net: string; mat: THREE.MeshStandardMaterial }[] = [];
  private pinEls = new Map<string, HTMLElement>();
  private cells: DesignCell[];
  private cellIdx = new Map<string, number>();
  private outNet = new Map<string, string>();
  private cellColour: THREE.Color[] = [];
  private colourBy: "function" | "module" = "function";
  private highlight: Set<string> | null = null;
  private hlNets: Set<string> | null = null;
  private selected: string | null = null;
  private hover: string | null = null;
  private value: (net: string) => number = () => 0;
  private prev = new Map<string, number>();
  private flash = new Map<string, number>();
  private explode = 0; private explodeCur = 0;
  private fly = { t: 1, fromP: new THREE.Vector3(), toP: new THREE.Vector3(), fromT: new THREE.Vector3(), toT: new THREE.Vector3(), dur: 0.035 };
  private intro = { on: false, t0: 0, dur: 2600, fromP: new THREE.Vector3(), toP: new THREE.Vector3(), toT: new THREE.Vector3() };
  private dirtyColours = true;
  private running = false;
  private lastInput = performance.now();
  private tip: HTMLElement;
  private tagObj!: CSS2DObject;
  lastPickMs = 0;
  ok = false;
  private ro?: ResizeObserver;
  private moduleColours = new Map<string, THREE.Color>();

  constructor(private host: HTMLElement, private design: Design, private lib: Lib, private cb: StageCallbacks) {
    this.W = design.die.w; this.H = design.die.h; this.RH = design.rowHeight;
    this.cells = design.cells;
    this.cells.forEach((c, i) => {
      this.cellIdx.set(c.id, i);
      const L = lib[c.type];
      const out = L ? Object.keys(c.pins).find((p) => L.pins[p] === "out") : undefined;
      if (out) this.outNet.set(c.id, c.pins[out]);
    });
    this.tip = document.createElement("div");
    this.tip.className = "overlay tip"; this.tip.hidden = true;
    host.append(this.tip);
    const canvas = document.createElement("canvas");
    canvas.className = "gl";
    host.prepend(canvas);
    try {
      this.renderer = new THREE.WebGLRenderer({ canvas, antialias: false, powerPreference: "high-performance" });
    } catch {
      host.insertAdjacentHTML("beforeend", `<div class="nogl">The 3D view needs WebGL, which this browser has turned off. The source, inspector and simulator still work.</div>`);
      return;
    }
    this.ok = true;
    this.build();
  }

  private sx = (x: number) => x;
  private sz = (y: number) => this.H - y;

  private build() {
    const { W, H, RH } = this;
    const r = this.renderer;
    r.setPixelRatio(Math.min(devicePixelRatio, 2));
    r.shadowMap.enabled = true; r.shadowMap.type = THREE.PCFSoftShadowMap;
    const bg = new THREE.Color(css("--bg") || "#090d13");
    this.scene.background = bg; this.scene.fog = new THREE.FogExp2(bg, 0.0028);
    const pmrem = new THREE.PMREMGenerator(r);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.controls = new OrbitControls(this.camera, r.domElement);
    Object.assign(this.controls, { enableDamping: true, dampingFactor: 0.08, maxPolarAngle: Math.PI * 0.48, minDistance: 4, maxDistance: 420, autoRotateSpeed: 0.4 });
    const lab = document.createElement("div"); lab.className = "labels"; this.host.append(lab);
    this.labels = new CSS2DRenderer({ element: lab });
    this.composer = new EffectComposer(r, new THREE.WebGLRenderTarget(2, 2, { type: THREE.HalfFloatType, samples: 4 }));
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(2, 2), 0.7, 0.45, 1.0);
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());

    this.scene.add(new THREE.AmbientLight(0x9fb2d6, 0.3));
    const key = new THREE.DirectionalLight(0xfff1dc, 1.75); key.position.set(-70, 130, 90);
    key.castShadow = true; key.shadow.mapSize.set(2048, 2048);
    Object.assign(key.shadow.camera, { left: -110, right: 110, top: 110, bottom: -110, near: 20, far: 400 });
    key.shadow.camera.updateProjectionMatrix(); key.shadow.bias = -0.0004; key.shadow.normalBias = 0.05;
    this.scene.add(key);
    const rim = new THREE.DirectionalLight(0x6a8cff, 0.6); rim.position.set(90, 40, -110); this.scene.add(rim);
    const desk = new THREE.Mesh(new THREE.PlaneGeometry(2000, 2000), new THREE.MeshStandardMaterial({ color: 0x0a0e14, roughness: 0.6, metalness: 0.45, envMapIntensity: 0.3 }));
    desk.rotation.x = -Math.PI / 2; desk.position.y = -4; desk.receiveShadow = true; this.scene.add(desk);

    this.chip.position.set(-W / 2, 0, -H / 2); this.scene.add(this.chip);
    for (const k of Object.keys(EXPLODE_IDX) as Group[]) { this.G[k] = new THREE.Group(); this.chip.add(this.G[k]); }
    const box = new THREE.BoxGeometry(1, 1, 1);
    const std = (o: THREE.MeshStandardMaterialParameters = {}) => new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.5, metalness: 0.1, envMapIntensity: 0.55, ...o });
    const irid = (o: THREE.MeshPhysicalMaterialParameters = {}) => new THREE.MeshPhysicalMaterial({ color: 0xffffff, metalness: 0.85, roughness: 0.25, iridescence: 0.9, iridescenceIOR: 1.5, iridescenceThicknessRange: [180, 640], envMapIntensity: 0.95, ...o });
    const glow = () => new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false });
    const add = (g: THREE.Object3D, mat: THREE.Material, sx: number, sy: number, sz: number, x: number, y: number, z: number) => {
      const m = new THREE.Mesh(box, mat); m.scale.set(sx, sy, sz); m.position.set(x, y, z); g.add(m); return m;
    };
    const inst = (geo: THREE.BufferGeometry, mat: THREE.Material, n: number, parent: THREE.Object3D) => {
      const m = new THREE.InstancedMesh(geo, mat, Math.max(1, n)); m.frustumCulled = false; m.setColorAt(0, new THREE.Color(1, 1, 1)); m.count = 0; parent.add(m); return m;
    };

    /* package: epoxy, cavity, lead frame, gold bond wires */
    const PW = W + 64, PD = H + 56;
    add(this.G.pkg, std({ color: 0x0b0e13, roughness: 0.86, metalness: 0, envMapIntensity: 0.25 }), PW, 3, PD, W / 2, -2.2, H / 2);
    add(this.G.pkg, std({ color: 0x161b23, roughness: 0.95, metalness: 0 }), W + 34, 0.05, H + 34, W / 2, -0.68, H / 2);
    add(this.G.pkg, std({ color: 0x8b919b, metalness: 0.9, roughness: 0.35 }), W + 14, 0.08, H + 14, W / 2, -0.62, H / 2);
    const leadMat = std({ color: 0xc2ab84, metalness: 0.9, roughness: 0.3 });
    const goldMat = std({ color: 0xe0b45a, metalness: 0.95, roughness: 0.22, envMapIntensity: 0.9 });
    const ports = this.design.ports;
    const pads = [
      ...ports.map((p) => ({ name: p.name, dir: p.dir as string, x: p.x, y: p.y })),
      { name: "VPWR", dir: "pwr", x: W * 0.35, y: H - 0.4 }, { name: "VGND", dir: "pwr", x: W * 0.65, y: 0.4 },
    ];
    for (const p of pads) {
      const px = this.sx(p.x), pz = this.sz(p.y);
      const left = p.x < W * 0.25, right = p.x > W * 0.75, top = p.y > H * 0.75;
      const dx = left ? -1 : right ? 1 : 0, dz = dx ? 0 : top ? -1 : 1;
      const padX = px + dx * -1.6, padZ = pz + dz * -1.6;
      add(this.G.pkg, goldMat, 2.4, 0.16, 2.4, padX, 0.08, padZ);
      const lx = dx ? (dx < 0 ? -15 : W + 15) : padX, lz = dz ? (dz < 0 ? -15 : H + 15) : padZ + (padZ - H / 2) * 0.12;
      const lead = new THREE.Vector3(lx, -0.55, dx ? H / 2 + (padZ - H / 2) * 1.12 : lz);
      add(this.G.pkg, leadMat, dx ? 14 : 1.4, 0.08, dx ? 1.4 : 14, lead.x + dx * 7, -0.6, lead.z + dz * 7);
      const a = new THREE.Vector3(padX, 0.18, padZ);
      const out = lead.clone().sub(a).setY(0).normalize();
      const curve = new THREE.CubicBezierCurve3(a, a.clone().add(out.clone().multiplyScalar(1.5)).setY(8), lead.clone().sub(out.clone().multiplyScalar(3)).setY(4.5), lead);
      const wm = std({ color: 0xe6b95c, metalness: 0.95, roughness: 0.2, envMapIntensity: 1, emissive: new THREE.Color(css("--high")), emissiveIntensity: 0 });
      this.G.pkg.add(new THREE.Mesh(new THREE.TubeGeometry(curve, 48, 0.12, 8, false), wm));
      if (p.dir !== "pwr") this.bond.push({ net: p.name, mat: wm });
      const el = document.createElement("div");
      el.className = "pinlabel" + (p.dir === "pwr" ? " pwr" : "");
      el.textContent = p.name;
      if (p.dir !== "pwr") {
        el.title = p.dir === "out" ? `Output ${p.name}: click to see everything that drives it` : `Input ${p.name}: click to see everything it reaches`;
        el.addEventListener("click", () => this.cb.onPort(p.name));
        this.pinEls.set(p.name, el);
      } else el.style.pointerEvents = "none";
      const o = new CSS2DObject(el);
      o.position.set(padX + dx * 7, 1.2, padZ + dz * 7);
      this.G.pkg.add(o);
    }

    /* die: oxide-coated silicon, seal ring, rows of wells, met1 power rails */
    add(this.G.sub, new THREE.MeshPhysicalMaterial({ color: 0x151d29, metalness: 0.5, roughness: 0.3, iridescence: 1, iridescenceIOR: 1.9, iridescenceThicknessRange: [280, 1150], envMapIntensity: 1 }), W, 0.4, H, W / 2, -0.2, H / 2);
    const seal = irid({ color: 0x9aa4b4 });
    add(this.G.sub, seal, W - 1, 0.1, 0.5, W / 2, 0.05, 1); add(this.G.sub, seal, W - 1, 0.1, 0.5, W / 2, 0.05, H - 1);
    add(this.G.sub, seal, 0.5, 0.1, H - 1, 1, 0.05, H / 2); add(this.G.sub, seal, 0.5, 0.1, H - 1, W - 1, 0.05, H / 2);
    const core = this.design.core ?? { x0: 0, y0: 0, x1: W, y1: H };
    const cw = core.x1 - core.x0;
    const well = std({ color: 0x22314a, roughness: 0.8 }), well2 = std({ color: 0x1b2638, roughness: 0.85 });
    const rows = this.design.rows ?? [];
    for (const row of rows) {
      const flip = row.orient.includes("S");
      add(this.G.sub, flip ? well2 : well, cw, 0.02, RH / 2 - 0.1, core.x0 + cw / 2, 0.045, this.sz(row.y + RH * 0.75));
      add(this.G.sub, flip ? well : well2, cw, 0.02, RH / 2 - 0.1, core.x0 + cw / 2, 0.045, this.sz(row.y + RH * 0.25));
    }
    const railY = new Set<number>(); rows.forEach((r0) => { railY.add(+r0.y.toFixed(3)); railY.add(+(r0.y + RH).toFixed(3)); });
    let k = 0;
    for (const y of [...railY].sort((a, b) => a - b)) add(this.G.m1, irid({ color: k++ % 2 ? 0x3d5f9c : 0x324c80 }), cw, 0.05, 0.48, core.x0 + cw / 2, Y.m1, this.sz(y));

    /* cells: body, diffusion, poly fingers, top strip that glows when the output is 1 */
    const n = this.cells.length;
    this.mLogic = inst(box, new THREE.MeshPhysicalMaterial({ roughness: 0.45, metalness: 0.1, clearcoat: 0.7, clearcoatRoughness: 0.25, envMapIntensity: 0.6 }), n, this.G.cell);
    this.mTop = inst(box, glow(), n, this.G.cell);
    this.mDiff = inst(box, std({ roughness: 0.5, metalness: 0.05, color: 0x6f5a3a }), n * 2, this.G.cell);
    this.mPoly = inst(box, std({ roughness: 0.35, metalness: 0.2, color: new THREE.Color(css("--poly")) }), n * 30, this.G.cell);
    this.mPhys = inst(box, std({ roughness: 0.8 }), this.design.phys.length, this.G.cell);
    let pi = 0, di = 0;
    this.cells.forEach((c, i) => {
      const b = cellBox(c, this.H, RH);
      const cx = b.x, cz = b.z, w = b.sx, dz = b.sz;
      setBox(this.mLogic, i, b.x, b.y, b.z, b.sx, b.sy, b.sz);
      for (const s of [-1, 1]) setBox(this.mDiff, di++, cx, Y.cellTop + 0.008, cz + s * dz * 0.22, w - 0.3, 0.016, dz * 0.3);
      const nf = Math.max(2, Math.min(28, Math.round(c.w / 0.92)));
      for (let f = 0; f < nf; f++) setBox(this.mPoly, pi++, c.x + ((f + 0.5) * c.w) / nf, Y.cellTop + 0.03, cz, 0.09, 0.04, RH - 0.9);
    });
    this.mLogic.count = n; this.mDiff.count = di; this.mPoly.count = pi;
    this.design.phys.forEach((p, i) => {
      const tap = p.type.startsWith("tap");
      setBox(this.mPhys, i, p.x + p.w / 2, 0.1, this.sz(p.y + RH / 2), p.w - 0.05, tap ? 0.22 : 0.14, RH - 0.5);
      this.mPhys.setColorAt(i, new THREE.Color(tap ? 0x3a4c66 : p.type.startsWith("decap") ? 0x253141 : 0x1c2532));
    });
    this.mPhys.count = this.design.phys.length;
    this.updateTop();

    /* routes from the DEF: wires per layer (base + glow), vias */
    const segsBy = new Map<string, Seg[]>();
    const viasBy = new Map<string, { x: number; z: number }[]>();
    for (const rt of this.design.routes) {
      if (rt.pts.length === 2) {
        if (!WIRE[rt.layer]) continue;
        const [[x1, y1], [x2, y2]] = rt.pts;
        if (!segsBy.has(rt.layer)) segsBy.set(rt.layer, []);
        segsBy.get(rt.layer)!.push({ net: rt.net, layer: rt.layer, x1, z1: this.sz(y1), x2, z2: this.sz(y2) });
      } else if (rt.pts.length === 1 && VIA[rt.layer]) {
        if (!viasBy.has(rt.layer)) viasBy.set(rt.layer, []);
        viasBy.get(rt.layer)!.push({ x: rt.pts[0][0], z: this.sz(rt.pts[0][1]) });
      }
    }
    for (const [layer, segs] of segsBy) {
      const spec = WIRE[layer];
      // Slightly see-through metal, so the cells underneath stay readable.
      const base = inst(box, irid({ color: new THREE.Color(css(spec.v)), transparent: true, opacity: layer === "met1" ? 0.62 : 0.78 }), segs.length, this.G[spec.g]);
      const gl = inst(box, glow(), segs.length, this.G[spec.g]);
      segs.forEach((s, i) => {
        const horiz = Math.abs(s.x2 - s.x1) >= Math.abs(s.z2 - s.z1);
        const len = Math.abs(horiz ? s.x2 - s.x1 : s.z2 - s.z1) + spec.w;
        setBox(base, i, (s.x1 + s.x2) / 2, spec.y, (s.z1 + s.z2) / 2, horiz ? len : spec.w, 0.06, horiz ? spec.w : len);
      });
      base.count = segs.length; base.instanceMatrix.needsUpdate = true;
      this.wires.set(layer, { base, glow: gl, segs, glowSegs: [] });
    }
    for (const [layer, vs] of viasBy) {
      const spec = VIA[layer];
      const m = inst(box, std({ color: 0xc9d3e3, metalness: 0.9, roughness: 0.2 }), vs.length, this.G[spec.g]);
      vs.forEach((v, i) => setBox(m, i, v.x, spec.y, v.z, spec.s, layer === "mcon" ? 0.08 : 0.16, spec.s));
      m.count = vs.length;
    }
    for (const p of this.design.power ?? []) {
      const [[x1, y1], [x2, y2]] = p.pts;
      const horiz = Math.abs(x2 - x1) >= Math.abs(y2 - y1);
      const len = Math.abs(horiz ? x2 - x1 : y2 - y1);
      const mat = std({ color: p.layer === "met5" ? 0xb69344 : 0xc9a24a, metalness: 0.9, roughness: 0.3, transparent: true, opacity: 0.55 });
      add(this.G.power, mat, horiz ? len : p.width, 0.12, horiz ? p.width : len, (x1 + x2) / 2, p.layer === "met5" ? Y.m5 : Y.m4 + 0.08, this.sz((y1 + y2) / 2));
    }

    const edges = new THREE.EdgesGeometry(box);
    this.selBox = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color: new THREE.Color(css("--accent")).multiplyScalar(2.4), toneMapped: false }));
    this.selBox.visible = false; this.G.cell.add(this.selBox);
    this.hovBox = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color: new THREE.Color(0xffffff).multiplyScalar(0.85), toneMapped: false }));
    this.hovBox.visible = false; this.G.cell.add(this.hovBox);
    const tagEl = document.createElement("div"); tagEl.className = "celltag"; tagEl.hidden = true;
    this.tagObj = new CSS2DObject(tagEl); this.G.cell.add(this.tagObj);
    for (const [g, text, y] of [["cell", "cells · transistors", Y.cellTop], ["m1", "met1 · power rails + local wires", Y.m1], ["m2", "met2 · vertical wires", Y.m2], ["m3", "met3 · long wires", Y.m3], ["m4", "met4", Y.m4]] as [Group, string, number][]) {
      const el = document.createElement("div"); el.className = "layerlabel"; el.textContent = text; el.style.opacity = "0";
      const o = new CSS2DObject(el); o.position.set(-3, y, this.H * 0.55); o.userData.layerLabel = true; this.G[g].add(o);
    }
    this.chip.traverse((o) => {
      const m = o as THREE.Mesh;
      if (!m.isMesh || Array.isArray(m.material) || !(m.material as THREE.MeshStandardMaterial).isMeshStandardMaterial || (m.material as THREE.Material).transparent) return;
      m.castShadow = true; m.receiveShadow = true;
    });
    for (const L of LAYERS) this.setLayer(L.key, L.on);

    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(this.host); this.resize();
    this.bindPointer();
    this.flyTo(null, { dir: new THREE.Vector3(...OVERVIEW_DIR).normalize(), instant: true });
    this.startIntro();
  }

  private colourFor(c: DesignCell): THREE.Color {
    if (this.colourBy === "module") {
      const key = c.module ?? "?";
      if (!this.moduleColours.has(key)) {
        const hues = [0.08, 0.52, 0.74, 0.95, 0.33, 0.62, 0.15];
        const i = this.moduleColours.size;
        this.moduleColours.set(key, key === this.design.source.top ? new THREE.Color(css("--clock")) : new THREE.Color().setHSL(hues[i % hues.length], 0.55, 0.52));
      }
      return this.moduleColours.get(key)!.clone();
    }
    return new THREE.Color(css(CATEGORY_VAR[category(c.type, this.lib[c.type])]));
  }
  moduleColour(mod: string): string {
    const c = this.moduleColours.get(mod);
    return c ? `#${c.getHexString()}` : "#888";
  }
  categoryOf(c: DesignCell): Category { return category(c.type, this.lib[c.type]); }

  setColourBy(mode: "function" | "module") {
    this.colourBy = mode;
    this.cellColour = this.cells.map((c) => this.colourFor(c));
    this.dirtyColours = true;
  }

  private refreshColours() {
    if (!this.cellColour.length) this.cellColour = this.cells.map((c) => this.colourFor(c));
    const dim = new THREE.Color(0x1a2230), tmp = new THREE.Color();
    this.cells.forEach((c, i) => {
      tmp.copy(this.cellColour[i]);
      if (this.highlight && !this.highlight.has(c.id)) tmp.lerp(dim, 0.82);
      else if (this.highlight) tmp.multiplyScalar(1.25);
      this.mLogic.setColorAt(i, tmp);
    });
    this.mLogic.instanceColor!.needsUpdate = true;
    for (const w of this.wires.values()) {
      w.segs.forEach((s, i) => {
        const on = !this.hlNets || this.hlNets.has(s.net);
        w.base.setColorAt(i, tmp.setScalar(on ? 1 : 0.18));
      });
      w.base.instanceColor!.needsUpdate = true;
    }
  }

  private updateTop() {
    const RH = this.RH;
    this.cells.forEach((c, i) => {
      const net = this.outNet.get(c.id);
      const on = net ? this.value(net) === 1 : false;
      const cz = this.sz(c.y + RH / 2), dz = RH - 0.5;
      if (on) setBox(this.mTop, i, c.x + c.w / 2, Y.cellTop + 0.05, cz - dz / 2 + 0.2, c.w - 0.25, 0.04, 0.24);
      else setBox(this.mTop, i, 0, -50, 0, 1e-4, 1e-4, 1e-4);
      this.mTop.setColorAt(i, new THREE.Color(css(isClockNet(net ?? "") ? "--clock" : "--high")).multiplyScalar(2.2));
    });
    this.mTop.count = this.cells.length; this.mTop.instanceMatrix.needsUpdate = true;
    if (this.mTop.instanceColor) this.mTop.instanceColor.needsUpdate = true;
  }

  private updateGlow(now = performance.now()) {
    const high = new THREE.Color(css("--high")), clk = new THREE.Color(css("--clock")), col = new THREE.Color();
    for (const w of this.wires.values()) {
      const spec = WIRE[w.segs[0]?.layer ?? "met1"];
      let k = 0; w.glowSegs = [];
      for (const s of w.segs) {
        if (this.value(s.net) !== 1) continue;
        if (this.hlNets && !this.hlNets.has(s.net)) continue;
        const horiz = Math.abs(s.x2 - s.x1) >= Math.abs(s.z2 - s.z1);
        const len = Math.abs(horiz ? s.x2 - s.x1 : s.z2 - s.z1) + spec.w + 0.04;
        setBox(w.glow, k, (s.x1 + s.x2) / 2, spec.y + 0.012, (s.z1 + s.z2) / 2, horiz ? len : spec.w + 0.04, 0.07, horiz ? spec.w + 0.04 : len);
        const f = this.flash.get(s.net);
        const boost = f ? Math.max(0, 1 - (now - f) / 600) : 0;
        col.copy(isClockNet(s.net) ? clk : high).multiplyScalar(1.6 + boost * 3.2);
        w.glow.setColorAt(k++, col);
        w.glowSegs.push(s);
      }
      w.glow.count = k; w.glow.instanceMatrix.needsUpdate = true;
      if (w.glow.instanceColor) w.glow.instanceColor.needsUpdate = true;
    }
    for (const b of this.bond) b.mat.emissiveIntensity = this.value(b.net) ? 1.4 : 0;
    for (const [name, el] of this.pinEls) el.classList.toggle("on", this.value(name) === 1);
  }

  /** Called after every simulation step. */
  setValues(get: (net: string) => number) {
    this.value = get;
    const now = performance.now();
    const nets = new Set<string>();
    for (const w of this.wires.values()) for (const s of w.segs) nets.add(s.net);
    for (const n of nets) {
      const v = get(n);
      if (v === 1 && this.prev.get(n) === 0 && !reduceMotion()) this.flash.set(n, now);
      this.prev.set(n, v);
    }
    this.updateTop(); this.updateGlow(now);
  }

  setHighlight(ids: Set<string> | null, nets: Set<string> | null = null) {
    this.highlight = ids && ids.size ? ids : null;
    this.hlNets = nets && nets.size ? nets : null;
    this.dirtyColours = true;
    this.updateGlow();
  }

  select(id: string | null) {
    this.selected = id;
    this.placeOutline(this.selBox, id, 0.14);
    const el = this.tagObj.element as HTMLElement;
    if (id && this.cellIdx.has(id)) {
      const c = this.cells[this.cellIdx.get(id)!];
      el.textContent = `${c.id} · ${c.type}`; el.hidden = false;
      this.tagObj.position.set(c.x + c.w / 2, Y.cellTop + 0.6, this.sz(c.y + this.RH / 2));
    } else el.hidden = true;
  }

  private placeOutline(obj: THREE.LineSegments, id: string | null, pad: number) {
    const c = id ? this.cells[this.cellIdx.get(id) ?? -1] : undefined;
    obj.visible = !!c;
    if (!c) return;
    obj.scale.set(c.w + pad, Y.cellTop + 0.2, this.RH - 0.3 + pad);
    obj.position.set(c.x + c.w / 2, (Y.cellTop + 0.2) / 2, this.sz(c.y + this.RH / 2));
  }

  setLayer(k: LayerKey, on: boolean) {
    const groups: Group[] = k === "via" ? ["via", "via2", "via3"] : k === "cell" ? ["cell"] : [k as Group];
    for (const g of groups) this.G[g].visible = on;
    if (k === "pkg") this.G.sub.visible = true;
  }
  layerOn(k: LayerKey) { return this.G[(k === "via" ? "via" : k) as Group].visible; }
  setExplode(x: number) { this.explode = Math.max(0, Math.min(1, x)); }
  get exploded() { return this.explode; }
  setQuality(hq: boolean) {
    this.renderer.setPixelRatio(hq ? Math.min(devicePixelRatio, 2) : 1);
    this.composer.setPixelRatio(hq ? Math.min(devicePixelRatio, 2) : 1);
    this.bloom.enabled = hq;
    this.resize();
  }

  flyTo(ids: string[] | null, opts: { dir?: THREE.Vector3; instant?: boolean } = {}) {
    if (!this.ok) return;
    const box = new THREE.Box3();
    if (!ids || !ids.length) box.set(new THREE.Vector3(-8, 0, -8), new THREE.Vector3(this.W + 8, 1, this.H + 8));
    else for (const id of ids) {
      const c = this.cells[this.cellIdx.get(id) ?? -1];
      if (!c) continue;
      box.expandByPoint(new THREE.Vector3(c.x, 0, this.sz(c.y + this.RH)));
      box.expandByPoint(new THREE.Vector3(c.x + c.w, 1, this.sz(c.y)));
    }
    if (box.isEmpty()) return;
    const c = box.getCenter(new THREE.Vector3()).add(this.chip.position);
    c.y += this.explodeCur * 6;
    const size = box.getSize(new THREE.Vector3());
    const vt = Math.tan((this.camera.fov * Math.PI) / 360), ht = vt * this.camera.aspect;
    const fit = Math.max(size.x / (2 * ht), (size.z * 0.85) / (2 * vt), 7);
    const dist = fit * (ids?.length ? 1.3 : 1.05) + 4 + this.explodeCur * 30;
    const dir = opts.dir ?? this.camera.position.clone().sub(this.controls.target).normalize();
    if (dir.y < 0.45) { dir.y = 0.75; dir.normalize(); }
    this.fly.fromP.copy(this.camera.position); this.fly.fromT.copy(this.controls.target);
    this.fly.toT.copy(c); this.fly.toP.copy(c).addScaledVector(dir, dist);
    this.fly.t = 0;
    if (opts.instant || reduceMotion()) { this.camera.position.copy(this.fly.toP); this.controls.target.copy(this.fly.toT); this.fly.t = 1; }
  }

  startIntro() {
    if (reduceMotion()) return;
    this.intro.toP.copy(this.camera.position); this.intro.toT.copy(this.controls.target);
    this.intro.fromP.copy(this.intro.toT).add(new THREE.Vector3(0.1, 1, 0.25).normalize().multiplyScalar(this.intro.toP.distanceTo(this.intro.toT) * 2.1));
    this.camera.position.copy(this.intro.fromP);
    this.intro.on = true; this.intro.t0 = performance.now() + 150;
  }

  private resize() {
    const w = this.host.clientWidth, h = this.host.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false); this.composer.setSize(w, h); this.labels.setSize(w, h);
    this.bloom.resolution.set(w, h);
    this.camera.aspect = w / Math.max(h, 1); this.camera.updateProjectionMatrix();
  }

  private bindPointer() {
    const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
    const el = this.renderer.domElement;
    let down: { x: number; y: number } | null = null, pending = false, last: PointerEvent | null = null;
    const pick = (ev: PointerEvent | MouseEvent) => {
      const r = el.getBoundingClientRect();
      ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, this.camera);
      // Logic cells win over the wires drawn above them (shift-click picks the wire); physical cells come last.
      const wires = [...this.wires.values()].filter((w) => w.base.parent?.visible).map((w) => w.base);
      const order: THREE.Object3D[][] = this.G.cell.visible ? (ev.shiftKey ? [wires, [this.mLogic], [this.mPhys]] : [[this.mLogic], wires, [this.mPhys]]) : [wires];
      for (const targets of order) {
        const hit = targets.length ? ray.intersectObjects(targets, false)[0] : undefined;
        if (!hit || hit.instanceId === undefined) continue;
        if (hit.object === this.mLogic) return { kind: "cell" as const, id: this.cells[hit.instanceId].id };
        if (hit.object === this.mPhys) return { kind: "phys" as const, p: this.design.phys[hit.instanceId] };
        for (const w of this.wires.values()) if (hit.object === w.base) return { kind: "net" as const, net: w.segs[hit.instanceId].net };
      }
      return null;
    };
    el.addEventListener("pointerdown", (e) => { down = { x: e.clientX, y: e.clientY }; this.lastInput = performance.now(); if (this.intro.on) this.endIntro(); });
    el.addEventListener("wheel", () => { this.lastInput = performance.now(); if (this.intro.on) this.endIntro(); }, { passive: true });
    el.addEventListener("pointerup", (e) => {
      if (!down || Math.hypot(e.clientX - down.x, e.clientY - down.y) > 5) return;
      const t0 = performance.now();
      const h = pick(e);
      this.lastPickMs = performance.now() - t0;
      if (!h) this.cb.onCell(null);
      else if (h.kind === "cell") this.cb.onCell(h.id);
      else if (h.kind === "net") this.cb.onNet(h.net);
      else this.cb.onPhys(h.p);
    });
    el.addEventListener("pointerleave", () => { this.tip.hidden = true; this.setHover(null); });
    el.addEventListener("pointermove", (e) => {
      last = e; if (pending) return; pending = true;
      requestAnimationFrame(() => {
        pending = false; if (!last) return;
        const h = pick(last);
        el.style.cursor = h ? "pointer" : "grab";
        this.setHover(h?.kind === "cell" ? h.id : null);
        if (h) {
          let t = "";
          if (h.kind === "cell") { const c = this.cells[this.cellIdx.get(h.id)!]; const o = this.outNet.get(c.id); t = `${c.id} <small>${c.type}</small>${o ? ` · out <b>${this.value(o)}</b>` : ""}`; }
          else if (h.kind === "net") t = `wire ${h.net} · <b>${this.value(h.net)}</b>`;
          else t = `${h.p.type} <small>physical only</small>`;
          this.tip.innerHTML = t; this.tip.hidden = false;
          const r = this.host.getBoundingClientRect();
          this.tip.style.left = `${last.clientX - r.left}px`; this.tip.style.top = `${last.clientY - r.top}px`;
        } else this.tip.hidden = true;
      });
    });
  }
  private setHover(id: string | null) {
    if (id === this.hover) return;
    this.hover = id; this.placeOutline(this.hovBox, id && id !== this.selected ? id : null, 0.08);
  }

  private endIntro() {
    if (!this.intro.on) return;
    this.intro.on = false; this.camera.position.copy(this.intro.toP); this.controls.target.copy(this.intro.toT);
  }

  private tick = () => {
    const now = performance.now();
    if (this.intro.on) {
      const u = Math.min(1, Math.max(0, (now - this.intro.t0) / this.intro.dur));
      this.camera.position.lerpVectors(this.intro.fromP, this.intro.toP, ease(u));
      if (u >= 1) this.intro.on = false;
    } else if (this.fly.t < 1) {
      this.fly.t = Math.min(1, this.fly.t + this.fly.dur);
      const e = ease(this.fly.t);
      this.camera.position.lerpVectors(this.fly.fromP, this.fly.toP, e); this.controls.target.lerpVectors(this.fly.fromT, this.fly.toT, e);
    }
    if (Math.abs(this.explode - this.explodeCur) > 0.001) {
      this.explodeCur += (this.explode - this.explodeCur) * (reduceMotion() ? 1 : 0.12);
      for (const [k, i] of Object.entries(EXPLODE_IDX)) this.G[k as Group].position.y = this.explodeCur * i * 4;
      this.chip.traverse((o) => { if (o.userData.layerLabel) ((o as CSS2DObject).element.style.opacity = String(Math.min(1, this.explodeCur * 3))); });
    }
    if (this.dirtyColours) { this.dirtyColours = false; this.refreshColours(); }
    if (this.flash.size) {
      let live = false;
      for (const [n, t] of this.flash) { if (now - t > 600) this.flash.delete(n); else live = true; }
      if (live || this.flash.size === 0) this.updateGlow(now);
    }
    if (this.selBox.visible) (this.selBox.material as THREE.LineBasicMaterial).color.copy(new THREE.Color(css("--accent"))).multiplyScalar(1.8 + Math.sin(now / 260) * 0.6);
    this.controls.autoRotate = !reduceMotion() && !this.intro.on && this.fly.t >= 1 && now - this.lastInput > 45000;
    this.controls.update();
    this.composer.render();
    this.labels.render(this.scene, this.camera);
  };

  /** Client (page) coordinates of a cell's top centre, for tests and tours. */
  screenPos(id: string): { x: number; y: number } | null {
    const c = this.cells[this.cellIdx.get(id) ?? -1];
    if (!c) return null;
    const b = cellBox(c, this.H, this.RH);
    const p = new THREE.Vector3(b.x, b.y + b.sy / 2 + this.G.cell.position.y, b.z).add(this.chip.position).project(this.camera);
    const r = this.renderer.domElement.getBoundingClientRect();
    return { x: r.left + ((p.x + 1) / 2) * r.width, y: r.top + ((1 - p.y) / 2) * r.height };
  }
  get busy() { return this.intro.on || this.fly.t < 1; }

  start() { if (this.ok && !this.running) { this.running = true; this.resize(); this.renderer.setAnimationLoop(this.tick); } }
  stop() { if (this.ok && this.running) { this.running = false; this.renderer.setAnimationLoop(null); } }
  noteInput() { this.lastInput = performance.now(); }
}
