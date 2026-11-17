import { useState } from "react";
import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Loading, Stat, relativeTime, type Tone } from "../../components/ui";

/** Admin › X › Trending Topics: what is getting hot (News Desk feeds, Google Trends, Reddit, X), scored, with a suggested tweet each.
 *  Approval mode and dry run by default; Automatic needs an explicit confirmation. The server decides; this page shows it and holds the switches. */

type Check = { check: string; passed: boolean; detail: string };
export type TrendPost = { id: number; topic_id: number; category: string; status: string; text: string; edited: boolean; angle: string; writer: string; score: number;
  sources: { name: string; url: string; title?: string; at?: string | null }[]; checks: Check[]; hold_reason: string | null; note: string | null; error: string | null;
  x_id: string | null; url: string | null; metrics: Record<string, number | string>; created_at: string | null; published_at: string | null; expires_at: string | null; cashtags: string[];
  sensitive?: Sensitive | null };
export type Sensitive = { kind: string; label: string; tier: "approval" | "strict"; matched?: string };
export type Growth = { evidence: string[]; notes: string[]; measured: boolean; capped?: boolean; observed_min?: number; items_before?: number; items_now?: number;
  outlets_before?: number; outlets_now?: number; google_from?: number; google_to?: number; google_up?: boolean; recent_2h?: number; prior_6h?: number };
export type Timeline = { event_time: string; first_seen: string; last_update: string; source_count: number; outlets: number; outlets_1h: number; items_per_hour: number;
  developing: boolean; new_outlets_2h: number };
export type Latency = { post_id: number; status: string; at: string; event_to_publish_min: number | null; detect_to_publish_min: number | null; written_to_publish_min: number | null; score?: number };
type LatencyStat = { median: number; max: number; n: number } | null;
export type TopicSource ={ source: string; outlet: string; title: string; url: string; reliable: boolean; engagement: number; at: string | null; extra?: Record<string, unknown> };
export type Topic = { id: number; title: string; category: string; status: string; score: number; peak_score: number; first_seen: string | null; last_seen: string | null;
  parts: { velocity?: number; engagement?: number; freshness?: number; impact?: number; surprise?: number; relevance?: number; learned?: number; recent_2h?: number; sources?: string[]; outlets?: string[]; age_hours?: number;
    growth?: Growth; sensitive?: Sensitive | null; stale?: string | null; why?: string; timeline?: Timeline; latency?: Latency };
  sources: TopicSource[]; metrics: Record<string, number>; posted_at: string | null; post: TrendPost | null };
export type SourceHealth = { name: string; last_scan?: string; items?: number; stored?: number; status: "ok" | "error" | "unavailable" | "off" | "idle"; problem: string | null;
  partial?: string; fails?: number; last_ok?: string | null; plan?: boolean; auth?: string | null };
export type TrendSettings = { enabled: boolean; kill_switch: boolean; mode: "approval" | "automatic"; automatic: boolean; auto_confirmed_at: string | null; live: boolean; hot_score: number; suggest_score: number;
  max_per_day: number; gap_minutes: number; topic_cooldown_hours: number; newsdesk_gap_minutes: number; shared_daily_cap: number; categories: Record<string, boolean>;
  sources: Record<string, Record<string, unknown> & { on: boolean }> };
export type Trending = { settings: TrendSettings; last_run: (Record<string, unknown> & { at?: string }) | null; health: Record<string, SourceHealth>;
  usage: { date?: string; requests?: Record<string, number>; cost_usd?: Record<string, number> }; topics: Topic[]; queue: TrendPost[]; held: TrendPost[]; published: TrendPost[];
  coordination: { posts_24h: { newsdesk: number; trending: number; total: number }; shared_cap: number; next_newsdesk_slot: string | null; desk_blocked: string | null; spacing: string | null; cap: string | null; newsdesk_x_mode: "live" | "dry_run"; x_live: boolean };
  learning: { measured_posts: number; learning: boolean; needs: number; weights: Record<string, number>; totals: Record<string, number>; bounds: number[];
    per_category?: Record<string, { posts: number; impressions: number; likes: number; reposts: number; replies: number; rate: number }> };
  latency?: { event_to_publish: LatencyStat; detect_to_publish: LatencyStat; written_to_publish: LatencyStat; recent: Latency[] };
  writer: "model" | "rules"; x: { configured: boolean; can_write: boolean; status: string; last_ok: Record<string, unknown> | null; last_error: { at: string; message: string } | null };
  cost: { per_post_usd: number; x_trends_per_request_usd: number; x_post_read_usd: number };
  incidents?: Incident[]; missed?: Missed[] };
