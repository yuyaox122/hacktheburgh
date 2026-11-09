// The Decompile pane as a real editor: edit the decompiled Verilog, then Yosys says what the edit really does.
//
//   Edit            -> CodeMirror on the decompiled Verilog (your accepted agent edits applied)
//   Check (⌘/Ctrl-S) -> core review_edit against the chip's real gates: same (proven) / tested_same / changed / broken
//   Explain         -> plain English for the selected lines (POST /witness/explain-text)
//   Ideas           -> Yuyao's ideas agent (#89) for the selected lines; Apply puts the edit in the editor and re-checks
//   Fix with agent  -> after "changed": the repair loop (#106) makes it match the chip again; Apply replaces the text
//
// The verdict always comes from Yosys, never from the model. Text you type is kept per chip for this visit.

import { EditorState } from "@codemirror/state";
import { EditorView, keymap, lineNumbers, highlightActiveLine, drawSelection } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { StreamLanguage, syntaxHighlighting, HighlightStyle, bracketMatching } from "@codemirror/language";
import { tags } from "@lezer/highlight";
import { verilog } from "@codemirror/legacy-modes/mode/verilog";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { loadChip } from "../api/core";
import { esc } from "../ui/dom";
import { explainText, ideas, reviewEdit, type IdeaOut, type Review } from "../api/witness";
import { cexHtml, REVIEW_PILL } from "./words";
import { md } from "../lib/md";
import { withAcceptedEdits } from "./store";

const drafts = new Map<string, string>();   // chip -> the editor text, kept while you browse the app
let view: EditorView | null = null;
let host: HTMLElement | null = null;
let chip = "";
let original = "";

const theme = EditorView.theme({
  "&": { height: "100%", fontSize: "12.5px", backgroundColor: "transparent", color: "var(--fg)" },
  ".cm-content": { fontFamily: "var(--font-mono)", caretColor: "var(--fg)" },
  ".cm-gutters": { backgroundColor: "transparent", color: "var(--faint)", border: "none" },
  ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--fg) 5%, transparent)" },
  ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--fg)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": { backgroundColor: "color-mix(in srgb, var(--fall) 30%, transparent)" },
  "&.cm-focused": { outline: "none" },
}, { dark: true });

/** The same Verilog colours as the read-only code panes (ui/dom.ts hiVerilog; --syn-* tokens). */
const verilogColours = HighlightStyle.define([
  { tag: [tags.keyword, tags.modifier, tags.typeName, tags.definitionKeyword], color: "var(--syn-kw)" },
  { tag: [tags.number, tags.bool, tags.atom], color: "var(--syn-num)" },
  { tag: [tags.operator, tags.punctuation], color: "var(--syn-op)" },
  { tag: [tags.comment, tags.lineComment, tags.blockComment], color: "var(--syn-com)" },
  { tag: [tags.string], color: "var(--syn-num)" },
]);

export const isOpen = () => !!view;
/** Open the editor over the Decompile pane's body (the read-only view stays underneath, hidden). */
export async function open(chipId: string, paneBody: HTMLElement) {
  close();
  chip = chipId;
  const { data } = await loadChip(chipId);
  original = withAcceptedEdits(chipId, data.decompiled.verilog);
  host = document.createElement("div");
  host.className = "ag-editor";
  host.innerHTML = `
    <div class="ag-ed-bar">
      <button type="button" class="btn sm primary" data-ed="check" title="Yosys checks your Verilog against the chip's gates (⌘/Ctrl-S)">Check with Yosys</button>
      <button type="button" class="btn sm" data-ed="explain" title="Plain English for the selected lines">Explain selection</button>
      <button type="button" class="btn sm" data-ed="ideas" title="The agent suggests 2–3 edits for the selected lines">Ideas</button>
      <button type="button" class="btn sm" data-ed="fix" hidden title="The agent edits it until Yosys proves it equal to the chip">Fix with agent</button>
      <span class="grow"></span>
      <button type="button" class="btn plain sm" data-ed="reset" title="Back to the decompiled Verilog">Reset</button>
      <button type="button" class="btn plain sm" data-ed="close">Close editor</button>
    </div>
    <div class="ag-ed-main"></div>
    <div class="ag-ed-out" aria-live="polite"><span class="muted">Edit the code, then Check with Yosys. Select lines for Explain or Ideas.</span></div>`;
  paneBody.querySelector<HTMLElement>("#cpCode")?.setAttribute("hidden", "");
  paneBody.append(host);
  view = new EditorView({
    parent: host.querySelector<HTMLElement>(".ag-ed-main")!,
    state: EditorState.create({
      doc: drafts.get(chipId) ?? original,
      extensions: [
        lineNumbers(), history(), drawSelection(), highlightActiveLine(), bracketMatching(), highlightSelectionMatches(),
        StreamLanguage.define(verilog), syntaxHighlighting(verilogColours), theme,
        keymap.of([{ key: "Mod-s", preventDefault: true, run: () => { void check(); return true; } },
          indentWithTab, ...defaultKeymap, ...historyKeymap, ...searchKeymap]),
        EditorView.updateListener.of((u) => { if (u.docChanged) drafts.set(chip, u.state.doc.toString()); }),
      ],
    }),
  });
  host.addEventListener("click", onClick);
  view.focus();
}

export function close() {
  view?.destroy();
  view = null;
  host?.parentElement?.querySelector<HTMLElement>("#cpCode")?.removeAttribute("hidden");
  host?.remove();
  host = null;
}

const text = () => view?.state.doc.toString() ?? "";
const out = (html: string) => { const o = host?.querySelector<HTMLElement>(".ag-ed-out"); if (o) o.innerHTML = html; };

