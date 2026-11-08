// The redstone build in the browser: every block of the layout as an instanced voxel, with live lamps and levers.
// It draws exactly the block list the bridge places in Minecraft, so it doubles as the no-Minecraft fallback.
// With /mc/ assets (npm run mc-assets) every block uses its real Minecraft model and texture; without them, flat colours.

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { RsLayout } from "../data/redstone";
import { bakeState, loadMcAssets, McMaterials, type McAssets } from "./mcmodels";

// Minecraft's own concrete colours (each routed net gets one colour of support blocks in the build).
const CONCRETE: Record<string, number> = {
  white: 0xcfd5d6, orange: 0xe06101, magenta: 0xa9309f, light_blue: 0x2489c7, yellow: 0xf1af15, lime: 0x5ea918,
  pink: 0xd5658f, gray: 0x373a3e, light_gray: 0x7d7d73, cyan: 0x157788, purple: 0x64209c, blue: 0x2d2f8f,
  brown: 0x603c20, green: 0x495b24, red: 0x8e2121, black: 0x14161b,
};
const LAMP_OFF = 0x6b4a2a, LAMP_ON = 0xffd27a, LEVER_OFF = 0x8b6a3c, LEVER_ON = 0xff4a2a;

function look(state: string): { color: number; h: number; w: number } | null {
  const name = state.split("[")[0].replace(/^minecraft:/, "");
  if (name.endsWith("_concrete")) return { color: CONCRETE[name.slice(0, -9)] ?? 0x888888, h: 1, w: 1 };
  if (name === "redstone_wire") return { color: 0xb3241a, h: 0.1, w: 0.62 };
  if (name === "repeater") return { color: 0xb9b9b9, h: 0.16, w: 1 };
  if (name === "comparator") return { color: 0xd8cfc4, h: 0.16, w: 1 };
  if (name === "redstone_block") return { color: 0xd0261a, h: 1, w: 1 };
  if (name === "stone" || name === "smooth_stone") return { color: 0x6f6f6f, h: 1, w: 1 };
  if (name.includes("sign") || name === "air") return null;
  return { color: 0x9a9a9a, h: 1, w: 1 };
}

/** Set one property of a block state string: withProp("lever[facing=north]", "powered", "true"). */
function withProp(state: string, k: string, v: string): string {
  const [name, rest] = state.split("[");
  const props = (rest ?? "").replace("]", "").split(",").filter((kv) => kv && !kv.startsWith(`${k}=`));
  return `${name}[${[...props, `${k}=${v}`].join(",")}]`;
}

/** The same blocks drawn twice (off and on); each instance shows in exactly one of the two meshes. */
class Toggle {
  private m = new THREE.Matrix4();
  private hide = new THREE.Matrix4().makeScale(0, 0, 0);
  constructor(private off: THREE.InstancedMesh, private on: THREE.InstancedMesh, private at: THREE.Vector3[]) {
    at.forEach((_, i) => this.set(i, false));
    this.flush();
  }
  set(i: number, lit: boolean) {
    const p = this.at[i];
    if (!p) return;
    this.m.makeTranslation(p.x, p.y, p.z);
    this.off.setMatrixAt(i, lit ? this.hide : this.m);
    this.on.setMatrixAt(i, lit ? this.m : this.hide);
  }
  flush() { this.off.instanceMatrix.needsUpdate = true; this.on.instanceMatrix.needsUpdate = true; }
}

export class Redstone3D {
  readonly el: HTMLElement;
  private renderer: THREE.WebGLRenderer | null = null;
  private scene = new THREE.Scene();
  private camera = new THREE.PerspectiveCamera(40, 1, 0.5, 6000);
  private controls: OrbitControls | null = null;
  private groups: THREE.Object3D[] = [];
  private lamps = new Map<string, { mesh: THREE.InstancedMesh; idx: number[] }>();
  private levers: { mesh: THREE.InstancedMesh; bits: string[] } | null = null;
  private running = false;
  private ro: ResizeObserver;
  private ray = new THREE.Raycaster();
  onLever?: (bit: string) => void;
  ok = true;
  private mc: McAssets | null = null;
  private mats: McMaterials | null = null;
  private lay: RsLayout | null = null;
  private painted: [Record<string, number>, Record<string, number>] | null = null;
  private mcLamps: Toggle | null = null;
  private mcLevers: Toggle | null = null;

