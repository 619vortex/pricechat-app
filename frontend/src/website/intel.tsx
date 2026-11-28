import { useCallback, useEffect, useRef, useState } from "react";
import { memberApi } from "./members";
import "./intel.css";

/** Passport › Intelligence (COMMUNITY_INTEL_MODE): send Price Chat a tip, follow what happens to it, appeal a decision, and see the
 *  Researcher boards. An editor decides every tip; nothing is published automatically. Tips earn community reputation and Researcher
 *  badges only: never XP or money. The quickest way is Telegram: paste a link or forward a post to the bot. */

export type Tip = {
  id: number; ref: string; kind: string; kind_label: string; title: string; note: string; url: string; host: string; via: string; status: string; stage: number;
  verified: boolean; verified_by: string | null; source: { class: string; label: string; outlet: string } | null; claim: string | null;
  dup: { kind?: string; ref?: string }; reason_label: string | null; editor_note: string | null; published_at: string | null; first_spotted: boolean;
  credit: boolean; disclosure: string | null; created_at: string | null; appeal: { status: string; text: string } | null; can_appeal: boolean;
  can_withdraw: boolean; can_answer: boolean;
};
export type Record_ = { sent: number; accepted: number; published: number; first_spotted: number; duplicates: number; rejected: number;
  acceptance: number | null; acceptance_after: number; median_hours: number | null; points: number };
export type IntelData = { tips: Tip[]; record: Record_; limit: { per_day: number; sent_today: number; level: string }; kinds: { key: string; label: string }[];
  points: Record<string, number>; bot: string };
type Check = { source: { class: string; label: string; outlet: string }; kind: string; tickers: string[]; flags: string[]; duplicate?: { mine: boolean; ref: string | null } };
type Board = { period: string; rows: { position: number; username: string; name: string; points: number; accepted: number; published: number; first_spotted: number }[];
  me: { position: number; points: number } | null };

export function useIntel(): [IntelData | null | false, () => Promise<void>] {
  const [data, setData] = useState<IntelData | null | false>(null);
  const load = useCallback(async () => { try { setData(await memberApi<IntelData>("/members/intel")); } catch { setData(false); } }, []);
  useEffect(() => { void load(); }, [load]);
  return [data, load];
}

const STATUS: Record<string, [string, string]> = {
  checking: ["Waiting for your answer", "wait"], review: ["With the desk", "wait"], needs_info: ["The desk has a question", "ask"], accepted: ["Accepted", "ok"],
  merged: ["Added to a story", "ok"], duplicate: ["Already sent", "dup"], rejected: ["Not accepted", "no"], withdrawn: ["Withdrawn", "dup"],
};
const STEPS = ["Sent", "Checked", "With the desk", "Decided", "Published"];
const SRC_TONE: Record<string, string> = { official: "ok", outlet: "ok", market: "info", social: "warn", unknown: "warn", shortener: "bad", invalid: "bad", none: "warn" };

