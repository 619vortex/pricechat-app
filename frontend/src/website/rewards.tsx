import { useCallback, useEffect, useRef, useState } from "react";
import { memberApi, type Rank } from "./members";
import { RankKiwi } from "./RankKiwi";
import { SitePage } from "./community";
import { pageHead } from "./seo";

/**
 * Kiwi Passport rank rewards: every rank as a milestone (artwork, XP needed, the reward, locked / unlocked / claim), and the
 * Kiwi Chick lessons page. Everything shown here is decided by the server (/api/members/rewards): a button the browser
 * shows can't unlock anything the server refuses.
 */

export type RewardKind = "none" | "content" | "profile" | "earn" | "arena" | "trading_credit" | "vip" | "tba";
export interface Claim { id: string; status: "PENDING" | "APPROVED" | "FULFILLED" | "REJECTED" | "REVOKED"; created_at: string; reviewed_at: string | null }
export interface RewardCard {
  rank: Rank;
  reached: boolean;
  current: boolean;
  unlocked: boolean;
  reward: { kind: RewardKind; title: string; description: string; enabled: boolean };
  xp_to_reach: number;
  progress: number;
  next_min_xp: number | null;
  claim?: Claim | null;
}
export interface Knight { active: boolean; status: string; amount_usd: number; terms: string; regions: string; expires_at: string | null }
export interface ProfileReward { display_name: string | null; accent: string | null; accents: string[]; next_rename_at: string | null }
export interface Rewards { ranks: RewardCard[]; level: number; score: number; knight: Knight; profile?: ProfileReward }

const fmt = (n: number) => n.toLocaleString("en-GB");
const day = (iso: string) => new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
const CLAIM_LABEL: Record<Claim["status"], string> = {
  PENDING: "Requested · waiting for review", APPROVED: "Approved", FULFILLED: "Completed ✓", REJECTED: "Not approved", REVOKED: "Access removed",
};
const ACCENT_LABEL: Record<string, string> = { green: "Kiwi green", gold: "Gold", ice: "Ice", ember: "Ember", violet: "Violet" };

export function useRewards() {
  const [data, setData] = useState<Rewards | null>(null);
  const load = useCallback(async () => {
    try { setData(await memberApi<Rewards>("/members/rewards")); } catch { /* the rest of the Passport still works */ }
  }, []);
  useEffect(() => { void load(); }, [load]);
  return { data, reload: load };
}

type State = "claimable" | "unlocked" | "done" | "locked" | "tba" | "off";

function stateOf(c: RewardCard, knight: Knight): State {
  const k = c.reward.kind;
  if (k === "tba") return "tba";
  if (!c.reward.enabled) return "off";
  if (!c.reached) return "locked";
  if (k === "none") return "done";
  if (k === "trading_credit") return c.claim ? (c.claim.status === "FULFILLED" ? "done" : "unlocked") : knight.active ? "claimable" : "unlocked";
  if (k === "vip") return c.claim ? "unlocked" : "claimable";
  return c.unlocked ? "unlocked" : "locked";
}

const STATE_LABEL: Record<State, string> = { claimable: "Claim now", unlocked: "Unlocked", done: "Completed", locked: "Locked", tba: "Coming later", off: "Paused" };

/** The Passport's rank section, compact: one horizontal row of the 10 Kiwi ranks (current highlighted, swipeable on phones)
 *  and the progress to the next rank right under it. Tap a Kiwi to see its reward in a small popover (a bottom sheet on
 *  phones), with its action when there is one. Outside click or Escape closes it. */
