// "Ideas" in the decompiled-Verilog pane: a conversation under a line where the agent proposes 2-3 concrete edits to
// that part. Apply puts one into the Verilog and asks the core to review the whole edited module with Yosys against
// the chip's real gates; Keep makes it part of the code shown. The agent only proposes: every verdict and its wording
// comes from the core (POST /api/chips/{chip}/review-edit), never from the LLM.

import type { ChipResult } from "../api/types";
import { esc, hiVerilog, toast } from "../ui/dom";
import { regionForLine } from "./agent";
import { ideas as askAgent, reviewEdit, type Review, type Verdict } from "../api/witness";
import { cexHtml, type CodeHost } from "./codeAgent";
import { applyPatches, clash, curRange, editedText, placeIdea, type Patch } from "./ideaEdits";
import { store, uid, type Edit, type Idea, type IdeaThread } from "./store";

export const drafts = new Map<string, string>();   // thread id -> unsent text (survives the pane re-rendering)
export let focusThread: string | null = null;
export const clearFocus = () => { focusThread = null; };

/** The copy on screen: "tt_um_x", or "tt_um_x+t137" for the copy with a planted trigger (the core's variant id). */
export function variantKey(host: CodeHost): string {
  const r = host.result as (ChipResult & { variant_id?: string }) | null;
  if (r?.variant !== "planted") return host.chip;
  return r.variant_id ?? (r.plant ? `${host.chip}+t${r.plant.n}` : host.chip);
}

/**
 * Every edit drawn in the pane: this copy's ideas (stored under "tt_um_x" or "tt_um_x+t137") and the chip's proven-claim
 * edits (stored under "tt_um_x" for both copies). Only edits whose lines still read as they did when the edit was made:
 * the clean and planted copies are different texts, so an edit made on one never lands on the other's lines.
 */
export function shownEdits(host: CodeHost): Edit[] {
  const r = host.result, vk = variantKey(host);
  if (!r) return [];
  const lines = r.decompiled.verilog.split("\n");
  const fits = (e: Edit) => e.lines[0] >= 1 && e.lines[1] <= lines.length && lines.slice(e.lines[0] - 1, e.lines[1]).join("\n") === e.original.join("\n");
  const own = [...store.edits(host.chip).filter((e) => e.kind !== "idea"), ...store.edits(vk).filter((e) => e.kind === "idea")];
  return own.filter(fits);
}
const keptEdits = (host: CodeHost): Edit[] => shownEdits(host).filter((e) => e.state === "accepted");
const keptPatches = (host: CodeHost): Patch[] => keptEdits(host);
/** Which kept edits a review ran with: Keep re-runs the review if they changed since. */
export const basisOf = (host: CodeHost) => keptEdits(host).map((e) => e.id).sort().join(",");
/** A kept idea that `lines` would overwrite (accepting a Suggest edit there would silently drop it). */
export const keptIdeaClash = (host: CodeHost, lines: [number, number]) => clash(lines, keptEdits(host).filter((e) => e.kind === "idea"));
const waiting = (host: CodeHost) => shownEdits(host).find((e) => e.state === "reviewing" || e.state === "reviewed");

/** The Verilog as shown: Decap's decompile with every kept edit in place. */
export function currentText(host: CodeHost): string | null {
  const r = host.result;
  return r ? editedText(r.decompiled.verilog, keptPatches(host)) : null;
}

// ---------- actions ----------

/** Hover "Ideas": open (or reopen) the thread under line `ln`. */
export function openIdeas(host: CodeHost, ln: number) {
  const r = host.result;
  if (!r) return;
  const vk = variantKey(host);
  const t = store.ideaThreads(vk).find((x) => x.line === ln)
    ?? store.addIdeaThread({ id: uid(), chip: vk, line: ln, lines: regionForLine(r, ln)?.lines ?? [ln, ln], msgs: [] });
  focusThread = t.id;
  store.changed(vk);
}

