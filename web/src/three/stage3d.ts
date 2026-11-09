// The Stage: a real placed-and-routed die in 3D (ported from docs/decap/prototype.html, driven by design.json).
// Scene units are microns. Scene x = DEF x, scene z = die height - DEF y (so the layout reads as drawn).

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { CSS2DObject, CSS2DRenderer } from "three/examples/jsm/renderers/CSS2DRenderer.js";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { ShaderPass } from "three/examples/jsm/postprocessing/ShaderPass.js";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import type { Design, DesignCell, Lib } from "../api/types";
import { category, CATEGORY_VAR, type Category } from "../lib/cells";
import { css, reduceMotion } from "../ui/dom";
import { cellBox, Y, usedArea, type Rect } from "./geometry";

export type LayerKey = "pkg" | "cell" | "m1" | "via" | "m2" | "m3" | "m4" | "power";
export const LAYERS: { key: LayerKey; label: string; colour: string; on: boolean }[] = [
  { key: "power", label: "Power straps", colour: "var(--met4)", on: false },
  { key: "m4", label: "Metal 4", colour: "var(--met4)", on: true },
  { key: "m3", label: "Metal 3", colour: "var(--met3)", on: true },
  { key: "m2", label: "Metal 2", colour: "var(--met2)", on: true },
  { key: "via", label: "Vias", colour: "var(--via)", on: true },
  { key: "m1", label: "Metal 1 and rails", colour: "var(--met1)", on: true },
  { key: "cell", label: "Cells", colour: "var(--adder)", on: true },
  { key: "pkg", label: "Package", colour: "var(--pkg)", on: true },
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
/** Microns each layer group lifts per EXPLODE_IDX step when the layers are fully separated. */
const LIFT = 7;
/** Layer groups that get a glass plate and a label when the stack is separated. */
const PLATES: [Group, string][] = [["cell", "Cells and transistors"], ["m1", "Metal 1 \u00b7 power rails, local wires"], ["m2", "Metal 2 \u00b7 vertical wires"], ["m3", "Metal 3 \u00b7 long wires"], ["m4", "Metal 4"]];
/** Default hero angle: the overview direction turned a little, so the die reads in three-quarter view. */
const HERO_DIR = new THREE.Vector3(-0.3, 0.8, 0.62).normalize();

/** Fullscreen finish after bloom: a soft vignette and a whisper of grain (hides gradient banding). */
const FinishShader = {
  uniforms: { tDiffuse: { value: null as THREE.Texture | null }, uTime: { value: 0 }, uVignette: { value: 0.42 } },
  vertexShader: `varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
  fragmentShader: `
    uniform sampler2D tDiffuse; uniform float uTime; uniform float uVignette; varying vec2 vUv;
    float hash(vec2 p) { return fract(sin(dot(p, vec2(12.9898, 78.233)) + uTime) * 43758.5453); }
    void main() {
      vec4 c = texture2D(tDiffuse, vUv);
      vec2 d = vUv - 0.5; d.x *= 1.25;
      c.rgb *= mix(1.0, 1.0 - smoothstep(0.18, 0.85, length(d)), uVignette);
      c.rgb += (hash(vUv * 1000.0) - 0.5) * 0.012;
      gl_FragColor = c;
    }`,
};

export interface StageCallbacks {
  onCell(id: string | null): void;
  onNet(net: string): void;
  onPort(name: string): void;
  onPhys(p: Design["phys"][number]): void;
  onHover?(text: string | null): void;
  /** The logic cell under the pointer (or null), with viewport coords — the Witness agent hover toolbar. */
  onHoverCell?(id: string | null, x: number, y: number): void;
}

interface Seg { net: string; x1: number; z1: number; x2: number; z2: number; layer: string }

const M4 = new THREE.Matrix4(), V3 = new THREE.Vector3(), S3 = new THREE.Vector3(), Q4 = new THREE.Quaternion();
function setBox(mesh: THREE.InstancedMesh, i: number, x: number, y: number, z: number, sx: number, sy: number, sz: number) {
  V3.set(x, y, z); S3.set(sx, sy, sz); M4.compose(V3, Q4, S3); mesh.setMatrixAt(i, M4);
}
const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

export class Stage3D {
  readonly W: number; readonly H: number; readonly RH: number;
  /** The used part of the die (DEF microns); the scene draws only this, not the whole Tiny Tapeout tile. */
  readonly V: Rect;
  /** Physical (filler/tap/decap) cells inside V, in mPhys instance order. */
  private physShown: Design["phys"] = [];
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
  /** Lift of the stack centre already applied to the camera (so separating the layers keeps them in frame). */
  private liftApplied = 0;
  private plates: { g: Group; mat: THREE.MeshBasicMaterial; edge: THREE.LineBasicMaterial; label: HTMLElement }[] = [];
  private finish!: ShaderPass;
  /** Shared time uniform for the moving pulses on wires that carry a 1. */
  private uTime = { value: 0 };
  private beam!: THREE.Mesh;
  private fly = { t: 1, fromP: new THREE.Vector3(), toP: new THREE.Vector3(), fromT: new THREE.Vector3(), toT: new THREE.Vector3(), dur: 0.035 };
  private intro = { on: false, t0: 0, dur: 3200, fromP: new THREE.Vector3(), toP: new THREE.Vector3(), toT: new THREE.Vector3() };
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
    this.V = usedArea(design);
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
    const { RH, V } = this;
    // The used rectangle in scene space: width W, depth H, with its top-left corner at (x0, z0).
    const W = V.x1 - V.x0, H = V.y1 - V.y0, x0 = V.x0, z0 = this.sz(V.y1), xc = x0 + W / 2, zc = z0 + H / 2;
    const r = this.renderer;
    r.setPixelRatio(Math.min(devicePixelRatio, 2));
    r.shadowMap.enabled = true; r.shadowMap.type = THREE.PCFSoftShadowMap;
    r.toneMapping = THREE.ACESFilmicToneMapping; r.toneMappingExposure = 1.25;
    const bg = new THREE.Color(css("--bg"));
    // Studio backdrop: a soft cool pool of light behind the chip, falling off to the page colour at the edges.
    this.scene.background = this.backdrop(bg);
    this.scene.fog = new THREE.FogExp2(bg.clone().lerp(new THREE.Color(0x1a2233), 0.35), 0.0022);
    const pmrem = new THREE.PMREMGenerator(r);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    pmrem.dispose();
    this.controls = new OrbitControls(this.camera, r.domElement);
    Object.assign(this.controls, { enableDamping: true, dampingFactor: 0.08, maxPolarAngle: Math.PI * 0.48, minDistance: 4, maxDistance: 420, autoRotateSpeed: 0.4 });
    const lab = document.createElement("div"); lab.className = "labels"; this.host.append(lab);
    this.labels = new CSS2DRenderer({ element: lab });
    this.composer = new EffectComposer(r, new THREE.WebGLRenderTarget(2, 2, { type: THREE.HalfFloatType, samples: 4 }));
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(2, 2), 0.75, 0.5, 0.95);
    this.composer.addPass(this.bloom);
    this.finish = new ShaderPass(FinishShader);
    this.composer.addPass(this.finish);
    this.composer.addPass(new OutputPass());

    this.scene.add(new THREE.HemisphereLight(0xa8c0ff, 0x1a1410, 0.45));
    const key = new THREE.DirectionalLight(0xfff1dc, 2.1); key.position.set(-70, 130, 90);
    key.castShadow = true; key.shadow.mapSize.set(2048, 2048);
    Object.assign(key.shadow.camera, { left: -130, right: 130, top: 130, bottom: -130, near: 20, far: 420 });
    key.shadow.camera.updateProjectionMatrix(); key.shadow.bias = -0.0004; key.shadow.normalBias = 0.05; key.shadow.radius = 4;
    this.scene.add(key);
    // A cool rim from behind picks out the package edges and the bond wires; a warm fill keeps the front from going flat.
    const rim = new THREE.DirectionalLight(0x6a8cff, 1.1); rim.position.set(90, 40, -110); this.scene.add(rim);
    const fill = new THREE.DirectionalLight(0xffc89a, 0.35); fill.position.set(110, 30, 80); this.scene.add(fill);
    this.scene.add(this.floor(Math.max(W, H)));

    this.chip.position.set(-xc, 0, -zc); this.scene.add(this.chip);
    for (const k of Object.keys(EXPLODE_IDX) as Group[]) { this.G[k] = new THREE.Group(); this.chip.add(this.G[k]); }
    const box = new THREE.BoxGeometry(1, 1, 1);
    const std = (o: THREE.MeshStandardMaterialParameters = {}) => new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.5, metalness: 0.1, envMapIntensity: 0.55, ...o });
    const irid = (o: THREE.MeshPhysicalMaterialParameters = {}) => new THREE.MeshPhysicalMaterial({ color: 0xffffff, metalness: 0.85, roughness: 0.25, iridescence: 0.9, iridescenceIOR: 1.5, iridescenceThicknessRange: [180, 640], envMapIntensity: 0.95, ...o });
    // Wires that carry a 1 glow, with bright pulses travelling along them (frozen when the viewer asks for less motion).
    const uTime = this.uTime;
    const glow = (pulse = false) => {
      const m = new THREE.MeshBasicMaterial({ color: 0xffffff, toneMapped: false });
      if (pulse) m.onBeforeCompile = (sh) => {
        sh.uniforms.uTime = uTime;
        sh.vertexShader = sh.vertexShader.replace("#include <common>", "#include <common>\nvarying vec3 vW;")
          .replace("#include <project_vertex>", "#include <project_vertex>\nvW = (modelMatrix * instanceMatrix * vec4(position, 1.0)).xyz;");
        sh.fragmentShader = sh.fragmentShader.replace("#include <common>", "#include <common>\nvarying vec3 vW; uniform float uTime;")
          .replace("vec4 diffuseColor = vec4( diffuse, opacity );", "vec4 diffuseColor = vec4( diffuse, opacity );\nfloat ph = fract((vW.x + vW.z) * 0.09 - uTime * 0.55);\ndiffuseColor.rgb *= 0.7 + 1.5 * pow(smoothstep(0.0, 0.92, ph) * (1.0 - smoothstep(0.92, 1.0, ph)), 5.0);");
      };
      return m;
    };
    const add = (g: THREE.Object3D, mat: THREE.Material, sx: number, sy: number, sz: number, x: number, y: number, z: number) => {
      const m = new THREE.Mesh(box, mat); m.scale.set(sx, sy, sz); m.position.set(x, y, z); g.add(m); return m;
    };
    const inst = (geo: THREE.BufferGeometry, mat: THREE.Material, n: number, parent: THREE.Object3D) => {
      const m = new THREE.InstancedMesh(geo, mat, Math.max(1, n)); m.frustumCulled = false; m.setColorAt(0, new THREE.Color(1, 1, 1)); m.count = 0; parent.add(m); return m;
    };

    /* package: epoxy, cavity, lead frame, gold bond wires */
    // A snug package: leads start LEAD um beyond the die edge and run LEN um out.
    const LEAD = 8, LEN = 8;
    // Moulded epoxy body with softened edges, so the rim light can catch them; a pin-1 dimple in one corner.
    const PW = W + 2 * (LEAD + LEN + 2), PD = H + 2 * (LEAD + LEN + 2);
    const body = new THREE.Mesh(new RoundedBoxGeometry(PW, 3, PD, 3, 1.1), new THREE.MeshPhysicalMaterial({ color: 0x0d1117, roughness: 0.62, metalness: 0, clearcoat: 0.35, clearcoatRoughness: 0.55, envMapIntensity: 0.5 }));
    body.position.set(xc, -2.2, zc); this.G.pkg.add(body);
    const dimple = new THREE.Mesh(new THREE.CircleGeometry(1.6, 32), std({ color: 0x07090c, roughness: 0.9, metalness: 0 }));
    dimple.rotation.x = -Math.PI / 2; dimple.position.set(xc - PW / 2 + 4.5, -0.69, zc - PD / 2 + 4.5); this.G.pkg.add(dimple);
    add(this.G.pkg, std({ color: 0x161b23, roughness: 0.95, metalness: 0 }), W + 2 * (LEAD - 1), 0.05, H + 2 * (LEAD - 1), xc, -0.68, zc);
    add(this.G.pkg, std({ color: 0x8b919b, metalness: 0.9, roughness: 0.35 }), W + 6, 0.08, H + 6, xc, -0.62, zc);
    const leadMat = std({ color: 0xc2ab84, metalness: 0.9, roughness: 0.3 });
    const goldMat = std({ color: 0xe0b45a, metalness: 0.95, roughness: 0.22, envMapIntensity: 0.9 });
    const ports = this.design.ports;
    const pads = [
      ...ports.map((p) => ({ name: p.name, label: p.label ?? p.name, dir: p.dir as string, x: p.x, y: p.y })),
      { name: "VPWR", label: "VPWR", dir: "pwr", x: x0 + W * 0.35, y: V.y1 - 0.4 }, { name: "VGND", label: "VGND", dir: "pwr", x: x0 + W * 0.65, y: V.y0 + 0.4 },
    ];
    for (const p of pads) {
      const px = this.sx(p.x), pz = this.sz(p.y);
      const left = p.x < x0 + W * 0.25, right = p.x > x0 + W * 0.75, top = p.y > V.y0 + H * 0.75;
      const dx = left ? -1 : right ? 1 : 0, dz = dx ? 0 : top ? -1 : 1;
      const padX = px + dx * -1.6, padZ = pz + dz * -1.6;
      add(this.G.pkg, goldMat, 2.4, 0.16, 2.4, padX, 0.08, padZ);
      const lx = dx ? (dx < 0 ? x0 - LEAD : x0 + W + LEAD) : padX, lz = dz ? (dz < 0 ? z0 - LEAD : z0 + H + LEAD) : padZ + (padZ - zc) * 0.12;
      const lead = new THREE.Vector3(lx, -0.55, dx ? zc + (padZ - zc) * 1.12 : lz);
      add(this.G.pkg, leadMat, dx ? LEN : 1.4, 0.08, dx ? 1.4 : LEN, lead.x + (dx * LEN) / 2, -0.6, lead.z + (dz * LEN) / 2);
      const a = new THREE.Vector3(padX, 0.18, padZ);
      const out = lead.clone().sub(a).setY(0).normalize();
      const curve = new THREE.CubicBezierCurve3(a, a.clone().add(out.clone().multiplyScalar(1.5)).setY(8), lead.clone().sub(out.clone().multiplyScalar(3)).setY(4.5), lead);
      const wm = std({ color: 0xe6b95c, metalness: 0.95, roughness: 0.2, envMapIntensity: 1, emissive: new THREE.Color(css("--high")), emissiveIntensity: 0 });
      this.G.pkg.add(new THREE.Mesh(new THREE.TubeGeometry(curve, 48, 0.12, 8, false), wm));
      if (p.dir !== "pwr") this.bond.push({ net: p.name, mat: wm });
      const el = document.createElement("div");
      el.className = "pinlabel" + (p.dir === "pwr" ? " pwr" : "");
      el.textContent = p.label;
      if (p.dir !== "pwr") {
        el.title = p.dir === "out" ? `Output ${p.label !== p.name ? `${p.label} (${p.name})` : p.name}: click to see everything that drives it` : `Input ${p.label !== p.name ? `${p.label} (${p.name})` : p.name}: click to see everything it reaches`;
        el.addEventListener("click", () => this.cb.onPort(p.name));
        this.pinEls.set(p.name, el);
      } else el.style.pointerEvents = "none";
      const o = new CSS2DObject(el);
      o.position.set(padX + dx * 7, 1.2, padZ + dz * 7);
      this.G.pkg.add(o);
    }

    /* die: oxide-coated silicon, seal ring, rows of wells, met1 power rails */
    add(this.G.sub, new THREE.MeshPhysicalMaterial({ color: 0x151d29, metalness: 0.5, roughness: 0.3, iridescence: 1, iridescenceIOR: 1.9, iridescenceThicknessRange: [280, 1150], envMapIntensity: 1 }), W, 0.4, H, xc, -0.2, zc);
    const seal = irid({ color: 0xb8c2d2, emissive: new THREE.Color(0x3a4a66), emissiveIntensity: 0.35 });
    add(this.G.sub, seal, W - 1, 0.1, 0.5, xc, 0.05, z0 + 1); add(this.G.sub, seal, W - 1, 0.1, 0.5, xc, 0.05, z0 + H - 1);
    add(this.G.sub, seal, 0.5, 0.1, H - 1, x0 + 1, 0.05, zc); add(this.G.sub, seal, 0.5, 0.1, H - 1, x0 + W - 1, 0.05, zc);
    const die = this.design.core ?? { x0: 0, y0: 0, x1: this.W, y1: this.H };
    // Wells and rails run the width of the core, cut to the used rectangle.
    const core = { x0: Math.max(die.x0, x0 + 1.5), x1: Math.min(die.x1, x0 + W - 1.5) };
    const cw = core.x1 - core.x0;
    const inV = (y: number) => y >= V.y0 && y <= V.y1;
    const well = std({ color: 0x22314a, roughness: 0.8 }), well2 = std({ color: 0x1b2638, roughness: 0.85 });
    const rows = this.design.rows ?? [];
    for (const row of rows) {
      if (!inV(row.y) || !inV(row.y + RH)) continue;
      const flip = row.orient.includes("S");
      add(this.G.sub, flip ? well2 : well, cw, 0.02, RH / 2 - 0.1, core.x0 + cw / 2, 0.045, this.sz(row.y + RH * 0.75));
      add(this.G.sub, flip ? well : well2, cw, 0.02, RH / 2 - 0.1, core.x0 + cw / 2, 0.045, this.sz(row.y + RH * 0.25));
    }
    const railY = new Set<number>(); rows.forEach((r0) => { railY.add(+r0.y.toFixed(3)); railY.add(+(r0.y + RH).toFixed(3)); });
    let k = 0;
    for (const y of [...railY].filter(inV).sort((a, b) => a - b)) add(this.G.m1, irid({ color: k++ % 2 ? 0x3d5f9c : 0x324c80 }), cw, 0.05, 0.48, core.x0 + cw / 2, Y.m1, this.sz(y));

    /* cells: body, diffusion, poly fingers, top strip that glows when the output is 1 */
    const n = this.cells.length;
    this.mLogic = inst(box, new THREE.MeshPhysicalMaterial({ roughness: 0.38, metalness: 0.08, clearcoat: 0.9, clearcoatRoughness: 0.18, sheen: 0.4, sheenRoughness: 0.4, envMapIntensity: 0.75 }), n, this.G.cell);
    this.mTop = inst(box, glow(), n, this.G.cell);
    // Diffusion takes a darker shade of its cell's colour (set in refreshColours), so cells read by function from afar.
    this.mDiff = inst(box, std({ roughness: 0.5, metalness: 0.05 }), n * 2, this.G.cell);
    this.mPoly = inst(box, std({ roughness: 0.3, metalness: 0.35, color: new THREE.Color(css("--poly")).lerp(new THREE.Color(0xffffff), 0.15) }), n * 30, this.G.cell);
    this.physShown = this.design.phys.filter((p) => p.x >= V.x0 && p.x + p.w <= V.x1 && inV(p.y) && inV(p.y + RH));
    this.mPhys = inst(box, std({ roughness: 0.8 }), this.physShown.length, this.G.cell);
    let pi = 0, di = 0;
    this.cells.forEach((c, i) => {
      const b = cellBox(c, this.H, RH);
      const cx = b.x, cz = b.z, w = b.sx, dz = b.sz;
      setBox(this.mLogic, i, b.x, b.y, b.z, b.sx, b.sy, b.sz);
      for (const s of [-1, 1]) setBox(this.mDiff, di++, cx, Y.cellTop + 0.008, cz + s * dz * 0.22, w - 0.3, 0.016, dz * 0.3);
      const nf = Math.max(2, Math.min(28, Math.round(c.w / 0.92)));
      for (let f = 0; f < nf; f++) setBox(this.mPoly, pi++, c.x + ((f + 0.5) * c.w) / nf, Y.cellTop + 0.03, cz, 0.07, 0.04, RH - 1.1);
    });
    this.mLogic.count = n; this.mDiff.count = di; this.mPoly.count = pi;
    this.physShown.forEach((p, i) => {
      const tap = p.type.startsWith("tap");
      setBox(this.mPhys, i, p.x + p.w / 2, 0.1, this.sz(p.y + RH / 2), p.w - 0.05, tap ? 0.22 : 0.14, RH - 0.5);
      this.mPhys.setColorAt(i, new THREE.Color(tap ? 0x3a4c66 : p.type.startsWith("decap") ? 0x253141 : 0x1c2532));
    });
    this.mPhys.count = this.physShown.length;
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
      const gl = inst(box, glow(true), segs.length, this.G[spec.g]);
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
      const m = inst(box, std({ color: new THREE.Color(css("--via")), metalness: 0.9, roughness: 0.2 }), vs.length, this.G[spec.g]);
      vs.forEach((v, i) => setBox(m, i, v.x, spec.y, v.z, spec.s, layer === "mcon" ? 0.08 : 0.16, spec.s));
      m.count = vs.length;
    }
    const clip = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
    for (const p of this.design.power ?? []) {
      let [[x1, y1], [x2, y2]] = p.pts;
      const horiz = Math.abs(x2 - x1) >= Math.abs(y2 - y1);
      // Power stripes cross the whole tile: keep the ones that cross the used rectangle, cut to it.
      if (horiz ? !inV(y1) : x1 < V.x0 || x1 > V.x1) continue;
      if (horiz) { x1 = clip(x1, V.x0, V.x1); x2 = clip(x2, V.x0, V.x1); } else { y1 = clip(y1, V.y0, V.y1); y2 = clip(y2, V.y0, V.y1); }
      const len = Math.abs(horiz ? x2 - x1 : y2 - y1);
      const mat = std({ color: p.layer === "met5" ? new THREE.Color(css("--pkg")).multiplyScalar(0.9) : new THREE.Color(css("--pkg")), metalness: 0.9, roughness: 0.3, transparent: true, opacity: 0.55 });
      add(this.G.power, mat, horiz ? len : p.width, 0.12, horiz ? p.width : len, (x1 + x2) / 2, p.layer === "met5" ? Y.m5 : Y.m4 + 0.08, this.sz((y1 + y2) / 2));
    }

    const edges = new THREE.EdgesGeometry(box);
    this.selBox = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color: new THREE.Color(css("--accent")).multiplyScalar(2.4), toneMapped: false }));
    this.selBox.visible = false; this.G.cell.add(this.selBox);
    this.hovBox = new THREE.LineSegments(edges, new THREE.LineBasicMaterial({ color: new THREE.Color(0xffffff).multiplyScalar(0.85), toneMapped: false }));
    this.hovBox.visible = false; this.G.cell.add(this.hovBox);
    const tagEl = document.createElement("div"); tagEl.className = "celltag"; tagEl.hidden = true;
    this.tagObj = new CSS2DObject(tagEl); this.G.cell.add(this.tagObj);
    // Separated view: each layer floats on a faint glass plate with its name at the front-left corner.
    const plateGeo = new THREE.PlaneGeometry(W + 4, H + 4); plateGeo.rotateX(-Math.PI / 2);
    const plateEdges = new THREE.EdgesGeometry(plateGeo);
    const tint: Partial<Record<Group, string>> = { cell: "--adder", m1: "--met1", m2: "--met2", m3: "--met3", m4: "--met4" };
    for (const [g, text] of PLATES) {
      const y = g === "cell" ? Y.cellBot - 0.02 : (WIRE[{ m1: "met1", m2: "met2", m3: "met3", m4: "met4" }[g as "m1"]]?.y ?? 0) - 0.05;
      const col = new THREE.Color(css(tint[g]!) || "#888");
      const mat = new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0, depthWrite: false, side: THREE.DoubleSide });
      const plate = new THREE.Mesh(plateGeo, mat); plate.position.set(xc, y, zc); plate.visible = false; plate.renderOrder = -1; this.G[g].add(plate);
      const edge = new THREE.LineBasicMaterial({ color: col.clone().multiplyScalar(1.6), transparent: true, opacity: 0, toneMapped: false });
      const ln = new THREE.LineSegments(plateEdges, edge); ln.position.copy(plate.position); ln.visible = false; this.G[g].add(ln);
      plate.userData.plate = ln.userData.plate = true;
      const el = document.createElement("div"); el.className = "layerlabel"; el.style.opacity = "0";
      el.innerHTML = `<i style="display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:6px;background:${css(tint[g]!)}"></i>${text}`;
      const o = new CSS2DObject(el); o.position.set(x0 + Math.min(14, W * 0.12), y, z0 + H + 3); this.G[g].add(o);
      this.plates.push({ g, mat, edge, label: el });
    }
    // Selection beacon: a soft column of light above the selected cell, so it can be found from any distance.
    const beamGeo = new THREE.CylinderGeometry(0.5, 0.5, 1, 24, 1, true); beamGeo.translate(0, 0.5, 0);
    this.beam = new THREE.Mesh(beamGeo, new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide, toneMapped: false,
      uniforms: { uCol: { value: new THREE.Color(css("--accent")).lerp(new THREE.Color(0xffffff), 0.25).multiplyScalar(2.4) }, uTime: this.uTime },
      vertexShader: `varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: `uniform vec3 uCol; uniform float uTime; varying vec2 vUv;
        void main() { float a = pow(clamp(1.0 - vUv.y, 0.0, 1.0), 2.2) * (0.55 + 0.15 * sin(uTime * 3.0 - vUv.y * 12.0)); gl_FragColor = vec4(uCol * a, a); }`,
    }));
    this.beam.visible = false; this.beam.renderOrder = 2; this.G.cell.add(this.beam);
    this.chip.traverse((o) => {
      const m = o as THREE.Mesh;
      if (!m.isMesh || Array.isArray(m.material) || !(m.material as THREE.MeshStandardMaterial).isMeshStandardMaterial || (m.material as THREE.Material).transparent) return;
      m.castShadow = true; m.receiveShadow = true;
    });
    for (const L of LAYERS) this.setLayer(L.key, L.on);

    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(this.host); this.resize();
    this.bindPointer();
    this.flyTo(null, { dir: HERO_DIR.clone(), instant: true });
    this.startIntro();
  }

  /** A radial gradient for the scene background (drawn screen-space, so it never moves with the camera). */
  private backdrop(bg: THREE.Color): THREE.Texture {
    const cv = document.createElement("canvas"); cv.width = 512; cv.height = 512;
    const g = cv.getContext("2d")!;
    const hex = (c: THREE.Color) => `#${c.getHexString()}`;
    const grad = g.createRadialGradient(256, 210, 10, 256, 256, 380);
    grad.addColorStop(0, hex(bg.clone().lerp(new THREE.Color(0x2a3550), 0.55)));
    grad.addColorStop(0.45, hex(bg.clone().lerp(new THREE.Color(0x182033), 0.5)));
    grad.addColorStop(1, hex(bg));
    g.fillStyle = grad; g.fillRect(0, 0, 512, 512);
    const t = new THREE.CanvasTexture(cv); t.colorSpace = THREE.SRGBColorSpace;
    return t;
  }

  /** The bench the package sits on: a dark plane that takes the shadow, with a fine dot grid that fades out with distance. */
  private floor(size: number): THREE.Object3D {
    const g = new THREE.Group();
    const shadow = new THREE.Mesh(new THREE.PlaneGeometry(1600, 1600), new THREE.ShadowMaterial({ opacity: 0.55 }));
    shadow.rotation.x = -Math.PI / 2; shadow.position.y = -3.69; shadow.receiveShadow = true; g.add(shadow);
    const R = Math.max(140, size * 1.6);
    const grid = new THREE.Mesh(new THREE.PlaneGeometry(R * 2, R * 2), new THREE.ShaderMaterial({
      transparent: true, depthWrite: false,
      uniforms: { uR: { value: R }, uCol: { value: new THREE.Color(0x7d95c8) } },
      vertexShader: `varying vec2 vP; void main() { vP = position.xy; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: `
        uniform float uR; uniform vec3 uCol; varying vec2 vP;
        void main() {
          vec2 cell = abs(fract(vP / 6.0) - 0.5) * 6.0;
          float dotm = 1.0 - smoothstep(0.14, 0.3, length(cell));
          vec2 line = abs(fract(vP / 30.0) - 0.5) * 30.0;
          float major = 1.0 - smoothstep(0.04, 0.16, min(line.x, line.y));
          float fade = 1.0 - smoothstep(uR * 0.25, uR, length(vP));
          gl_FragColor = vec4(uCol, (dotm * 0.22 + major * 0.07) * fade);
        }`,
    }));
    grid.rotation.x = -Math.PI / 2; grid.position.y = -3.7; g.add(grid);
    return g;
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
      const mk = this.marks?.get(c.id);
      if (mk) tmp.copy(mk).multiplyScalar(1.3);
      if (this.highlight && !this.highlight.has(c.id)) tmp.lerp(dim, 0.82);
      else if (this.highlight) tmp.multiplyScalar(1.25);
      this.mLogic.setColorAt(i, tmp);
      tmp.multiplyScalar(0.62);
      this.mDiff.setColorAt(2 * i, tmp); this.mDiff.setColorAt(2 * i + 1, tmp);
    });
    this.mLogic.instanceColor!.needsUpdate = true;
    this.mDiff.instanceColor!.needsUpdate = true;
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
        col.copy(isClockNet(s.net) ? clk : high).multiplyScalar(1.3 + boost * 3);
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

  /** Per-cell colour marks drawn over the normal colours (the Witness agent's verdicts). */
  private marks: Map<string, THREE.Color> | null = null;
  setMarks(m: Map<string, string> | null) {
    this.marks = m && m.size ? new Map([...m].map(([k, v]) => [k, new THREE.Color(v)])) : null;
    this.dirtyColours = true;
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
      el.innerHTML = `${c.id}<small>${c.type}</small>`; el.hidden = false;
      this.tagObj.position.set(c.x + c.w / 2, Y.cellTop + 0.6, this.sz(c.y + this.RH / 2));
      this.beam.position.set(c.x + c.w / 2, Y.cellTop, this.sz(c.y + this.RH / 2));
      this.beam.scale.set(Math.max(c.w, 1.4) * 0.75, 18, (this.RH - 0.5) * 0.75);
      this.beam.visible = true;
    } else { el.hidden = true; this.beam.visible = false; }
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
    if (!ids || !ids.length) box.set(new THREE.Vector3(this.V.x0 - 8, 0, this.sz(this.V.y1) - 8), new THREE.Vector3(this.V.x1 + 8, 1, this.sz(this.V.y0) + 8));
    else for (const id of ids) {
      const c = this.cells[this.cellIdx.get(id) ?? -1];
      if (!c) continue;
      box.expandByPoint(new THREE.Vector3(c.x, 0, this.sz(c.y + this.RH)));
      box.expandByPoint(new THREE.Vector3(c.x + c.w, 1, this.sz(c.y)));
    }
    if (box.isEmpty()) return;
    const c = box.getCenter(new THREE.Vector3()).add(this.chip.position);
    c.y += this.liftApplied;
    const size = box.getSize(new THREE.Vector3());
    const vt = Math.tan((this.camera.fov * Math.PI) / 360), ht = vt * this.camera.aspect;
    const fit = Math.max(size.x / (2 * ht), (size.z * 0.85) / (2 * vt), 7);
    const dist = fit * (ids?.length ? 1.3 : 1.05) + 4 + this.explodeCur * LIFT * 5;
    const dir = opts.dir ?? this.camera.position.clone().sub(this.controls.target).normalize();
    if (dir.y < 0.45) { dir.y = 0.75; dir.normalize(); }
    if (this.intro.on && !opts.dir) {
      // Still flying in: land the intro on this framing instead of starting a second move afterwards.
      const d = this.intro.toP.clone().sub(this.intro.toT).normalize();
      this.intro.toT.copy(c); this.intro.toP.copy(c).addScaledVector(d, dist);
      return;
    }
    this.fly.fromP.copy(this.camera.position); this.fly.fromT.copy(this.controls.target);
    this.fly.toT.copy(c); this.fly.toP.copy(c).addScaledVector(dir, dist);
    this.fly.t = 0;
    if (opts.instant || reduceMotion()) { this.camera.position.copy(this.fly.toP); this.controls.target.copy(this.fly.toT); this.fly.t = 1; }
  }

  /** Opening shot: the camera swings in from high and to the side while the layers settle onto the die one by one. */
  startIntro() {
    if (reduceMotion()) return;
    this.intro.toP.copy(this.camera.position); this.intro.toT.copy(this.controls.target);
    const off = this.intro.toP.clone().sub(this.intro.toT);
    const sp = new THREE.Spherical().setFromVector3(off);
    sp.theta += 0.9; sp.phi = Math.max(0.12, sp.phi * 0.35); sp.radius *= 2.3;
    this.intro.fromP.copy(this.intro.toT).add(new THREE.Vector3().setFromSpherical(sp));
    this.camera.position.copy(this.intro.fromP);
    this.intro.on = true; this.intro.t0 = performance.now() + 150;
  }
  private introPose(u: number) {
    const e = ease(u);
    const a = new THREE.Spherical().setFromVector3(this.intro.fromP.clone().sub(this.intro.toT));
    const b = new THREE.Spherical().setFromVector3(this.intro.toP.clone().sub(this.intro.toT));
    let dt = b.theta - a.theta; if (dt > Math.PI) dt -= 2 * Math.PI; if (dt < -Math.PI) dt += 2 * Math.PI;
    const s = new THREE.Spherical(a.radius + (b.radius - a.radius) * e, a.phi + (b.phi - a.phi) * e, a.theta + dt * e);
    this.camera.position.copy(this.intro.toT).add(new THREE.Vector3().setFromSpherical(s));
    this.controls.target.copy(this.intro.toT);
    // Layers drop into place bottom-up, each a beat after the one below it.
    for (const [k, i] of Object.entries(EXPLODE_IDX)) {
      const lu = Math.min(1, Math.max(0, (u - 0.1 - i * 0.045) / 0.5));
      this.G[k as Group].position.y = (1 - ease(lu)) * i * LIFT * 1.6;
    }
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
        if (hit.object === this.mPhys) return { kind: "phys" as const, p: this.physShown[hit.instanceId] };
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
        this.cb.onHoverCell?.(h?.kind === "cell" ? h.id : null, last.clientX, last.clientY);
        if (h) {
          let t = "";
          if (h.kind === "cell") { const c = this.cells[this.cellIdx.get(h.id)!]; const o = this.outNet.get(c.id); t = `<span>${c.id}</span><small>${c.type}</small>${o ? `<small>out</small><b>${this.value(o)}</b>` : ""}`; }
          else if (h.kind === "net") t = `<small>wire</small><span>${h.net}</span><b>${this.value(h.net)}</b>`;
          else t = `<span>${h.p.type}</span><small>no logic</small>`;
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
    this.applyExplode(true);
  }

  /** Lift each layer group by the current separation, fade the glass plates in, and keep the stack centred in view. */
  private applyExplode(force = false) {
    const x = this.explodeCur;
    for (const [k, i] of Object.entries(EXPLODE_IDX)) this.G[k as Group].position.y = x * i * LIFT;
    const a = Math.min(1, x * 2.5);
    for (const p of this.plates) {
      p.mat.opacity = a * 0.07; p.edge.opacity = a * 0.55;
      const vis = a > 0.01;
      this.G[p.g].children.forEach((o) => { if (o.userData.plate) o.visible = vis; });
      p.label.style.opacity = String(a);
    }
    // Raise the camera and its target with the middle of the stack, and back off so the taller stack still fits.
    const lift = x * LIFT * 4.5;
    const d = lift - this.liftApplied;
    if (Math.abs(d) > 1e-4 || force) {
      const back = this.camera.position.clone().sub(this.controls.target).normalize().multiplyScalar(d * 2.4);
      this.camera.position.y += d; this.controls.target.y += d;
      this.camera.position.add(back);
      this.liftApplied = lift;
    }
  }

  private tick = () => {
    const now = performance.now();
    if (!reduceMotion()) { this.uTime.value = now / 1000; this.finish.uniforms.uTime.value = (now / 1000) % 10; }
    if (this.intro.on) {
      const u = Math.min(1, Math.max(0, (now - this.intro.t0) / this.intro.dur));
      this.introPose(u);
      if (u >= 1) this.endIntro();
    } else if (this.fly.t < 1) {
      this.fly.t = Math.min(1, this.fly.t + this.fly.dur);
      const e = ease(this.fly.t);
      this.camera.position.lerpVectors(this.fly.fromP, this.fly.toP, e); this.controls.target.lerpVectors(this.fly.fromT, this.fly.toT, e);
    }
    if (!this.intro.on && Math.abs(this.explode - this.explodeCur) > 0.001) {
      this.explodeCur += (this.explode - this.explodeCur) * (reduceMotion() ? 1 : 0.1);
      if (Math.abs(this.explode - this.explodeCur) <= 0.001) this.explodeCur = this.explode;
      this.applyExplode();
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
    if (this.frame++ % 6 === 0) this.declutterPins();
  };

  private frame = 0;
  /** Hide pin labels that would overlap one already placed (lit pins win), so names never print over each other. */
  private declutterPins() {
    const els = [...this.pinEls.values()].sort((a, b) => Number(b.classList.contains("on")) - Number(a.classList.contains("on")));
    const placed: DOMRect[] = [];
    for (const el of els) {
      const r = el.getBoundingClientRect();
      if (!r.width) continue;
      const hit = placed.some((p) => r.left < p.right + 4 && r.right > p.left - 4 && r.top < p.bottom + 2 && r.bottom > p.top - 2);
      if (el.classList.contains("clash") !== hit) el.classList.toggle("clash", hit);
      if (!hit) placed.push(r);
    }
  }

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
  /** Free the WebGL context (browsers allow only a few at once): called when the Stage switches chips. */
  dispose() {
    this.stop();
    this.ro?.disconnect();
    if (this.ok) {
      this.controls?.dispose();
      this.chip?.traverse((o) => {
        const m = o as THREE.Mesh;
        m.geometry?.dispose?.();
        const mat = m.material as THREE.Material | THREE.Material[] | undefined;
        (Array.isArray(mat) ? mat : mat ? [mat] : []).forEach((x) => x.dispose());
      });
      this.renderer.dispose();
      this.renderer.forceContextLoss();
    }
    this.host.replaceChildren();
  }
  noteInput() { this.lastInput = performance.now(); }
}
