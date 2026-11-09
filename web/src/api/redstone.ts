// Client for redstone/bridge.py (proxied at /redstone by Vite): Verilog -> redstone build in a live Minecraft server.

export interface RsCheck { vectors: number; mismatches: number; first_mismatch: number | null; against: string; sequential: boolean }

export interface RsBuild {
  id: string;
  title: string;
  kind: "chip" | "verilog";
  prebuilt: boolean;
  built: boolean;
  inputs: string[];
  outputs: string[];
  sequential: boolean;
  settle_rt: number;
  size: [number, number];
  blocks: number;
  bbox: [number, number, number, number, number, number];
  stats: Record<string, unknown>;
  check?: RsCheck;
  origin?: { chip?: string; variant?: string; n?: number | null; operand?: string };
  prims?: { prims: number; by_kind: Record<string, number> };
  compile_s?: number;
  build_s?: number;
}

/** [x, y, z, block state] in world coordinates (y relative to the floor). */
export type RsBlock = [number, number, number, string];

export interface RsLayout {
  bbox: [number, number, number, number, number, number];
  inputs: Record<string, [number, number, number]>;
  outputs: Record<string, number | null | [number, number, number, string]>;
  labels: [number, number, number, string][];
  blocks: RsBlock[];
  last: RsBlock[];
  settle_rt: number;
}

export interface RsState {
  id: string;
  inputs: Record<string, number>;
  outputs: Record<string, number>;
  expected: Record<string, number>;
  settled: boolean;
  t: number;
}

export interface RsHealth { ok: boolean; players: string; lan_ip: string; game_port: number; version: string }

export interface RsCompileIn { preset?: string; verilog?: string; name?: string; id?: string; title?: string; origin?: RsBuild["origin"] }

async function call<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/redstone/${path}`, body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  let d: unknown = null;
  try { d = await r.json(); } catch { /* the Vite proxy answers HTML or nothing when the bridge is down */ }
  if (!r.ok || d === null) {
    const msg = (d as { error?: string } | null)?.error;
    throw new Error(msg ?? (r.status === 502 || r.status === 504 || d === null
      ? "The redstone bridge is not running. Start it with: cd redstone && python3 bridge.py"
      : `HTTP ${r.status}`));
  }
  return d as T;
}

export const rs = {
  health: () => call<RsHealth>("health"),
  builds: () => call<RsBuild[]>("builds"),
  presets: () => call<{ id: string; title: string }[]>("presets"),
  compilePreset: (preset: string) => call<RsBuild>("compile", { preset }),
  compile: (b: RsCompileIn) => call<RsBuild>("compile", b),
  build: (id: string) => call<RsBuild & { build_s: number; commands: number }>(`build/${id}`, {}),
  layout: (id: string) => call<RsBuild & { layout: RsLayout }>(`layout/${id}`),
  state: (id: string) => call<RsState>(`state/${id}`),
  set: (id: string, bits: Record<string, number>, sprint = true) => call<RsState>(`set/${id}`, { bits, sprint }),
  clock: (id: string, sprint = true) => call<{ ok: boolean; wait_s: number }>(`clock/${id}`, { sprint }),
  camera: (id: string, view: "levers" | "overview" | "side") => call<{ ok: boolean }>(`camera/${id}`, { view }),
  speed: (rate: number) => call<{ ok: boolean }>("speed", { rate }),
};

/** Group bit names into buses: "ui_in[3]" -> ui_in, index 3. Single bits are buses of width 1. */
export function buses(bits: string[]): { name: string; bits: string[] }[] {
  const by = new Map<string, { i: number; b: string }[]>();
  for (const b of bits) {
    const m = b.match(/^(.*)\[(\d+)\]$/);
    const name = m ? m[1] : b, i = m ? +m[2] : 0;
    if (!by.has(name)) by.set(name, []);
    by.get(name)!.push({ i, b });
  }
  return [...by].map(([name, xs]) => ({ name, bits: xs.sort((a, c) => c.i - a.i).map((x) => x.b) }));   // MSB first
}

/** Handoff from the Decompile page: the Verilog to build when the Minecraft page opens. */
export const pendingBuild: { req: RsCompileIn | null } = { req: null };
