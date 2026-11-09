// A small, safe Markdown renderer for what the agent writes (guide answers, reports, explanations, replies).
// Everything is HTML-escaped FIRST, then a few constructs are turned on: paragraphs, line breaks, # headings,
// - / * / 1. lists, `code`, ```fenced code```, **bold**, simple | tables |, and > quotes. No raw HTML, no links,
// and no single-* or _ italics, because Verilog (a*b, tt_um_x, q_c12) is full of both.

import { esc } from "../ui/dom";

function inline(s: string): string {
  // s is already escaped; protect code spans first so ** inside code stays literal
  const codes: string[] = [];
  s = s.replace(/`([^`]+)`/g, (_, c: string) => { codes.push(c); return `\u0000${codes.length - 1}\u0000`; });
  s = s.replace(/\*\*([^*][\s\S]*?)\*\*/g, "<b>$1</b>");
  return s.replace(/\u0000(\d+)\u0000/g, (_, i: string) => `<code>${codes[Number(i)]}</code>`);
}

const isRow = (l: string) => /^\s*\|.*\|\s*$/.test(l);
const isSep = (l: string) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
const cells = (l: string) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim()));

/** Markdown text -> HTML string (safe to put in innerHTML). */
export function md(src: string | null | undefined): string {
  if (!src) return "";
  const lines = esc(String(src)).replace(/\r\n?/g, "\n").split("\n");
  const out: string[] = [];
  let para: string[] = [];
  const flush = () => { if (para.length) { out.push(`<p>${para.map(inline).join("<br>")}</p>`); para = []; } };

  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (/^\s*```/.test(l)) {                                   // fenced code
      flush();
      const body: string[] = [];
      while (++i < lines.length && !/^\s*```/.test(lines[i])) body.push(lines[i]);
      out.push(`<pre><code>${body.join("\n")}</code></pre>`);
      continue;
    }
    if (!l.trim()) { flush(); continue; }
    const h = l.match(/^\s*(#{1,4})\s+(.*)$/);
    if (h) { flush(); out.push(`<p class="md-h">${inline(h[2])}</p>`); continue; }
    if (isRow(l) && i + 1 < lines.length && isSep(lines[i + 1])) {   // table
      flush();
      const head = cells(l);
      i++;
      const rows: string[][] = [];
      while (i + 1 < lines.length && isRow(lines[i + 1])) rows.push(cells(lines[++i]));
      out.push(`<div class="md-tw"><table><thead><tr>${head.map((c) => `<th>${c}</th>`).join("")}</tr></thead>` +
        `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    const li = l.match(/^\s*([-*•]|\d+[.)])\s+(.*)$/);
    if (li) {                                                    // list (consecutive items)
      flush();
      const ordered = /\d/.test(li[1]);
      const items = [li[2]];
      while (i + 1 < lines.length) {
        const m = lines[i + 1].match(/^\s*([-*•]|\d+[.)])\s+(.*)$/);
        if (!m) break;
        items.push(m[2]); i++;
      }
      out.push(`<${ordered ? "ol" : "ul"}>${items.map((t) => `<li>${inline(t)}</li>`).join("")}</${ordered ? "ol" : "ul"}>`);
      continue;
    }
    const q = l.match(/^\s*&gt;\s?(.*)$/);
    if (q) { flush(); out.push(`<blockquote>${inline(q[1])}</blockquote>`); continue; }
    para.push(l);
  }
  flush();
  return `<div class="md">${out.join("")}</div>`;
}
