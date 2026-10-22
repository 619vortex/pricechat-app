/**
 * The Story Kiwi tries to fly. One joke, no words: it wants to, for one glorious second it almost does, then it
 * remembers it's a kiwi.
 *
 * The bird is the shared Kiwi rig (KiwiRig.tsx): the canonical image in layers, never redrawn. This file only
 * moves those layers, plus a few small overlays (wing stubs, sweat, feathers, stars, dust) and a ground shadow
 * that lets go of the bird in the air and catches it again on landing.
 *
 * 2.5D: the body turns in real perspective (rotateY) when it tumbles and when it turns around to walk home; the
 * wings foreshorten as they beat; the far wing is smaller and darker than the near one; the highlight on the
 * body stays put while the body rotates under it; the shadow shrinks and fades with height.
 *
 * Timing lives in one place (the timeline below, in ms). Web Animations API, transform / opacity only, runs
 * only while the scene is on screen, then rests for a while before the next attempt.
 */
import { useEffect, useRef } from "react";
import { KiwiRig } from "./KiwiRig";

const T = 10600; // one attempt: idle, wind-up, run, take-off, panic, fall, crash, recovery, walk back
const REST = 9000; // quiet time between attempts: an easter egg, not a banner
const FIRST = 2600; // first attempt once the Story scene has been seen (after its entrance)

type Pt = [number, string, string?]; // [time ms, value, easing of the segment that starts here]

function frames(prop: "transform" | "opacity", pts: Pt[]): Keyframe[] {
  const s = [...pts].sort((a, b) => a[0] - b[0]);
  if (s[0][0] > 0) s.unshift([0, s[0][1]]);
  if (s[s.length - 1][0] < T) s.push([T, s[s.length - 1][1]]);
  return s.map(([t, v, e]) => ({ offset: t / T, [prop]: v, easing: e ?? "ease-in-out" }));
}

/** Alternates a / b every half period from t0 to t1 (legs running, wings flapping). */
function osc(t0: number, t1: number, period: number, a: string, b: string): Pt[] {
  const out: Pt[] = [];
  for (let t = t0, i = 0; t <= t1; t += period / 2, i++) out.push([Math.round(t), i % 2 ? b : a]);
  return out;
}

