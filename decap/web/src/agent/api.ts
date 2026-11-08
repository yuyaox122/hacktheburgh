// Client for the Witness agent API (decap/agent: `decap serve`, proxied at /witness by vite.config.ts).
// The agent proposes; only the core's Yosys checker (core/claims.py) decides what is proven.

export interface Scope { cone_of?: string; cells?: string[]; nets?: string[] }

export interface Attempt {
  attempt: number;
  first_draft: boolean;
  claim: string | null;
  label?: string;
  verdict: string;            // credit outcome: proven | inductive | partial | restated | refuted | bounded | ...
  truth?: string | null;      // does it hold: proven | inductive | bounded | refuted | tautology
  english?: string | null;    // written by the checker's code, not the LLM
  counterexample?: Record<string, number> | null;
  refute_kind?: string | null;
  error?: string | null;
  credit?: { cleared: boolean; complete?: boolean | null; reason?: string; literals?: number; cone_literals?: number } | null;
  cells?: string[];
  checker_s?: number | null;
  wall_s?: number;
}

export interface ToolStep { tool: string; args?: Record<string, unknown>; cells?: string[]; lines?: number[]; wall_s?: number; is_error?: boolean }

/** The plain-English reading written after Yosys decided (decap_agent/plain.py). The formal claim stays the source. */
export interface Plain {
  headline: string; explanation: string; example?: string; verdict: string;
  source?: "claim" | "code";   // claim: reading of a checked claim; code: read from the (proven) decompiled lines
  proof?: string | null;       // for source code: the decompile's own verdict (proven = code equals the gates)
}

export interface Done {
  final: string; first_draft: string; explained: boolean; proof?: boolean; claim: string | null; english: string | null;
  cells: string[]; cached: boolean; plain?: Plain | null;
  shown?: string;            // the best attempt's verdict (what people see); `final` is the last one (the bench's)
}

export type AgentEvent =
  | ({ kind: "tool" } & ToolStep)
  | ({ kind: "claim" } & Attempt)
  | ({ kind: "done" } & Done)
  | { kind: "thought"; step?: string; why?: string }
  | ({ kind: "reading" } & Plain)
  | { kind: "error"; error: string };

/** Interactive runs may refine up to 4 times; the frozen bench keeps the plan's 2 (decap/agent bench). */
export const INTERACTIVE_REFINES = 2;   // the code reading always answers; the proof search is the bonus

/** POST /witness/explain and read its Server-Sent Events as they arrive. `fresh` skips the server's run cache. */
export async function explain(chip: string, scope: Scope, onEvent: (e: AgentEvent) => void, signal?: AbortSignal,
                              fresh = false): Promise<void> {
  const res = await fetch("/witness/explain", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chip, scope, max_refine: INTERACTIVE_REFINES, cache: !fresh }), signal,
  });
  if (!res.ok || !res.body) throw new Error(`The agent service answered ${res.status}. Is 'decap serve' running on port 8791?`);
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
      if (line) onEvent(JSON.parse(line.slice(6)) as AgentEvent);
    }
  }
}

export interface Formal {
  text: string; claim: string; english: string | null; assumptions: string[]; ambiguous: boolean; alternative: string;
  error: string | null; verdict?: Attempt & { verdict: string };
}

export type TerminalEvent = AgentEvent | ({ kind: "done" } & Record<string, unknown>) | { kind: "note"; intent: string; text?: string };

/** A natural-language command for the chip agent (the terminal): 'is anything hidden?', 'what does block b0 do?'... */
export async function command(chip: string, text: string, onEvent: (e: TerminalEvent) => void, signal?: AbortSignal,
                              upload?: { verilog: string; filename?: string; repair?: boolean; suspects?: unknown[] }): Promise<void> {
  const res = await fetch("/witness/command", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ chip, command: text, ...upload }), signal,
  });
  if (!res.ok || !res.body) throw new Error(`The agent service answered ${res.status}. Is 'decap serve' running on port 8791?`);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  let ended = false;   // a stream that stops without done/error must not leave the panel spinning
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      const ev = JSON.parse(line.slice(6)) as TerminalEvent;
      if (ev.kind === "done" || ev.kind === "error") ended = true;
      onEvent(ev);
    }
  }
  if (!ended) throw new Error("The agent's stream ended before it finished. Try again.");
}

