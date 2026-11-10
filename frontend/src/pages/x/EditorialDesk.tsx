import { useState } from "react";
import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, relativeTime } from "../../components/ui";

/** Admin › X › News Desk: the editorial desk (owner, 2026-11-10). The next slot's preview queue (the exact picture, headline and credit,
 * the score, every gate, whether it will post, and a Block button) and the story history (what was posted, what was merged or skipped, why). */

type Gate = { ok: boolean; detail: string };
export type QueueCard = { event: number; kind: string; headline: string; credit: string; score: number; held_score: number; topic: string; picture: string | null;
  gates: Record<string, Gate>; will_post: boolean; status: string; image: string; blocked: boolean; expires_at: string | null };
export type Queue = { next_slot: string; next_slot_dubai: string; rule: string; candidates: QueueCard[]; fallback: string; bar: number;
  releasing: { event: number; headline: string; telegram: string; x: string; blocked: boolean }[] };
type StoryPost = { event: number; at: string; kind: string; headline: string; telegram: string; x: string; why: string };
type Merged = { event: number | null; at: string | null; headline: string; why: string };
export type Story = { subjects: string[]; type: string; direction: string; topic: string; first_coverage: string; last_coverage: string; posts_made: number;
  posts: StoryPost[]; merged_or_skipped: Merged[] };

export type Decision = { event: number; at: string | null; decision: string; kind: string; desk: string; headline: string; score: number; evidence: string;
  sources: number; why: string; new_because: string; telegram: string | null; x: string | null;
  outlets?: string[]; via_worldmonitor?: string[]; development?: { outlet?: string; url?: string; headline?: string; since?: string } | null };

const GATE_NAME: Record<string, string> = { dedup: "Not a repeat", diversity: "Feed diversity", evidence: "Evidence", bar: "Score", numbers: "Numbers check", headline: "Headline rules", owner: "Owner" };
const json = (value: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(value), headers: { "Content-Type": "application/json" } });