export function RankRewards({ data, reload, onProfileSaved }: { data: Rewards; reload: () => Promise<void>; onProfileSaved?: () => void }) {
  const cards = data.ranks;
  const current = cards.find((c) => c.current) ?? cards[0];
  const next = cards.find((c) => !c.reached);
  const states = cards.map((c) => stateOf(c, data.knight));
  const [open, setOpen] = useState<string | null>(() => {
    const k = typeof location !== "undefined" ? location.hash.match(/^#reward-([a-z]+)$/)?.[1] : undefined;  // links like /passport#reward-warrior
    return k && cards.some((c) => c.rank.key === k) ? k : null;
  });
  const root = useRef<HTMLDivElement>(null);
  const line = useRef<HTMLOListElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const [at, setAt] = useState<{ left: number; top: number; arrow: number } | null>(null);

  useEffect(() => {  // the row starts at your rank (or the one a link opened), never moving the page itself
    const el = line.current, cur = el?.querySelector<HTMLElement>(open ? `#reward-${open}` : "li.is-current");
    if (el && cur) el.scrollLeft = cur.offsetLeft - el.clientWidth / 2 + cur.clientWidth / 2;
  }, [current.rank.key]);  // eslint-disable-line react-hooks/exhaustive-deps

  // place the popover under its Kiwi (clamped inside the section); phones use a bottom sheet instead (CSS)
  const place = useCallback(() => {
    if (!open || !root.current) return setAt(null);
    const btn = root.current.querySelector<HTMLElement>(`#reward-${open} button`);
    if (!btn) return;
    const box = root.current.getBoundingClientRect(), b = btn.getBoundingClientRect();
    const width = Math.min(340, box.width);
    const center = b.left + b.width / 2 - box.left;
    const left = Math.max(0, Math.min(box.width - width, center - width / 2));
    const top = (line.current ? line.current.offsetTop + line.current.offsetHeight : 0) + 10;
    setAt({ left, top, arrow: center - left });
  }, [open]);
  useEffect(() => {
    place();
    const el = line.current;
    window.addEventListener("resize", place);
    el?.addEventListener("scroll", place, { passive: true });
    return () => { window.removeEventListener("resize", place); el?.removeEventListener("scroll", place); };
  }, [place]);

  // outside click / Escape closes it (focus goes back to the Kiwi that opened it)
  useEffect(() => {
    if (!open) return;
    const close = () => {
      const btn = root.current?.querySelector<HTMLElement>(`#reward-${open} button`);
      setOpen(null);
      btn?.focus();
    };
    const down = (e: PointerEvent) => {
      const t = e.target as Node;
      if (pop.current?.contains(t) || line.current?.contains(t)) return;  // the row's own buttons switch / toggle
      setOpen(null);
    };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    document.addEventListener("pointerdown", down);
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("pointerdown", down); document.removeEventListener("keydown", key); };
  }, [open]);

  const sel = cards.find((c) => c.rank.key === open) ?? null;
  const selState = sel ? states[cards.indexOf(sel)] : null;
  return (
    <div className="rw" ref={root}>
      <ol className="rw-timeline" aria-label="Kiwi ranks: tap one to see its reward" ref={line}>
        {cards.map((c, i) => (
          <li key={c.rank.key} id={`reward-${c.rank.key}`} className={`${c.current ? "is-current" : c.reached ? "is-done" : "is-locked"}${open === c.rank.key ? " is-open" : ""}`}>
            <button type="button" aria-current={c.current ? "step" : undefined} aria-expanded={open === c.rank.key} aria-controls="rw-pop"
                    aria-label={`${c.rank.name}, ${fmt(c.rank.min_xp)} XP, ${STATE_LABEL[states[i]].toLowerCase()}`}
                    onClick={() => setOpen(open === c.rank.key ? null : c.rank.key)}>
              <span className="rw-kiwi">
                <RankKiwi art={c.rank.art} rankKey={c.rank.key} size={40} />
                {states[i] === "claimable" && <i className="rw-dot" aria-hidden />}
                {!c.reached && <i className="rw-lockmark" aria-hidden>🔒</i>}
              </span>
              <span className="rw-name">{c.rank.name}</span>
              <small>{fmt(c.rank.min_xp)}</small>
            </button>
          </li>
        ))}
      </ol>

      {next ? (
        <div className="rw-next">
          <div className="rw-bar" role="progressbar" aria-label={`${fmt(data.score)} of ${fmt(next.rank.min_xp)} XP to ${next.rank.name}`}
               aria-valuemin={0} aria-valuemax={next.rank.min_xp} aria-valuenow={data.score}>
            <i style={{ width: `${Math.round(next.progress * 100)}%` }} />
          </div>
          <small><b>{fmt(data.score)}</b> / {fmt(next.rank.min_xp)} XP · {fmt(next.xp_to_reach)} XP to {next.rank.name}</small>
        </div>
      ) : <p className="rw-next"><small>Every rank reached. Legendary.</small></p>}

      {sel && selState && (
        <>
          <div className="rw-scrim" aria-hidden onPointerDown={() => setOpen(null)} />
          <div id="rw-pop" ref={pop} className={`rw-pop is-${selState}`} role="dialog" aria-label={`${sel.rank.name} reward`}
               style={at ? ({ left: at.left, top: at.top, "--arrow": `${at.arrow}px` } as React.CSSProperties) : undefined}>
            <RewardPopover c={sel} state={selState} knight={data.knight} profile={data.profile} reload={reload} onProfileSaved={onProfileSaved}
                           onClose={() => setOpen(null)} />
          </div>
        </>
      )}
    </div>
  );
}

