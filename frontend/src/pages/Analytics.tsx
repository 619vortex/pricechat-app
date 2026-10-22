import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import type { GrowthChart, GrowthContentRow, GrowthDashboard, GrowthKpi, GrowthPeriod, GrowthTimelineItem } from "../api/types";
import { compact } from "../components/social";
import { ApiErrorBanner, Card, Empty, Loading, PageHeader, Stat, Tabs, formatTime, relativeTime, type Tone } from "../components/ui";

const PERIODS: { key: GrowthPeriod; label: string }[] = [
  { key: "24h", label: "24H" },
  { key: "7d", label: "7D" },
  { key: "30d", label: "30D" },
  { key: "all", label: "ALL" },
];

const pct = (v: number | null | undefined, digits = 0) => (v == null ? "-" : `${(v * 100).toFixed(digits)}%`);
const usd = (v: number | null | undefined, digits = 2) => (v == null ? "-" : `$${v.toFixed(digits)}`);
// X returns post text HTML-escaped (&amp; &lt; &gt;); show it as written.
const unescape = (t: string) => t.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&");

export function Analytics() {
  const [period, setPeriod] = useState<GrowthPeriod>("7d");
  const { data, failure } = useApi(() => api.growthDashboard(period), 120000);
  const stale = data && data.period !== period;
  return (
    <>
      <PageHeader title="Analytics" subtitle="Growth: is Price Chat's social activity producing growth? Real collected data only." />
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <Tabs tabs={[{ key: "growth", label: "Growth" }]} value="growth" onChange={() => undefined} />
        <div className="btn-group" role="group" aria-label="Period">
          {PERIODS.map((p) => (
            <button key={p.key} className={`btn btn-sm ${period === p.key ? "btn-primary" : ""}`} onClick={() => setPeriod(p.key)}>
              {p.label}
            </button>
          ))}
        </div>
      </div>
      {!data || stale ? failure ? <ApiErrorBanner error={failure} /> : <Loading /> : <Dashboard d={data} />}
    </>
  );
}

