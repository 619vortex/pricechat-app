import { useState } from "react";
import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Tabs } from "../../components/ui";

/** Admin › X › News Desk: Viral Engine 2.0 (owner, 2026-11-11). For each recent story, the five hooks the desk pitched it with, which
 * won and why the others lost; what our audience engaged with by hook, format, question and links (X metrics); the hook experiments;
 * and today's data-post candidates with the MARKETS GONE MAD pick. Two switches: data posts, and experiments. Reading changes nothing. */

type Angle = { hook: string; name: string; headline: string; context: string; stakes: string; evidence: string; support: number; score: number;
  parts: Record<string, number>; problems: string[] };
type Story = { event: number; title: string; status: string; score: number; hook: string; exploring: boolean; angles: Angle[];
  reactions: { name: string; pct: number }[] };
type Feature = Record<string, { posts: number; rate: number; vs_average: number }>;
type Experiment = { arm: string; control: string; arm_posts: number; control_posts: number; lift: number | null; verdict: string };
export type Viral = { at: string; hooks: Record<string, { name: string; directive: string }>; explore_pct: number; margin: number; stories: Story[];
  engagement: { days: number; measured_posts: number; average_rate: number; learning: boolean; features: Record<string, Feature>; experiments: Experiment[];
    hook_points: Record<string, number>; telegram: string };
  data: { candidates: { kind: string; headline: string; interest: number; why: Record<string, number> }[]; bar: number; mgm_bar: number; mgm_today: string;
    recent: { event: number; title: string; kicker: string; status: string }[] } };
type Switches = { data_posts: boolean; experiments: boolean };
type View = "hooks" | "learning" | "data";

const FEATURE_NAMES: Record<string, string> = { hook: "Hook", format: "Picture", question: "Asks a question", links: "Links on X", writer: "Written by" };

function explain(parts: Record<string, number>): string {
  return Object.entries(parts).filter(([, v]) => v).map(([k, v]) => `${k} ${v > 0 && k !== "support" ? "+" : ""}${v}`).join(", ");
}

