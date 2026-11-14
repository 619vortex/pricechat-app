import { Fragment, useCallback, useState, type FormEvent, type ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import type { Approval, MetricSnapshot, MonitorRuleType, SocialOpportunity, SocialOpportunityStatus, SocialPost, WatchCategory, WatchedAccount, XCredentialsInput, XStatus } from "../api/types";
import { TrendBadge } from "../components/community";
import { GrowthPilot } from "./x/GrowthPilot";
import { NewsDesk } from "./x/NewsDesk";
import { TrendingTopics } from "./x/TrendingTopics";
import { CapabilityBadge, compact, FeedbackButtons, GuardChecks, Reasons, SignalBars } from "../components/social";
import { Advanced, ApiErrorBanner, Badge, Card, Empty, Loading, PageHeader, Stat, Tabs, formatTime, label, relativeTime, type Tone } from "../components/ui";

type Tab = "newsdesk" | "trending" | "growth" | "overview" | "mentions" | "accounts" | "topics" | "opportunities" | "own" | "analytics" | "connection";
const TABS: { key: Tab; label: string; more?: boolean }[] = [
  { key: "overview", label: "Overview" },
  { key: "newsdesk", label: "News Desk" },
  { key: "trending", label: "Trending Topics" },
  { key: "opportunities", label: "Post ideas" },
  { key: "growth", label: "Growth Pilot" },
  { key: "connection", label: "Connect X" },
  { key: "mentions", label: "Mentions", more: true },
  { key: "own", label: "Our posts", more: true },
  { key: "accounts", label: "Accounts we follow", more: true },
  { key: "topics", label: "Topics we watch", more: true },
  { key: "analytics", label: "Analytics", more: true },
];

const STATUS_TEXT: Record<string, [string, Tone]> = {
  not_configured: ["Not connected", "neutral"],
  disconnected: ["Saved, not tested", "warn"],
  connected: ["Connected", "good"],
  error: ["Connection problem", "bad"],
  paused: ["Reading paused", "warn"],
};

export function XPage() {
  const [params, setParams] = useSearchParams();
  const tab = (TABS.find((t) => t.key === params.get("tab"))?.key ?? "overview") as Tab;
  const go = (t: Tab) => setParams(t === "overview" ? {} : { tab: t });
  return (
    <>
      <PageHeader title="X / Twitter" subtitle="What's being said on X and post ideas. Nothing is posted without your approval." />
      <Tabs tabs={TABS} value={tab} onChange={go} />
      {tab === "growth" && <GrowthPilot />}
      {tab === "newsdesk" && <NewsDesk />}
      {tab === "trending" && <TrendingTopics />}
      {tab === "overview" && <OverviewTab go={go} />}
      {tab === "mentions" && <PostsTab source="mention" empty="No mentions yet." intro="Posts that mention your account, newest first." />}
      {tab === "accounts" && <AccountsTab />}
      {tab === "topics" && <RulesTab />}
      {tab === "opportunities" && <OpportunitiesTab />}
      {tab === "own" && <PostsTab source="own" empty="None of your posts have been read yet." intro="Your own recent posts and the metrics X reports for them." />}
      {tab === "analytics" && <AnalyticsTab />}
      {tab === "connection" && <ConnectionTab />}
    </>
  );
}

function StatusBadge({ status }: { status: string }) {
  const [text, tone] = STATUS_TEXT[status] ?? [label(status), "neutral" as Tone];
  return <Badge tone={tone}>{text}</Badge>;
}

function NotConnected() {
  return (
    <div className="banner banner-info">
      X is not connected yet. Open the <Link className="link" to="/x?tab=connection">Connection</Link> tab, paste your X API keys and press “Test connection”.
    </div>
  );
}

// --- overview ------------------------------------------------------------------------------------

const ACTION_LABEL: Record<string, string> = { post: "Posts", reply: "Replies", like: "Likes", repost: "Reposts", follow: "Follows" };

/** Supervised operation: status, switches and today's actions against the safety ceilings. */
function OperatorPanel() {
  const { data, failure, reload } = useApi(api.xOperator, 15000);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  async function run(fn: () => Promise<unknown>, msg: string) {
    setBusy(true);
    try {
      await fn();
      setNotice(msg);
      setError(null);
      await reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const caps = Object.values(data.write_capability);
  const writeText = !data.user_context
    ? "Needs user credentials (Access Token + Secret)"
    : caps.every((c) => c === "unavailable")
      ? "Read-only token: regenerate with Read and write"
      : caps.some((c) => c === "available")
        ? "Available"
        : "Not confirmed yet (confirmed on the first approved action)";
  return (
    <Card
      title="X operator"
      actions={
        <div className="switch-row">
          <Badge tone={data.social_actions_active ? "good" : "warn"}>SOCIAL ACTIONS {data.social_actions_active ? "ACTIVE" : "PAUSED"}</Badge>
          <button
            className={`btn btn-sm ${data.social_actions_active ? "" : "btn-primary"}`}
            disabled={busy}
            onClick={() => void run(() => api.setSocialActions(!data.social_actions_active), data.social_actions_active ? "Social actions paused: nothing can be sent." : "Social actions active (every action still needs your approval).")}
          >
            {data.social_actions_active ? "Pause" : "Activate"}
          </button>
          <Badge tone={data.dry_run ? "info" : "warn"}>{data.dry_run ? "DRY RUN" : "LIVE"}</Badge>
          <button
            className="btn btn-sm"
            disabled={busy}
            onClick={() => {
              if (data.dry_run && !window.confirm("Turn dry run OFF? From now on, actions you approve are really sent to X.")) return;
              void run(() => api.updateGuard({ dry_run: !data.dry_run }), data.dry_run ? "Dry run is off: approved actions are sent to X." : "Dry run is on: approved actions are checked but not sent.");
            }}
          >
            {data.dry_run ? "Go live" : "Back to dry run"}
          </button>
        </div>
      }
    >
      <dl className="kv">
        <dt>X status</dt>
        <dd>
          <StatusBadge status={data.x_status} /> {data.username ? `@${data.username}` : ""}
        </dd>
        <dt>Mode</dt>
        <dd>
          Approval required for every action <span className="muted small">· Automatic: {data.automatic}</span>
        </dd>
        <dt>Write access</dt>
        <dd>{writeText}</dd>
      </dl>
      <div className="field-label" style={{ marginTop: 12 }}>
        Today (safety ceilings, not targets)
      </div>
      <div className="operator-today">
        {data.today.map((t) => (
          <div key={t.action}>
            <div className="muted small">{ACTION_LABEL[t.action] ?? t.action}</div>
            <strong>
              {t.done} / {t.limit}
            </strong>
            <div className="muted small">max {t.per_hour}/hour</div>
          </div>
        ))}
      </div>
      <dl className="kv" style={{ marginTop: 12 }}>
        <dt>Pending approvals</dt>
        <dd>
          <Link className="link" to="/approvals">
            {data.pending_approvals}
          </Link>
        </dd>
        <dt>Opportunities</dt>
        <dd>{data.opportunities}</dd>
        <dt>AI</dt>
        <dd className="small">
          screening {data.ai.screening_model ?? "-"} · analysis {data.ai.analysis_model ?? "-"}{" "}
          <Badge tone={data.ai.analysis_status === "running" ? "info" : data.ai.analysis_status === "failed" ? "bad" : "neutral"}>
            {data.ai.analysis_status === "running" ? "analysing…" : data.ai.analysis_status ?? "not run yet"}
          </Badge>{" "}
          {data.ai.last_analysis_at ? `last ${relativeTime(data.ai.last_analysis_at)}` : ""}
        </dd>
        <dt>X writes today (est.)</dt>
        <dd className="small">
          {data.usage_today.writes ?? 0} requests · ${(data.usage_today.write_cost ?? 0).toFixed(3)} (local estimate)
        </dd>
      </dl>
      {data.learning.length > 0 && (
        <>
          <div className="field-label" style={{ marginTop: 12 }}>
            Learned from your decisions
          </div>
          <ul className="reasons">
            {data.learning.map((l) => (
              <li key={l.key}>{l.text}</li>
            ))}
          </ul>
        </>
      )}
      <div className="btn-group" style={{ marginTop: 12 }}>
        <button className="btn btn-sm" disabled={busy} onClick={() => void run(() => api.prepareTestPost(), "A harmless test post is waiting in the approval queue. Nothing is sent until you approve it.")}>
          Prepare test post
        </button>
        <Link className="link small" to="/approvals">
          Open approval queue →
        </Link>
      </div>
      {notice && <div className="banner banner-good">{notice}</div>}
      <ApiErrorBanner error={error} />
    </Card>
  );
}

function OverviewTab({ go }: { go: (t: Tab) => void }) {
  const { data, failure, reload } = useApi(api.xOverview, 30000);
  const signals = useApi(() => api.crossSignals(), 60000);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  async function sync() {
    setBusy(true);
    try {
      const r = await api.syncX();
      const res = r.result as { new_posts?: number; requests?: number; stopped?: string | null };
      setResult(
        r.status === "success"
          ? `Read ${res.new_posts ?? 0} new post(s) with ${res.requests ?? 0} API request(s).${res.stopped ? ` Stopped early: ${res.stopped}` : ""}`
          : r.status === "queued"
            ? "Queued: another background job is running. X reading starts right after it; this page updates by itself."
            : `${label(r.status)}: ${r.error ?? ""}`,
      );
      setError(null);
      await reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const st = data.status;
  return (
    <>
      {!st.configured && <NotConnected />}
      {st.configured && <OperatorPanel />}
      <div className="stats">
        <Stat label="Status" value={<StatusBadge status={st.status} />} hint={st.account_username ? `@${st.account_username}` : undefined} />
        <Stat label="Post ideas" value={data.open_opportunities} tone={data.open_opportunities ? "warn" : "neutral"} hint={<button className="link-btn" onClick={() => go("opportunities")}>see them</button>} />
        <Stat label="Posts read" value={data.posts_total} hint={`${data.posts_today} today · ${data.mentions} mentions`} />
        <Stat label="X cost today" value={`$${(st.usage_today.cost ?? 0).toFixed(2)}`} hint={`limit $${st.budget.daily_usd.toFixed(2)} a day`} />
      </div>
      {st.blocked_until && new Date(st.blocked_until) > new Date() && (
        <div className="banner banner-warn">X asked Price Chat to slow down (rate limit or usage cap). Reading resumes automatically after {formatTime(st.blocked_until)}.</div>
      )}
      <Advanced label="Reading from X and the AI behind it">
      <div className="grid-2">
        <Card
          title="Reading"
          actions={
            st.configured && (
              <button className="btn btn-sm" disabled={busy || st.paused} onClick={() => void sync()}>
                {busy ? "Reading…" : "Read now"}
              </button>
            )
          }
        >
          <dl className="kv">
            <dt>Last successful read</dt>
            <dd>{relativeTime(st.last_success_at)}</dd>
            <dt>Requests today</dt>
            <dd>{st.usage_today.requests ?? 0}</dd>
            <dt>Last error</dt>
            <dd className={st.last_error ? "tone-bad" : "muted"}>{st.last_error ? `${st.last_error} (${relativeTime(st.last_error_at)})` : "none"}</dd>
          </dl>
          {result && <div className="banner banner-good">{result}</div>}
          <ApiErrorBanner error={error} />
          <p className="muted small">Price Chat reads X every 15 minutes, only what is new since the last read, and stops at your daily budget.</p>
        </Card>
        <AiAnalysisCard overview={data} />
      </div>
        <p className="muted small">Following {data.monitored_accounts} accounts and {data.rules} topics. Est. ${(st.usage_month.cost ?? 0).toFixed(2)} of ${st.budget.monthly_usd.toFixed(2)} this month.</p>
      </Advanced>
      <div className="grid-2">
        <Card title="Monitored topics on X (last 24 h)">
          {data.topic_activity.length === 0 ? (
            <Empty>No monitored topics yet. Add some on the Monitored topics tab.</Empty>
          ) : (
            <ul className="feed">
              {data.topic_activity.map((t) => (
                <li key={t.id}>
                  <TrendBadge trend={t.trend} />
                  <div className="feed-body">
                    <span>
                      {t.name}
                      {!t.enabled && <span className="muted"> (off)</span>}
                    </span>
                    <span className="muted small">
                      {t.posts_24h} posts found in 24 h · {t.posts_prev_24h} the day before
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <p className="muted small">{data.cost_note}</p>
        </Card>
        <Card title="Telegram ↔ X signals">
          <CrossSignalList signals={signals.data ?? []} onChanged={() => void signals.reload()} />
        </Card>
      </div>
    </>
  );
}

/** The background AI analysis of X posts, shown separately so X data never waits for it. */
function AiAnalysisCard({ overview }: { overview: import("../api/types").XOverview }) {
  const jobs = useApi(api.jobs, 10000);
  const routing = useApi(api.routing);
  const job = jobs.data?.find((j) => j.name === "x.opportunities");
  const model = (task: string) => routing.data?.routes.find((r) => r.task === task)?.model;
  const st = overview.status;
  const err = job?.last_error ?? "";

  let tone: "good" | "warn" | "bad" | "info" | "neutral" = "neutral";
  let text: string;
  let detail: string | null = null;
  if (!st.configured) {
    text = "Waiting for X to be connected";
  } else if (st.status === "error") {
    tone = "bad";
    text = "X API error";
    detail = st.last_error ?? "X rejected the request. See the Connection tab.";
  } else if (overview.posts_total === 0) {
    text = "No X data yet";
    detail = "Nothing to analyse yet, so no model is called. Add monitored topics or accounts, then Read now.";
  } else if (job?.running || job?.status === "running") {
    tone = "info";
    text = "Analyzing X activity…";
    detail = `Started ${relativeTime(job?.last_run_at)}. The strong model may take a minute, longer while it loads; results appear under Opportunities.`;
  } else if (/timed out|timeout|took too long/i.test(err)) {
    tone = "warn";
    text = "AI timeout";
    detail = `${err} It will retry automatically.`;
  } else if (/not installed|model_unavailable|not found/i.test(err)) {
    tone = "warn";
    text = "AI model unavailable";
    detail = err;
  } else if (/unavailable|cannot reach|connection/i.test(err)) {
    tone = "warn";
    text = "AI unavailable";
    detail = `${err} Make sure Ollama is running.`;
  } else if (job?.status === "failed") {
    tone = "bad";
    text = "AI analysis failed";
    detail = err || null;
  } else if (job?.last_finished_at) {
    tone = "good";
    const r = job.last_result as { screened?: number; created?: number };
    text = "Up to date";
    detail = `Last run ${relativeTime(job.last_finished_at)} in ${job.last_duration_ms != null ? `${Math.round(job.last_duration_ms / 1000)} s` : "-"}: screened ${r.screened ?? 0} post(s), ${r.created ?? 0} new opportunit${r.created === 1 ? "y" : "ies"}.`;
  } else {
    text = "Waiting for the first analysis";
    detail = "Runs automatically after new posts are read.";
  }

  return (
    <Card title="AI analysis">
      <div className="row" style={{ justifyContent: "flex-start" }}>
        <Badge tone={tone}>{text}</Badge>
      </div>
      {detail && <p className="muted small">{detail}</p>}
      <dl className="kv">
        <dt>Screening</dt>
        <dd className="mono small">{model("x.relevance") ?? "…"} (fast)</dd>
        <dt>Analysis</dt>
        <dd className="mono small">{model("x.opportunity") ?? "…"} (strong, only for relevant posts)</dd>
      </dl>
      <p className="muted small">Runs in the background; X data above never waits for it.</p>
    </Card>
  );
}

export function CrossSignalList({ signals, onChanged }: { signals: import("../api/types").CrossSignal[]; onChanged?: () => void }) {
  if (!signals.length) return <Empty>No topic is being discussed in both your Telegram community and on X right now.</Empty>;
  return (
    <ul className="feed">
      {signals.map((s) => (
        <li key={s.id}>
          {s.early_signal ? (
            <Badge tone="warn" title="Discussed in the Telegram community before monitored X accounts. A content signal, not a market prediction.">
              EARLY COMMUNITY SIGNAL{s.lead_minutes ? ` · ${s.lead_minutes >= 120 ? `${Math.round(s.lead_minutes / 60)} h` : `${s.lead_minutes} min`} earlier` : ""}
            </Badge>
          ) : (
            <Badge tone="info">both</Badge>
          )}
          <div className="feed-body">
            <span>
              {s.telegram_topic_id ? <Link className="link" to={`/telegram/topics/${s.telegram_topic_id}`}>{s.topic_name}</Link> : s.topic_name}
            </span>
            <span className="muted small">
              Telegram: {s.telegram_messages_total} messages from {s.telegram_participants} participants ({s.telegram_messages_24h} in 24 h, {label(s.telegram_trend)}) · X: {s.x_posts} posts from{" "}
              {s.x_authors} author{s.x_authors === 1 ? "" : "s"}
              {s.x_watched_authors ? ` (${s.x_watched_authors} monitored)` : ""} ({s.x_posts_24h} in 24 h, {label(s.x_trend)})
            </span>
            <span className="muted small">Interpretation: the same topic is active in both places. This is a content signal, not evidence of a market event.
            </span>
            <span className="small">{s.recommendation}</span>
          </div>
          {onChanged && (
            <button className="btn btn-ghost btn-sm" title="Dismiss" onClick={() => void api.dismissCrossSignal(s.id).then(onChanged)}>
              ✕
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}

// --- posts ---------------------------------------------------------------------------------------

function Metrics({ m }: { m: Record<string, number | null> }) {
  const keys: [string, string][] = [
    ["impressions", "views"],
    ["likes", "likes"],
    ["replies", "replies"],
    ["reposts", "reposts"],
    ["quotes", "quotes"],
    ["bookmarks", "bookmarks"],
  ];
  const shown = keys.filter(([k]) => k in m);
  if (!shown.length) return null;
  return (
    <div className="x-metrics">
      {shown.map(([k, text]) => (
        <span key={k} title={m[k] == null ? "Not provided by X" : undefined}>
          {compact(m[k])} {text}
        </span>
      ))}
    </div>
  );
}

export function PostItem({ p, extra }: { p: SocialPost; extra?: ReactNode }) {
  return (
    <div className="x-post">
      <div className="x-post-head">
        <strong>{p.author_name ?? p.author_username ?? "unknown"}</strong>
        {p.author_username && <span className="muted">@{p.author_username}</span>}
        {p.author_followers != null && <span className="muted small">{compact(p.author_followers)} followers</span>}
        <span className="muted small">{formatTime(p.posted_at)}</span>
        {p.topics.map((t) => (
          <Badge key={`t-${t}`} tone="info">
            {t}
          </Badge>
        ))}
        {p.source_kinds.map((k) => (
          <Badge key={k}>{k}</Badge>
        ))}
        {p.url && (
          <a className="link small push-right" href={p.url} target="_blank" rel="noreferrer">
            open on X ↗
          </a>
        )}
      </div>
      <div className="x-post-text">{p.text}</div>
      <Metrics m={p.metrics} />
      {extra}
    </div>
  );
}

function PostsTab({ source, empty, intro }: { source: "own" | "mention"; empty: string; intro: string }) {
  const fetcher = useCallback(() => api.xPosts({ source }), [source]);
  const { data, failure } = useApi(fetcher, 60000);
  const [history, setHistory] = useState<{ id: number; rows: MetricSnapshot[] } | null>(null);
  return (
    <Card title={source === "own" ? "Our posts" : "Mentions"}>
      <p className="muted small">{intro}</p>
      <ApiErrorBanner error={failure} />
      {!data ? (
        <Loading />
      ) : data.length === 0 ? (
        <Empty>{empty}</Empty>
      ) : (
        data.map((p) => (
          <PostItem
            key={p.id}
            p={p}
            extra={
              source === "own" && (
                <>
                  <button className="link-btn small" onClick={() => void api.xPostMetrics(p.id).then((rows) => setHistory(history?.id === p.id ? null : { id: p.id, rows }))}>
                    {history?.id === p.id ? "Hide metric history" : "Metric history"}
                  </button>
                  {history?.id === p.id && <MetricHistory rows={history.rows} />}
                </>
              )
            }
          />
        ))
      )}
    </Card>
  );
}

function MetricHistory({ rows }: { rows: MetricSnapshot[] }) {
  if (!rows.length) return <Empty>No snapshots yet.</Empty>;
  const cols = ["impressions", "likes", "replies", "reposts", "quotes", "bookmarks", "profile_visits", "url_clicks"] as const;
  return (
    <table className="table">
      <thead>
        <tr>
          <th>Captured</th>
          {cols.map((c) => (
            <th key={c}>{label(c)}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.captured_at}>
            <td className="small nowrap">{formatTime(r.captured_at)}</td>
            {cols.map((c) => (
              <td key={c} className={r[c] == null ? "muted small" : "small"}>
                {r[c] == null ? "n/a" : r[c]}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// --- monitored accounts ---------------------------------------------------------------------------

const CATEGORIES: WatchCategory[] = ["major_creator", "news", "project", "community_member", "partner", "emerging_creator", "industry", "other"];
const CATEGORY_LABEL: Record<WatchCategory, string> = {
  major_creator: "Creator",
  news: "News",
  project: "Project",
  community_member: "Community",
  partner: "Partner",
  emerging_creator: "Emerging creator",
  industry: "Industry",
  other: "Other",
};
const PRIORITIES = [1, 2, 3, 4, 5];

function AccountsTab() {
  const { data, failure, reload } = useApi(api.xWatchlist);
  const [username, setUsername] = useState("");
  const [category, setCategory] = useState<WatchCategory>("industry");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState<number | null>(null);
  async function run(fn: () => Promise<unknown>) {
    try {
      await fn();
      setError(null);
      await reload();
    } catch (e) {
      setError(e);
    }
  }
  async function add(e: FormEvent) {
    e.preventDefault();
    await run(() => api.watchX({ username: username.trim(), category, reason: reason.trim() }));
    setUsername("");
    setReason("");
  }
  return (
    <Card title="Monitored accounts">
      <p className="muted small">Public accounts Price Chat reads regularly. Relationship memory only records public interactions with your account (mentions and replies); nothing sensitive is inferred.</p>
      <ApiErrorBanner error={failure ?? error} />
      <form className="goal-add" onSubmit={add}>
        <input className="input" placeholder="@username" value={username} onChange={(e) => setUsername(e.target.value)} maxLength={16} />
        <select className="input select" value={category} onChange={(e) => setCategory(e.target.value as WatchCategory)} aria-label="Category">
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>
              {CATEGORY_LABEL[c]}
            </option>
          ))}
        </select>
        <input className="input" placeholder="Why watch this account? (optional)" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} />
        <button className="btn btn-primary" disabled={!/^@?[A-Za-z0-9_]{1,15}$/.test(username.trim())}>
          Watch
        </button>
      </form>
      {!data ? (
        <Loading />
      ) : data.length === 0 ? (
        <Empty>No accounts monitored yet.</Empty>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Account</th>
              <th>Category</th>
              <th>Priority</th>
              <th>Followers</th>
              <th>Interactions with us</th>
              <th>Last checked</th>
              <th>On</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.map((w) => (
              <AccountRow key={w.id} w={w} open={open === w.id} onToggle={() => setOpen(open === w.id ? null : w.id)} run={run} />
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function AccountRow({ w, open, onToggle, run }: { w: WatchedAccount; open: boolean; onToggle: () => void; run: (fn: () => Promise<unknown>) => Promise<void> }) {
  const [notes, setNotes] = useState(w.relationship?.notes ?? "");
  const [watchNotes, setWatchNotes] = useState(w.notes);
  const rel = w.relationship;
  return (
    <>
      <tr>
        <td>
          <button className="link-btn" onClick={onToggle}>
            {w.profile.display_name ?? w.profile.username}
          </button>
          <div className="muted small">
            @{w.profile.username}
            {w.reason ? ` · ${w.reason}` : ""}
            {w.last_error ? <span className="tone-bad"> · {w.last_error}</span> : null}
          </div>
        </td>
        <td>
          <select className="input select select-sm" value={w.category} onChange={(e) => void run(() => api.updateWatch(w.id, { category: e.target.value as WatchCategory }))} aria-label="Category">
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {CATEGORY_LABEL[c]}
              </option>
            ))}
          </select>
        </td>
        <td>
          <select className="input select select-sm" value={w.priority} onChange={(e) => void run(() => api.updateWatch(w.id, { priority: Number(e.target.value) }))} aria-label="Priority" title="1 = highest">
            {PRIORITIES.map((n) => (
              <option key={n} value={n}>
                P{n}
              </option>
            ))}
          </select>
        </td>
        <td>{compact(w.profile.followers_count)}</td>
        <td>{rel ? `${rel.interaction_count} (${rel.mention_count} mentions, ${rel.reply_count} replies)` : <span className="muted">none</span>}</td>
        <td className="muted small nowrap">
          {relativeTime(w.last_checked_at)}
          {w.last_checked_at && <div>{w.last_success_at ? `ok ${relativeTime(w.last_success_at)}` : "no success yet"}</div>}
        </td>
        <td>
          <label className="toggle">
            <input type="checkbox" checked={w.enabled} onChange={(e) => void run(() => api.updateWatch(w.id, { enabled: e.target.checked }))} />
            <span />
          </label>
        </td>
        <td>
          <button className="btn btn-ghost btn-sm" aria-label="Stop watching" onClick={() => void run(() => api.unwatchX(w.id))}>
            ✕
          </button>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={8}>
            <dl className="kv">
              <dt>Account ID</dt>
              <dd className="mono small">{w.profile.external_id}</dd>
              <dt>Why watched</dt>
              <dd>
                <div className="row">
                  <input className="input" value={watchNotes} onChange={(e) => setWatchNotes(e.target.value)} maxLength={2000} placeholder="Notes about why you monitor this account" />
                  <button className="btn btn-sm" onClick={() => void run(() => api.updateWatch(w.id, { notes: watchNotes }))}>
                    Save
                  </button>
                </div>
              </dd>
              <dt>First interaction</dt>
              <dd>{formatTime(rel?.first_interaction_at)}</dd>
              <dt>Last interaction</dt>
              <dd>{formatTime(rel?.last_interaction_at)}</dd>
              <dt>Topics they discuss with us</dt>
              <dd>{rel && Object.keys(rel.topics).length ? Object.entries(rel.topics).map(([t, n]) => `${t} (${n})`).join(", ") : <span className="muted">none yet</span>}</dd>
              <dt>Follows us</dt>
              <dd className="muted">{rel?.they_follow_us == null ? "unknown (not available from the API)" : rel.they_follow_us ? "yes" : "no"}</dd>
              <dt>Relationship notes</dt>
              <dd>
                <div className="row">
                  <input className="input" value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={2000} placeholder="Private notes about this account" />
                  <button className="btn btn-sm" onClick={() => void run(() => api.setRelationshipNotes(w.profile.id, notes))}>
                    Save
                  </button>
                </div>
              </dd>
            </dl>
            <AccountPosts profileId={w.profile.id} />
          </td>
        </tr>
      )}
    </>
  );
}

function AccountPosts({ profileId }: { profileId: number }) {
  const fetcher = useCallback(() => api.xPosts({ profile_id: profileId }), [profileId]);
  const { data } = useApi(fetcher);
  if (!data) return <Loading />;
  if (!data.length) return <Empty>No posts read from this account yet.</Empty>;
  return (
    <div className="stack">
      {data.slice(0, 10).map((p) => (
        <PostItem key={p.id} p={p} />
      ))}
    </div>
  );
}

// --- monitored topics (search rules) -----------------------------------------------------------

// Ready-made monitored topics (X search syntax; retweets excluded, English only to keep cost and noise down).
const TOPIC_PRESETS: { name: string; query: string }[] = [
  { name: "Bitcoin", query: "(bitcoin OR btc) lang:en -is:retweet" },
  { name: "Ethereum", query: "(ethereum OR eth) lang:en -is:retweet" },
  { name: "Gold", query: '("gold price" OR "price of gold") lang:en -is:retweet' },
  { name: "Real estate", query: '("real estate" OR "property market" OR "house prices") lang:en -is:retweet' },
  { name: "Collectibles", query: '(collectibles OR "trading cards" OR "watch market") lang:en -is:retweet' },
  { name: "Cars", query: '("car prices" OR "classic cars" OR supercar) lang:en -is:retweet' },
  { name: "Markets", query: '("stock market" OR "S&P 500" OR nasdaq) lang:en -is:retweet' },
];

function RulesTab() {
  const { data, failure, reload } = useApi(api.xRules);
  const [name, setName] = useState("");
  const [query, setQuery] = useState("");
  const [type, setType] = useState<MonitorRuleType>("keyword");
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState<number | null>(null);
  async function run(fn: () => Promise<unknown>) {
    try {
      await fn();
      setError(null);
      await reload();
    } catch (e) {
      setError(e);
    }
  }
  async function add(e: FormEvent) {
    e.preventDefault();
    await run(() => api.addXRule({ name: name.trim() || query.trim(), type, query: query.trim(), max_results: 10 }));
    setName("");
    setQuery("");
  }
  const existing = new Set((data ?? []).map((r) => r.name.toLowerCase()));
  const presets = TOPIC_PRESETS.filter((t) => !existing.has(t.name.toLowerCase()));
  return (
    <Card title="Monitored topics">
      <p className="muted small">
        Keywords and searches Price Chat checks on X (recent posts, last 7 days). Each check costs a little: about $0.005 per post read, at most 10 posts per topic per check. Retweets are excluded.
      </p>
      <ApiErrorBanner error={failure ?? error} />
      {presets.length > 0 && (
        <div className="chips">
          <span className="muted small">Quick add:</span>
          {presets.map((t) => (
            <button key={t.name} className="chip chip-btn" title={t.query} onClick={() => void run(() => api.addXRule({ name: t.name, type: "search", query: t.query, max_results: 10 }))}>
              + {t.name}
            </button>
          ))}
        </div>
      )}
      <form className="goal-add" onSubmit={add}>
        <select className="input select" value={type} onChange={(e) => setType(e.target.value as MonitorRuleType)} aria-label="Type">
          <option value="keyword">Keywords</option>
          <option value="search">Advanced X search</option>
          <option value="account">Posts from an account</option>
        </select>
        <input className="input" placeholder={type === "search" ? '"funding rate" lang:en -is:reply' : type === "account" ? "username" : "funding rates"} value={query} onChange={(e) => setQuery(e.target.value)} maxLength={512} />
        <input className="input" placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} maxLength={160} />
        <button className="btn btn-primary" disabled={!query.trim()}>
          Add
        </button>
      </form>
      {!data ? (
        <Loading />
      ) : data.length === 0 ? (
        <Empty>No monitored topics yet.</Empty>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Topic</th>
              <th>Type</th>
              <th>Priority</th>
              <th>Posts found</th>
              <th>Last checked</th>
              <th>On</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.map((r) => (
              <Fragment key={r.id}>
                <tr>
                  <td>
                    <button className="link-btn" onClick={() => setOpen(open === r.id ? null : r.id)}>
                      {r.name}
                    </button>
                    <div className="muted small mono">{r.query}</div>
                    {r.last_error && <div className="tone-bad small">{r.last_error}</div>}
                  </td>
                  <td>{label(r.type)}</td>
                  <td>
                    <select className="input select select-sm" value={r.priority} onChange={(e) => void run(() => api.updateXRule(r.id, { priority: Number(e.target.value) }))} aria-label="Priority" title="1 = checked first">
                      {[1, 2, 3, 4, 5].map((n) => (
                        <option key={n} value={n}>
                          P{n}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    {r.posts_matched}{" "}
                    <span className="muted small">
                      ({r.posts_24h} in 24 h · last check {r.last_result_count})
                    </span>
                  </td>
                  <td className="muted small nowrap">
                    {relativeTime(r.last_checked_at)}
                    {r.last_checked_at && <div>{r.last_success_at ? `ok ${relativeTime(r.last_success_at)}` : "no success yet"}</div>}
                  </td>
                  <td>
                    <label className="toggle">
                      <input type="checkbox" checked={r.enabled} onChange={(e) => void run(() => api.updateXRule(r.id, { enabled: e.target.checked }))} />
                      <span />
                    </label>
                  </td>
                  <td>
                    <button className="btn btn-ghost btn-sm" aria-label="Delete" onClick={() => void run(() => api.deleteXRule(r.id))}>
                      ✕
                    </button>
                  </td>
                </tr>
                {open === r.id && (
                  <tr>
                    <td colSpan={7}>
                      <RulePosts ruleId={r.id} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function RulePosts({ ruleId }: { ruleId: number }) {
  const fetcher = useCallback(() => api.xPosts({ rule_id: ruleId }), [ruleId]);
  const { data } = useApi(fetcher);
  if (!data) return <Loading />;
  if (!data.length) return <Empty>No matching posts yet.</Empty>;
  return (
    <div className="stack">
      {data.slice(0, 15).map((p) => (
        <PostItem key={p.id} p={p} />
      ))}
    </div>
  );
}

// --- opportunities -----------------------------------------------------------------------------------

const OPP_FILTERS: { key: SocialOpportunityStatus; label: string }[] = [
  { key: "new", label: "New" },
  { key: "reviewing", label: "In approval queue" },
  { key: "accepted", label: "Accepted" },
  { key: "dismissed", label: "Dismissed" },
];

function OpportunitiesTab() {
  const [status, setStatus] = useState<SocialOpportunityStatus>("new");
  const fetcher = useCallback(() => api.xOpportunities(status), [status]);
  const { data, failure, reload } = useApi(fetcher);
  const [busy, setBusy] = useState(false);
  async function detect() {
    setBusy(true);
    try {
      await api.runJob("x.opportunities");
      await reload();
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card
      title="X opportunities"
      actions={
        <div className="btn-group">
          {OPP_FILTERS.map((f) => (
            <button key={f.key} className={`btn btn-sm ${status === f.key ? "btn-primary" : ""}`} onClick={() => setStatus(f.key)}>
              {f.label}
            </button>
          ))}
          <button className="btn btn-sm" disabled={busy} onClick={() => void detect()}>
            {busy ? "Looking…" : "Find now"}
          </button>
        </div>
      }
    >
      <p className="muted small">
        Conversations where Price Chat could add something useful. The fast model filters posts first; the strong model explains why each one matters. Proposing an action puts it in the approval queue. Nothing is sent to X in this phase.
      </p>
      <ApiErrorBanner error={failure} />
      {!data ? (
        <Loading />
      ) : data.length === 0 ? (
        <Empty>No opportunities with this status.</Empty>
      ) : (
        <div className="stack">
          {data.map((o) => (
            <XOpportunityCard key={`${o.id}-${o.status}`} o={o} onChanged={() => void reload()} />
          ))}
        </div>
      )}
    </Card>
  );
}

function XOpportunityCard({ o, onChanged }: { o: SocialOpportunity; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const [direction, setDirection] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [proposed, setProposed] = useState<Approval[]>([]);
  const [error, setError] = useState<unknown>(null);
  const tips = (o.engagement as { suggestions?: { like?: boolean; repost?: boolean; follow_author?: boolean; reason?: string } }).suggestions ?? {};
  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      setError(null);
      onChanged();
    } catch (e) {
      setError(e);
    }
  }
  async function propose(key: string, fn: () => Promise<Approval>) {
    setBusy(key);
    try {
      const a = await fn();
      setProposed((p) => [...p, a]);
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }
  return (
    <div className="opp">
      <div className="opp-head">
        <strong>{o.topic || label(o.kind)}</strong>
        <Badge tone="info">{label(o.kind)}</Badge>
        <Badge>{label(o.suggested_action)}</Badge>
        <span className="muted small">relevance {Math.round(o.relevance * 100)}% · priority {Math.round(o.priority * 100)}</span>
      </div>
      <PostItem p={o.post} />
      <div className="opp-grid">
        <div>
          <div className="field-label">Why it matters</div>
          <p>{o.why}</p>
        </div>
        <div>
          <div className="field-label">What Price Chat could contribute</div>
          <p>{o.contribution}</p>
        </div>
      </div>
      <div className="row small">
        {o.reasons.slice(0, 3).map((r) => (
          <span key={r} className="chip">{r}</span>
        ))}
        <button className="link-btn" onClick={() => setOpen(!open)}>
          {open ? "Hide details" : "Why this rank?"}
        </button>
      </div>
      {open && (
        <div className="ranking-detail">
          <SignalBars signals={o.signals} />
          <Reasons reasons={o.reasons} />
          {o.knowledge.length > 0 && (
            <div className="small">
              <div className="field-label">Relevant Price Chat knowledge</div>
              {o.knowledge.map((k, i) => (
                <div key={k.id ?? i}>
                  {k.id ? `k${k.id}: ` : ""}
                  {k.title ?? k.content}
                </div>
              ))}
            </div>
          )}
          <div className="muted small">{o.model ? `assessed by ${o.model}` : ""} {formatTime(o.generated_at)}</div>
        </div>
      )}
      <ApiErrorBanner error={error} />
      {(o.status === "new" || o.status === "reviewing") && (
        <div className="operator-actions">
          <div className="field-label">Propose an action (goes to the approval queue; nothing is sent until you approve)</div>
          <div className="btn-group">
            <button className="btn btn-sm btn-primary" disabled={busy !== null} onClick={() => void propose("reply", () => api.draftXReply(o.id, { direction: direction.trim() || undefined }))}>
              {busy === "reply" ? "Writing reply…" : "Draft reply with AI"}
            </button>
            <button className="btn btn-sm" disabled={busy !== null} onClick={() => void propose("post", () => api.draftXReply(o.id, { standalone: true, direction: direction.trim() || undefined }))}>
              {busy === "post" ? "Writing post…" : "Draft post"}
            </button>
            <button className="btn btn-sm" disabled={busy !== null} title={tips.like ? tips.reason : undefined} onClick={() => void propose("like", () => api.proposeXAction(o.id, { action: "like" }))}>
              Like{tips.like ? " ✦" : ""}
            </button>
            <button className="btn btn-sm" disabled={busy !== null} title={tips.repost ? tips.reason : undefined} onClick={() => void propose("repost", () => api.proposeXAction(o.id, { action: "repost" }))}>
              Repost{tips.repost ? " ✦" : ""}
            </button>
            {o.post.author_username && (
              <button className="btn btn-sm" disabled={busy !== null} title={tips.follow_author ? tips.reason : undefined} onClick={() => void propose("follow", () => api.proposeXAction(o.id, { action: "follow" }))}>
                Follow @{o.post.author_username}{tips.follow_author ? " ✦" : ""}
              </button>
            )}
          </div>
          <input className="input" value={direction} onChange={(e) => setDirection(e.target.value)} maxLength={500} placeholder="Optional direction for the AI, e.g. mention that funding resets every 8 hours" />
          {(tips.like || tips.repost || tips.follow_author) && <div className="muted small">✦ suggested by the AI{tips.reason ? `: ${tips.reason}` : ""}</div>}
          {proposed.map((a) => (
            <div key={a.id} className={`banner ${a.guard_result.allowed ? "banner-info" : "banner-warn"}`}>
              {label(a.action)} added to the <Link className="link" to="/approvals">approval queue</Link> as #{a.id}
              {a.payload.text ? <div className="draft-body">{a.payload.text}</div> : null}
              <div className="small">Safety check now: {a.guard_result.reason}</div>
              {!a.guard_result.allowed && <GuardChecks checks={a.guard_result.checks} />}
            </div>
          ))}
        </div>
      )}
      {o.status === "new" && (
        <>
          <div className="btn-group">
            <button className="btn btn-sm btn-primary" onClick={() => void act(() => api.feedback("social_opportunity", o.id, "approve"))}>
              Useful
            </button>
            <button className="btn btn-sm" onClick={() => void act(() => api.feedback("social_opportunity", o.id, "reject"))}>
              Not useful
            </button>
            <FeedbackButtons kind="social_opportunity" id={o.id} only={["important", "not_important"]} onDone={onChanged} />
          </div>
        </>
      )}
      {o.status !== "new" && (
        <div className="btn-group">
          <button className="btn btn-sm btn-ghost" onClick={() => void act(() => api.updateXOpportunity(o.id, "new"))}>
            Move back to new
          </button>
        </div>
      )}
    </div>
  );
}

// --- analytics ---------------------------------------------------------------------------------------

function AnalyticsTab() {
  const { data, failure } = useApi(api.xAnalytics);
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const t = data.totals;
  return (
    <>
      <div className="stats">
        <Stat label="Posts analysed" value={data.post_count} />
        <Stat label="Impressions" value={compact(t.impressions)} hint={t.impressions == null ? "needs account keys" : undefined} />
        <Stat label="Likes" value={compact(t.likes)} />
        <Stat label="Replies" value={compact(t.replies)} />
        <Stat label="Reposts" value={compact(t.reposts)} />
        <Stat label="Bookmarks" value={compact(t.bookmarks)} />
        <Stat label="Profile visits" value={compact(t.profile_visits)} />
        <Stat label="Link clicks" value={compact(t.url_clicks)} />
      </div>
      <div className="banner banner-info">{data.note} “n/a” means X did not provide that number.</div>
      <Card title="Per post">
        {data.posts.length === 0 ? (
          <Empty>No posts yet.</Empty>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Post</th>
                <th>Posted</th>
                <th>Impressions</th>
                <th>Likes</th>
                <th>Replies</th>
                <th>Reposts</th>
                <th>Bookmarks</th>
              </tr>
            </thead>
            <tbody>
              {data.posts.map((p) => (
                <tr key={p.id}>
                  <td className="clamp">{p.text}</td>
                  <td className="muted small nowrap">{formatTime(p.posted_at)}</td>
                  {(["impressions", "likes", "replies", "reposts", "bookmarks"] as const).map((k) => (
                    <td key={k} className={p.metrics[k] == null ? "muted small" : ""}>
                      {compact(p.metrics[k])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </>
  );
}

// --- connection --------------------------------------------------------------------------------------

type KeyField = { key: keyof XCredentialsInput; label: string; hint: string };
const APP_FIELDS: KeyField[] = [
  { key: "api_key", label: "Consumer Key (API Key)", hint: "Keys and tokens › Consumer Keys" },
  { key: "api_secret", label: "Consumer Secret (API Key Secret)", hint: "shown together with the Consumer Key" },
];
const USER_FIELDS: KeyField[] = [
  { key: "access_token", label: "Access Token", hint: "Keys and tokens › Access Token and Secret (for your own account)" },
  { key: "access_token_secret", label: "Access Token Secret", hint: "shown together with the Access Token" },
];

const capOk = (d: XStatus, k: string) => d.capabilities[k]?.status === "available";
const CAP_SECTIONS: { title: string; keys: string[]; status: (d: XStatus) => [string, Tone] }[] = [
  {
    title: "Public read",
    keys: ["app_auth", "public_read", "search_recent", "account_lookup", "mentions", "own_posts"],
    status: (d) => (capOk(d, "public_read") ? ["Connected", "good"] : d.capabilities.public_read ? ["Failed", "bad"] : ["Not tested", "neutral"]),
  },
  {
    title: "User account",
    keys: ["own_account", "private_metrics"],
    status: (d) =>
      !d.auth_modes.includes("user_context") ? ["Not configured", "neutral"] : capOk(d, "own_account") ? ["Connected", "good"] : ["Failed", "bad"],
  },
  {
    title: "Write capability",
    keys: ["write_post", "write_reply", "write_like", "write_repost", "write_follow", "write_actions"],
    status: (d) => {
      const w = ["post", "reply", "like", "repost", "follow"].map((a) => d.capabilities[`write_${a}`]?.status);
      if (w.every((x) => x === "available")) return ["Available · approval required", "good"];
      if (w.every((x) => x === "unavailable")) return ["Read-only token", "bad"];
      if (w.every((x) => x === "not_configured")) return ["Requires user credentials", "neutral"];
      return ["Unconfirmed", "warn"];
    },
  },
];

function ConnectionTab() {
  const { data, failure, reload, setData } = useApi(api.xStatus);
  const [form, setForm] = useState<XCredentialsInput>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function run(name: string, fn: () => Promise<XStatus>, msg: string) {
    setBusy(name);
    try {
      setData(await fn());
      setNotice(msg);
      setError(null);
    } catch (e) {
      setError(e);
      setNotice(null);
      await reload();
    } finally {
      setBusy(null);
    }
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    const body: XCredentialsInput = {};
    for (const [k, v] of Object.entries(form)) if (v && v.trim()) body[k as keyof XCredentialsInput] = v.trim();
    setForm({}); // never keep secrets in the page longer than needed
    await run("save", async () => {
      await api.saveXCredentials(body);
      return api.testX();
    }, "Saved encrypted on this computer and tested. See what your access allows below.");
  }

  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  // Keys are checked as pairs, like the backend: Bearer alone is fine; app keys and user tokens are optional.
  const saved = new Set(data.credentials.filter((c) => c.configured).map((c) => c.name.replace("x_", "")));
  const has = (k: keyof XCredentialsInput) => Boolean(form[k]?.trim()) || saved.has(k);
  const typed = (k: keyof XCredentialsInput) => Boolean(form[k]?.trim());
  const pairProblem =
    (typed("api_key") || typed("api_secret")) && !(has("api_key") && has("api_secret"))
      ? "Consumer Key and Consumer Secret belong together: paste both."
      : (typed("access_token") || typed("access_token_secret")) && !(has("access_token") && has("access_token_secret"))
        ? "Access Token and Access Token Secret belong together: paste both."
        : (typed("access_token") || typed("access_token_secret")) && !(has("api_key") && has("api_secret"))
          ? "Access Token and Secret also need the Consumer Key and Consumer Secret of the same app."
          : null;
  const anything = Object.values(form).some((v) => v?.trim());
  const keyInput = (f: KeyField) => (
    <label key={f.key} className="field">
      <span className="field-label">
        {f.label}
        {saved.has(f.key) && <span className="muted small"> · saved</span>}
      </span>
      <input className="input secret-input" type="password" autoComplete="off" spellCheck={false} value={form[f.key] ?? ""} onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} placeholder={saved.has(f.key) ? "saved (paste to replace)" : ""} />
      <span className="field-hint">{f.hint}</span>
    </label>
  );
  return (
    <>
      {notice && <div className="banner banner-good">{notice}</div>}
      <ApiErrorBanner error={error} />
      <div className="grid-2">
        <Card
          title="Connection"
          actions={
            data.configured && (
              <div className="btn-group">
                <button className="btn btn-sm" disabled={busy !== null} onClick={() => void run("test", api.testX, "Connection tested.")}>
                  {busy === "test" ? "Testing…" : "Test connection"}
                </button>
                {data.paused ? (
                  <button className="btn btn-sm btn-primary" onClick={() => void run("read", () => api.setXReading(true), "Reading resumed.")}>
                    Resume reading
                  </button>
                ) : (
                  <button className="btn btn-sm" onClick={() => void run("read", () => api.setXReading(false), "Reading paused. No X API requests will be made.")}>
                    Pause reading
                  </button>
                )}
              </div>
            )
          }
        >
          <dl className="kv">
            <dt>Status</dt>
            <dd>
              <StatusBadge status={data.status} />
            </dd>
            <dt>Account</dt>
            <dd>{data.account_username ? `@${data.account_username}${data.account_name ? ` (${data.account_name})` : ""}` : <span className="muted">not identified (needs account keys)</span>}</dd>
            <dt>Access</dt>
            <dd>{data.auth_modes.length ? data.auth_modes.map((m) => (m === "app_only" ? "public data (Bearer Token)" : "your account (account keys)")).join(" + ") : "none"}</dd>
            <dt>Saved keys</dt>
            <dd>
              {data.credentials.map((c) => (
                <div key={c.name} className="small">
                  <Badge tone={c.configured ? "good" : "neutral"}>{c.configured ? "saved" : "not set"}</Badge> <span className="mono">{c.name.replace("x_", "")}</span>
                  {c.configured && <span className="muted"> · {c.source === "env" ? "from .env" : `saved ${relativeTime(c.saved_at)}`}</span>}
                </div>
              ))}
            </dd>
            <dt>Budget</dt>
            <dd>
              ${(data.usage_today.cost ?? 0).toFixed(3)} of ${data.budget.daily_usd.toFixed(2)} today · ${(data.usage_month.cost ?? 0).toFixed(2)} of ${data.budget.monthly_usd.toFixed(2)} this month
            </dd>
            <dt>Last error</dt>
            <dd className={data.last_error ? "tone-bad" : "muted"}>{data.last_error ?? "none"}</dd>
          </dl>
          {data.configured && (
            <button
              className="link-btn tone-bad"
              onClick={() => {
                if (window.confirm("Remove all saved X keys from this computer? Reading stops immediately.")) void run("remove", api.removeXCredentials, "X keys removed.");
              }}
            >
              Remove saved keys
            </button>
          )}
        </Card>
        <Card title="What your X access allows">
          {Object.keys(data.capabilities).length === 0 ? (
            <Empty>Press “Test connection” after saving keys. Price Chat checks each feature once with a tiny request.</Empty>
          ) : (
            CAP_SECTIONS.map((sec) => {
              const rows = sec.keys.filter((k) => data.capabilities[k]);
              if (!rows.length) return null;
              const overall = sec.status(data);
              return (
                <div key={sec.title} className="cap-section">
                  <div className="row">
                    <strong>{sec.title}</strong>
                    <Badge tone={overall[1]}>{overall[0]}</Badge>
                  </div>
                  <table className="table">
                    <tbody>
                      {rows.map((k) => {
                        const c = data.capabilities[k];
                        return (
                          <tr key={k}>
                            <td>
                              {c.label ?? label(k)}
                              {c.detail && <div className="muted small">{c.detail}</div>}
                            </td>
                            <td>
                              <CapabilityBadge cap={c} />
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              );
            })
          )}
        </Card>
      </div>
      <Card title={data.configured ? "Replace keys" : "Connect X"}>
        <ol className="setup-steps">
          <li>
            Open <a className="link" href="https://console.x.com" target="_blank" rel="noreferrer">console.x.com</a>, sign in with the X account Price Chat should use, and create an app (any name).
          </li>
          <li>In the app, open “Keys and tokens”. Copy the <strong>Bearer Token</strong> (and, if you have them, the <strong>Consumer Key and Secret</strong>). The Bearer Token alone is enough for public monitoring.</li>
          <li>
            To read your own account and to let Price Chat act on your approval: in the app's <strong>User authentication settings</strong> set the permissions to{" "}
            <strong>Read and write</strong>, then generate (or regenerate) the <strong>Access Token and Secret</strong> and paste them below.
          </li>
          <li>Press “Save & test”. X charges per request (pay-per-use); Price Chat stops at your daily budget.</li>
        </ol>
        <form className="stack" onSubmit={save}>
          <h3 className="field-label">App / public read credentials</h3>
          <label className="field">
            <span className="field-label">
              Bearer Token
              {saved.has("bearer_token") && <span className="muted small"> · saved</span>}
            </span>
            <input className="input secret-input" type="password" autoComplete="off" spellCheck={false} value={form.bearer_token ?? ""} onChange={(e) => setForm({ ...form, bearer_token: e.target.value })} placeholder={saved.has("bearer_token") ? "saved (paste to replace)" : "AAAAAAAAAAAAAAAAAAAAA…"} />
            <span className="field-hint">Keys and tokens › Bearer Token. Enough on its own for public reads.</span>
          </label>
          {APP_FIELDS.map(keyInput)}
          <label className="field">
            <span className="field-label">Your X username (optional)</span>
            <input className="input" value={form.username ?? ""} onChange={(e) => setForm({ ...form, username: e.target.value })} placeholder={data.account_username ? `@${data.account_username}` : "@pricechat"} maxLength={16} />
            <span className="field-hint">Used to find your mentions with the Bearer Token.</span>
          </label>
          <h3 className="field-label">User-context credentials (optional)</h3>
          <p className="muted small">Only needed to read your own account and private post metrics. Leave empty for now if you don't have them.</p>
          {USER_FIELDS.map(keyInput)}
          {pairProblem && <div className="banner banner-warn">{pairProblem}</div>}
          <div className="row">
            <button className="btn btn-primary" disabled={busy !== null || pairProblem !== null || !(anything || data.configured)}>
              {busy === "save" ? "Saving & testing…" : "Save & test"}
            </button>
            <span className="muted small">Keys are stored encrypted with Windows (DPAPI) on this computer. They are never shown again, logged, or given to the AI.</span>
          </div>
        </form>
        <p className="muted small">
          Write actions (post, reply, like, repost, follow) need the Access Token and Secret from an app with <strong>Read and write</strong> permission.
          Even then, every action waits for your approval in the queue, and nothing is sent while dry run is on. Automatic mode is locked.
        </p>
      </Card>
    </>
  );
}