export function IntelTab({ data, reload }: { data: IntelData; reload: () => Promise<void> }) {
  const r = data.record;
  return (
    <div className="ci2" data-testid="intel">
      <header className="ci2-hero">
        <div>
          <p className="site-label">Community Intelligence</p>
          <h2 className="site-display ci2-title">Send the desk a tip</h2>
          <p className="ci2-lede">News, research, market anomalies and discoveries. An editor reads every tip and checks it against independent sources.
            Nothing is published automatically. Good tips earn community reputation and Researcher badges.</p>
        </div>
        <div className="ci2-fast" aria-label="The fastest way">
          <span className="ci2-tg" aria-hidden="true">✈</span>
          <div><b>Fastest: Telegram</b><span>Paste a link or forward a post to {data.bot} in a private chat. That's it.</span></div>
        </div>
      </header>

      <div className="ci2-record" aria-label="Your track record">
        <div><b>{r.sent}</b><span>Sent</span></div>
        <div><b>{r.accepted}</b><span>Accepted</span></div>
        <div><b>{r.published}</b><span>Published</span></div>
        <div><b>{r.first_spotted}</b><span>First spotted</span></div>
        <div><b>{r.acceptance == null ? "—" : `${Math.round(r.acceptance * 100)}%`}</b><span>{r.acceptance == null ? `Rate after ${r.acceptance_after} decided` : "Accepted rate"}</span></div>
        <div><b className="ci2-pts">{r.points > 0 ? "+" : ""}{r.points}</b><span>Community score</span></div>
      </div>

      <TipForm data={data} onSent={reload} />
      <MyTips tips={data.tips} reload={reload} />
      <Boards />
      <details className="ci2-rules"><summary>How tips are scored</summary>
        <ul>
          <li>Sending a tip earns nothing by itself. A tip scores when an editor accepts it <b>and</b> an independent source confirms it: +{data.points.accepted}, or +{data.points.research} for research whose data an editor checked.</li>
          <li>+{data.points.published} more if a story built on it is published. "First spotted" when your tip came before every outlet the desk follows.</li>
          <li>Up to 3 accepted tips count per day, and one per story: sending the same story twice doesn't count twice. The first person to send a link keeps the credit.</li>
          <li>A tip rejected as spam or misleading costs {Math.abs(data.points.spam)}. You can appeal any rejection once, within 14 days.</li>
          <li>Reputation and badges only: never XP, money or rewards with money value.</li>
        </ul>
      </details>
    </div>
  );
}

function TipForm({ data, onSent }: { data: IntelData; onSent: () => Promise<void> }) {
  const [url, setUrl] = useState("");
  const [note, setNote] = useState("");
  const [kind, setKind] = useState<string | null>(null);
  const [disclosure, setDisclosure] = useState<"none" | "holds" | null>(null);
  const [credit, setCredit] = useState(true);
  const [check, setCheck] = useState<Check | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => {
    window.clearTimeout(timer.current);
    if (!url.trim() && !note.trim()) { setCheck(null); return; }
    timer.current = window.setTimeout(() => {
      void memberApi<Check>("/members/intel/check", { method: "POST", body: JSON.stringify({ url, note }) }).then(setCheck).catch(() => undefined);
    }, 350);
    return () => window.clearTimeout(timer.current);
  }, [url, note]);
  const left = Math.max(0, data.limit.per_day - data.limit.sent_today);
  const shown = kind ?? check?.kind ?? "news";
  async function send() {
    if (!disclosure) { setMsg({ ok: false, text: "Answer the position question first." }); return; }
    setBusy(true); setMsg(null);
    try {
      const r = await memberApi<{ tip: Tip }>("/members/intel", { method: "POST", body: JSON.stringify({ url, note, kind: shown, disclosure, credit }) });
      setMsg({ ok: true, text: `Sent ✓ ${r.tip.ref}. The desk has it; you'll hear back here and in Telegram.` });
      setUrl(""); setNote(""); setKind(null); setDisclosure(null); setCheck(null);
      await onSent();
    } catch (e) { setMsg({ ok: false, text: e instanceof Error ? e.message : "That didn't send. Try again." }); } finally { setBusy(false); }
  }
  const dup = check?.duplicate;
  return (
    <form className="ci2-form" onSubmit={(e) => { e.preventDefault(); void send(); }} aria-label="Send a tip">
      <div className="ci2-form-main">
        <div className="ci2-f"><label htmlFor="ci2-url">Link</label>
          <input id="ci2-url" type="url" inputMode="url" placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} /></div>
        <div className="ci2-f"><label htmlFor="ci2-note">Why it matters <span>(optional)</span></label>
          <textarea id="ci2-note" maxLength={600} placeholder="One or two lines: what happened, and any figure with its asset, like $BTC funding −0.05%/h" value={note} onChange={(e) => setNote(e.target.value)} /></div>
        <fieldset className="ci2-f"><legend>Type</legend>
          <div className="ci2-kinds">{data.kinds.map((k) => (
            <button key={k.key} type="button" className="ci2-chip" aria-pressed={shown === k.key} onClick={() => setKind(k.key)}>{k.label}</button>))}</div>
        </fieldset>
        <fieldset className="ci2-f"><legend>Do you hold a position in what this is about? <span>Required · only the editor sees it</span></legend>
          <div className="ci2-kinds">
            <button type="button" className="ci2-chip" aria-pressed={disclosure === "none"} onClick={() => setDisclosure("none")}>No position</button>
            <button type="button" className="ci2-chip" aria-pressed={disclosure === "holds"} onClick={() => setDisclosure("holds")}>I hold a position</button>
          </div>
        </fieldset>
        <label className="ci2-tick"><input type="checkbox" checked={credit} onChange={(e) => setCredit(e.target.checked)} /> Credit me as "Spotted by" if a story built on this is published</label>
        <div className="ci2-send">
          <button type="submit" className="site-button" disabled={busy || left === 0 || (!url.trim() && !note.trim()) || !!dup?.mine}>{busy ? "Sending…" : "Send to the desk"}</button>
          <span className="ci2-hint">{left} of {data.limit.per_day} left today ({data.limit.level})</span>
        </div>
        {msg && <p className={msg.ok ? "ci2-ok" : "site-error"} role={msg.ok ? "status" : "alert"}>{msg.text}</p>}
      </div>
      <aside className="ci2-live" aria-live="polite" aria-label="Checks">
        <h3>Checks</h3>
        {!check ? <p className="ci2-hint">Paste a link to see how the desk will read it.</p> : (
          <ul>
            <li className={`is-${SRC_TONE[check.source.class] ?? "warn"}`}><b>{check.source.label}{check.source.outlet ? ` · ${check.source.outlet}` : ""}</b>
              <span>{check.source.class === "official" ? "A primary source: the strongest kind." : check.source.class === "outlet" ? "An outlet the desk follows." :
                check.source.class === "social" ? "Treated as a rumour until an outlet or official source confirms it." : check.source.class === "market" ? "Figures are re-read from live data." :
                check.source.class === "shortener" ? "Shortened links can't be checked: paste where it leads." : check.source.class === "invalid" ? "Use a full https:// address." :
                "Unverified until another source backs it."}</span></li>
            {check.tickers.length > 0 && <li className="is-info"><b>{check.tickers.map((t) => `$${t}`).join(" ")}</b><span>Any figures are checked against live market data.</span></li>}
            {dup && <li className={dup.mine ? "is-bad" : "is-info"}><b>{dup.mine ? `You already sent this (${dup.ref})` : "Someone sent this link first"}</b>
              <span>{dup.mine ? "One tip per link." : "The first sender keeps the credit; you'd be \"also spotted\"."}</span></li>}
            {check.flags.map((f) => <li key={f} className="is-bad"><b>{f}</b><span>The editor sees this flag.</span></li>)}
          </ul>
        )}
      </aside>
    </form>
  );
}

