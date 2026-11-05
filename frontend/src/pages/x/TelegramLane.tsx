import { useState } from "react";
import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Badge, Card, relativeTime, type Tone } from "../../components/ui";

/** The News Desk's Telegram lane: its own switch, and every message Telegram confirmed. X's switches above don't touch it. */

type LanePost = { id: number; status: string; headline: string; source: string; message_id: number | null; url: string | null; error: string | null; created_at: string | null; delivered_at: string | null };
export type Lane = { enabled: boolean; max_per_day: number; gap_minutes: number; channel: string | null; problem: string | null; paused_until: string | null; last_run: { at?: string } | null; posts: LanePost[] };

const TONE: Record<string, Tone> = { delivered: "good", uncertain: "warn", publishing: "info", refused: "neutral" };
const WORD: Record<string, string> = { delivered: "Delivered", uncertain: "Check the channel", publishing: "Sending", refused: "Not sent" };
const put = (value: unknown): RequestInit => ({ method: "PUT", body: JSON.stringify(value), headers: { "Content-Type": "application/json" } });

export function TelegramLane() {
  const { data, reload } = useApi(() => request<Lane>("/x/newsdesk/telegram"), 30000);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (!data || data.enabled === undefined) return null;
  const save = async (change: Record<string, unknown>) => {
    setBusy(true); setError(null);
    try { await request("/x/newsdesk/telegram", put(change)); await reload(); } catch (e) { setError(e); } finally { setBusy(false); }
  };
  return (
    <Card title="Telegram">
      {error ? <ApiErrorBanner error={error} /> : null}
      <p className="muted">Verified crypto stories go to {data.channel ?? "the announcement channel"} through the Price Chat bot, silently, at most {data.max_per_day} a day and {data.gap_minutes} minutes apart. Separate from X: stopping one doesn't stop the other.</p>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <Badge tone={!data.enabled ? "neutral" : data.problem ? "warn" : "good"}>{!data.enabled ? "Off" : data.problem ? "Paused" : "On"}</Badge>
        <button className={data.enabled ? "btn btn-sm btn-danger" : "btn btn-sm"} disabled={busy} onClick={() => void save({ enabled: !data.enabled })}>{data.enabled ? "Stop posting to Telegram" : "Start posting to Telegram"}</button>
        {data.problem && <button className="btn btn-sm" disabled={busy} onClick={() => void save({ resume: true })}>Try again now</button>}
        {data.last_run?.at && <span className="muted small">Last checked {relativeTime(data.last_run.at)}</span>}
      </div>
      {data.problem && <p className="banner banner-warn" role="status">{data.problem}</p>}
      <div className="stack" style={{ marginTop: 12 }}>
        {(data.posts ?? []).slice(0, 12).map((p) => (
          <div key={p.id} className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <Badge tone={TONE[p.status] ?? "neutral"}>{WORD[p.status] ?? p.status}</Badge>
            {p.url ? <a href={p.url} target="_blank" rel="noreferrer">{p.headline}</a> : <span>{p.headline}</span>}
            <span className="muted small">{p.message_id ? `Message ${p.message_id}` : p.error ?? ""}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}
