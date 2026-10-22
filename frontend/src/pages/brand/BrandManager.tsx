import { useEffect, useState, type ReactNode } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Loading, relativeTime } from "../../components/ui";
import { bm, num, STATUS, type BmEvent, type Overview, type Video } from "./api";
import { AnalyticsTab } from "./Analytics";
import { CalendarTab } from "./Calendar";
import { TrendsTab } from "./Trends";
import { SettingsTab } from "./Settings";
import { StudioTab } from "./Studio";
import { TikTokTab } from "./TikTok";
import "./brand.css";

/** BRAND MANAGER: the AI social media manager. Main screen = one button; everything complicated lives in Settings. */
const LOCAL = import.meta.env.MODE !== "test" && typeof window !== "undefined" && ["127.0.0.1", "localhost"].includes(window.location.hostname);

const LIVE = "https://pricechat.live/admin/brand-manager";

/** Brand Manager lives on the live site only: TikTok signs in to, and fetches videos from, pricechat.live, and the AI
 * worker serves the live queue. The local copy of the dashboard sends you there instead of keeping a second, idle queue. */
function GoLive() {
  const target = LIVE + window.location.pathname.replace(/^\/admin\/brand-manager/, "");
  useEffect(() => { const t = window.setTimeout(() => window.location.assign(target), 2500); return () => window.clearTimeout(t); }, [target]);
  return (
    <div className="bmx">
      <section className="bmx-card">
        <h2>Brand Manager runs on the live site</h2>
        <p>This is the local copy of your dashboard. TikTok, your videos and the AI worker all work with <b>pricechat.live</b>, so you're being taken there now.</p>
        <p><a className="btn btn-primary" href={target}>Open Brand Manager on pricechat.live</a></p>
      </section>
    </div>
  );
}

export function BrandManagerPage() {
  if (LOCAL) return <GoLive />;
  return (
    <div className="bmx">
      <nav className="bmx-tabs" aria-label="Brand Manager">
        <NavLink to="/brand-manager" end>Overview</NavLink>
        <NavLink to="/brand-manager/tiktok">TikTok</NavLink>
        <NavLink to="/brand-manager/studio">Content Studio</NavLink>
        <NavLink to="/brand-manager/trends">Trends</NavLink>
        <NavLink to="/brand-manager/calendar">Calendar</NavLink>
        <NavLink to="/brand-manager/analytics">Analytics</NavLink>
        <NavLink to="/brand-manager/settings">Settings</NavLink>
        <span className="bmx-soon" title="Coming after TikTok">X · Instagram · Facebook · YouTube <em>soon</em></span>
      </nav>
      <Routes>
        <Route index element={<OverviewTab />} />
        <Route path="tiktok" element={<TikTokTab />} />
        <Route path="studio" element={<StudioTab />} />
        <Route path="studio/:id" element={<StudioTab />} />
        <Route path="trends" element={<TrendsTab />} />
        <Route path="calendar" element={<CalendarTab />} />
        <Route path="analytics" element={<AnalyticsTab />} />
        <Route path="settings" element={<SettingsTab />} />
        <Route path="*" element={<Navigate to="/brand-manager" replace />} />
      </Routes>
    </div>
  );
}

const STATE_LABEL = { running: "RUNNING", stopped: "STOPPED", blocked: "BLOCKED", error: "ERROR" } as const;

