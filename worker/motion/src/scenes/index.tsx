import React from "react";
import { AbsoluteFill, Img, interpolate, spring, staticFile, useCurrentFrame } from "remotion";
import { Chip, clamp, Counter, easeInOut, easeOut, fmtMoney, Kinetic, Particles } from "../fx";
import { C, FONT, SAFE, type Look } from "../theme";
import type { Photo, Props, Scene } from "../types";

export interface SceneProps { scene: Scene; look: Look; e: number; props: Props; frames: number; index: number }

const str = (v: unknown, d = ""): string => (typeof v === "string" ? v : typeof v === "number" ? String(v) : d);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

const Stage: React.FC<{ children: React.ReactNode; top?: number; center?: boolean }> = ({ children, top = 300, center }) => (
  <div style={{ position: "absolute", left: SAFE.left, width: SAFE.right - SAFE.left, top, bottom: 1920 - SAFE.contentBottom,
                display: "flex", flexDirection: "column", justifyContent: center ? "center" : "flex-start", alignItems: center ? "center" : "flex-start" }}>
    {children}
  </div>
);

// --- the card itself: 3D entrance, gentle float, holographic light sweep ------------------------------------------------
const Card: React.FC<{ src: string; width: number; ratio: number; look: Look; enter: number; floatAmt: number; sweepAt?: number }> =
  ({ src, width, ratio, look, enter, floatAmt, sweepAt = 18 }) => {
    const f = useCurrentFrame();
    const ry = interpolate(enter, [0, 1], [78, 0]) + Math.sin(f / 28) * 6 * floatAmt;
    const rx = Math.cos(f / 33) * 4 * floatAmt;
    const sweep = interpolate(f, [sweepAt, sweepAt + 26], [-60, 160], { ...clamp, easing: easeInOut });
    const h = width * ratio;
    return (
      <div style={{ perspective: 1600, width, height: h }}>
        <div style={{ width, height: h, position: "relative", transform: `rotateY(${ry}deg) rotateX(${rx}deg) scale(${interpolate(enter, [0, 1], [0.7, 1])})`,
                      transformStyle: "preserve-3d", opacity: interpolate(enter, [0, 0.25], [0, 1], clamp) }}>
          <div style={{ position: "absolute", inset: -40, borderRadius: 60, background: look.glow, filter: "blur(55px)", opacity: 0.85 }} />
          <Img src={src} style={{ position: "absolute", inset: 0, width, height: h, borderRadius: width * 0.045, objectFit: "cover",
                                  boxShadow: "0 40px 80px rgba(0,0,0,0.6)" }} />
          <div style={{ position: "absolute", inset: 0, borderRadius: width * 0.045, overflow: "hidden", mixBlendMode: "screen", opacity: 0.55 }}>
            <div style={{ position: "absolute", top: "-50%", bottom: "-50%", width: "45%", left: `${sweep}%`, transform: "rotate(18deg)",
                          background: "linear-gradient(90deg, transparent, rgba(255,255,255,0.0) 20%, rgba(255,255,255,0.8) 50%, rgba(160,255,220,0.3) 65%, transparent)" }} />
          </div>
        </div>
      </div>
    );
  };

// --- scenes -------------------------------------------------------------------------------------------------------------