function Dashboard({ d }: { d: GrowthDashboard }) {
  return (
    <div className="stack">
      <KpiGrid kpis={d.kpis} period={d.period} />
      <GrowthChartCard d={d} />
      <Timeline items={d.timeline} />
      <ContentCard rows={d.content} />
      <div className="grid-2">
        <AiCard d={d} />
        <CostCard d={d} />
      </div>
      <RelationshipsCard d={d} />
      <TelegramCard d={d} />
      <Card title="What X does not provide (or we have not collected)">
        <ul className="reasons">
          {d.not_provided.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
        <p className="muted small">
          Snapshot schedule: {d.snapshots.schedule} {d.snapshots.first_snapshot_at ? `First follower snapshot: ${formatTime(d.snapshots.first_snapshot_at)}.` : ""}{" "}
          The detailed audit log is on{" "}
          <Link className="link" to="/activity">
            Activity
          </Link>
          .
        </p>
      </Card>
    </div>
  );
}

// --- KPIs ------------------------------------------------------------------------------------------------

function fmt(k: GrowthKpi, v: number | null): string {
  if (v == null) return "-";
  if (k.unit === "%") return pct(v, 1);
  if (k.unit === "$") return usd(v);
  return Math.abs(v) >= 10000 ? compact(v) : v.toLocaleString();
}

function KpiGrid({ kpis, period }: { kpis: GrowthKpi[]; period: GrowthPeriod }) {
  return (
    <div className="kpi-grid">
      {kpis.map((k) => {
        const { delta, pct: p } = k.change;
        const tone: Tone = delta == null || delta === 0 ? "neutral" : (delta > 0) !== (k.key === "api_spend") ? "good" : "warn";
        const sign = delta != null && delta > 0 ? "+" : "";
        const signed = k.key === "follower_change" && k.value != null && k.value > 0 ? `+${fmt(k, k.value)}` : fmt(k, k.value);
        return (
          <div key={k.key} className={`stat kpi ${k.available ? "" : "kpi-na"}`} title={k.note}>
            <span className="stat-label">{k.label}</span>
            <span className="stat-value">{k.available ? signed : "n/a"}</span>
            <span className="stat-hint">
              {!k.available ? (
                k.note
              ) : delta != null ? (
                <span className={`tone-${tone}`}>
                  {sign}
                  {k.unit === "%" ? pct(delta, 1) : k.unit === "$" ? usd(delta) : delta.toLocaleString()}
                  {p != null ? ` (${sign}${p}%)` : ""} vs previous {period === "24h" ? "24h" : period}
                </span>
              ) : period === "all" ? (
                k.note || "all time"
              ) : (
                <span className="muted">no comparison (not collected for the previous period){k.note ? ` · ${k.note}` : ""}</span>
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}

// --- main chart ---------------------------------------------------------------------------------------------

type Overlay = "impressions" | "engagements" | "posts" | "replies" | "likes" | "reposts";
const OVERLAYS: { key: Overlay; label: string; color: string; kind: "line" | "bar" }[] = [
  { key: "impressions", label: "Impressions", color: "#3b9eff", kind: "line" },
  { key: "engagements", label: "Engagements", color: "#a78bfa", kind: "line" },
  { key: "posts", label: "Posts", color: "#f5a524", kind: "bar" },
  { key: "replies", label: "Replies", color: "#38bdf8", kind: "bar" },
  { key: "likes", label: "Likes", color: "#f472b6", kind: "bar" },
  { key: "reposts", label: "Reposts", color: "#facc15", kind: "bar" },
];
const ACTION_COLOR: Record<string, string> = { like: "#f472b6", reply: "#38bdf8", post: "#f5a524", repost: "#facc15", follow: "#22c55e" };

function GrowthChartCard({ d }: { d: GrowthDashboard }) {
  const [on, setOn] = useState<Set<Overlay>>(new Set(["impressions", "posts", "likes"]));
  const toggle = (k: Overlay) => setOn((s) => new Set(s.has(k) ? [...s].filter((x) => x !== k) : [...s, k]));
  return (
    <Card title={`Growth${d.account ? ` · @${d.account}` : ""}`}>
      <div className="row small" style={{ flexWrap: "wrap", justifyContent: "flex-start", gap: 6 }}>
        <span className="legend-item">
          <span className="legend-swatch" style={{ background: "var(--accent)" }} /> X followers
        </span>
        {OVERLAYS.map((o) => (
          <label key={o.key} className="legend-item">
            <input type="checkbox" checked={on.has(o.key)} onChange={() => toggle(o.key)} />
            <span className="legend-swatch" style={{ background: o.color }} /> {o.label}
          </label>
        ))}
      </div>
      <SvgChart c={d.chart} overlays={OVERLAYS.filter((o) => on.has(o.key))} timeline={d.timeline} />
      <p className="muted small">
        {d.chart.follower_points.length === 0
          ? "No follower snapshots in this period yet: the follower line starts with the first snapshot (history is never back-filled). "
          : `${d.chart.follower_points.length} follower snapshot(s) in this period. `}
        {d.chart.note} Markers along the bottom are Price Chat actions. {d.disclaimer}
      </p>
    </Card>
  );
}

function SvgChart({ c, overlays, timeline }: { c: GrowthChart; overlays: typeof OVERLAYS; timeline: GrowthTimelineItem[] }) {
  const W = 1000, H = 300, L = 56, R = 56, T = 12, B = 44;
  const n = c.buckets.length;
  if (n === 0) return <Empty>No data in this period.</Empty>;
  const t0 = new Date(c.buckets[0]).getTime();
  const stepMs = c.step === "hour" ? 3600e3 : 86400e3;
  const t1 = new Date(c.buckets[n - 1]).getTime() + stepMs;
  const x = (t: number) => L + ((t - t0) / (t1 - t0)) * (W - L - R);
  const bx = (i: number) => x(t0 + i * stepMs);
  const bw = (W - L - R) / n;

  const fp = c.follower_points.map((p) => ({ t: new Date(p.t).getTime(), v: p.v }));
  const fv = fp.map((p) => p.v);
  const fmin = fv.length ? Math.min(...fv) : 0, fmax = fv.length ? Math.max(...fv) : 1;
  const pad = Math.max(1, Math.round((fmax - fmin) * 0.15));
  const lo = fmin - pad, hi = fmax + pad;
  const yF = (v: number) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);

  const ovMax = Math.max(1, ...overlays.flatMap((o) => (c[o.key] as (number | null)[]).map((v) => v ?? 0)));
  const yO = (v: number) => T + (1 - v / ovMax) * (H - T - B);
  const bars = overlays.filter((o) => o.kind === "bar");
  const labelEvery = Math.max(1, Math.ceil(n / 8));

  return (
    <svg className="growth-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Followers and activity over time">
      {[0, 0.25, 0.5, 0.75, 1].map((f) => (
        <line key={f} x1={L} x2={W - R} y1={T + f * (H - T - B)} y2={T + f * (H - T - B)} stroke="var(--border)" strokeWidth={1} />
      ))}
      {bars.map((o, j) =>
        (c[o.key] as number[]).map((v, i) =>
          v ? (
            <rect key={`${o.key}${i}`} x={bx(i) + 2 + (j * (bw - 4)) / bars.length} width={Math.max(1, (bw - 4) / bars.length - 1)}
              y={yO(v)} height={H - B - yO(v)} fill={o.color} opacity={0.7}>
              <title>{`${o.label}: ${v}`}</title>
            </rect>
          ) : null,
        ),
      )}
      {overlays
        .filter((o) => o.kind === "line")
        .map((o) => {
          const pts = (c[o.key] as (number | null)[]).map((v, i) => (v == null ? null : `${bx(i) + bw / 2},${yO(v)}`)).filter(Boolean);
          return pts.length ? (
            <g key={o.key}>
              <polyline points={pts.join(" ")} fill="none" stroke={o.color} strokeWidth={2} strokeDasharray="5 4" />
              {(c[o.key] as (number | null)[]).map((v, i) =>
                v == null ? null : (
                  <circle key={i} cx={bx(i) + bw / 2} cy={yO(v)} r={3} fill={o.color}>
                    <title>{`${o.label}: ${v.toLocaleString()}`}</title>
                  </circle>
                ),
              )}
            </g>
          ) : null;
        })}
      {fp.length > 0 && (
        <g>
          <polyline points={fp.map((p) => `${x(p.t)},${yF(p.v)}`).join(" ")} fill="none" stroke="var(--accent)" strokeWidth={3} />
          {fp.map((p, i) => (
            <circle key={i} cx={x(p.t)} cy={yF(p.v)} r={3.5} fill="var(--accent)">
              <title>{`${new Date(p.t).toLocaleString()}: ${p.v.toLocaleString()} followers`}</title>
            </circle>
          ))}
          <text x={L - 6} y={yF(hi) + 4} textAnchor="end" className="axis">{hi.toLocaleString()}</text>
          <text x={L - 6} y={yF(lo)} textAnchor="end" className="axis">{lo.toLocaleString()}</text>
        </g>
      )}
      {overlays.length > 0 && (
        <text x={W - R + 6} y={T + 10} className="axis">{compact(ovMax)}</text>
      )}
      {timeline.map((a, i) => {
        const t = new Date(a.at).getTime();
        if (t < t0 || t > t1) return null;
        return (
          <g key={i}>
            <line x1={x(t)} x2={x(t)} y1={H - B} y2={H - B + 8} stroke={ACTION_COLOR[a.action] ?? "var(--muted)"} strokeWidth={3} />
            <title>{`${formatTime(a.at)} · ${a.title}`}</title>
          </g>
        );
      })}
      {c.buckets.map((b, i) =>
        i % labelEvery === 0 ? (
          <text key={b} x={bx(i) + bw / 2} y={H - 10} textAnchor="middle" className="axis">
            {c.step === "hour" ? new Date(b).toLocaleTimeString([], { hour: "2-digit" }) : new Date(b).toLocaleDateString([], { day: "numeric", month: "short" })}
          </text>
        ) : null,
      )}
    </svg>
  );
}

// --- timeline, content -----------------------------------------------------------------------------------------

function Timeline({ items }: { items: GrowthTimelineItem[] }) {
  return (
    <Card title="Price Chat actions" actions={<Link className="link small" to="/activity">Full audit log →</Link>}>
      {items.length === 0 ? (
        <Empty>No actions executed on X in this period.</Empty>
      ) : (
        <ul className="feed">
          {items.slice(0, 20).map((a, i) => (
            <li key={i}>
              <span className="legend-swatch" style={{ background: ACTION_COLOR[a.action] ?? "var(--muted)", marginTop: 6 }} />
              <div className="feed-body">
                <span>
                  {a.title}
                  {a.topic ? <span className="muted"> · {a.topic}</span> : null}
                  {a.url && (
                    <>
                      {" "}
                      <a className="link small" href={a.url} target="_blank" rel="noreferrer">
                        open ↗
                      </a>
                    </>
                  )}
                </span>
                {a.text && <span className="muted small clamp">{unescape(a.text)}</span>}
              </div>
              <span className="muted small nowrap">{formatTime(a.at)}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

type SortKey = "impressions" | "likes" | "replies" | "reposts" | "bookmarks" | "engagement_rate";

function ContentCard({ rows }: { rows: GrowthContentRow[] }) {
  const [sort, setSort] = useState<SortKey>("impressions");
  const sorted = [...rows].sort((a, b) => (b[sort] ?? -1) - (a[sort] ?? -1));
  const cp = (r: GrowthContentRow, h: string) => {
    const c = r.checkpoints[h];
    return c ? `${c.impressions ?? "n/a"} imp · ${c.likes ?? "-"} likes` : "-";
  };
  return (
    <Card
      title="Content performance"
      actions={
        <select className="input input-sm" value={sort} onChange={(e) => setSort(e.target.value as SortKey)} aria-label="Sort by">
          <option value="impressions">By impressions</option>
          <option value="likes">By likes</option>
          <option value="replies">By replies</option>
          <option value="reposts">By reposts</option>
          <option value="bookmarks">By bookmarks</option>
          <option value="engagement_rate">By engagement rate</option>
        </select>
      }
    >
      {rows.length === 0 ? (
        <Empty>No posts of yours were collected in this period.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Post</th>
                <th>Impr.</th>
                <th>Likes</th>
                <th>Replies</th>
                <th>Reposts</th>
                <th>Bookm.</th>
                <th>Eng. rate</th>
                <th>~1h / 6h / 24h</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r) => (
                <tr key={r.id}>
                  <td style={{ maxWidth: 360 }}>
                    <div className="clamp">{unescape(r.text)}</div>
                    <div className="muted small">
                      {r.kind} · {formatTime(r.posted_at)}
                      {r.topic ? ` · ${r.topic}` : ""} ·{" "}
                      {r.via_price_chat ? (r.ai_drafted ? (r.human_edited ? "AI draft, edited by you" : "AI draft, unchanged") : "via Price Chat") : "posted outside Price Chat"}
                      {r.url && (
                        <>
                          {" "}
                          ·{" "}
                          <a className="link small" href={r.url} target="_blank" rel="noreferrer">
                            open ↗
                          </a>
                        </>
                      )}
                    </div>
                  </td>
                  <td>{r.impressions ?? "n/a"}</td>
                  <td>{r.likes ?? "-"}</td>
                  <td>{r.replies ?? "-"}</td>
                  <td>{r.reposts ?? "-"}</td>
                  <td>{r.bookmarks ?? "n/a"}</td>
                  <td>{pct(r.engagement_rate, 1)}</td>
                  <td className="small muted">
                    {["1h", "6h", "24h"].map((h) => (
                      <div key={h}>
                        {h}: {cp(r, h)}
                      </div>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted small">
        Numbers are the latest X reported. "n/a": X did not provide it (impressions and bookmarks need private metrics on your own posts, last 30 days). The ~1h/6h/24h
        checkpoints exist only for posts Price Chat published.
      </p>
    </Card>
  );
}

// --- AI, cost, relationships, Telegram ----------------------------------------------------------------------------

function AiCard({ d }: { d: GrowthDashboard }) {
  const a = d.ai;
  const trend = a.trust_trend.filter((w) => w.reviewed > 0);
  return (
    <Card title="AI performance">
      <div className="stats">
        <Stat label="Opportunities found" value={a.opportunities_x + a.opportunities_telegram} hint={`${a.opportunities_x} X · ${a.opportunities_telegram} Telegram`} />
        <Stat label="Approved / rejected" value={`${a.approved} / ${a.rejected}`} hint={`${a.proposals} proposed · ${a.pending} pending · ${a.expired} expired`} />
        <Stat label="AI draft acceptance" value={pct(a.ai_acceptance_rate)} hint={`${a.ai_drafts_reviewed} AI drafts reviewed`} />
        <Stat label="Human edit rate" value={pct(a.human_edit_rate)} hint="of accepted AI drafts" />
      </div>
      <dl className="kv">
        <dt>Likes</dt>
        <dd className="small">
          {a.likes.recommended} recommended by AI · {a.likes.proposed} proposed · {a.likes.approved} approved
        </dd>
        <dt>Replies</dt>
        <dd className="small">
          {a.replies.recommended} recommended · {a.replies.proposed} proposed · {a.replies.approved} approved
        </dd>
        <dt>API actions</dt>
        <dd className="small">
          {a.successful_actions} succeeded · {a.failed_or_blocked} failed or blocked
        </dd>
        <dt>Trust trend</dt>
        <dd className="small">
          {trend.length === 0
            ? "Not enough reviewed AI drafts yet."
            : trend.map((w) => `wk of ${w.week_start}: ${pct(w.approved_unchanged_rate)} approved unchanged (${w.reviewed})`).join(" · ")}
        </dd>
      </dl>
    </Card>
  );
}

function CostCard({ d }: { d: GrowthDashboard }) {
  const c = d.cost;
  return (
    <Card title="X API cost">
      <div className="stats">
        <Stat label="Today" value={usd(c.today.cost)} hint={`${c.today.requests} requests · limit ${usd(c.budget.daily_usd)}`} />
        <Stat label="7 days" value={usd(c.d7.cost)} hint={`${c.d7.requests} requests`} />
        <Stat label="30 days" value={usd(c.d30.cost)} hint={`${c.d30.requests} requests · month ${usd(c.month.cost)} of ${usd(c.budget.monthly_usd)}`} />
      </div>
      <dl className="kv">
        <dt>Per useful opportunity</dt>
        <dd className="small">{c.per_useful_opportunity_30d == null ? "not calculable yet" : `${usd(c.per_useful_opportunity_30d, 3)} (${c.useful_opportunities_30d} in 30 days)`}</dd>
        <dt>Per approved action</dt>
        <dd className="small">{c.per_approved_action_30d == null ? "not calculable yet" : `${usd(c.per_approved_action_30d, 3)} (${c.approved_actions_30d} in 30 days)`}</dd>
        <dt>Per executed action</dt>
        <dd className="small">{c.per_executed_action_30d == null ? "not calculable yet" : `${usd(c.per_executed_action_30d, 3)} (${c.executed_actions_30d} in 30 days)`}</dd>
      </dl>
      <p className="muted small">{c.note} Budget protections are unchanged.</p>
    </Card>
  );
}

function RelationshipsCard({ d }: { d: GrowthDashboard }) {
  const rows = d.relationships;
  return (
    <Card title="Relationships">
      <p className="muted small">Accounts Price Chat has interacted with, most recent first. Context for you, not a score to chase.</p>
      {rows.length === 0 ? (
        <Empty>No interactions executed by Price Chat yet.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Account</th>
                <th>Followers</th>
                <th>From us</th>
                <th>From them</th>
                <th>Last interaction</th>
                <th>Topics</th>
                <th>History</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.account}>
                  <td>
                    <a className="link" href={`https://x.com/${r.account}`} target="_blank" rel="noreferrer">
                      @{r.account}
                    </a>
                    {r.name && <div className="muted small">{r.name}</div>}
                  </td>
                  <td>{r.followers == null ? "-" : compact(r.followers)}</td>
                  <td className="small">
                    {r.likes_from_us} likes · {r.replies_from_us} replies · {r.reposts_from_us} reposts{r.we_follow ? " · following" : ""}
                  </td>
                  <td className="small">
                    {r.their_mentions} mentions · {r.their_replies} replies
                  </td>
                  <td className="small">{relativeTime(r.last_interaction_at)}</td>
                  <td className="small">{r.topics.join(", ") || "-"}</td>
                  <td className="small muted">{r.history.map((h) => `${h.action} ${formatTime(h.at)}`).join(" · ") || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function TelegramCard({ d }: { d: GrowthDashboard }) {
  const t = d.telegram;
  const prev = (v: number | null) => (v == null ? undefined : `previous period: ${v}`);
  return (
    <Card title="Telegram community" actions={<Link className="link small" to="/telegram">Community →</Link>}>
      {t.connected_sources === 0 && <div className="banner banner-info">No authorised Telegram chat yet: connect one on Community › Connection.</div>}
      <div className="stats">
        <Stat label="Messages collected" value={t.messages} hint={prev(t.messages_prev)} />
        <Stat label="Active discussions" value={t.active_discussions} />
        <Stat label="Topics detected" value={t.topics_detected} hint={prev(t.topics_prev)} />
        <Stat label="Questions detected" value={t.questions_detected} hint={prev(t.questions_prev)} />
        <Stat label="Content opportunities" value={t.content_opportunities} />
      </div>
      <div className="field-label" style={{ marginTop: 10 }}>
        Members
      </div>
      {t.member_history.length === 0 ? (
        <p className="muted small">{t.member_note}</p>
      ) : (
        <div className="stack">
          {t.member_history.map((c) => (
            <div key={c.chat} className="row small" style={{ justifyContent: "flex-start" }}>
              <strong>{c.chat}</strong>
              <span>{c.latest_members == null ? "member count not readable" : `${c.latest_members.toLocaleString()} members`}</span>
              <Sparkline points={c.points} />
            </div>
          ))}
          <p className="muted small">{t.member_note}</p>
        </div>
      )}
    </Card>
  );
}

function Sparkline({ points }: { points: { t: string; v: number }[] }) {
  if (points.length < 2) return <span className="muted">history builds from now on</span>;
  const W = 160, H = 28;
  const ts = points.map((p) => new Date(p.t).getTime());
  const vs = points.map((p) => p.v);
  const [t0, t1, v0, v1] = [Math.min(...ts), Math.max(...ts), Math.min(...vs), Math.max(...vs)];
  const pts = points.map((p, i) => `${((ts[i] - t0) / Math.max(1, t1 - t0)) * W},${H - 2 - ((p.v - v0) / Math.max(1, v1 - v0)) * (H - 4)}`);
  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} aria-label="member history">
      <polyline points={pts.join(" ")} fill="none" stroke="var(--accent)" strokeWidth={2} />
    </svg>
  );
}