const tr = (x: number, y: number) => `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
const rot = (d: number) => `rotate(${d}deg)`;
const turn = (d: number) => `perspective(900px) rotateY(${d}deg)`;
// a wing beat: angle, size, and how far it turns toward us (foreshortening)
const wing = (d: number, s: number, y = 0) => `perspective(320px) rotateY(${y}deg) rotate(${d}deg) scale(${s})`;

function attempt(el: HTMLElement): Animation[] {
  const scene = (el.closest(".site-horizon") as HTMLElement | null) ?? el.parentElement!;
  const W = el.offsetWidth, H = el.offsetHeight, S = scene.clientWidth, SH = scene.clientHeight;
  // the crashed Kiwi reaches ~1.4 of its width to the right: keep the whole joke inside the scene
  // (and clear of the scene's faded edge, the last ~15%)
  const crash = Math.max(W * 0.8, Math.min(S * 0.34, S * 0.84 - el.offsetLeft - W * 1.4));
  const runX = crash * 0.42, peakX = crash * 0.78, driftX = crash * 0.86, peakY = Math.min(SH * 0.17, H * 0.9);
  const all: Animation[] = [];
  const go = (sel: string, prop: "transform" | "opacity", pts: Pt[]) => {
    const node = el.querySelector<HTMLElement>(sel);
    if (node) all.push(node.animate(frames(prop, pts), { duration: T }));
  };

  // where it is: [t, x, y, easing]
  const path: [number, number, number, string?][] = [
    [0, 0, 0], [950, 0, 0], [1150, W * 0.03, 0], [1350, W * 0.06, 0],
    [1600, W * 0.06, 0, "cubic-bezier(.45,0,.9,.6)"], // run: accelerates
    [2600, runX, 0, "cubic-bezier(.2,.7,.4,1)"], // take-off: a real, curved climb
    [3600, peakX, -peakY, "ease-out"], // ...it's flying?
    [4000, driftX, -peakY - 3], // forward speed dies
    [4450, driftX + crash * 0.02, -peakY + 2, "cubic-bezier(.55,0,1,.45)"], // gravity wins
    [5250, crash, 0, "ease-out"],
    [5380, crash + 3, -H * 0.05, "ease-in"], // tiny impact bounce
    [5500, crash + 4, 0],
    [9050, crash + 4, 0, "ease-in-out"], // walks back to its spot, as if nothing happened
    [10350, 0, 0],
  ];
  go(".kf-move", "transform", path.map(([t, x, y, e]) => [t, tr(x, y), e]));
  // the shadow stays on the ground: it follows along, shrinks and fades with height, reconnects on landing
  go(".kf-shadow", "transform", path.map(([t, x, y, e]) => [t, `translateX(${x.toFixed(1)}px) scale(${(1 - 0.5 * Math.min(1, -y / peakY)).toFixed(3)})`, e]));
  go(".kf-shadow", "opacity", path.map(([t, , y, e]) => [t, (1 - 0.7 * Math.min(1, -y / peakY)).toFixed(3), e]));
  // ...and under the face-plant the body's weight sits further forward
  go(".kf-shadow-in", "transform", [[0, "none"], [5200, "none"], [5300, "translateX(42%) scaleX(1.12)"], [7500, "translateX(42%) scaleX(1.12)"], [8050, "none"]]);

  // body angle (pivot near the front foot): lean, nose up, wobble, tumble, face-first, back up, shake it off
  const tilt: [number, number, string?][] = [
    [0, 0], [1100, -3], [1500, 4], [1750, 10], [2600, 10], [2800, -12], [3500, -6],
    [3700, -6], [3850, -2], [4000, -9], [4150, -3], [4300, -8],
    [4450, -10, "cubic-bezier(.5,0,.9,.5)"], [5250, 62, "ease-out"], [5380, 66], [5500, 62],
    [6900, 62, "cubic-bezier(.3,.7,.4,1)"], [7500, 42], [7800, 42, "ease-out"], [8000, -5], [8100, 0],
    [8150, 5], [8250, -5], [8350, 4], [8450, -2], [8550, 0],
  ];
  go(".kf-tilt", "transform", tilt.map(([t, d, e]) => [t, rot(d), e]));
  // the light doesn't rotate with the bird: the highlight slides around the body instead
  go(".kr-light", "transform", tilt.map(([t, d, e]) => [t, rot(-d), e]));
  // squash and stretch (pivot at the feet)
  const sc = (x: number, y: number) => `scale(${x}, ${y})`;
  go(".kf-squash", "transform", [
    [0, sc(1, 1)], [900, sc(1, 1)], [1250, sc(1.05, 0.92)], [1500, sc(1.05, 0.92)], [1650, sc(1, 1)], [2450, sc(1, 1)],
    [2600, sc(1.08, 0.86)], [2750, sc(0.94, 1.08)], [2950, sc(1, 1)], [5250, sc(1, 1)], [5300, sc(1.14, 0.82)],
    [5420, sc(0.96, 1.04)], [5550, sc(1.04, 0.94)], [7800, sc(1.04, 0.94)], [8000, sc(1, 1)], [8200, sc(1.04, 0.96)],
    [8350, sc(0.97, 1.03)], [8500, sc(1, 1)],
  ]);
  // depth: it tumbles a little toward us while falling; later it really turns around to walk home, and back
  go(".kf-face", "transform", [
    [0, turn(0)], [4450, turn(0), "ease-in"], [4950, turn(28)], [5250, turn(12), "ease-out"], [5500, turn(0)],
    [8950, turn(0), "cubic-bezier(.4,0,.2,1)"], [9200, turn(180)], [10250, turn(180), "cubic-bezier(.4,0,.2,1)"], [10500, turn(360)],
  ]);

  // legs: two little steps, a sprint, tucked in flight, flailing, kicking in the air, walking home
  for (const [sel, sign, lag] of [[".kr-leg-l", 1, 0], [".kr-leg-r", -1, 1]] as const) {
    const a = (d: number) => rot(d * sign);
    go(sel, "transform", [
      [0, rot(0)], [950 + lag * 200, rot(0)], [1050 + lag * 200, rot(-14)], [1150 + lag * 200, rot(0)],
      ...osc(1600, 2600, 110, a(30), a(-30)),
      [2650, rot(sign > 0 ? 40 : 28)], [3750, rot(sign > 0 ? 40 : 28)],
      ...osc(3800, 5250, 90, rot(20 + 22 * sign), rot(20 - 22 * sign)),
      [5300, rot(0)], ...osc(6000, 6500, 160, a(18), a(-18)), [6560, rot(0)],
      [9050, rot(0)], ...osc(9100, 10300, 200, a(18), a(-18)), [10350, rot(0)],
    ]);
  }
  // wings: the kiwi barely has any, which is the point. They turn toward us on the down-beat (depth).
  for (const [sel, amp, lag, sz] of [[".kr-wing-near", 1, 0, 1], [".kr-wing-far", 0.8, 25, 0.86]] as const) {
    go(sel, "transform", [
      [0, wing(0, 0)], [1200, wing(-5, 0.5 * sz)], [1300, wing(12, 0.5 * sz)], [1400, wing(-8, 0.5 * sz)], [1500, wing(0, 0)],
      [2550, wing(0, 0)],
      ...osc(2620 + lag, 3700, 100, wing(-35 * amp, sz, 0), wing(25 * amp, sz, 45 * amp)), // flapping ridiculously hard
      ...osc(3700 + lag, 4500, 60, wing(-55 * amp, 1.05 * sz, 0), wing(35 * amp, 1.05 * sz, 55 * amp)), // frantic
      ...osc(4500 + lag, 5250, 130, wing(-25 * amp, 0.9 * sz, 0), wing(15 * amp, 0.9 * sz, 30 * amp)),
      [5320, wing(0, 0)],
      [8150, wing(-10, 0.5 * sz)], [8250, wing(10, 0.5 * sz)], [8350, wing(-8, 0.5 * sz)], [8450, wing(0, 0)], // shakes itself off
    ]);
  }
  // crest feathers: blown back while running, fluttering in the air, shaken out at the end
  go(".kr-crest", "transform", [
    [0, rot(0)], [1600, rot(0)], [1800, rot(-7)], ...osc(1900, 2600, 140, rot(-6), rot(-9)),
    ...osc(2650, 4450, 80, rot(-5), rot(4)), [4500, rot(-10)], [5250, rot(-12)], [5400, rot(4)], [5600, rot(0)],
    ...osc(8150, 8450, 70, rot(-7), rot(6)), [8500, rot(0)],
  ]);
  // the eye: looks ahead with determination, eyes go wide at the peak, glances away embarrassed, then at us
  go(".kr-eye-img", "transform", [
    [0, "translate(0, 0)"], [900, "translate(0, 0)"], [1050, "translate(0.6%, 0.1%)"], [3600, "translate(0.6%, 0.1%)"],
    [3700, "translate(0, -0.4%)"], [5300, "translate(0, 0)"], [7300, "translate(0, 0)"], [7420, "translate(-0.5%, 0.4%)"],
    [8000, "translate(-0.5%, 0.4%)"], [8150, "translate(0, 0)"],
  ]);
  go(".kr-eyebig", "opacity", [[0, "0"], [3700, "0"], [3780, "1"], [5250, "1"], [5300, "0"]]);
  go(".kr-eyebig", "transform", [[0, "scale(1)"], [3700, "scale(1)"], [3820, "scale(1.45)"], [3900, "scale(1.38)"], [5250, "scale(1.38)"], [5300, "scale(1)"]]);
  // blink, dazed, embarrassed, "nothing happened"
  const lid = (s: number) => `scaleY(${s})`;
  go(".kr-lid", "transform", [
    [0, lid(0)], [350, lid(0)], [420, lid(1)], [500, lid(0)], [5320, lid(0)], [5420, lid(1)], [6600, lid(1)], [6800, lid(0)],
    [7200, lid(0)], [7350, lid(0.45)], [7900, lid(0.45)], [8000, lid(0)], [8650, lid(0)], [8760, lid(1)], [8900, lid(0)],
  ]);
  // sweat
  go(".kf-sweat-0", "opacity", [[0, "0"], [3850, "0"], [3900, "1"], [4500, "1"], [4650, "0"]]);
  go(".kf-sweat-0", "transform", [[0, tr(0, 0)], [3850, tr(0, 0), "ease-out"], [4650, "translate(-120%, -140%) rotate(-30deg)"]]);
  go(".kf-sweat-1", "opacity", [[0, "0"], [4050, "0"], [4100, "1"], [4600, "1"], [4750, "0"], [7400, "0"], [7450, "1"], [7850, "1"], [7950, "0"]]);
  go(".kf-sweat-1", "transform", [[0, tr(0, 0)], [4050, tr(0, 0), "ease-out"], [4750, "translate(60%, -160%) rotate(25deg)"], [4760, tr(0, 0)], [7400, tr(0, 0), "ease-in"], [7950, "translate(0, 90%)"]]);
  // motion streaks while it runs
  [0, 1, 2].forEach((i) => {
    go(`.kf-streak-${i}`, "opacity", [[0, "0"], [1800 + i * 60, "0"], [1900 + i * 60, "0.8"], [2200 + i * 60, "0.35"], [2450 + i * 60, "0.8"], [2750, "0"]]);
    go(`.kf-streak-${i}`, "transform", [[0, "scaleX(0.3)"], [1800 + i * 60, "scaleX(0.3)"], [2300, "scaleX(1)"], [2750, "scaleX(0.6)"]]);
  });
  // impact: dust, a small green glow, stars around the head
  [-90, 0, 90].forEach((dx, i) => {
    go(`.kf-dust-${i}`, "opacity", [[0, "0"], [5260, "0"], [5300, "0.9"], [6000, "0"]]);
    go(`.kf-dust-${i}`, "transform", [[0, "translate(0, 0) scale(0.3)"], [5260, "translate(0, 0) scale(0.3)", "ease-out"], [6000, `translate(${dx}%, ${dx ? -40 : -80}%) scale(1.4)`]]);
  });
  go(".kf-glow", "opacity", [[0, "0"], [5250, "0"], [5320, "1"], [6300, "0"]]);
  go(".kf-glow", "transform", [[0, "translateX(-50%) scale(0.5)"], [5250, "translateX(-50%) scale(0.5)", "ease-out"], [6300, "translateX(-50%) scale(1.1)"]]);
  [0, 1, 2].forEach((i) => {
    go(`.kf-star-${i}`, "opacity", [[0, "0"], [5450, "0"], [5550, "1"], [6800, "1"], [7000, "0"]]);
    const orbit: Pt[] = [];
    for (let t = 5450; t <= 7000; t += 90) {
      const a = ((t - 5450) / 700) * Math.PI * 2 + (i * Math.PI * 2) / 3;
      // an ellipse seen at an angle: stars behind the head are smaller and dimmer (depth)
      orbit.push([t, `translate(${(Math.cos(a) * 160).toFixed(0)}%, ${(Math.sin(a) * 55).toFixed(0)}%) scale(${(0.85 + Math.sin(a) * 0.25).toFixed(2)})`, "linear"]);
    }
    go(`.kf-star-${i}`, "transform", [[0, "scale(0)"], ...orbit]);
  });
  // a couple of feathers come loose and float down (in the scene, not attached to the bird)
  [0, 1, 2].forEach((i) => {
    const t0 = 4600 + i * 140, sx = driftX + W * (0.3 + i * 0.06), sy = -peakY + H * (0.2 + i * 0.05);
    const ex = sx - W * (0.18 - i * 0.12), ey = H * (0.86 - i * 0.03);
    go(`.kf-feather-${i}`, "opacity", [[0, "0"], [t0, "0"], [t0 + 80, "0.9"], [t0 + 1300, "0.9"], [t0 + 1700, "0"]]);
    go(`.kf-feather-${i}`, "transform", [
      [0, `${tr(sx, sy)} rotate(0deg)`], [t0, `${tr(sx, sy)} rotate(0deg)`, "ease-out"],
      [t0 + 600, `${tr((sx + ex) / 2 + W * 0.06, (sy + ey) / 2)} rotate(${80 + i * 30}deg)`],
      [t0 + 1700, `${tr(ex, ey)} rotate(${170 + i * 20}deg)`],
    ]);
  });
  return all;
}

export function KiwiFlight() {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = root.current;
    const reduce = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;
    if (!el || typeof el.animate !== "function" || typeof IntersectionObserver === "undefined") return;
    let anims: Animation[] = [];
    let timer = 0, blink = 0;
    let visible = false, running = false, due = true, seen = false;
    const stop = () => (anims.forEach((a) => a.cancel()), (anims = []), (running = false));
    const play = () => {
      if (reduce?.matches) return;
      due = false;
      running = true;
      anims = attempt(el);
      anims[0]?.finished.then(() => (stop(), schedule(REST)), () => {});
    };
    const schedule = (ms: number) => {
      window.clearTimeout(timer);
      timer = window.setTimeout(() => (visible ? play() : (due = true)), ms);
      // one quiet blink while it rests
      window.clearTimeout(blink);
      blink = window.setTimeout(() => visible && !running && el.querySelector(".kr-lid")?.animate(
        [{ transform: "scaleY(0)" }, { transform: "scaleY(1)", offset: 0.5 }, { transform: "scaleY(0)" }], { duration: 170 }), ms / 2);
    };
    const io = new IntersectionObserver(([e]) => {
      visible = e.isIntersecting;
      if (running) anims.forEach((a) => (visible ? a.play() : a.pause()));
      else if (visible && due) schedule(seen ? 1200 : FIRST);
      if (visible) seen = true;
    }, { threshold: 0.35 });
    io.observe(el.closest(".site-horizon") ?? el);
    return () => {
      io.disconnect();
      window.clearTimeout(timer);
      window.clearTimeout(blink);
      stop();
    };
  }, []);

  return (
    <div className="site-horizon-kiwi kf" ref={root} aria-hidden>
      <span className="kf-shadow"><span className="kf-shadow-in" /></span>
      {[0, 1, 2].map((i) => <span key={i} className={`kf-fx kf-feather kf-feather-${i}`} />)}
      <div className="kf-move">
        <span className="kf-fx kf-glow" />
        {[0, 1, 2].map((i) => <span key={i} className={`kf-fx kf-streak kf-streak-${i}`} />)}
        {[0, 1, 2].map((i) => <span key={i} className={`kf-fx kf-dust kf-dust-${i}`} />)}
        <div className="kf-tilt">
          <div className="kf-squash">
            <div className="kf-face">
              <KiwiRig variant="story">
                <span className="kf-fx kf-sweat kf-sweat-0" />
                <span className="kf-fx kf-sweat kf-sweat-1" />
              </KiwiRig>
            </div>
          </div>
        </div>
        {[0, 1, 2].map((i) => (
          <svg key={i} className={`kf-fx kf-star kf-star-${i}`} viewBox="0 0 20 20" aria-hidden>
            <path d="M10 0 L12.2 7.8 L20 10 L12.2 12.2 L10 20 L7.8 12.2 L0 10 L7.8 7.8 Z" />
          </svg>
        ))}
      </div>
    </div>
  );
}
