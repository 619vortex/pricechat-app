import React from "react";
import { AbsoluteFill, Audio, continueRender, delayRender, Easing, Img, interpolate, OffthreadVideo, Sequence, staticFile, useCurrentFrame,
         useVideoConfig } from "remotion";
import { measureText } from "@remotion/layout-utils";
import type { Asset, Clip, EditorialProps, Overlay, StyleSpec, Word } from "./types";

// Content Studio v4: an edit, not a slideshow. Real pictures carry the video; graphics are quiet and only appear when
// they say something (a name, a verified price, a real chart). No particles, glows, shakes, whips or counting numbers.

const FPS = 30;
const W = 1080;
const H = 1920;
// TikTok covers the top tabs, the right-hand buttons and the bottom caption block.
const SAFE = { left: 64, right: 940, top: 180, bottom: 1500 };
const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const ease = Easing.bezier(0.25, 0.1, 0.25, 1);

const fontOf = (s: StyleSpec) => (s.font === "serif" ? "'Playfair Display', Georgia, serif" : "Inter, 'Segoe UI', Arial, sans-serif");
const UI = "Inter, 'Segoe UI', Arial, sans-serif";
const MONO = "'JetBrains Mono', Consolas, monospace";
const str = (v: unknown, d = ""): string => (typeof v === "string" ? v : typeof v === "number" ? String(v) : d);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

let fontsReady: Promise<void> | null = null;
function loadFonts(): Promise<void> {
  if (!fontsReady) {
    const faces = [new FontFace("Inter", `url(${staticFile("fonts/Inter-Variable.ttf")})`, { weight: "100 900" }),
                   new FontFace("Playfair Display", `url(${staticFile("fonts/PlayfairDisplay-Variable.ttf")})`, { weight: "400 900" }),
                   new FontFace("JetBrains Mono", `url(${staticFile("fonts/JetBrainsMono-Variable.ttf")})`, { weight: "100 800" })];
    fontsReady = Promise.all(faces.map((f) => f.load().then((x) => { (document.fonts as unknown as { add(f: FontFace): void }).add(x); })))
      .then(() => undefined);
  }
  return fontsReady;
}

/** How a picture is placed: the crop (fit + focus + zoom) and a slow, linear camera move across the shot. */
function framing(clip: Clip, a: Asset, p: number, amount: number, boxW: number, boxH: number): React.CSSProperties {
  const [fx, fy] = clip.focus;
  const z = Math.max(1, clip.zoom || 1);
  const m = clip.move;
  // scale of the picture that covers (fill) or fits (frame) the box, then the zoom
  const cover = Math.max(boxW / a.w, boxH / a.h);
  const contain = Math.min(boxW / a.w, boxH / a.h);
  const base = (clip.fit === "fill" ? cover : contain) * z;
  const push = m === "push" ? 1 + amount * p : m === "pull" ? 1 + amount * (1 - p) : 1;
  const s = base * (m === "push" || m === "pull" || m === "none" ? push : 1 + amount);
  const w = a.w * s;
  const h = a.h * s;
  // keep the focus point where the crop puts it, within the picture's overflow
  const ox = Math.max(0, w - boxW);
  const oy = Math.max(0, h - boxH);
  let x = w <= boxW ? (boxW - w) / 2 : -ox * fx;
  let y = h <= boxH ? (boxH - h) / 2 : -oy * fy;
  // a pan travels part of the overflow, centred on the focus point: right/down = the view moves right/down
  const pan = (o: number, focus: number, forward: boolean) => {
    const travel = o * 0.6;
    const start = Math.min(Math.max(o * focus - travel / 2, 0), o - travel);
    return -(start + travel * (forward ? p : 1 - p));
  };
  if (m === "pan_left" || m === "pan_right") x = w <= boxW ? x : pan(ox, fx, m === "pan_right");
  if (m === "pan_up" || m === "pan_down") y = h <= boxH ? y : pan(oy, fy, m === "pan_down");
  return { position: "absolute", left: x, top: y, width: w, height: h, maxWidth: "none" };
}