/** The hook for a sale: the verified price counts up and lands with a flash of light. */
export const PriceReveal: React.FC<SceneProps> = ({ scene, look, e, props }) => {
  const f = useCurrentFrame();
  const value = num(scene.data.value) ?? 0;
  const rays = interpolate(f, [16, 40], [0, 1], clamp);
  const cardIn = spring({ frame: f - 2, fps: 30, config: { damping: 18, stiffness: 90 } });
  return (
    <AbsoluteFill>
      <div style={{ position: "absolute", left: 540 - 700, top: 640 - 700, width: 1400, height: 1400, opacity: 0.35 * rays,
                    transform: `rotate(${f * 0.4}deg) scale(${0.6 + 0.4 * rays})`,
                    background: `repeating-conic-gradient(from 0deg, ${look.accent}55 0deg 6deg, transparent 6deg 24deg)`,
                    maskImage: "radial-gradient(closest-side, black, transparent)", WebkitMaskImage: "radial-gradient(closest-side, black, transparent)" }} />
      {props.assets.card ? (
        <div style={{ position: "absolute", left: 505 - 215, top: 235, opacity: cardIn, transform: `translateY(${interpolate(cardIn, [0, 1], [60, 0])}px)` }}>
          <Card src={props.assets.card} width={430} ratio={props.assets.cardRatio ?? 1.39} look={look} enter={cardIn} floatAmt={0.6 * e} sweepAt={34} />
        </div>
      ) : null}
      <Stage top={props.assets.card ? 860 : 520} center={!props.assets.card}>
        <div style={{ width: "100%", display: "flex", flexDirection: "column", alignItems: "center", gap: 18 }}>
          {str(scene.data.label) ? <Chip text={str(scene.data.label)} color={look.accent} delay={0} /> : null}
          <Counter value={value} decimals={num(scene.data.decimals) ?? 0} startFrame={4} frames={Math.round(30 / Math.max(0.7, e))} size={200}
                   color={C.white} glow={look.glow} />
          {str(scene.data.sub) ? (
            <div style={{ fontFamily: FONT.ui, fontWeight: 700, fontSize: 40, color: C.soft, opacity: interpolate(f, [30, 40], [0, 1], clamp), textAlign: "center" }}>
              {str(scene.data.sub)}
            </div>
          ) : null}
        </div>
      </Stage>
    </AbsoluteFill>
  );
};

