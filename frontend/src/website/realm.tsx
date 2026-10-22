import { useEffect, useRef, useState } from "react";
import { MemberApiError, memberApi, type Rank } from "./members";
import { RankKiwi } from "./RankKiwi";
import "./realm.css";

/**
 * Vortex Realm, the Warrior Kiwi reward: a 3D fantasy RPG (Godot 4, exported for the web) played right here, in the Passport's
 * Realm tab. The server gates the page AND every game file (/api/members/realm/play/...), so only Warrior Kiwi and up (or
 * the owner, from Admin) can load it. The game is only downloaded once the player presses Play (about 42 MB the first time,
 * then the browser keeps it). Saves stay in the player's browser; the game gives no Passport XP.
 */

interface Status {
  unlocked: boolean; available: boolean; needs: { name: string; min_xp: number }; online?: number;
  rank: { current: Rank; next: Rank | null; score: number; xp_to_next: number; progress: number } | null;
}

export const REALM_SRC = "/api/members/realm/play/index.html";

export function RealmTab() {
  const [s, setS] = useState<Status | null>(null);
  const [state, setState] = useState<"loading" | "signin" | "error" | "ok">("loading");
  useEffect(() => {
    memberApi<Status>("/members/realm")
      .then((r) => { setS(r); setState("ok"); })
      .catch((e) => setState(e instanceof MemberApiError && e.status === 401 ? "signin" : "error"));
  }, []);
  if (state === "loading") return <p className="site-empty">Loading Vortex Realm…</p>;
  if (state === "signin") return <p className="site-empty">Sign in to your Kiwi Passport to play.</p>;
  if (state === "error" || !s) return <p className="site-error">Couldn't reach Price Chat.</p>;
  if (!s.unlocked) return <RealmLocked s={s} />;
  if (!s.available) return <p className="site-empty">Vortex Realm is being updated: try again in a few minutes.</p>;
  return <RealmPlayer online={s.online} />;
}

function RealmLocked({ s }: { s: Status }) {
  const r = s.rank;
  const pct = r ? Math.round(Math.min(1, r.score / Math.max(1, s.needs.min_xp)) * 100) : 0;
  return (
    <section className="vr-gate">
      <h2>⚔️ Vortex Realm</h2>
      <p>A 3D fantasy RPG you play right here: create a hero, fight monsters, take on quests and collect loot.
        It unlocks at <b>{s.needs.name}</b> ({s.needs.min_xp.toLocaleString("en-GB")} XP).</p>
      {r && (
        <div className="vr-progress">
          <RankKiwi art={r.current.art} rankKey={r.current.key} size={56} />
          <div>
            <b>{r.current.name}</b> · {r.score.toLocaleString("en-GB")} / {s.needs.min_xp.toLocaleString("en-GB")} XP
            <div className="rw-bar" role="progressbar" aria-label={`Progress towards ${s.needs.name}`} aria-valuemin={0}
                 aria-valuemax={s.needs.min_xp} aria-valuenow={Math.min(r.score, s.needs.min_xp)}><i style={{ width: `${pct}%` }} /></div>
            <small>{Math.max(0, s.needs.min_xp - r.score).toLocaleString("en-GB")} XP to go</small>
          </div>
        </div>
      )}
      {/* the Passport tab on this same page: no reload, straight to today's missions */}
      <a className="site-button" href="#passport">See how to earn XP</a>
    </section>
  );
}

/** The game in a frame, started on demand, with full screen. Also used by Admin → Realm (test). */
export function RealmPlayer({ online }: { online?: number } = {}) {
  const [on, setOn] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLIFrameElement>(null);
  const full = () => {
    const el = box.current as (HTMLDivElement & { webkitRequestFullscreen?: () => void }) | null;
    if (!el) return;
    if (el.requestFullscreen) void el.requestFullscreen().then(() => {
      try { void (screen.orientation as ScreenOrientation & { lock?: (o: string) => Promise<void> }).lock?.("landscape").catch(() => undefined); }
      catch { /* not on this device */ }
    }).catch(() => window.open(REALM_SRC, "_blank"));
    else if (el.webkitRequestFullscreen) el.webkitRequestFullscreen();
    else window.open(REALM_SRC, "_blank");  // iPhone: no full screen for page elements, the game opens on its own page instead
    frame.current?.focus();
  };
  if (!on)
    return (
      <section className="vr-start">
        <h2>⚔️ Vortex Realm</h2>
        <p>Create a hero (Knight, Ranger or Sorceress), explore the world, fight monsters and the world boss, quest and loot.
          Your heroes are saved in this browser.</p>
        <p>🌐 <b>Online:</b> other Kiwis play in the same world. Press <b>Enter</b> to chat (start with <b>/p</b> for party chat) and{" "}
          <b>P</b> for the player list, where you can invite Kiwis to a party of up to 4. Party kills nearby share XP.
          {typeof online === "number" && <> <b>{online} Kiwi{online === 1 ? "" : "s"}</b> in the Realm right now.</>}</p>
        <button type="button" className="site-button vr-play" onClick={() => setOn(true)}>▶ Play</button>
        <small>The first start downloads about 42 MB. Best on a computer; on a phone, turn it sideways.</small>
        <small className="vr-keys">Keyboard: WASD move · mouse click attack · right-drag turn · Space jump · Q block · 1–4 skills · E use · I bag · M map · F1 all keys</small>
      </section>
    );
  return (
    <div className="vr-wrap">
      <div className="vr-bar">
        <span>⚔️ Vortex Realm</span>
        <button type="button" className="site-button" onClick={full}>⛶ Full screen</button>
      </div>
      <div className="vr-box" ref={box}>
        <iframe ref={frame} title="Vortex Realm" src={REALM_SRC} allow="fullscreen; autoplay; gamepad" allowFullScreen />
      </div>
    </div>
  );
}
