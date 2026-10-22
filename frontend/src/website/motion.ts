/**
 * Price Chat motion system: the few behaviours CSS can't do alone. Styles and keyframes live in motion.css.
 * Everything here checks reduced motion and does nothing (or shows the final state at once) when it is on.
 */

import { useEffect, useRef, useState, type RefObject } from "react";

export function reducedMotion(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

/** A real mouse on a desktop-sized screen (never touch, never phones). */
export function finePointer(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(hover: hover) and (pointer: fine)").matches && window.innerWidth > 960;
}

/**
 * Scroll reveal for every `.site-reveal` inside the returned ref (including ones added later), and pausing of
 * idle animations in `[data-pause-offscreen]` elements while they are out of view. Off with reduced motion.
 */
export function useReveal<T extends HTMLElement = HTMLDivElement>(): RefObject<T | null> {
  const ref = useRef<T>(null);
  useEffect(() => {
    const root = ref.current;
    if (!root || typeof IntersectionObserver === "undefined" || reducedMotion()) return;
    root.classList.add("site-reveal-on");
    const reveal = new IntersectionObserver(
      (entries) => entries.forEach((e) => e.isIntersecting && (e.target.classList.add("is-in"), reveal.unobserve(e.target))),
      { rootMargin: "0px 0px -8% 0px" },
    );
    const pause = new IntersectionObserver((entries) => entries.forEach((e) => e.target.classList.toggle("is-offscreen", !e.isIntersecting)));
    const watch = () => {
      root.querySelectorAll(".site-reveal:not(.is-in)").forEach((el) => reveal.observe(el));
      root.querySelectorAll("[data-pause-offscreen]:not([data-watched])").forEach((el) => {
        el.setAttribute("data-watched", "");
        pause.observe(el);
      });
    };
    watch();
    const mo = new MutationObserver(watch);
    mo.observe(root, { childList: true, subtree: true });
    return () => {
      reveal.disconnect();
      pause.disconnect();
      mo.disconnect();
    };
  }, []);
  return ref;
}

/** True once the element has been (mostly) seen. Immediately true with reduced motion or no observer. */
export function useInViewOnce(ref: RefObject<HTMLElement | null>, threshold = 0.3): boolean {
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || reducedMotion() || typeof IntersectionObserver === "undefined") {
      setSeen(true);
      return;
    }
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) {
        setSeen(true);
        io.disconnect();
      }
    }, { threshold });
    io.observe(el);
    return () => io.disconnect();
  }, [ref, threshold]);
  return seen;
}

/**
 * Desktop-only, barely-there response to the cursor: sets --px / --py in [-1, 1] on the element, which CSS turns into
 * a few pixels of movement. One rAF per frame at most; nothing on touch, phones or reduced motion.
 */
export function usePointerParallax(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const el = ref.current;
    if (!el || reducedMotion() || !finePointer()) return;
    let frame = 0;
    let x = 0;
    let y = 0;
    const onMove = (e: PointerEvent) => {
      x = (e.clientX / window.innerWidth) * 2 - 1;
      y = (e.clientY / window.innerHeight) * 2 - 1;
      if (!frame)
        frame = requestAnimationFrame(() => {
          frame = 0;
          el.style.setProperty("--px", x.toFixed(3));
          el.style.setProperty("--py", y.toFixed(3));
        });
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => {
      window.removeEventListener("pointermove", onMove);
      cancelAnimationFrame(frame);
    };
  }, [ref]);
}

/**
 * A number that counts to its new value when it changes (not on first render). ~650ms, eased; instant with
 * reduced motion. Use only where a change is meaningful (XP, rank score, standings).
 */
export function useCountUp(value: number, duration = 650): number {
  const [shown, setShown] = useState(value);
  const from = useRef(value);
  useEffect(() => {
    const start = from.current;
    from.current = value;
    if (start === value) return;
    if (reducedMotion()) {
      setShown(value);
      return;
    }
    let raf = 0;
    let t0: number | null = null; // timed from the first frame: one clock, never undershoots
    const tick = (t: number) => {
      t0 ??= t;
      const k = Math.min(1, (t - t0) / duration);
      const eased = 1 - Math.pow(1 - k, 3);
      setShown(Math.round(start + (value - start) * eased));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, duration]);
  return shown;
}

/** True the first time this runs in the browser session (e.g. the passport's full entrance), then false. */
export function useFirstTime(key: string): boolean {
  const [first] = useState(() => {
    try {
      if (sessionStorage.getItem(key)) return false;
      sessionStorage.setItem(key, "1");
      return true;
    } catch {
      return false;
    }
  });
  return first && !reducedMotion();
}