/** Send what the person typed (may be empty) and the thread so far; store the agent's reply and its ideas. */
export async function askIdeas(host: CodeHost, threadId: string, text: string) {
  const t = store.ideaThread(threadId), r = host.result;
  if (!t || !r) return;
  if (t.msgs.some((m) => m.pending)) { toast("The agent is still thinking about the last request."); return; }
  const kept = keptPatches(host);
  let ap;
  try { ap = applyPatches(r.decompiled.verilog.split("\n"), kept); }
  catch (e) { toast((e as Error).message, "bad"); return; }
  const history = t.msgs.filter((m) => !m.pending && !m.error).map((m) => ({
    role: m.role,
    text: m.ideas?.length ? `${m.text}\n${m.ideas.map((x, i) => `Idea ${i + 1}: ${x.title} (lines ${x.lines[0]}-${x.lines[1]}, ${x.intent})`).join("\n")}` : m.text,
  }));
  if (text) store.pushIdeaMsg(t.id, { id: uid(), role: "user", text, ts: Date.now() });
  const reply = store.pushIdeaMsg(t.id, { id: uid(), role: "agent", ts: Date.now(), pending: true,
    text: `Reading lines ${t.lines[0]}–${t.lines[1]} and thinking of ways to change them…` });
  try {
    const res = await askAgent({ chip: t.chip, line: curRange(ap, [t.line, t.line])[0], lines: curRange(ap, t.lines),
      verilog: ap.lines.join("\n"), ask: text, history });
    const out: Idea[] = (res.ideas ?? []).map((i) => {
      const p = placeIdea(ap, i.lines, kept);
      return { ...i, id: uid(), orig: "orig" in p ? p.orig : null, original: ap.lines.slice(i.lines[0] - 1, i.lines[1]),
        problem: "error" in p ? p.error : undefined };
    });
    store.updateIdeaMsg(t.id, reply.id, { pending: false, ideas: out, model: res.model,
      text: res.reply || (out.length ? "Here are some ideas." : "I have no ideas for this part. Try asking for something specific.") });
  } catch (e) {
    const msg = (e as Error).message;
    store.updateIdeaMsg(t.id, reply.id, { pending: false, error: true, text: msg });
    toast(msg, "bad");
  }
}

/** Apply: put the idea into the Verilog and have the core review it (kept edits + this one). */
export async function applyIdea(host: CodeHost, threadId: string, ideaId: string) {
  const t = store.ideaThread(threadId), r = host.result;
  const idea = t?.msgs.flatMap((m) => m.ideas ?? []).find((i) => i.id === ideaId);
  if (!t || !r || !idea) return;
  if (!idea.orig) { toast(idea.problem ?? "This idea can't be applied any more. Ask again.", "bad"); return; }
  const w = waiting(host);
  if (w) { toast(`Keep or Undo the edit on lines ${w.lines[0]}–${w.lines[1]} first.`, "bad"); return; }
  const c = clash(idea.orig, keptPatches(host));
  if (c) { toast(`This idea overlaps the edit you kept on lines ${c.lines[0]}–${c.lines[1]}. Undo that edit first.`, "bad"); return; }
  const now = r.decompiled.verilog.split("\n").slice(idea.orig[0] - 1, idea.orig[1]);
  if (now.join("\n") !== idea.original.join("\n")) { toast("The decompiled Verilog changed since this idea was made. Ask again.", "bad"); return; }
  const e: Edit = { id: uid(), chip: t.chip, runId: "", lines: idea.orig, original: idea.original, replacement: idea.replacement,
    claim: "", state: "reviewing", cells: [], kind: "idea", ideaId: idea.id, title: idea.title, intent: idea.intent };
  store.addEdit(e);
  await review(host, e.id);
}

/**
 * Ask the core's Yosys about the module with this edit and the kept ones, against the chip's gates, and (when edits are
 * kept) about this edit alone, against the text before it: a kept deliberate change must not be blamed on a refactor.
 */
export async function review(host: CodeHost, editId: string) {
  const e = store.edit(editId), r = host.result;
  if (!e || !r) return;
  const kept = keptPatches(host);
  let text: string, before: string | undefined;
  try {
    text = editedText(r.decompiled.verilog, [...kept, e]);
    before = kept.length ? editedText(r.decompiled.verilog, kept) : undefined;
  } catch (err) { store.updateEdit(editId, { state: "reviewed", error: (err as Error).message }); return; }
  store.updateEdit(editId, { state: "reviewing", error: undefined, review: undefined, basis: basisOf(host) });
  try {
    const rv = await reviewEdit(e.chip, text, before);
    if (store.edit(editId)?.state === "reviewing") store.updateEdit(editId, { state: "reviewed", review: rv, at: Date.now() });
  } catch (err) {
    const msg = (err as Error).message;
    if (store.edit(editId)?.state === "reviewing") store.updateEdit(editId, { state: "reviewed", error: msg });
    toast(msg, "bad");
  }
}

export function keep(host: CodeHost, editId: string) {
  const e = store.edit(editId);
  if (!e?.review || !keepable(e.review)) return;
  if ((e.basis ?? "") !== basisOf(host)) {
    toast("Your kept edits changed since Yosys reviewed this one, so it is reviewing it again. Keep it once the new verdict is in.", "bad");
    void review(host, editId);
    return;
  }
  const c = clash(e.lines, keptPatches(host));
  if (c) { toast(`It overlaps the edit you kept on lines ${c.lines[0]}–${c.lines[1]}.`, "bad"); return; }
  store.setEdit(editId, "accepted");
  toast("Kept: the Verilog now shows your edit. Download it with “Download edited .v” above the code.", "good");
}