const Picture: React.FC<{ clip: Clip; a: Asset; style: StyleSpec; frames: number; talking: (f: number) => boolean; from: number }> =
  ({ clip, a, style, frames, talking, from }) => {
    const f = useCurrentFrame();
    const p = interpolate(f, [0, Math.max(1, frames)], [0, 1], clamp);
    const framed = clip.fit === "frame";
    const boxW = W;
    const boxH = framed ? style.frameHeight : H;
    const css = { ...framing(clip, a, p, style.move, boxW, boxH), filter: style.grade || undefined };
    const src = staticFile(a.src);
    const vol = (vf: number) => {
      if (!clip.gain) return 0;
      const g = talking(from + vf) ? clip.gain * 0.22 : clip.gain; // the clip's own sound sits under the narration
      return g * interpolate(vf, [0, 6, frames - 8, frames], [0, 1, 1, 0], clamp);
    };
    const media = a.kind === "video"
      ? <OffthreadVideo src={src} startFrom={Math.round((clip.in ?? 0) * FPS)} volume={vol} muted={!clip.gain || !a.audio} style={css} />
      : <Img src={src} style={css} />;
    if (!framed) return <AbsoluteFill style={{ overflow: "hidden" }}>{media}</AbsoluteFill>;
    // the same picture, enlarged, blurred and darkened behind the frame (no black bars, nothing invented)
    const back: React.CSSProperties = { position: "absolute", inset: -60, width: W + 120, height: H + 120, objectFit: "cover", filter: "blur(38px) brightness(0.38) saturate(0.9)" };
    return (
      <AbsoluteFill style={{ background: style.frameBg, overflow: "hidden" }}>
        {style.backdrop === "blur" ? (a.kind === "video"
          ? <OffthreadVideo src={src} startFrom={Math.round((clip.in ?? 0) * FPS)} muted style={back} />
          : <Img src={src} style={back} />) : null}
        <div style={{ position: "absolute", left: 0, top: style.frameTop, width: boxW, height: boxH, overflow: "hidden" }}>{media}</div>
      </AbsoluteFill>
    );
  };

/** The licence's credit line, small, for as long as the picture is on screen. */
const Credit: React.FC<{ text: string; style: StyleSpec; framed: boolean }> = ({ text, style, framed }) => {
  if (!text) return null;
  const top = style.creditCorner === "top" ? SAFE.top + 64 : framed ? style.frameTop + style.frameHeight + 10 : 1130;
  return (
    <div style={{ position: "absolute", right: W - SAFE.right, top, maxWidth: 560, textAlign: "right", fontFamily: UI, fontSize: 19, fontWeight: 500,
                  color: "rgba(255,255,255,0.7)", textShadow: "0 1px 4px rgba(0,0,0,0.9)", letterSpacing: 0.2 }}>{text}</div>
  );
};

// --- overlays: one job each, on and off without fuss ------------------------------------------------------------------
const useFade = (frames: number, inF = 7, outF = 6) => {
  const f = useCurrentFrame();
  const o = interpolate(f, [0, inF, frames - outF, frames], [0, 1, 1, 0], clamp);
  const y = interpolate(f, [0, inF + 3], [14, 0], { ...clamp, easing: ease });
  return { opacity: o, transform: `translateY(${y}px)` };
};

const Title: React.FC<{ o: Overlay; s: StyleSpec; frames: number }> = ({ o, s, frames }) => {
  const st = useFade(frames);
  const text = str(o.data.text);
  const size = text.length > 70 ? 50 : text.length > 42 ? 58 : 66;
  return (
    <div style={{ position: "absolute", left: SAFE.left, width: SAFE.right - SAFE.left, top: 250, ...st }}>
      {o.data.kicker ? <div style={{ fontFamily: UI, fontWeight: 700, fontSize: 26, letterSpacing: 3, color: s.accent, textTransform: "uppercase", marginBottom: 14 }}>
        {str(o.data.kicker)}</div> : null}
      <div style={{ fontFamily: fontOf(s), fontWeight: s.font === "serif" ? 600 : 700, fontSize: size, lineHeight: 1.12, color: s.ink,
                    textShadow: "0 2px 18px rgba(0,0,0,0.55)" }}>{text}</div>
    </div>
  );
};

const LowerThird: React.FC<{ o: Overlay; s: StyleSpec; frames: number }> = ({ o, s, frames }) => {
  const f = useCurrentFrame();
  const st = useFade(frames);
  const line = interpolate(f, [2, 14], [0, 1], { ...clamp, easing: ease });
  return (
    <div style={{ position: "absolute", left: SAFE.left, top: 1000, maxWidth: SAFE.right - SAFE.left, ...st }}>
      <div style={{ display: "flex", gap: 20, alignItems: "stretch", padding: "18px 26px 18px 0", background: "linear-gradient(90deg, rgba(0,0,0,0.62), rgba(0,0,0,0.0))" }}>
        <div style={{ width: 6, background: s.accent, transform: `scaleY(${line})`, transformOrigin: "top" }} />
        <div>
          <div style={{ fontFamily: fontOf(s), fontWeight: s.font === "serif" ? 600 : 700, fontSize: 46, color: "#fff", lineHeight: 1.1 }}>{str(o.data.name)}</div>
          {o.data.detail ? <div style={{ fontFamily: UI, fontWeight: 500, fontSize: 30, color: "rgba(255,255,255,0.82)", marginTop: 6 }}>{str(o.data.detail)}</div> : null}
        </div>
      </div>
    </div>
  );
};

