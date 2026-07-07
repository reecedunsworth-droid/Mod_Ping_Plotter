/* PathPulse mini time-series chart.
 *
 * A small dependency-free canvas renderer so the tool runs fully offline (no
 * CDN). Draws a latency line with a min-max band and a packet-loss overlay,
 * with time/ms axes and a hover tooltip. One instance per canvas.
 *
 * Data points: { ts (epoch s), rtt (ms|null), rtt_min, rtt_max, loss (%) }.
 */
(function (global) {
  "use strict";

  const COLORS = {
    grid: "rgba(148,163,184,0.12)",
    axis: "#7d8ba1",
    rtt: "#4aa8ff",
    band: "rgba(74,168,255,0.14)",
    loss: "#ff5470",
    tip: "rgba(15,20,30,0.95)",
  };

  function niceCeil(x) {
    if (x <= 0) return 1;
    const exp = Math.floor(Math.log10(x));
    const base = Math.pow(10, exp);
    const f = x / base;
    const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
    return nice * base;
  }

  function fmtTime(ts, spanSec) {
    const d = new Date(ts * 1000);
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    if (spanSec <= 20 * 60) {
      const ss = String(d.getSeconds()).padStart(2, "0");
      return `${hh}:${mm}:${ss}`;
    }
    return `${hh}:${mm}`;
  }

  class TimeChart {
    constructor(canvas) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.points = [];
      this.windowSec = 3600;
      this.hover = null;
      this.pad = { l: 52, r: 14, t: 12, b: 26 };

      this._onResize = () => this.draw();
      if (global.ResizeObserver) {
        this._ro = new ResizeObserver(this._onResize);
        this._ro.observe(canvas);
      } else {
        global.addEventListener("resize", this._onResize);
      }
      canvas.addEventListener("mousemove", (e) => this._onMove(e));
      canvas.addEventListener("mouseleave", () => { this.hover = null; this.draw(); });
    }

    setData(points, windowSec) {
      this.points = points || [];
      if (windowSec) this.windowSec = windowSec;
      this.draw();
    }

    pushPoint(p, maxAgeSec) {
      this.points.push(p);
      const cutoff = (Date.now() / 1000) - (maxAgeSec || this.windowSec);
      while (this.points.length && this.points[0].ts < cutoff) this.points.shift();
      this.draw();
    }

    _dims() {
      const dpr = global.devicePixelRatio || 1;
      const rect = this.canvas.getBoundingClientRect();
      const w = Math.max(320, rect.width);
      const h = Math.max(160, rect.height || 220);
      if (this.canvas.width !== Math.round(w * dpr) ||
          this.canvas.height !== Math.round(h * dpr)) {
        this.canvas.width = Math.round(w * dpr);
        this.canvas.height = Math.round(h * dpr);
      }
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      return { w, h };
    }

    _scales(w, h) {
      const now = Date.now() / 1000;
      const xMax = now;
      const xMin = now - this.windowSec;
      let yMax = 1;
      for (const p of this.points) {
        const v = p.rtt_max != null ? p.rtt_max : p.rtt;
        if (v != null && v > yMax) yMax = v;
      }
      yMax = niceCeil(yMax * 1.15);
      const pad = this.pad;
      const px = (ts) => pad.l + (ts - xMin) / (xMax - xMin) * (w - pad.l - pad.r);
      const py = (v) => h - pad.b - (v / yMax) * (h - pad.t - pad.b);
      return { xMin, xMax, yMax, px, py, pad };
    }

    draw() {
      const { w, h } = this._dims();
      const ctx = this.ctx;
      ctx.clearRect(0, 0, w, h);
      const s = this._scales(w, h);
      const { px, py, pad, yMax, xMin, xMax } = s;

      // Y grid + labels.
      ctx.font = "11px system-ui, sans-serif";
      ctx.textBaseline = "middle";
      ctx.fillStyle = COLORS.axis;
      const yTicks = 4;
      for (let i = 0; i <= yTicks; i++) {
        const v = (yMax / yTicks) * i;
        const y = py(v);
        ctx.strokeStyle = COLORS.grid;
        ctx.beginPath();
        ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
        ctx.textAlign = "right";
        ctx.fillText(v.toFixed(v < 10 ? 1 : 0), pad.l - 6, y);
      }
      // X grid + labels.
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      const xTicks = 5;
      for (let i = 0; i <= xTicks; i++) {
        const ts = xMin + (xMax - xMin) * (i / xTicks);
        const x = px(ts);
        ctx.strokeStyle = COLORS.grid;
        ctx.beginPath();
        ctx.moveTo(x, pad.t); ctx.lineTo(x, h - pad.b); ctx.stroke();
        ctx.fillStyle = COLORS.axis;
        ctx.fillText(fmtTime(ts, this.windowSec), x, h - pad.b + 5);
      }

      if (!this.points.length) {
        ctx.fillStyle = COLORS.axis;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText("waiting for data…", w / 2, h / 2);
        return;
      }

      // Min-max band.
      const band = this.points.filter(p => p.rtt_min != null && p.rtt_max != null);
      if (band.length > 1) {
        ctx.fillStyle = COLORS.band;
        ctx.beginPath();
        band.forEach((p, i) => {
          const x = px(p.ts), y = py(p.rtt_max);
          i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
        });
        for (let i = band.length - 1; i >= 0; i--) {
          ctx.lineTo(px(band[i].ts), py(band[i].rtt_min));
        }
        ctx.closePath();
        ctx.fill();
      }

      // Loss overlay: red bars at the baseline scaled by loss %.
      const baseY = h - pad.b;
      for (const p of this.points) {
        if (p.loss && p.loss > 0) {
          const x = px(p.ts);
          const barH = (p.loss / 100) * (h - pad.t - pad.b) * 0.4;
          ctx.strokeStyle = COLORS.loss;
          ctx.globalAlpha = 0.5;
          ctx.beginPath();
          ctx.moveTo(x, baseY); ctx.lineTo(x, baseY - barH); ctx.stroke();
          ctx.globalAlpha = 1;
        }
      }

      // Latency line (break on null rtt = full loss).
      ctx.strokeStyle = COLORS.rtt;
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      let started = false;
      for (const p of this.points) {
        if (p.rtt == null) { started = false; continue; }
        const x = px(p.ts), y = py(p.rtt);
        if (!started) { ctx.moveTo(x, y); started = true; }
        else ctx.lineTo(x, y);
      }
      ctx.stroke();

      // Hover tooltip.
      if (this.hover != null) {
        const p = this._nearest(this.hover, px);
        if (p && p.rtt != null) {
          const x = px(p.ts), y = py(p.rtt);
          ctx.fillStyle = COLORS.rtt;
          ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill();
          const label = `${fmtTime(p.ts, this.windowSec)}  ${p.rtt.toFixed(1)}ms` +
                        (p.loss ? `  ${p.loss.toFixed(0)}% loss` : "");
          ctx.font = "11px system-ui, sans-serif";
          const tw = ctx.measureText(label).width + 12;
          let tx = x + 8; if (tx + tw > w - pad.r) tx = x - tw - 8;
          const ty = Math.max(pad.t, y - 26);
          ctx.fillStyle = COLORS.tip;
          ctx.fillRect(tx, ty, tw, 18);
          ctx.fillStyle = "#e6edf6";
          ctx.textAlign = "left"; ctx.textBaseline = "middle";
          ctx.fillText(label, tx + 6, ty + 9);
        }
      }
    }

    _nearest(mouseX, px) {
      let best = null, bestD = Infinity;
      for (const p of this.points) {
        const d = Math.abs(px(p.ts) - mouseX);
        if (d < bestD) { bestD = d; best = p; }
      }
      return bestD < 40 ? best : null;
    }

    _onMove(e) {
      const rect = this.canvas.getBoundingClientRect();
      this.hover = e.clientX - rect.left;
      this.draw();
    }

    toPNG(filename) {
      this.canvas.toBlob((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url; a.download = filename || "pathpulse.png";
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      });
    }
  }

  global.TimeChart = TimeChart;
})(window);
