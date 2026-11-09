// The agent inside the decompiled-Verilog pane: a hover toolbar on every line (Explain · Suggest edit · Comment · Ideas),
// comment threads under lines (claude.ai-style; @agent asks the agent), suggested edits as a diff with Accept /
// Dismiss, Ideas threads (ideas.ts: the agent proposes edits, Yosys reviews the ones you apply), and accepted edits
// shown in place of the lines they replace. Re-applied after every render of the pane.

import { md } from "../lib/md";
import { $, $$, esc, hiVerilog, toast } from "../ui/dom";
import { comment, regionForLine, runAgent } from "./agent";
import { store, type Comment, type Edit } from "./store";
import { cexHtml, diffHtml, pill } from "./words";
import type { CodeHost } from "../ui/state";
import * as ideas from "./ideas";

let composer: { chip: string; line: number } | null = null;
const showOriginal = new Set<string>();   // accepted edit ids whose original lines are expanded
const bound = new WeakSet<HTMLElement>();
let hover: HTMLDivElement | null = null;
let hoverLine = 0;
let hoverHost: CodeHost | null = null;
let hideT = 0;

const when = (t: number) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

/** Decorate the code pane after the Chip page rendered it (idempotent). */
export function decorateCode(host: CodeHost) {
  const r = host.result;
  const ol = $<HTMLOListElement>("#cpCode", host.el);
  if (!r || !ol) return;
  bindOnce(host);
  // keep the caret of a half-typed comment or ask across the repaint
  const typing = document.activeElement instanceof HTMLTextAreaElement && ol.contains(document.activeElement) ? document.activeElement : null;
  const caret = typing ? { id: typing.id, value: typing.value, a: typing.selectionStart, b: typing.selectionEnd } : null;
  $$(".ag-row", ol).forEach((x) => x.remove());
  $$("li[data-ln]", ol).forEach((li) => { li.classList.remove("ag-replaced", "ag-v-ok", "ag-v-bad", "ag-v-warn", "ag-orig-shown"); li.querySelector(".ag-cbub")?.remove(); });

  // verdict marks in the gutter: the latest finished run per line range
  for (const run of [...store.runs(host.chip)].reverse()) {
    if (run.status !== "done" || !run.lines || run.lines[0] < 1) continue;
    const cls = run.explained ? "ag-v-ok" : run.final === "refuted" ? "ag-v-bad" : "ag-v-warn";
    for (let ln = run.lines[0]; ln <= run.lines[1]; ln++) {
      const li = ol.querySelector<HTMLElement>(`li[data-ln="${ln}"]`);
      li?.classList.remove("ag-v-ok", "ag-v-bad", "ag-v-warn"); li?.classList.add(cls);
    }
  }
  const after = new Map<number, string[]>();   // line -> rows to insert after it
  const push = (ln: number, h: string) => { const a = after.get(ln) ?? []; a.push(h); after.set(ln, a); };

  const edits = ideas.shownEdits(host), basis = ideas.basisOf(host);
  for (const e of edits) {
    if (e.state === "reviewing" || e.state === "reviewed") push(e.lines[1], ideas.reviewRow(e, basis));
    else if (e.state === "accepted") {
      const open = showOriginal.has(e.id);
      for (let ln = e.lines[0]; ln <= e.lines[1]; ln++) ol.querySelector(`li[data-ln="${ln}"]`)?.classList.add(open ? "ag-orig-shown" : "ag-replaced");
      push(e.lines[1], acceptedRow(e, open));
    } else if (e.state === "suggested") push(e.lines[1], suggestedRow(e));
  }
  const lines = new Set(store.comments(host.chip).map((c) => c.line));
  if (composer?.chip === host.chip) lines.add(composer.line);
  for (const ln of lines) {
    const cs = store.comments(host.chip, ln);
    push(ln, threadRow(host.chip, ln, cs));
    const li = ol.querySelector(`li[data-ln="${ln}"]`);
    if (li && cs.length) li.insertAdjacentHTML("beforeend", `<button type="button" class="ag-cbub" data-ag="thread" data-line="${ln}" title="${cs.length} comment${cs.length > 1 ? "s" : ""}">${cs.length}</button>`);
  }
  for (const t of store.ideaThreads(ideas.variantKey(host))) push(t.line, ideas.threadRow(t, edits));
  for (const [ln, rows] of after) ol.querySelector(`li[data-ln="${ln}"]`)?.insertAdjacentHTML("afterend", rows.join(""));
  const back = caret ? ol.querySelector<HTMLTextAreaElement>(`#${caret.id}`) : null;
  if (back && caret) { back.value = caret.value; back.focus({ preventScroll: true }); back.setSelectionRange(caret.a, caret.b); }
  const ta = composer?.chip === host.chip ? ol.querySelector<HTMLTextAreaElement>(`#ag-c-${composer.line}`) : null;
  if (!back && ta && document.activeElement !== ta && !ta.value) ta.focus({ preventScroll: true });
  const ita = ideas.focusThread ? ol.querySelector<HTMLTextAreaElement>(`#ag-i-${ideas.focusThread}`) : null;
  if (ita) { ideas.clearFocus(); ita.focus({ preventScroll: true }); ita.closest("li")?.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
  // "Download edited .v" in the pane header once any edit is kept
  const head = ol.closest(".pane")?.querySelector(".pane-h");
  const dl = head?.querySelector("[data-ag=download]");
  const kept = edits.some((e) => e.state === "accepted");
  if (kept && head && !dl) head.insertAdjacentHTML("beforeend", `<button class="btn sm" type="button" data-ag="download" title="Save the Verilog shown, with your kept edits, as a .v file">Download edited .v</button>`);
  else if (!kept) dl?.remove();
}

function suggestedRow(e: Edit): string {
  return `<li class="ag-row ag-edit suggested" data-edit="${e.id}">
    <div class="ag-edit-h"><span class="ag-tag">Suggested edit</span>${pill("proven")}<span class="muted">lines ${e.lines[0]}–${e.lines[1]} · from the agent's proven claim</span></div>
    ${diffHtml(e.original, e.replacement)}
    <div class="ag-actions"><button type="button" class="btn sm primary" data-ag="accept" data-id="${e.id}">Accept edit</button><button type="button" class="btn sm" data-ag="dismiss" data-id="${e.id}">Dismiss</button><button type="button" class="btn sm" data-ag="cells" data-id="${e.id}">Show its cells</button></div>
  </li>`;
}

function acceptedRow(e: Edit, open: boolean): string {
  const tag = e.kind === "idea" ? `Your edit${e.title ? `: ${esc(e.title)}` : ""} · replaces lines ${e.lines[0]}–${e.lines[1]}` : `Agent edit · replaces lines ${e.lines[0]}–${e.lines[1]}`;
  return `<li class="ag-row ag-edit accepted" data-edit="${e.id}">
    <div class="ag-code">${e.replacement.map((t) => `<div>${hiVerilog(t) || " "}</div>`).join("") || `<div class="muted">(lines removed)</div>`}</div>
    <div class="ag-actions"><span class="ag-tag">${tag}</span>${e.kind === "idea" ? ideas.keptNote(e) : ""}<button type="button" class="btn sm" data-ag="orig" data-id="${e.id}">${open ? "Hide" : "Show"} original</button>${e.cells.length ? `<button type="button" class="btn sm" data-ag="cells" data-id="${e.id}">Show its cells</button>` : ""}<button type="button" class="btn sm" data-ag="undo" data-id="${e.id}">Undo</button></div>
  </li>`;
}

function commentHtml(c: Comment): string {
  return `<div class="ag-cm ${c.author}">
    <div class="ag-cm-h"><b>${c.author === "agent" ? "Witness agent" : "You"}</b><time>${when(c.ts)}</time>${c.verdict ? pill(c.verdict) : ""}</div>
    ${c.pending ? `<p><span class="spin"></span> ${esc(c.text)}</p>` : md(c.text)}
    ${c.claim ? `<details class="ag-how"><summary>The checked claim</summary><code class="ag-claim">${esc(c.claim)}</code>${c.english ? `<p class="muted">${esc(c.english)}</p>` : ""}</details>` : ""}
    ${c.verdict === "refuted" ? cexHtml(c.counterexample) : ""}
  </div>`;
}

function threadRow(chip: string, ln: number, cs: Comment[]): string {
  const open = composer?.chip === chip && composer.line === ln;
  return `<li class="ag-row ag-thread" data-line="${ln}">
    ${cs.map(commentHtml).join("")}
    ${open ? `<form class="ag-compose" data-line="${ln}">
      <textarea class="text" id="ag-c-${ln}" rows="2" placeholder="Comment on line ${ln}. Write a claim and tick the box to have the agent check it, e.g. “the alarm turns on when the time matches the switches”"></textarea>
      <div class="ag-actions"><label><input type="checkbox" id="ag-ask-${ln}" checked> Ask the agent to check this with Yosys</label><span class="grow"></span><button type="button" class="btn sm" data-ag="cancel">Cancel</button><button type="submit" class="btn sm primary">Comment</button></div>
    </form>` : `<div class="ag-actions"><button type="button" class="btn sm" data-ag="reply" data-line="${ln}">Reply</button></div>`}
  </li>`;
}

function bindOnce(host: CodeHost) {
  if (bound.has(host.el)) return;
  bound.add(host.el);
  host.el.addEventListener("click", (ev) => {
    const b = (ev.target as HTMLElement).closest<HTMLElement>("[data-ag]");
    if (!b) return;
    ev.stopPropagation();
    const id = b.dataset.id ?? "", line = +(b.dataset.line ?? 0);
    const edit = store.edit(id);
    switch (b.dataset.ag) {
      case "accept": {
        const k = edit && ideas.keptIdeaClash(host, edit.lines);
        if (k) { toast(`This edit overlaps your kept edit on lines ${k.lines[0]}–${k.lines[1]}. Undo that one first.`, "bad"); break; }
        store.setEdit(id, "accepted"); toast("Edit accepted: the Verilog now shows the proven version; its cells are blue in 3D.", "good"); break;
      }
      case "dismiss": store.setEdit(id, "dismissed"); break;
      case "undo": store.setEdit(id, "dismissed"); showOriginal.delete(id); break;
      case "orig": showOriginal.has(id) ? showOriginal.delete(id) : showOriginal.add(id); decorateCode(host); break;
      case "cells": if (edit) host.highlightFromGuide({ cells: edit.cells }); break;
      case "thread": case "reply": composer = { chip: host.chip, line }; decorateCode(host); break;
      case "cancel": composer = null; decorateCode(host); break;
      case "keep": ideas.keep(host, id); break;
      case "review": void ideas.review(host, id); break;
      case "idea-apply": void ideas.applyIdea(host, b.dataset.thread!, b.dataset.idea!); break;
      case "ideas-close": ideas.drafts.delete(b.dataset.thread!); store.removeIdeaThread(b.dataset.thread!); break;
      case "download": ideas.download(host); break;
    }
  });
  host.el.addEventListener("input", (ev) => {
    const ta = ev.target as HTMLElement;
    const f = ta.closest<HTMLFormElement>("form.ag-ideas-compose");
    if (f && ta instanceof HTMLTextAreaElement) ideas.drafts.set(f.dataset.thread!, ta.value);
  });
  host.el.addEventListener("keydown", (ev) => {
    const f = (ev.target as HTMLElement).closest<HTMLFormElement>("form.ag-ideas-compose");
    if (f && ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); f.requestSubmit(); }
  });
  host.el.addEventListener("submit", (ev) => {
    const f = (ev.target as HTMLElement).closest<HTMLFormElement>("form.ag-compose");
    if (!f) return;
    ev.preventDefault();
    if (f.classList.contains("ag-ideas-compose")) {
      const id = f.dataset.thread!, ta = f.querySelector("textarea"), text = (ideas.drafts.get(id) ?? ta?.value ?? "").trim();
      ideas.drafts.delete(id);
      if (ta) ta.value = "";   // sent: the repaint below must not put it back
      void ideas.askIdeas(host, id, text);
      return;
    }
    const ln = +f.dataset.line!;
    const text = $<HTMLTextAreaElement>(`#ag-c-${ln}`, f).value.trim();
    if (!text) return;
    const ask = $<HTMLInputElement>(`#ag-ask-${ln}`, f).checked || /@agent\b/i.test(text);
    composer = null;
    const r = host.result!;
    void comment(host.chip, ln, text, ask, regionForLine(r, ln)?.scope ?? null);
  });
  host.el.addEventListener("mouseover", (ev) => {
    const li = (ev.target as HTMLElement).closest<HTMLElement>("#cpCode li[data-ln]");
    if (li) showHover(host, li);
  });
  host.el.addEventListener("mouseout", (ev) => {
    if ((ev.target as HTMLElement).closest("#cpCode")) { clearTimeout(hideT); hideT = window.setTimeout(hideHover, 350); }
  });
}