/** The card flies in, turns to face the viewer and floats, with its own art blurred behind it (depth). */
export const CardEntrance: React.FC<SceneProps> = ({ scene, look, e, props }) => {
  const f = useCurrentFrame();
  const enter = spring({ frame: f, fps: 30, config: { damping: 13, stiffness: 70 * Math.max(0.7, e) } });
  const name = str(scene.data.name);
  const sub = [str(scene.data.set), str(scene.data.year)].filter(Boolean).join(" · ");
  if (!props.assets.card) return <Punchline {...{ scene, look, e, props, frames: 0, index: 0 }} />;
  return (
    <AbsoluteFill>
      <div style={{ position: "absolute", left: 540 - 300, top: 215, transform: `translateX(${interpolate(enter, [0, 1], [500, 0])}px)` }}>
        <Card src={props.assets.card} width={600} ratio={props.assets.cardRatio ?? 1.39} look={look} enter={enter} floatAmt={e} />
      </div>
      <div style={{ position: "absolute", left: SAFE.left, right: 1080 - SAFE.right, top: 1065, display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
        <div style={{ fontFamily: FONT.ui, fontWeight: 900, fontSize: 30, letterSpacing: 6, color: look.accent, opacity: interpolate(f, [12, 20], [0, 1], clamp) }}>
          {sub.toUpperCase()}
        </div>
        {name ? <div style={{ fontFamily: FONT.ui, fontWeight: 900, fontSize: 54, color: C.white, opacity: interpolate(f, [8, 16], [0, 1], clamp) }}>{name}</div> : null}
      </div>
    </AbsoluteFill>
  );
};

/** A close-up: the camera pushes into the card while its name, set and grade land beside it. */
export const CardDetail: React.FC<SceneProps> = ({ scene, look, e, props, frames }) => {
  const f = useCurrentFrame();
  const p = interpolate(f, [0, Math.max(30, frames)], [0, 1], { ...clamp, easing: easeInOut });
  const grade = str(scene.data.grade);
  const note = str(scene.data.grade_note);
  const badge = spring({ frame: f - 10, fps: 30, config: { damping: 10, stiffness: 160 } });
  if (!props.assets.card) return <Punchline {...{ scene, look, e, props, frames, index: 0 }} />;
  const ratio = props.assets.cardRatio ?? 1.39;
  const w = SAFE.right - SAFE.left;
  return (
    <AbsoluteFill>
      <div style={{ position: "absolute", left: SAFE.left, top: 250, width: SAFE.right - SAFE.left, height: 850, overflow: "hidden", borderRadius: 40,
                    boxShadow: `0 0 0 3px ${look.accent}55, 0 30px 90px rgba(0,0,0,0.7)` }}>
        <Img src={props.assets.card} style={{ position: "absolute", width: w, height: w * ratio, left: 0,
                                              top: interpolate(p, [0, 1], [0, -(w * ratio - 850) * 0.55]),
                                              transform: `scale(${interpolate(p, [0, 1], [1.08, 1.0 + 0.1 * e])})`, transformOrigin: "50% 20%" }} />
        <div style={{ position: "absolute", inset: 0, background: "linear-gradient(180deg, rgba(0,0,0,0) 55%, rgba(4,5,6,0.92) 100%)" }} />
      </div>
      {grade ? (
        <div style={{ position: "absolute", right: 1080 - SAFE.right + 20, top: 860, width: 250, height: 250, borderRadius: "50%",
                      transform: `scale(${badge}) rotate(${interpolate(badge, [0, 1], [-30, -8])}deg)`,
                      background: `radial-gradient(circle at 35% 30%, ${look.accent}, ${props.gold ? C.goldDeep : C.emeraldDeep})`,
                      boxShadow: `0 0 50px ${look.glow}, inset 0 0 0 8px rgba(255,255,255,0.25)`, display: "flex", flexDirection: "column",
                      alignItems: "center", justifyContent: "center", color: "#08120c" }}>
          <div style={{ fontFamily: FONT.ui, fontWeight: 800, fontSize: 24, letterSpacing: 4 }}>GRADE</div>
          <div style={{ fontFamily: FONT.display, fontSize: 82, lineHeight: 1 }}>{grade}</div>
          {note ? <div style={{ fontFamily: FONT.ui, fontWeight: 900, fontSize: 24, letterSpacing: 2 }}>{note.toUpperCase()}</div> : null}
        </div>
      ) : null}
      <div style={{ position: "absolute", left: SAFE.left + 26, top: 905, display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 14, maxWidth: 560 }}>
        {str(scene.data.name) ? <Chip text={str(scene.data.name)} color={C.white} delay={4} size={42} /> : null}
        {str(scene.data.set) ? <Chip text={[str(scene.data.set), str(scene.data.year)].filter(Boolean).join(" · ")} color={look.accent} delay={9} size={34} outline /> : null}
      </div>
    </AbsoluteFill>
  );
};

/** The sale record, drawn as our own info card from the verified data (never a fake screenshot). */
export const Evidence: React.FC<SceneProps> = ({ scene, look }) => {
  const f = useCurrentFrame();
  const rows = (Array.isArray(scene.data.rows) ? scene.data.rows : []) as { k: string; v: string }[];
  const panel = spring({ frame: f, fps: 30, config: { damping: 16, stiffness: 120 } });
  return (
    <Stage top={300} center>
      <div style={{ width: "100%", borderRadius: 40, background: C.panel, border: `2px solid ${C.line}`, padding: "48px 52px", boxSizing: "border-box",
                    boxShadow: "0 40px 100px rgba(0,0,0,0.55)", transform: `translateY(${interpolate(panel, [0, 1], [80, 0])}px)`, opacity: panel }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 30 }}>
          <div style={{ width: 54, height: 54, borderRadius: 16, background: look.accent, display: "flex", alignItems: "center", justifyContent: "center",
                        fontFamily: FONT.ui, fontWeight: 900, fontSize: 34, color: "#06110b" }}>✓</div>
          <div style={{ fontFamily: FONT.ui, fontWeight: 900, fontSize: 54, color: C.white }}>{str(scene.data.title, "Sale record")}</div>
        </div>
        {rows.map((r, i) => {
          const s = spring({ frame: f - 6 - i * 5, fps: 30, config: { damping: 15, stiffness: 150 } });
          return (
            <div key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 30, padding: "22px 0",
                                  borderTop: i ? `2px solid ${C.line}` : undefined, opacity: s, transform: `translateX(${interpolate(s, [0, 1], [50, 0])}px)` }}>
              <div style={{ fontFamily: FONT.ui, fontWeight: 600, fontSize: 40, color: C.muted }}>{r.k}</div>
              <div style={{ fontFamily: FONT.ui, fontWeight: 900, fontSize: 54, color: /\$|\d/.test(r.v) ? look.accent : C.white, textAlign: "right" }}>{r.v}</div>
            </div>
          );
        })}
        {str(scene.data.footer) ? (
          <div style={{ marginTop: 26, fontFamily: FONT.ui, fontWeight: 600, fontSize: 30, color: C.muted, opacity: interpolate(f, [26, 36], [0, 1], clamp) }}>
            {str(scene.data.footer)}
          </div>
        ) : null}
      </div>
    </Stage>
  );
};

