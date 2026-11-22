import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { TrendBadge } from "../components/community";
import { PriorityBadge } from "../components/social";
import type { CommandCenter, PilotCheckStatus } from "../api/types";
import { adminHome, type TodoItem } from "../api/home";
import { ApiErrorBanner, Badge, Card, Empty, Loading, PageHeader, Stat, formatTime, label, relativeTime, statusTone, type Tone } from "../components/ui";
import { CrossSignalList } from "./XPage";

const CONN: Record<string, [string, "good" | "warn" | "bad" | "neutral"]> = {
  connected: ["Connected", "good"],
  disconnected: ["Waiting", "warn"],
  paused: ["Paused", "warn"],
  error: ["Problem", "bad"],
  not_configured: ["Not set up", "neutral"],
};

const PILOT_TONE: Record<PilotCheckStatus, Tone> = {
  ready: "good", needs_setup: "warn", waiting: "info", problem: "bad", disabled: "neutral", paused: "warn", dry_run: "info", supervised: "good",
};
const PILOT_TEXT: Record<PilotCheckStatus, string> = {
  ready: "READY", needs_setup: "NEEDS SETUP", waiting: "WAITING FOR DATA", problem: "PROBLEM", disabled: "DISABLED",
  paused: "PAUSED", dry_run: "DRY RUN", supervised: "SUPERVISED",
};

