import type { Props, Theme } from "./types";

// Price Chat design system: black/charcoal grounds, emerald accents, strong white type, gold only for rare collectibles.
export const C = {
  black: "#040506",
  charcoal: "#0b0f0e",
  panel: "rgba(18, 24, 22, 0.86)",
  line: "rgba(255,255,255,0.08)",
  emerald: "#19e68c",
  emeraldDeep: "#0a7a4b",
  white: "#ffffff",
  soft: "#dfe7e3",
  muted: "#93a39c",
  gold: "#f6c453",
  goldDeep: "#a8761c",
  red: "#ff4d5e",
};

export const FONT = { display: "Anton, Impact, sans-serif", ui: "Inter, 'Segoe UI', Arial, sans-serif" };

// TikTok's interface covers the right edge (buttons), the bottom (caption + music) and the top (tabs).
export const SAFE = { left: 70, right: 940, top: 170, contentBottom: 1130, captionTop: 1170, captionBottom: 1460 };
export const W = 1080;
export const H = 1920;

export interface Look {
  accent: string;
  accent2: string;
  glow: string;
  label: string; // the small strap at the top
  particles: "dust" | "sparkle" | "grid" | "confetti" | "none";
  grid: boolean;
}

export function look(p: Props): Look {
  const gold = p.gold;
  const map: Record<Theme, Look> = {
    pokemon: { accent: gold ? C.gold : C.emerald, accent2: gold ? C.emerald : "#7cf5c0", glow: gold ? "rgba(246,196,83,0.55)" : "rgba(25,230,140,0.5)",
               label: gold ? "CARD SALE" : "CARD MARKET", particles: "sparkle", grid: false },
    crypto: { accent: C.emerald, accent2: "#4fd1ff", glow: "rgba(25,230,140,0.45)", label: "CRYPTO NEWS", particles: "grid", grid: true },
    trading: { accent: C.emerald, accent2: C.red, glow: "rgba(25,230,140,0.45)", label: "MARKET MOVE", particles: "dust", grid: true },
    viral: { accent: C.emerald, accent2: C.gold, glow: "rgba(25,230,140,0.55)", label: "MONEY STORY", particles: "dust", grid: false },
    community: { accent: C.emerald, accent2: "#ffd36b", glow: "rgba(25,230,140,0.5)", label: "COMMUNITY", particles: "confetti", grid: false },
  };
  return map[p.theme] ?? map.viral;
}

// How strongly things move: calm documentary vs. fast viral cuts.
export function energy(p: Props): number {
  return p.intensity === "calm" ? 0.55 : p.intensity === "high" ? 1.35 : 1.0;
}
