import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Badge, Card, Loading, Stat } from "../../components/ui";

/** Growth G3: how Price Chat's X posts performed, Originals against the News Desk, Trending and replies, and the winning formats. The
 *  numbers come from the snapshots the X history job already takes (no extra X reads). Followers per post are an estimate. */

export type PerfSummary = {
  posts: number; measured_24h: number; median_impressions_24h: number | null; median_engagement_rate_24h: number | null;
  engagement_rate_24h: number | null; reposts: number; bookmarks: number; replies: number; likes: number; quotes: number;
  profile_visits: number; impressions: number; followers_est_24h: number | null; followers_per_1k_impressions: number | null; label?: string;
};
export type PerfFormat = PerfSummary & {
  group: string; group_label: string; format: string; impressions_lift: number | null; engagement_lift: number | null; score: number | null; rank?: number;
};
export type PerfMeasure = {
  impressions: number | null; likes: number | null; replies: number | null; reposts: number | null; quotes: number | null; bookmarks: number | null;
  profile_visits: number | null; url_clicks: number | null; engagement: number | null; engagement_rate: number | null; updated_at?: string | null;
};
export type PerfPost = {
  x_id: string; url: string; published_at: string; text: string; group: string; format: string; draft_id: number | null;
  at_24h: PerfMeasure | null; latest: PerfMeasure; followers_est: number | null; followers_window: { change: number; posts_in_window: number } | null;
};
export type PerfReport = {
  days: number; as_of: string; overall: PerfSummary; groups: Record<string, PerfSummary>; formats: PerfFormat[]; winners: PerfFormat[];
  pictures: Record<string, PerfSummary>; originals: PerfPost[]; top_posts: PerfPost[]; followers_now: number | null; notes: string[];
  originals_vs_news: Record<string, { originals: number | null; news: number | null; ratio: number | null } | boolean>;
  method: Record<string, string | number | number[]>;
  experiment?: Experiment;
};
export type ExperimentFamily = PerfSummary & { family: string; impressions_lift: number | null; engagement_lift: number | null; score: number | null; enough: boolean };
export type Experiment = {
  start: string; end: string; days: number; day: number | null; state: string; per_day: number[]; slots_utc: string[];
  posted_per_day: Record<string, number>; originals: PerfSummary; news: PerfSummary; families: ExperimentFamily[]; winners: string[];
  measured_originals: number; g4_ready: boolean; g4_needs: { measured_originals: number; families_with_min_n: number };
};

function ExperimentPanel({ e }: { e: Experiment }) {
  const [lo, hi] = e.per_day;
  return (
    <div className="stack" data-testid="experiment" style={{ borderTop: "1px solid var(--border, #ddd)", paddingTop: 10 }}>
      <div><b>7-day growth experiment</b> <span className="muted small">{e.start} to {e.end} · {e.state}{e.day ? ` · day ${e.day} of ${e.days}` : ""} ·
        {` ${lo}-${hi}`} approved Originals a day at {e.slots_utc.join(", ")} UTC · News Desk is the control</span></div>
      <div className="small" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {Object.entries(e.posted_per_day).map(([d, n]) => (
          <Badge key={d} tone={n >= lo ? "good" : n > 0 ? "warn" : "neutral"}>{d.slice(5)}: {n}</Badge>
        ))}
      </div>
      <div className="small">Originals: {e.originals.posts} posted, {e.originals.measured_24h} measured, median {num(e.originals.median_impressions_24h)} impressions,
        {" "}{per1k(e.originals.engagement_rate_24h)} engagements / 1k · News Desk in the same days: {e.news.posts} posts, median {num(e.news.median_impressions_24h)},
        {" "}{per1k(e.news.engagement_rate_24h)} / 1k</div>
      <div className="table-wrap"><table className="table small">
        <thead><tr><th>Family</th><th>Posted</th><th>Measured</th><th>Median impr. 24 h</th><th>Eng. / 1k</th><th>Impr. lift</th><th>Eng. lift</th></tr></thead>
        <tbody>{e.families.map((f) => (
          <tr key={f.family}><td>{f.family}{e.winners.includes(f.family) ? " ★" : ""}</td><td>{f.posts}</td><td>{f.measured_24h}{f.enough ? "" : " (needs 3)"}</td>
            <td>{num(f.median_impressions_24h)}</td><td>{per1k(f.engagement_rate_24h)}</td><td>{lift(f.impressions_lift)}</td><td>{lift(f.engagement_lift)}</td></tr>
        ))}</tbody>
      </table></div>
      <div className="small muted">Learner (G4): {e.g4_ready ? "enough data to start" : `not yet: needs ${e.g4_needs.measured_originals} measured Originals and ${e.g4_needs.families_with_min_n} families with 3 each (now ${e.measured_originals})`}.</div>
    </div>
  );
}

const num = (v: number | null | undefined, dp = 0) => (v === null || v === undefined ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: dp }));
/** engagements per 1,000 impressions: easier to read than a 0.4% rate on a small account */
export const per1k = (rate: number | null | undefined) => (rate === null || rate === undefined ? "—" : (rate * 1000).toFixed(1));
const lift = (v: number | null | undefined) => (v === null || v === undefined ? "—" : `${v.toFixed(2)}×`);