/** A verified price, stated once: it arrives as a finished number at the moment it's said. No counting up. */
const Price: React.FC<{ o: Overlay; s: StyleSpec; frames: number }> = ({ o, s, frames }) => {
  const st = useFade(frames, 6, 6);
  const top = num(o.data.top) ?? 980;
  return (
    <div style={{ position: "absolute", left: SAFE.left, top, ...st }}>
      <div style={{ display: "inline-block", padding: "22px 30px 24px", background: "rgba(8,9,10,0.78)", borderRadius: 6, borderLeft: `6px solid ${s.accent}` }}>
        <div style={{ fontFamily: UI, fontWeight: 700, fontSize: 24, letterSpacing: 3, color: "rgba(255,255,255,0.72)", textTransform: "uppercase" }}>{str(o.data.label)}</div>
        <div style={{ fontFamily: fontOf(s), fontWeight: s.font === "serif" ? 600 : 700, fontSize: 104, lineHeight: 1.05, color: "#fff", fontVariantNumeric: "tabular-nums" }}>
          {str(o.data.value)}</div>
        {o.data.note ? <div style={{ fontFamily: UI, fontWeight: 500, fontSize: 26, color: "rgba(255,255,255,0.78)", marginTop: 6 }}>{str(o.data.note)}</div> : null}
      </div>
    </div>
  );
};

const BASIS_TINT: Record<string, string> = { observed: "#9cc2ff", aggregated: "#9cc2ff", estimate: "#ffc93c", inferred: "#cfa4ff" };
const GREEN = "#2fd08a", RED = "#ff5d6c";
const usdShort = (v: number) => {
  const a = Math.abs(v), sg = v < 0 ? "-" : "";
  return sg + (a >= 1e9 ? `$${(a / 1e9).toFixed(1)}B` : a >= 1e6 ? `$${(a / 1e6).toFixed(1)}M` : a >= 1e3 ? `$${Math.round(a / 1e3)}K` : `$${Math.round(a)}`);
};
const utcStamp = (t: number) => {
  const d = new Date(t * 1000);
  return `${d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" })} ${d.toISOString().slice(11, 16)} UTC`;
};

/** Market structure, from exchange data (backend marketviz.py): the price on top and what explains it underneath (CVD,
 * aggregated open interest, reported liquidations, TWAP-like slices), or the order book as resting liquidity by price.
 * The basis (observed / aggregated / model estimate / inferred), venues, units and UTC window are always on the card. */
