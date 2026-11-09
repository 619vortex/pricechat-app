/**
 * Read & Earn (Kiwi Passport): Price Chat's latest X posts and replies, one at a time, in ONE self-contained card:
 *
 *   @you on X · today's reading XP · posts left
 *   ONE POST (author, text, media) · read it here (a few seconds on screen) · +XP · Like / Reply / Repost · done ✓
 *   Up next (tap one to jump to it) · Next post
 *
 * Nothing is shown until X is connected through Price Chat's own "Connect X" (OAuth on X's page; never a password, never
 * X's login in a frame). The server checks that too (members/x_feed.py).
 *
 * Reading here is an action on Price Chat's own site, verified by the server's clock, so it can earn a little XP. Liking,
 * reposting or replying on X never earns anything: X's Developer Policy forbids rewards for actions on X. Reply and Repost
 * run right here with the member's OWN X account when its permission allows (members/x_actions.py, X confirms each one);
 * Like always opens X's own like screen (X removed likes from its self-serve API) as a small window over this page, in
 * the same browser and X sign-in (openOnX), and is shown as "opened", never as
 * "liked". Every action is one press by the member; nothing happens by itself.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { ConnectButton } from "./community";
import { burst } from "./fx";
import {
  memberApi, refreshSession, type FeedItem, type Providers, type FeedResult, type Passport, type XActionResult, type XFeed,
} from "./members";
import { reducedMotion } from "./motion";
import { playSound, warmSounds } from "./sound";
import "./xfeed.css";

const LEAVE_MS = 260;
const wait = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms));
const n = (v: number | null | undefined) => (v ?? 0).toLocaleString("en-GB");

function ago(iso: string | null): string {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

type Read = { state: "reading" | "rewarding" | "read" | "no_xp"; xp: number; note?: string };
type Act = "like" | "reply" | "repost";

export function XFeedSection({ reload, rewardFrom }: {
  reload: () => Promise<Passport | null>; rewardFrom?: React.MutableRefObject<Element | null>;
}) {
  const [feed, setFeed] = useState<XFeed | null>(null);
  const [index, setIndex] = useState(0); // position in the loaded items
  const [leaving, setLeaving] = useState(false);
  const load = useCallback(async () => {
    try {
      const next = await memberApi<XFeed>("/members/me/x-feed");
      setFeed(next);
      setIndex(0);
    } catch {
      /* couldn't load: keep what's shown (or nothing). "Caught up" is only ever the server's answer */
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  if (!feed) return null;
  const connected = !feed.needs_x && feed.x.connected;
  const item = connected ? feed.items[index] ?? null : null;
  const upNext = connected ? feed.items.slice(index + 1, index + 4) : [];
  const left = Math.max(0, feed.remaining - index);

  async function go(to: number) {
    setLeaving(true);
    await wait(reducedMotion() ? 0 : LEAVE_MS);
    setLeaving(false);
    if (to < feed!.items.length) setIndex(to);
    else await load(); // the end of what's loaded: fetch what's left (read posts are no longer included)
  }

  return (
    <section className="pp-section site-reveal xf" aria-labelledby="xf-title">
      <div className="xf-box">
        <header className="xf-top">
          <h2 className="site-display pp-title" id="xf-title">Read &amp; Earn</h2>
          {connected && feed.x.username && <span className="xf-acct" title="Your connected X account"><b aria-hidden>𝕏</b> @{feed.x.username}</span>}
        </header>

        {!connected ? (
          <ConnectX />
        ) : (
          <>
            <TodayBar feed={feed} left={left} />
            {item ? (
              <PostCard key={item.id} item={item} readSeconds={feed.read_seconds} x={feed.x} leaving={leaving}
                        reload={reload} rewardFrom={rewardFrom} onNext={() => void go(index + 1)}
                        onEarned={(xp) => setFeed((f) => f && { ...f, today: { ...f.today, earned: f.today.earned + xp, read: (f.today.read ?? 0) + 1 } })} />
            ) : (
              <div className="xf-empty">
                <p className="xf-empty-title">You're caught up 🥝</p>
                <p className="site-note">Come back when Price Chat posts again.</p>
              </div>
            )}
            {upNext.length > 0 && (
              <div className="xf-next">
                <p className="xf-label">Up next</p>
                <ul>
                  {upNext.map((u, i) => (
                    <li key={u.id}>
                      <button type="button" className="xf-next-row" onClick={() => void go(index + 1 + i)}>
                        <span className="xf-next-kind">{u.kind === "reply" && u.replying_to ? `↳ @${u.replying_to}` : u.kind === "quote" ? "Quote" : "Post"}</span>
                        <span className="xf-next-text">{u.text}</span>
                        {u.xp > 0 && <span className="xf-next-xp">+{u.xp}</span>}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <p className="xf-foot">Reading here earns Kiwi XP. Likes, replies and reposts are support only: X's rules don't allow rewards for them.</p>
          </>
        )}
      </div>
    </section>
  );
}

/** Not connected: one clear card. The feed only appears once X has authorised Price Chat (X's own sign-in page). */
function ConnectX() {
  // audit PP-14: in-Passport replies / reposts only exist while the X app may write (providers x.write); otherwise they open X
  const [write, setWrite] = useState(false);
  useEffect(() => {
    let on = true;
    memberApi<Providers>("/members/auth/providers").then((p) => { if (on) setWrite(!!p.x?.write); }, () => {});
    return () => { on = false; };
  }, []);
  return (
    <div className="xf-connect-card">
      <span className="xf-connect-mark" aria-hidden>𝕏</span>
      <p className="xf-connect-title">Connect X to start Read &amp; Earn</p>
      <p className="site-note">Read Price Chat's latest posts right here and earn Kiwi XP for each one.{write ? " Reply and repost without leaving your Passport." : ""}</p>
      <ConnectButton provider="x" label="Connect X" className="site-button xf-connect-btn" />
      <small className="xf-connect-fine">You sign in on X's own page. Price Chat never sees your password and never posts without you pressing a button.</small>
    </div>
  );
}

function TodayBar({ feed, left }: { feed: XFeed; left: number }) {
  const { earned, cap } = feed.today;
  const pct = cap > 0 ? Math.min(100, (earned / cap) * 100) : 0;
  return (
    <div className="xf-today" aria-label={`Today: ${earned} of ${cap} reading XP earned, ${left} posts left`}>
      {cap > 0 && (
        <div className="xf-today-bar" role="progressbar" aria-label="Reading XP today" aria-valuemin={0} aria-valuemax={cap} aria-valuenow={earned}>
          <i style={{ width: `${pct}%` }} />
        </div>
      )}
      <p className="xf-today-meta">
        {cap > 0 && <span><strong>{earned} / {cap}</strong> XP today</span>}
        {(feed.today.read ?? 0) > 0 && <span>{feed.today.read} read</span>}
        <span>{left > 0 ? `${left} post${left === 1 ? "" : "s"} left` : "All read"}</span>
      </p>
    </div>
  );
}

function PostCard({ item, readSeconds, x, leaving, reload, rewardFrom, onNext, onEarned }: {
  item: FeedItem; readSeconds: number; x: XFeed["x"]; leaving: boolean; reload: () => Promise<Passport | null>;
  rewardFrom?: React.MutableRefObject<Element | null>; onNext: () => void; onEarned: (xp: number) => void;
}) {
  const card = useRef<HTMLElement>(null);
  const xpEl = useRef<HTMLSpanElement>(null);
  const [read, setRead] = useState<Read>({ state: "reading", xp: item.xp });
  const [progress, setProgress] = useState(0);
  const [reward, setReward] = useState<"rise" | "fly" | null>(null);

  // the reading clock: counts only while this card is on screen and the tab is visible; the server checks its own clock
  useEffect(() => {
    const el = card.current;
    if (!el) return;
    let visible = typeof IntersectionObserver === "undefined";
    let seen = 0, last = Date.now(), started = false, asking = false, retryAt = 0, done = false;
    const io = typeof IntersectionObserver !== "undefined"
      ? new IntersectionObserver(([e]) => (visible = e.intersectionRatio >= 0.5), { threshold: [0, 0.5, 1] }) : null;
    io?.observe(el);
    const timer = window.setInterval(() => {
      const now = Date.now(), dt = Math.min(now - last, 1000) / 1000;
      last = now;
      if (done || !visible || document.visibilityState === "hidden") return;
      if (!started) {
        started = true;
        void memberApi("/members/me/x-feed/shown", { method: "POST", body: JSON.stringify({ post_id: item.id }) }).catch(() => undefined);
      }
      seen += dt;
      setProgress(Math.min(1, seen / readSeconds));
      if (seen < readSeconds || asking || now < retryAt) return;
      asking = true;
      void memberApi<FeedResult>("/members/me/x-feed/read", { method: "POST", body: JSON.stringify({ post_id: item.id }) })
        .then(async (r) => {
          if (r.code === "too_soon" || r.code === "not_started") {
            retryAt = Date.now() + 1500; // the server's clock catches up
            return;
          }
          done = true;
          if (r.code === "paid" && r.xp > 0) await celebrate(r.xp);
          else setRead({ state: r.code === "already" ? "read" : "no_xp", xp: 0, note: r.code === "refused" || r.code === "connect_x" ? r.message : undefined });
        })
        .catch(() => (retryAt = Date.now() + 4000))
        .finally(() => (asking = false));
    }, 250);
    return () => {
      window.clearInterval(timer);
      io?.disconnect();
    };
  }, [item.id]); // eslint-disable-line react-hooks/exhaustive-deps

  /** The server paid: the same reward moment as Watch & Earn (sound, "+N XP" rises, sparks, flies into the Passport). */
  async function celebrate(xp: number) {
    const calm = reducedMotion();
    setRead({ state: "rewarding", xp });
    setReward("rise");
    playSound("xp");
    await wait(16);
    if (xpEl.current) burst(xpEl.current, 8, 46, true);
    await wait(calm ? 0 : 420);
    if (rewardFrom) rewardFrom.current = calm ? null : xpEl.current;
    setReward("fly");
    try {
      await reload(); // the Passport counts it in, moves the bar, adds Recent XP; a rank-up shows its celebration first
      refreshSession();
    } finally {
      await wait(calm ? 700 : 650);
      if (rewardFrom && rewardFrom.current === xpEl.current) rewardFrom.current = null;
      setReward(null);
      setRead({ state: "read", xp });
      onEarned(xp);
    }
  }

  const isRead = read.state === "read" || read.state === "no_xp";
  const author = item.author ?? { name: "Price Chat", username: "pricechat" };
  return (
    <article ref={card} className={`xf-card${leaving ? " is-leaving" : ""}${read.state === "rewarding" ? " is-earning" : ""}`}
             aria-labelledby={`xf-post-${item.id}`}>
      {item.parent ? (
        <>
          <div className="xf-parent">
            <span className="xf-avatar xf-avatar-other" aria-hidden>{(item.parent.name || item.parent.username || "?").charAt(0).toUpperCase()}</span>
            <div>
              <p className="xf-who">{item.parent.name && <strong>{item.parent.name}</strong>} {item.parent.username && <span>@{item.parent.username}</span>}</p>
              <p className="xf-parent-text">{item.parent.text}</p>
            </div>
          </div>
          <span className="xf-thread" aria-hidden />
        </>
      ) : item.replying_to ? (
        <p className="xf-replying">Replying to <strong>@{item.replying_to}</strong></p>
      ) : null}

      <div className="xf-main">
        <img className="xf-avatar" src="/favicon.png" alt="" width={40} height={40} decoding="async" />
        <div className="xf-body">
          <p className="xf-who">
            <strong>{author.name}</strong> <span>@{author.username}</span>{item.posted_at && <span> · {ago(item.posted_at)}</span>}
          </p>
          <p className="xf-text" id={`xf-post-${item.id}`}>{item.text}</p>
          {item.media && item.media.length > 0 && <Media media={item.media} url={item.url} />}
          <p className="xf-metrics">
            <span title="Replies on X">💬 {n(item.replies)}</span><span title="Reposts on X">🔁 {n(item.reposts)}</span><span title="Likes on X">♥ {n(item.likes)}</span>
            <a href={item.url} target="_blank" rel="noopener noreferrer">{(item.replies ?? 0) > 0 ? "Read the replies on X ↗" : "Open on X ↗"}</a>
          </p>
        </div>
      </div>

      <div className="xf-read" aria-live="polite">
        {read.state === "reading" ? (
          read.xp > 0 ? (
            <>
              <div className="xf-track" aria-hidden><span style={{ "--p": progress } as React.CSSProperties} /></div>
              <span className="xf-read-label">Reading · <strong>+{read.xp} XP</strong></span>
            </>
          ) : (
            <span className="xf-read-label">Reading · today's reading XP is used up</span>
          )
        ) : read.state === "rewarding" ? (
          <span className="xf-read-label is-done">✓ Read</span>
        ) : (
          <span className={`xf-read-label${read.xp > 0 ? " is-done" : ""}`}>✓ Read{read.xp > 0 ? <strong> +{read.xp} XP</strong> : null}</span>
        )}
        {read.note && <span className="xf-note">{read.note}</span>}
        {reward && <span ref={xpEl} className={`we-reward xf-reward is-${reward}`}>+{read.xp} XP</span>}
      </div>

      <XSupport id={item.id} x={x} done={item.done ?? []} />
      <div className="xf-actions">
        <button type="button" className={`xf-act xf-nextbtn${isRead ? " is-primary" : ""}`} onClick={onNext}>Next post <span aria-hidden>→</span></button>
      </div>
    </article>
  );
}

function Media({ media, url }: { media: NonNullable<FeedItem["media"]>; url: string }) {
  return (
    <div className={`xf-media is-${Math.min(media.length, 4)}`}>
      {media.map((m) => (
        <a key={m.url} href={url} target="_blank" rel="noopener noreferrer" className={`xf-media-item${m.type !== "photo" ? " is-video" : ""}`}
           aria-label={m.type !== "photo" ? "Play the video on X" : m.alt || "Open the image on X"}>
          <img src={m.url} alt={m.alt ?? ""} loading="lazy" decoding="async" width={m.width ?? undefined} height={m.height ?? undefined} />
          {m.type !== "photo" && <span className="xf-play" aria-hidden>▶</span>}
        </a>
      ))}
    </div>
  );
}

// --- like, reply and repost ---------------------------------------------------------------------------------------------

const intentUrl = {
  like: (id: string) => `https://x.com/intent/like?tweet_id=${encodeURIComponent(id)}`,
  reply: (id: string) => `https://x.com/intent/post?in_reply_to=${encodeURIComponent(id)}`,
  repost: (id: string) => `https://x.com/intent/retweet?tweet_id=${encodeURIComponent(id)}`,
};
const MAX_REPLY = 280;

/** X's own screens open as a small window over Read & Earn, in this same browser (and its X sign-in), never a new tab or
 *  another app's browser. Returns false when the browser blocked the window (the link then opens normally). Calls
 *  `onClosed` when the member closes it, so the card can say "back from X". */
export function openOnX(url: string, onClosed?: () => void): boolean {
  const w = Math.min(560, window.screen?.availWidth ?? 560), h = Math.min(700, window.screen?.availHeight ?? 700);
  const left = Math.max(0, (window.screenX ?? 0) + ((window.outerWidth ?? w) - w) / 2);
  const top = Math.max(0, (window.screenY ?? 0) + ((window.outerHeight ?? h) - h) / 3);
  let win: Window | null = null;
  try {
    win = window.open(url, "pricechat-x", `popup=yes,width=${w},height=${h},left=${Math.round(left)},top=${Math.round(top)}`);
  } catch {
    win = null;
  }
  if (!win) return false;
  if (onClosed) {
    const t = window.setInterval(() => {
      let closed = true;
      try { closed = win!.closed; } catch { /* another site's window: keep waiting */ closed = false; }
      if (closed) { window.clearInterval(t); onClosed(); }
    }, 600);
    window.setTimeout(() => window.clearInterval(t), 10 * 60 * 1000);
  }
  return true;
}

/** Like, Reply and Repost on a Price Chat post. With X connected and allowed to post, Reply and Repost happen right here, as
 *  the member, from one press each (X confirms them; the member writes every word of a reply). Like always opens X's own
 *  like screen: X's API has no likes any more, so it says "opened on X", never "liked". Without the posting permission,
 *  Reply and Repost open X's own screens too, with a one-tap way to allow them here. Support only: never XP. */
export function XSupport({ id, x, done: initial = [] }: { id: string; x?: XFeed["x"]; done?: Act[] }) {
  const can = !!x?.can_act;
  const [done, setDone] = useState<Partial<Record<Act, string | true>>>(() => Object.fromEntries(initial.map((k) => [k, true])));
  const [composing, setComposing] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<Act | null>(null);
  const [problem, setProblem] = useState<{ message: string; reconnect: boolean } | null>(null);
  const box = useRef<HTMLDivElement>(null);

  function mark(kind: Act, link: string | true) {
    warmSounds();
    setDone((d) => ({ ...d, [kind]: link }));
    if (box.current && !reducedMotion() && kind !== "like") burst(box.current, 8, 40, false);
    playSound("tick");
  }

  async function send(kind: "reply" | "repost") {
    if (busy) return;
    setBusy(kind);
    setProblem(null);
    try {
      const r = await memberApi<XActionResult>(`/members/me/x/${kind}`, {
        method: "POST", body: JSON.stringify(kind === "reply" ? { post_id: id, text: text.trim() } : { post_id: id }),
      });
      if (r.ok) {
        mark(kind, r.url ?? true);
        if (kind === "reply") { setComposing(false); setText(""); }
      } else {
        setProblem({ message: r.message, reconnect: !!r.reconnect });
      }
    } catch (e) {
      setProblem({ message: e instanceof Error ? e.message : "Couldn't reach X. Nothing was sent.", reconnect: false });
    } finally {
      setBusy(null);
    }
  }

  const [liking, setLiking] = useState(false);
  function openLike(e: React.MouseEvent) {
    // X's API has no likes any more: X's own like screen, as a small window on top of this page (same browser, same X
    // sign-in). One tap on ♥ there, close it, and you're back here.
    if (openOnX(intentUrl.like(id), () => setLiking(false))) {
      e.preventDefault();
      setLiking(true);
    }
    mark("like", true);
    void memberApi("/members/me/x-feed/like-opened", { method: "POST", body: JSON.stringify({ post_id: id }) }).catch(() => undefined);
  }

  const left = MAX_REPLY - text.length;
  const doneBtn = (kind: Act, label: string) => {
    const link = done[kind];
    return typeof link === "string"
      ? <a className="xf-x-btn is-done" href={link} target="_blank" rel="noopener noreferrer">✓ {label} <small>view</small></a>
      : <span className="xf-x-btn is-done">✓ {label}</span>;
  };
  return (
    <div className="xf-x" ref={box}>
      <div className="xf-x-row">
        {liking ? <span className="xf-x-btn is-done" role="status">♥ Tap Like in the X window, then close it</span>
          : done.like ? doneBtn("like", "Opened on X") : (
          <a className="xf-x-btn is-quiet" href={intentUrl.like(id)} target="_blank" rel="noopener noreferrer" onClick={openLike}>
            <span aria-hidden className="xf-heart">♥</span> Like <small aria-hidden>↗</small>
          </a>
        )}
        {done.reply ? doneBtn("reply", "Replied") : can ? (
          <button type="button" className={`xf-x-btn${composing ? " is-open" : ""}`} aria-expanded={composing} onClick={() => setComposing((c) => !c)}>
            <span aria-hidden>💬</span> Reply
          </button>
        ) : (
          <a className="xf-x-btn" href={intentUrl.reply(id)} target="_blank" rel="noopener noreferrer"
             onClick={(e) => { if (openOnX(intentUrl.reply(id))) e.preventDefault(); mark("reply", true); }}>
            <span aria-hidden>💬</span> Reply <small aria-hidden>↗</small>
          </a>
        )}
        {done.repost ? doneBtn("repost", "Reposted") : can ? (
          <button type="button" className="xf-x-btn" disabled={busy === "repost"} onClick={() => void send("repost")}>
            <span aria-hidden>🔁</span> {busy === "repost" ? "Reposting…" : "Repost"}
          </button>
        ) : (
          <a className="xf-x-btn" href={intentUrl.repost(id)} target="_blank" rel="noopener noreferrer"
             onClick={(e) => { if (openOnX(intentUrl.repost(id))) e.preventDefault(); mark("repost", true); }}>
            <span aria-hidden>🔁</span> Repost <small aria-hidden>↗</small>
          </a>
        )}
      </div>

      {composing && can && !done.reply && (
        <form className="xf-compose" onSubmit={(e) => { e.preventDefault(); if (text.trim() && left >= 0) void send("reply"); }}>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={MAX_REPLY + 20} autoFocus
                    placeholder="Write your reply to @pricechat…" aria-label="Your reply" />
          <div className="xf-compose-bar">
            <small className={left < 0 ? "is-over" : left < 30 ? "is-low" : ""}>{left}</small>
            <button type="button" className="site-textbutton" onClick={() => { setComposing(false); setText(""); }}>Cancel</button>
            <button type="submit" className="xf-x-post" disabled={!text.trim() || left < 0 || busy === "reply"}>
              {busy === "reply" ? "Posting…" : "Post reply"}
            </button>
          </div>
        </form>
      )}

      {problem && (
        <p className="pp-problem" role="alert">
          {problem.message}
          {problem.reconnect && <> <ConnectButton provider="x" label="Reconnect X" className="site-textbutton" /></>}
        </p>
      )}

      {!can && x?.connected && (
        <p className="xf-support">Reply and Repost open X for now · <ConnectButton provider="x" label="Allow them right here" className="site-textbutton xf-x-connect" /></p>
      )}
    </div>
  );
}
