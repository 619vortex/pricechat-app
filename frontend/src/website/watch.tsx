/**
 * Watch & Earn (Kiwi Passport): the newest Price Chat videos, played right here. One featured player, the next few as
 * thumbnails; picking one swaps it into the player in place. Only the active video ever has an iframe, and the YouTube
 * player script loads on the first Watch press.
 *
 * Honest by construction: the player counts seconds that actually played in this browser (paused, buffering or a
 * hidden tab don't count), and the server pays only once that much time has really passed since Watch was pressed
 * here. It's "played N seconds in Price Chat", never "watched the video": YouTube doesn't tell anyone that. Comments
 * and subscriptions aren't offered: member Google sign-in has no YouTube permission, so they can't be done or checked.
 * The reward moment (sound, "+N XP", sparks, the flight to the Passport) runs only on the server's "paid" answer.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { memberApi, refreshSession, type Passport, type WatchList, type WatchResult, type WatchVideo } from "./members";
import { burst } from "./fx";
import { reducedMotion } from "./motion";
import { playSound, useSoundPref, warmSounds } from "./sound";
import "./watch.css";

const TICK_MS = 500;
const EMBED_REFUSED = new Set([100, 101, 150]); // removed, or the owner doesn't allow playback on other sites

// --- the YouTube IFrame Player API ---------------------------------------------------------------------------------

interface YTPlayer {
  getPlayerState(): number;
  getCurrentTime(): number;
  playVideo(): void;
  destroy(): void;
}
interface YTNamespace {
  Player: new (el: HTMLElement, opts: Record<string, unknown>) => YTPlayer;
}
declare global {
  interface Window {
    YT?: YTNamespace;
    onYouTubeIframeAPIReady?: () => void;
  }
}

let api: Promise<YTNamespace | null> | null = null;

/** The official player API, loaded once and only when someone presses Watch. null = blocked or unreachable. */
export function loadYouTubeApi(timeoutMs = 8000): Promise<YTNamespace | null> {
  if (window.YT?.Player) return Promise.resolve(window.YT);
  if (!api) {
    api = new Promise((resolve) => {
      const before = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => {
        before?.();
        resolve(window.YT ?? null);
      };
      const s = document.createElement("script");
      s.src = "https://www.youtube.com/iframe_api";
      s.async = true;
      s.onerror = () => {
        api = null;
        resolve(null);
      };
      document.head.appendChild(s);
      window.setTimeout(() => {
        if (!window.YT?.Player) api = null; // try again on the next press
        resolve(window.YT?.Player ? window.YT : null);
      }, timeoutMs);
    });
  }
  return api;
}

// --- formatting ----------------------------------------------------------------------------------------------------

const isShort = (v: WatchVideo) => v.type === "short";

function duration(s: number | null): string | null {
  if (!s) return null;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}

function date(iso: string | null): string | null {
  return iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : null;
}

// --- the player ----------------------------------------------------------------------------------------------------

/** One embedded player. Reports played seconds through `onPlayed`: time the video's own position actually moved forward,
 *  capped by the real time that passed (pauses, buffering, a hidden tab or seeking ahead add nothing). */