/** A line chart drawn from the brief's verified points: the line draws itself, the value counts to the latest point. */
export const Chart: React.FC<SceneProps> = ({ scene, look, e, frames }) => {
  const f = useCurrentFrame();
  const pts = (Array.isArray(scene.data.points) ? scene.data.points : []) as [number, number][];
  const ys = pts.map((p) => p[1]);
  const lo = Math.min(...ys), hi = Math.max(...ys);
  const span = hi - lo || 1;
  const w = 870, h = 520;
  const up = ys.length > 1 && ys[ys.length - 1] >= ys[0];
  const col = up ? C.emerald : C.red;
  const path = pts.map((p, i) => `${i ? "L" : "M"}${(i / Math.max(1, pts.length - 1)) * w},${h - ((p[1] - lo) / span) * (h - 40) - 20}`).join(" ");
  const draw = interpolate(f, [4, Math.min(frames - 6, 50 / e)], [0, 1], { ...clamp, easing: easeOut });
  const change = num(scene.data.change_pct);
  const last = pts.length ? pts[pts.length - 1] : null;
  const yAt = (i: number) => h - ((pts[i][1] - lo) / span) * (h - 40) - 20;
  // the glowing head rides the line as it draws, carrying a price tag
  const hx = draw * w;
  const fi = draw * Math.max(1, pts.length - 1);
  const i0 = Math.min(pts.length - 1, Math.floor(fi));
  const i1 = Math.min(pts.length - 1, i0 + 1);
  const hy = pts.length ? yAt(i0) + (yAt(i1) - yAt(i0)) * (fi - i0) : 0;
  const hv = pts.length ? pts[Math.round(fi)][1] : 0; // the nearest real data point, never an in-between value
  return (
    <Stage top={280}>
      <div style={{ fontFamily: FONT.ui, fontWeight: 800, fontSize: 40, color: C.soft }}>{str(scene.data.title)}</div>
      {num(scene.data.value) !== null ? (
        <Counter value={num(scene.data.value)!} prefix={str(scene.data.prefix, "$")} decimals={num(scene.data.decimals) ?? 0} size={150}
                 color={C.white} glow={look.glow} startFrame={2} frames={28} />
      ) : null}
      {change !== null ? (
        <div style={{ marginTop: 10 }}>
          <Chip text={`${change >= 0 ? "▲" : "▼"} ${Math.abs(change).toFixed(2)}%${str(scene.data.change_label) ? " · " + str(scene.data.change_label) : ""}`}
                color={change >= 0 ? C.emerald : C.red} dark={change >= 0} delay={12} size={36} />
        </div>
      ) : null}
      <div style={{ marginTop: 40, width: w, height: h, position: "relative", borderRadius: 30, background: C.panel, border: `2px solid ${C.line}`, overflow: "hidden" }}>
        <svg width={w} height={h} style={{ position: "absolute", inset: 0 }}>
          <defs>
            <linearGradient id="fill" x1="0" x2="0" y1="0" y2="1">
              <stop offset="0" stopColor={col} stopOpacity={0.35} />
              <stop offset="1" stopColor={col} stopOpacity={0} />
            </linearGradient>
            <clipPath id="reveal"><rect x={0} y={0} width={draw * w} height={h} /></clipPath>
          </defs>
          {[0.25, 0.5, 0.75].map((g) => <line key={g} x1={0} x2={w} y1={h * g} y2={h * g} stroke="rgba(255,255,255,0.07)" strokeWidth={2} />)}
          <g clipPath="url(#reveal)">
            <path d={`${path} L${w},${h} L0,${h} Z`} fill="url(#fill)" />
            <path d={path} fill="none" stroke={col} strokeWidth={8} strokeLinejoin="round" style={{ filter: `drop-shadow(0 0 12px ${col})` }} />
          </g>
          {last && draw > 0.01 ? (
            <g>
              <line x1={0} x2={w} y1={hy} y2={hy} stroke={col} strokeOpacity={0.35} strokeWidth={2} strokeDasharray="10 10" />
              <circle cx={hx} cy={hy} r={30 + Math.sin(f / 4) * 6} fill={col} opacity={0.25} />
              <circle cx={hx} cy={hy} r={13} fill={C.white} stroke={col} strokeWidth={6} />
            </g>
          ) : null}
        </svg>
        {last && draw > 0.01 ? (
          <div style={{ position: "absolute", left: Math.min(w - 230, Math.max(10, hx - 115)), top: Math.max(8, hy - 84), width: 230, textAlign: "center",
                        padding: "8px 0", borderRadius: 14, background: col, color: up ? "#06110b" : C.white, fontFamily: FONT.ui, fontWeight: 900, fontSize: 32 }}>
            {fmtMoney(hv)}
          </div>
        ) : null}
        {str(scene.data.label) ? (
          <div style={{ position: "absolute", left: 28, bottom: 20, fontFamily: FONT.ui, fontWeight: 600, fontSize: 28, color: C.muted }}>{str(scene.data.label)}</div>
        ) : null}
      </div>
    </Stage>
  );
};

