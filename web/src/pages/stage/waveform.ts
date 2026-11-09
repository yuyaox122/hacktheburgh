// The simulator bar's logic analyser: the last clocks of each lane, drawn on a canvas in the token colours.

import { css } from "../../ui/dom";

/** One lane: a single wire (a square wave) or a named number drawn as a bus with its value written in. */
export interface Lane { label: string; bits: string[]; out: boolean }

/** Draw `history` (one snapshot of net values per clock, oldest first) into `cv`, `n` clocks wide. */
export function drawWave(cv: HTMLCanvasElement, lanes: Lane[], history: Record<string, number>[], n = 40) {
  const dpr = Math.min(devicePixelRatio, 2), w = cv.clientWidth, h = cv.clientHeight;
  if (!w || !h) return;
  cv.width = w * dpr; cv.height = h * dpr;
  const g = cv.getContext("2d")!;
  g.scale(dpr, dpr); g.clearRect(0, 0, w, h);
  if (!lanes.length) return;
  const font = css("--font-body") || "system-ui";
  g.font = `500 10.5px ${font}`;
  const lw = Math.min(110, Math.max(36, ...lanes.map((l) => g.measureText(l.label).width + 12)));
  const rowH = h / lanes.length, step = (w - lw) / n;
  const first = n - history.length;
  const inCol = css("--muted"), outCol = css("--high"), fg = css("--fg"), grid = css("--hover");
  // faint clock grid
  g.strokeStyle = grid; g.lineWidth = 1; g.beginPath();
  for (let i = 0; i <= n; i += 5) { const x = Math.round(lw + i * step) + 0.5; g.moveTo(x, 0); g.lineTo(x, h); }
  g.stroke();
  lanes.forEach((lane, r) => {
    const y0 = r * rowH, pad = Math.min(4, rowH * 0.18), hi = y0 + pad, lo = y0 + rowH - pad, mid = (hi + lo) / 2;
    const col = lane.out ? outCol : inCol;
    g.fillStyle = lane.out ? fg : inCol;
    g.font = `500 10.5px ${font}`; g.textBaseline = "middle";
    g.fillText(lane.label, 0, mid, lw - 8);
    if (!history.length) return;
    g.strokeStyle = col; g.lineWidth = 1.3;
    if (lane.bits.length === 1) {
      g.beginPath();
      let prevY: number | null = null;
      history.forEach((snap, i) => {
        const x = lw + (first + i) * step, y = snap[lane.bits[0]] ? hi : lo;
        if (prevY === null) g.moveTo(x, y); else { g.lineTo(x, prevY); g.lineTo(x, y); }
        g.lineTo(x + step, y); prevY = y;
      });
      g.stroke();
      return;
    }
    // bus: runs of equal value, drawn as a lane with crossings at each change and the value written inside
    const vals = history.map((s) => lane.bits.reduce((acc, b, k) => acc + ((s[b] ?? 0) << k), 0));
    const runs: { v: number; a: number; b: number }[] = [];
    vals.forEach((v, i) => { const last = runs[runs.length - 1]; if (last && last.v === v) last.b = i + 1; else runs.push({ v, a: i, b: i + 1 }); });
    const k = Math.min(3, step / 2);
    g.font = `11px ${css("--font-mono") || "monospace"}`; g.textAlign = "center";
    for (const run of runs) {
      const xa = lw + (first + run.a) * step, xb = lw + (first + run.b) * step;
      g.beginPath();
      g.moveTo(xa, mid); g.lineTo(xa + k, hi); g.lineTo(xb - k, hi); g.lineTo(xb, mid); g.lineTo(xb - k, lo); g.lineTo(xa + k, lo); g.closePath();
      g.fillStyle = col; g.globalAlpha = lane.out ? 0.12 : 0.08; g.fill(); g.globalAlpha = 1; g.stroke();
      const label = String(run.v);
      if (xb - xa - 2 * k > g.measureText(label).width + 4) { g.fillStyle = lane.out ? outCol : fg; g.fillText(label, (xa + xb) / 2, mid + 0.5); }
    }
    g.textAlign = "start";
  });
}
