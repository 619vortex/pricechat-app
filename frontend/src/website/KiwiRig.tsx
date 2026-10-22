/**
 * The Kiwi rig: the canonical mascot (/brand/kiwi.webp) with depth, never redrawn.
 *
 * Every layer is the same image, cut with clip-path: body, crest feathers, legs (story) and the eye, whose
 * contents can move inside a fixed window (a glance). On top sits one shading layer masked to the exact
 * silhouette: a soft highlight on the back, shade under the belly and a green rim from the Price Chat light.
 * At rest every layer lines up with the original image, so the Kiwi looks exactly like the logo.
 *
 * Used by the hero (calm idle life, below) and by the Story (KiwiFlight drives the same layers).
 */
import { useEffect, useRef, type ReactNode, type RefObject } from "react";
import { reducedMotion } from "./motion";
import { site } from "./websiteData";

export function KiwiRig({ variant, alt = "", eager = false, children }: {
  variant: "hero" | "story";
  alt?: string;
  eager?: boolean;
  children?: ReactNode;
}) {
  const { src, width, height } = site.kiwi;
  const story = variant === "story";
  const layer = (cls: string, a = "") => (
    <img className={`kr-img ${cls}`} src={src} width={width} height={height} alt={a} decoding="async"
         loading={eager ? "eager" : "lazy"} {...(eager && a ? { fetchPriority: "high" as const } : {})} />
  );
  const wing = (cls: string) => (
    <svg className={`kr-wing ${cls}`} viewBox="0 0 100 100" aria-hidden>
      <path d="M96 96 C78 84 52 66 30 40 C24 32 18 22 14 10 C26 20 34 22 40 20 C42 30 48 34 56 34 C58 46 66 56 74 60 C84 70 92 82 96 96 Z" />
    </svg>
  );
  const mask = `url(${src})`;
  return (
    <div className={`kr kr-${variant}`}>
      <div className="kr-persp">
        <div className="kr-tilt">
          {story && wing("kr-wing-far")}
          {story && layer("kr-leg kr-leg-l")}
          {story && layer("kr-leg kr-leg-r")}
          {layer("kr-body", alt)}
          {layer("kr-crest")}
          <span className="kr-shade" style={{ WebkitMaskImage: mask, maskImage: mask }} aria-hidden>
            <span className="kr-light" />
          </span>
          <span className="kr-rim" style={{ WebkitMaskImage: `${mask}, ${mask}`, maskImage: `${mask}, ${mask}` }} aria-hidden />
          {story && wing("kr-wing-near")}
          <span className="kr-eye" aria-hidden>{layer("kr-eye-img")}</span>
          {story && layer("kr-eyebig")}
          <span className="kr-lid" aria-hidden />
          {children}
        </div>
      </div>
    </div>
  );
}

/* --- hero idle: calm, irregular, mostly still ------------------------------------------------------------ */

/** Small seeded PRNG: the same "random" rhythm on every load (deterministic), never a fixed loop. */
function mulberry32(seed: number) {
  return () => {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const blinkFrames = (lid: number): Keyframe[] => [
  { transform: "scaleY(0)" }, { transform: `scaleY(${lid})`, offset: 0.45 }, { transform: `scaleY(${lid})`, offset: 0.55 }, { transform: "scaleY(0)" },
];

/**
 * Gives the hero Kiwi small, occasional signs of life: blinks (sometimes double), a glance, a tiny head tilt, a
 * ruffle of the crest feathers, and long stretches of nothing. Pauses while the hero is off screen, the page is
 * scrolled or the tab is hidden. Phones get blinks only. Reduced motion: nothing.
 */
export function useKiwiIdle(root: RefObject<HTMLElement | null>) {
  const started = useRef(false);
  useEffect(() => {
    const el = root.current;
    if (!el || started.current || typeof el.animate !== "function" || reducedMotion()) return;
    started.current = true;
    const rand = mulberry32(20261004);
    const simple = typeof matchMedia === "function" && matchMedia("(max-width: 760px), (hover: none)").matches;
    const q = (s: string) => el.querySelector<HTMLElement>(s);
    let timer = 0, visible = true;
    const io = typeof IntersectionObserver !== "undefined"
      ? new IntersectionObserver(([e]) => (visible = e.isIntersecting))
      : null;
    io?.observe(el);
    const awake = () => visible && !document.hidden && !document.documentElement.classList.contains("pc-scrolled");

    const blink = (double = false) => {
      const lid = q(".kr-lid");
      if (!lid) return;
      const a = lid.animate(blinkFrames(1), { duration: 150, easing: "ease-in-out" });
      if (double) a.finished.then(() => window.setTimeout(() => lid.animate(blinkFrames(1), { duration: 140 }), 110), () => {});
    };
    const glance = () => {
      const eye = q(".kr-eye-img");
      if (!eye) return;
      const dirs = [[0.6, 0], [-0.5, 0.1], [0.4, -0.5], [0.2, 0.5]];
      const [x, y] = dirs[Math.floor(rand() * dirs.length)];
      const hold = 700 + rand() * 900;
      const to = `translate(${x}%, ${y}%)`;
      eye.animate([{ transform: "translate(0, 0)" }, { transform: to, offset: 120 / (hold + 240) }, { transform: to, offset: (hold + 120) / (hold + 240) }, { transform: "translate(0, 0)" }],
        { duration: hold + 240, easing: "ease-in-out" });
    };
    const tilt = () => {
      const t = q(".kr-tilt");
      if (!t) return;
      const d = (rand() < 0.5 ? -1 : 1) * (0.9 + rand() * 0.8);
      const hold = 900 + rand() * 900;
      const to = `rotate(${d.toFixed(2)}deg) translateY(-0.4%)`;
      t.animate([{ transform: "none" }, { transform: to, offset: 260 / (hold + 520) }, { transform: to, offset: (hold + 260) / (hold + 520) }, { transform: "none" }],
        { duration: hold + 520, easing: "cubic-bezier(.4,0,.2,1)" });
    };
    const ruffle = () => q(".kr-crest")?.animate(
      [{ transform: "none" }, { transform: "rotate(-4deg)" }, { transform: "rotate(3deg)" }, { transform: "rotate(-2deg)" }, { transform: "none" }],
      { duration: 620, easing: "ease-in-out" });

    const next = () => {
      const r = rand();
      let wait = 1800 + rand() * 4200;
      if (awake()) {
        if (simple) {
          if (r < 0.75) blink(r < 0.12);
          wait += 2500;
        } else if (r < 0.32) blink();
        else if (r < 0.44) blink(true);
        else if (r < 0.62) glance();
        else if (r < 0.74) tilt();
        else if (r < 0.84) ruffle();
        else wait += 4000 + rand() * 4000; // a long, still pause
      }
      timer = window.setTimeout(next, wait);
    };
    timer = window.setTimeout(next, 1600); // after the hero entrance
    return () => {
      window.clearTimeout(timer);
      io?.disconnect();
      started.current = false;
    };
  }, [root]);
}
