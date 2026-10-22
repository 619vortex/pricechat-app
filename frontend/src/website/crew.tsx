import { useEffect, useRef, useState } from "react";
import { burst, pop } from "./fx";
import { memberApi } from "./members";
import { reducedMotion } from "./motion";
import { playSound } from "./sound";
import { InviteModal } from "./invite";

/** INVITE YOUR CREW: the member's personal Telegram invite (a bot deep link, so every friend is attributed), shared through
 *  Telegram's own share sheet (the member picks the chats: no contacts are read, nothing is sent for them). XP is paid by the
 *  server once a friend joined the Price Chat Telegram as a new member and stayed; sharing or copying earns nothing. */

export interface Crew {
  enabled: boolean; link: string; share_url: string; community: string; reward_xp: number; bonus_xp: number; bonus_at: number; hold_hours: number;
  verified: number; pending: number; joined_waiting: number; rejected: number; xp_earned: number; bonus_paid: boolean;
  milestones: { name: string; at: number; done: boolean }[];
}

const SEEN = "pc-crew-verified";

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function CrewCard() {
  const [c, setC] = useState<Crew | null>(null);
  const [copied, setCopied] = useState<"idle" | "ok" | "fail">("idle");
  const [cheer, setCheer] = useState<string | null>(null);
  const [web, setWeb] = useState(false);
  const card = useRef<HTMLElement>(null);
  useEffect(() => {
    let stop = false;
    const load = () => memberApi<Crew>("/members/me/crew").then((d) => { if (!stop) setC(d); }).catch(() => undefined);
    void load();
    const id = window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, 60000);
    return () => { stop = true; window.clearInterval(id); };
  }, []);
  // a friend was verified since the last visit: celebrate once (the XP itself was paid by the server)
  useEffect(() => {
    if (!c) return;
    let before = c.verified;
    try { before = Number(localStorage.getItem(SEEN) ?? c.verified); localStorage.setItem(SEEN, String(c.verified)); } catch { /* private mode */ }
    if (c.verified > before) {
      const n = c.verified - before;
      setCheer(`🥝 ${n === 1 ? "A friend" : `${n} friends`} joined your crew! +${n * c.reward_xp} XP`);
      playSound("levelup");
      if (card.current && !reducedMotion()) burst(card.current, 22, 120, true);
      window.setTimeout(() => setCheer(null), 5000);
    }
  }, [c?.verified]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!c || !c.enabled) return null;
  const toBonus = Math.max(0, c.bonus_at - c.verified);
  const next = c.milestones.find((m) => !m.done);
  return (
    <section ref={card} className="pp-section crew site-reveal" aria-labelledby="crew-title">
      <div className="crew-head">
        <span className="crew-icon" aria-hidden>👥</span>
        <div>
          <h2 className="site-display crew-title" id="crew-title">Invite your crew</h2>
          <p className="crew-lede">Invite friends to Price Chat. Earn <b className="site-green">+{c.reward_xp} XP</b> for each verified new member.</p>
        </div>
      </div>
      {cheer && <p className="crew-cheer" role="status">{cheer}</p>}
      <div className="crew-actions">
        <a className="crew-tg" href={c.share_url} target="_blank" rel="noopener noreferrer" onClick={() => playSound("swish")}>
          <svg viewBox="0 0 24 24" aria-hidden><path d="M21.5 4.2 2.9 11.4c-1.3.5-1.3 1.2-.2 1.6l4.8 1.5 1.8 5.6c.2.6.4.8.9.8.4 0 .6-.2.9-.5l2.3-2.2 4.7 3.5c.9.5 1.5.2 1.7-.8l3.1-14.6c.3-1.3-.5-1.9-1.4-1.5ZM9.8 14.6l-.4 4 -1.4-4.6 10.3-6.5-8.5 7.1Z" /></svg>
          Invite via Telegram</a>
        <button type="button" className={`crew-copy${copied === "ok" ? " is-ok" : ""}`}
                onClick={async (e) => {
                  const b = e.currentTarget;
                  const ok = await copy(c.link);
                  setCopied(ok ? "ok" : "fail");
                  if (ok) { pop(b, 1.05); playSound("coin"); }
                  window.setTimeout(() => setCopied("idle"), 1800);
                }}>{copied === "ok" ? "Copied 🥝" : "Copy invite link"}</button>
      </div>
      {copied === "fail" && <p className="pp-problem" role="alert">Couldn't copy. Long-press to copy: <span className="crew-raw">{c.link}</span></p>}
      <dl className="crew-stats" aria-label="Your crew">
        <div><dt>Friends joined</dt><dd>{c.verified}</dd></div>
        <div><dt>XP earned</dt><dd className="site-green">+{c.xp_earned}</dd></div>
        <div><dt>Waiting</dt><dd>{c.pending}</dd></div>
      </dl>
      {c.bonus_xp > 0 && (
        <div className="crew-progress">
          <div className="crew-progress-row"><b>{Math.min(c.verified, c.bonus_at)} / {c.bonus_at} friends joined</b>
            <span>{c.bonus_paid ? `Bonus +${c.bonus_xp} XP earned ✓` : `${toBonus} more to +${c.bonus_xp} bonus XP`}</span></div>
          <div className="crew-bar" role="progressbar" aria-label="Friends towards the crew bonus" aria-valuemin={0} aria-valuemax={c.bonus_at}
               aria-valuenow={Math.min(c.verified, c.bonus_at)}><i style={{ ["--p" as string]: Math.min(1, c.verified / c.bonus_at) }} /></div>
        </div>
      )}
      <ul className="crew-miles" aria-label="Crew milestones">
        {c.milestones.map((m) => <li key={m.name} className={m.done ? "is-done" : m === next ? "is-next" : ""}>{m.done ? "✓ " : ""}{m.name} <small>{m.at}</small></li>)}
      </ul>
      <p className="crew-fine">A friend counts once they open your invite, join the Price Chat Telegram as a new member and are still there after
        {" "}{c.hold_hours} hours{c.joined_waiting > 0 ? ` (${c.joined_waiting} joined, being checked)` : ""}. Sharing or copying earns nothing; each Telegram
        account counts once; existing members and your own account don't count. <button type="button" className="site-textbutton" onClick={() => setWeb(true)}>
        Website invite link</button></p>
      {web && <InviteModal onClose={() => setWeb(false)} />}
    </section>
  );
}