/** Is Price Chat ready for the real pilot? Each row is backed by a real test, never by configuration alone. */
function PilotPanel({ d }: { d: CommandCenter }) {
  const p = d.pilot;
  return (
    <Card title="Price Chat pilot status" actions={<Badge tone={p.ready ? "good" : "warn"}>{p.ready ? "Ready for the pilot" : "Not ready yet"}</Badge>}>
      <table className="table">
        <tbody>
          {p.checks.map((c) => (
            <tr key={c.key}>
              <td className="nowrap">
                <strong>{c.label}</strong>
              </td>
              <td className="nowrap">
                <Badge tone={PILOT_TONE[c.status]}>{PILOT_TEXT[c.status]}</Badge>
              </td>
              <td>
                {c.detail}
                {c.next_step && c.status !== "ready" && <div className="muted small">Next: {c.next_step}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

/** A short tag for each kind of waiting item (the home to-do keys); unknown keys fall back to their own label. */
const KIND: Record<string, string> = {
  signals: "Signal", bot_access: "VIP bot", knight: "Reward", vip: "Reward", workers: "Workers", approvals: "Post", drafts: "Draft", memories: "AI memory",
};
const kindOf = (t: TodoItem) => KIND[t.key] ?? t.label;

/** Home (redesign, owner 2026-11-22): what needs you (one list, filtered by kind, each with a link to the page that handles it),
 *  alerts, whether everything is running and a few numbers. Growth and the older, more technical details are folded underneath. */
export function Overview() {
  const h = useApi(adminHome, 20000);
  const [kind, setKind] = useState("all");
  const d = h.data;
  if (!d) return h.failure ? <ApiErrorBanner error={h.failure} /> : <Loading />;
  const waiting = d.todo.filter((t) => t.count > 0);
  const total = waiting.reduce((n, t) => n + t.count, 0);
  const kinds = [...new Set(waiting.map(kindOf))];
  const shown = kind === "all" ? waiting : waiting.filter((t) => kindOf(t) === kind);
  const s = d.status;
  const scanAge = s.last_scan_at ? (Date.now() - new Date(s.last_scan_at).getTime()) / 60000 : null;
  const [tgText, tgTone] = CONN[s.telegram] ?? [label(s.telegram), "neutral"];
  const [xText, xTone] = CONN[s.x] ?? [label(s.x), "neutral"];
  const health: { name: string; text: string; tone: Tone; to: string }[] = [
    { name: "Signal scanner", text: scanAge == null ? "Hasn't run yet" : scanAge < 40 ? `Checked ${relativeTime(s.last_scan_at)}` : `Last check ${relativeTime(s.last_scan_at)}`,
      tone: scanAge != null && scanAge < 40 ? "good" : "warn", to: "/trading-os" },
    { name: "Telegram", text: tgText, tone: tgTone, to: "/telegram" },
    { name: "X / Twitter", text: xText, tone: xTone, to: "/x" },
    { name: "AI", text: s.ai ? "Working" : "Offline", tone: s.ai ? "good" : "bad", to: "/settings" },
    { name: "Background jobs", text: s.failing_jobs.length ? `${s.failing_jobs.length} having problems` : "All fine", tone: s.failing_jobs.length ? "warn" : "good", to: "/settings?tab=jobs" },
  ];
  // alerts: anything that is wrong right now, each with the page that fixes it (nothing here switches anything itself)
  const alerts: { tone: "bad" | "warn"; title: string; detail: string; to: string; action: string }[] = [];
  if (s.kill_switch) alerts.push({ tone: "bad", title: "Emergency stop is ON", detail: "Nothing is posted or sent.", to: "/settings", action: "Turn it off in Settings" });
  if (s.failing_jobs.length) alerts.push({ tone: "warn", title: `${s.failing_jobs.length} background job${s.failing_jobs.length === 1 ? "" : "s"} having problems`, detail: s.failing_jobs.join(", "), to: "/settings?tab=jobs", action: "Open jobs" });
  if (!s.ai) alerts.push({ tone: "bad", title: "The AI is offline", detail: "Drafts, summaries and replies wait until it is back.", to: "/settings", action: "Open Settings" });
  for (const x of health.slice(1, 3)) if (x.tone === "bad") alerts.push({ tone: "bad", title: `${x.name}: ${x.text}`, detail: "Its connection reports a problem.", to: x.to, action: "Open" });
  if (scanAge != null && scanAge >= 40) alerts.push({ tone: "warn", title: "The signal scanner hasn't checked recently", detail: `Last check ${relativeTime(s.last_scan_at)}.`, to: "/trading-os", action: "Open Signals" });

  return (
    <div className="ov">
      <PageHeader title="Home" subtitle="What needs you, and whether everything is running." />
      {s.kill_switch && <div className="banner banner-error"><strong>Emergency stop is ON.</strong> Nothing is posted or sent. <Link className="link" to="/settings">Turn it off in Settings</Link></div>}

      <div className="ov-kpis">
        <Stat label="Waiting for you" value={total} tone={total ? "warn" : "good"} />
        <Stat label="Members" value={d.numbers.members.toLocaleString()} hint={`+${d.numbers.new_members_7d} this week`} />
        <Stat label="Signals this week" value={d.numbers.signals_7d} hint={`${d.numbers.open_signals} open now`} />
        <Stat label="Background jobs" value={s.failing_jobs.length ? `${s.failing_jobs.length} failing` : "All fine"} tone={s.failing_jobs.length ? "warn" : "good"} />
      </div>

      <div className="ov-grid">
        <section className="card ov-needs" aria-labelledby="ov-needs-title">
          <div className="card-head ov-needs-head">
            <h2 id="ov-needs-title">{total ? `Needs you (${total})` : "Needs you"}</h2>
            {kinds.length > 1 && (
              <div className="ov-chips" role="group" aria-label="Filter by kind">
                <button type="button" className={`ov-chip${kind === "all" ? " active" : ""}`} aria-pressed={kind === "all"} onClick={() => setKind("all")}>All {total}</button>
                {kinds.map((k) => {
                  const n = waiting.filter((t) => kindOf(t) === k).reduce((a, t) => a + t.count, 0);
                  return <button key={k} type="button" className={`ov-chip${kind === k ? " active" : ""}`} aria-pressed={kind === k} onClick={() => setKind(k)}>{k} {n}</button>;
                })}
              </div>
            )}
          </div>
          {waiting.length === 0 ? <p className="home-clear">✓ All clear. Nothing is waiting for you.</p> : (
            <ul className="ov-rows">
              {shown.map((t) => (
                <li key={t.key} className="ov-row">
                  <span className={`ov-tag ov-tag-${t.key}`}>{kindOf(t)}</span>
                  <span className="ov-row-text"><b>{t.count}</b> {t.label}</span>
                  <Link className="ov-review" to={t.to}>Review</Link>
                </li>
              ))}
            </ul>
          )}
        </section>

        <div className="ov-side">
          <Card title={alerts.length ? `Alerts (${alerts.length})` : "Alerts"} className="ov-alerts">
            {alerts.length === 0 ? <p className="muted small">No alerts. Everything that reports a status is fine.</p> : (
              <ul className="ov-alert-list">
                {alerts.map((a) => (
                  <li key={a.title} className={`ov-alert ov-alert-${a.tone}`}>
                    <span className="ov-alert-dot" aria-hidden="true" />
                    <div><strong>{a.title}</strong><div className="muted small">{a.detail}</div><Link className="link" to={a.to}>{a.action}</Link></div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Is everything running?">
            <ul className="home-health">
              {health.map((x) => (
                <li key={x.name}><Link to={x.to}><span>{x.name}</span><Badge tone={x.tone}>{x.text}</Badge></Link></li>
              ))}
            </ul>
            {s.failing_jobs.length > 0 && <p className="muted small">Having problems: {s.failing_jobs.join(", ")}</p>}
          </Card>
        </div>
      </div>

      <details className="advanced ov-panel">
        <summary>Growth: visitors, sign-ups and affiliate volume</summary>
        <div className="ov-panel-links">
          <Link className="link" to="/analytics">Open Analytics</Link>
          <Link className="link" to="/affiliate">Open Affiliate</Link>
        </div>
      </details>
      <details className="advanced ov-panel">
        <summary>Recent activity and details: setup checklist, community, AI</summary>
        <div className="stack"><Details /></div>
      </details>
    </div>
  );
}

/** The old command center, folded under Home: setup checklist, AI and community details, recent activity. */
function Details() {
  const cc = useApi(api.commandCenter, 20000);
  const ov = useApi(api.overview, 20000);
  const d = cc.data;
  if (!d) return cc.failure ? <ApiErrorBanner error={cc.failure} /> : <Loading />;

  return (
    <>
      <PilotPanel d={d} />
      <div className="grid-2">
        <Card title="Top content opportunities" actions={<Link className="link" to="/content?tab=opportunities">All</Link>}>
          {d.content.top.length === 0 ? (
            <Empty>No open opportunities.</Empty>
          ) : (
            <ul className="feed">
              {d.content.top.map((o) => (
                <li key={o.id}>
                  <PriorityBadge value={o.priority === "unranked" ? "" : o.priority} />
                  <div className="feed-body">
                    <span>{o.title}</span>
                    <span className="muted small">{o.reasons.join(" · ") || o.platforms.join(", ")}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Trending in the community" actions={<Link className="link" to="/telegram">Community</Link>}>
          {d.trending.length === 0 ? (
            <Empty>Nothing trending yet.</Empty>
          ) : (
            <ul className="feed">
              {d.trending.map((t) => (
                <li key={t.id}>
                  <TrendBadge trend={t.trend} />
                  <div className="feed-body">
                    <Link className="link" to={`/telegram/topics/${t.id}`}>
                      {t.name}
                    </Link>
                    <span className="muted small">{t.messages_24h} messages in 24h</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="grid-2">
        <Card title="Discussed on Telegram and X" actions={<Link className="link" to="/x">X</Link>}>
          <CrossSignalList signals={d.cross_signals} />
        </Card>
        <Card title="Recent activity" actions={<Link to="/activity" className="link">View all</Link>}>
          {d.recent_activity.length === 0 ? (
            <Empty>No activity yet.</Empty>
          ) : (
            <ul className="feed">
              {d.recent_activity.map((a) => (
                <li key={a.id}>
                  <Badge tone={a.level === "error" ? "bad" : a.level === "warning" ? "warn" : a.category === "ai" ? "info" : "neutral"}>{a.label}</Badge>
                  <div className="feed-body">
                    <span>{a.message}</span>
                    <span className="muted small">{formatTime(a.created_at)}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {ov.data && (
        <div className="grid-2">
          <Card title="Active goals" actions={<Link to="/brain" className="link">Edit</Link>}>
            {ov.data.active_goals.length === 0 ? (
              <Empty>No active goals.</Empty>
            ) : (
              <ol className="goal-list">
                {ov.data.active_goals.map((g) => (
                  <li key={g.id}>
                    <Badge tone={g.priority <= 1 ? "warn" : "neutral"}>P{g.priority}</Badge>
                    <span>{g.name}</span>
                  </li>
                ))}
              </ol>
            )}
          </Card>
          <Card title="Agent">
            <dl className="kv">
              <dt>Status</dt>
              <dd>
                <Badge tone={statusTone[ov.data.agent_status]}>{ov.data.agent_status}</Badge>
              </dd>
              <dt>Chat model</dt>
              <dd className="mono">{ov.data.effective_model}</dd>
              <dt>Last answer</dt>
              <dd>{relativeTime(ov.data.last_success_at)}</dd>
              <dt>Knowledge items</dt>
              <dd>
                <Link to="/knowledge" className="link">
                  {ov.data.memory_count}
                </Link>
              </dd>
            </dl>
          </Card>
        </div>
      )}
    </>
  );
}
