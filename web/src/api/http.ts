// The one fetch helper behind every client: static data (/data), the core API (/api), the Witness agent (/witness)
// and the redstone bridge (/redstone). JSON in, JSON out, errors as ApiError with a sentence a person can act on.

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

/** What to tell people when a service is down, and how to start it. */
export const DOWN = {
  core: "bASIC's core API isn't reachable. Start it from the repo root with: uvicorn basic.api.main:app (port 8000).",
  witness: "The agent service isn't reachable. Start it with: basic serve --port 8791",
  redstone: "The redstone bridge is not running. Start it from the repo root with: .venv/bin/python -m minecraft.redstone.bridge",
};

export interface RequestOpts {
  method?: string;
  /** Sent as JSON (implies POST unless `method` says otherwise). */
  body?: unknown;
  signal?: AbortSignal;
  /** The message for a service that doesn't answer (network error, 5xx, or a proxy page instead of JSON). */
  down?: string;
}

const isAbort = (e: unknown) => (e as Error)?.name === "AbortError";

/** fetch() with a JSON body; a network failure becomes ApiError(0, down). Aborts pass through untouched. */
async function send(url: string, o: RequestOpts = {}): Promise<Response> {
  const init: RequestInit = { method: o.method ?? (o.body === undefined ? "GET" : "POST"), signal: o.signal };
  if (o.body !== undefined) { init.headers = { "Content-Type": "application/json" }; init.body = JSON.stringify(o.body); }
  try { return await fetch(url, init); }
  catch (e) { if (isAbort(e)) throw e; throw new ApiError(0, o.down ?? `Couldn't reach ${url}.`); }
}

/** The server's own words for a failed request: `{error}` (core, bridge) or FastAPI's `{detail}`. */
async function failure(res: Response, down?: string): Promise<ApiError> {
  let said = "";
  try {
    const j = await res.json() as { error?: unknown; detail?: unknown };
    const m = j.error ?? j.detail;
    said = typeof m === "string" ? m : m ? JSON.stringify(m) : "";
  } catch { /* not JSON */ }
  return new ApiError(res.status, said || (down && (res.status >= 500 || res.status === 0) ? down : `${res.status} ${res.statusText}`.trim()));
}

/** Request JSON. Throws ApiError for HTTP errors and for answers that aren't JSON (a missing static file, a proxy page). */
export async function request<T>(url: string, o: RequestOpts = {}): Promise<T> {
  const res = await send(url, o);
  if (!res.ok) throw await failure(res, o.down);
  if (!(res.headers.get("content-type") ?? "").includes("json")) throw new ApiError(404, o.down ?? `${url} is not JSON (missing file?)`);
  return res.json() as Promise<T>;
}

export const getJson = <T>(url: string, signal?: AbortSignal) => request<T>(url, { signal });
export const postJson = <T>(url: string, body: unknown, signal?: AbortSignal, down?: string) => request<T>(url, { body, signal, down });

/** True when `url` answers 2xx (health checks). */
export const reachable = (url: string) => fetch(url).then((r) => r.ok, () => false);

/** POST JSON and hand each Server-Sent Event's `data:` payload to `onEvent` as it arrives. */
export async function stream<E>(url: string, body: unknown, onEvent: (e: E) => void, signal?: AbortSignal, down?: string): Promise<void> {
  const res = await send(url, { body, signal, down });
  if (!res.ok || !res.body) throw await failure(res, down);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (line) onEvent(JSON.parse(line.slice(6)) as E);
    }
  }
}