function Player({ v, onPlayed, onUntracked, onRefused }: {
  v: WatchVideo; onPlayed: (secs: number) => void; onUntracked: () => void; onRefused: () => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [fallback, setFallback] = useState(false);
  const cb = useRef({ onPlayed, onUntracked, onRefused });
  cb.current = { onPlayed, onUntracked, onRefused };

  useEffect(() => {
    let alive = true;
    let player: YTPlayer | null = null;
    let timer = 0;
    void loadYouTubeApi().then((YT) => {
      if (!alive) return;
      if (!YT || !host.current) {
        setFallback(true); // still plays, it just can't count
        cb.current.onUntracked();
        return;
      }
      const mount = document.createElement("div"); // the API swaps this for its iframe; React's node stays put
      host.current.appendChild(mount);
      player = new YT.Player(mount, {
        host: "https://www.youtube-nocookie.com",
        videoId: v.external_id,
        width: "100%",
        height: "100%",
        playerVars: { autoplay: 1, playsinline: 1, rel: 0, modestbranding: 1 },
        events: {
          onReady: (e: { target: YTPlayer }) => e.target.playVideo(),
          onError: (e: { data: number }) => EMBED_REFUSED.has(e.data) && cb.current.onRefused(),
        },
      });
      let played = 0;
      let last = Date.now();
      let pos: number | null = null;
      timer = window.setInterval(() => {
        const now = Date.now();
        const dt = (now - last) / 1000;
        last = now;
        let t: number | null = null;
        try {
          t = player?.getCurrentTime() ?? null;
        } catch {
          /* not ready yet */
        }
        if (typeof t !== "number" || !Number.isFinite(t)) return;
        const moved = pos === null ? 0 : t - pos;
        pos = t;
        // forward, and no faster than the clock (+ a little jitter): a seek ahead or a replayed loop start counts nothing
        if (moved > 0 && moved <= dt * 1.5 + 0.25 && document.visibilityState !== "hidden") {
          played += Math.min(moved, dt);
          cb.current.onPlayed(played);
        }
      }, TICK_MS);
    });
    return () => {
      alive = false;
      window.clearInterval(timer);
      try {
        player?.destroy();
      } catch {
        /* already gone */
      }
    };
  }, [v.external_id]);

  return (
    <div className={`we-player${isShort(v) ? " is-short" : ""}`}>
      {fallback ? (
        <iframe src={`https://www.youtube-nocookie.com/embed/${encodeURIComponent(v.external_id)}?autoplay=1&playsinline=1&rel=0`}
                title={v.title} allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen />
      ) : (
        <div ref={host} className="we-player-host" title={v.title} />
      )}
    </div>
  );
}

// --- Watch & Earn --------------------------------------------------------------------------------------------------

type Done = { xp: number; note?: string };
type Reward = { id: string; xp: number; stage: "rise" | "fly" };

const wait = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms));