const MarketChart: React.FC<{ o: Overlay; s: StyleSpec; frames: number }> = ({ o, s, frames }) => {
  const f = useCurrentFrame();
  const st = useFade(frames, 8, 6);
  const v = str(o.data.variant);
  const pts = ((o.data.points as number[][]) ?? []).filter((p) => p.length >= 2);
  const price = ((o.data.price as number[][]) ?? []).filter((p) => p.length >= 2);
  const draw = interpolate(f, [6, 6 + Math.min(40, frames * 0.4)], [0, 1], { ...clamp, easing: ease });
  const cw = 900, ch = 600;
  const basis = str(o.data.basis), tint = BASIS_TINT[basis] ?? "#9cc2ff";
  const top = num(o.data.top) ?? 470;
  const line = (p: number[][], x0: number, x1: number, y0: number, y1: number) => {
    if (p.length < 2) return { d: "", X: (_: number) => 0, Y: (_: number) => 0, lo: 0, hi: 0 };
    const ts = p.map((q) => q[0]), vs = p.map((q) => q[1]);
    const t0 = Math.min(...ts), t1 = Math.max(...ts), lo = Math.min(...vs), hi = Math.max(...vs), span = hi - lo || Math.abs(hi) || 1;
    const X = (t: number) => x0 + ((t - t0) / (t1 - t0 || 1)) * (x1 - x0);
    const Y = (val: number) => y0 + (1 - (val - (lo - span * 0.06)) / (span * 1.12)) * (y1 - y0);
    return { d: p.map((q, i) => `${i ? "L" : "M"}${X(q[0]).toFixed(1)},${Y(q[1]).toFixed(1)}`).join(" "), X, Y, lo, hi };
  };
  let body: React.ReactNode = null;
  let t0 = num(o.data.start), t1 = num(o.data.end);
  if (v === "depth") {
    const rows = ((o.data.levels as [number, number, string][]) ?? []).slice().sort((a, b) => b[0] - a[0]);
    const mid = num(o.data.mid) ?? 0;
    const walls = (o.data.walls as { venue: string; side: string; px: number; usd: number }[]) ?? [];
    const big = Math.max(...rows.map((r) => r[1]), 1);
    const rh = Math.min(26, (ch - 40) / Math.max(1, rows.length));
    const shown = Math.round(draw * rows.length);
    const every = Math.max(1, Math.ceil(rows.length / 10));
    const midRow = Math.max(0, rows.findIndex((r) => r[0] < mid));
    const w0 = walls[0];
    const wallRow = w0 ? Math.max(0, rows.findIndex((r) => r[0] <= w0.px)) : 0;
    body = (
      <g>
        {rows.slice(0, shown).map(([px, u, side], i) => (
          <g key={px}>
            <rect x={150} y={20 + i * rh} width={Math.max(2, (u / big) * (cw - 330))} height={rh - 3} rx={2} fill={side === "bid" ? GREEN : RED} opacity={0.85} />
            {i % every === 0 ? <text x={140} y={20 + i * rh + rh * 0.75} fill="rgba(255,255,255,0.6)" fontFamily={MONO} fontSize={20} textAnchor="end">
              ${Math.round(px).toLocaleString("en-US")}</text> : null}
          </g>
        ))}
        <line x1={150} x2={cw} y1={20 + midRow * rh - 2} y2={20 + midRow * rh - 2} stroke="#fff" strokeDasharray="8 6" strokeWidth={2} />
        <text x={cw - 4} y={20 + midRow * rh - 10} fill="#fff" fontFamily={UI} fontSize={22} textAnchor="end">price ${Math.round(mid).toLocaleString("en-US")}</text>
        {w0 && draw >= 1 ? <text x={cw - 4} y={20 + wallRow * rh + rh} fill={s.accent} fontFamily={UI} fontWeight={700} fontSize={28} textAnchor="end">
          {w0.venue} {w0.side} {usdShort(w0.usd)} @ ${Math.round(w0.px).toLocaleString("en-US")}</text> : null}
      </g>
    );
    t0 = t1 = num(o.data.at);
  } else {
    const twap = v === "twap";
    const P = line(price.length >= 2 ? price : pts, 0, cw, 10, twap ? ch - 60 : 220);
    const lower: React.ReactNode[] = [];
    if (!twap) {
      const y0 = 260, y1 = ch - 50;
      if (v === "liq") {
        const bars = (o.data.bars as number[][]) ?? [];
        const m = Math.max(...bars.flatMap((b) => [b[1], b[2]]), 1);
        const bt0 = Math.min(...bars.map((b) => b[0])), bt1 = Math.max(...bars.map((b) => b[0]));
        const X = (t: number) => 20 + ((t - bt0) / (bt1 - bt0 || 1)) * (cw - 40);
        const zero = (y0 + y1) / 2, bw = Math.max(6, (cw - 40) / Math.max(1, bars.length) - 4);
        lower.push(<line key="z" x1={0} x2={cw} y1={zero} y2={zero} stroke="rgba(255,255,255,0.18)" strokeWidth={2} />);
        bars.slice(0, Math.round(draw * bars.length)).forEach(([t, sh, lg]) => {
          if (sh > 0) lower.push(<rect key={`s${t}`} x={X(t) - bw / 2} y={zero - (sh / m) * (zero - y0)} width={bw} height={(sh / m) * (zero - y0)} fill={GREEN} />);
          if (lg > 0) lower.push(<rect key={`l${t}`} x={X(t) - bw / 2} y={zero} width={bw} height={(lg / m) * (y1 - zero)} fill={RED} />);
        });
        lower.push(<text key="a" x={4} y={y0 + 22} fill={GREEN} fontFamily={UI} fontSize={22}>shorts liquidated</text>,
          <text key="b" x={4} y={y1 - 6} fill={RED} fontFamily={UI} fontSize={22}>longs liquidated</text>);
        t0 = t0 ?? bt0; t1 = t1 ?? bt1;
      } else if (pts.length >= 2) {
        const L = line(pts, 0, cw, y0, y1);
        const last = pts[pts.length - 1];
        const col = v === "cvd" ? (last[1] >= 0 ? GREEN : RED) : s.accent;
        if (v === "cvd" && L.lo < 0 && L.hi > 0) lower.push(<line key="z" x1={0} x2={cw} y1={L.Y(0)} y2={L.Y(0)} stroke="rgba(255,255,255,0.18)" strokeWidth={2} />);
        lower.push(<path key="l" d={L.d} fill="none" stroke={col} strokeWidth={5} strokeLinejoin="round" pathLength={1} strokeDasharray={1} strokeDashoffset={1 - draw} />);
        if (draw >= 1) lower.push(<text key="v" x={cw - 4} y={Math.max(y0 + 30, L.Y(last[1]) - 16)} fill="#fff" fontFamily={UI} fontWeight={700} fontSize={34}
          textAnchor="end">{usdShort(last[1])}</text>);
        lower.push(<text key="n" x={4} y={y0 + 4} fill="rgba(255,255,255,0.6)" fontFamily={UI} fontSize={22}>
          {v === "cvd" ? "aggressive buys − sells, cumulative" : "open interest"}</text>);
        t0 = t0 ?? pts[0][0]; t1 = t1 ?? last[0];
      }
    }
    const marks = (o.data.marks as number[]) ?? [];
    const side = str(o.data.side);
    body = (
      <g>
        <path d={P.d} fill="none" stroke="rgba(255,255,255,0.85)" strokeWidth={3} strokeLinejoin="round" pathLength={1} strokeDasharray={1} strokeDashoffset={1 - draw} />
        <text x={4} y={30} fill="rgba(255,255,255,0.6)" fontFamily={UI} fontSize={22}>price</text>
        {twap && draw >= 1 ? marks.map((t) => {
          const base = price.length >= 2 ? price : pts;
          const near = base.reduce((a, q) => (Math.abs(q[0] - t) < Math.abs(a[0] - t) ? q : a), base[0]);
          return <circle key={t} cx={P.X(t)} cy={P.Y(near[1]) + (side === "buy" ? 26 : -26)} r={9} fill="#be78ff" />;
        }) : null}
        {twap && draw >= 1 ? <text x={cw / 2} y={ch - 24} fill="#cfa4ff" fontFamily={UI} fontWeight={600} fontSize={24} textAnchor="middle">
          ● regular same-size {side} orders (inferred: who placed them is unknown)</text> : null}
        {lower}
      </g>
    );
    if (twap && marks.length) { t0 = t0 ?? marks[0]; t1 = t1 ?? marks[marks.length - 1]; }
  }
  return (
    <div style={{ position: "absolute", left: (W - cw) / 2 - 20, top, width: cw + 40, padding: "24px 20px 20px", background: "rgba(10,11,12,0.9)", borderRadius: 8, ...st }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, padding: "0 8px" }}>
        <div style={{ fontFamily: UI, fontWeight: 700, fontSize: 28, color: "#fff" }}>{str(o.data.title)}</div>
        <div style={{ fontFamily: UI, fontWeight: 700, fontSize: 18, letterSpacing: 2, color: tint, border: `2px solid ${tint}`, borderRadius: 4,
                      padding: "4px 8px", textTransform: "uppercase", whiteSpace: "nowrap" }}>{str(o.data.basis_label, basis)}</div>
      </div>
      <svg width={cw} height={ch} style={{ display: "block", marginTop: 8 }}>{body}</svg>
      <div style={{ display: "flex", justifyContent: "space-between", fontFamily: UI, fontSize: 21, color: "rgba(255,255,255,0.6)", padding: "2px 8px 0" }}>
        <span>{t0 != null ? utcStamp(t0) : ""}</span><span>{t1 != null && t1 !== t0 ? utcStamp(t1) : ""}</span>
      </div>
      <div style={{ fontFamily: UI, fontWeight: 500, fontSize: 21, color: "rgba(255,255,255,0.6)", padding: "4px 8px 0" }}>{str(o.data.source)}</div>
    </div>
  );
};

