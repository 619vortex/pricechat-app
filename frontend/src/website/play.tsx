/**
 * Kiwi Passport play area: one loop, ONE action → one click → reward → next action.
 *
 *   Kiwi + rank · XP bar ("18 XP to Kiwi Chick") · ONE active mission with one big button · "2 / 5 today".
 *
 * Honest by construction: every reward shown here is XP the server already recorded (the score went up after a reload,
 * or a new ledger event arrived). Nothing is assumed, nothing is awarded in the browser, and actions on X stay at
 * 0 XP (X's developer policy). Animations answer the member's own clicks; the resting state is calm.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ConnectButton, PIcon, TelegramButton } from "./community";
import { KiwiRig, useKiwiIdle } from "./KiwiRig";
import { memberApi, refreshSession, type MissionView, type Passport, type Rank, type XActionResult, type XPEvent, type XPRuleInfo } from "./members";
import { RankKiwi } from "./RankKiwi";
import { RANK_ASSETS } from "./rankAssets";
import { site } from "./websiteData";
import { AuraGlow, decoKey, HoloSheen, RankGear, RankStage } from "./rankDeco";
import { reducedMotion, useCountUp } from "./motion";
import { burst, flash, flyXP, onScreen, pop, xpDock } from "./fx";
import { playSound } from "./sound";
import { InviteModal } from "./invite";
import { TelegramBar } from "./tgbar";
import { StreakCard } from "./streak";
import type { Rewards } from "./rewards";
import "./play.css";

// --- timing (ms) --------------------------------------------------------------------------------------------------

const SUCCESS_HOLD = 850; // a finished mission shows ✓ this long, then the next one slides in
const CARD_EXIT = 220;
const LEVEL_UP_DELAY = 450; // bar full → level-up moment

// --- the mission queue ----------------------------------------------------------------------------------------------

/** Not a card here. Connecting accounts (X, Telegram, Google) lives in "Connected accounts" below, never in the main
 *  card: a connect step there sent people round in a loop. Tasks that first need an account connected are left out too,
 *  so the main card only ever offers something you can do right now. The latest video plays in Watch & Earn below. */
const NOT_IN_PLAY = new Set(["link_x", "link_telegram", "link_google", "open_short",
  // community / X tasks happen elsewhere and can't be confirmed on the spot: in the main card they came back round and round
  "telegram_join_channel", "telegram_participation", "telegram_discussion", "weekly_active_days", "x_conversation", "x_like", "x_reply", "x_repost"]);
const needsConnecting = (m: MissionView) => m.cta?.action === "link" || m.status === "unavailable";

