import { useEffect, useRef, useState } from "react";
import type { TelegramActivity } from "./members";
import { playSound } from "./sound";
import { TelegramButton } from "./community";

/** Telegram chatter (a row of the Kiwi Passport's Daily Missions): how many messages you've sent in the Price Chat Telegram and
 * today's chat XP against the daily cap. The XP is already on your account (the server counts each real message): when
 * you come back, the messages sent since this browser last showed the bar are "converted" on screen: the counter ticks
 * up, a coin per message, then the XP pops. Sounds only after you've interacted with the page, and never when off. */

const SEEN = "pc-tg-seen";
const reduced = () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

function seen(): { m: number; xp: number } | null {
  try { return JSON.parse(localStorage.getItem(SEEN) ?? "null"); } catch { return null; }
}

/** `row`: drawn as a Daily Missions row (the same card as Watch a short / Read a post / Open a pack). */
export function TelegramBar({ t, row = false }: { t: TelegramActivity | null | undefined; row?: boolean }) {
  const total = t?.messages_total ?? 0;
  const xp = t?.xp_total ?? 0;
  const before = useRef(seen());
  const gained = before.current && total > before.current.m ? { m: total - before.current.m, xp: Math.max(0, xp - before.current.xp) } : null;
  const [shown, setShown] = useState(gained ? total - gained.m : total);
  const [pop, setPop] = useState<string | null>(null);
  const [glow, setGlow] = useState(false);

  useEffect(() => {
    if (!t) return;
    try { localStorage.setItem(SEEN, JSON.stringify({ m: total, xp })); } catch { /* a convenience only */ }
    const from = before.current?.m ?? total;
    before.current = { m: total, xp };
    if (total <= from) {
      setShown(total);
      return;
    }
    const earned = gained?.xp ?? 0;
    const finish = () => {
      setGlow(true);
      if (earned > 0) {
        setPop(`+${earned} XP`);
        playSound("xp");
      }
    };
    if (reduced()) {
      setShown(total);
      finish();
      return;
    }
    const timers: number[] = [];
    let n = from;
    const steps = Math.min(total - from, 15);
    const jump = Math.max(1, Math.ceil((total - from) / steps));
    const step = () => {
      n = Math.min(total, n + jump);
      setShown(n);
      playSound("coin");
      if (n < total) timers.push(window.setTimeout(step, 120));
      else timers.push(window.setTimeout(finish, 250));
    };
    timers.push(window.setTimeout(step, 700));
    return () => timers.forEach((x) => window.clearTimeout(x));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [t, total, xp]);

  useEffect(() => {
    if (!pop) return;
    const x = window.setTimeout(() => { setPop(null); setGlow(false); }, 2600);
    return () => window.clearTimeout(x);
  }, [pop]);

  if (!t) {
    return (
      <div className={`tgb is-off${row ? " is-row" : ""}`}>
        <span className="tgb-icon" aria-hidden>✈</span>
        <span className="tgb-main"><b>Telegram chat</b><small>Connect Telegram: every message you send in Price Chat becomes XP</small></span>
        <TelegramButton purpose="link" available label="Connect" className="tgb-go" onDone={() => location.assign("/passport?linked=telegram")} />
      </div>
    );
  }
  const cap = Math.max(1, t.daily_cap || 1);
  const today = Math.min(t.xp_today, cap);
  return (
    <div className={`tgb${glow ? " is-glow" : ""}${row ? " is-row" : ""}`} title={t.note}>
      <span className="tgb-icon" aria-hidden>✈</span>
      <span className="tgb-main">
        <b>Telegram chat</b>
        <span className="tgb-track" role="progressbar" aria-label="Telegram XP today" aria-valuemin={0} aria-valuemax={cap} aria-valuenow={today}>
          <i style={{ width: `${(today / cap) * 100}%` }} />
        </span>
        <small>{t.messages_today} today · {today} / {cap} XP{today >= cap ? " · max for today ✓" : ""}</small>
      </span>
      <span className="tgb-count" aria-live="polite"><span key={shown} className="tgb-num">{shown.toLocaleString("en-GB")}</span><small>messages</small></span>
      {gained && glow && <span className="tgb-conv" aria-hidden>{gained.m} 💬 → 🥝</span>}
      {pop && <span className="tgb-pop" role="status">{pop}</span>}
    </div>
  );
}