/** A real dataset, drawn once from left to right: a line (prices over time) or dots (individual recorded sales). */
const Chart: React.FC<{ o: Overlay; s: StyleSpec; frames: number }> = ({ o, s, frames }) => {
  const f = useCurrentFrame();
  const st = useFade(frames, 8, 6);
  const pts = ((o.data.points as number[][]) ?? []).filter((p) => p.length >= 2);
  if (o.data.kind === "market") return <MarketChart o={o} s={s} frames={frames} />;
  if (pts.length < 2) return null;
  const dots = o.data.kind === "sales";
  const pre = str(o.data.prefix, "$");
  const fmt = (v: number) => pre + (Math.abs(v) >= 10000 || Number.isInteger(v) ? Math.round(v).toLocaleString("en-US")
    : v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })) + str(o.data.suffix);
  const date = (t: number) => new Date(t * 1000).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
  const cw = 900, ch = 520, padL = 8, padR = 8, padT = 70, padB = 70;
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), lo = Math.min(...ys), hi = Math.max(...ys);
  const span = hi - lo || Math.abs(hi) || 1;
  const X = (t: number) => padL + ((t - x0) / (x1 - x0 || 1)) * (cw - padL - padR);
  const Y = (v: number) => padT + (1 - (v - (lo - span * 0.08)) / (span * 1.16)) * (ch - padT - padB);
  const draw = interpolate(f, [6, 6 + Math.min(40, frames * 0.4)], [0, 1], { ...clamp, easing: ease });
  const shown = Math.max(1, Math.round(draw * pts.length));
  const path = pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1];
  const hiIdx = num(o.data.highlight) ?? pts.length - 1;
  const hp = pts[Math.min(pts.length - 1, Math.max(0, hiIdx))];
  const top = num(o.data.top) ?? 520;
  return (
    <div style={{ position: "absolute", left: (W - cw) / 2 - 20, top, width: cw + 40, padding: "26px 20px 22px", background: "rgba(10,11,12,0.86)", borderRadius: 8, ...st }}>
      <div style={{ fontFamily: UI, fontWeight: 700, fontSize: 28, color: "#fff", padding: "0 8px" }}>{str(o.data.title)}</div>
      <svg width={cw} height={ch} style={{ display: "block" }}>
        {[0, 0.5, 1].map((k) => <line key={k} x1={0} x2={cw} y1={padT + k * (ch - padT - padB)} y2={padT + k * (ch - padT - padB)} stroke="rgba(255,255,255,0.10)" strokeWidth={2} />)}
        <text x={cw - 4} y={padT - 14} fill="rgba(255,255,255,0.6)" fontFamily={MONO} fontSize={22} textAnchor="end">{fmt(hi)}</text>
        <text x={cw - 4} y={ch - padB + 32} fill="rgba(255,255,255,0.6)" fontFamily={MONO} fontSize={22} textAnchor="end">{fmt(lo)}</text>
        {dots ? (
          pts.slice(0, shown).map((p, i) => {
            const isHi = p === hp;
            return <circle key={i} cx={X(p[0])} cy={Y(p[1])} r={isHi ? 13 : 8} fill={isHi ? s.accent : "rgba(255,255,255,0.78)"} />;
          })
        ) : (
          <path d={path} fill="none" stroke={s.accent} strokeWidth={5} strokeLinejoin="round" strokeLinecap="round" pathLength={1}
                strokeDasharray={1} strokeDashoffset={1 - draw} />
        )}
        {draw >= 1 ? (
          <g opacity={interpolate(f, [6 + Math.min(40, frames * 0.4), 14 + Math.min(40, frames * 0.4)], [0, 1], clamp)}>
            {!dots ? <circle cx={X(last[0])} cy={Y(last[1])} r={10} fill={s.accent} /> : null}
            <text x={Math.min(cw - 10, Math.max(170, X(hp[0])))} y={Math.max(30, Y(hp[1]) - 26)} fill="#fff" fontFamily={UI} fontWeight={700} fontSize={34}
                  textAnchor={X(hp[0]) > cw * 0.6 ? "end" : "middle"}>{fmt(hp[1])}</text>
          </g>
        ) : null}
        <text x={4} y={ch - 14} fill="rgba(255,255,255,0.55)" fontFamily={UI} fontSize={22}>{date(x0)}</text>
        <text x={cw - 4} y={ch - 14} fill="rgba(255,255,255,0.55)" fontFamily={UI} fontSize={22} textAnchor="end">{date(x1)}</text>
      </svg>
      <div style={{ fontFamily: UI, fontWeight: 500, fontSize: 21, color: "rgba(255,255,255,0.6)", padding: "4px 8px 0" }}>{str(o.data.source)}</div>
    </div>
  );
};