/** The selected line range (1-based), or the cursor's line. */
function selLines(): [number, number] {
  const s = view!.state, r = s.selection.main;
  const a = s.doc.lineAt(r.from).number, b = s.doc.lineAt(Math.max(r.from, r.to - (r.to > r.from ? 1 : 0))).number;
  return [a, Math.max(a, b)];
}

function replaceLines(a: number, b: number, lines: string[]) {
  const d = view!.state.doc;
  const from = d.line(a).from, to = d.line(Math.min(b, d.lines)).to;
  view!.dispatch({ changes: { from, to, insert: lines.join("\n") } });
}

function setText(t: string) { view!.dispatch({ changes: { from: 0, to: view!.state.doc.length, insert: t } }); }

async function check(): Promise<Review | null> {
  if (!view) return null;
  const t = text();
  if (t === original) { out(`<span class="pill ok">unchanged</span> <span class="muted">This is the decompiled Verilog, already proven equal to the chip.</span>`); return null; }
  out(`<span class="spin"></span> Yosys is checking your Verilog against the chip's gates…`);
  try {
    const r = await reviewEdit(chip, t);
    const [word, cls] = REVIEW_PILL[r.verdict] ?? [r.verdict, ""];
    out(`<span class="pill ${cls}">${esc(word)}</span> <b>${esc(r.headline ?? "")}</b>
      ${r.detail ? `<details${r.verdict === "broken" ? " open" : ""}><summary class="muted">details</summary><p class="muted">${esc(r.detail)}</p></details>` : ""}
      ${r.verdict === "changed" ? cexHtml(r.counterexample as Record<string, number> | null) : ""}
      <span class="muted">${r.seconds != null ? `${r.seconds.toFixed(1)} s` : ""}</span>`);
    host!.querySelector<HTMLElement>("[data-ed='fix']")!.hidden = r.verdict !== "changed";
    return r;
  } catch (e) { out(`<span class="pill bad">error</span> ${esc((e as Error).message)}`); return null; }
}

async function explainSel() {
  const [a, b] = selLines();
  const lines = text().split("\n").slice(a - 1, b).map((t, i) => ({ line: a + i, text: t }));
  out(`<span class="spin"></span> Reading lines ${a}–${b}…`);
  try {
    const p = await explainText(chip, lines);
    out(`<b>Lines ${a}–${b}: ${esc(p.headline)}</b>${md(p.explanation)}${p.example ? `<p class="muted">e.g. <code>${esc(p.example)}</code></p>` : ""}
      <span class="muted">Read from these lines by the agent; Check with Yosys for a verdict.</span>`);
  } catch (e) { out(`<span class="pill bad">error</span> ${esc((e as Error).message)}`); }
}

let lastIdeas: IdeaOut[] = [];
async function ideasSel() {
  const [a, b] = selLines();
  out(`<span class="spin"></span> The agent is thinking of edits for lines ${a}–${b}…`);
  try {
    const r = await ideas({ chip, line: a, lines: [a, b], verilog: text(), ask: "", history: [] });
    lastIdeas = r.ideas;
    out(`${r.reply ? md(r.reply) : ""}<ol class="ag-ed-ideas">${r.ideas.map((x, i) => `<li><b>${esc(x.title)}</b>
      <span class="pill ${x.intent === "same" ? "" : "warn"}">${x.intent === "same" ? "keeps behaviour" : "changes behaviour"}</span>
      <p class="muted">${esc(x.why)}</p><pre class="ag-ed-pre">${esc(x.replacement.join("\n"))}</pre>
      <button type="button" class="btn sm" data-ed-apply="${i}">Apply lines ${x.lines[0]}–${x.lines[1]}</button></li>`).join("")}</ol>`);
  } catch (e) { out(`<span class="pill bad">error</span> ${esc((e as Error).message)}`); }
}

function fix() {
  out(`<span class="spin"></span> The agent is repairing your Verilog until Yosys proves it equal to the chip… (watch the Agent drawer)`);
  const onDone = (e: Event) => {
    document.removeEventListener("agent:repaired", onDone);
    const d = (e as CustomEvent<{ chip: string; verdict: string; verilog: string; diff: Array<{ line: number }> }>).detail;
    if (d.chip !== chip || !view) return;
    if (d.verdict === "fixed") {
      setText(d.verilog);
      out(`<span class="pill ok">fixed</span> The agent changed line${d.diff.length === 1 ? "" : "s"} ${d.diff.map((x) => x.line).join(", ")}; Yosys proved it equal to the chip. <span class="muted">(Ctrl/⌘-Z undoes it.)</span>`);
      host!.querySelector<HTMLElement>("[data-ed='fix']")!.hidden = true;
    } else out(`<span class="pill bad">${esc(d.verdict)}</span> The agent couldn't make it match the chip.`);
  };
  document.addEventListener("agent:repaired", onDone);
  document.dispatchEvent(new CustomEvent("agent:repair", { detail: { chip, verilog: text(), filename: "editor.v" } }));
}

function onClick(e: MouseEvent) {
  const t = e.target as HTMLElement;
  const ap = t.closest<HTMLElement>("[data-ed-apply]");
  if (ap) {
    const x = lastIdeas[+ap.dataset.edApply!];
    if (x && view) { replaceLines(x.lines[0], x.lines[1], x.replacement); void check(); }
    return;
  }
  const act = t.closest<HTMLElement>("[data-ed]")?.dataset.ed;
  if (act === "check") void check();
  else if (act === "explain") void explainSel();
  else if (act === "ideas") void ideasSel();
  else if (act === "fix") fix();
  else if (act === "reset" && view) { setText(original); drafts.delete(chip); out(`<span class="muted">Back to the decompiled Verilog.</span>`); }
  else if (act === "close") close();
}