function Hooks({ data }: { data: Viral }) {
  if (data.stories.length === 0) return <Empty>No stories pitched in the last 48 hours yet.</Empty>;
  return (
    <>
      {data.stories.map((s) => (
        <div key={s.event} className="feed-item" style={{ marginBottom: 14 }}>
          <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
            <strong style={{ minWidth: 0, flex: "1 1 280px" }}>{s.title}</strong>
            <Badge tone={s.status === "released" ? "good" : "info"}>{s.status === "released" ? "Posted" : s.status}</Badge>
            {s.exploring ? <Badge tone="warn">Experiment: runner-up hook</Badge> : null}
          </div>
          {s.reactions.length ? <p className="small muted" style={{ margin: "4px 0" }}>Measured since the first report: {s.reactions.map((r) => `${r.name} ${r.pct > 0 ? "+" : ""}${r.pct}%`).join(", ")}</p> : null}
          <div style={{ overflowX: "auto" }}>
            <table className="table small">
              <thead><tr><th>Hook</th><th>Score</th><th>Headline it gives</th><th>Stands on</th><th>Why</th></tr></thead>
              <tbody>
                {[...s.angles].sort((a, b) => b.score - a.score).map((a) => (
                  <tr key={a.hook}>
                    <td style={{ whiteSpace: "nowrap" }}>{a.hook === s.hook ? <Badge tone="good">{a.name}</Badge> : a.name}</td>
                    <td>{a.problems.length ? "refused" : a.score}</td>
                    <td style={{ minWidth: 220 }}>{a.headline}{a.stakes ? <div className="muted">{a.stakes}</div> : null}</td>
                    <td style={{ minWidth: 200 }} className="muted">{a.evidence}</td>
                    <td className="muted">{a.problems.length ? a.problems.join("; ") : explain(a.parts)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </>
  );
}

function Learning({ data }: { data: Viral }) {
  const e = data.engagement;
  return (
    <>
      <p className="small muted">
        {e.learning ? `Learning from ${e.measured_posts} measured X posts (last ${e.days} days).` : `Collecting: ${e.measured_posts} measured X posts so far; hooks are scored on facts alone until there are enough.`} {e.telegram}
        {" "}About {data.explore_pct}% of stories try their runner-up hook when it is factual and within {data.margin} points.
      </p>
      {Object.keys(e.hook_points).length ? <p className="small">Audience points per hook: {Object.entries(e.hook_points).map(([k, v]) => `${data.hooks[k]?.name ?? k} ${v > 0 ? "+" : ""}${v}`).join(" · ")}</p> : null}
      {Object.entries(e.features).filter(([, rows]) => Object.keys(rows).length).map(([name, rows]) => (
        <div key={name} style={{ overflowX: "auto" }}>
          <table className="table small">
            <thead><tr><th>{FEATURE_NAMES[name] ?? name}</th><th>Posts</th><th>Engagement rate</th><th>vs average</th></tr></thead>
            <tbody>{Object.entries(rows).map(([k, r]) => (
              <tr key={k}><td>{data.hooks[k]?.name ?? k}</td><td>{r.posts}</td><td>{(r.rate * 100).toFixed(2)}%</td><td>{r.vs_average.toFixed(2)}×</td></tr>
            ))}</tbody>
          </table>
        </div>
      ))}
      {e.experiments.length ? (
        <div style={{ overflowX: "auto" }}>
          <table className="table small">
            <thead><tr><th>Experiment</th><th>Posts</th><th>Lift</th><th>Result</th></tr></thead>
            <tbody>{e.experiments.map((x) => (
              <tr key={`${x.arm}-${x.control}`}><td>{data.hooks[x.arm]?.name ?? x.arm} instead of {data.hooks[x.control]?.name ?? x.control}</td>
                <td>{x.arm_posts} / {x.control_posts}</td><td>{x.lift === null ? "-" : `${x.lift > 0 ? "+" : ""}${(x.lift * 100).toFixed(0)}%`}</td><td>{x.verdict}</td></tr>
            ))}</tbody>
          </table>
        </div>
      ) : <Empty>No experiment has posted yet.</Empty>}
    </>
  );
}

function DataPosts({ data }: { data: Viral }) {
  const d = data.data;
  return (
    <>
      <p className="small muted">Built from daily closes we fetch and measure ourselves. One data post a day at most, above {d.bar} interest; MARKETS GONE MAD after the US close only above {d.mgm_bar}.</p>
      <p><strong>MARKETS GONE MAD today:</strong> {d.mgm_today || "nothing qualifies (most days nothing does)"}</p>
      {d.candidates.length ? (
        <div style={{ overflowX: "auto" }}>
          <table className="table small">
            <thead><tr><th>Interest</th><th>Post</th><th>Why</th></tr></thead>
            <tbody>{d.candidates.map((c) => (
              <tr key={c.headline}><td>{c.interest}</td><td>{c.headline}</td><td className="muted">{explain(c.why)}</td></tr>
            ))}</tbody>
          </table>
        </div>
      ) : <Empty>No daily closes fetched yet: the desk fetches them on its next run.</Empty>}
      {d.recent.length ? <p className="small muted">This week: {d.recent.map((r) => `${r.kicker ? `${r.kicker}: ` : ""}${r.title} (${r.status})`).join(" · ")}</p> : null}
    </>
  );
}

export function ViralEngine() {
  const [view, setView] = useState<View>("hooks");
  const viral = useApi(() => request<Viral>("/x/newsdesk/viral"), 60000);
  const sw = useApi(() => request<Switches>("/x/newsdesk/network"), 60000);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const put = async (body: Partial<Switches>) => {
    setBusy(true); setError(null);
    try { await request("/x/newsdesk/network", { method: "PUT", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } }); await Promise.all([sw.reload(), viral.reload()]); }
    catch (e) { setError(e); } finally { setBusy(false); }
  };
  const v = viral.data && Array.isArray(viral.data.stories) ? viral.data : null;
  const s = sw.data && typeof sw.data.data_posts === "boolean" ? sw.data : null;
  return (
    <Card title="Viral Engine">
      {error ? <ApiErrorBanner error={error} /> : null}
      <p className="muted">Every story is pitched five ways (shocking fact, market consequence, controversy, winners vs losers, contradiction). Only hooks the reporting or our prices support are built, each is fact-checked, and the strongest goes out.</p>
      {s ? (
        <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 8 }}>
          <span>Data posts and MARKETS GONE MAD: <strong>{s.data_posts ? "on" : "off"}</strong></span>
          <button className="btn btn-sm" disabled={busy} onClick={() => void put({ data_posts: !s.data_posts })}>{s.data_posts ? "Turn data posts off" : "Turn data posts on"}</button>
          <span>Hook experiments: <strong>{s.experiments ? "on" : "off"}</strong></span>
          <button className="btn btn-sm" disabled={busy} onClick={() => void put({ experiments: !s.experiments })}>{s.experiments ? "Turn experiments off" : "Turn experiments on"}</button>
        </div>
      ) : null}
      <Tabs tabs={[{ key: "hooks", label: "Hooks" }, { key: "learning", label: "What works" }, { key: "data", label: "Data posts" }]} value={view} onChange={setView} />
      {viral.failure ? <ApiErrorBanner error={viral.failure} /> : !v ? <p className="muted">Loading…</p>
        : view === "hooks" ? <Hooks data={v} /> : view === "learning" ? <Learning data={v} /> : <DataPosts data={v} />}
    </Card>
  );
}
