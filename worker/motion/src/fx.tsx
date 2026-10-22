import React from "react";
import { AbsoluteFill, Easing, interpolate, random, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { C, FONT, H, W, type Look } from "./theme";

export const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
export const easeOut = Easing.bezier(0.16, 1, 0.3, 1);
export const easeInOut = Easing.bezier(0.65, 0, 0.35, 1);

/** Three depth layers that drift at different speeds (parallax), a slow light, a faint grid, vignette and grain. */
export const Background: React.FC<{ look: Look; e: number; bgImage?: string }> = ({ look, e, bgImage }) => {
  const f = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const t = f / 30;
  const far = { x: Math.sin(t * 0.21) * 60 * e, y: Math.cos(t * 0.17) * 40 * e };
  const mid = { x: Math.sin(t * 0.33 + 1) * 110 * e, y: Math.cos(t * 0.29 + 2) * 90 * e };
  const sweep = interpolate(f % 240, [0, 240], [-0.4, 1.4]);
  return (
    <AbsoluteFill style={{ background: `radial-gradient(120% 80% at 50% 0%, ${C.charcoal} 0%, ${C.black} 70%)`, overflow: "hidden" }}>
      {bgImage ? (
        <AbsoluteFill style={{ transform: `translate(${far.x}px, ${far.y}px) scale(1.5)`, opacity: 0.18, filter: "blur(60px) saturate(1.4)" }}>
          <img src={bgImage} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
        </AbsoluteFill>
      ) : null}
      <div style={{ position: "absolute", left: -300 + far.x, top: -420 + far.y, width: 1500, height: 1100, borderRadius: "50%",
                    background: `radial-gradient(closest-side, ${look.glow}, transparent)`, opacity: 0.55 }} />
      <div style={{ position: "absolute", left: 380 + mid.x, top: 1250 + mid.y, width: 1100, height: 900, borderRadius: "50%",
                    background: `radial-gradient(closest-side, ${look.glow}, transparent)`, opacity: 0.22 }} />
      {look.grid ? <Grid e={e} /> : null}
      <div style={{ position: "absolute", inset: 0, opacity: 0.07,
                    background: `linear-gradient(115deg, transparent ${sweep * 100 - 12}%, white ${sweep * 100}%, transparent ${sweep * 100 + 12}%)` }} />
      <Particles kind={look.particles} color={look.accent} e={e} total={durationInFrames} />
      <AbsoluteFill style={{ background: "radial-gradient(90% 70% at 50% 45%, transparent 55%, rgba(0,0,0,0.75) 100%)" }} />
      <Grain />
    </AbsoluteFill>
  );
};

const Grid: React.FC<{ e: number }> = ({ e }) => {
  const f = useCurrentFrame();
  const off = (f * 0.6 * e) % 90;
  return (
    <AbsoluteFill style={{ opacity: 0.09, transform: `perspective(900px) rotateX(58deg) translateY(${off + 300}px) scale(2.2)`,
                           backgroundImage: "linear-gradient(rgba(255,255,255,.9) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.9) 1px, transparent 1px)",
                           backgroundSize: "90px 90px", transformOrigin: "50% 100%" }} />
  );
};

/** Deterministic particles (same frame = same picture, so renders are reproducible). */
export const Particles: React.FC<{ kind: Look["particles"]; color: string; e: number; total: number; count?: number }> = ({ kind, color, e, count }) => {
  const f = useCurrentFrame();
  if (kind === "none") return null;
  const n = count ?? (kind === "confetti" ? 36 : kind === "sparkle" ? 46 : 40);
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {Array.from({ length: n }).map((_, i) => {
        const r = (k: string) => random(`${kind}-${i}-${k}`);
        const speed = (0.4 + r("s") * 1.2) * e;
        const x = r("x") * W + Math.sin(f / 40 + i) * 18;
        const y = (((r("y") * H - f * speed * 2) % H) + H) % H;
        const size = kind === "grid" ? 3 + r("z") * 4 : kind === "confetti" ? 8 + r("z") * 10 : 2 + r("z") * 5;
        const tw = kind === "sparkle" ? 0.25 + 0.75 * Math.abs(Math.sin(f / (8 + r("t") * 14) + i)) : 0.35 + r("o") * 0.5;
        const style: React.CSSProperties = { position: "absolute", left: x, top: y, width: size, height: size, opacity: tw * (kind === "dust" ? 0.45 : 0.7),
                                             background: kind === "confetti" ? (i % 3 ? color : i % 2 ? C.gold : C.white) : color,
                                             borderRadius: kind === "grid" ? 1 : kind === "confetti" ? 2 : "50%",
                                             boxShadow: kind === "sparkle" ? `0 0 ${size * 3}px ${color}` : undefined,
                                             transform: kind === "confetti" ? `rotate(${f * 3 + i * 40}deg)` : undefined };
        return <div key={i} style={style} />;
      })}
    </AbsoluteFill>
  );
};

const Grain: React.FC = () => {
  const f = useCurrentFrame();
  const seed = f % 8;
  return (
    <AbsoluteFill style={{ opacity: 0.06, mixBlendMode: "overlay" }}>
      <svg width={W} height={H}>
        <filter id={`grain${seed}`}>
          <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves={2} seed={seed} />
        </filter>
        <rect width={W} height={H} filter={`url(#grain${seed})`} />
      </svg>
    </AbsoluteFill>
  );
};