function ensureHover(): HTMLDivElement {
  if (hover) return hover;
  hover = document.createElement("div");
  hover.className = "ag-hover";
  hover.setAttribute("role", "toolbar");
  hover.setAttribute("aria-label", "Agent actions for this line");
  hover.innerHTML = `<button type="button" data-h="explain" title="An agent explains this line's region in one line; Yosys checks it">Explain</button><button type="button" data-h="edit" title="Explain, then propose the proven line as an edit">Suggest edit</button><button type="button" data-h="comment" title="Comment on this line (the agent can check it)">Comment</button><button type="button" data-h="ideas" title="Ask the agent for 2-3 ways to change this part; Yosys reviews any edit you apply">Ideas</button>`;
  hover.addEventListener("mouseenter", () => clearTimeout(hideT));
  hover.addEventListener("mouseleave", () => { hideT = window.setTimeout(hideHover, 250); });
  hover.addEventListener("click", (ev) => {
    const b = (ev.target as HTMLElement).closest<HTMLElement>("[data-h]");
    if (!b || !hoverHost) return;
    act(hoverHost, hoverLine, b.dataset.h as "explain" | "edit" | "comment" | "ideas");
    hideHover();
  });
  document.body.append(hover);
  return hover;
}

function showHover(host: CodeHost, li: HTMLElement) {
  clearTimeout(hideT);
  const h = ensureHover();
  hoverHost = host; hoverLine = +li.dataset.ln!;
  const has = !!(host.result && regionForLine(host.result, hoverLine));
  $$<HTMLButtonElement>("[data-h=explain],[data-h=edit]", h).forEach((b) => (b.disabled = !has));
  const pane = li.closest(".pane-b")?.getBoundingClientRect() ?? li.getBoundingClientRect();
  const rr = li.getBoundingClientRect();
  h.style.top = `${Math.max(pane.top, rr.top - 2)}px`;
  h.style.left = `${pane.right - h.offsetWidth - 14}px`;
  h.classList.add("on");
}
function hideHover() { hover?.classList.remove("on"); }