/** A news headline: the strap, the words landing, the source underneath. */
export const Headline: React.FC<SceneProps> = ({ scene, look, e, frames, index }) => {
  const f = useCurrentFrame();
  const bar = interpolate(f, [0, 10], [0, 1], { ...clamp, easing: easeOut });
  const text = str(scene.data.text) || str(scene.screen);
  const size = text.length > 70 ? 78 : text.length > 45 ? 92 : 112;
  const photo = photoOf(scene.data.photo);
  // breaking news: the words open with a few frames of RGB split
  const split = str(scene.data.kicker) === "BREAKING" ? interpolate(f, [0, 8], [14, 0], clamp) : 0;
  return (
    <AbsoluteFill>
      {photo ? <PhotoLayer photo={photo} look={look} e={e} frames={frames} index={index} dim={0.62} /> : null}
      <Stage top={photo ? 560 : 360}>
        <HeadlineBody {...{ scene, look, e, bar, text, size, split }} />
      </Stage>
    </AbsoluteFill>
  );
};

const HeadlineBody: React.FC<{ scene: Scene; look: Look; e: number; bar: number; text: string; size: number; split: number }> =
  ({ scene, look, e, bar, text, size, split }) => (
    <div style={{ filter: split ? `drop-shadow(${split}px 0 0 rgba(255,0,80,0.7)) drop-shadow(${-split}px 0 0 rgba(0,220,255,0.7))` : undefined }}>
      <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
        <Chip text={str(scene.data.kicker, "NEWS")} color={look.accent} />
        {str(scene.data.source) ? <Chip text={str(scene.data.source)} color={C.white} outline delay={6} size={30} /> : null}
      </div>
      <div style={{ height: 8, width: 420 * bar, background: look.accent, borderRadius: 4, margin: "34px 0" }} />
      <Kinetic text={text} size={size} accent={look.accent} mode="rise" stagger={Math.max(2, Math.round(3 / e))} />
    </div>
  );

/** One big verified number and what it means. */
export const Stat: React.FC<SceneProps> = ({ scene, look }) => {
  const f = useCurrentFrame();
  const v = num(scene.data.value);
  return (
    <Stage top={420}>
      {str(scene.data.kicker) ? <Chip text={str(scene.data.kicker)} color={look.accent} /> : null}
      <div style={{ height: 30 }} />
      {v !== null ? <Counter value={v} prefix={str(scene.data.prefix)} suffix={str(scene.data.suffix)} decimals={num(scene.data.decimals) ?? 0} size={210}
                             color={C.white} glow={look.glow} startFrame={3} /> : null}
      <div style={{ marginTop: 24, opacity: interpolate(f, [20, 30], [0, 1], clamp) }}>
        <Kinetic text={str(scene.data.label) || str(scene.screen)} size={64} accent={look.accent} font={FONT.ui} startFrame={20} />
      </div>
    </Stage>
  );
};

