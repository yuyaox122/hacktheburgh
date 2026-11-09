// Minecraft page: compile Verilog (or a chip Decap decompiled) to real redstone, build it in a live server,
// and drive it from here. Levers flip in the world; lamps are read back from the world, next to what the netlist predicts.

import { buses, pendingBuild, rs, type RsBuild, type RsCompileIn, type RsHealth, type RsState } from "../api/redstone";
import { Redstone3D } from "../three/redstone3d";
import { $, $$, esc, toast } from "../ui/dom";
import { icon } from "../ui/icons";

const EXAMPLE = `// Any small synthesisable Verilog: one clock (clk), optional async reset.
// Multiply-accumulate: every clock, acc += a * b.
module mac (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [1:0] a,
    input  wire [1:0] b,
    output reg  [3:0] acc
);
    always @(posedge clk or negedge rst_n)
        if (!rst_n) acc <= 0;
        else acc <= acc + a * b;
endmodule
`;

/** A bus's value from its bits' own indices (buses can have gaps: tied or unused bits are not built). */
const word = (bits: string[], v: Record<string, number>) => bits.reduce((n, b) => n + (v[b] ? 2 ** +(b.match(/\[(\d+)\]$/)?.[1] ?? 0) : 0), 0);

export class MinecraftPage {
  readonly el: HTMLElement;
  private view = new Redstone3D();
  private builds: RsBuild[] = [];
  private cur: RsBuild | null = null;
  private st: RsState | null = null;
  private health: RsHealth | null = null;
  private healthErr: string | null = null;
  private busy: string | null = null;
  private sprint = true;
  private timer = 0;
  private polling = false;
  private settleSince = 0;

  constructor() {
    this.el = document.createElement("div");
    this.el.className = "mc";
    this.el.innerHTML = `
      <aside class="mc-side">
        <section class="sect" id="mcServer"></section>
        <section class="sect" id="mcPresetsS" hidden><h4>Ready-made</h4><div class="mc-builds" id="mcPresets"></div></section>
        <section class="sect"><h4>Builds</h4><div class="mc-builds" id="mcBuilds"></div></section>
        <section class="sect">
          <h4>Your Verilog</h4>
          <textarea id="mcSrc" spellcheck="false" rows="12">${esc(EXAMPLE)}</textarea>
          <div class="acts"><button class="btn primary" type="button" id="mcCompile">${icon.cube}Compile and build</button></div>
          <p class="fine">Yosys turns it into NOT, AND-NOT, OR gates and latches; each becomes a redstone tile. Before building, the redstone netlist is checked against iverilog running your source.</p>
        </section>
      </aside>
      <section class="mc-main">
        <header class="mc-head" id="mcHead"></header>
        <div class="mc-view" id="mcView"></div>
        <div class="mc-io" id="mcIO"></div>
      </section>`;
    $("#mcView", this.el).append(this.view.el);
    this.view.onLever = (bit) => this.flip(bit);
    $("#mcCompile", this.el).addEventListener("click", () => {
      const v = $<HTMLTextAreaElement>("#mcSrc", this.el).value;
      void this.compileAndBuild({ verilog: v });
    });
  }

  /** want: a build id from the URL (#/minecraft/<id>). */
  async mount(want: string | null = null) {
    this.view.start();
    this.startPolling();
    await this.refresh();
    const req = pendingBuild.req;
    pendingBuild.req = null;
    if (req) { await this.compileAndBuild(req); return; }
    const id = want && this.builds.some((b) => b.id === want) ? want
      : this.cur?.id ?? this.builds.find((b) => !b.prebuilt)?.id ?? this.builds[0]?.id;
    if (id && id !== this.cur?.id) await this.select(id);
  }
  unmount() { this.view.stop(); this.stopPolling(); }

  context() {
    return { page: "minecraft", build: this.cur && { id: this.cur.id, title: this.cur.title, inputs: this.cur.inputs, outputs: this.cur.outputs,
      check: this.cur.check, size: this.cur.size, blocks: this.cur.blocks }, state: this.st };
  }

  // ---------------------------------------------------------------- data

