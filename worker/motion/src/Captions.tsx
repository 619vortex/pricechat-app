import React from "react";
import { interpolate, spring, useCurrentFrame } from "remotion";
import { measureText } from "@remotion/layout-utils";
import { clamp } from "./fx";
import { C, FONT, SAFE, type Look } from "./theme";
import type { CaptionStyle, Word } from "./types";

const WIDTH = SAFE.right - SAFE.left - 20;
const OUTLINE = "0 0 2px #000, 0 3px 0 #000, 3px 0 0 #000, -3px 0 0 #000, 0 -3px 0 #000, 0 8px 18px rgba(0,0,0,0.7)";

/** Word-synced captions: one short phrase at a time, inside the safe zone above TikTok's own caption. Never two phrases at once. */
export const Captions: React.FC<{ words: Word[]; style: CaptionStyle; look: Look; hideUntil?: number }> = ({ words, style, look }) => {
  const f = useCurrentFrame();
  const t = f / 30;
  const phrases = React.useMemo(() => {
    const out: Word[][] = [];
    for (const w of words) {
      if (!out.length || out[out.length - 1][0].p !== w.p) out.push([]);
      out[out.length - 1].push(w);
    }
    return out;
  }, [words]);
  // the phrase on screen: from its first word until the next phrase starts (or a beat after its last word)
  let idx = -1;
  for (let i = 0; i < phrases.length; i++) {
    const start = phrases[i][0].s - 0.04;
    const end = i + 1 < phrases.length ? Math.min(phrases[i + 1][0].s - 0.04, phrases[i][phrases[i].length - 1].e + 0.6) : phrases[i][phrases[i].length - 1].e + 0.5;
    if (t >= start && t < end) { idx = i; break; }
  }
  if (idx < 0) return null;
  const ph = phrases[idx];
  const text = ph.map((w) => w.t).join(" ");
  const weight = style === "clean" ? 700 : 900;
  const at100 = measureText({ text, fontFamily: "Inter", fontSize: 100, fontWeight: String(weight) }).width;
  const size = Math.max(58, Math.min(style === "clean" ? 70 : 86, (WIDTH * 1.85 / at100) * 100));
  const startF = Math.round((ph[0].s - 0.04) * 30);
  const enter = spring({ frame: f - startF, fps: 30, config: { damping: 14, stiffness: 220 } });
  const enterStyle: React.CSSProperties = style === "clean"
    ? { opacity: interpolate(f - startF, [0, 5], [0, 1], clamp) }
    : { transform: `scale(${interpolate(enter, [0, 1], [0.86, 1])}) translateY(${interpolate(enter, [0, 1], [18, 0])}px)`, opacity: interpolate(enter, [0, 0.4], [0, 1], clamp) };
  return (
    <div style={{ position: "absolute", left: SAFE.left, width: SAFE.right - SAFE.left, top: SAFE.captionTop, height: SAFE.captionBottom - SAFE.captionTop,
                  display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ ...enterStyle, display: "flex", flexWrap: "wrap", justifyContent: "center", rowGap: 4, maxWidth: WIDTH,
                    fontFamily: FONT.ui, fontWeight: weight, fontSize: size, lineHeight: 1.12, textAlign: "center" }}>
        {ph.map((w, i) => {
          const active = t >= w.s - 0.02 && t < (i + 1 < ph.length ? ph[i + 1].s : w.e + 0.3);
          const spoken = t >= w.s - 0.02;
          let color: string = C.white;
          let extra: React.CSSProperties = { textShadow: OUTLINE };
          if (style === "pop") {
            color = active || w.hl ? (w.hl ? look.accent : C.white) : C.white;
            const pop = active ? spring({ frame: f - Math.round(w.s * 30), fps: 30, config: { damping: 12, stiffness: 260 } }) : 1;
            extra = { ...extra, transform: `scale(${active ? interpolate(pop, [0, 1], [1.18, 1.06]) : 1})`, color: active && !w.hl ? look.accent : color };
            color = (extra.color as string) ?? color;
          } else if (style === "karaoke") {
            color = spoken ? (w.hl ? look.accent : C.white) : "rgba(255,255,255,0.45)";
          } else if (style === "boxed") {
            if (active) {
              extra = { background: look.accent, color: "#06110b", borderRadius: 14, padding: "0 14px", margin: `0 ${size * 0.13 - 14}px`, textShadow: "none",
                        transform: "rotate(-2deg) scale(1.06)", boxShadow: "0 8px 0 rgba(0,0,0,0.45)" };
            }
            color = active ? "#06110b" : w.hl ? look.accent : C.white;
          } else {
            color = w.hl ? look.accent : C.white;
            extra = { textShadow: "0 4px 16px rgba(0,0,0,0.85)" };
          }
          return <span key={i} style={{ display: "inline-block", margin: `0 ${size * 0.13}px`, color, ...extra }}>{w.t}</span>;
        })}
      </div>
    </div>
  );
};
