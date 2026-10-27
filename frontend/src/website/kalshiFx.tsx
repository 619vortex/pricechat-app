import { useEffect, useRef, useState } from "react";

/* Earn › Kalshi visuals for the bot panel (kalshiBrain.tsx). Every one is driven by what the bot reported: the core's
   particle stream scales with the markets it read, bursts on each new cycle and takes the mode's colour; the gauge needle
   is the best basket's edge; the ring counts down to the next report. All motion stops under prefers-reduced-motion. */

const reduced = () => typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
const isTestDom = () => typeof navigator !== "undefined" && navigator.userAgent.includes("jsdom");

const MODE_RGB: Record<string, [number, number, number]> = {
  live: [0, 229, 143], paper: [255, 209, 102], halted: [255, 92, 110], offline: [120, 135, 130], idle: [120, 135, 130],
};

/** Animated number: eases from the last value to the new one (from `start` on first show, when given). */
export function CountUp({ value, digits = 0, prefix = "", start }: { value: number; digits?: number; prefix?: string; start?: number }) {
  const [shown, setShown] = useState(start ?? value);
  const from = useRef(start ?? value);
  useEffect(() => {
    if (reduced() || isTestDom()) { setShown(value); from.current = value; return; }
    const a = from.current, t0 = performance.now();
    let raf = 0;
    const step = (t: number) => {
      // a frame's timestamp can precede t0: clamp so the number never runs backwards past its start
      const k = Math.max(0, Math.min(1, (t - t0) / 900)), e = 1 - Math.pow(1 - k, 3);
      setShown(a + (value - a) * e);
      if (k < 1) raf = requestAnimationFrame(step); else from.current = value;
    };
    raf = requestAnimationFrame(step);
    return () => { cancelAnimationFrame(raf); from.current = value; };
  }, [value]);
  return <>{prefix}{shown.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}</>;
}

type P = { x: number; y: number; vx: number; vy: number; life: number; hot: boolean };

/** The bot's "neural core": a drifting mesh, a stream of market particles pulled into the centre (rate ~ log of markets
    read), a shockwave on each new cycle, and gold sparks for every basket whose YES bids sum past $1. */