function MyTips({ tips, reload }: { tips: Tip[]; reload: () => Promise<void> }) {
  if (tips.length === 0) return <p className="ci2-empty">Your tips will appear here, with every step the desk takes.</p>;
  return (
    <section className="ci2-section" aria-labelledby="ci2-mine"><h3 id="ci2-mine">Your tips</h3>
      <ul className="ci2-tips">{tips.map((t) => <TipRow key={t.id} t={t} reload={reload} />)}</ul>
    </section>
  );
}

function TipRow({ t, reload }: { t: Tip; reload: () => Promise<void> }) {
  const [mode, setMode] = useState<"" | "appeal" | "answer">("");
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  const [label, cls] = t.published_at ? ["Published", "pub"] : STATUS[t.status] ?? [t.status, "wait"];
  const rejected = t.status === "rejected" || t.status === "duplicate";
  async function go(path: string, body: unknown) {
    setErr("");
    try { await memberApi(path, { method: "POST", body: JSON.stringify(body) }); setMode(""); setText(""); await reload(); }
    catch (e) { setErr(e instanceof Error ? e.message : "That didn't go through."); }
  }
  return (
    <li className="ci2-tip">
      <div className="ci2-tip-h"><b>{t.title || t.host || "Forwarded post"}</b><span className={`ci2-state is-${cls}`}>{label}</span></div>
      <div className="ci2-steps" aria-label={`Step ${t.stage + 1} of 5`}>{STEPS.map((s, i) => (
        <i key={s} className={i <= t.stage ? (rejected && i === 3 ? "no" : "on") : ""} title={s} />))}</div>
      <p className="ci2-tip-m">
        <span>{t.ref} · {t.kind_label} · via {t.via === "telegram" ? "Telegram" : "Passport"}</span>
        <span>{t.verified ? `Confirmed: ${t.verified_by}` : "Not independently confirmed yet"}</span>
        {t.first_spotted && <span className="ci2-first">⚡ First spotted</span>}
      </p>
      {t.reason_label && <p className="ci2-why">Reason: {t.reason_label}</p>}
      {t.dup.kind === "tip" && t.status === "duplicate" && <p className="ci2-why">Another member sent this first ({t.dup.ref}). You're credited as "also spotted".</p>}
      {t.editor_note && <p className="ci2-editor">Editor: {t.editor_note}</p>}
      {t.appeal && <p className="ci2-why">Appeal {t.appeal.status === "open" ? "waiting for the desk" : t.appeal.status === "upheld" ? "upheld" : "not upheld"}.</p>}
      <div className="ci2-tip-a">
        {t.can_answer && <button type="button" className="site-textbutton" onClick={() => setMode(mode === "answer" ? "" : "answer")}>Answer the desk</button>}
        {t.can_appeal && <button type="button" className="site-textbutton" onClick={() => setMode(mode === "appeal" ? "" : "appeal")}>Appeal</button>}
        {t.can_withdraw && <button type="button" className="site-textbutton" onClick={() => void go(`/members/intel/${t.id}/withdraw`, {})}>Withdraw</button>}
      </div>
      {mode && (
        <div className="ci2-reply">
          <label htmlFor={`ci2-r-${t.id}`}>{mode === "appeal" ? "Why should the decision change?" : "Your answer"}</label>
          <textarea id={`ci2-r-${t.id}`} maxLength={mode === "appeal" ? 400 : 600} value={text} onChange={(e) => setText(e.target.value)} />
          <button type="button" className="site-button" disabled={!text.trim()}
                  onClick={() => void go(`/members/intel/${t.id}/${mode}`, mode === "appeal" ? { text } : { text })}>{mode === "appeal" ? "Send appeal" : "Send answer"}</button>
          {mode === "appeal" && <span className="ci2-hint">One appeal per tip, within 14 days. An upheld appeal removes any penalty.</span>}
        </div>
      )}
      {err && <p className="site-error" role="alert">{err}</p>}
    </li>
  );
}

function Boards() {
  const [period, setPeriod] = useState<"week" | "month" | "all">("week");
  const [b, setB] = useState<Board | null>(null);
  useEffect(() => { void memberApi<Board>(`/members/intel/leaderboard?period=${period}`).then(setB).catch(() => setB(null)); }, [period]);
  return (
    <section className="ci2-section" aria-labelledby="ci2-boards">
      <div className="ci2-board-h"><h3 id="ci2-boards">Top researchers</h3>
        <div className="ci2-seg" role="tablist" aria-label="Period">{(["week", "month", "all"] as const).map((p) => (
          <button key={p} type="button" role="tab" aria-selected={period === p} onClick={() => setPeriod(p)}>{p === "week" ? "This week" : p === "month" ? "This month" : "All time"}</button>))}</div>
      </div>
      {!b || !Array.isArray(b.rows) || b.rows.length === 0 ? <p className="ci2-empty">No accepted tips {period === "all" ? "yet" : `this ${period}`}. Be the first.</p> : (
        <ol className="ci2-board">{b.rows.slice(0, 20).map((r) => (
          <li key={r.username} className={b.me && b.me.position === r.position ? "is-me" : ""}>
            <span className="ci2-pos">{r.position}</span>
            <a className="ci2-name" href={`/u/${encodeURIComponent(r.username)}`}>{r.name}</a>
            <span className="ci2-meta">{r.accepted} accepted · {r.published} published{r.first_spotted ? ` · ${r.first_spotted} first` : ""}</span>
            <b className="ci2-pts">{r.points}</b>
          </li>))}</ol>
      )}
      {b?.me && b.me.position > 20 && <p className="ci2-hint">You're #{b.me.position} with {b.me.points} points.</p>}
    </section>
  );
}