/** A compact VIP Trading Bot status: locked (with the rank to reach), qualified, or unlocked. Never a big banner. */
export function VipStatus({ level }: { level: number }) {
  const [s, setS] = useState<{ rules: { min_rank_level: number }; rank?: { ok: boolean }; flow?: { first_trade: boolean }; access?: { bot: boolean } } | null>(null);
  useEffect(() => { void memberApi<NonNullable<typeof s>>("/members/trading-bot").then(setS).catch(() => undefined); }, []);
  if (!s) return null;
  const min = s.rules.min_rank_level;
  const state = s.flow?.first_trade || s.access?.bot ? "open" : (s.rank?.ok ?? level >= min) ? "ready" : "locked";
  return (
    <a className={`pp-vip is-${state}`} href="/earn" aria-label="VIP Trading Bot">
      <span className="pp-vip-icon" aria-hidden>{state === "locked" ? "🔒" : "✓"}</span>
      <span className="pp-vip-text"><b>VIP Trading Bot</b>
        <small>{state === "open" ? "Unlocked: open your trading terminal" : state === "ready" ? "You qualify: finish the setup" : `Unlocks at Kiwi Rank ${min}${min === 4 ? " (Scout Kiwi)" : ""}`}</small></span>
      <span className="pp-vip-go">{state === "open" ? "Open →" : state === "ready" ? "Continue →" : "View requirements →"}</span>
    </a>
  );
}