export function Core({ markets, hot, cycle, mode }: { markets: number; hot: number; cycle: number; mode: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const live = useRef({ markets, hot, mode, burst: 0, cycle });
  useEffect(() => {
    const L = live.current;
    if (cycle !== L.cycle) L.burst = 1;
    Object.assign(L, { markets, hot, mode, cycle });
  }, [markets, hot, mode, cycle]);
  useEffect(() => {
    const cv = ref.current;
    if (!cv || isTestDom()) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    let w = 0, h = 0, raf = 0, last = performance.now();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const resize = () => {
      w = cv.clientWidth; h = cv.clientHeight;
      cv.width = w * dpr; cv.height = h * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(cv);
    const nodes = Array.from({ length: 70 }, (_, i) => {
      const ring = 1 + (i % 3), a = (i / 70) * Math.PI * 2 * 3;
      return { a, r: ring * 0.13 + Math.random() * 0.05, s: (Math.random() - 0.5) * 0.12 };
    });
    const ps: P[] = [];
    let spawn = 0, wave = 0;
    const frame = (t: number) => {
      const dt = Math.min(0.05, (t - last) / 1000); last = t;
      const L = live.current, [r, g, b] = MODE_RGB[L.mode] ?? MODE_RGB.idle;
      const col = (a: number) => `rgba(${r},${g},${b},${a})`;
      const cx = w / 2, cy = h / 2, RX = Math.min(w * 0.46, 520), RY = h * 0.44;
      ctx.clearRect(0, 0, w, h);
      // mesh
      const pts = nodes.map((n) => { n.a += n.s * dt; return [cx + Math.cos(n.a) * n.r * 2.1 * RX, cy + Math.sin(n.a) * n.r * 2.1 * RY]; });
      ctx.lineWidth = 0.6;
      for (let i = 0; i < pts.length; i++) {
        for (let j = i + 1; j < pts.length; j++) {
          const d = Math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]);
          if (d < 90) { ctx.strokeStyle = col(0.2 * (1 - d / 90)); ctx.beginPath(); ctx.moveTo(pts[i][0], pts[i][1]); ctx.lineTo(pts[j][0], pts[j][1]); ctx.stroke(); }
        }
      }
      for (const [x, y] of pts) { ctx.fillStyle = col(0.5); ctx.beginPath(); ctx.arc(x, y, 1.4, 0, 6.283); ctx.fill(); }
      // particles: markets streaming in
      const rate = L.mode === "offline" || L.mode === "idle" ? 4 : 10 + Math.log10(Math.max(10, L.markets)) * 9;
      spawn += rate * dt;
      while (spawn > 1) {
        spawn -= 1;
        const a = Math.random() * 6.283;
        ps.push({ x: cx + Math.cos(a) * w * 0.55, y: cy + Math.sin(a) * h * 0.7, vx: 0, vy: 0, life: 1, hot: Math.random() < Math.min(0.25, L.hot / 200) });
      }
      for (let i = ps.length - 1; i >= 0; i--) {
        const p = ps[i], dx = cx - p.x, dy = cy - p.y, dist = Math.hypot(dx, dy) || 1;
        p.vx += (dx / dist) * 140 * dt + (-dy / dist) * 40 * dt; p.vy += (dy / dist) * 140 * dt + (dx / dist) * 40 * dt;
        p.vx *= 0.96; p.vy *= 0.96; p.x += p.vx * dt; p.y += p.vy * dt;
        if (dist < 10) { ps.splice(i, 1); continue; }
        ctx.fillStyle = p.hot ? "rgba(255,209,102,0.95)" : col(0.75);
        ctx.beginPath(); ctx.arc(p.x, p.y, p.hot ? 2.2 : 1.3, 0, 6.283); ctx.fill();
      }
      // core + heartbeat
      const beat = 1 + Math.sin(t / 380) * 0.08;
      const gr = ctx.createRadialGradient(cx, cy, 0, cx, cy, 46 * beat);
      gr.addColorStop(0, col(0.95)); gr.addColorStop(0.35, col(0.35)); gr.addColorStop(1, col(0));
      ctx.fillStyle = gr; ctx.beginPath(); ctx.arc(cx, cy, 46 * beat, 0, 6.283); ctx.fill();
      // new-cycle shockwave
      if (L.burst > 0) { wave = 1; L.burst = 0; }
      if (wave > 0) {
        const rr = (1 - wave) * w * 0.55;
        ctx.strokeStyle = col(wave * 0.8); ctx.lineWidth = 2.5 * wave; ctx.beginPath(); ctx.ellipse(cx, cy, rr, rr * (h / w) * 1.4, 0, 0, 6.283); ctx.stroke();
        wave -= dt * 0.7;
      }
      if (!reduced()) raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => { cancelAnimationFrame(raf); ro.disconnect(); };
  }, []);
  return <canvas ref={ref} className="kx-core" aria-hidden />;
}

/** Half-dial from -2¢ to +3¢ per basket: needle = the best basket now; the gold tick = the fee gate it must clear. */
export function EdgeGauge({ edge, gate }: { edge: number | null; gate: number }) {
  const lo = -2, hi = 3;
  const ang = (v: number) => -90 + ((Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo)) * 180;
  const tick = (v: number) => {
    const a = (ang(v) - 90) * (Math.PI / 180);
    return { x1: 50 + Math.cos(a) * 34, y1: 52 + Math.sin(a) * 34, x2: 50 + Math.cos(a) * 44, y2: 52 + Math.sin(a) * 44 };
  };
  const z = tick(0), gt = tick(gate);
  const v = edge ?? lo;
  return (
    <figure className="kx-gauge">
      <svg viewBox="0 0 100 60" role="img" aria-label={`Best basket ${edge == null ? "none" : `${edge.toFixed(2)} cents`}; gate ${gate} cents`}>
        <defs>
          <linearGradient id="kx-g" x1="0" x2="1"><stop offset="0" stopColor="#ff5c6e" /><stop offset="0.4" stopColor="#ffd166" /><stop offset="1" stopColor="#00e58f" /></linearGradient>
        </defs>
        <path d="M 8 52 A 42 42 0 0 1 92 52" className="kx-arc" stroke="url(#kx-g)" />
        <line {...z} className="kx-zero" /><line {...gt} className="kx-gate" />
        <g className="kx-needle" style={{ transform: `rotate(${ang(v)}deg)` }}><line x1="50" y1="52" x2="50" y2="16" /><circle cx="50" cy="52" r="3.4" /></g>
      </svg>
      <figcaption><b className={v > gate ? "is-good" : v > 0 ? "is-warm" : "is-bad"}>{edge == null ? "—" : `${edge > 0 ? "+" : ""}${edge.toFixed(2)}¢`}</b>
        <span>best basket · needs &gt; {gate}¢</span></figcaption>
    </figure>
  );
}

/** Ring counting down to the bot's next report (its interval + how long the last cycle took). */
export function NextScan({ receivedAt, every, took }: { receivedAt: string; every: number; took: number }) {
  const [, tick] = useState(0);
  useEffect(() => { const id = window.setInterval(() => tick((n) => n + 1), 250); return () => window.clearInterval(id); }, []);
  const period = Math.max(1, every + took);
  const left = period - (Date.now() - new Date(receivedAt).getTime()) / 1000;
  const frac = Math.max(0, Math.min(1, left / period));
  const C = 2 * Math.PI * 16;
  return (
    <div className="kx-ring" title="Until the bot's next cycle report">
      <svg viewBox="0 0 40 40" aria-hidden><circle cx="20" cy="20" r="16" className="kx-ring-bg" />
        <circle cx="20" cy="20" r="16" className="kx-ring-fg" strokeDasharray={C} strokeDashoffset={C * (1 - frac)} /></svg>
      <span>{left > 0 ? <><b>{Math.ceil(left)}s</b> to next scan</> : <b className="kx-scanning">scanning…</b>}</span>
    </div>
  );
}

/** Endless tape of the baskets the bot is watching. */
export function Tape({ items }: { items: { event: string; title: string; edge_cents: number; legs: number }[] }) {
  if (items.length === 0) return null;
  const row = items.map((b) => (
    <span key={b.event} className="kx-tape-item"><b>{(b.title || b.event).slice(0, 38)}</b> {b.legs} outcomes
      <em className={b.edge_cents > 0 ? "is-good" : "is-bad"}>{b.edge_cents > 0 ? "▲" : "▼"} {b.edge_cents.toFixed(2)}¢</em></span>
  ));
  return (
    <div className="kx-tape" aria-hidden>
      <div className="kx-tape-track" style={{ animationDuration: `${Math.max(20, items.length * 6)}s` }}><span>{row}</span><span>{row}</span></div>
    </div>
  );
}