  private async refresh() {
    try { this.health = await rs.health(); this.healthErr = null; } catch (e) { this.health = null; this.healthErr = (e as Error).message; }
    try { this.builds = await rs.builds(); } catch { this.builds = []; }
    try {
      const ps = await rs.presets();
      $("#mcPresetsS", this.el).hidden = !ps.length;
      $("#mcPresets", this.el).innerHTML = ps.map((p) => `<button type="button" class="mc-build" data-preset="${esc(p.id)}"><b>${esc(p.title)}</b><span>Compile and build</span></button>`).join("");
      $$<HTMLElement>("[data-preset]", this.el).forEach((x) => x.addEventListener("click", () => void this.compileAndBuild({ preset: x.dataset.preset! })));
    } catch { $("#mcPresetsS", this.el).hidden = true; }
    this.renderServer(); this.renderBuilds();
    if (!this.cur) this.renderHead();
  }

  private async select(id: string) {
    const b = this.builds.find((x) => x.id === id);
    if (!b) return;
    if (location.hash !== `#/minecraft/${id}`) history.replaceState(null, "", `#/minecraft/${id}`);
    this.cur = b; this.st = null;
    this.renderBuilds(); this.renderHead(); this.renderIO();
    try {
      const l = await rs.layout(id);
      if (this.cur?.id !== id) return;
      this.view.setLayout(l.layout);
    } catch (e) { toast((e as Error).message, "bad"); }
    void this.poll();
  }

  private async compileAndBuild(req: RsCompileIn) {
    if (this.busy) return;
    this.busy = "Compiling"; this.renderHead();
    const t0 = performance.now();
    const tick = setInterval(() => { const t = $("#mcT", this.el); if (t) t.textContent = ((performance.now() - t0) / 1000).toFixed(0); }, 250);
    try {
      const b = await rs.compile(req);
      this.builds = [b, ...this.builds.filter((x) => x.id !== b.id)];
      await this.select(b.id);
      if (b.check && b.check.mismatches) toast(`Careful: the redstone netlist disagrees with iverilog on ${b.check.mismatches} of ${b.check.vectors} vectors`, "bad");
      if (this.health?.ok) await this.buildInWorld();
    } catch (e) { toast((e as Error).message, "bad"); }
    finally { clearInterval(tick); this.busy = null; this.renderHead(); }
  }

  private async buildInWorld() {
    if (!this.cur) return;
    const id = this.cur.id;
    this.busy = "Building in Minecraft"; this.renderHead();
    try {
      const r = await rs.build(id);
      this.cur = { ...this.cur, ...r, built: true };
      this.builds = this.builds.map((x) => (x.id === id ? this.cur! : x));
      toast(`Built ${r.commands.toLocaleString()} blocks in ${r.build_s.toFixed(1)} s`, "good");
      void rs.camera(id, "levers").catch(() => undefined);
    } catch (e) { toast((e as Error).message, "bad"); }
    finally { this.busy = null; this.renderBuilds(); this.renderHead(); this.renderIO(); }
  }

  private startPolling() { if (!this.polling) { this.polling = true; this.loop(); } }
  private stopPolling() { this.polling = false; clearTimeout(this.timer); }
  private loop = () => {
    if (!this.polling) return;
    void this.poll().finally(() => { if (this.polling) this.timer = window.setTimeout(this.loop, 500); });
  };

  private async poll() {
    const b = this.cur;
    if (!b || !b.built || !this.health?.ok || this.busy) return;
    try {
      const s = await rs.state(b.id);
      if (this.cur?.id !== b.id) return;
      const changed = !this.st || this.st.settled !== s.settled;
      if ((!this.st || this.st.settled) && !s.settled) this.settleSince = performance.now();
      this.st = s;
      this.view.paint(s.outputs, s.inputs);
      changed ? this.renderIO() : this.paintIO();
    } catch { /* bridge hiccup: keep the last state */ }
  }

  // ---------------------------------------------------------------- actions

  private async setBits(bits: Record<string, number>) {
    if (!this.cur) return;
    this.settleSince = performance.now();
    if (this.st) { this.st = { ...this.st, inputs: { ...this.st.inputs, ...bits }, settled: false }; this.paintIO(); }
    try {
      const s = await rs.set(this.cur.id, bits, this.sprint);
      this.st = s; this.view.paint(s.outputs, s.inputs); this.renderIO();
    } catch (e) { toast((e as Error).message, "bad"); }
  }
  private flip(bit: string) { if (this.st && this.cur?.built) void this.setBits({ [bit]: this.st.inputs[bit] ? 0 : 1 }); }