/** Everything the member can act on now, easiest first: XP tasks, then support-only ones. */
/** Scroll to an easy task section (Watch & Earn / Read & Earn) below the main card. */
function goTo(id: string): void {
  document.getElementById(id)?.closest("section")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

export function missionQueue(p: Passport): MissionView[] {
  const pool = [...(p.today ?? []), ...(p.more_missions ?? [])]
    .filter((m) => (m.status === "available" || m.status === "in_progress") && !NOT_IN_PLAY.has(m.kind) && !needsConnecting(m));
  const weight = (m: MissionView) => (m.earns_xp === false ? 100 : 10);
  return pool.map((m, i) => ({ m, i })).sort((a, b) => weight(a.m) - weight(b.m) || a.i - b.i).map((x) => x.m);
}

function sameLocalDay(iso: string, now = new Date()): boolean {
  const d = new Date(iso);
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
}

/** "2 / 5 today": missions finished today (the automatic check-in included) out of those plus what's still open. */
export function todayCount(p: Passport, queue: MissionView[]): { done: number; total: number } {
  const done = (p.missions ?? []).filter((m) => m.status === "completed" && m.completed_at && sameLocalDay(m.completed_at)).length;
  return { done, total: done + queue.length };
}

// --- XP the member hasn't seen arrive yet (e.g. the daily check-in, a just-linked account) -------------------------

const SEEN_KEY = "pc-xp-seen";
const FIRST_VISIT_WINDOW_MS = 10 * 60 * 1000;

function unseen(recent: XPEvent[]): XPEvent[] {
  let last: number | null = null;
  try {
    const v = localStorage.getItem(SEEN_KEY);
    last = v ? Number(v) : null;
  } catch {
    /* no storage: treat like a first visit */
  }
  const fresh = (e: XPEvent) => e.xp > 0 && !e.reversed && !e.is_reversal;
  if (last !== null && Number.isFinite(last)) return recent.filter((e) => e.id > last! && fresh(e));
  return recent.filter((e) => fresh(e) && Date.now() - new Date(e.occurred_at).getTime() < FIRST_VISIT_WINDOW_MS);
}

function markSeen(recent: XPEvent[]): void {
  if (!recent.length) return;
  try {
    const max = Math.max(...recent.map((e) => e.id));
    if (max > Number(localStorage.getItem(SEEN_KEY) ?? 0)) localStorage.setItem(SEEN_KEY, String(max));
  } catch {
    /* a convenience only */
  }
}

// --- the Kiwi -------------------------------------------------------------------------------------------------------

export type Reaction = { kind: "happy" | "excited" | "charge"; key: number } | null;

const wing = (d: number, s: number, y = 0) => `perspective(320px) rotateY(${y}deg) rotate(${d}deg) scale(${s})`;

/** The member's Kiwi at their rank, alive: blinks, glances and the odd head tilt at rest (the site's Kiwi rig, never
 *  redrawn); hops and flaps when something good happens; glows as the next rank gets close. */
export function PlayKiwi({ rank, size, reaction = null, charge = 0, label, worker = false }: {
  rank: Rank; size: number; reaction?: Reaction; charge?: number; label?: string; worker?: boolean;
}) {
  const root = useRef<HTMLSpanElement>(null);
  const art = rank.art as Rank["art"] & { asset?: string | null };
  const official = art.asset ?? RANK_ASSETS[rank.key] ?? null;
  const rig = !worker && !official && art.treatment !== "egg";  // a Worker wears gear drawn for the Kiwi image
  const k = decoKey(rank.key, art.treatment);
  useKiwiIdle(root);

  useEffect(() => {
    const el = root.current;
    const fig = el?.querySelector<HTMLElement>(".pk-figure");
    if (!reaction || !el || !fig || typeof fig.animate !== "function" || reducedMotion()) return;
    const anims: Animation[] = [];
    const run = (sel: string, frames: Keyframe[], o: KeyframeAnimationOptions) => {
      const t = el.querySelector<HTMLElement>(sel);
      if (t) anims.push(t.animate(frames, o));
    };
    if (reaction.kind === "charge") {
      anims.push(fig.animate([0, 1, -1, 1, -1, 1, 0].map((s) => ({ transform: `translateX(${s * 1.5}%)` })), { duration: 420 }));
    } else {
      const big = reaction.kind === "excited";
      const h = big ? 16 : 9;
      anims.push(fig.animate([
        { transform: "translateY(0) scale(1, 1) rotate(0)" },
        { transform: "translateY(2%) scale(1.06, 0.94) rotate(0)", offset: 0.14 },
        { transform: `translateY(-${h}%) scale(0.96, 1.05) rotate(${big ? -4 : 0}deg)`, offset: 0.45 },
        { transform: `translateY(-${h * 0.6}%) scale(1, 1) rotate(${big ? 3 : 0}deg)`, offset: 0.6 },
        { transform: "translateY(0) scale(1.05, 0.95) rotate(0)", offset: 0.8 },
        { transform: "translateY(0) scale(1, 1) rotate(0)" },
      ], { duration: big ? 720 : 520, easing: "cubic-bezier(.3,.7,.4,1)" }));
      const beats = big ? 5 : 3;
      for (const [sel, sz, lag] of [[".kr-wing-near", 1, 0], [".kr-wing-far", 0.86, 25]] as const) {
        const f: Keyframe[] = [{ transform: wing(0, 0) }];
        for (let i = 0; i < beats; i++) f.push({ transform: wing(-35, sz) }, { transform: wing(25, sz, 45) });
        f.push({ transform: wing(0, 0) });
        run(sel, f, { duration: beats * 120 + 120, delay: 90 + lag, easing: "ease-in-out" });
      }
      if (big) {
        run(".kr-leg-l", [{ transform: "rotate(0)" }, { transform: "rotate(-14deg)" }, { transform: "rotate(0)" }], { duration: 360, delay: 200 });
        run(".kr-leg-r", [{ transform: "rotate(0)" }, { transform: "rotate(12deg)" }, { transform: "rotate(0)" }], { duration: 360, delay: 240 });
      }
    }
    return () => anims.forEach((a) => a.cancel());
  }, [reaction]);

  const a11y = label ? { role: "img", "aria-label": label } : { "aria-hidden": true };
  return (
    <span ref={root} className={`pk rk rk-${art.treatment} rkd-${k}${rig ? "" : " is-flat"}${charge >= 0.8 ? " is-charged" : ""}`}
          style={{ width: size, height: size, "--charge": Math.min(1, Math.max(0, charge)).toFixed(2) } as React.CSSProperties} {...a11y}>
      {rig && <RankStage k={k} layer="back" small={false} />}
      <span className="pk-figure">
        <span className="pk-breath pc-idle-kiwi">
          {rig && (k === "legend" || k === "mythic") && <AuraGlow src={site.kiwi.src} k={k} />}
          {rig && <RankGear k={k} layer="back" />}
          {rig ? <KiwiRig variant="story" /> : <RankKiwi art={art} rankKey={rank.key} size={size} worker={worker} />}
          {rig && k === "mythic" && <HoloSheen src={site.kiwi.src} />}
          {rig && <RankGear k={k} layer="front" />}
        </span>
      </span>
      {rig && art.overlay ? <img className="rk-overlay" src={art.overlay} alt="" decoding="async" /> : null}
      {rig && <RankStage k={k} layer="front" small={false} />}
    </span>
  );
}

// --- the play area ----------------------------------------------------------------------------------------------------

function scoreOf(p: Passport): number {
  return p.rank.score ?? p.member.xp;
}

type Toast = { text: string; key: number } | null;
type Handled = "opened" | "sent" | "skipped" | "completed";

export function PassportPlay({ p, ranks, reload, extra, rewardFrom, onEditName, rules = [] }: {
  p: Passport; ranks: Rank[]; reload: () => Promise<Passport | null>; extra?: React.ReactNode; onEditName?: () => void;
  /** The XP rules (the real configured rewards shown on the quick XP cards). */
  rules?: XPRuleInfo[];
  /** Set by another part of the Passport (Watch & Earn) just before its reload: the next XP flies from there, already sparked. */
  rewardFrom?: React.MutableRefObject<Element | null>;
}) {
  const score = scoreOf(p);
  const [shown, setShown] = useState({ score, progress: p.rank.progress, rank: p.rank.current });
  const [reaction, setReaction] = useState<Reaction>(null);
  const [toast, setToast] = useState<Toast>(null);
  const [moved, setMoved] = useState<{ to: number; key: number } | null>(null);
  const [invite, setInvite] = useState(false);
  const [levelUp, setLevelUp] = useState<{ to: Rank; id: number } | null>(null);
  const [handled, setHandled] = useState<Map<number, Handled>>(new Map());
  const [skipOrder, setSkipOrder] = useState<number[]>([]);
  const [pinned, setPinned] = useState<MissionView | null>(null); // the mission being played stays until its card has left
  const prev = useRef<{ score: number; level: number; pos: number | null } | null>(null);
  const origin = useRef<Element | null>(null);
  const kiwiRef = useRef<HTMLDivElement>(null);
  const barRef = useRef<HTMLDivElement>(null);
  const numRef = useRef<HTMLParagraphElement>(null);
  const toastRef = useRef<HTMLParagraphElement>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const [barKey, setBarKey] = useState(0);
  const shownScore = useCountUp(shown.score, 450);

  const react = (kind: "happy" | "excited" | "charge") => setReaction({ kind, key: Date.now() + Math.random() });

  /** Real XP arrived: spark at the source, the chip flies to the bar (or, when the bar is scrolled away, to a small badge at
   *  the top right: the page never scrolls by itself), the number counts up, the Kiwi reacts if it's on screen. */
  const celebrate = useCallback(async (amount: number, from: Element | null, next: Passport, sparked = false) => {
    const src = from && from.isConnected ? from : kiwiRef.current;
    if (src && !sparked) burst(src, 10, 60);
    if (src && barRef.current && onScreen(barRef.current)) await flyXP(src, barRef.current, amount);
    else if (src && onScreen(src)) {
      const dock = xpDock(scoreOf(next) - amount);
      await flyXP(src, dock.el, amount);
      dock.land(scoreOf(next));
    }
    pop(numRef.current);
    flash(numRef.current);
    pop(barRef.current, 1.04);
    const up = next.rank_up;
    if (up) {
      setShown((s) => ({ ...s, score: scoreOf(next), progress: 1 }));
      react("charge");
      window.setTimeout(() => setLevelUp({ to: next.rank.current, id: up.id }), reducedMotion() ? 0 : LEVEL_UP_DELAY);
    } else {
      setShown({ score: scoreOf(next), progress: next.rank.progress, rank: next.rank.current });
      if (onScreen(kiwiRef.current)) react(amount >= 10 ? "excited" : "happy"); // a small hop: the Kiwi noticed
    }
  }, []);

  useEffect(() => {
    const before = prev.current;
    const pos = p.standing?.position ?? null;
    prev.current = { score, level: p.rank.current.level, pos };
    if (!before) {
      // first paint: XP that arrived while the member was away (the daily check-in, a just-connected account…)
      const fresh = unseen(p.recent);
      markSeen(p.recent);
      const sum = fresh.reduce((t, e) => t + e.xp, 0);
      if (sum > 0 && !reducedMotion()) {
        const label = fresh.length === 1 ? fresh[0].reason : `${fresh.length} rewards since your last visit`;
        setShown((s) => ({ ...s, score: Math.max(0, score - sum) }));
        window.setTimeout(() => alive.current && setToast({ text: `+${sum} XP · ${label}`, key: Date.now() }), 450);
        window.setTimeout(() => alive.current && void celebrate(sum, toastRef.current, p), 1150);
        return;
      }
      if (sum > 0) setToast({ text: `+${sum} XP · ${fresh.length === 1 ? fresh[0].reason : `${fresh.length} rewards since your last visit`}`, key: Date.now() });
      if (p.rank_up) setLevelUp({ to: p.rank.current, id: p.rank_up.id });
      return;
    }
    markSeen(p.recent);
    if (pos !== null && before.pos !== null && pos < before.pos) {
      // the server says the member moved up the all-time board: shown once the XP has landed
      window.setTimeout(() => alive.current && setMoved({ to: pos, key: Date.now() }), reducedMotion() || score <= before.score ? 0 : 700);
    }
    if (score > before.score) {
      const ext = rewardFrom?.current ?? null;
      if (rewardFrom) rewardFrom.current = null;
      void celebrate(score - before.score, ext ?? origin.current, p, !!ext);
    }
    else if (!levelUp && (score !== before.score || p.rank.current.level !== before.level))
      setShown({ score, progress: p.rank.progress, rank: p.rank.current });
  }, [p]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!moved) return;
    const t = window.setTimeout(() => setMoved(null), 2800);
    return () => window.clearTimeout(t);
  }, [moved]);

  useEffect(() => {
    if (!toast) return;
    const t = window.setTimeout(() => setToast(null), 2600);
    return () => window.clearTimeout(t);
  }, [toast]);

  async function closeLevelUp() {
    const id = levelUp?.id;
    setLevelUp(null);
    setShown({ score: scoreOf(p), progress: 0, rank: p.rank.current });
    setBarKey((k) => k + 1); // a new, empty fill: no drain from full
    window.requestAnimationFrame(() => window.requestAnimationFrame(() => setShown((s) => ({ ...s, progress: p.rank.progress }))));
    react("excited");
    if (id != null) {
      try {
        await memberApi("/members/me/notifications/read", { method: "POST", body: JSON.stringify({ ids: [id] }) });
        await reload();
      } catch {
        /* shown once either way */
      }
    }
  }

  // the queue: what's left, skipped ones at the back
  const queue = useMemo(() => missionQueue(p), [p]);
  const fresh = queue.filter((m) => !handled.has(m.id));
  const skipped = skipOrder.map((id) => queue.find((m) => m.id === id)).filter((m): m is MissionView => !!m && handled.get(m.id) === "skipped");
  const candidates = [...fresh, ...skipped];
  const active = pinned ? (p.missions ?? []).find((m) => m.id === pinned.id) ?? pinned : candidates[0] ?? null;
  const count = todayCount(p, queue);
  const waiting = queue.some((m) => handled.get(m.id) === "opened");

  const finish = (m: MissionView, how: Handled) => {
    setPinned(null);
    setHandled((h) => new Map(h).set(m.id, how));
    if (how === "skipped") setSkipOrder((o) => [...o.filter((id) => id !== m.id), m.id]);
  };

  const { next } = p.rank;
  // what the bar is showing right now: while new XP is still flying in (or a level-up waits), the old rank's target
  const settled = shown.rank.level === p.rank.current.level;
  const shownNext = settled ? next : ranks.find((r) => r.level === shown.rank.level + 1) ?? next;
  const nextMin = shownNext?.min_xp ?? null;
  const toNext = settled ? p.rank.xp_to_next : Math.max(0, (nextMin ?? 0) - shownScore);
  // the bar shows progress WITHIN the current rank (from its threshold to the next one), and says so
  const curMin = shown.rank.min_xp ?? 0;
  const inRank = Math.max(0, shownScore - curMin);
  const span = nextMin !== null ? Math.max(1, nextMin - curMin) : null;
  const levelsUp = (m: MissionView) => !!next && m.earns_xp !== false && m.xp > 0 && m.xp >= p.rank.xp_to_next;

  return (
    <section className="pp-play pp-a" aria-label="Kiwi Passport">
      <div className="pp-hero">
        <div className="pp-kiwi" ref={kiwiRef}>
          <PlayKiwi rank={shown.rank} size={200} reaction={reaction} charge={next ? shown.progress : 0} label={shown.rank.name}
                    worker={(p.roles ?? []).includes("WORKER")} />
          {toast && <p key={toast.key} ref={toastRef} className="pp-toast" role="status">{toast.text}</p>}
        </div>
        <div className="pp-id">
          <h1 className="pp-user">{p.member.username}
            {onEditName && <button type="button" className="pp-user-edit" onClick={onEditName} aria-label="Edit your username" title="Change your name">
              <svg viewBox="0 0 16 16" aria-hidden><path d="M11.3 2.3a1.6 1.6 0 0 1 2.3 2.3L5.4 12.8 2.5 13.5l.7-2.9z" /></svg></button>}
          </h1>
          <p className="pp-rank-name">{shown.rank.name}</p>
          <p className="pp-xp-big" ref={numRef}>{shownScore.toLocaleString("en-GB")} <span>XP</span></p>
          {moved && <p key={moved.key} className="pp-moved" role="status">↑ You moved to #{moved.to}</p>}
          {extra}
        </div>
        <div className="pp-bar">
          <div className={`pp-bar-track${next && shown.progress >= 0.8 ? " is-near" : ""}`} ref={barRef} role="progressbar"
               aria-label={shownNext ? `Progress within ${shown.rank.name} towards ${shownNext.name}` : "Highest rank reached"}
               aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(shown.progress * 100)}>
            <span key={barKey} className="pp-bar-fill" style={{ "--p": shown.progress } as React.CSSProperties} />
          </div>
          <div className="pp-bar-meta">
            <p className="pp-to-next">
              {shownNext ? <><strong>{toNext.toLocaleString("en-GB")} XP</strong> to {shownNext.name}</> : "The highest rank. Legendary."}
            </p>
            {span !== null && (
              <span className="pp-bar-of" title={`${shown.rank.name} starts at ${curMin.toLocaleString("en-GB")} XP, ${shownNext?.name} at ${nextMin?.toLocaleString("en-GB")} XP`}>
                {Math.min(inRank, span).toLocaleString("en-GB")} / {span.toLocaleString("en-GB")} XP this rank</span>
            )}
          </div>
        </div>
        {/* the daily streak: one slim row, right with the XP (no section of its own) */}
        {p.streak && <StreakCard s={p.streak} />}
        {/* with the profile: where you stand, and bringing friends */}
        <nav className="pp-links" aria-label="Leaderboard and invites">
          <a className="pp-link" href="/website/leaderboard">🏆 Leaderboard{p.standing ? <span className="pp-link-pos"> #{p.standing.position}</span> : null}</a>
          <button type="button" className="pp-link" onClick={() => setInvite(true)}>👥 Invite friends</button>
        </nav>
      </div>

      <div className="pp-stage">
        {p.member.xp_suspended && <p className="site-error">XP earning is paused on this account while the team reviews it.</p>}
        {active ? (
          <ActiveMission key={active.id} m={active} reload={reload} levelsUp={levelsUp(active)} canSkip={candidates.length > 1}
                         onOrigin={(el) => {
                           origin.current = el;
                           setPinned(active);
                         }} onFinish={(how) => finish(active, how)} />
        ) : null}
        {active && <ul className="pp-quick" aria-label="Telegram chat"><li><TelegramBar t={p.telegram} row /></li></ul>}
        {active ? null : (
          <div className="pp-mcard pp-mcard-done">
            {/* never "all caught up" next to missions still to do: the daily status and the extra XP are two separate things */}
            {count.total > 0 && count.done >= count.total ? <p className="pp-mcard-title">Daily missions complete ✓</p>
              : count.total > 0 ? <p className="pp-mcard-title">Your missions are in progress</p> : null}
            {count.total > 0 && count.done < count.total && <p className="pp-mcard-sub">Opened missions count by themselves when they happen.</p>}
            <h3 className="pp-easy-title">Keep earning XP</h3>
            <QuickXP p={p} rules={rules} />
            {waiting && count.total === 0 && <p className="pp-mcard-sub">Opened missions count by themselves when they happen.</p>}
          </div>
        )}
        {count.total > 0 && (
          <p className="pp-count" aria-label={`${count.done} of ${count.total} done today`}>
            {count.total <= 8 && (
              <span className="pp-dots" aria-hidden>
                {Array.from({ length: count.total }, (_, i) => <span key={i} className={i < count.done ? "is-on" : ""} />)}
              </span>
            )}
            {count.done} / {count.total} today
          </p>
        )}
      </div>
      {invite && <InviteModal onClose={() => setInvite(false)} />}

      {levelUp && <LevelUp from={ranks.find((r) => r.level === levelUp.to.level - 1) ?? null} to={levelUp.to} entitlements={p.entitlements} onClose={() => void closeLevelUp()} />}
    </section>
  );
}