function RewardPopover({ c, state, knight, profile, reload, onProfileSaved, onClose }: {
  c: RewardCard; state: State; knight: Knight; profile?: ProfileReward; reload: () => Promise<void>; onProfileSaved?: () => void; onClose: () => void;
}) {
  const { rank, reward } = c;
  const [custom, setCustom] = useState(false);
  return (
    <>
      <div className="rw-pop-head">
        <RankKiwi art={rank.art} rankKey={rank.key} size={56} label={rank.name} />
        <div className="rw-pop-title">
          <span className="rw-rank">{rank.name} <small>· {fmt(rank.min_xp)} XP</small></span>
          <b>{reward.kind === "none" ? "Your journey starts here" : reward.title}</b>
        </div>
        <button type="button" className="rw-pop-x" onClick={onClose} aria-label="Close">×</button>
      </div>
      <span className={`rw-pill is-${state}`}>{state === "locked" ? "🔒 " : state === "unlocked" || state === "done" ? "✓ " : ""}{STATE_LABEL[state]}</span>
      {reward.description && reward.kind !== "tba" && <p className="rw-desc">{reward.description}</p>}
      {!c.reached && reward.kind !== "tba" && <p className="rw-fine">Unlocks at {rank.name}: {fmt(c.xp_to_reach)} XP to go</p>}
      {/* never a bare "Paused" / "Coming later": say why it can't be used and what happens next */}
      {state === "off" && <p className="rw-fine">Paused for now by the Price Chat team. It'll be back here as soon as it reopens.</p>}
      {state === "tba" && <p className="rw-fine">This reward is still being prepared. It's announced here first.</p>}
      {state !== "off" && c.reached && (
        <div className="rw-act">
          <RewardAction c={c} knight={knight} profile={profile} open={custom} toggle={() => setCustom((v) => !v)} reload={reload} onProfileSaved={onProfileSaved} />
        </div>
      )}
      {reward.kind === "trading_credit" && <KnightTerms knight={knight} />}
    </>
  );
}

function KnightTerms({ knight }: { knight: Knight }) {
  if (!knight.active) return <p className="rw-fine">Pending activation: not claimable yet. Terms, eligible regions and expiry will be shown here before it opens.</p>;
  return (
    <details className="rw-fine">
      <summary>Terms, regions and expiry</summary>
      {knight.terms && <p>{knight.terms}</p>}
      {knight.regions && <p><b>Regions:</b> {knight.regions}</p>}
      <p><b>Expires:</b> {knight.expires_at ? day(knight.expires_at) : "no end date set"}</p>
      <p>Reviewed by the Price Chat team; nothing is sent automatically. Trading is risky and not financial advice.</p>
    </details>
  );
}

function RewardAction({ c, knight, profile, open, toggle, reload, onProfileSaved }: {
  c: RewardCard; knight: Knight; profile?: ProfileReward; open: boolean; toggle: () => void; reload: () => Promise<void>; onProfileSaved?: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function run(path: string) {
    setBusy(true); setErr(null);
    try { await memberApi(path, { method: "POST", body: JSON.stringify({}) }); await reload(); }
    catch (e) { setErr(e instanceof Error ? e.message : "Couldn't do that. Try again."); }
    finally { setBusy(false); }
  }
  const error = err && <p className="rw-err" role="alert">{err}</p>;
  switch (c.reward.kind) {
    case "content":
      return <a className="site-button rw-btn" href="/learn">Open beginner content →</a>;
    case "profile":
      return (
        <>
          <button className="site-button rw-btn" aria-expanded={open} onClick={toggle}>{open ? "Close" : "Customize your Passport"}</button>
          {open && profile && <ProfileCustomizer profile={profile} onSaved={async () => { await reload(); onProfileSaved?.(); }} />}
        </>
      );
    case "earn":
      return <a className="site-button rw-btn" href="/earn">Open Earn →</a>;
    case "arena":
      return <a className="site-button rw-btn" href="/passport#realm">⚔️ Play Vortex Realm →</a>;
    case "trading_credit":
      if (c.claim) return <p className="rw-status">{CLAIM_LABEL[c.claim.status]} · claimed {day(c.claim.created_at)}</p>;
      if (!knight.active) return <p className="rw-status">Pending activation</p>;
      return (<>
        <button className="site-button rw-btn" disabled={busy} onClick={() => void run("/members/rewards/knight/claim")}>
          {busy ? "Claiming…" : `Claim the $${knight.amount_usd} trading reward`}</button>
        <small className="rw-fine">Needs a verified Hyperliquid wallet on your Passport. Once per member.</small>
        {error}
      </>);
    case "vip":
      return <VipAction claim={c.claim ?? null} busy={busy} onRequest={() => void run("/members/rewards/royal/request")} error={error} />;
    default:
      return null;
  }
}

function VipAction({ claim, busy, onRequest, error }: { claim: Claim | null; busy: boolean; onRequest: () => void; error: React.ReactNode }) {
  const [vip, setVip] = useState<{ invite_url: string | null; description: string } | null>(null);
  useEffect(() => {
    if (claim?.status === "APPROVED") void memberApi<{ invite_url: string | null; description: string }>("/members/rewards/royal").then(setVip).catch(() => undefined);
  }, [claim?.status]);
  if (!claim) return (<>
    <button className="site-button rw-btn" disabled={busy} onClick={onRequest}>{busy ? "Sending…" : "Request access"}</button>
    <small className="rw-fine">The Price Chat team reviews every request.</small>
    {error}
  </>);
  if (claim.status === "APPROVED") return (<>
    {vip?.description && <p className="rw-desc">{vip.description}</p>}
    {vip?.invite_url ? <a className="site-button rw-btn" href={vip.invite_url} target="_blank" rel="noopener noreferrer">Join the WhatsApp VIP group ↗</a>
      : <p className="rw-status">Approved ✓ The invite link will appear here as soon as the team adds it.</p>}
    <small className="rw-fine">Private: please don't share the link.</small>
  </>);
  return <p className="rw-status">{CLAIM_LABEL[claim.status]}</p>;
}

/** Kiwi reward: display name (rank-gated, checked and rate limited on the server) and a Passport frame colour. */
export function ProfileCustomizer({ profile, onSaved }: { profile: ProfileReward; onSaved: () => Promise<void> | void }) {
  const [name, setName] = useState(profile.display_name ?? "");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  async function save(body: Record<string, unknown>, ok: string) {
    setBusy(true); setMsg(null);
    try {
      await memberApi("/members/rewards/profile", { method: "PATCH", body: JSON.stringify(body) });
      setMsg({ ok: true, text: ok });
      await onSaved();
    } catch (e) { setMsg({ ok: false, text: e instanceof Error ? e.message : "Couldn't save." }); }
    finally { setBusy(false); }
  }
  const locked = !!profile.next_rename_at;
  return (
    <div className="rw-custom">
      <form onSubmit={(e) => { e.preventDefault(); if (name.trim()) void save({ display_name: name }, "Display name saved ✓"); }}>
        <label htmlFor="rw-dname">Display name</label>
        <div className="rw-row">
          <input id="rw-dname" className="site-input" value={name} maxLength={32} onChange={(e) => setName(e.target.value)} disabled={locked || busy}
                 autoComplete="off" spellCheck={false} />
          <button className="site-button" disabled={locked || busy || !name.trim() || name.trim() === profile.display_name}>Save</button>
        </div>
        <small className="rw-fine">{locked ? `You can change it again on ${day(profile.next_rename_at!)}.`
          : "2-32 characters. Once a week. Your @username and sign-ins stay the same."}</small>
      </form>
      <fieldset className="rw-accents">
        <legend>Passport frame</legend>
        {profile.accents.map((a) => (
          <button key={a} type="button" className={`rw-swatch is-${a}`} aria-pressed={profile.accent === a} disabled={busy}
                  onClick={() => void save(profile.accent === a ? { clear_accent: true } : { accent: a }, "Frame saved ✓")}>
            <span aria-hidden /> {ACCENT_LABEL[a] ?? a}
          </button>
        ))}
      </fieldset>
      {msg && <p className={msg.ok ? "rw-ok" : "rw-err"} role="status">{msg.text}</p>}
    </div>
  );
}

// --- Kiwi Chick: beginner content -----------------------------------------------------------------------------------

interface Lesson { id: number; title: string; description: string; video_url: string | null; thumbnail_url: string | null; guide_text: string }

/** A YouTube address as its privacy-enhanced embed; anything else stays a plain link. */
export function youtubeEmbed(url: string): string | null {
  const m = url.match(/^https:\/\/(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?v=|shorts\/|embed\/)|youtu\.be\/)([A-Za-z0-9_-]{6,15})/);
  return m ? `https://www.youtube-nocookie.com/embed/${m[1]}` : null;
}

export function LearnPage() {
  const [state, setState] = useState<{ items: Lesson[]; coming_soon: boolean } | { error: string; status: number } | null>(null);
  useEffect(() => {
    memberApi<{ items: Lesson[]; coming_soon: boolean }>("/members/rewards/beginner").then(setState)
      .catch((e) => setState({ error: e instanceof Error ? e.message : "Couldn't load.", status: (e as { status?: number }).status ?? 0 }));
  }, []);
  return (
    <SitePage {...pageHead("learn")}>
      <section className="rw-learn">
        <h1>🐣 Beginner content</h1>
        <p className="site-muted">Your Kiwi Chick reward: lessons from Price Chat for getting started.</p>
        {!state ? <p className="site-empty">Loading…</p>
          : "error" in state ? (
            <div className="rw-gate">
              <p>{state.status === 401 ? "Sign in to your Kiwi Passport to see your rewards." : state.error}</p>
              <a className="site-button" href={state.status === 401 ? "/join" : "/passport#reward-chick"}>{state.status === 401 ? "Sign in" : "See your progress"}</a>
            </div>
          ) : state.coming_soon ? (
            <div className="rw-soon"><b>Coming soon</b><p>The first lessons are being prepared. They'll appear here as soon as they're published.</p></div>
          ) : (
            <ol className="rw-lessons">
              {state.items.map((l) => <LessonView key={l.id} l={l} />)}
            </ol>
          )}
      </section>
    </SitePage>
  );
}

function LessonView({ l }: { l: Lesson }) {
  const embed = l.video_url ? youtubeEmbed(l.video_url) : null;
  return (
    <li className="rw-lesson">
      <h2>{l.title}</h2>
      {l.description && <p className="site-muted">{l.description}</p>}
      {embed ? (
        <div className="rw-video"><iframe src={embed} title={l.title} loading="lazy" allow="encrypted-media; picture-in-picture" allowFullScreen /></div>
      ) : l.video_url ? (
        <a className="rw-video-link" href={l.video_url} target="_blank" rel="noopener noreferrer">
          {l.thumbnail_url && <img src={l.thumbnail_url} alt="" loading="lazy" />}<span>▶ Watch the video ↗</span>
        </a>
      ) : l.thumbnail_url ? <img className="rw-thumb" src={l.thumbnail_url} alt="" loading="lazy" /> : null}
      {l.guide_text && <div className="rw-guide">{l.guide_text.split(/\n{2,}/).map((para, i) => <p key={i}>{para}</p>)}</div>}
    </li>
  );
}
