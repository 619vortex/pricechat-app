import { useState } from "react";
import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Tabs } from "../../components/ui";

/** Admin › X › News Desk: the media engine (owner, 2026-11-13). Each candidate's editorial scorecard (surprise, impact, controversy,
 * relevance, discussion, beat, audience), why it was rejected, the five ranked headline hooks with every fact-check problem, the debate
 * question, the picture plan and the accuracy gate; then engagement analytics normalized per platform, with which metric sources are
 * connected. One control: the editorial bar. */

type Hook = { text: string; strategy: string; rank: number; total: number; engagement: number; accuracy: number; problems: string[]; ok: boolean };
type Card_ = { total?: number; dims?: Record<string, number>; beat?: string; beat_name?: string; beat_points?: number; audience?: number; reject?: string;
  safety?: string[]; reasons?: Record<string, string[]> };
type Story = { event: number; title: string; headline: string; status: string; why: string; virality: number; evidence: string; editorial: Card_;
  below_minimum: boolean; hooks: Hook[]; chosen_hook: string; hook_why: string; question: string; question_why: string;
  visual: { order?: string[]; first?: string; why?: string }; accuracy: { ok: boolean; problems: string[] } };
export type Scorecards = { minimum: number; dimensions: string[]; beats: Record<string, string>; stories: Story[] };
type Row = { posts: number; index: number };
type Platform = { posts: number; rated: number; median_rate: number | null; median_views: number | null; views: number; shares: number; replies: number; reactions: number };
export type Performance = { days: number; measured: number; learning: boolean; learn_min: number; platforms: Record<string, Platform>;
  tables: Record<string, Record<string, Row>>; beat_points: Record<string, number>; strategy_points: Record<string, number>;
  connections: Record<string, { connected: boolean | null; why: string }> };
type View = "stories" | "performance";

const DIM: Record<string, string> = { surprise: "Surprise", impact: "Impact", controversy: "Controversy", relevance: "Relevance", discussion: "Discussion" };