  private setBus(bits: string[], n: number) {
    const v: Record<string, number> = {};
    bits.forEach((b) => (v[b] = (n >> +(b.match(/\[(\d+)\]$/)?.[1] ?? 0)) & 1));
    void this.setBits(v);
  }

  private async clock() {
    if (!this.cur) return;
    this.settleSince = performance.now();
    try { await rs.clock(this.cur.id, this.sprint); toast(this.sprint ? "Clock edge sent (fast-forwarded)" : "Clock edge playing at normal speed (about a minute)"); }
    catch (e) { toast((e as Error).message, "bad"); }
  }

  private async reset() {
    if (!this.cur) return;
    const rb = this.cur.inputs.find((b) => /^(rst_n|reset_n|rstn|resetn)$/.test(b));
    const ra = this.cur.inputs.find((b) => /^(rst|reset)$/.test(b));
    if (rb) { await this.setBits({ [rb]: 0 }); await this.setBits({ [rb]: 1 }); }
    else if (ra) { await this.setBits({ [ra]: 1 }); await this.setBits({ [ra]: 0 }); }
  }

  // ---------------------------------------------------------------- render

  private renderServer() {
    const h = this.health;
    $("#mcServer", this.el).innerHTML = h?.ok
      ? `<h4><span class="mc-dot on"></span>Minecraft server live</h4>
         <p>Join in <b>${esc(h.version)}</b>: Multiplayer, Direct Connect, <code class="mc-addr">${esc(h.lan_ip || "localhost")}:${h.game_port}</code></p>
         <p class="fine">${esc(h.players.replace(/There are (\d+) of a max of \d+ players online:\s*/, (_m, n) => `${n} online `))}</p>`
      : `<h4><span class="mc-dot"></span>Minecraft server offline</h4><p class="fine">${esc(this.healthErr ?? "")}</p>
         <p class="fine">The 3-D view still works: pick a build to see every block it places.</p>
         <div class="acts"><button class="btn sm" type="button" id="mcRetry">${icon.reset}Retry</button></div>`;
    $("#mcRetry", this.el)?.addEventListener("click", () => void this.refresh());
  }

  private renderBuilds() {
    const el = $("#mcBuilds", this.el);
    el.innerHTML = this.builds.length ? this.builds.map((b) => `
      <button type="button" class="mc-build" data-id="${esc(b.id)}" aria-pressed="${b.id === this.cur?.id}">
        <b>${esc(b.title)}</b>
        <span>${b.blocks.toLocaleString()} blocks · ${b.size[0]}×${b.size[1]}${b.built ? "" : " · <i>not built</i>"}</span>
      </button>`).join("") : `<p class="fine">No builds yet.</p>`;
    $$<HTMLElement>("[data-id]", el).forEach((x) => x.addEventListener("click", () => void this.select(x.dataset.id!)));
  }

