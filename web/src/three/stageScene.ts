// The Stage's fixed parts: layer tables, the finishing shader, small instancing helpers, and the backdrop and floor the
// package sits on. Stage3D (stage3d.ts) builds the chip from a design.json with these.

import * as THREE from "three";
import { Y } from "./geometry";

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
export type Group = "pkg" | "sub" | "cell" | "m1" | "via" | "m2" | "via2" | "m3" | "via3" | "m4" | "power";
export const EXPLODE_IDX: Record<Group, number> = { pkg: 0, sub: 0, cell: 1, m1: 2, via: 3, m2: 4, via2: 5, m3: 6, via3: 7, m4: 8, power: 9 };
export const WIRE: Record<string, { g: Group; y: number; w: number; v: string }> = {
  met1: { g: "m1", y: Y.m1, w: 0.2, v: "--met1" },
  met2: { g: "m2", y: Y.m2, w: 0.2, v: "--met2" },
  met3: { g: "m3", y: Y.m3, w: 0.34, v: "--met3" },
  met4: { g: "m4", y: Y.m4, w: 0.34, v: "--met4" },
};
export const VIA: Record<string, { g: Group; y: number; s: number }> = {
  mcon: { g: "m1", y: Y.m1 - 0.06, s: 0.19 },
  via: { g: "via", y: Y.via, s: 0.24 },
  via2: { g: "via2", y: Y.via2, s: 0.3 },
  via3: { g: "via3", y: Y.via3, s: 0.36 },
  via4: { g: "via3", y: Y.via3, s: 0.36 },
};
export const isClockNet = (n: string) => /^(clk|clknet_)/.test(n);
/** Microns each layer group lifts per EXPLODE_IDX step when the layers are fully separated. */
export const LIFT = 7;
/** Layer groups that get a glass plate and a label when the stack is separated. */
export const PLATES: [Group, string][] = [["cell", "Cells and transistors"], ["m1", "Metal 1 \u00b7 power rails, local wires"], ["m2", "Metal 2 \u00b7 vertical wires"], ["m3", "Metal 3 \u00b7 long wires"], ["m4", "Metal 4"]];
/** Default hero angle: the overview direction turned a little, so the die reads in three-quarter view. */
export const HERO_DIR = new THREE.Vector3(-0.3, 0.8, 0.62).normalize();

/** Fullscreen finish after bloom: a soft vignette and a whisper of grain (hides gradient banding). */
export const FinishShader = {
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

export interface Seg { net: string; x1: number; z1: number; x2: number; z2: number; layer: string }

const M4 = new THREE.Matrix4(), V3 = new THREE.Vector3(), S3 = new THREE.Vector3(), Q4 = new THREE.Quaternion();
export function setBox(mesh: THREE.InstancedMesh, i: number, x: number, y: number, z: number, sx: number, sy: number, sz: number) {
  V3.set(x, y, z); S3.set(sx, sy, sz); M4.compose(V3, Q4, S3); mesh.setMatrixAt(i, M4);
}
export const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

/** A radial gradient for the scene background (drawn screen-space, so it never moves with the camera). */
export function backdrop(bg: THREE.Color): THREE.Texture {
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
export function floor(size: number): THREE.Object3D {
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