/** Kinetic typography for the line that lands the story. */
export const Punchline: React.FC<SceneProps> = ({ scene, look, e, props, index }) => {
  const f = useCurrentFrame();
  const text = str(scene.screen) || str(scene.data.text);
  const size = text.length > 28 ? 120 : text.length > 16 ? 150 : 190;
  const words = text.split(/\s+/).filter(Boolean).length;
  const line = interpolate(f, [6 + words * 4, 20 + words * 4], [0, 1], { ...clamp, easing: easeOut });
  const side = index % 2 ? 1 : -1;
  return (
    <AbsoluteFill>
      {props.assets.card ? ( // the card, big and soft behind the words: a second depth layer drifting the other way
        <div style={{ position: "absolute", left: 540 - 380 + side * 140 - side * f * 0.6 * e, top: 260, width: 760, opacity: 0.16,
                      transform: `rotate(${side * 12}deg)`, filter: "blur(3px) saturate(1.3)" }}>
          <Img src={props.assets.card} style={{ width: 760, borderRadius: 34 }} />
        </div>
      ) : null}
      <Stage top={300} center>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 34 }}>
          <Kinetic text={text} size={size} accent={look.accent} mode={e > 1.1 ? "slam" : "rise"} stagger={e > 1.1 ? 5 : 4} align="center" />
          <div style={{ height: 10, width: 360 * line, borderRadius: 5, background: look.accent, boxShadow: `0 0 24px ${look.glow}` }} />
        </div>
      </Stage>
    </AbsoluteFill>
  );
};

const photoOf = (v: unknown): Photo | null => {
  const p = v as Photo | null | undefined;
  return p && typeof p.src === "string" ? p : null;
};

/** A licensed photo filling the frame: a slow Ken Burns push and pan (wide pictures pan across), a blurred copy of itself
 *  behind (no black bars), the brand grade, a light leak sweeping over, and its credit line. */
export const PhotoLayer: React.FC<{ photo: Photo; look: Look; e: number; frames: number; index: number; dim?: number; height?: number }> =
  ({ photo, look, e, frames, index, dim = 0.45, height = 1920 }) => {
    const f = useCurrentFrame();
    const p = interpolate(f, [0, Math.max(30, frames)], [0, 1], clamp);
    const src = staticFile(photo.src);
    const wide = photo.ratio < 1.2; // narrower than the 9:16 frame: pan across it
    const dir = index % 2 ? 1 : -1;
    const pan = wide ? interpolate(p, [0, 1], [-1, 1]) * dir * Math.min(260, (1 / Math.max(0.3, photo.ratio)) * 120) : 0;
    const scale = interpolate(p, [0, 1], [1.14, 1.14 + 0.16 * e]);
    const enter = spring({ frame: f, fps: 30, config: { damping: 18, stiffness: 120 } });
    const leak = interpolate(f, [4, 40], [-0.6, 1.6], clamp);
    return (
      <AbsoluteFill style={{ height, overflow: "hidden" }}>
        <Img src={src} style={{ position: "absolute", inset: -80, width: "calc(100% + 160px)", height: "calc(100% + 160px)", objectFit: "cover",
                                filter: "blur(40px) saturate(1.3) brightness(0.6)" }} />
        <Img src={src} style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover",
                                transform: `translateX(${pan}px) scale(${scale * interpolate(enter, [0, 1], [1.12, 1])})`,
                                filter: `saturate(1.12) contrast(1.06) blur(${interpolate(enter, [0, 1], [10, 0])}px)` }} />
        {/* the brand grade: a tint in the accent colour, dark top and bottom for the brand tag and the words */}
        <AbsoluteFill style={{ background: look.accent, mixBlendMode: "color", opacity: 0.16 }} />
        <AbsoluteFill style={{ background: `linear-gradient(180deg, rgba(0,0,0,${dim + 0.2}) 0%, rgba(0,0,0,${dim * 0.35}) 30%, rgba(0,0,0,${dim * 0.5}) 55%, rgba(0,0,0,${dim + 0.4}) 100%)` }} />
        <div style={{ position: "absolute", top: -400, bottom: -400, width: 520, left: `${leak * 100}%`, transform: "rotate(16deg)", mixBlendMode: "screen", opacity: 0.35,
                      background: `linear-gradient(90deg, transparent, ${look.accent}88, rgba(255,240,200,0.55), transparent)`, filter: "blur(30px)" }} />
        <div style={{ position: "absolute", right: 1080 - SAFE.right - 30, top: SAFE.top + 70, fontFamily: FONT.ui, fontWeight: 600, fontSize: 21,
                      color: "rgba(255,255,255,0.72)", textShadow: "0 2px 6px rgba(0,0,0,0.8)", maxWidth: 520, textAlign: "right" }}>{photo.credit}</div>
      </AbsoluteFill>
    );
  };

