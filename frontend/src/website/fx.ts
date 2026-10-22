/**
 * Kiwi Passport reward effects: a "+N XP" chip with a trail of sparks that flies from where the member pressed to the
 * XP bar, and small particle bursts. Transform / opacity only (Web Animations API), drawn in a fixed layer above the
 * page and removed when done. Reduced motion, or no animation support: nothing is drawn and promises resolve at once.
 */
import { reducedMotion } from "./motion";

const GREEN = ["#00e58f", "#5dffc0", "#b8ffe4"];

function canAnimate(): boolean {
  return typeof document !== "undefined" && typeof document.body.animate === "function" && !reducedMotion();
}

function centre(el: Element): { x: number; y: number } {
  const r = el.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
}

function dot(cls: string, x: number, y: number, colour?: string): HTMLSpanElement {
  const s = document.createElement("span");
  s.className = cls;
  s.style.left = `${x}px`;
  s.style.top = `${y}px`;
  if (colour) s.style.background = colour;
  document.body.appendChild(s);
  return s;
}

/** True when the element is (at least partly) inside the viewport. */
export function onScreen(el: Element | null): boolean {
  if (!el || !el.isConnected || typeof window === "undefined") return false;
  const r = el.getBoundingClientRect();
  return r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth && r.height > 0;
}

/** Sparks out of a point, then gone (~550ms). `shapes` mixes in small stars and Kiwi feathers. */
export function burst(from: Element | { x: number; y: number }, count = 10, spread = 70, shapes = false): void {
  if (!canAnimate()) return;
  const { x, y } = from instanceof Element ? centre(from) : from;
  for (let i = 0; i < count; i++) {
    const a = (Math.PI * 2 * i) / count + Math.random() * 0.5;
    const d = spread * (0.55 + Math.random() * 0.45);
    const kind = !shapes ? "fx-spark" : i % 3 === 1 ? "fx-star" : i % 3 === 2 ? "fx-feather" : "fx-spark";
    const s = dot(kind, x, y, kind === "fx-spark" ? GREEN[i % GREEN.length] : undefined);
    const size = 0.6 + Math.random() * 0.7;
    const spin = kind === "fx-spark" ? 0 : Math.round((Math.random() - 0.5) * 160);
    const tilt = Math.round((a * 180) / Math.PI);
    s.animate(
      [{ transform: `translate(-50%, -50%) rotate(${tilt}deg) scale(${size})`, opacity: 1 },
       { transform: `translate(calc(-50% + ${Math.cos(a) * d}px), calc(-50% + ${Math.sin(a) * d}px)) rotate(${tilt + spin}deg) scale(0)`, opacity: 0 }],
      { duration: 480 + Math.random() * 160, easing: "cubic-bezier(.2,.7,.3,1)" },
    ).onfinish = () => s.remove();
  }
}

/** "+N XP" flies on an arc from `from` to `to`, sparks trailing; resolves on arrival (~620ms). */
export function flyXP(from: Element, to: Element, amount: number): Promise<void> {
  if (!canAnimate()) return Promise.resolve();
  const a = centre(from);
  const b = centre(to);
  // arc height, kept inside the viewport (phones: the chip never leaves the screen)
  const lift = Math.max(0, Math.min(140, Math.abs(b.x - a.x) * 0.25 + 60, Math.min(a.y, b.y) - 28));
  const path = (dx: number, dy: number, s: number) => `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px)) scale(${s})`;
  const mid = { x: (b.x - a.x) * 0.5, y: (b.y - a.y) * 0.5 - lift };
  const chip = dot("fx-chip", a.x, a.y);
  chip.textContent = `+${amount} XP`;
  const frames = (s0: number, s1: number) => [
    { transform: path(0, 0, s0), opacity: 1 },
    { transform: path(0, -18, s0 * 1.18), opacity: 1, offset: 0.18 },
    { transform: path(mid.x, mid.y, s0), opacity: 1, offset: 0.55 },
    { transform: path(b.x - a.x, b.y - a.y, s1), opacity: 0.2 },
  ];
  const flight = chip.animate(frames(1, 0.55), { duration: 620, easing: "cubic-bezier(.45,0,.25,1)" });
  for (let i = 0; i < 6; i++) {
    const s = dot("fx-spark", a.x, a.y, GREEN[i % GREEN.length]);
    s.animate(frames(0.9 - i * 0.1, 0.3), { duration: 620, delay: 40 + i * 35, easing: "cubic-bezier(.45,0,.25,1)", fill: "backwards" })
      .onfinish = () => s.remove();
  }
  return new Promise((resolve) => {
    flight.onfinish = () => {
      chip.remove();
      resolve();
    };
    flight.oncancel = () => {
      chip.remove();
      resolve();
    };
  });
}

/** A one-off "pop" on an element (the XP number, the bar). */
export function pop(el: Element | null, scale = 1.14): void {
  if (!el || !canAnimate()) return;
  el.animate([{ transform: "scale(1)" }, { transform: `scale(${scale})`, offset: 0.35 }, { transform: "scale(1)" }],
    { duration: 380, easing: "cubic-bezier(.3,1.6,.5,1)" });
}

/** A small temporary XP badge at the top right, for XP that lands while the Passport's own XP is scrolled away.
 *  Shows `from`, becomes `to` when the chip arrives (`land`), then fades. Nothing is scrolled. */
export function xpDock(from: number): { el: HTMLElement; land: (to: number) => void } {
  const el = document.createElement("span");
  el.className = "fx-dock";
  el.setAttribute("aria-hidden", "true");
  el.textContent = `${from.toLocaleString("en-GB")} XP`;
  document.body.appendChild(el);
  const anim = canAnimate();
  if (anim) el.animate([{ opacity: 0, transform: "translateY(-6px)" }, { opacity: 1, transform: "none" }], { duration: 180, fill: "both" });
  return {
    el,
    land(to: number) {
      el.textContent = `${to.toLocaleString("en-GB")} XP`;
      el.classList.add("is-landed");
      pop(el, 1.12);
      const out = () => el.remove();
      if (!anim) return void window.setTimeout(out, 1200);
      el.animate([{ opacity: 1 }, { opacity: 0, transform: "translateY(-4px)" }], { duration: 260, delay: 900, fill: "forwards" }).onfinish = out;
    },
  };
}

/** A short green flash on a number that just changed. */
export function flash(el: Element | null): void {
  if (!el || !canAnimate()) return;
  el.animate([{ color: "#00e58f", textShadow: "0 0 14px rgba(0, 229, 143, 0.55)" }, { color: "", textShadow: "none" }],
    { duration: 480, easing: "ease-out" });
}
