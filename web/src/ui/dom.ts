// Small DOM helpers shared by every view.

export const $ = <T extends Element = HTMLElement>(s: string, r: ParentNode = document) => r.querySelector<T>(s) as T;
export const $$ = <T extends Element = HTMLElement>(s: string, r: ParentNode = document) => [...r.querySelectorAll<T>(s)];
export const esc = (s: unknown) =>
  String(s ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]!);
export const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
export const reduceMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Light Verilog syntax colouring (input is raw text; output is safe HTML). */
export function hiVerilog(line: string): string {
  const i = line.indexOf("//");
  const code = i >= 0 ? line.slice(0, i) : line, com = i >= 0 ? line.slice(i) : "";
  const h = esc(code)
    .replace(/\b(\d+'[sS]?[dbhoDBHO][0-9a-fA-F_xXzZ]+|\d+)\b/g, '<span class="num">$1</span>')
    .replace(/\b(module|endmodule|input|output|inout|wire|reg|logic|always|always_ff|always_comb|posedge|negedge|if|else|assign|genvar|generate|endgenerate|for|begin|end|case|endcase|default|parameter|localparam|integer)\b/g, '<span class="k">$1</span>')
    .replace(/(&lt;=|&amp;&amp;|\|\||==|!=|[\^~!?])/g, '<span class="op">$1</span>');
  return h + (com ? `<span class="c">${esc(com)}</span>` : "");
}

export function isTyping(): boolean {
  const a = document.activeElement as HTMLInputElement | null;
  if (!a) return false;
  if (a.isContentEditable) return true;
  return /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) && !["checkbox", "range", "radio", "button"].includes(a.type);
}

export function toast(text: string, kind: "good" | "bad" | "" = "") {
  const t = document.createElement("div");
  t.className = `toast ${kind}`;
  t.setAttribute("role", "status");
  t.textContent = text;
  document.body.append(t);
  setTimeout(() => t.remove(), 4200);
}

/** One code line's text cell. Leading indent becomes a CSS variable so wrapped lines hang under their own indent. */
export function codeText(line: string): string {
  const lead = /^[ \t]*/.exec(line)![0];
  const ind = [...lead].reduce((n, ch) => n + (ch === "\t" ? 4 : 1), 0);
  const body = line.slice(lead.length);
  return `<span class="t" style="--i:${ind}">${hiVerilog(body) || "\u00a0"}</span>`;
}

const WRAP_KEY = "decap.wrap";
/** Soft wrap for every code view (on by default, remembered per browser). */
export function wrapOn(): boolean { try { return localStorage.getItem(WRAP_KEY) !== "0"; } catch { return true; } }
export function setWrap(on: boolean) {
  document.body.classList.toggle("nowrap", !on);
  try { localStorage.setItem(WRAP_KEY, on ? "1" : "0"); } catch { /* private mode */ }
  document.querySelectorAll<HTMLElement>("[data-wrap]").forEach((b) => b.setAttribute("aria-pressed", String(on)));
}

/** Save text as a file (Verilog downloads). A trailing newline is added if missing. */
export function saveText(name: string, text: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text.endsWith("\n") ? text : text + "\n"], { type: "text/x-verilog" }));
  a.download = name;
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
