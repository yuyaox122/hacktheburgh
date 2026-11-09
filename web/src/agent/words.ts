// How the agent's results read on screen: what each checker verdict means in words, its pill, the counterexample
// chips and diffs. Shared by the code pane, the Ideas threads, the editor, the drawer and the terminal.

import { esc, hiVerilog } from "../ui/dom";
import { md } from "../lib/md";
import type { Plain } from "../api/witness";

/** A claim's verdict as a pill: [label, tone]. */
const VERDICT: Record<string, [string, string]> = {
  proven: ["Proven", "ok"], inductive: ["Proven from reset", "ok"], tested: ["Tested only", "warn"], refuted: ["Refuted", "bad"],
  partial: ["True, incomplete", "warn"], restated: ["True, no shorter", ""], bounded: ["Holds 30 cycles", "warn"],
  tautology: ["Tautology", ""], unparseable: ["Didn't parse", ""], timeout: ["Timed out", ""], unchecked: ["Not checked", ""],
};
export const pill = (v?: string | null) => {
  const [t, c] = VERDICT[v ?? ""] ?? [v ?? "…", ""];
  return `<span class="pill ${c}">${esc(t)}</span>`;
};

/** The core's review of an edited module (review-edit): [label, tone]. */
export const REVIEW_PILL: Record<string, [string, string]> = {
  same: ["Same circuit, proven", "ok"], tested_same: ["No difference found, not proven", "warn"],
  changed: ["Behaviour changed", "bad"], broken: ["Broken", "bad"],
};

/** The input values where a claim or an edit breaks (flip-flop state bits folded into a count). */
export function cexHtml(c?: Record<string, number> | null, label = "Breaks at"): string {
  if (!c) return "";
  const shown = Object.entries(c).filter(([k]) => !k.startsWith("q_"));
  const hidden = Object.keys(c).length - shown.length;
  return `<div class="ag-cex"><span class="ag-lbl">${esc(label)}</span>${shown.map(([k, v]) => `<code>${esc(k)} = ${esc(v)}</code>`).join("")}${hidden ? `<span class="muted">+${hidden} flip-flop bits</span>` : ""}</div>`;
}

/** Removed and added Verilog lines, highlighted. */
export const diffHtml = (del: string[], add: string[]) =>
  `<div class="ag-diff">${del.map((t) => `<div class="del"><span>−</span>${hiVerilog(t) || " "}</div>`).join("")}${add.map((t) => `<div class="add"><span>+</span>${hiVerilog(t) || " "}</div>`).join("")}${!add.length ? `<div class="add muted"><span>+</span>(lines removed)</div>` : ""}</div>`;

// What each checker verdict means, said the way a person would say it. Used wherever the agent replies in words.

const SAID: Record<string, string> = {
  proven: "Yosys proved this for every input.",
  inductive: "Yosys proved this for every state the chip can reach.",
  partial: "It's true, but it leaves part of this logic unexplained, so it isn't the full story.",
  restated: "It's true, but no simpler than the code itself, so it doesn't add much.",
  bounded: "It holds for the first 30 clock cycles after reset. Yosys couldn't prove it holds forever.",
  tested: "It passed random testing. That isn't a proof.",
  refuted: "Yosys found an input where the chip does something else.",
  tautology: "That's true of any chip, so it says nothing about this one.",
  unparseable: "The agent couldn't write its idea in a form the checker can read.",
  timeout: "Yosys ran out of time before deciding.",
  no_claim: "The agent ran out of budget before making a claim.",
  unchecked: "This hasn't been checked yet.",
};

/** One or two plain sentences for a verdict. `failed` = the agent's last attempt, phrased as what went wrong. */
export function verdictWords(v: string, failed = false): string {
  const s = SAID[v] ?? `The checker said "${v}".`;
  if (!failed) return s;
  if (v === "refuted") return "My best explanation was wrong. " + s + " Try running it again.";
  return "I couldn't get an explanation proven. " + s;
}

/** The readable explanation block: headline, two or three sentences, one worked step, and where it came from. */
export function plainHtml(p: Plain | null | undefined): string {
  if (!p) return "";
  const proven = p.verdict === "proven" || p.verdict === "inductive";
  const by = p.source === "code"
    ? `Read from the decompiled code${p.proof === "proven" ? ", which Yosys proved equal to the chip's gates" : p.proof ? ` (decompile: ${esc(p.proof)})` : ""}`
    : proven ? "Plain-English reading of the proven claim below" : `Plain-English reading. ${esc(verdictWords(p.verdict))}`;
  return `<div class="ag-plain">
    <b class="ag-plain-h">${esc(p.headline)}</b>
    ${md(p.explanation)}
    ${p.example ? `<p class="ag-eg"><span>e.g.</span><code>${esc(p.example)}</code></p>` : ""}
    <span class="ag-by">${by}</span>
  </div>`;
}