function Stories({ data }: { data: Scorecards }) {
  if (data.stories.length === 0) return <Empty>No news candidates scored in the last 48 hours yet.</Empty>;
  return (
    <>
      {data.stories.map((s) => {
        const c = s.editorial || {};
        const rejected = !s.accuracy.ok || Boolean(c.reject) || s.why.startsWith("editorial:");
        return (
          <div key={s.event} className="feed-item" style={{ marginBottom: 14 }}>
            <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
              <strong style={{ minWidth: 0, flex: "1 1 280px" }}>{s.title}</strong>
              {typeof c.total === "number" ? <Badge tone={c.total >= data.minimum ? "good" : "warn"}>Editorial {c.total}</Badge> : null}
              {c.beat_name ? <Badge tone="info">{c.beat_name}</Badge> : null}
              <Badge tone={s.status === "released" ? "good" : rejected ? "bad" : "info"}>{s.status === "released" ? "Posted" : rejected ? "Rejected" : s.status}</Badge>
            </div>
            {c.dims ? (
              <p className="small" style={{ margin: "4px 0" }}>
                {Object.entries(c.dims).map(([k, v]) => `${DIM[k] ?? k} ${v}`).join(" · ")}
                {c.beat_points ? ` · beat ${c.beat_points > 0 ? "+" : ""}${c.beat_points}` : ""}{c.audience ? ` · audience ${c.audience > 0 ? "+" : ""}${c.audience}` : ""}
              </p>
            ) : null}
            {!s.accuracy.ok ? <p className="small banner banner-warn">Accuracy gate: {s.accuracy.problems.join("; ")}</p> : null}
            {c.reject ? <p className="small muted">Rejected: {c.reject}</p> : s.why ? <p className="small muted">Desk: {s.why}</p> : null}
            {s.hooks.length ? (
              <div style={{ overflowX: "auto" }}>
                <table className="table small">
                  <thead><tr><th>#</th><th>Hook</th><th>From</th><th>Engagement</th><th>Accuracy</th><th>Problems</th></tr></thead>
                  <tbody>{s.hooks.map((h) => (
                    <tr key={`${h.rank}-${h.text}`}>
                      <td>{h.rank}</td>
                      <td style={{ minWidth: 220 }}>{h.text === s.chosen_hook ? <Badge tone="good">Chosen</Badge> : null} {h.text}</td>
                      <td>{h.strategy}</td><td>{h.engagement}</td><td>{h.accuracy}</td>
                      <td className="muted">{h.problems.length ? h.problems.join("; ") : "none"}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            ) : null}
            <p className="small muted" style={{ margin: "4px 0" }}>
              Question: {s.question || "none"}{s.question_why ? ` (${s.question_why})` : ""}
              {s.visual?.first ? ` · Picture: ${s.visual.first}${s.visual.why ? `, ${s.visual.why}` : ""}` : ""}
            </p>
          </div>
        );
      })}
    </>
  );
}

function table(t: Record<string, Row> | undefined, names?: Record<string, string>) {
  const rows = Object.entries(t || {});
  if (!rows.length) return <span className="muted">no measured posts yet</span>;
  return rows.map(([k, r]) => `${names?.[k] ?? k} ${r.index}× (${r.posts})`).join(" · ");
}

function PerformanceView({ data, beats }: { data: Performance; beats: Record<string, string> }) {
  return (
    <>
      {Object.entries(data.connections).map(([p, c]) => (
        <p key={p} className="small"><strong>{p === "x" ? "X" : "Telegram"}:</strong> <Badge tone={c.connected ? "good" : "warn"}>{c.connected ? "connected" : "not connected"}</Badge> {c.why}</p>
      ))}
      <div style={{ overflowX: "auto" }}>
        <table className="table small">
          <thead><tr><th>Platform</th><th>Posts</th><th>Median rate</th><th>Median views</th><th>Shares</th><th>Replies</th><th>Reactions</th></tr></thead>
          <tbody>{Object.entries(data.platforms).map(([p, r]) => (
            <tr key={p}><td>{p === "x" ? "X" : "Telegram"}</td><td>{r.posts}</td><td>{r.median_rate ?? "–"}</td><td>{r.median_views ?? "–"}</td><td>{r.shares}</td><td>{r.replies}</td><td>{r.reactions}</td></tr>
          ))}</tbody>
        </table>
      </div>
      <p className="small muted">Index = engagement rate against the platform's own median over {data.days} days (1× is a typical post). {data.learning
        ? "Learning is on." : `Learning starts at ${data.learn_min} measured posts (${data.measured} so far): until then nothing is adjusted.`}</p>
      <p className="small"><strong>By beat:</strong> {table(data.tables.beat, beats)}</p>
      <p className="small"><strong>By hook:</strong> {table(data.tables.hook_strategy)}</p>
      <p className="small"><strong>By picture:</strong> {table(data.tables.format)}</p>
      <p className="small"><strong>With a question:</strong> {table(data.tables.question)}</p>
      {Object.keys(data.beat_points).length ? <p className="small">Audience points per beat: {Object.entries(data.beat_points).map(([k, v]) => `${beats[k] ?? k} ${v > 0 ? "+" : ""}${v}`).join(", ")}</p> : null}
    </>
  );
}

export function EditorialScores() {
  const [view, setView] = useState<View>("stories");
  const cards = useApi(() => request<Scorecards>("/x/newsdesk/editorial/scorecards"), 60000);
  const perf = useApi(() => request<Performance>("/x/newsdesk/editorial/performance"), 120000);
  const [bar, setBar] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const c = cards.data && Array.isArray(cards.data.stories) ? cards.data : null;
  const p = perf.data && perf.data.platforms ? perf.data : null;
  const save = async () => {
    const n = Number(bar);
    if (!Number.isInteger(n) || n < 0 || n > 100) { setError(new Error("The bar is a whole number from 0 to 100.")); return; }
    setBusy(true); setError(null);
    try { await request("/x/newsdesk/editorial/quality", { method: "PUT", body: JSON.stringify({ editorial_min: n }), headers: { "Content-Type": "application/json" } }); setBar(""); await cards.reload(); }
    catch (e) { setError(e); } finally { setBusy(false); }
  };
  return (
    <Card title="Editorial scores">
      {error ? <ApiErrorBanner error={error} /> : null}
      <p className="muted">Every story is scored for surprise, impact, genuine controversy, relevance and discussion; weak and minor corporate news is rejected, and a slot with nothing above the bar is skipped. Five headline hooks are ranked for engagement and accuracy; any hook, caption or question that adds a name, number, ticker, accusation, cause or quote the sources don't have is rejected.</p>
      {c ? (
        <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 8 }}>
          <span>Editorial bar: <strong>{c.minimum || "off"}</strong></span>
          <input className="input" style={{ width: 80 }} inputMode="numeric" aria-label="New editorial bar" placeholder="0-100" value={bar} onChange={(e) => setBar(e.target.value)} />
          <button className="btn btn-sm" disabled={busy || bar === ""} onClick={() => void save()}>Set bar</button>
        </div>
      ) : null}
      <Tabs tabs={[{ key: "stories", label: "Stories" }, { key: "performance", label: "Performance" }]} value={view} onChange={setView} />
      {view === "stories"
        ? (cards.failure ? <ApiErrorBanner error={cards.failure} /> : !c ? <p className="muted">Loading…</p> : <Stories data={c} />)
        : (perf.failure ? <ApiErrorBanner error={perf.failure} /> : !p ? <p className="muted">Loading…</p> : <PerformanceView data={p} beats={c?.beats ?? {}} />)}
    </Card>
  );
}
