// Small DOM helpers shared by every view.

export const $ = <T extends Element = HTMLElement>(s: string, r: ParentNode = document) => r.querySelector<T>(s) as T;
export const $$ = <T extends Element = HTMLElement>(s: string, r: ParentNode = document) => [...r.querySelectorAll<T>(s)];
export const esc = (s: unknown) =>
  String(s ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]!);
export const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
export const reduceMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

export function html(strings: TemplateStringsArray, ...vals: unknown[]) {
  return strings.reduce((s, str, i) => s + str + (i < vals.length ? String(vals[i] ?? "") : ""), "");
}

/** Light Verilog syntax colouring (input is raw text; output is safe HTML). */
export function hiVerilog(line: string): string {
  const i = line.indexOf("//");
  const code = i >= 0 ? line.slice(0, i) : line, com = i >= 0 ? line.slice(i) : "";
  const h = code.replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]!)
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
  t.className = `overlay callout ${kind}`;
  Object.assign(t.style, { position: "fixed", left: "50%", bottom: "64px", transform: "translateX(-50%)", zIndex: "30", maxWidth: "min(92vw, 640px)" });
  t.textContent = text;
  document.body.append(t);
  setTimeout(() => t.remove(), 4200);
}