// --- quick XP: consistent mission cards with the real configured rewards ------------------------------------------------

const QUICK: { key: string; icon: string; title: string; rule: string; to: string; extra?: string }[] = [
  { key: "watch", icon: "▶", title: "Watch a short", rule: "website.content_view", to: "we-title", extra: "watch.combo" },
  { key: "read", icon: "📖", title: "Read a post", rule: "website.x_post_read", to: "xf-title" },
  { key: "pack", icon: "🃏", title: "Open a pack", rule: "pack.open", to: "/giveaway" },
];

function QuickXP({ p, rules }: { p: Passport; rules: XPRuleInfo[] }) {
  const today = new Date().toDateString();
  return (
    <ul className="pp-quick" aria-label="More ways to earn XP">
      {QUICK.map((q) => {
        const rule = rules.find((r) => r.event_type === q.rule);
        const combo = q.extra ? rules.find((r) => r.event_type === q.extra && r.enabled && (r.daily_cap ?? 0) > 0) : undefined;
        const paid = p.recent.filter((e) => e.event_type === q.rule && e.xp > 0 && new Date(e.occurred_at).toDateString() === today).length;
        const cap = rule && rule.xp > 0 && rule.daily_cap ? Math.floor(rule.daily_cap / rule.xp) : null;
        const done = cap !== null && paid >= cap;
        const reward = rule && rule.enabled && rule.xp > 0 ? `+${rule.xp} XP` : "XP";
        const sub = done ? "Done for today ✓" : cap !== null ? `${paid} / ${cap} today${combo ? " · combo bonus" : ""}` : combo ? "+ combo bonus" : "Any time";
        const inner = (
          <>
            <span className="pp-quick-icon" aria-hidden>{q.icon}</span>
            <span className="pp-quick-text"><b>{q.title}</b><small>{sub}</small></span>
            <span className={`pp-quick-xp${done ? " is-done" : ""}`}>{done ? "✓" : reward}</span>
            <span className="pp-quick-go" aria-hidden>→</span>
          </>
        );
        return (
          <li key={q.key}>
            {q.to.startsWith("/") ? <a className={`pp-quick-card${done ? " is-done" : ""}`} href={q.to}>{inner}</a>
              : <button type="button" className={`pp-quick-card${done ? " is-done" : ""}`} onClick={() => goTo(q.to)}>{inner}</button>}
          </li>
        );
      })}
      {/* Telegram chat: a mission row like the others (message counter, today's chat XP against the cap) */}
      <li><TelegramBar t={p.telegram} row /></li>
    </ul>
  );
}