  constructor() {
    this.el = document.createElement("div");
    this.el.className = "rs3d";
    const canvas = document.createElement("canvas");
    this.el.append(canvas);
    try {
      this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: "high-performance" });
      this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    } catch { this.ok = false; this.el.innerHTML = `<div class="empty">WebGL is not available in this browser.</div>`; }
    this.scene.background = new THREE.Color(0x0b0b0d);
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x30303a, 1.6));
    const sun = new THREE.DirectionalLight(0xffffff, 1.4);
    sun.position.set(-0.4, 1, 0.6);
    this.scene.add(sun);
    if (this.renderer) {
      this.controls = new OrbitControls(this.camera, this.renderer.domElement);
      this.controls.enableDamping = true;
      this.controls.screenSpacePanning = false;
      this.controls.maxPolarAngle = Math.PI * 0.48;
      canvas.addEventListener("click", (e) => this.pick(e));
    }
    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(this.el);
    if (this.renderer) void loadMcAssets().then((a) => {
      if (!a) return;
      this.mc = a;
      this.mats = new McMaterials(this.renderer!.capabilities.getMaxAnisotropy());
      if (this.lay) { this.build(this.lay); if (this.painted) this.paint(...this.painted); }
    });
  }

  /** Replace the scene with this layout. Coordinates are world blocks, so x/z match what players see in Minecraft. */
  setLayout(lay: RsLayout) {
    this.lay = lay; this.painted = null;
    this.build(lay);
    this.overview(lay);
  }

  private build(lay: RsLayout) {
    for (const g of this.groups) { this.scene.remove(g); (g as THREE.Mesh).geometry?.dispose(); }
    this.groups = []; this.lamps.clear(); this.levers = null; this.mcLamps = null; this.mcLevers = null;
    const [x0, , z0, x1, , z1] = lay.bbox;
    const cx = (x0 + x1) / 2, cz = (z0 + z1) / 2;
    const all = [...lay.blocks, ...lay.last];
    const lampAt = new Map<string, number>();
    const leverAt = new Map<string, string>();
    for (const [b, p] of Object.entries(lay.inputs)) leverAt.set(`${p[0]},${p[1]},${p[2]}`, b);
    const solid: { x: number; y: number; z: number; c: number; h: number; w: number }[] = [];
    const lampList: { x: number; y: number; z: number }[] = [];
    const leverList: { x: number; y: number; z: number; bit: string; s: string }[] = [];
    const byState = new Map<string, [number, number, number][]>();     // textured mode: instances per block state
    for (const [x, y, z, s] of all) {
      const key = `${x},${y},${z}`;
      if (s.startsWith("redstone_lamp")) { lampAt.set(key, lampList.length); lampList.push({ x, y, z }); continue; }
      if (s.startsWith("lever")) { const bit = leverAt.get(key); if (bit) leverList.push({ x, y, z, bit, s }); continue; }
      if (this.mc && !s.includes("sign") && s !== "air") {
        const st = s.replace(/^minecraft:/, "");
        let list = byState.get(st);
        if (!list) byState.set(st, (list = []));
        list.push([x, y, z]);
        continue;
      }
      const l = look(s);
      if (l) solid.push({ x, y, z, c: l.color, h: l.h, w: l.w });
    }
    // levers the layout does not list as blocks (prebuilt chips): put them at their input positions
    const have = new Set(leverList.map((l) => l.bit));
    for (const [b, p] of Object.entries(lay.inputs)) if (!have.has(b) && !/^HOLD_/.test(b)) leverList.push({ x: p[0], y: p[1], z: p[2], bit: b, s: "lever[face=floor,facing=north]" });

    // textured mode: one instanced mesh per distinct block state, built from its real Minecraft model
    const mcMesh = (state: string, at: { x: number; y: number; z: number }[]): THREE.InstancedMesh | null => {
      const baked = this.mc && this.mats ? bakeState(this.mc, state) : null;
      if (!baked) return null;
      const mesh = new THREE.InstancedMesh(baked.geometry, baked.textures.map((t) => this.mats!.get(t)), Math.max(1, at.length));
      const m = new THREE.Matrix4();
      at.forEach((p, i) => mesh.setMatrixAt(i, m.makeTranslation(p.x - cx, p.y, p.z - cz)));
      mesh.count = at.length;
      mesh.frustumCulled = false;
      this.add(mesh);
      return mesh;
    };
    for (const [st, at] of byState) {
      if (mcMesh(st, at.map(([x, y, z]) => ({ x, y, z })))) continue;
      const l = look(st);                                         // a block the asset set does not cover
      if (l) for (const [x, y, z] of at) solid.push({ x, y, z, c: l.color, h: l.h, w: l.w });
    }

    const box = new THREE.BoxGeometry(1, 1, 1);
    box.translate(0, 0.5, 0);                                    // instances stand on their y
    const m4 = new THREE.Matrix4(), col = new THREE.Color();
    const place = (mesh: THREE.InstancedMesh, i: number, x: number, y: number, z: number, w: number, h: number) => {
      m4.makeScale(w, h, w).setPosition(x - cx, y, z - cz);
      mesh.setMatrixAt(i, m4);
    };
    const mat = new THREE.MeshLambertMaterial();
    const solidMesh = new THREE.InstancedMesh(box, mat, Math.max(1, solid.length));
    solid.forEach((b, i) => { place(solidMesh, i, b.x, b.y, b.z, b.w, b.h); solidMesh.setColorAt(i, col.setHex(b.c)); });
    solidMesh.count = solid.length;
    this.add(solidMesh);

    if (this.mc) {
      const at = (l: { x: number; y: number; z: number }[]) => l.map((p) => new THREE.Vector3(p.x - cx, p.y, p.z - cz));
      const lampOff = mcMesh("redstone_lamp[lit=false]", lampList), lampOn = mcMesh("redstone_lamp[lit=true]", lampList);
      if (lampOff && lampOn) this.mcLamps = new Toggle(lampOff, lampOn, at(lampList));
      const lever = leverList[0]?.s ?? "lever[face=floor,facing=north]";   // the bridge gives every lever the same facing
      const leverOff = mcMesh(withProp(lever, "powered", "false"), leverList), leverOn = mcMesh(withProp(lever, "powered", "true"), leverList);
      if (leverOff && leverOn) this.mcLevers = new Toggle(leverOff, leverOn, at(leverList));
    }

    const glow = new THREE.MeshBasicMaterial({ visible: !this.mcLamps });
    const lampMesh = new THREE.InstancedMesh(box, glow, Math.max(1, lampList.length));
    lampList.forEach((b, i) => { place(lampMesh, i, b.x, b.y, b.z, 1, 1); lampMesh.setColorAt(i, col.setHex(LAMP_OFF)); });
    lampMesh.count = lampList.length;
    this.add(lampMesh);
    // each output bit lights the lamp nearest its probe block
    for (const [bit, o] of Object.entries(lay.outputs)) {
      if (!Array.isArray(o)) continue;
      let best = -1, bd = 1e9;
      lampList.forEach((l, i) => { const d = Math.abs(l.x - o[0]) + Math.abs(l.y - o[1]) + Math.abs(l.z - o[2]); if (d < bd) { bd = d; best = i; } });
      if (best >= 0 && bd <= 4) this.lamps.set(bit, { mesh: lampMesh, idx: [best] });
    }

    const leverMesh = new THREE.InstancedMesh(box, new THREE.MeshBasicMaterial({ visible: !this.mcLevers }), Math.max(1, leverList.length));   // still the click target
    leverList.forEach((b, i) => { place(leverMesh, i, b.x, b.y, b.z, 0.7, 0.7); leverMesh.setColorAt(i, col.setHex(LEVER_OFF)); });
    leverMesh.count = leverList.length;
    this.add(leverMesh);
    this.levers = { mesh: leverMesh, bits: leverList.map((l) => l.bit) };

    const w = x1 - x0 + 40, d = z1 - z0 + 40;
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(w, d), new THREE.MeshLambertMaterial({ color: 0x1b1b1f }));
    floor.rotation.x = -Math.PI / 2; floor.position.y = -1.01;
    this.add(floor);
    const grid = new THREE.GridHelper(Math.max(w, d), Math.round(Math.max(w, d) / 16), 0x2a2a30, 0x202025);
    grid.position.y = -1;
    this.add(grid);
    this.overview(lay);
  }

  private add(o: THREE.Object3D) { this.scene.add(o); this.groups.push(o); }

  private bboxSize = { w: 1, d: 1 };
  overview(lay?: RsLayout) {
    if (lay) { const [x0, , z0, x1, , z1] = lay.bbox; this.bboxSize = { w: x1 - x0, d: z1 - z0 }; }
    const { w, d } = this.bboxSize;
    const r = Math.max(w * 0.62, d * 1.1, 60);
    this.camera.position.set(-w * 0.08, r * 0.75, d * 0.5 + r * 0.55);
    this.controls?.target.set(0, 0, 0);
    this.controls?.update();
  }

  /** Paint live state: lit lamps and thrown levers. */
  paint(outputs: Record<string, number>, inputs: Record<string, number>) {
    this.painted = [outputs, inputs];
    if (this.mcLamps) {
      for (const [bit, l] of this.lamps) for (const i of l.idx) this.mcLamps.set(i, !!outputs[bit]);
      this.mcLamps.flush();
    }
    if (this.mcLevers && this.levers) {
      this.levers.bits.forEach((b, i) => this.mcLevers!.set(i, !!inputs[b]));
      this.mcLevers.flush();
    }
    const col = new THREE.Color();
    for (const [bit, l] of this.lamps) {
      for (const i of l.idx) l.mesh.setColorAt(i, col.setHex(outputs[bit] ? LAMP_ON : LAMP_OFF));
      if (l.mesh.instanceColor) l.mesh.instanceColor.needsUpdate = true;
    }
    if (this.levers) {
      this.levers.bits.forEach((b, i) => this.levers!.mesh.setColorAt(i, col.setHex(inputs[b] ? LEVER_ON : LEVER_OFF)));
      if (this.levers.mesh.instanceColor) this.levers.mesh.instanceColor.needsUpdate = true;
    }
  }

  private pick(e: MouseEvent) {
    if (!this.levers || !this.renderer || !this.onLever) return;
    const r = this.renderer.domElement.getBoundingClientRect();
    this.ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), this.camera);
    const hit = this.ray.intersectObject(this.levers.mesh)[0];
    if (hit?.instanceId !== undefined) this.onLever(this.levers.bits[hit.instanceId]);
  }

  private resize() {
    if (!this.renderer) return;
    const w = this.el.clientWidth, h = this.el.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  private tick = () => { this.controls?.update(); this.renderer?.render(this.scene, this.camera); };
  start() { if (this.renderer && !this.running) { this.running = true; this.resize(); this.renderer.setAnimationLoop(this.tick); } }
  stop() { if (this.renderer && this.running) { this.running = false; this.renderer.setAnimationLoop(null); } }
  destroy() { this.stop(); this.ro.disconnect(); this.renderer?.dispose(); }
}
