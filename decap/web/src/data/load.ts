// Static-first data loading: /data/* (generated into public/data) first, then the API, then the dev fixture.
// Anything that came from the fixture is flagged so the UI can say so on screen.

import type { Bench, ChatReply, ChipResult, CompareResult, Design, FixResult, Lib } from "./types";
import fixtureBench from "../fixtures/bench.json";
import fixtureCounter from "../fixtures/chips/tt_um_fixture_counter4.json";
import fixturePlanted from "../fixtures/chips/tt_um_fixture_counter4.planted.json";

export const FIXTURE_CHIPS: Record<string, ChipResult> = {
  [fixtureCounter.chip]: fixtureCounter as unknown as ChipResult,
};
export const fixtureUsed = new Set<string>();
export type Source = "static" | "api" | "fixture" | "preplanted";
const listeners: (() => void)[] = [];
export const onFixtureChange = (f: () => void) => listeners.push(f);
const markFixture = (what: string) => { fixtureUsed.add(what); listeners.forEach((f) => f()); };

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  const ct = res.headers.get("content-type") ?? "";
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    if (ct.includes("json")) { try { msg = (await res.json()).error ?? msg; } catch { /* keep status text */ } }
    throw new ApiError(res.status, msg);
  }
  if (!ct.includes("json")) throw new ApiError(404, `${url} is not JSON (missing file?)`);
  return res.json() as Promise<T>;
}

export const post = <T>(path: string, body: unknown, signal?: AbortSignal) =>
  getJson<T>(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), signal });

async function firstOf<T>(tries: [Source, () => Promise<T>][]): Promise<{ data: T; source: Source }> {
  let last: unknown;
  for (const [source, f] of tries) {
    try { return { data: await f(), source }; } catch (e) { last = e; }
  }
  throw last;
}

export async function loadDesign(): Promise<{ design: Design; lib: Lib }> {
  const [design, lib] = await Promise.all([getJson<Design>("/data/design.json"), getJson<Lib>("/data/lib.json")]);
  return { design, lib };
}
let libCache: Promise<Lib> | null = null;
export const loadLib = () => (libCache ??= getJson<Lib>("/data/lib.json").catch(() => ({}) as Lib));

let benchCache: Promise<{ data: Bench; source: Source }> | null = null;
export function loadBench(): Promise<{ data: Bench; source: Source }> {
  return (benchCache ??= firstOf<Bench>([
    ["static", () => getJson("/data/bench.json")],
    ["api", () => getJson("/api/bench")],
    ["fixture", async () => { markFixture("bench"); return fixtureBench as unknown as Bench; }],
  ]));
}

const chipCache = new Map<string, Promise<{ data: ChipResult; source: Source }>>();
export function loadChip(chip: string): Promise<{ data: ChipResult; source: Source }> {
  if (!chipCache.has(chip)) {
    chipCache.set(chip, firstOf<ChipResult>([
      ["static", () => getJson(`/data/chips/${encodeURIComponent(chip)}.json`)],
      ["api", () => getJson(`/api/chip/${encodeURIComponent(chip)}`)],
      ["fixture", async () => {
        const f = FIXTURE_CHIPS[chip];
        if (!f) throw new ApiError(404, `No result for ${chip} yet (not in public/data, the API or the fixture).`);
        markFixture(`chip ${chip}`); return f;
      }],
    ]));
    chipCache.get(chip)!.catch(() => chipCache.delete(chip));
  }
  return chipCache.get(chip)!;
}

/** POST /api/plant. If the API is down: the backend's pre-planted copy (/data/chips/<chip>__planted.json, its own N,
 *  labelled as such on screen), or for the dev fixture chip a fixture planted copy relabelled with n. */
export async function plant(chip: string, n: number, signal?: AbortSignal): Promise<{ data: ChipResult; source: Source }> {
  try { return { data: await post<ChipResult>("/api/plant", { chip, n }, signal), source: "api" }; }
  catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    try { return { data: await getJson<ChipResult>(`/data/chips/${encodeURIComponent(chip)}__planted.json`), source: "preplanted" }; } catch { /* none */ }
    if (!FIXTURE_CHIPS[chip]) throw e;
    markFixture("plant");
    const s = JSON.stringify(fixturePlanted).replaceAll("8'd137", `8'd${n}`).replace(/"n": ?137/, `"n":${n}`);
    return { data: JSON.parse(s) as ChipResult, source: "fixture" };
  }
}

export async function fix(chip: string, n: number): Promise<{ data: FixResult; source: Source }> {
  try { return { data: await post<FixResult>("/api/fix", { chip, n }), source: "api" }; }
  catch (e) {
    if (!FIXTURE_CHIPS[chip]) throw e;
    markFixture("fix");
    const p = fixturePlanted as unknown as ChipResult;
    return {
      data: { removed_cells: p.plant?.inserted_cells ?? [], verdict: "proven", detail: "FIXTURE: made-up result. The flagged cone was removed and the rest proven equal to the clean decompile.", seconds: 0.9, verilog: FIXTURE_CHIPS[chip].decompiled.verilog },
      source: "fixture",
    };
  }
}

export const compare = (body: { verilog?: string; netlist?: string; chip?: string }) => post<CompareResult>("/api/compare", body);
export const chat = (messages: { role: string; content: string }[], context: unknown, signal?: AbortSignal) =>
  post<ChatReply>("/api/chat", { messages, context }, signal);
export const health = () => getJson<{ ok: boolean; yosys?: boolean; llm?: boolean }>("/api/health");
