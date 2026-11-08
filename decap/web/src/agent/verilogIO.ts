// Download and upload Verilog on the two code panes: Decompile (the decompiled Verilog, with any agent edits you
// accepted) and Explore (the chip's source file). Upload takes your own .v file and asks Yosys whether this chip
// really implements it (decap_agent/source_check.py, streamed into the Agent drawer).
//
// Self-contained: it decorates the panes when they appear (MutationObserver), so the page files stay untouched.

import { loadChip, loadDesign } from "../data/load";
import { toast } from "../ui/dom";
import { icon } from "../ui/icons";
import { store } from "./store";

const DOWNLOAD = `<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 2.8v7.7M4.8 7.3 8 10.5l3.2-3.2"/><path d="M2.5 10.5v1.7a1.3 1.3 0 0 0 1.3 1.3h8.4a1.3 1.3 0 0 0 1.3-1.3v-1.7"/></svg>`;
const MAX_BYTES = 400_000;

let chip = "";
let picker: HTMLInputElement | null = null;

/** Called by the Agent panel whenever the current chip changes (both pages set it). */
export function setChip(c: string | null) { chip = c ?? ""; }

/** The decompiled Verilog with the agent edits you accepted applied, bottom-up so line numbers stay valid. */
export function withAcceptedEdits(chipId: string, verilog: string): string {
  const lines = verilog.split("\n");
  const accepted = store.edits(chipId).filter((e) => e.state === "accepted").sort((a, b) => b.lines[0] - a.lines[0]);
  for (const e of accepted) lines.splice(e.lines[0] - 1, e.lines[1] - e.lines[0] + 1, ...e.replacement);
  return lines.join("\n");
}

function save(name: string, text: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text.endsWith("\n") ? text : text + "\n"], { type: "text/x-verilog" }));
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

async function download(kind: "dec" | "src") {
  if (!chip) return;
  try {
    if (kind === "dec") {
      const { data } = await loadChip(chip);
      const n = store.edits(chip).filter((e) => e.state === "accepted").length;
      save(`${chip}_decompiled.v`, withAcceptedEdits(chip, data.decompiled.verilog));
      toast(n ? `Downloaded the decompiled Verilog with your ${n} accepted edit${n > 1 ? "s" : ""}` : "Downloaded the decompiled Verilog");
    } else {
      const { design } = await loadDesign(chip);
      const files = Object.entries(design.source.files ?? {});
      const shown = document.querySelector(".code-pane .meta .file")?.textContent?.trim();
      const [name, text] = files.find(([f]) => f === shown) ?? files[0] ?? [];
      if (!name) { toast("This chip has no source file to download"); return; }
      save(name.split("/").pop() || `${chip}.v`, text ?? "");
      toast(`Downloaded ${name}`);
    }
  } catch (e) {
    toast(`Download failed: ${(e as Error).message}`);
  }
}

function upload() {
  if (!chip) return;
  if (!picker) {
    picker = document.createElement("input");
    picker.type = "file";
    picker.accept = ".v,.sv,.vh,.txt";
    picker.hidden = true;
    picker.addEventListener("change", async () => {
      const f = picker!.files?.[0];
      picker!.value = "";
      if (!f) return;
      if (f.size > MAX_BYTES) { toast(`That file is over ${MAX_BYTES / 1000} kB`); return; }
      const text = await f.text();
      document.dispatchEvent(new CustomEvent("agent:upload", { detail: { chip, filename: f.name, text } }));
    });
    document.body.append(picker);
  }
  picker.click();
}

const bar = (kind: "dec" | "src") => `<span class="vio">
  <button class="btn plain sm" type="button" data-vio="dl-${kind}" title="${kind === "dec" ? "Download the decompiled Verilog (with edits you accepted)" : "Download this source file"}">${DOWNLOAD}Download</button>
  <button class="btn plain sm" type="button" data-vio="up" title="Upload your own Verilog and Yosys checks whether this chip implements it">${icon.upload}Check my Verilog</button>
</span>`;

function decorate() {
  const dec = document.querySelector("#cpCode")?.closest(".pane")?.querySelector(".pane-h");
  if (dec && !dec.querySelector(".vio")) dec.insertAdjacentHTML("beforeend", bar("dec"));
  const src = document.querySelector("#stSrc")?.closest(".pane")?.querySelector(".chiphdr .meta");
  if (src && !src.querySelector(".vio")) src.insertAdjacentHTML("beforeend", bar("src"));
}

let raf = 0;
let installed = false;
export function install() {
  if (installed) return;
  installed = true;
  new MutationObserver(() => { cancelAnimationFrame(raf); raf = requestAnimationFrame(decorate); })
    .observe(document.body, { childList: true, subtree: true });
  document.addEventListener("click", (e) => {
    const b = (e.target as HTMLElement).closest<HTMLElement>("[data-vio]");
    if (!b) return;
    const v = b.dataset.vio;
    if (v === "up") upload();
    else void download(v === "dl-dec" ? "dec" : "src");
  });
  decorate();
}