export function WatchEarn({ reload, rewardFrom }: {
  reload: () => Promise<Passport | null>;
  /** Shared with the Passport play area: the "+N XP" here is where its flying chip starts. */
  rewardFrom?: React.MutableRefObject<Element | null>;
}) {
  const [list, setList] = useState<WatchList | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [played, setPlayed] = useState(0);
  const [done, setDone] = useState<Record<string, Done>>({});
  const [reward, setReward] = useState<Reward | null>(null);
  const [refused, setRefused] = useState<Set<string>>(new Set());
  const [untracked, setUntracked] = useState(false);
  const [sound, setSound] = useSoundPref();
  const started = useRef<string | null>(null); // the video the server knows was started here
  const sending = useRef(false);
  const retryAt = useRef(0);
  const ticked = useRef(new Set<string>()); // the halfway tick, once per video
  const feature = useRef<HTMLElement>(null);
  const xpEl = useRef<HTMLSpanElement>(null);

  const load = useCallback(async () => {
    try {
      setList(await memberApi<WatchList>("/members/me/watch"));
    } catch {
      setList((l) => l ?? { threshold_seconds: 0, videos: [] });
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  if (!list || list.videos.length === 0) return null; // nothing real to show: no section

  const videos = list.videos;
  const active = videos.find((v) => v.external_id === activeId) ?? videos[0];
  const finished: Done | null = done[active.external_id] ?? (active.status === "completed" ? { xp: active.xp } : null);
  const earns = active.status === "available" && !finished && !refused.has(active.external_id);
  const next = videos[(videos.indexOf(active) + 1) % videos.length];
  const short = isShort(active);
  const need = active.needed_seconds;
  const rewarding = reward?.id === active.external_id ? reward : null;

  function watch(v: WatchVideo) {
    const switching = v.external_id !== active.external_id;
    warmSounds(); // a press: from now on a sound may play (if it's on)
    setActiveId(v.external_id);
    setPlaying(true);
    setPlayed(0);
    setUntracked(false);
    sending.current = false;
    retryAt.current = 0;
    started.current = null;
    void memberApi("/members/me/watch/start", { method: "POST", body: JSON.stringify({ external_id: v.external_id }) })
      .then(() => {
        started.current = v.external_id;
      })
      .catch(() => undefined); // it still plays; it just can't count
    if (switching) feature.current?.scrollIntoView?.({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
  }

  /** The server confirmed XP: the bar fills green, the card pulses, "+N XP" rises with a few sparks, then flies to the
   *  Passport (which counts it in), and the card settles on ✓ Earned. Only ever after a "paid" answer. */
  async function celebrate(v: WatchVideo, xp: number) {
    const calm = reducedMotion();
    setReward({ id: v.external_id, xp, stage: "rise" });
    playSound("xp");
    await wait(16);
    if (xpEl.current) burst(xpEl.current, 8, 46, true);
    await wait(calm ? 0 : 420);
    if (rewardFrom) rewardFrom.current = calm ? null : xpEl.current;
    setReward({ id: v.external_id, xp, stage: "fly" });
    try {
      await reload(); // the Passport above flies the chip in, counts up, moves the rank bar, adds the Recent XP row
      refreshSession();
    } finally {
      await wait(calm ? 900 : 700);
      if (rewardFrom && rewardFrom.current === xpEl.current) rewardFrom.current = null;
      setDone((d) => ({ ...d, [v.external_id]: { xp } }));
      setReward(null);
      void load();
    }
  }

  /** The player says `secs` played. Once the goal is reached, ask the server; it decides (and may say "not yet"). */
  async function onPlayed(v: WatchVideo, secs: number) {
    setPlayed(secs);
    const open = v.status === "available" && !done[v.external_id] && started.current === v.external_id;
    if (open && secs >= v.needed_seconds / 2 && secs < v.needed_seconds && !ticked.current.has(v.external_id)) {
      ticked.current.add(v.external_id);
      playSound("tick"); // halfway: one tiny tick, nothing more while watching
    }
    if (!open || sending.current || secs < v.needed_seconds || Date.now() < retryAt.current) return;
    sending.current = true;
    try {
      const r = await memberApi<WatchResult>("/members/me/watch/complete", {
        method: "POST", body: JSON.stringify({ external_id: v.external_id, played_seconds: Math.floor(secs) }),
      });
      if (r.code === "too_soon" || r.code === "not_started") {
        retryAt.current = Date.now() + 2000; // the clock catches up on the next ticks
        sending.current = false;
        return;
      }
      if (r.code === "paid" && r.xp > 0) {
        await celebrate(v, r.xp);
        return;
      }
      setDone((d) => ({ ...d, [v.external_id]: { xp: 0, note: r.code === "refused" ? r.message : undefined } }));
      await reload();
      void load();
    } catch {
      retryAt.current = Date.now() + 4000;
      sending.current = false;
    }
  }

  const dur = duration(active.duration_seconds);
  const kicker = active.latest ? `Watch our latest ${short ? "Short" : "video"}` : short ? "Price Chat Short" : "Price Chat video";
  const blocked = refused.has(active.external_id);
  const earned = !!finished && (finished.xp > 0 || active.status === "completed");
  const shownSecs = rewarding ? need : Math.min(need, Math.floor(played));

  return (
    <section className="pp-section site-reveal we" aria-labelledby="we-title">
      <div className="pp-head">
        <h2 className="site-display pp-title" id="we-title">Watch &amp; Earn</h2>
        <button type="button" className="we-sound" aria-pressed={sound} onClick={() => setSound(!sound)}
                title={sound ? "Reward sounds are on" : "Reward sounds are off"}>
          <span aria-hidden>{sound ? "🔊" : "🔇"}</span> {sound ? "Sound on" : "Sound off"}
        </button>
      </div>
      {list?.combo && (
        <p className="we-combo" role="status">
          🔥 <b>Watch combo</b> · {list.combo.today === 0 ? "1st video normal XP, 2nd double, 3rd+ double +5"
            : `${list.combo.today} watched today: next video pays ${list.combo.next === "double" ? "DOUBLE XP" : "DOUBLE XP +5"}`}
        </p>
      )}

      <article ref={feature} className={`we-feature${short ? " is-short" : ""}${rewarding ? " is-earning" : ""}${earned ? " is-earned" : ""}`}
               aria-labelledby="we-feature-title">
        <div className="we-stage">
          {playing && !blocked ? (
            <Player key={active.external_id} v={active}
                    onPlayed={(s) => void onPlayed(active, s)}
                    onUntracked={() => setUntracked(true)}
                    onRefused={() => {
                      setRefused((r) => new Set(r).add(active.external_id));
                      setPlaying(false);
                    }} />
          ) : (
            <button type="button" className={`we-poster${short ? " is-short" : ""}`} disabled={blocked}
                    onClick={() => watch(active)} aria-label={`Watch ${active.title}`}>
              {active.thumbnail && <img src={active.thumbnail} alt="" loading="lazy" decoding="async" />}
              {!blocked && <span className="we-play" aria-hidden>▶</span>}
              {dur && <span className="we-dur">{dur}</span>}
            </button>
          )}
          {rewarding && <span ref={xpEl} className={`we-reward is-${rewarding.stage}`} role="status">+{rewarding.xp} XP</span>}
        </div>

        <div className="we-info">
          <div className="we-kicker">
            <span>{kicker}</span>
            {earned ? (
              <span className="pp-mcard-reward is-earned">✓ Earned</span>
            ) : (earns || rewarding) && active.xp > 0 ? (
              <span className="pp-mcard-reward">+{active.xp} XP</span>
            ) : null}
          </div>
          <h3 className="we-title" id="we-feature-title">{active.title}</h3>
          {(dur || active.published_at) && <p className="we-meta">{[dur, date(active.published_at)].filter(Boolean).join(" · ")}</p>}

          <div className="we-status">
            {finished ? (
              <p className="we-done" role="status">✓ Completed</p>
            ) : earns && active.xp > 0 ? (
              playing && !untracked ? (
                <div className={`we-progress${rewarding ? " is-full" : ""}`}>
                  <p className="we-goal">Watch {need} seconds</p>
                  <div className="we-track" role="progressbar" aria-label="Seconds played here" aria-valuemin={0} aria-valuemax={need}
                       aria-valuenow={shownSecs}>
                    <span style={{ "--p": rewarding ? 1 : Math.min(1, played / need) } as React.CSSProperties} />
                  </div>
                  <p className="we-count">{shownSecs} / {need} sec</p>
                </div>
              ) : !playing ? (
                <p className="we-goal">Watch {need} seconds here · <strong>+{active.xp} XP</strong></p>
              ) : null
            ) : null}
            {finished?.note && <p className="we-note">{finished.note}</p>}
            {untracked && !finished && <p className="we-note">This browser blocked the player's progress, so this play can't earn XP.</p>}
            {blocked && <p className="we-note">This video can't play inside Price Chat. You can still watch it on YouTube.</p>}
          </div>

          <div className="we-actions">
            {!playing && !blocked && <button type="button" className="pp-go" onClick={() => watch(active)}>▶ Watch</button>}
            {videos.length > 1 && (
              <button type="button" className="we-next" onClick={() => watch(next)}>Next video <span aria-hidden>→</span></button>
            )}
            <a className={blocked ? "pp-go" : "we-yt"} href={active.url} target="_blank" rel="noopener noreferrer">Open on YouTube</a>
          </div>
        </div>
      </article>

      {videos.length > 1 && (
        <ul className="we-list" aria-label="Price Chat videos">
          {videos.map((v) => {
            const d = done[v.external_id] ?? (v.status === "completed" ? { xp: v.xp } : null);
            const isActive = v === active;
            const won = !!d && (d.xp > 0 || v.status === "completed");
            return (
              <li key={v.external_id}>
                <button type="button" className={`we-item${isActive ? " is-active" : ""}${won ? " is-earned" : ""}`}
                        aria-current={isActive || undefined} onClick={() => (isActive && playing ? undefined : watch(v))}>
                  <span className="we-item-thumb">
                    {v.thumbnail && <img src={v.thumbnail} alt="" loading="lazy" decoding="async" />}
                    {isActive ? (
                      <span className="we-item-now">{playing ? "▶ Playing" : "Selected"}</span>
                    ) : (
                      <span className="we-play we-play-sm" aria-hidden>▶</span>
                    )}
                    {duration(v.duration_seconds) && <span className="we-dur">{duration(v.duration_seconds)}</span>}
                  </span>
                  <span className="we-item-title">{v.title}</span>
                  <span className="we-item-meta">
                    {won ? <span className="we-item-done">✓ Earned</span>
                      : d ? <span className="we-item-seen">✓ Watched</span>
                      : v.status === "available" && v.xp > 0 ? <span className="we-item-xp">+{v.xp} XP</span> : null}
                    {date(v.published_at) && <span>{date(v.published_at)}</span>}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