/** Start the agent from a line (also used by the command palette). */
export function act(host: CodeHost, ln: number, what: "explain" | "edit" | "comment" | "ideas") {
  const r = host.result;
  if (!r) return;
  if (what === "comment") { composer = { chip: host.chip, line: ln }; decorateCode(host); return; }
  if (what === "ideas") { ideas.openIdeas(host, ln); return; }
  const reg = regionForLine(r, ln);
  if (!reg) { toast(`Line ${ln} has no gates under it to explain.`); return; }
  host.highlightFromGuide({ cells: reg.block?.cells ?? (reg.scope.cells ?? []) });
  document.dispatchEvent(new CustomEvent("agent:open"));
  const key = JSON.stringify(reg.scope);
  const fresh = store.runs(host.chip).some((x) => JSON.stringify(x.scope) === key && x.status === "done" && !x.explained);
  void runAgent({ chip: host.chip, scope: reg.scope, label: reg.label, origin: what === "edit" ? "edit" : "line", line: ln,
    lines: reg.lines, suggestEdit: what === "edit", verilog: r.decompiled.verilog, fresh }).then((run) => {
    if (what === "edit" && run.status === "done" && !run.explained) toast("No edit to suggest: the agent's claim wasn't proven and credited. See the agent panel.", "bad");
  });
}