/** A major infrastructure incident Trending saw (banking, payments, exchanges, groundings, cloud, cyber): when, how it scored, what happened. */
export type Incident = { topic_id: number; title: string; category: string; kind: string; label: string; entities: string[]; major: boolean; verified: boolean;
  evidence: string; event_time: string; first_detected: string; detect_lag_min: number | null; score: number; peak_score: number; outlets: number; status: string;
  reason: string; why: string; post_id: number | null; posted_at: string | null; first_line_at: string | null; updated: string };
/** An audited miss from before the incident watch existed. */
export type Missed = { story: string; date: string; event_time: string | null; event_note: string; first_detected: string | null; first_in_feeds: string | null;
  first_in_feeds_note: string; score: number | null; stage: string; reason: string; now: string };

const CATEGORY: Record<string, string> = { crypto: "Crypto", stocks: "Stocks", markets: "Markets", geopolitics: "Geopolitics", technology: "Technology", world: "World events" };
const SOURCE: Record<string, string> = { news: "News", google: "Google Trends", faa: "FAA airport status", reddit: "Reddit", x_trends: "X trends", x_accounts: "X accounts" };
const HEALTH_TONE: Record<string, Tone> = { ok: "good", error: "bad", unavailable: "warn", off: "neutral", idle: "info" };
const STATUS_TONE: Record<string, Tone> = { pending: "info", queued: "info", held: "warn", dry_run: "neutral", published: "good", failed: "bad", uncertain: "warn", rejected: "neutral", expired: "neutral" };
const utc = (iso: string | null | undefined): string => (iso ? `${iso.slice(0, 10)} ${iso.slice(11, 16)} UTC` : "-");

/** Admin › Trending › Major incidents: every incident topic with its first detection, score and the reason it did or didn't go out,
 *  then the audited misses (owner, 2026-11-17). */