function OverviewTab() {
  const { data, failure, setData, reload } = useApi(bm.overview, 10000);
  const [busy, setBusy] = useState(false);
  const [consent, setConsent] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const a = data.automation;
  const needsConsent = !a.upload_consent;

  const toggle = async () => {
    setBusy(true);
    setError(null);
    try {
      const next = a.running ? await bm.stop() : await bm.start(consent);
      setData({ ...data, automation: next });
      void reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <section className={`bmx-hero is-${a.status}`}>
        <div className="bmx-hero-text">
          <p className="bmx-eyebrow">Brand Manager · {data.brand.name}</p>
          <h1>YOUR AI BRAND MANAGER</h1>
          <p className="bmx-sub">Your social media runs itself.</p>
          <div className="bmx-pills">
            <span className={`bmx-pill is-${a.status}`}><i />AI {STATE_LABEL[a.status]}</span>
            <span className={`bmx-pill ${data.tiktok.status === "connected" ? "is-running" : "is-blocked"}`}>
              <i />TikTok {data.tiktok.status === "connected" ? `@${data.tiktok.username ?? "connected"}` : data.tiktok.status}
            </span>
            {a.last_tick_at && <span className="bmx-pill is-muted">checked {relativeTime(a.last_tick_at)}</span>}
          </div>
        </div>
        <div className="bmx-go">
          <button className={`bmx-big ${a.running ? "is-stop" : ""}`} onClick={() => void toggle()} disabled={busy || (!a.running && needsConsent && !consent)}>
            {busy ? "…" : a.running ? "STOP AUTO" : "START AUTO"}
          </button>
          {!a.running && needsConsent && (
            <label className="bmx-consent">
              <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              Brand Manager may upload finished videos to my TikTok drafts. I post them from the TikTok app.
            </label>
          )}
          {a.running && <p className="bmx-fine">Stopping finishes what's rendering and starts nothing new.</p>}
        </div>
      </section>

      <ApiErrorBanner error={error} />
      {(a.status === "blocked" || a.status === "error" || (!a.running && a.blocking.length > 0)) && (
        <div className="bmx-alert" role="alert">
          <b>{a.status === "error" ? "Something went wrong" : "AUTO can't run yet"}</b>
          <ul>{(a.reason && !a.blocking.includes(a.reason) ? [a.reason, ...a.blocking] : a.blocking).map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      )}
      {a.warnings.length > 0 && <div className="bmx-note"><ul>{a.warnings.map((w) => <li key={w}>{w}</li>)}</ul></div>}

      <section className="bmx-stats">
        <Tile label="Videos created today" value={data.stats.created_today} />
        <Tile label="Published today" value={data.stats.published_today} hint={data.stats.sent_to_drafts_today ? `+${data.stats.sent_to_drafts_today} sent to drafts` : "confirmed by TikTok"} />
        <Tile label="Upcoming scheduled" value={data.stats.upcoming} />
        <Tile label="Total views" value={num(data.stats.total_views)} hint={data.stats.measured_videos ? `${data.stats.measured_videos} videos measured` : "from TikTok's API"} />
        <Tile label="Followers gained" value={data.stats.followers_gained === null ? "—" : `${data.stats.followers_gained >= 0 ? "+" : ""}${num(data.stats.followers_gained)}`}
              hint={data.stats.followers !== null ? `${num(data.stats.followers)} followers` : "needs user.info.stats"} />
        <Tile label="Best video" value={data.stats.best_video ? num(data.stats.best_video.metrics?.views) : "—"} hint={data.stats.best_video?.topic ?? "no views yet"} />
      </section>

      <div className="bmx-cols">
        <section className="bmx-card">
          <h2>Pipeline</h2>
          {data.in_progress.length === 0 && <p className="muted">Nothing in progress. {a.running ? "The next video is planned before its posting slot." : "Press START AUTO."}</p>}
          {data.in_progress.map((v) => <Pipeline key={v.id} v={v} stages={data.pipeline} />)}
          <h2 style={{ marginTop: 18 }}>Up next</h2>
          {data.upcoming.length === 0 ? <p className="muted">No videos scheduled yet.</p> : (
            <ul className="bmx-list">{data.upcoming.map((v) => <li key={v.id}><VideoLine v={v} /></li>)}</ul>
          )}
        </section>
        <section className="bmx-card">
          <h2>Activity</h2>
          <EventList events={data.events} />
          <p className="bmx-fine">AI workers: {data.workers.length === 0 ? "none registered (Settings › AI)" : data.workers.map((w) => `${w.name} ${w.online ? "online" : "offline"}`).join(" · ")}</p>
        </section>
      </div>
    </>
  );
}

function Tile({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return <div className="bmx-tile"><span>{label}</span><b>{value}</b>{hint && <small>{hint}</small>}</div>;
}

export function Pipeline({ v, stages }: { v: Video; stages: { key: string; label: string }[] }) {
  return (
    <div className="bmx-pipe">
      <p className="bmx-pipe-title"><NavLink to={`/brand-manager/studio/${v.id}`}>{v.topic}</NavLink> <StatusBadge s={v.status} /></p>
      <ol>
        {stages.map((s) => {
          const st = v.stages[s.key]?.status ?? "waiting";
          return <li key={s.key} className={`is-${st}`} title={v.stages[s.key]?.note || s.label}><i />{s.label}</li>;
        })}
      </ol>
    </div>
  );
}

export function StatusBadge({ s }: { s: Video["status"] }) {
  const [label, tone] = STATUS[s];
  return <span className={`bmx-badge is-${tone}`}>{label}</span>;
}

export function VideoLine({ v }: { v: Video }) {
  return (
    <span className="bmx-vline">
      <NavLink to={`/brand-manager/studio/${v.id}`}>{v.topic}</NavLink>
      <span className="muted">{v.slot_at ? new Date(v.slot_at).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" }) : "unscheduled"} · {v.template_label}</span>
      <StatusBadge s={v.status} />
    </span>
  );
}

export function EventList({ events }: { events: BmEvent[] }) {
  if (!events.length) return <p className="muted">Nothing yet.</p>;
  return (
    <ul className="bmx-events">
      {events.map((e, i) => (
        <li key={i} className={`is-${e.level}`}><time>{relativeTime(e.at)}</time><span>{e.message}</span></li>
      ))}
    </ul>
  );
}

export type { Overview };
