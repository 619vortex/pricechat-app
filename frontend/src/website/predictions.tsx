import { useCallback, useEffect, useState } from "react";
import { SitePage } from "./community";
import { memberApi } from "./members";
import "./predictions.css";

/** Kiwi Passport › Predictions: today's Bitcoin call, the weekly call, the member's record (accuracy, streak, Predictor level),
 *  week / month / all-time boards and the shareable card. One backend with Telegram: a call made in the group is the same call here.
 *  Free game for XP: Predictor XP never counts towards the Kiwi rank or any reward with money value. */

export type PredOption = { key: string; label: string; lo?: number | null; hi?: number | null };
export type PredQuestion = {
  id: number; kind: "daily" | "weekly"; type: "direction" | "range"; asset: string; title: string; options: PredOption[];
  ref_price: number | null; settle_day: string; opens_at: string; locks_at: string; settles_at: string;
  status: "open" | "locked" | "held" | "settled" | "void"; outcome: string | null; settle_price: number | null; void_reason: string;
  calls: number; split: Record<string, number> | null; my_call: string | null; xp: { play: number; right: number }; source: string;
};
export type PredLevel = { level: number; name: string; xp: number; floor: number; next_at: number | null; next_name: string | null };
export type PredStats = {
  calls: number; decided: number; right: number; accuracy: number | null; accuracy_after: number; streak: number; best_streak: number;
  xp: number; pending_xp: number; level: PredLevel; week_position: number | null; week_players: number; linked: boolean;
};
export type PredHistory = { question_id: number; kind: string; title: string; settle_day: string; call: string; outcome: string | null;
  result: "right" | "wrong" | "tie" | "void" | null; status: string; settle_price: number | null; via: string };
export type PredOverview = { open: PredQuestion[]; recent: PredQuestion[]; signed_in: boolean; rules: Record<string, string>;
  stats?: PredStats; history?: PredHistory[] };
type Board = { period: string; min_calls: number; players: number;
  rows: { position: number; name: string; points: number; right: number; decided: number; calls: number; accuracy: number }[];
  me: { position: number; points: number; right: number; decided: number; accuracy: number } | null };

const usd = (v: number | null | undefined) => (v == null ? "—" : `$${Math.round(v).toLocaleString("en-US")}`);
const pct = (v: number | null | undefined) => (v == null ? "—" : `${Math.round(v * 100)}%`);