export function IncidentsCard({ incidents, missed }: { incidents: Incident[]; missed: Missed[] }) {
  return (
    <Card title={`Major incidents (${incidents.length} in 72 h)`}>
      {incidents.length === 0 ? <Empty>No banking, payment, exchange, aviation, cloud or cyber incident in the last 72 hours.</Empty> : (
        <table className="table small"><thead><tr><th>Incident</th><th>Event / first seen</th><th>Score</th><th>Evidence</th><th>Outcome</th></tr></thead>
          <tbody>{incidents.map((e) => (
            <tr key={e.topic_id}>
              <td><strong>{e.title}</strong><div className="small muted">{e.label}{e.entities.length ? ` · ${e.entities.join(", ")}` : " · sector-wide"}</div></td>
              <td>{utc(e.event_time)}<div className="small muted">first seen {utc(e.first_detected)}{e.detect_lag_min !== null ? ` (+${minutes(e.detect_lag_min)})` : ""}</div></td>
              <td>{e.score.toFixed(0)}<div className="small muted">peak {e.peak_score.toFixed(0)}</div></td>
              <td><Badge tone={e.verified ? "good" : "warn"}>{e.verified ? "verified" : "unverified"}</Badge><div className="small muted">{e.evidence} · {e.outlets} outlet{e.outlets === 1 ? "" : "s"}</div></td>
              <td><Badge tone={STATUS_TONE[e.status] ?? "neutral"}>{e.status}</Badge><div className="small">{e.reason}</div>{e.posted_at && <div className="small muted">sent {utc(e.posted_at)}</div>}</td>
            </tr>))}</tbody></table>)}
      <p className="small muted">A verified major incident (two outlets, the company's own statement, or an official source such as the FAA) may go out beyond the daily limit, at most 2 more in 24 hours and 60 minutes after the last trend post. An unverified one waits for approval.</p>
      {missed.length > 0 && <>
        <h4>Audited misses</h4>
        <table className="table small"><thead><tr><th>Story</th><th>Event</th><th>First detected</th><th>Score</th><th>Why it was missed</th><th>Now</th></tr></thead>
          <tbody>{missed.map((m) => (
            <tr key={m.story}>
              <td><strong>{m.story}</strong><div className="small muted">{m.date}</div></td>
              <td>{utc(m.event_time)}<div className="small muted">{m.event_note}</div></td>
              <td>{m.first_detected ? utc(m.first_detected) : <Badge tone="bad">never</Badge>}<div className="small muted">in our feeds {utc(m.first_in_feeds)}: {m.first_in_feeds_note}</div></td>
              <td>{m.score ?? "-"}</td>
              <td><Badge tone="warn">{m.stage}</Badge><div className="small">{m.reason}</div></td>
              <td className="small">{m.now}</td>
            </tr>))}</tbody></table></>}
    </Card>
  );
}

const PARTS: [keyof Topic["parts"], string, number][] = [["velocity", "Momentum", 30], ["freshness", "Freshness", 25], ["impact", "Market impact", 15], ["surprise", "Surprise", 10],
  ["relevance", "Relevance", 10], ["engagement", "Engagement", 10]];

function minutes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "-";
  return n < 90 ? `${n} min` : `${(n / 60).toFixed(1)} h`;
}

/** When it happened, when we saw it, its newest item, how many sources and how fast it is spreading. */
export function TimelineNote({ t }: { t?: Timeline }) {
  if (!t) return null;
  return <div className="small muted">event {relativeTime(t.event_time)} · first seen {relativeTime(t.first_seen)} · last update {relativeTime(t.last_update)}
    {" · "}{t.source_count} source{t.source_count === 1 ? "" : "s"} · {t.items_per_hour}/h{t.outlets_1h ? ` · ${t.outlets_1h} outlet${t.outlets_1h === 1 ? "" : "s"} in the last hour` : ""}
    {t.developing ? " · developing" : ""}</div>;
}

const body = (method: string, data: unknown): RequestInit => ({ method, body: JSON.stringify(data) });

function scoreTone(score: number, s: TrendSettings): Tone {
  return score >= s.hot_score ? "bad" : score >= s.suggest_score ? "warn" : "neutral";
}

function ScoreBar({ value, max }: { value: number; max: number }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  return <span className="tt-bar" title={`${value} / ${max}`} style={{ display: "inline-block", width: 60, height: 6, background: "var(--line, #ddd)", borderRadius: 3, verticalAlign: "middle" }}>
    <span style={{ display: "block", width: `${pct}%`, height: 6, background: "var(--accent, #2b6)", borderRadius: 3 }} /></span>;
}

function engagementText(t: Topic): string {
  const m = t.metrics ?? {};
  const bits: string[] = [];
  const traffic = t.sources.find((x) => x.source === "google" && x.extra?.approx_traffic)?.extra?.approx_traffic;
  if (traffic) bits.push(`Google ${String(traffic)} searches`);
  if (m.reddit) bits.push(`Reddit ${Math.round(m.reddit).toLocaleString()} (score + 2×comments)`);
  if (m.x_trends) bits.push(`X ${Math.round(m.x_trends).toLocaleString()} posts`);
  const outlets = t.parts.outlets?.length ?? 0;
  if (outlets) bits.push(`${outlets} outlet${outlets === 1 ? "" : "s"}`);
  return bits.join(" · ") || `${m.signals ?? 0} signals`;
}

const EVIDENCE: Record<string, string> = { scans: "more outlets across scans", google: "Google Trends", engagement: "engagement rising" };

