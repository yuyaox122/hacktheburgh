import { esc } from "../ui/dom";
import { md } from "../lib/md";
import type { Plain } from "../api/witness";

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
