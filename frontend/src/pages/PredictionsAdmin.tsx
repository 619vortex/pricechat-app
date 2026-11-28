import { useState } from "react";
import { request } from "../api/client";
import { useApi } from "../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Loading, PageHeader, type Tone } from "../components/ui";

/** Admin › Community › Predictions: every call with its votes (by source), settlement evidence and audit trail; create a call for a day,
 *  resolve a held call by hand (with a logged reason), cancel (void: no XP, XP already written is reversed). The feature flag
 *  (PREDICTIONS_MODE off | preview | live) is a Railway variable: this page shows it but never changes it. */

type Q = {
  id: number; kind: string; title: string; status: string; outcome: string | null; ref_price: number | null; settle_price: number | null;
  settle_day: string; opens_at: string; locks_at: string; settles_at: string; calls: number; split: Record<string, number>;
  options: { key: string; label: string }[]; evidence: Record<string, unknown>; audit: { at: string; who: string; what: string; reason?: string }[];
  tg_chat: string; tg_message_id: number | null; votes_by: Record<string, number>; void_reason: string; created_by: string;
};
type List = { questions: Q[]; mode: string; group: string; preview_chat: boolean; preview_members: string[]; rules: Record<string, string>; players_week: number };

const TONE: Record<string, Tone> = { open: "good", locked: "info", held: "warn", settled: "neutral", void: "bad", scheduled: "info" };
const usd = (v: number | null) => (v == null ? "—" : `$${Math.round(v).toLocaleString("en-US")}`);
const t = (iso: string) => iso.slice(5, 16).replace("T", " ");

function Row({ q, onChanged }: { q: Q; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [close, setClose] = useState("");
  const [reason, setReason] = useState("");
  async function post(path: string, body: unknown) {
    setBusy(true); setErr(null);
    try { await request(`/predictions/questions/${q.id}/${path}`, { method: "POST", body: JSON.stringify(body) }); onChanged(); }
    catch (e) { setErr(e); } finally { setBusy(false); }
  }
  const label = (k: string | null) => q.options.find((o) => o.key === k)?.label ?? k ?? "—";
  return (
    <Card title={<span>#{q.id} {q.title} <span className="muted small">· {q.settle_day}</span></span>} actions={<Badge tone={TONE[q.status] ?? "neutral"}>{q.status}</Badge>}>
      <div className="stack small">
        <div>Opens {t(q.opens_at)} · locks {t(q.locks_at)} · settles {t(q.settles_at)} UTC · reference {usd(q.ref_price)}</div>
        <div><b>{q.calls}</b> calls: {q.options.map((o) => `${o.label} ${q.split[o.key] ?? 0}`).join(" · ")} · by source: {Object.entries(q.votes_by).map(([k, v]) => `${k} ${v}`).join(", ") || "none"}</div>
        {q.status === "settled" && <div>Result: <b>{label(q.outcome)}</b> at {usd(q.settle_price)} ({String(q.evidence.how ?? "")}{q.evidence.reason ? `: ${String(q.evidence.reason)}` : ""})</div>}
        {q.status === "void" && <div>Cancelled: {q.void_reason}</div>}
        <div className="muted">Group post: {q.tg_message_id ? `${q.tg_chat} #${q.tg_message_id}` : "not posted"}</div>
        <details><summary>Settlement evidence and audit</summary>
          <pre className="small" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(q.evidence, null, 1)}</pre>
          <ul>{q.audit.map((a, i) => <li key={i}>{t(a.at)} · {a.who} · {a.what}{a.reason ? `: ${a.reason}` : ""}</li>)}</ul>
        </details>
        <a href={`/api/predictions/questions/${q.id}/card.png`} target="_blank" rel="noreferrer">The group post's image</a>
        <ApiErrorBanner error={err} />
        {q.status !== "void" && q.status !== "settled" && (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            {(q.status === "held" || q.status === "locked") && <>
              <input aria-label="Checked close" placeholder="Close you checked, e.g. 61234.5" value={close} onChange={(e) => setClose(e.target.value)} />
              <button className="btn" disabled={busy || !close || reason.trim().length < 5} onClick={() => post("resolve", { close: Number(close), reason })}>Resolve by hand</button>
            </>}
            <input aria-label="Reason" placeholder="Reason (logged and shown)" value={reason} onChange={(e) => setReason(e.target.value)} style={{ minWidth: 220 }} />
            <button className="btn" disabled={busy || reason.trim().length < 5} onClick={() => post("cancel", { reason })}>Cancel (void)</button>
          </div>
        )}
        {q.status === "settled" && (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <input aria-label="Reason" placeholder="Reason to cancel a settled call" value={reason} onChange={(e) => setReason(e.target.value)} style={{ minWidth: 220 }} />
            <button className="btn" disabled={busy || reason.trim().length < 5} onClick={() => post("cancel", { reason })}>Cancel and reverse XP</button>
          </div>
        )}
      </div>
    </Card>
  );
}

export function PredictionsAdminPage() {
  const { data, failure, loading, reload } = useApi(() => request<List>("/predictions/questions?limit=60"), 30_000);
  const [kind, setKind] = useState<"daily" | "weekly">("daily");
  const [day, setDay] = useState(() => new Date().toISOString().slice(0, 10));
  const [err, setErr] = useState<unknown>(null);
  async function create() {
    setErr(null);
    try { await request("/predictions/questions", { method: "POST", body: JSON.stringify({ kind, day }) }); await reload(); } catch (e) { setErr(e); }
  }
  return (
    <div className="stack">
      <PageHeader title="Predictions" subtitle="Free daily and weekly Bitcoin calls for Predictor XP (never rank or money). Telegram and the Passport share one vote." />
      {!data ? (loading ? <Loading /> : <ApiErrorBanner error={failure} />) : (
        <>
          <Card title="Switch" actions={<Badge tone={data.mode === "live" ? "good" : data.mode === "preview" ? "warn" : "neutral"}>{data.mode}</Badge>}>
            <div className="small stack">
              <div>PREDICTIONS_MODE is <b>{data.mode}</b>. Group: {data.group}. {data.mode === "preview" ? `Preview posts go to the test chat${data.preview_chat ? "" : " (not set: nothing posts)"}; the Passport tab shows to ${data.preview_members.join(", ") || "nobody"}.` : ""}</div>
              <div className="muted">{data.players_week} players on this week's board. The switch is a Railway variable: changing it redeploys.</div>
            </div>
          </Card>
          <Card title="Create a call for a day">
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }} className="small">
              <select aria-label="Kind" value={kind} onChange={(e) => setKind(e.target.value as "daily" | "weekly")}><option value="daily">Daily</option><option value="weekly">Weekly (a Monday)</option></select>
              <input aria-label="Day" type="date" value={day} onChange={(e) => setDay(e.target.value)} />
              <button className="btn primary" onClick={create}>Create</button>
              <span className="muted">The schedule creates them itself; use this for a missed day or a test. The reference close is read from Hyperliquid.</span>
            </div>
            <ApiErrorBanner error={err} />
          </Card>
          {data.questions.length === 0 ? <Empty>No calls yet.</Empty> : data.questions.map((q) => <Row key={`${q.id}-${q.status}`} q={q} onChanged={reload} />)}
          <details><summary className="small">Rules members see</summary><ul className="small">{Object.values(data.rules).map((r) => <li key={r}>{r}</li>)}</ul></details>
        </>
      )}
    </div>
  );
}