/** A real photo with the segment's words slammed over it (or, for a b-roll shot, the photo alone moving). */
export const PhotoScene: React.FC<SceneProps> = ({ scene, look, e, frames, index }) => {
  const f = useCurrentFrame();
  const photo = photoOf(scene.data.photo);
  const text = str(scene.screen);
  if (!photo) return null;
  const size = text.length > 28 ? 118 : text.length > 16 ? 146 : 180;
  const words = text.split(/\s+/).filter(Boolean).length;
  const bar = interpolate(f, [6 + words * 4, 18 + words * 4], [0, 1], { ...clamp, easing: easeOut });
  return (
    <AbsoluteFill>
      <PhotoLayer photo={photo} look={look} e={e} frames={frames} index={index} dim={text ? 0.5 : 0.25} />
      {text ? (
        <div style={{ position: "absolute", left: SAFE.left, width: SAFE.right - SAFE.left, top: 640, display: "flex", flexDirection: "column", gap: 26,
                      filter: "drop-shadow(0 10px 30px rgba(0,0,0,0.85))" }}>
          <Kinetic text={text} size={size} accent={look.accent} mode="slam" stagger={e > 1.1 ? 4 : 5} />
          <div style={{ height: 12, width: 380 * bar, borderRadius: 6, background: look.accent, boxShadow: `0 0 26px ${look.glow}` }} />
        </div>
      ) : null}
    </AbsoluteFill>
  );
};

/** The coin: its logo spins in in 3D (with an edge), pulses with light rings, and its live price counts up. */
export const CoinScene: React.FC<SceneProps> = ({ scene, look, e, props }) => {
  const f = useCurrentFrame();
  const spin = spring({ frame: f, fps: 30, config: { damping: 20, stiffness: 55 * Math.max(0.8, e) } });
  const ry = interpolate(spin, [0, 1], [-540, 0]) + Math.sin(f / 22) * 10;
  const rx = Math.cos(f / 30) * 6;
  const size = 470;
  const change = num(scene.data.change_pct);
  const value = num(scene.data.value);
  const logo = props.assets.coin;
  const face = (z: number, shade: number) => (
    <div key={z} style={{ position: "absolute", inset: 0, transform: `translateZ(${z}px)`, borderRadius: "50%", overflow: "hidden",
                          filter: shade < 1 ? `brightness(${shade})` : undefined, background: logo ? undefined : look.accent,
                          display: "flex", alignItems: "center", justifyContent: "center" }}>
      {logo ? <Img src={logo} style={{ width: "100%", height: "100%" }} />
        : <span style={{ fontFamily: FONT.display, fontSize: 170, color: "#06110b" }}>{str(scene.data.symbol)}</span>}
    </div>
  );
  return (
    <AbsoluteFill>
      {[0, 1, 2].map((k) => { // light rings pulsing out from the coin
        const t = ((f + k * 20) % 60) / 60;
        return <div key={k} style={{ position: "absolute", left: 540 - 300, top: 470 - 300, width: 600, height: 600, borderRadius: "50%",
                                     border: `4px solid ${look.accent}`, opacity: (1 - t) * 0.5 * spin, transform: `scale(${0.8 + t * 0.9})` }} />;
      })}
      <div style={{ position: "absolute", left: 540 - size / 2, top: 470 - size / 2, width: size, height: size, perspective: 1400 }}>
        <div style={{ position: "absolute", inset: -60, borderRadius: "50%", background: look.glow, filter: "blur(70px)", opacity: 0.9 }} />
        <div style={{ position: "absolute", inset: 0, transformStyle: "preserve-3d", transform: `rotateY(${ry}deg) rotateX(${rx}deg) scale(${interpolate(spin, [0, 1], [0.4, 1])})` }}>
          {Array.from({ length: 10 }).map((_, i) => face(-i * 2.4, 0.45))}
          {face(1, 1)}
        </div>
      </div>
      <div style={{ position: "absolute", left: SAFE.left, right: 1080 - SAFE.right, top: 770, display: "flex", flexDirection: "column", alignItems: "center", gap: 16 }}>
        <Chip text={`${str(scene.data.symbol)} · ${str(scene.data.label, "PRICE NOW")}`} color={look.accent} delay={14} size={34} />
        {value !== null ? <Counter value={value} decimals={num(scene.data.decimals) ?? 0} startFrame={14} frames={26} size={170} color={C.white} glow={look.glow} /> : null}
        <div style={{ display: "flex", gap: 14, alignItems: "center" }}>
          {change !== null ? <Chip text={`${change >= 0 ? "▲" : "▼"} ${Math.abs(change).toFixed(2)}% · 24h`} color={change >= 0 ? C.emerald : C.red} dark={change >= 0} delay={30} size={32} /> : null}
          {str(scene.data.source) ? <Chip text={str(scene.data.source)} color={C.white} outline delay={34} size={26} /> : null}
        </div>
      </div>
    </AbsoluteFill>
  );
};

