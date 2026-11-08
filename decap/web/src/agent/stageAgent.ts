// The Witness agent on the Explore (3D) view: hover a logic cell -> Explain / Suggest edit / Comment.
// Same actions as the Decompile code pane, but pointed at a physical component. The agent can only look;
// Yosys decides. `regionForCell` maps the hovered cell to the block it belongs to.

import { loadChip } from "../data/load";
import { regionForCell, runAgent } from "./agent";

let bar: HTMLDivElement | null = null;
let chip = "";
let cell = "";
let hideT = 0;

function ensureBar(): HTMLDivElement {
  if (bar) return bar;
  bar = document.createElement("div");
  bar.className = "ag-hover ag-stage";
  bar.setAttribute("role", "toolbar");
  bar.setAttribute("aria-label", "Agent actions for this component");
  bar.innerHTML = `
    <button type="button" data-ag="explain" title="The agent explains this component's block; Yosys checks it">Explain</button>
    <button type="button" data-ag="edit" title="Explain, then propose the proven line as an edit">Suggest edit</button>
    <button type="button" data-ag="comment" title="Open this chip's decompiled Verilog to comment there">Comment</button>`;
  bar.addEventListener("mouseenter", () => clearTimeout(hideT));
  bar.addEventListener("mouseleave", () => { hideT = window.setTimeout(hide, 250); });
  bar.addEventListener("click", (e) => {
    const b = (e.target as HTMLElement).closest<HTMLElement>("[data-ag]");
    if (!b) return;
    act(b.dataset.ag as "explain" | "edit" | "comment");
    hide();
  });
  document.body.append(bar);
  return bar;
}

let lastCell = "";   // don't chase the cursor's fine movement within one cell

/** Called by the Stage's onHoverCell: show the toolbar once for the hovered cell, then hold it still so the
 *  cursor can actually reach the buttons. Leaving the cell hides it after a grace period (so the buttons don't
 *  vanish mid-reach); entering the bar cancels the hide. */
export function hover(chipName: string, cellId: string | null, x: number, y: number) {
  if (!cellId) {
    if (bar?.classList.contains("on")) { clearTimeout(hideT); hideT = window.setTimeout(hide, 450); }
    return;
  }
  clearTimeout(hideT);
  chip = chipName;
  cell = cellId;
  const b = ensureBar();
  if (!b.classList.contains("on") || cellId !== lastCell) {   // position once per cell
    b.style.left = `${x + 14}px`;
    b.style.top = `${y - 12}px`;
    b.classList.add("on");
  }
  lastCell = cellId;
}

export function hide() {
  bar?.classList.remove("on");
  cell = "";
  lastCell = "";
}

function act(what: "explain" | "edit" | "comment") {
  if (!chip || !cell) return;
  if (what === "comment") {   // comments live on the Decompile page's lines
    location.hash = `#/chip/${encodeURIComponent(chip)}`;
    document.dispatchEvent(new CustomEvent("agent:open"));
    return;
  }
  document.dispatchEvent(new CustomEvent("agent:open"));
  void loadChip(chip).then(({ data }) => {
    const reg = regionForCell(data, cell);
    if (!reg) return;
    void runAgent({
      chip, scope: reg.scope, label: `3D cell ${cell} · ${reg.label}`, origin: "stage",
      line: reg.lines[0] || undefined, lines: reg.lines[0] ? reg.lines : undefined,
      suggestEdit: what === "edit", verilog: data.decompiled.verilog,
    });
  });
}