/** A directional (horizontal or vertical) motion blur for fast moves, via an SVG filter. */
export const MotionBlur: React.FC<{ amount: number; vertical?: boolean; id: string; children: React.ReactNode }> = ({ amount, vertical, id, children }) => {
  if (amount < 0.5) return <>{children}</>;
  const dev = vertical ? `0 ${amount}` : `${amount} 0`;
  return (
    <AbsoluteFill style={{ filter: `url(#${id})` }}>
      <svg width={0} height={0} style={{ position: "absolute" }}>
        <filter id={id} x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation={dev} />
        </filter>
      </svg>
      {children}
    </AbsoluteFill>
  );
};

/** A number that counts up to its verified value and lands with a punch. */
export const Counter: React.FC<{ value: number; prefix?: string; suffix?: string; decimals?: number; startFrame?: number; frames?: number; size: number; color: string; glow: string }> =
  ({ value, prefix = "$", suffix = "", decimals = 0, startFrame = 0, frames = 32, size, color, glow }) => {
    const f = useCurrentFrame();
    const p = interpolate(f, [startFrame, startFrame + frames], [0, 1], { ...clamp, easing: Easing.out(Easing.cubic) });
    const shown = value * p;
    const land = spring({ frame: f - startFrame - frames, fps: 30, config: { damping: 9, stiffness: 180 } });
    const scale = 1 + (p >= 1 ? 0.12 * (1 - land) : 0);
    const text = prefix + shown.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }) + suffix;
    return (
      <div style={{ fontFamily: FONT.display, fontSize: size, lineHeight: 1, color, letterSpacing: 2, transform: `scale(${scale})`,
                    textShadow: `0 0 ${30 + 50 * (p >= 1 ? 1 - land : 0)}px ${glow}, 0 8px 0 rgba(0,0,0,0.45)`, fontVariantNumeric: "tabular-nums",
                    whiteSpace: "nowrap" }}>
        {text}
      </div>
    );
  };

/** Kinetic typography: words land one by one (slam, rise or type), numbers and key words in the accent colour. */
export const Kinetic: React.FC<{ text: string; size: number; accent: string; mode?: "slam" | "rise" | "type"; startFrame?: number; stagger?: number;
                                  align?: "left" | "center"; maxWidth?: number; color?: string; font?: string }> =
  ({ text, size, accent, mode = "rise", startFrame = 0, stagger = 4, align = "left", maxWidth = 870, color = C.white, font = FONT.display }) => {
    const f = useCurrentFrame();
    const words = text.split(/\s+/).filter(Boolean);
    return (
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: align === "center" ? "center" : "flex-start", columnGap: size * 0.24,
                    rowGap: size * 0.02, maxWidth, fontFamily: font, fontSize: size, lineHeight: 1.04, textTransform: font === FONT.display ? "uppercase" : undefined }}>
        {words.map((w, i) => {
          const local = f - startFrame - i * stagger;
          const s = spring({ frame: local, fps: 30, config: mode === "slam" ? { damping: 11, stiffness: 220 } : { damping: 16, stiffness: 140 } });
          const hot = /\d/.test(w) || w.startsWith("*");
          const clean = w.replace(/^\*/, "");
          const style: React.CSSProperties = mode === "slam"
            ? { transform: `scale(${interpolate(s, [0, 1], [2.3, 1])})`, opacity: interpolate(local, [0, 3], [0, 1], clamp) }
            : mode === "type"
              ? { opacity: local >= 0 ? 1 : 0 }
              : { transform: `translateY(${interpolate(s, [0, 1], [size * 0.7, 0])}px)`, opacity: interpolate(s, [0, 0.6], [0, 1], clamp) };
          return (
            <span key={i} style={{ ...style, display: "inline-block", color: hot ? accent : color,
                                   textShadow: hot ? `0 0 28px ${accent}66` : "0 6px 0 rgba(0,0,0,0.35)" }}>{clean}</span>
          );
        })}
      </div>
    );
  };

/** The rounded strap / chip used for labels across every template. */
export const Chip: React.FC<{ text: string; color: string; dark?: boolean; size?: number; delay?: number; outline?: boolean }> =
  ({ text, color, dark = true, size = 34, delay = 0, outline }) => {
    const f = useCurrentFrame();
    const s = spring({ frame: f - delay, fps: 30, config: { damping: 15, stiffness: 160 } });
    return (
      <div style={{ display: "inline-flex", alignItems: "center", gap: 12, padding: `${size * 0.32}px ${size * 0.7}px`, borderRadius: 999,
                    background: outline ? "rgba(0,0,0,0.35)" : color, border: outline ? `3px solid ${color}` : undefined,
                    color: outline ? color : dark ? "#06110b" : C.white, fontFamily: FONT.ui, fontWeight: 800, fontSize: size, letterSpacing: 1.5,
                    transform: `translateX(${interpolate(s, [0, 1], [-40, 0])}px)`, opacity: s, whiteSpace: "nowrap" }}>
        {text}
      </div>
    );
  };

export function fmtMoney(v: number): string {
  return v >= 100 ? `$${Math.round(v).toLocaleString("en-US")}` : `$${v.toFixed(2)}`;
}