/** A countdown item (#3, #2, #1). */
export const ListItem: React.FC<SceneProps> = ({ scene, look }) => {
  const f = useCurrentFrame();
  const s = spring({ frame: f, fps: 30, config: { damping: 10, stiffness: 150 } });
  return (
    <Stage top={340}>
      <div style={{ fontFamily: FONT.display, fontSize: 300, lineHeight: 1, color: look.accent, transform: `scale(${s})`, transformOrigin: "left center",
                    textShadow: `0 0 60px ${look.glow}` }}>#{str(scene.data.n, "1")}</div>
      <Kinetic text={str(scene.screen) || str(scene.data.text)} size={96} accent={look.accent} startFrame={8} />
    </Stage>
  );
};

/** The branded ending: Kiwi hops in, the wordmark and the site. Same treatment on every video. */
export const Outro: React.FC<SceneProps> = ({ scene, look, props }) => {
  const f = useCurrentFrame();
  const hop = spring({ frame: f - 2, fps: 30, config: { damping: 8, stiffness: 140 } });
  const bob = Math.sin(f / 7) * 6;
  const word = spring({ frame: f - 8, fps: 30, config: { damping: 14, stiffness: 120 } });
  return (
    <AbsoluteFill>
      <Particles kind={props.theme === "community" ? "confetti" : "sparkle"} color={look.accent} e={1} total={0} count={24} />
      <div style={{ position: "absolute", left: 540 - 230, top: 330, width: 460, transform: `translateY(${interpolate(hop, [0, 1], [500, 0]) + bob}px)` }}>
        <div style={{ position: "absolute", inset: 30, borderRadius: "50%", background: look.glow, filter: "blur(60px)" }} />
        <Img src={staticFile("kiwi.png")} style={{ width: 460, position: "relative",
                                                            filter: `drop-shadow(0 0 3px ${look.accent}) drop-shadow(0 0 22px ${look.accent}aa)` }} />
      </div>
      <div style={{ position: "absolute", left: SAFE.left, right: 1080 - SAFE.right, top: 790, display: "flex", flexDirection: "column", alignItems: "center", gap: 18,
                    opacity: word, transform: `translateY(${interpolate(word, [0, 1], [40, 0])}px)` }}>
        <div style={{ fontFamily: FONT.display, fontSize: 150, lineHeight: 1, color: C.white, letterSpacing: 3 }}>
          PRICE <span style={{ color: look.accent }}>CHAT</span>
        </div>
        {str(scene.data.cta) ? <div style={{ fontFamily: FONT.ui, fontWeight: 700, fontSize: 42, color: C.soft, textAlign: "center" }}>{str(scene.data.cta)}</div> : null}
        {props.brand.site ? <Chip text={props.brand.site} color={look.accent} delay={14} size={38} /> : null}
      </div>
    </AbsoluteFill>
  );
};

export const SCENES: Record<string, React.FC<SceneProps>> = {
  price_reveal: PriceReveal, card_entrance: CardEntrance, card_detail: CardDetail, evidence: Evidence, chart: Chart,
  headline: Headline, stat: Stat, punchline: Punchline, list: ListItem, outro: Outro, coin: CoinScene, photo: PhotoScene,
};

export { fmtMoney };