const Facts: React.FC<{ o: Overlay; s: StyleSpec; frames: number }> = ({ o, s, frames }) => {
  const st = useFade(frames);
  const rows = (o.data.rows as { k: string; v: string }[]) ?? [];
  const top = num(o.data.top) ?? 560;
  return (
    <div style={{ position: "absolute", left: SAFE.left, width: SAFE.right - SAFE.left, top, padding: "30px 34px", background: "rgba(10,11,12,0.88)", borderRadius: 8, ...st }}>
      <div style={{ fontFamily: UI, fontWeight: 700, fontSize: 26, letterSpacing: 3, color: s.accent, textTransform: "uppercase", marginBottom: 18 }}>{str(o.data.title)}</div>
      {rows.map((r, i) => (
        <div key={i} style={{ display: "flex", justifyContent: "space-between", gap: 20, padding: "14px 0", borderTop: i ? "2px solid rgba(255,255,255,0.08)" : undefined }}>
          <span style={{ fontFamily: UI, fontWeight: 500, fontSize: 32, color: "rgba(255,255,255,0.7)" }}>{r.k}</span>
          <span style={{ fontFamily: UI, fontWeight: 700, fontSize: 34, color: "#fff", textAlign: "right", fontVariantNumeric: "tabular-nums" }}>{r.v}</span>
        </div>
      ))}
      {o.data.footer ? <div style={{ fontFamily: UI, fontSize: 21, color: "rgba(255,255,255,0.55)", marginTop: 14 }}>{str(o.data.footer)}</div> : null}
    </div>
  );
};

const SourceTag: React.FC<{ o: Overlay; frames: number }> = ({ o, frames }) => {
  const st = useFade(frames, 6, 6);
  return (
    <div style={{ position: "absolute", left: SAFE.left, top: SAFE.top + 64, fontFamily: UI, fontWeight: 600, fontSize: 22, color: "rgba(255,255,255,0.85)",
                  padding: "8px 14px", background: "rgba(0,0,0,0.55)", borderRadius: 4, ...st }}>{str(o.data.text)}</div>
  );
};

/** On-screen storytelling text (for videos told without narration, or a line the edit wants read, not heard). */
const TextCard: React.FC<{ o: Overlay; s: StyleSpec; frames: number }> = ({ o, s, frames }) => {
  const st = useFade(frames);
  const text = str(o.data.text);
  return (
    <div style={{ position: "absolute", left: SAFE.left, width: SAFE.right - SAFE.left, top: num(o.data.top) ?? 1010, ...st }}>
      <span style={{ fontFamily: fontOf(s), fontWeight: s.font === "serif" ? 600 : 700, fontSize: text.length > 60 ? 46 : 56, lineHeight: 1.18, color: "#fff",
                     background: "rgba(0,0,0,0.6)", boxDecorationBreak: "clone", WebkitBoxDecorationBreak: "clone", padding: "4px 14px" }}>{text}</span>
    </div>
  );
};