export function download(host: CodeHost) {
  const text = currentText(host);
  if (text == null) return;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
  a.download = `${variantKey(host).replace(/[^\w.-]+/g, "_")}_edited.v`;
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ---------- rendering (rows inserted into #cpCode by decorateCode) ----------

/** The verdict that belongs to this edit: on its own (vs the text before it) when other edits were kept, else vs the chip. */
const own = (rv: Review): Verdict => rv.this_edit ?? rv;
const unchecked = (v?: Verdict) => !!v && v.verdict === "broken" && v.headline.startsWith("Yosys couldn't");
const keepable = (rv: Review) => rv.verdict !== "broken" && own(rv).verdict !== "broken";

const VERDICT: Record<string, [string, string]> = {
  same: ["Same circuit, proven", "ok"], tested_same: ["No difference found, not proven", "warn"], changed: ["Behaviour changed", "bad"],
};
export function reviewPill(e: Edit): string {
  const rv = e.review;
  if (!rv) return `<span class="pill">Not reviewed</span>`;
  const v = own(rv), alone = !!rv.this_edit;
  const [t, c] = v.verdict === "broken" ? (v.headline === "Doesn't compile" ? ["Doesn't compile", "bad"] : unchecked(v) ? ["Not checked", "warn"] : ["Can't be kept", "bad"])
    : v.verdict === "changed" && e.intent === "change" ? ["Behaviour changed, as intended?", "warn"]
    : v.verdict === "same" && alone ? ["Same as before, proven", "ok"]
    : VERDICT[v.verdict];
  return `<span class="pill ${c}"${alone ? ` title="This edit on its own, compared with the Verilog before it (your other kept edits included)"` : ""}>${esc(t)}</span>`;
}

const diffHtml = (del: string[], add: string[]) =>
  `<div class="ag-diff">${del.map((t) => `<div class="del"><span>−</span>${hiVerilog(t) || " "}</div>`).join("")}${add.map((t) => `<div class="add"><span>+</span>${hiVerilog(t) || " "}</div>`).join("")}${!add.length ? `<div class="add muted"><span>+</span>(lines removed)</div>` : ""}</div>`;
const intentTag = (i: Idea["intent"]) =>
  i === "change" ? `<span class="ag-intent change" title="A deliberate change to what the chip does">Changes behaviour</span>`
    : `<span class="ag-intent same" title="A rewrite that should do exactly the same thing">Keeps behaviour</span>`;
const span = (l: [number, number]) => (l[0] === l[1] ? `line ${l[0]}` : `lines ${l[0]}–${l[1]}`);

function cex(c?: Record<string, number> | null): string {
  if (!c) return "";
  const { step, ...ins } = c;
  return cexHtml(ins, step != null ? `Differs at cycle ${step}` : "Differs with");
}

/** An applied idea while Yosys reviews it, then with its verdict and Keep / Undo. Goes under the lines it replaces. */
export function reviewRow(e: Edit, basisNow: string): string {
  const rv = e.review, v = rv && own(rv);
  const body = e.state === "reviewing"
    ? `<p class="ag-busy"><span class="spin"></span> Yosys is reviewing the edited module against the chip's real gates…</p>`
    : e.error ? `<div class="callout bad">${esc(e.error)}</div>`
    : rv && v ? `<p><b>${esc(v.headline)}</b></p>${v.detail ? `<p class="muted ag-detail">${esc(v.detail)}</p>` : ""}${cex(v.counterexample)}${
      rv.this_edit ? `<p class="muted ag-detail">Compared with the chip, with your kept edits too: <b>${esc(rv.headline)}</b></p>` : ""}${
      e.state === "reviewed" && (e.basis ?? "") !== basisNow ? `<p class="muted ag-detail">Your kept edits changed since this review: Keep will have Yosys review it again first.</p>` : ""}` : "";
  const canKeep = e.state === "reviewed" && !!rv && !e.error && keepable(rv);
  const unwanted = v?.verdict === "changed" && e.intent !== "change";
  const retry = !!e.error || unchecked(rv) || unchecked(rv?.this_edit);
  return `<li class="ag-row ag-edit ag-review ${e.state}" data-edit="${e.id}">
    <div class="ag-edit-h"><span class="ag-tag">Applied idea</span><b>${esc(e.title ?? "")}</b>${e.state === "reviewed" && rv ? reviewPill(e) : ""}<span class="muted">replaces ${span(e.lines)}${rv ? ` · Yosys ${rv.seconds.toFixed(1)} s` : ""}</span></div>
    ${diffHtml(e.original, e.replacement)}
    <div role="status" aria-live="polite">${body}</div>
    <div class="ag-actions">
      ${e.state === "reviewed" ? `<button type="button" class="btn sm${canKeep && !unwanted ? " primary" : ""}" data-ag="keep" data-id="${e.id}"${canKeep ? "" : ` disabled title="${esc(rv ? `${[rv.this_edit, rv].find((x) => x?.verdict === "broken")?.headline ?? "Not reviewed"}: it can't be kept` : "Not reviewed yet")}"`}>${unwanted ? "Keep anyway" : "Keep"}</button>` : ""}
      ${retry ? `<button type="button" class="btn sm" data-ag="review" data-id="${e.id}">Review again</button>` : ""}
      <button type="button" class="btn sm" data-ag="dismiss" data-id="${e.id}">Undo</button>
    </div>
  </li>`;
}

/** The note on a kept idea's row: its verdict when it was applied. */
export function keptNote(e: Edit): string {
  return `${reviewPill(e)}${e.stale ? `<span class="muted" title="Yosys reviewed it together with an edit that has since been undone or replaced">reviewed before an Undo</span>` : ""}`;
}

function ideaCard(t: IdeaThread, i: Idea, edits: Edit[]): string {
  const e = edits.find((x) => x.ideaId === i.id);
  const status = !e ? (i.orig ? `<button type="button" class="btn sm primary" data-ag="idea-apply" data-thread="${t.id}" data-idea="${i.id}" title="Put this into the Verilog and have Yosys review it">Apply</button>`
      : `<span class="muted">${esc(i.problem ?? "")}</span>`)
    : e.state === "accepted" ? `<span class="pill ok">Kept</span>`
    : e.state === "reviewing" ? `<span class="muted"><span class="spin"></span> Yosys is reviewing it under ${span(e.lines)}</span>`
    : `<span class="muted">Applied under ${span(e.lines)}: Keep or Undo it there.</span>`;
  return `<div class="ag-idea" data-idea="${i.id}">
    <div class="ag-edit-h"><b>${esc(i.title)}</b>${intentTag(i.intent)}${i.orig ? `<span class="muted">${span(i.orig)}</span>` : ""}</div>
    ${i.why ? `<p class="muted">${esc(i.why)}</p>` : ""}
    ${diffHtml(i.original, i.replacement)}
    <div class="ag-actions">${status}</div>
  </div>`;
}

/** One Ideas thread under its line: the conversation, idea cards, and the box to ask (again). */
export function threadRow(t: IdeaThread, edits: Edit[]): string {
  const busy = t.msgs.some((m) => m.pending);
  const when = (ts: number) => new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const msgs = t.msgs.map((m) => `<div class="ag-cm ${m.role === "agent" ? "agent" : "you"}${m.error ? " err" : ""}">
      <div class="ag-cm-h"><b>${m.role === "agent" ? "Ideas agent" : "You"}</b><time>${when(m.ts)}</time>${m.model ? `<span class="muted">${esc(m.model)}</span>` : ""}</div>
      <p${m.pending ? ` role="status"` : ""}>${m.pending ? `<span class="spin"></span> ` : ""}${esc(m.text)}</p>
      ${m.ideas?.length ? `<div class="ag-ideas-list">${m.ideas.map((i) => ideaCard(t, i, edits)).join("")}</div>` : ""}
    </div>`).join("");
  return `<li class="ag-row ag-thread ag-ideas" data-thread="${t.id}">
    <div class="ag-edit-h"><span class="ag-tag">Ideas · ${span(t.lines)}</span><span class="muted">The agent proposes; Yosys checks every edit you apply against the chip's real gates.</span></div>
    ${msgs}
    <form class="ag-compose ag-ideas-compose" data-thread="${t.id}">
      <textarea class="text" id="ag-i-${t.id}" rows="2" aria-label="Ask the Ideas agent about ${span(t.lines)}" placeholder="${t.msgs.length ? "Ask for something else, or press Enter for more ideas" : "What would you like to change here? Leave it empty for the agent's own ideas, e.g. “make it count down”"}">${esc(drafts.get(t.id) ?? "")}</textarea>
      <div class="ag-actions"><span class="grow"></span><button type="button" class="btn sm" data-ag="ideas-close" data-thread="${t.id}">Close</button><button type="submit" class="btn sm primary"${busy ? " disabled" : ""}>${t.msgs.length ? "Ask" : "Get ideas"}</button></div>
    </form>
  </li>`;
}
