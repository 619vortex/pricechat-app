import { useEffect, useRef, useState } from "react";
import { playSound } from "./sound";

/** Daily consistency streak, one slim row in the Kiwi Passport's main square: consecutive days you came back AND completed a task. When today's first
 * task lands, the flame flares, the number counts up (a tick per day), the day lights up, and the streak XP pops;
 * milestone days get the level-up fanfare. Sounds only after you've interacted with the page, and never when off. */

export interface StreakView {
  current: number;
  best: number;
  done_today: boolean;
  at_risk: boolean;
  next_day: number;
  next_reward: number;
  next_is_milestone: boolean;
  next_milestone: number | null;
  next_milestone_bonus: number;
  today_xp: number;
  days: { day: string; done: boolean; xp: number; today: boolean }[];
  milestones: Record<string, number>;
}

const SEEN = "pc-streak-seen";
const reduced = () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

function seen(): { n: number; day: string } | null {
  try { return JSON.parse(localStorage.getItem(SEEN) ?? "null"); } catch { return null; }
}

function weekday(d: string): string {
  return new Date(`${d}T12:00:00Z`).toLocaleDateString("en-GB", { weekday: "narrow", timeZone: "UTC" });
}

export function StreakCard({ s }: { s: StreakView }) {
  const today = s.days[s.days.length - 1]?.day ?? "";
  const before = useRef(seen());
  // first render: start from what this browser last showed, so a new day animates from there
  const startFrom = before.current && before.current.n < s.current && s.done_today ? before.current.n : s.current;
  const [shown, setShown] = useState(startFrom);
  const [flare, setFlare] = useState(false);
  const [pop, setPop] = useState<string | null>(null);
  const last = useRef(startFrom);

  useEffect(() => {
    const from = last.current;
    const to = s.current;
    last.current = to;
    try { localStorage.setItem(SEEN, JSON.stringify({ n: to, day: today })); } catch { /* a convenience only */ }
    if (to <= from || !s.done_today) {
      setShown(to);
      return;
    }
    // celebrate: count up, a tick per step, then the flame flare + XP pop (+ fanfare on a milestone)
    if (reduced()) {
      setShown(to);
      setPop(`+${s.today_xp} XP`);
      playSound(s.milestones[String(to)] ? "levelup" : "streak");
      return;
    }
    let n = from;
    const steps = Math.min(to - from, 12);
    const jump = Math.max(1, Math.ceil((to - from) / steps));
    const timers: number[] = [];
    const step = () => {
      n = Math.min(to, n + jump);
      setShown(n);
      if (n < to) {
        playSound("tick");
        timers.push(window.setTimeout(step, 140));
      } else {
        setFlare(true);
        setPop(`+${s.today_xp} XP`);
        playSound(s.milestones[String(to)] ? "levelup" : "streak");
        timers.push(window.setTimeout(() => setFlare(false), 1600));
      }
    };
    timers.push(window.setTimeout(step, 450));
    return () => timers.forEach((t) => window.clearTimeout(t));
  }, [s.current, s.done_today, s.today_xp, s.milestones, today]);

  useEffect(() => {
    if (!pop) return;
    const t = window.setTimeout(() => setPop(null), 2600);
    return () => window.clearTimeout(t);
  }, [pop]);

  const week = s.days.slice(-7);
  return (
    <div className={`stk${flare ? " is-flaring" : ""}${s.done_today ? " is-done" : s.at_risk ? " is-risk" : ""}`} role="group" aria-label="Daily streak">
      <div className={`stk-flame${s.current > 0 ? " is-lit" : ""}`} aria-hidden>
        <svg viewBox="0 0 64 80"><defs>
          <radialGradient id="stkG" cx="50%" cy="70%" r="60%"><stop offset="0" stopColor="#fff6c2" /><stop offset=".45" stopColor="#ffb02e" /><stop offset="1" stopColor="#ff4d1c" /></radialGradient>
        </defs><path className="stk-flame-outer" d="M32 2C36 20 54 28 54 50a22 22 0 1 1-44 0c0-10 5-17 10-22 1 8 5 12 9 13-3-13 0-27 3-39Z" fill="url(#stkG)" />
          <path className="stk-flame-inner" d="M32 40c3 7 11 10 11 19a11 11 0 1 1-22 0c0-5 3-8 5-10 1 4 3 6 5 6-2-6-1-11 1-15Z" fill="#fff3b0" /></svg>
        {flare && <span className="stk-sparks">{Array.from({ length: 10 }, (_, i) => <i key={i} style={{ ["--a" as string]: `${i * 36}deg` }} />)}</span>}
      </div>
      <p className="stk-num" aria-live="polite"><span key={shown} className="stk-digits">{shown}</span><small>{shown === 1 ? "day" : "days"}</small></p>
      <ol className="stk-days" aria-label="Last 7 days">
        {week.map((d, i) => (
          <li key={d.day} className={`${d.done ? "is-done" : ""}${d.today ? " is-today" : ""}`} style={{ ["--i" as string]: i }}
              title={`${weekday(d.day)} ${d.day}${d.done ? ` · streak day · +${d.xp} XP` : ""}`}>
            <span className="stk-dot" />
          </li>
        ))}
      </ol>
      <span className={`stk-state${s.done_today ? " is-done" : ""}`}>{s.done_today ? "Today ✓" : `+${s.next_reward} XP today`}</span>
      {pop && <span className="stk-pop" role="status">{pop}</span>}
    </div>
  );
}