export function EditorialDesk() {
  const queue = useApi(() => request<Queue>("/x/newsdesk/editorial/queue"), 60000);
  const history = useApi(() => request<{ rule: string; stories: Story[] }>("/x/newsdesk/editorial/history"), 120000);
  const decided = useApi(() => request<{ decisions: Decision[]; skipped_slots: Record<string, string> }>("/x/newsdesk/editorial/decisions"), 120000);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const q = queue.data && Array.isArray(queue.data.candidates) ? queue.data : null;
  const stories = history.data && Array.isArray(history.data.stories) ? history.data.stories : null;
  const block = async (c: QueueCard) => {
    const reason = c.blocked ? "" : window.prompt(`Block "${c.headline}"? It won't post on Telegram or X. Reason (optional):`, "");
    if (reason === null) return;
    setBusy(true); setError(null);
    try { await request("/x/newsdesk/editorial/block", json({ event_id: c.event, block: !c.blocked, reason })); await queue.reload(); }
    catch (e) { setError(e); } finally { setBusy(false); }
  };
  return (
    <>
      <Card title={q ? `Next slot: ${q.next_slot_dubai} Dubai` : "Next slot"}>
        {error ? <ApiErrorBanner error={error} /> : null}
        {queue.failure ? <ApiErrorBanner error={queue.failure} /> : null}
        {q && <p className="muted small">{q.rule}</p>}
        {q && q.candidates.length === 0 && <Empty>No candidate yet. {q.fallback}</Empty>}
        {q && q.candidates.map((c) => (
          <div key={c.event} className="feed-item" style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "flex-start", marginBottom: 16 }}>
            <img src={`/api/x/newsdesk/editorial/preview/${c.event}.jpg`} alt={`Preview: ${c.headline}`} loading="lazy" style={{ width: 320, maxWidth: "100%", aspectRatio: "16 / 9", borderRadius: 6, background: "#111" }} />
            <div style={{ flex: "1 1 280px", minWidth: 0 }}>
              <p style={{ margin: 0 }}><b>{c.headline}</b></p>
              <p className="muted small" style={{ margin: "2px 0 8px" }}><i>{c.credit}</i></p>
              <div className="row" style={{ gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                <Badge tone={c.will_post ? "good" : c.blocked ? "bad" : "neutral"}>{c.will_post ? "Will post" : c.blocked ? "Blocked" : "Won't post"}</Badge>
                <span className="small">score {c.score}{c.held_score > c.score ? ` (best ${c.held_score})` : ""} · bar {q.bar} · {c.picture ?? "picture"}</span>
                <button className="btn btn-sm" disabled={busy} onClick={() => void block(c)}>{c.blocked ? "Unblock" : "Block"}</button>
              </div>
              <ul className="small" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
                {Object.entries(c.gates).map(([k, g]) => <li key={k}>{g.ok ? "✓" : "✗"} {GATE_NAME[k] ?? k}: {g.detail}</li>)}
              </ul>
              {!c.will_post && <p className="small muted" style={{ margin: "4px 0 0" }}>{c.status}</p>}
            </div>
          </div>
        ))}
        {q && q.candidates.length > 0 && <p className="muted small">{q.fallback}</p>}
      </Card>
      <Card title="Story history (72 hours)">
        {history.failure ? <ApiErrorBanner error={history.failure} /> : null}
        {stories && stories.length === 0 && <Empty>No stories posted in the last 72 hours.</Empty>}
        {stories && stories.length > 0 && (
          <table className="table small">
            <thead><tr><th>Story</th><th>Asset</th><th>Type</th><th>Direction</th><th>First</th><th>Last</th><th>Posts</th><th>Merged / skipped, and why</th></tr></thead>
            <tbody>
              {stories.map((s) => (
                <tr key={`${s.first_coverage}-${s.subjects.join()}`}>
                  <td>{s.topic}</td><td>{s.subjects.join(", ")}</td><td>{s.type}</td><td>{s.direction || "-"}</td>
                  <td>{relativeTime(s.first_coverage)}</td><td>{relativeTime(s.last_coverage)}</td><td>{s.posts_made}</td>
                  <td>{s.merged_or_skipped.length === 0 ? "-" : <ul style={{ margin: 0, paddingLeft: 16 }}>{s.merged_or_skipped.map((m, i) => <li key={i}>{m.why}</li>)}</ul>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
      <Card title="Decisions (24 hours): why each story was selected or rejected">
        {decided.failure ? <ApiErrorBanner error={decided.failure} /> : null}
        {decided.data && Array.isArray(decided.data.decisions) && decided.data.decisions.length === 0 && <Empty>No candidates in the last 24 hours.</Empty>}
        {decided.data && Array.isArray(decided.data.decisions) && decided.data.decisions.length > 0 && (
          <table className="table small">
            <thead><tr><th>When</th><th>Decision</th><th>Desk</th><th>Headline</th><th>Score</th><th>Evidence</th><th>Why</th></tr></thead>
            <tbody>
              {decided.data.decisions.map((d) => (
                <tr key={d.event}>
                  <td>{d.at ? relativeTime(d.at) : "-"}</td>
                  <td><Badge tone={d.decision === "selected" ? "good" : d.decision === "waiting" ? "neutral" : "bad"}>{d.decision}</Badge></td>
                  <td>{d.desk}</td><td>{d.headline}</td><td>{d.score}</td><td>{d.evidence || "-"}{d.sources ? ` (${d.sources})` : ""}
                    {d.outlets && d.outlets.length > 0 && <div className="muted">{d.outlets.map((o) => d.via_worldmonitor?.includes(o) ? `${o} (via World Monitor catalog)` : o).join(", ")}</div>}</td>
                  <td>{d.why}{d.new_because ? ` · new because ${d.new_because}` : ""}
                    {d.development?.url && <div className="muted">New fact from <a className="link" href={d.development.url} target="_blank" rel="noopener noreferrer">{d.development.outlet}</a>, against what was already told: {d.development.since}</div>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {decided.data && Object.keys(decided.data.skipped_slots || {}).length > 0 && (
          <p className="muted small">Slots skipped on purpose: {Object.keys(decided.data.skipped_slots).slice(-6).map((t) => new Date(t).toISOString().slice(5, 16).replace("T", " ")).join(", ")} UTC</p>
        )}
      </Card>
    </>
  );
}