/** The picture steps back (darker) while a chart or record card is up, so the graphic reads cleanly. */
const Dim: React.FC<{ o: Overlay; frames: number }> = ({ o, frames }) => {
  const f = useCurrentFrame();
  const amount = num(o.data.amount) ?? 0.5;
  return <AbsoluteFill style={{ background: "#000", opacity: amount * interpolate(f, [0, 8, frames - 6, frames], [0, 1, 1, 0], clamp) }} />;
};

const OVERLAYS: Record<string, React.FC<{ o: Overlay; s: StyleSpec; frames: number }>> = {
  dim: ({ o, frames }) => <Dim o={o} frames={frames} />,
  title: Title, lower_third: LowerThird, price: Price, chart: Chart, facts: Facts, source: ({ o, frames }) => <SourceTag o={o} frames={frames} />, text: TextCard,
};

/** Captions: one short phrase at a time, in the lower third, readable and calm. They never cover the subject. */
const Captions: React.FC<{ words: Word[]; s: StyleSpec }> = ({ words, s }) => {
  const f = useCurrentFrame();
  const t = f / FPS;
  const phrases = React.useMemo(() => {
    const out: Word[][] = [];
    for (const w of words) {
      if (!out.length || out[out.length - 1][0].p !== w.p) out.push([]);
      out[out.length - 1].push(w);
    }
    return out;
  }, [words]);
  if (s.captions === "none") return null;
  let ph: Word[] | null = null;
  for (let i = 0; i < phrases.length; i++) {
    const a = phrases[i][0].s - 0.05;
    const b = i + 1 < phrases.length ? Math.min(phrases[i + 1][0].s - 0.05, phrases[i][phrases[i].length - 1].e + 0.5) : phrases[i][phrases[i].length - 1].e + 0.4;
    if (t >= a && t < b) { ph = phrases[i]; break; }
  }
  if (!ph) return null;
  const text = ph.map((w) => w.t).join(" ");
  const maxW = SAFE.right - SAFE.left;
  const wide = measureText({ text, fontFamily: "Inter", fontSize: s.captionSize, fontWeight: "600" }).width;
  const size = wide > maxW * 1.9 ? Math.round(s.captionSize * (maxW * 1.9) / wide) : s.captionSize;
  const boxed = s.captions === "boxed";
  return (
    <div style={{ position: "absolute", left: SAFE.left, width: maxW, top: 1280, display: "flex", justifyContent: "center" }}>
      <div style={{ textAlign: "center", fontFamily: UI, fontWeight: 600, fontSize: size, lineHeight: 1.25, color: "#fff",
                    textShadow: boxed ? "none" : "0 2px 3px rgba(0,0,0,0.9), 0 0 14px rgba(0,0,0,0.6)" }}>
        <span style={boxed ? { background: "rgba(0,0,0,0.66)", padding: "4px 14px", boxDecorationBreak: "clone", WebkitBoxDecorationBreak: "clone", borderRadius: 4 } : undefined}>
          {ph.map((w, i) => <React.Fragment key={i}>{i ? " " : ""}<span style={{ color: w.hl ? s.accent : undefined }}>{w.t}</span></React.Fragment>)}
        </span>
      </div>
    </div>
  );
};

const Watermark: React.FC<{ brand: string }> = ({ brand }) => (
  <div style={{ position: "absolute", left: SAFE.left, top: SAFE.top, display: "flex", alignItems: "center", gap: 10, opacity: 0.82 }}>
    <Img src={staticFile("kiwi.png")} style={{ width: 40 }} />
    <span style={{ fontFamily: UI, fontWeight: 800, fontSize: 24, letterSpacing: 2.5, color: "#fff", textShadow: "0 1px 4px rgba(0,0,0,0.8)" }}>{brand.toUpperCase()}</span>
  </div>
);

const EndCard: React.FC<{ brand: EditorialProps["brand"]; frames: number }> = ({ brand, frames }) => {
  const f = useCurrentFrame();
  const o = interpolate(f, [0, 10], [0, 1], clamp);
  return (
    <AbsoluteFill style={{ background: "#0b0b0c", opacity: o, alignItems: "center", justifyContent: "center", gap: 22 }}>
      <Img src={staticFile("kiwi.png")} style={{ width: 150, opacity: interpolate(f, [4, 16], [0, 1], clamp) }} />
      <div style={{ fontFamily: UI, fontWeight: 800, fontSize: 54, letterSpacing: 4, color: "#fff" }}>{brand.name.toUpperCase()}</div>
      <div style={{ fontFamily: UI, fontWeight: 500, fontSize: 30, color: "rgba(255,255,255,0.65)" }}>{brand.site}</div>
      <div style={{ height: frames }} />
    </AbsoluteFill>
  );
};