/** null while loading, false when Predictions isn't available to this visitor (off, or preview for others). */
export function usePredictions(): [PredOverview | null | false, () => Promise<void>] {
  const [data, setData] = useState<PredOverview | null | false>(null);
  const load = useCallback(async () => {
    try { setData(await memberApi<PredOverview>("/members/predictions")); } catch { setData(false); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  return [data, load];
}

function Countdown({ to }: { to: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  const ms = Math.max(0, new Date(to).getTime() - now);
  const h = Math.floor(ms / 3.6e6), m = Math.floor((ms % 3.6e6) / 6e4), s = Math.floor((ms % 6e4) / 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return <span className="pd-count" aria-label={`${h} hours ${m} minutes left`}><b>{pad(h)}</b>h <b>{pad(m)}</b>m <b>{pad(s)}</b>s</span>;
}

export const TELEGRAM_GROUP = "https://t.me/cryptopricechat";

function CallCard({ q, signedIn, onVoted }: { q: PredQuestion; signedIn: boolean; onVoted: (q: PredQuestion) => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState("");
  async function vote(option: string) {
    setBusy(option); setErr("");
    try {
      const r = await memberApi<{ question: PredQuestion }>(`/members/predictions/${q.id}/vote`, { method: "POST", body: JSON.stringify({ option }) });
      onVoted(r.question);
    } catch (e) { setErr(e instanceof Error ? e.message : "That didn't go through. Try again."); } finally { setBusy(null); }
  }
  const direction = q.type === "direction";
  return (
    <article className={`pd-card${q.my_call ? " is-called" : ""}`} data-testid={`call-${q.kind}`}>
      <div className="pd-eyebrow">{q.kind === "daily" ? "Daily call" : "Weekly call"} · locks in <Countdown to={q.locks_at} /></div>
      <h3 className="pd-q">{q.title}</h3>
      <p className="pd-ref">vs {usd(q.ref_price)} · {q.source}</p>
      {!signedIn ? (
        <div className="pd-guest">
          <a className="site-button" href={TELEGRAM_GROUP} target="_blank" rel="noopener noreferrer">Play in Telegram</a>
          <a className="site-textbutton" href="/join">or sign in to play here</a>
          <p className="pd-small">No sign-up needed: tap {q.options.map((o) => o.label).join(" or ")} under today's post in @cryptopricechat.</p>
        </div>
      ) : (
      <div className={`pd-options ${direction ? "is-two" : "is-four"}`}>
        {q.options.map((o) => (
          <button key={o.key} type="button" className={`pd-opt pd-${o.key}${q.my_call === o.key ? " is-mine" : ""}`} disabled={!!busy}
            aria-pressed={q.my_call === o.key} onClick={() => vote(o.key)}>
            {o.key === "up" ? "▲ " : o.key === "down" ? "▼ " : ""}{o.label}{q.my_call === o.key ? " ✓" : ""}
          </button>
        ))}
      </div>
      )}
      <p className="pd-small">{q.my_call ? `Your call is in. Change it until the lock. ` : ""}+{q.xp.play} XP for calling it, +{q.xp.right} more if right ·
        {" "}{q.calls.toLocaleString()} call{q.calls === 1 ? "" : "s"} so far · split shown at the lock</p>
      {err && <p className="site-error" role="alert">{err}</p>}
    </article>
  );
}

function Result({ q }: { q: PredQuestion }) {
  const label = q.options.find((o) => o.key === q.outcome)?.label ?? (q.outcome === "tie" ? "Tie" : "");
  return (
    <div className="pd-result">
      <span>{q.title}</span>
      {q.status === "settled" ? <b className={q.outcome === "down" ? "is-down" : "is-up"}>{label} · {usd(q.settle_price)}</b>
        : q.status === "void" ? <b className="is-void">Cancelled</b> : <b className="is-wait">{q.status === "held" ? "Being checked" : "Locked"}</b>}
      {q.split && <span className="pd-split">{q.options.map((o) => `${o.label} ${pct(q.split?.[o.key])}`).join(" · ")}</span>}
    </div>
  );
}

function Boards() {
  const [period, setPeriod] = useState<"week" | "month" | "all">("week");
  const [board, setBoard] = useState<Board | null>(null);
  useEffect(() => { setBoard(null); memberApi<Board>(`/members/predictions/leaderboard?period=${period}`).then(setBoard).catch(() => setBoard(null)); }, [period]);
  return (
    <section className="pd-section" aria-labelledby="pd-board">
      <div className="pd-head"><h3 id="pd-board">Leaderboard</h3>
        <div className="pd-seg" role="tablist" aria-label="Period">
          {(["week", "month", "all"] as const).map((p) => <button key={p} type="button" role="tab" aria-selected={period === p} onClick={() => setPeriod(p)}>
            {p === "week" ? "Week" : p === "month" ? "Month" : "All time"}</button>)}
        </div></div>
      {!board ? <p className="pd-small">Loading…</p> : board.rows.length === 0 ? (
        <p className="pd-small">Nobody has {board.min_calls} settled calls {period === "all" ? "yet" : `this ${period}`}. You need {board.min_calls} to appear.</p>
      ) : (
        <ol className="pd-board">
          {board.rows.slice(0, 10).map((r) => (
            <li key={r.position} className={board.me?.position === r.position ? "is-me" : ""}>
              <span className="pd-pos">{r.position}</span><span className="pd-name">{r.name}</span>
              <span className="pd-pts">{r.right}/{r.decided} · {r.points} pts</span>
            </li>
          ))}
          {board.me && board.me.position > 10 && <li className="is-me"><span className="pd-pos">{board.me.position}</span><span className="pd-name">You</span>
            <span className="pd-pts">{board.me.right}/{board.me.decided} · {board.me.points} pts</span></li>}
        </ol>
      )}
      <p className="pd-small">Ranked by prediction points (10 a right daily call, 38 a right weekly call, +5 against the crowd), then accuracy.
        Minimum {board?.min_calls ?? 3} settled calls. Calendar periods, UTC.</p>
    </section>
  );
}

export function PredictionsTab({ data, reload }: { data: PredOverview; reload: () => Promise<void> }) {
  const [open, setOpen] = useState(data.open);
  useEffect(() => setOpen(data.open), [data.open]);
  const st = data.stats;
  const lvl = st?.level;
  const progress = lvl && lvl.next_at ? Math.min(1, (lvl.xp - lvl.floor) / (lvl.next_at - lvl.floor)) : 1;
  return (
    <div className="pd" data-testid="predictions">
      <header className="pd-hero">
        <div><p className="pd-eyebrow">Price Chat Predictions</p><h2 className="pd-title">Call the close</h2>
          <p className="pd-lede">One free Bitcoin call a day, one a week. Same call here and in the Telegram group.</p></div>
        {lvl && <div className="pd-level" aria-label={`Predictor level ${lvl.level}: ${lvl.name}`}>
          <span className="pd-level-n">{lvl.name}</span>
          <span className="pd-bar"><i style={{ width: `${Math.round(progress * 100)}%` }} /></span>
          <span className="pd-small">{lvl.xp} Predictor XP{lvl.next_at ? ` · ${lvl.next_at - lvl.xp} to ${lvl.next_name}` : " · top level"}</span>
        </div>}
      </header>

      {open.length === 0 ? <p className="pd-empty">No call is open right now. The daily call opens at 13:30 UTC (17:30 Dubai).</p> :
        open.map((q) => <CallCard key={q.id} q={q} signedIn={data.signed_in} onVoted={(nq) => { setOpen((cur) => cur.map((x) => (x.id === nq.id ? nq : x))); void reload(); }} />)}

      {st && (
        <div className="pd-stats">
          <div><b className="is-green">{st.accuracy == null ? "—" : pct(st.accuracy)}</b><span>Accuracy{st.accuracy == null ? ` (after ${st.accuracy_after})` : ""}</span></div>
          <div><b>{st.streak}</b><span>Streak · best {st.best_streak}</span></div>
          <div><b>{st.week_position ? `#${st.week_position}` : "—"}</b><span>This week</span></div>
        </div>
      )}
      {st && st.pending_xp > 0 && <p className="pd-note">{st.pending_xp} XP pending: it is written when your Passport is a day old.</p>}

      {data.recent.length > 0 && <section className="pd-section"><h3>Latest results</h3>{data.recent.slice(0, 4).map((q) => <Result key={q.id} q={q} />)}</section>}

      {data.history && data.history.length > 0 && (
        <section className="pd-section"><h3>Your calls</h3>
          <ul className="pd-hist">{data.history.slice(0, 10).map((h) => (
            <li key={h.question_id}><span>{h.settle_day.slice(5)} · {h.kind === "daily" ? "Daily" : "Weekly"} · {h.call}</span>
              <b className={`is-${h.result ?? "wait"}`}>{h.result === "right" ? "✓ Right" : h.result === "wrong" ? "✗" : h.result === "tie" ? "Tie" : h.result === "void" ? "Cancelled" : "Waiting"}</b></li>
          ))}</ul></section>
      )}

      <Boards />

      {st && st.calls > 0 && (
        <section className="pd-section pd-share"><h3>Share your week</h3>
          <img src="/api/members/predictions/card.png?fmt=portrait" alt="Your Price Chat Predictions card for this week" loading="lazy" />
          <div className="pd-share-row">
            <a className="site-button" href="/api/members/predictions/card.png?fmt=portrait" download="price-chat-predictions.png">Download for Stories</a>
            <a className="site-textbutton" href="/api/members/predictions/card.png?fmt=landscape" download="price-chat-predictions-x.png">Landscape for X</a>
          </div></section>
      )}

      <details className="pd-rules"><summary>Rules</summary>
        <ul>{Object.entries(data.rules).map(([k, v]) => <li key={k}>{v}</li>)}</ul></details>
    </div>
  );
}

/** /predictions: the public entry point. Anyone can see today's call; playing needs no Passport (Telegram), a Passport adds the site. */
export function PredictionsPage() {
  const [data, reload] = usePredictions();
  return (
    <SitePage title="Predictions" description="One free Bitcoin call a day: will it close higher or lower? Play for XP in Telegram or on the site." path="/predictions">
      {data === null ? <p className="site-empty">Loading…</p>
        : data === false ? <p className="site-empty">Predictions aren't open right now.</p>
        : <PredictionsTab data={data} reload={reload} />}
    </SitePage>
  );
}