export function MetricsRow({ m, testId }: { m: PerfMeasure; testId?: string }) {
  return (
    <div className="small" data-testid={testId} style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
      <span><b>{num(m.impressions)}</b> impressions</span>
      <span><b>{num(m.reposts)}</b> reposts</span>
      <span><b>{num(m.bookmarks)}</b> bookmarks</span>
      <span><b>{num(m.replies)}</b> replies</span>
      <span><b>{num(m.likes)}</b> likes</span>
      <span><b>{num(m.profile_visits)}</b> profile visits</span>
      <span><b>{per1k(m.engagement_rate)}</b> engagements / 1k impressions</span>
    </div>
  );
}

const GROUPS = ["original", "news", "trend", "other"];

export function OriginalsPerformance() {
  const { data, failure, loading } = useApi(() => request<PerfReport>("/originals/performance?days=30"), 300_000);
  if (!data) return loading ? <Loading /> : <ApiErrorBanner error={failure} />;
  const vs = data.originals_vs_news;
  const enough = vs.enough_data === true;
  return (
    <Card title={<span>Performance <span className="muted small">· last {data.days} days on X · measured 24 h after posting</span></span>}
      actions={data.followers_now !== null ? <Badge tone="info">{num(data.followers_now)} followers</Badge> : undefined}>
      <div className="stack" data-testid="performance">
        {data.notes.map((n) => <div key={n} className="small muted" role="note">{n}</div>)}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 12 }}>
          {GROUPS.map((g) => {
            const s = data.groups[g];
            if (!s) return null;
            return (
              <div key={g} data-testid={`group-${g}`} style={{ border: "1px solid var(--border, #ddd)", borderRadius: 8, padding: 10 }}>
                <div><b>{s.label}</b> <span className="muted small">{s.posts} posts · {s.measured_24h} measured</span></div>
                <Stat label="Median impressions (24 h)" value={num(s.median_impressions_24h)} />
                <Stat label="Engagements / 1k impressions" value={per1k(s.engagement_rate_24h)} />
                <div className="small muted">{num(s.reposts)} reposts · {num(s.bookmarks)} bookmarks · {num(s.profile_visits)} profile visits</div>
                <div className="small muted">Followers (estimate): {s.followers_est_24h === null ? "—" : `${s.followers_est_24h >= 0 ? "+" : ""}${num(s.followers_est_24h, 1)}`}</div>
              </div>
            );
          })}
        </div>
        <div className="small" data-testid="originals-vs-news">
          <b>Originals vs News Desk:</b>{" "}
          {enough ? (() => {
            const imp = vs.median_impressions_24h as { ratio: number | null };
            const eng = vs.engagement_rate_24h as { ratio: number | null };
            return <span>{lift(imp.ratio)} the impressions, {lift(eng.ratio)} the engagement rate</span>;
          })() : <span className="muted">not enough posted Originals yet (needs {String(data.method.min_posts_for_a_winner)} with 24 h numbers)</span>}
        </div>
        <div>
          <b className="small">Winning formats</b>
          {data.winners.length === 0 ? <div className="small muted">None yet: a format needs {String(data.method.min_posts_for_a_winner)} measured posts.</div> : (
            <ol className="small" data-testid="winners">
              {data.winners.map((f) => (
                <li key={`${f.group}-${f.format}`}><b>{f.format}</b> <span className="muted">({f.group_label}, {f.measured_24h} posts)</span>: {lift(f.impressions_lift)} impressions,{" "}
                  {lift(f.engagement_lift)} engagement vs all posts</li>
              ))}
            </ol>
          )}
        </div>
        <details>
          <summary className="small">Every format</summary>
          <div className="table-wrap"><table className="table small">
            <thead><tr><th>Format</th><th>Lane</th><th>Posts</th><th>Measured</th><th>Median impr. 24 h</th><th>Eng. / 1k</th><th>Reposts</th><th>Bookmarks</th><th>Followers (est.)</th><th>Rank</th></tr></thead>
            <tbody>{data.formats.map((f) => (
              <tr key={`${f.group}-${f.format}`}><td>{f.format}</td><td>{f.group_label}</td><td>{f.posts}</td><td>{f.measured_24h}</td>
                <td>{num(f.median_impressions_24h)}</td><td>{per1k(f.engagement_rate_24h)}</td><td>{f.reposts}</td><td>{f.bookmarks}</td>
                <td>{f.followers_est_24h === null ? "—" : num(f.followers_est_24h, 1)}</td><td>{f.rank ?? "—"}</td></tr>
            ))}</tbody>
          </table></div>
          <p className="small muted">With a picture: {per1k(data.pictures["with picture"]?.engagement_rate_24h)} engagements / 1k, median {num(data.pictures["with picture"]?.median_impressions_24h)} impressions ·
            no picture: {per1k(data.pictures["no picture"]?.engagement_rate_24h)} / 1k, median {num(data.pictures["no picture"]?.median_impressions_24h)}.</p>
        </details>
        {data.experiment && <ExperimentPanel e={data.experiment} />}
        <div className="small muted">How: {String(data.method.ranking)}. Followers: {String(data.method.followers)}. Cost: {String(data.method.cost)}.</div>
      </div>
    </Card>
  );
}