// --- one mission -------------------------------------------------------------------------------------------------------

function ago(iso: string | null | undefined): string {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

const KIND_ICON: Record<string, string> = { x_reply: "💬", x_conversation: "💬", x_repost: "🔁", x_like: "❤️", open_short: "▶", telegram_participation: "🔥",
                                            telegram_join_channel: "📣", telegram_discussion: "💬",
                                            weekly_active_days: "🔥", weekly_content: "▶" };

const CHECK = <svg className="pp-go-check" viewBox="0 0 16 16" aria-hidden><path d="M3 8.5l3.2 3.2L13 4.8" /></svg>;

function ActiveMission({ m, reload, levelsUp, canSkip, onOrigin, onFinish }: {
  m: MissionView; reload: () => Promise<Passport | null>; levelsUp: boolean; canSkip: boolean;
  onOrigin: (el: Element | null) => void; onFinish: (how: Handled) => void;
}) {
  const [phase, setPhase] = useState<"idle" | "busy" | "success" | "leaving">("idle");
  const [done, setDone] = useState<{ text: string; sub?: string; xp?: number } | null>(null);
  const [mode, setMode] = useState<"none" | "compose" | "confirm" | "verify">("none");
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<{ message: string; reconnect: boolean } | null>(null);
  const card = useRef<HTMLElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const actionArea = useRef<HTMLDivElement>(null);
  const timers = useRef<number[]>([]);
  useEffect(() => () => timers.current.forEach((t) => window.clearTimeout(t)), []);
  const later = (fn: () => void, ms: number) => timers.current.push(window.setTimeout(fn, ms));

  const t = m.target;
  const support = m.earns_xp === false;
  const linkProvider = m.cta?.action === "link" && (m.cta.platform === "x" || m.cta.platform === "google") ? m.cta.platform : null;

  /** Done: ✓ on the button, then the card slides out and the next one comes in. */
  function succeed(how: Handled, d: { text: string; sub?: string; xp?: number }) {
    setDone(d);
    setPhase("success");
    if (btn.current && !d.xp) burst(btn.current, 8, 46); // XP missions get their sparks from the XP itself
    later(() => setPhase("leaving"), SUCCESS_HOLD);
    later(() => onFinish(how), SUCCESS_HOLD + CARD_EXIT);
  }

  /** The member opened something (a link): the server records it; XP only if the server awarded it. */
  async function open() {
    onOrigin(actionArea.current ?? card.current);
    if (m.cta?.url) window.open(m.cta.url, "_blank", "noopener");
    setPhase("busy");
    let after: Passport | null = null;
    try {
      await memberApi(`/members/missions/${m.id}/open`, { method: "POST" });
      after = await reload();
      refreshSession();
    } catch {
      /* the link still opened / the video still plays */
    }
    const now = after?.missions?.find((x) => x.id === m.id);
    if (now?.status === "completed") succeed("completed", { text: "Done", xp: now.xp_awarded || undefined });
    else if (m.kind === "x_like") succeed("opened", { text: "Opened on X", sub: "Thanks for the support" });
    else if (m.kind === "telegram_discussion") succeed("opened", { text: "Opened", sub: "Comment in your own words: it counts automatically" });
    else succeed("opened", { text: "Opened", sub: m.goal > 1 ? "Counts by itself when it happens" : undefined });
  }

  /** "Join channel": the member joins on Telegram, then Verify asks Telegram (through our bot). XP only on a yes. */
  async function verifyJoin() {
    onOrigin(actionArea.current ?? card.current);
    setPhase("busy");
    setProblem(null);
    try {
      const r = await memberApi<{ ok: boolean; code: string; message: string; xp?: number }>(`/members/missions/${m.id}/verify`, { method: "POST" });
      if (r.ok) {
        await reload();
        refreshSession();
        succeed("completed", { text: "Verified", xp: r.xp || undefined });
      } else {
        setPhase("idle");
        setProblem({ message: r.message, reconnect: false });
      }
    } catch (e) {
      setPhase("idle");
      setProblem({ message: e instanceof Error ? e.message : "Couldn't check right now. Please try again.", reconnect: false });
    }
  }

  async function xAction(kind: "reply" | "repost") {
    onOrigin(actionArea.current ?? card.current);
    setPhase("busy");
    setProblem(null);
    try {
      const r = await memberApi<XActionResult>(`/members/me/x/${kind}`, {
        method: "POST", body: JSON.stringify({ post_id: m.cta?.post_id, ...(kind === "reply" ? { text } : {}) }),
      });
      if (r.ok) {
        setMode("none");
        succeed("sent", { text: r.message });
        void reload();
      } else {
        setPhase("idle");
        setProblem({ message: r.message, reconnect: r.reconnect });
      }
    } catch (e) {
      setPhase("idle");
      setProblem({ message: e instanceof Error ? e.message : "Something went wrong. Nothing was sent.", reconnect: false });
    }
  }

  const reward = support ? "No XP · support" : m.xp ? `+${m.xp} XP` : null;
  const icon = (m.platform === "website" ? "website" : m.platform) as "x" | "youtube" | "telegram" | "website";
  const busy = phase === "busy";

  let action: React.ReactNode = null;
  if (phase === "success" && done) {
    action = (
      <div className="pp-go pp-go-done" role="status">
        {CHECK} {done.text}{done.xp ? <strong> +{done.xp} XP</strong> : null}
      </div>
    );
  } else if (mode === "compose") {
    action = (
      <form className="pp-composer" onSubmit={(e) => { e.preventDefault(); void xAction("reply"); }}>
        <label htmlFor={`reply-${m.id}`} className="site-muted">Reply to @pricechat</label>
        <textarea id={`reply-${m.id}`} value={text} maxLength={280} rows={3} autoFocus onChange={(e) => setText(e.target.value)} placeholder="Your take…" />
        <div className="pp-composer-foot">
          <span className="site-muted">{280 - text.length}</span>
          <button type="button" className="site-textbutton" onClick={() => setMode("none")}>Cancel</button>
          <button type="submit" ref={btn} className="pp-go pp-go-sm" disabled={busy || !text.trim()}>{busy ? "Sending…" : "Send reply"}</button>
        </div>
      </form>
    );
  } else if (mode === "confirm") {
    action = (
      <div className="pp-confirm" role="group" aria-label="Confirm repost">
        <p>Repost this to your followers on X?</p>
        <div className="pp-composer-foot">
          <button type="button" className="site-textbutton" onClick={() => setMode("none")}>Cancel</button>
          <button type="button" ref={btn} className="pp-go pp-go-sm" disabled={busy} onClick={() => void xAction("repost")}>{busy ? "Reposting…" : "Yes, repost"}</button>
        </div>
      </div>
    );
  } else if (m.cta?.action === "telegram_join" && m.cta.url) {
    action = mode === "verify" ? (
      <button ref={btn} className={`pp-go${busy ? " is-busy" : ""}`} disabled={busy} onClick={() => void verifyJoin()}>{busy ? "Checking…" : "Verify"}</button>
    ) : (
      <button ref={btn} className="pp-go" onClick={() => {
        window.open(m.cta!.url, "_blank", "noopener");
        void memberApi(`/members/missions/${m.id}/open`, { method: "POST" }).catch(() => undefined);
        setMode("verify");
      }}>{m.cta.label}</button>
    );
  } else if (m.cta?.action === "open" && m.cta.url) {
    action = <button ref={btn} className={`pp-go${busy ? " is-busy" : ""}`} disabled={busy} onClick={() => void open()}>{m.cta.label}</button>;
  } else if (linkProvider) {
    action = <ConnectButton provider={linkProvider} label={m.cta!.label} className="pp-go" />;
  } else if (m.cta?.action === "link" && m.cta.platform === "telegram") {
    action = <TelegramButton purpose="link" available label={m.cta.label} onDone={() => location.assign("/passport?linked=telegram")} />;
  } else if (m.cta?.action === "x_repost") {
    action = <button ref={btn} className="pp-go" onClick={() => setMode("confirm")}>{m.cta.label}</button>;
  } else if (m.cta?.action === "x_reply") {
    action = <button ref={btn} className="pp-go" onClick={() => setMode("compose")}>{m.cta.label}</button>;
  }

  return (
    <article ref={card} id={`mission-${m.id}`} className={`pp-mcard is-${phase}`} aria-labelledby={`mission-${m.id}-title`}>
      <div className="pp-mcard-top">
        <span className="pp-mcard-icon" title={m.platform}>{KIND_ICON[m.kind] ? <span aria-hidden>{KIND_ICON[m.kind]}</span> : <PIcon p={icon} />}</span>
        {reward && (
          <span className={`pp-mcard-reward${support ? " is-zero" : ""}`}
                title={support ? "X doesn't allow apps to reward actions on X, so this one is just for support." : undefined}>
            {reward}
          </span>
        )}
      </div>
      <h2 className="pp-mcard-title" id={`mission-${m.id}-title`}>{m.title}</h2>
      {levelsUp && phase !== "success" && <p className="pp-mcard-lvl">⚡ This levels you up</p>}

      {t.text ? (
        <blockquote className="pp-mcard-quote">
          <span className="site-muted">
            {m.platform === "telegram" ? "Price Chat update" : `@pricechat${t.replied_to ? ` replying to @${t.replied_to}` : ""}`}{t.posted_at ? ` · ${ago(t.posted_at)}` : ""}
          </span>
          {t.text}
        </blockquote>
      ) : null}

      {m.goal > 1 && phase !== "success" && (
        <p className="pp-mcard-progress">
          <span className="pp-dots" aria-hidden>{Array.from({ length: Math.min(m.goal, 10) }, (_, i) => <span key={i} className={i < m.progress ? "is-on" : ""} />)}</span>
          <strong>{m.progress}</strong> / {m.goal}{m.kind === "telegram_participation" ? " messages today · counted automatically" : ""}
        </p>
      )}

      <div className="pp-mcard-action" ref={actionArea}>{action}</div>
      {done?.sub && phase === "success" && <p className="pp-mcard-sub">{done.sub}</p>}
      {mode === "verify" && phase === "idle" && !problem && <p className="pp-mcard-sub">Joined? Press Verify: Telegram confirms it to Price Chat.</p>}
      {problem && (
        <p className="pp-problem" role="alert">
          {problem.message}
          {problem.reconnect && <> <ConnectButton provider="x" label="Reconnect X" className="site-textbutton" /></>}
        </p>
      )}
      {canSkip && mode !== "compose" && mode !== "confirm" && (
        <button className={`pp-skip${phase === "idle" ? "" : " is-held"}`} disabled={phase !== "idle"} aria-hidden={phase !== "idle" || undefined}
                onClick={() => { setPhase("leaving"); later(() => onFinish("skipped"), CARD_EXIT); }}>Skip for now</button>
      )}
    </article>
  );
}

// --- level up ------------------------------------------------------------------------------------------------------------

/** The strongest moment, ~2s: the room darkens, energy builds round the old Kiwi, it bursts (an egg cracks), the new Kiwi
 *  lands with its new look and kit, LEVEL UP!, the rank, then "Next mission →". Esc or the button closes it. */
function LevelUp({ from, to, entitlements, onClose }: {
  from: Rank | null; to: Rank; entitlements: Passport["entitlements"]; onClose: () => void;
}) {
  const stage = useRef<HTMLDivElement>(null);
  const [reaction, setReaction] = useState<Reaction>(null);
  const unlocked = entitlements.filter((e) => e.unlocked && e.required_rank === to.level);
  // the rank's own reward (Rank Rewards), named by the server; the older entitlement names only when there's none
  const [reward, setReward] = useState<string | null>(null);
  useEffect(() => {
    void memberApi<Rewards>("/members/rewards").then((r) => {
      const c = r.ranks.find((x) => x.rank.key === to.key);
      if (c?.unlocked) setReward(c.reward.title);
    }).catch(() => undefined);
  }, [to.key]);
  const close = useRef(onClose);
  close.current = onClose; // the parent re-renders while XP counts up: the sequence below must run once, not restart
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && close.current();
    document.addEventListener("keydown", k);
    const t1 = window.setTimeout(() => {
      if (stage.current) burst(stage.current, 22, 150);
      playSound("levelup"); // only after the member has interacted, and only with sound on
    }, reducedMotion() ? 0 : 900);
    const t2 = window.setTimeout(() => setReaction({ kind: "excited", key: 1 }), reducedMotion() ? 0 : 1050);
    return () => {
      document.removeEventListener("keydown", k);
      window.clearTimeout(t1);
      window.clearTimeout(t2);
    };
  }, []);
  const egg = from?.art.treatment === "egg";
  return (
    <div className="lu" role="dialog" aria-modal="true" aria-labelledby="rankup-title">
      <div className={`lu-card lu-to-${to.art.treatment}`}>
        <div className="lu-stage" ref={stage}>
          <span className="lu-energy" />
          {from && (
            <span className={`lu-old${egg ? " is-egg" : ""}`}>
              {egg ? (
                <span className="lu-egg" aria-hidden>
                  <span className="lu-shell lu-shell-l" />
                  <span className="lu-shell lu-shell-r" />
                </span>
              ) : (
                <PlayKiwi rank={from} size={170} />
              )}
            </span>
          )}
          <span className="lu-flash" />
          <span className="lu-sweep" aria-hidden />
          <span className="lu-new"><PlayKiwi rank={to} size={190} reaction={reaction} label={to.name} /></span>
        </div>
        <p className="lu-kicker">Level up!</p>
        <h2 className="site-display lu-title" id="rankup-title">{to.name}</h2>
        <p className="lu-badge">Rank {to.level}</p>
        {reward ? <a className="lu-unlocks" href={`/passport#reward-${to.key}`} onClick={onClose}>Unlocked: <b>{reward}</b> →</a>
          : unlocked.length > 0 && <p className="lu-unlocks">Unlocked: {unlocked.map((e) => e.name).join(", ")}</p>}
        <a className="lu-pack" href="/giveaway">🎁 +1 Level-up pack: holo guaranteed. <b>Open it →</b></a>
        <button className="pp-go lu-next" onClick={onClose} autoFocus>Next mission →</button>
      </div>
    </div>
  );
}