  private renderHead() {
    const head = $("#mcHead", this.el);
    const b = this.cur;
    if (!b) { head.innerHTML = this.busy ? `<span class="spin"></span> ${esc(this.busy)} <span id="mcT">0</span> s` : `<span class="muted">Pick a build, or compile your own Verilog.</span>`; return; }
    const gates = b.prims?.prims ?? (b.stats.units as number | undefined);
    const secs = (2 * b.settle_rt) / 20;
    const ck = b.check;
    head.innerHTML = `
      <div class="ttl"><h2>${esc(b.title)}</h2>
        <div class="badges">
          ${ck ? `<span class="verdict ${ck.mismatches ? "failed" : "proven"}" title="Redstone netlist vs ${esc(ck.against)}, ${ck.vectors} random ${ck.sequential ? "clock cycles" : "input vectors"}">${ck.mismatches ? `${ck.mismatches} of ${ck.vectors} differ` : `Matches the Verilog on ${ck.vectors}/${ck.vectors}`}</span>` : b.prebuilt ? `<span class="verdict proven" title="Checked in vanilla Minecraft 1.21.11 (redstone/check.py)">Checked in vanilla Minecraft</span>` : ""}
          ${b.origin?.variant === "planted" ? `<span class="pill warn">Trigger ${esc(b.origin.n ?? "")} planted</span>` : ""}
          ${b.origin?.variant === "fixed" ? `<span class="pill">Trigger ${esc(b.origin.n ?? "")} removed by Fix</span>` : ""}
        </div>
      </div>
      <div class="chipstats">
        ${gates ? `<span><b>${gates}</b>Redstone gates</span>` : ""}
        <span><b>${(b.blocks / 1000).toFixed(0)}k</b>Blocks</span>
        <span><b>${b.size[0]}×${b.size[1]}</b>Footprint</span>
        <span title="Static timing: ${b.settle_rt} redstone ticks"><b>${secs.toFixed(0)} s</b>Worst-case settle</span>
      </div>
      <span class="grow"></span>
      <div class="acts">
        ${this.busy ? `<span class="muted"><span class="spin"></span> ${esc(this.busy)} <span id="mcT">0</span> s</span>` : ""}
        <div class="seg" role="group" aria-label="Move players in Minecraft">
          <button type="button" data-cam="levers" title="Teleport players to the levers">Levers</button>
          <button type="button" data-cam="overview" title="Teleport players above the build">Above</button>
          <button type="button" data-cam="side" title="Teleport players beside the build">Side</button>
        </div>
        <button class="btn plain sm" type="button" id="mcFit" title="Reset the 3-D view">${icon.fit}</button>
        <button class="btn ${b.built ? "" : "primary"}" type="button" id="mcBuild" ${this.busy || !this.health?.ok ? "disabled" : ""}>${icon.cube}${b.built ? "Rebuild" : "Build in Minecraft"}</button>
      </div>`;
    $("#mcBuild", head)?.addEventListener("click", () => void this.buildInWorld());
    $("#mcFit", head)?.addEventListener("click", () => this.view.overview());
    $$<HTMLElement>("[data-cam]", head).forEach((x) => x.addEventListener("click", () => {
      if (b.built) rs.camera(b.id, x.dataset.cam as "levers").then(() => toast("Players moved")).catch((e) => toast((e as Error).message, "bad"));
    }));
  }

