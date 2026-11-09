// Client for the Witness agent API (agent/: `decap serve`, proxied at /witness by vite.config.ts), plus the core's
// review-edit route the agent features use. The agent proposes; only the core's Yosys checker decides what is proven.

import { ApiError, DOWN, postJson, reachable, request, stream } from "./http";

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
const INTERACTIVE_REFINES = 2;   // the code reading always answers; the proof search is the bonus

/** POST /witness/explain and read its Server-Sent Events as they arrive. `fresh` skips the server's run cache. */
export const explain = (chip: string, scope: Scope, onEvent: (e: AgentEvent) => void, signal?: AbortSignal, fresh = false) =>
  stream<AgentEvent>("/witness/explain", { chip, scope, max_refine: INTERACTIVE_REFINES, cache: !fresh }, onEvent, signal, DOWN.witness);

export interface Formal {
  text: string; claim: string; english: string | null; assumptions: string[]; ambiguous: boolean; alternative: string;
  error: string | null; verdict?: Attempt & { verdict: string };
}

export type TerminalEvent = AgentEvent | ({ kind: "done" } & Record<string, unknown>) | { kind: "note"; intent: string; text?: string };

/** A natural-language command for the chip agent (the terminal): 'is anything hidden?', 'what does block b0 do?'... */
export async function command(chip: string, text: string, onEvent: (e: TerminalEvent) => void, signal?: AbortSignal,
                              upload?: { verilog: string; filename?: string; repair?: boolean; suspects?: unknown[] }): Promise<void> {
  let ended = false;   // a stream that stops without done/error must not leave the panel spinning
  await stream<TerminalEvent>("/witness/command", { chip, command: text, ...upload }, (ev) => {
    if (ev.kind === "done" || ev.kind === "error") ended = true;
    onEvent(ev);
  }, signal, DOWN.witness);
  if (!ended) throw new Error("The agent's stream ended before it finished. Try again.");
}

/** The judge's / commenter's sentence -> one formal line (+ the checker's verdict when check = true). */
export const formalise = (chip: string, sentence: string, scope: Scope, check: boolean) =>
  postJson<Formal>("/witness/formalise", { chip, sentence, scope, check }, undefined, DOWN.witness);

export const agentUp = () => reachable("/witness/healthz");

/** Plain English for some lines of Verilog (the editor's "Explain selection"). */
export const explainText = (chip: string, lines: { line: number; text: string }[]) =>
  postJson<{ headline: string; explanation: string; example?: string }>("/witness/explain-text", { chip, lines }, undefined, DOWN.witness);

export interface Session { id: string; chip?: string; kind?: string; status?: string; started_at?: string; command?: string }
/** Recent agent sessions (the terminal's /status). */
export const sessions = () => request<Session[]>("/witness/status", { down: DOWN.witness });

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

/** POST /witness/ideas: 2-3 concrete edits for lines `lines` of the current Verilog (kept edits applied). */
export const ideas = (body: IdeasAsk, signal?: AbortSignal) => postJson<IdeasReply>("/witness/ideas", body, signal, DOWN.witness);

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

const REVIEW_TIMEOUT_MS = 150_000;   // the core gives the review 90 s; allow for the request around it

/** POST /api/chips/{chip}/review-edit: Yosys checks the full edited module against the chip's real gates, and (with
 * `baseline`, the text before this edit) what this edit alone does. */
export async function reviewEdit(chip: string, verilog: string, baseline?: string): Promise<Review> {
  const ctl = new AbortController(), t = setTimeout(() => ctl.abort(), REVIEW_TIMEOUT_MS);
  try {
    return await postJson<Review>(`/api/chips/${encodeURIComponent(chip)}/review-edit`, baseline != null ? { verilog, baseline } : { verilog }, ctl.signal, DOWN.core);
  } catch (e) {
    if ((e as Error).name === "AbortError") throw new ApiError(0, `Yosys didn't answer within ${REVIEW_TIMEOUT_MS / 1000} s. Review it again, or try a smaller edit.`);
    throw e;
  } finally { clearTimeout(t); }
}
