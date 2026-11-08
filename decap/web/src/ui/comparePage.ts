// Compare: paste or upload Verilog or a netlist, get the nearest real chips and why they match.

import type { CompareResult } from "../data/types";
import { compare, loadBench } from "../data/load";
import { $, esc, hiVerilog } from "./dom";
import { icon } from "./icons";

export class ComparePage {
  readonly el = document.createElement("div");
  private res: CompareResult | null = null;
  private err: string | null = null;
  private busy = false;
  constructor() { this.el.className = "compare"; }

  async load() {
    const bench = await loadBench().catch(() => null);
    this.el.innerHTML = `
      <section class="headline"><span class="eyebrow">Compare</span><h1>Chips like yours.</h1>
        <p>Paste a small design. bASIC finds the real Tiny Tapeout chips built the most like it, and shows how they did it.</p></section>
      <div class="card">
        <div class="acts">
          <div class="seg" role="group" aria-label="Input kind"><button type="button" data-kind="verilog" aria-pressed="true">Verilog</button><button type="button" data-kind="netlist" aria-pressed="false">Netlist</button></div>
          <span class="grow"></span>
          <select class="sel" id="cmpChip" aria-label="Or start from a benchmark chip"><option value="">Start from a chip</option>${(bench?.data.chips ?? []).map((c) => `<option value="${esc(c.chip)}">${esc(c.title || c.chip)}</option>`).join("")}</select>
          <label class="btn" for="cmpFile">${icon.upload}Upload</label><input id="cmpFile" type="file" accept=".v,.sv,.txt" hidden>
        </div>
        <textarea class="text" id="cmpText" spellcheck="false" aria-label="Your design" placeholder="module my_counter (input clk, input rst_n, output reg [7:0] q);&#10;  always @(posedge clk or negedge rst_n)&#10;    if (!rst_n) q <= 0; else q <= q + 1;&#10;endmodule"></textarea>
        <div class="acts"><span class="fine" id="cmpNote">Up to about 500 logic cells.</span><span class="grow"></span><button class="btn primary" type="button" id="cmpGo">Find similar chips</button></div>
      </div>
      <div id="cmpOut"></div>`;
    let kind = "verilog";
    this.el.querySelectorAll<HTMLElement>("[data-kind]").forEach((b) => b.addEventListener("click", () => {
      kind = b.dataset.kind!; this.el.querySelectorAll("[data-kind]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    }));
    $<HTMLInputElement>("#cmpFile", this.el).addEventListener("change", async (e) => {
      const f = (e.target as HTMLInputElement).files?.[0];
      if (!f) return;
      const t = await f.text();
      $<HTMLTextAreaElement>("#cmpText", this.el).value = t;
      if (/sky130_fd_sc_hd__/.test(t)) { kind = "netlist"; this.el.querySelectorAll("[data-kind]").forEach((x) => x.setAttribute("aria-pressed", String((x as HTMLElement).dataset.kind === "netlist"))); }
      $("#cmpNote", this.el).textContent = `${f.name}, ${t.split("\n").length} lines`;
    });
    $("#cmpGo", this.el).addEventListener("click", () => {
      const chip = $<HTMLSelectElement>("#cmpChip", this.el).value, text = $<HTMLTextAreaElement>("#cmpText", this.el).value.trim();
      if (chip && !text) this.run({ chip });
      else if (text) this.run(kind === "netlist" ? { netlist: text } : { verilog: text });
      else $("#cmpNote", this.el).textContent = "Paste a design, upload a file or start from a chip.";
    });
  }

  private async run(body: { verilog?: string; netlist?: string; chip?: string }) {
    if (this.busy) return;
    this.busy = true; this.err = null; this.res = null;
    const out = $("#cmpOut", this.el);
    out.innerHTML = `<div class="empty"><span class="spin"></span>Synthesising and ranking. This can take a minute.</div>`;
    try { this.res = await compare(body); }
    catch (e) { this.err = (e as Error).message; }
    this.busy = false;
    this.renderOut();
  }

  private renderOut() {
    const out = $("#cmpOut", this.el);
    if (this.err) { out.innerHTML = `<div class="callout bad"><b>Compare is not available right now.</b><span>It needs the bASIC API with Yosys running. ${esc(this.err)}</span></div>`; return; }
    const r = this.res;
    if (!r) { out.innerHTML = ""; return; }
    const fp = Object.entries(r.fingerprint ?? {}).filter(([, v]) => typeof v !== "object").map(([k, v]) => `<span class="pill">${esc(k)} ${esc(v)}</span>`).join(" ");
    const blocks = (b: unknown) => Array.isArray(b) ? b.join(", ") : b && typeof b === "object" ? Object.entries(b as Record<string, number>).map(([k, v]) => `${v} ${k}`).join(", ") : String(b ?? "");
    out.innerHTML = `<div class="group" style="margin-bottom:16px"><span class="eyebrow">Your fingerprint</span><div class="acts">${fp || `<span class="muted">None returned</span>`}</div></div>
      <div class="ncards">${r.neighbours.map((n, i) => `
        <article class="ncard">
          <div class="hd"><span class="rank">${i + 1}</span><a href="#/chip/${encodeURIComponent(n.chip)}">${esc(n.title || n.chip)}</a><span class="mono fine">${esc(n.chip)}</span><span class="grow"></span>
            <span class="score" title="Similarity ${n.score.toFixed(2)}"><i style="width:${Math.max(0, Math.min(1, n.score)) * 100}%"></i></span><span class="mono">${n.score.toFixed(2)}</span></div>
          <p>${esc(n.why)}</p>
          <div class="row"><span>${n.logic_cells} logic cells</span><span>${n.area_um2.toFixed(0)} µm²</span><span>${esc(blocks(n.blocks))}</span></div>
          ${n.excerpt ? `<pre>${n.excerpt.split("\n").map(hiVerilog).join("\n")}</pre>` : ""}
        </article>`).join("") || `<p class="muted">No similar chips found.</p>`}</div>`;
  }
  context() { return { page: "compare", result: this.res ? { fingerprint: this.res.fingerprint, neighbours: this.res.neighbours.map((n) => ({ chip: n.chip, score: n.score, why: n.why })) } : null }; }
}