/** What we measured about a topic's growth: the only thing that can take it to the automatic line. */
export function GrowthNote({ g }: { g?: Growth }) {
  if (!g) return null;
  if (!g.measured) return <div className="small muted">No measured growth{g.capped ? " (capped below the automatic line)" : ""}: approval only</div>;
  return <div className="small">Growth: {g.evidence.map((e) => EVIDENCE[e] ?? e).join(", ")}{g.notes.length ? <span className="muted"> · {g.notes.join("; ")}</span> : null}</div>;
}

export function SensitiveBadge({ x }: { x?: Sensitive | null }) {
  if (!x) return null;
  return <Badge tone={x.tier === "approval" ? "bad" : "warn"}>{x.tier === "approval" ? "sensitive: approval only" : "sensitive: 2+ outlets"} · {x.label}</Badge>;
}

function PostCard({ p, busy, act, s }: { p: TrendPost; busy: boolean; act: (fn: () => Promise<unknown>) => Promise<void>; s: TrendSettings }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(p.text);
  const open = ["pending", "queued", "held"].includes(p.status);
  return (
    <div className="tt-post" style={{ borderLeft: "3px solid var(--line, #ccc)", paddingLeft: 10, margin: "8px 0" }}>
      <div className="row" style={{ gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <Badge tone={STATUS_TONE[p.status] ?? "neutral"}>{p.status === "dry_run" ? "dry run" : p.status}</Badge>
        <Badge tone={scoreTone(p.score, s)}>score {p.score.toFixed(0)}</Badge>
        <SensitiveBadge x={p.sensitive} />
        <span className="small muted">{CATEGORY[p.category] ?? p.category} · {p.writer === "model" ? "model writer" : p.writer === "owner" ? "edited by you" : "rules writer"}{p.cashtags.length ? ` · ${p.cashtags.join(" ")}` : ""}</span>
      </div>
      {editing ? <textarea className="input" rows={5} value={text} maxLength={280} onChange={(e) => setText(e.target.value)} aria-label="Edit tweet" style={{ width: "100%" }} />
        : <p style={{ whiteSpace: "pre-wrap", margin: "6px 0" }}>{p.text}</p>}
      {p.hold_reason && <p className="small banner banner-warn">Held: {p.hold_reason}</p>}
      {p.note && !p.hold_reason && <p className="small muted">{p.note}</p>}
      {p.error && <p className="small banner banner-bad">{p.error}</p>}
      {p.sources.length > 0 && <p className="small">Backed by: {p.sources.map((x, i) => <span key={x.url}>{i ? ", " : ""}<a href={x.url} target="_blank" rel="noreferrer">{x.name}</a></span>)}</p>}
      {p.url && <p className="small"><a href={p.url} target="_blank" rel="noreferrer">View on X</a>{p.metrics?.impression_count !== undefined && ` · ${p.metrics.impression_count} views · ${p.metrics.like_count ?? 0} likes · ${p.metrics.retweet_count ?? 0} reposts · ${p.metrics.reply_count ?? 0} replies`}</p>}
      {open && <div className="row" style={{ gap: 6 }}>
        {p.status === "pending" && <button className="btn btn-sm btn-primary" disabled={busy || editing} onClick={() => void act(() => request(`/x/trending/posts/${p.id}/approve`, { method: "POST" }))}>{s.live ? "Approve and post" : "Approve (dry run)"}</button>}
        {editing ? <button className="btn btn-sm" disabled={busy} onClick={() => void act(async () => { await request(`/x/trending/posts/${p.id}`, body("PUT", { text })); setEditing(false); })}>Save</button>
          : p.sources.length > 0 && <button className="btn btn-sm" disabled={busy} onClick={() => setEditing(true)}>Edit</button>}
        <button className="btn btn-sm" disabled={busy} onClick={() => void act(() => request(`/x/trending/posts/${p.id}/reject`, { method: "POST" }))}>Reject</button>
      </div>}
    </div>
  );
}

export function TrendingTopics() {
  const { data, failure, reload } = useApi(() => request<Trending>("/x/trending"), 30000);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (failure) return <ApiErrorBanner error={failure} />;
  if (!data) return <Loading />;
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null);
    try { await fn(); await reload(); } catch (e) { setError(e); } finally { setBusy(false); }
  };
  const s = data.settings;
  const save = (change: Record<string, unknown>) => act(() => request("/x/trending/settings", body("PUT", change)));
  const goAutomatic = () => {
    if (window.confirm(`Turn on automatic publishing? Tweets for topics scoring ${s.hot_score}+ will be ${s.live ? "posted to X" : "recorded (dry run)"} without asking you, up to ${s.max_per_day} a day, still inside every limit and check.`)) void save({ mode: "automatic", confirm_automatic: true });
  };
  const goLive = () => {
    if (window.confirm(`Follow the News Desk's X mode? While the News Desk is live, approved${s.automatic ? " and automatic" : ""} trend tweets will be posted to X at about $${data.cost.per_post_usd} each.`)) void save({ live: true, confirm_live: true });
  };
  const health = Object.entries(data.health);
  const usage = data.usage ?? {};
  const c = data.coordination;
  const num = (key: keyof TrendSettings, label: string, min: number, max: number) => (
    <label key={key}>{label} <input className="input" type="number" min={min} max={max} defaultValue={Number(s[key])} key={`${key}${String(s[key])}`}
      onBlur={(e) => { const n = Number(e.target.value); if (n !== s[key] && n >= min && n <= max) void save({ [key]: n }); }} /></label>);

  return (
    <div className="stack">
      {error ? <ApiErrorBanner error={error} /> : null}
      <Card title="Trending Topics">
        <p className="muted">Finds what is getting hot in crypto, stocks, markets, geopolitics, tech and world events, scores it, and suggests a tweet backed by a reliable source. It coordinates with the News Desk: no duplicate stories, no posts next to its slots, one shared daily X cap.</p>
        <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <Badge tone={s.automatic ? "warn" : "info"}>{s.automatic ? "Automatic: hot topics publish by themselves" : "Approval mode: nothing goes out without you"}</Badge>
          <Badge tone={c.x_live ? "good" : "neutral"}>{c.x_live ? "LIVE: posting to X" : !s.live ? "Dry run (Trending's own switch)" : "Dry run: the News Desk's X lane is in dry run"}</Badge>
          {s.kill_switch && <Badge tone="bad">Stopped: no trend post goes out</Badge>}
          {!s.enabled && <Badge tone="bad">Scanning is off</Badge>}
          {s.automatic ? <button className="btn btn-sm" disabled={busy} onClick={() => void save({ mode: "approval" })}>Back to approval mode</button>
            : <button className="btn btn-sm" disabled={busy} onClick={goAutomatic}>Enable automatic publishing</button>}
          {s.live ? <button className="btn btn-sm" disabled={busy} onClick={() => void save({ live: false })}>Force dry run</button>
            : <button className="btn btn-sm btn-primary" disabled={busy || !data.x.can_write} title={data.x.can_write ? "" : "X isn't connected with posting access"} onClick={goLive}>Follow the News Desk's X mode</button>}
          {s.kill_switch ? <button className="btn btn-sm" disabled={busy} onClick={() => void save({ kill_switch: false })}>Release stop</button>
            : <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => void save({ kill_switch: true })}>Stop trend posts</button>}
          <button className="btn btn-sm" disabled={busy} onClick={() => void act(() => request("/x/trending/scan", { method: "POST" }))}>Scan now</button>
          <button className="btn btn-sm" disabled={busy} onClick={() => void save({ enabled: !s.enabled })}>{s.enabled ? "Stop scanning" : "Start scanning"}</button>
        </div>
        {!s.automatic && <p className="small muted">Automatic publishing is disabled. It needs your confirmation to turn on.</p>}
        <p className="small muted">Posts reach X only while the News Desk's X lane is live ({c.newsdesk_x_mode === "live" ? "it is live now" : "it is in dry run now"}) and nothing is stopped: the News Desk's emergency stop and pauses stop trend posts too.
          Automatic posts at most {s.max_per_day} a day, {Math.round(s.gap_minutes / 60 * 10) / 10} h apart, for topics scoring {s.hot_score}+
          with measured growth (more outlets across scans, Google Trends, rising engagement). Sensitive subjects (allegations, crimes, minors...) always wait for you.</p>
        {c.desk_blocked && <p className="banner banner-warn" role="status">Posting waits: {c.desk_blocked}</p>}
        {data.writer === "rules" && <p className="muted small">Writing: Price Chat's rules writer (no Anthropic key). Add one in API keys for the model writer; its words pass the same checks.</p>}
        <div className="stat-grid">
          <Stat label="Hot topics now" value={data.topics.filter((t) => t.status === "active" && t.score >= s.hot_score).length} hint={`score ${s.hot_score}+`} />
          <Stat label="Waiting for you" value={data.queue.filter((p) => p.status === "pending").length} />
          <Stat label="X posts, 24 h (shared)" value={`${c.posts_24h.total} / ${c.shared_cap}`} hint={`News Desk ${c.posts_24h.newsdesk} · Trending ${c.posts_24h.trending} / ${s.max_per_day}`} />
          <Stat label="Next News Desk slot" value={c.next_newsdesk_slot ? new Date(c.next_newsdesk_slot).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "-"} hint={`trend posts keep ${s.newsdesk_gap_minutes} min clear`} />
          <Stat label="Last scan" value={data.last_run?.at ? relativeTime(data.last_run.at) : "Not yet"} />
        </div>
      </Card>

      <Card title="Sources">
        <table className="table small"><thead><tr><th>Source</th><th>Status</th><th>Last scan</th><th>Items</th><th>Requests today</th><th>Cost today</th><th>Problem</th></tr></thead>
          <tbody>{Object.keys(SOURCE).map((k) => {
            const h = data.health[k];
            const st = h?.status ?? (s.sources[k]?.on ? "idle" : "off");
            return (<tr key={k}><td>{SOURCE[k]}</td>
              <td><Badge tone={HEALTH_TONE[st] ?? "neutral"}>{st === "unavailable" && h?.plan ? "not on your X plan" : st}</Badge>{h?.fails ? <span className="small"> {h.fails} fails</span> : null}</td>
              <td>{h?.last_scan ? relativeTime(h.last_scan) : "-"}</td><td>{h?.items ?? "-"}</td><td>{usage.requests?.[k] ?? 0}</td>
              <td>{usage.cost_usd?.[k] ? `$${usage.cost_usd[k].toFixed(3)}` : "-"}</td><td>{h?.problem ?? h?.partial ?? ""}</td></tr>);
          })}</tbody></table>
        {health.length === 0 && <p className="small muted">No scan yet.</p>}
        <p className="small muted">Google Trends: the official "Trending now" RSS feed. Reddit: Reddit's OAuth API (set REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET). X: trends ${data.cost.x_trends_per_request_usd} a request, account posts ${data.cost.x_post_read_usd} each. Nothing is scraped.</p>
      </Card>

      {data.latency && (data.latency.recent?.length ?? 0) > 0 && <Card title="Speed: detection to publication">
        <div className="stats">
          <Stat label="Event → sent (median)" value={minutes(data.latency.event_to_publish?.median)} hint={`worst ${minutes(data.latency.event_to_publish?.max)} · ${data.latency.event_to_publish?.n ?? 0} posts`} />
          <Stat label="First seen → sent (median)" value={minutes(data.latency.detect_to_publish?.median)} hint={`worst ${minutes(data.latency.detect_to_publish?.max)}`} />
          <Stat label="Written → sent (median)" value={minutes(data.latency.written_to_publish?.median)} hint="time spent waiting on limits and spacing" />
        </div>
        <p className="small muted">Measured on each trend post (sent or recorded in dry run). Event = the first report of the story; first seen = our first scan that read it. Scans run every 5 minutes.</p>
      </Card>}

      <IncidentsCard incidents={data.incidents ?? []} missed={data.missed ?? []} />

      <Card title={`Trending now (${data.topics.length})`}>
        {data.topics.length === 0 ? <Empty>No topics yet: the first scan runs a few minutes after start, or press Scan now.</Empty> : (
          <table className="table small tt-topics"><thead><tr><th>Score</th><th>Topic</th><th>Why</th><th>Sources</th><th>Engagement</th><th>Suggested tweet</th></tr></thead>
            <tbody>{data.topics.map((t) => (
              <tr key={t.id}>
                <td><Badge tone={scoreTone(t.score, s)}>{t.score.toFixed(0)}</Badge>{t.status !== "active" && <div className="small muted">{t.status}</div>}</td>
                <td><b>{t.title}</b><div className="small muted">{CATEGORY[t.category] ?? t.category}{t.parts.timeline ? "" : ` · first seen ${relativeTime(t.first_seen)}`}</div>
                  <TimelineNote t={t.parts.timeline} />
                  {t.parts.stale && <Badge tone="neutral">stale</Badge>} <SensitiveBadge x={t.parts.sensitive} />
                  {t.parts.why && <div className="small">Why: {t.parts.why}</div>}
                  {t.parts.latency && <div className="small muted">posted: event → sent {minutes(t.parts.latency.event_to_publish_min)}, first seen → sent {minutes(t.parts.latency.detect_to_publish_min)}</div>}</td>
                <td>{PARTS.map(([k, name, max]) => <div key={k} className="small">{name} <ScoreBar value={Number(t.parts[k] ?? 0)} max={max} /> {Number(t.parts[k] ?? 0).toFixed(0)}</div>)}
                  {t.parts.learned !== undefined && t.parts.learned !== 1 && <div className="small muted">learned ×{t.parts.learned}</div>}
                  <GrowthNote g={t.parts.growth} /></td>
                <td>{(t.parts.sources ?? []).map((x) => <Badge key={x} tone="info">{SOURCE[x] ?? x}</Badge>)}
                  <div className="small">{t.sources.filter((x) => x.reliable).slice(0, 3).map((x, i) => <span key={x.url || i}>{i ? ", " : ""}<a href={x.url} target="_blank" rel="noreferrer">{x.outlet}</a></span>)}</div></td>
                <td className="small">{engagementText(t)}</td>
                <td className="small" style={{ maxWidth: 320 }}>{t.post ? <><Badge tone={STATUS_TONE[t.post.status] ?? "neutral"}>{t.post.status === "dry_run" ? "dry run" : t.post.status}</Badge> <span style={{ whiteSpace: "pre-wrap" }}>{t.post.hold_reason ? t.post.hold_reason : t.post.text}</span></>
                  : <span className="muted">{t.score >= s.suggest_score ? "next scan" : `below ${s.suggest_score}`}</span>}</td>
              </tr>))}</tbody></table>)}
      </Card>

      <Card title={`Suggested tweets (${data.queue.length})`}>
        {data.queue.length === 0 ? <Empty>Nothing waiting. Topics scoring {s.suggest_score}+ get a tweet here.</Empty> : data.queue.map((p) => <PostCard key={p.id} p={p} busy={busy} act={act} s={s} />)}
      </Card>

      {data.held.length > 0 && <Card title={`Held by the checks (${data.held.length})`}>
        <p className="small muted">Never sent: no reliable source, a claim the sources don't make, more than one cashtag, or the News Desk already has the story.</p>
        {data.held.map((p) => <PostCard key={p.id} p={p} busy={busy} act={act} s={s} />)}
      </Card>}

      <Card title="Posted and dry runs">
        {data.published.length === 0 ? <Empty>Nothing posted yet.</Empty> : data.published.map((p) => <PostCard key={p.id} p={p} busy={busy} act={act} s={s} />)}
        <p className="small muted">Learning from X metrics: {data.learning.learning ? `${data.learning.measured_posts} posts measured; weights ${Object.entries(data.learning.weights).map(([k, v]) => `${k} ×${v}`).join(", ") || "neutral"}`
          : `needs ${data.learning.needs} measured posts (has ${data.learning.measured_posts})`}. Weights stay between ×{data.learning.bounds[0]} and ×{data.learning.bounds[1]}.</p>
      </Card>

      <Card title="Performance">
        {data.learning.measured_posts === 0 ? <Empty>No X metrics yet: they are read once a day for posts of the last 3 days (owned reads, $0.001 each).</Empty> : <>
          <div className="stat-grid">
            <Stat label="Impressions" value={(data.learning.totals.impression_count ?? 0).toLocaleString()} />
            <Stat label="Likes" value={(data.learning.totals.like_count ?? 0).toLocaleString()} />
            <Stat label="Reposts" value={(data.learning.totals.retweet_count ?? 0).toLocaleString()} />
            <Stat label="Replies" value={(data.learning.totals.reply_count ?? 0).toLocaleString()} />
          </div>
          <table className="table small"><thead><tr><th>Category</th><th>Posts</th><th>Impressions</th><th>Likes</th><th>Reposts</th><th>Replies</th><th>Engagement rate</th><th>Ranking weight</th></tr></thead>
            <tbody>{Object.entries(data.learning.per_category ?? {}).map(([k, v]) => <tr key={k}><td>{CATEGORY[k] ?? k}</td><td>{v.posts}</td><td>{v.impressions.toLocaleString()}</td><td>{v.likes}</td><td>{v.reposts}</td><td>{v.replies}</td>
              <td>{(v.rate * 100).toFixed(2)}%</td><td>×{data.learning.weights[`cat:${k}`] ?? 1}</td></tr>)}</tbody></table></>}
        {Object.keys(data.learning.weights).some((k) => k.startsWith("src:")) && <p className="small">Source weights: {Object.entries(data.learning.weights).filter(([k]) => k.startsWith("src:")).map(([k, v]) => `${SOURCE[k.slice(4)] ?? k} ×${v}`).join(", ")}</p>}
      </Card>

      <Card title="Settings">
        <div className="row" style={{ gap: 16, flexWrap: "wrap" }}>
          {num("hot_score", "Hot threshold", 30, 100)}
          {num("suggest_score", "Suggest threshold", 10, 100)}
          {num("max_per_day", "Trend posts a day", 0, 24)}
          {num("gap_minutes", "Minutes between trend posts", 15, 1440)}
          {num("topic_cooldown_hours", "Topic cooldown (hours)", 1, 168)}
          {num("newsdesk_gap_minutes", "Clear of News Desk (minutes)", 0, 240)}
          {num("shared_daily_cap", "Shared X cap a day", 1, 96)}
        </div>
        <div className="row" style={{ gap: 12, flexWrap: "wrap", marginTop: 12 }}>
          {Object.entries(CATEGORY).map(([k, name]) => <label key={k}><input type="checkbox" checked={s.categories[k] !== false} disabled={busy} onChange={(e) => void save({ categories: { ...s.categories, [k]: e.target.checked } })} /> {name}</label>)}
        </div>
        <div className="row" style={{ gap: 12, flexWrap: "wrap", marginTop: 12 }}>
          {Object.entries(SOURCE).map(([k, name]) => <label key={k}><input type="checkbox" checked={!!s.sources[k]?.on} disabled={busy} onChange={(e) => void save({ sources: { [k]: { on: e.target.checked } } })} /> {name}</label>)}
          <label title="Reddit's public listings, without OAuth. Reddit rate-limits (and may refuse) unauthenticated clients."><input type="checkbox" checked={!!s.sources.reddit?.public_json} disabled={busy}
            onChange={(e) => void save({ sources: { reddit: { public_json: e.target.checked } } })} /> Reddit without OAuth (public JSON)</label>
        </div>
      </Card>
    </div>
  );
}