/** The judge's / commenter's sentence -> one formal line (+ the checker's verdict when check = true). */
export async function formalise(chip: string, sentence: string, scope: Scope, check: boolean): Promise<Formal> {
  const res = await fetch("/witness/formalise", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ chip, sentence, scope, check }),
  });
  if (!res.ok) throw new Error(`The agent service answered ${res.status}. Is 'decap serve' running on port 8791?`);
  return res.json();
}

export async function agentUp(): Promise<boolean> {
  try { return (await fetch("/witness/healthz")).ok; } catch { return false; }
}

// ---------- Ideas: the agent proposes edits to a region, the core's Yosys reviews the edited module ----------

export interface IdeaOut {
  id: string; title: string; why: string;
  intent: "same" | "change";          // "same": should keep behaviour (refactor); "change": a deliberate new behaviour
  lines: [number, number];            // in the Verilog that was sent (1-based, inclusive)
  replacement: string[];
}
export interface IdeasReply { reply: string; ideas: IdeaOut[]; model: string }
export interface IdeasAsk {
  chip: string; line: number; lines: [number, number]; verilog: string; ask: string;
  history: { role: "user" | "agent"; text: string }[];
}

/** Why a request failed, in plain English: FastAPI's {detail}, else the status with a hint at which service is down. */
async function failure(res: Response, down: string): Promise<Error> {
  let detail = "";
  try {
    const j = await res.json() as { detail?: unknown };
    detail = typeof j.detail === "string" ? j.detail : j.detail ? JSON.stringify(j.detail) : "";
  } catch { /* not JSON */ }
  if (detail) return new Error(detail);
  return new Error(res.status >= 500 ? down : `The server answered ${res.status}.`);
}

/** POST /witness/ideas: 2-3 concrete edits for lines `lines` of the current Verilog (kept edits applied). */
export async function ideas(body: IdeasAsk, signal?: AbortSignal): Promise<IdeasReply> {
  let res: Response;
  try {
    res = await fetch("/witness/ideas", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new Error("The agent service isn't reachable. Start it with 'decap serve --port 8791' (decap/agent).");
  }
  if (!res.ok) throw await failure(res, `The agent service answered ${res.status}. Is 'decap serve' running on port 8791?`);
  return res.json();
}

export interface Verdict {
  verdict: "same" | "tested_same" | "changed" | "broken";
  headline: string;                   // written by the core's code, never by the LLM
  detail: string;
  counterexample: Record<string, number> | null;   // input values (+ optional `step`) where the edit behaves differently
}
export interface Review extends Verdict {
  chip: string;
  this_edit?: Verdict;                // with a baseline: this edit alone, compared with the text before it
  seconds: number;
}

export const REVIEW_TIMEOUT_MS = 150_000;   // the core gives the review 90 s; allow for the request around it

/** POST /api/chips/{chip}/review-edit: Yosys checks the full edited module against the chip's real gates, and (with
 * `baseline`, the text before this edit) what this edit alone does. */
export async function reviewEdit(chip: string, verilog: string, baseline?: string): Promise<Review> {
  let res: Response;
  const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), REVIEW_TIMEOUT_MS);
  try {
    res = await fetch(`/api/chips/${encodeURIComponent(chip)}/review-edit`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(baseline != null ? { verilog, baseline } : { verilog }),
      signal: ctl.signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw new Error(`Yosys didn't answer within ${REVIEW_TIMEOUT_MS / 1000} s. Review it again, or try a smaller edit.`);
    throw new Error("Decap's core API isn't reachable, so Yosys can't review the edit. Start it on port 8000.");
  } finally { clearTimeout(t); }
  if (!res.ok) throw await failure(res, `Decap's core API answered ${res.status}. Is it running on port 8000?`);
  return res.json();
}