  private renderIO() {
    const io = $("#mcIO", this.el);
    const b = this.cur;
    if (!b) { io.innerHTML = ""; return; }
    if (!b.built || !this.health?.ok) {
      io.innerHTML = `<p class="fine mc-note">${this.health?.ok ? "Build it in Minecraft to drive it from here." : "Start the Minecraft server and the bridge to drive this build live."} Click a lever in the 3-D view or use the switches below once it's live.</p>`;
      return;
    }
    const s = this.st;
    const ins = buses(b.inputs), outs = buses(b.outputs);
    const trig = (b.origin?.variant === "planted" || b.origin?.variant === "fixed") && b.origin.n != null ? ins.find((x) => x.name === b.origin!.operand) ?? ins.find((x) => x.name === "ui_in") : null;
    io.innerHTML = `
      <div class="mc-col">
        <div class="eyebrow">Inputs <span class="muted">(levers)</span></div>
        ${ins.map((bus) => `
          <div class="mc-bus" data-bus="${esc(bus.name)}">
            <span class="nm">${esc(bus.name)}</span>
            <span class="bits">${bus.bits.map((bit) => `<button type="button" class="mc-bit" data-bit="${esc(bit)}" title="${esc(bit)}" aria-pressed="${!!s?.inputs[bit]}"></button>`).join("")}</span>
            ${bus.bits.length > 1 ? `<input class="mc-num" type="number" min="0" max="${word(bus.bits, Object.fromEntries(bus.bits.map((q) => [q, 1])))}" value="${s ? word(bus.bits, s.inputs) : 0}" aria-label="${esc(bus.name)} as a number">` : ""}
          </div>`).join("")}
        <div class="acts">
          ${b.sequential ? `<button class="btn" type="button" id="mcClock">${icon.step}Clock</button><button class="btn plain" type="button" id="mcReset">${icon.reset}Reset</button>` : ""}
          ${trig ? `<button class="btn primary" type="button" id="mcTrig" title="Set ${esc(trig.name)} to the planted number">Type ${esc(b.origin!.n)} into ${esc(trig.name)}</button>` : ""}
          <label class="mc-sprint"><input type="checkbox" id="mcSprint" ${this.sprint ? "checked" : ""}> Fast-forward</label>
        </div>
      </div>
      <div class="mc-col">
        <div class="eyebrow">Outputs <span class="muted">(lamps, read from the world)</span> <span class="mc-settle" id="mcSettle"></span></div>
        ${outs.map((bus) => `
          <div class="mc-bus out" data-obus="${esc(bus.name)}">
            <span class="nm">${esc(bus.name)}</span>
            <span class="bits">${bus.bits.map((bit) => `<i class="mc-lamp" data-lamp="${esc(bit)}" title="${esc(bit)}"></i>`).join("")}</span>
            <span class="val mono" data-val="${esc(bus.name)}"></span>
          </div>`).join("")}
      </div>`;
    $$<HTMLElement>("[data-bit]", io).forEach((x) => x.addEventListener("click", () => this.flip(x.dataset.bit!)));
    $$<HTMLInputElement>(".mc-num", io).forEach((x) => x.addEventListener("change", () => {
      const bus = ins.find((q) => q.name === x.closest<HTMLElement>("[data-bus]")!.dataset.bus)!;
      this.setBus(bus.bits, Math.max(0, Math.floor(+x.value || 0)));
    }));
    $("#mcClock", io)?.addEventListener("click", () => void this.clock());
    $("#mcReset", io)?.addEventListener("click", () => void this.reset());
    $("#mcTrig", io)?.addEventListener("click", () => trig && this.setBus(trig.bits, Number(b.origin!.n)));
    $<HTMLInputElement>("#mcSprint", io)?.addEventListener("change", (e) => {
      this.sprint = (e.target as HTMLInputElement).checked;
      if (!this.sprint) void rs.speed(20).catch(() => undefined);
    });
    this.paintIO();
  }

  /** Cheap update of switches, lamps and values without rebuilding the panel (keeps focus in number fields). */
  private paintIO() {
    const s = this.st, b = this.cur, io = $("#mcIO", this.el);
    if (!s || !b) return;
    $$<HTMLElement>("[data-bit]", io).forEach((x) => x.setAttribute("aria-pressed", String(!!s.inputs[x.dataset.bit!])));
    $$<HTMLElement>("[data-lamp]", io).forEach((x) => {
      const bit = x.dataset.lamp!;
      x.classList.toggle("on", !!s.outputs[bit]);
      x.classList.toggle("bad", s.outputs[bit] !== s.expected[bit]);
    });
    for (const bus of buses(b.outputs)) {
      const v = $(`[data-val="${CSS.escape(bus.name)}"]`, io);
      if (v) v.innerHTML = `${word(bus.bits, s.outputs)}${s.settled ? "" : ` <span class="muted">→ ${word(bus.bits, s.expected)}</span>`}`;
    }
    for (const bus of buses(b.inputs)) {
      const n = $<HTMLInputElement>(`[data-bus="${CSS.escape(bus.name)}"] .mc-num`, io);
      if (n && document.activeElement !== n) n.value = String(word(bus.bits, s.inputs));
    }
    const st = $("#mcSettle", io);
    if (st) {
      const secs = (performance.now() - this.settleSince) / 1000;
      // the worst case at normal speed is 2 * settle_rt game ticks; past that (plus slack) it is a real disagreement
      const late = secs > Math.max(8, (2 * b.settle_rt) / 20 + 5) * (this.sprint ? 0.5 : 1);
      st.className = `mc-settle ${s.settled ? "ok" : late ? "bad" : "run"}`;
      st.innerHTML = s.settled ? "Matches the simulation"
        : late ? `Differs from the simulation${b.sequential ? " (its flip-flops may be out of step: press Reset)" : ""}`
        : `<span class="spin"></span>Signal travelling… ${secs.toFixed(1)} s`;
    }
  }

  destroy() { this.view.destroy(); this.stopPolling(); }
}