const SafeGuides: React.FC<{ s: StyleSpec }> = ({ s }) => (
  <AbsoluteFill style={{ pointerEvents: "none" }}>
    <div style={{ position: "absolute", left: SAFE.left, top: SAFE.top, width: SAFE.right - SAFE.left, height: SAFE.bottom - SAFE.top, outline: "3px dashed rgba(0,255,255,0.6)" }} />
    <div style={{ position: "absolute", left: 0, top: s.frameTop, width: W, height: s.frameHeight, outline: "3px dashed rgba(255,200,0,0.6)" }} />
    <div style={{ position: "absolute", right: 0, top: 700, width: 130, height: 900, background: "rgba(255,0,0,0.18)" }} />
    <div style={{ position: "absolute", left: 0, bottom: 0, width: W, height: 380, background: "rgba(255,0,0,0.18)" }} />
  </AbsoluteFill>
);

export const Editorial: React.FC<EditorialProps> = (p) => {
  const { durationInFrames } = useVideoConfig();
  const [handle] = React.useState(() => delayRender("fonts"));
  React.useEffect(() => { loadFonts().then(() => continueRender(handle)).catch(() => continueRender(handle)); }, [handle]);
  const s = p.style;
  const speech = React.useMemo(() => p.captions.words.map((w) => [Math.round((w.s - 0.15) * FPS), Math.round((w.e + 0.25) * FPS)] as const), [p.captions.words]);
  const talking = React.useCallback((f: number) => !!p.narration.src && speech.some(([a, b]) => f >= a && f <= b), [speech, p.narration.src]);
  const musicVolume = (f: number) => {
    const duck = talking(f) || talking(f + 4) ? 0.35 : 1;
    return p.music.gain * duck * interpolate(f, [0, 15, durationInFrames - 40, durationInFrames], [0, 1, 1, 0], clamp);
  };
  const D = s.transition === "dissolve" ? Math.max(0, s.dissolve) : 0;
  const endFrames = Math.round(p.endCard * FPS);
  const clips = p.video;
  return (
    <AbsoluteFill style={{ background: s.frameBg }}>
      {clips.map((c, i) => {
        const a = p.assets[c.asset];
        if (!a) return null;
        const from = Math.round(c.start * FPS);
        const frames = Math.max(1, Math.round(c.dur * FPS) + (i + 1 < clips.length ? D : 0));
        return (
          <Sequence key={c.id} from={from} durationInFrames={frames} name={`${c.id} ${a.src}`}>
            <ClipLayer clip={c} a={a} style={s} frames={frames} fadeIn={i ? D : 0} talking={talking} from={from} />
          </Sequence>
        );
      })}
      {p.overlays.map((o) => {
        const C = OVERLAYS[o.type];
        const frames = Math.max(1, Math.round(o.dur * FPS));
        return C ? <Sequence key={o.id} from={Math.round(o.start * FPS)} durationInFrames={frames} name={`${o.id} ${o.type}`}><C o={o} s={s} frames={frames} /></Sequence> : null;
      })}
      {s.captions !== "none" && p.captions.words.length ? (
        <AbsoluteFill style={{ pointerEvents: "none", background: "linear-gradient(180deg, transparent 58%, rgba(0,0,0,0.42) 68%, rgba(0,0,0,0.5) 76%, transparent 86%)" }} />
      ) : null}
      <Sequence from={0} durationInFrames={Math.max(1, durationInFrames - endFrames)} name="watermark"><Watermark brand={p.brand.name} /></Sequence>
      <Captions words={p.captions.words} s={s} />
      {endFrames > 0 ? <Sequence from={durationInFrames - endFrames} durationInFrames={endFrames} name="end card"><EndCard brand={p.brand} frames={0} /></Sequence> : null}
      {p.safe ? <SafeGuides s={s} /> : null}
      {p.narration.src ? <Audio src={staticFile(p.narration.src)} volume={p.narration.gain} /> : null}
      {p.music.src ? <Audio src={staticFile(p.music.src)} volume={musicVolume} loop /> : null}
    </AbsoluteFill>
  );
};

const ClipLayer: React.FC<{ clip: Clip; a: Asset; style: StyleSpec; frames: number; fadeIn: number; talking: (f: number) => boolean; from: number }> =
  ({ clip, a, style, frames, fadeIn, talking, from }) => {
    const f = useCurrentFrame();
    const o = fadeIn ? interpolate(f, [0, fadeIn], [0, 1], clamp) : 1;
    return (
      <AbsoluteFill style={{ opacity: o }}>
        <Picture clip={clip} a={a} style={style} frames={frames} talking={talking} from={from} />
        <Credit text={a.credit ?? ""} style={style} framed={clip.fit === "frame"} />
      </AbsoluteFill>
    );
  };
