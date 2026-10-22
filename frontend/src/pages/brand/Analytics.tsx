import { useState } from "react";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Loading, relativeTime } from "../../components/ui";
import { bm, num, type Group } from "./api";
import { VideoLine } from "./BrandManager";

/** Only numbers TikTok's API reported. Empty until published videos have views. */
export function AnalyticsTab() {
  const { data, failure, reload } = useApi(bm.analytics, 60000);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const i = data.insights;
  const collect = async () => {
    setBusy(true);
    setError(null);
    try { await bm.collect(); await reload(); } catch (e) { setError(e); } finally { setBusy(false); }
  };
  return (
    <>
      <div className="bmx-toolbar">
        <p className="muted">Source: TikTok's API (views, likes, comments, shares, followers). Last read {relativeTime(data.last_collected)}.</p>
        <button className="btn" disabled={busy} onClick={() => void collect()}>{busy ? "Reading TikTok…" : "Refresh from TikTok"}</button>
      </div>
      <ApiErrorBanner error={error} />
      {i.notes.map((n) => <p key={n} className="bmx-note">{n}</p>)}
      {data.followers.length > 1 && <section className="bmx-card"><h2>Followers</h2><Spark points={data.followers.map((f) => f.followers)} /></section>}
      {i.videos_measured > 0 && (
        <div className="bmx-cols">
          <section className="bmx-card">
            <h2>What works</h2>
            <ul className="bmx-facts">
              {i.best_video && <li>Best video: <b>{i.best_video.topic}</b> ({num(i.best_video.views)} views)</li>}
              {i.by_category?.[0] && <li>Best topic type: <b>{i.by_category[0].key}</b> ({num(i.by_category[0].avg_views)} avg views)</li>}
              {i.by_template?.[0] && <li>Best template: <b>{i.by_template[0].key}</b></li>}
              {i.best_length && <li>Best length: <b>{i.best_length}</b></li>}
              {i.by_hour?.[0] && <li>Best posting hour: <b>{i.by_hour[0].key}</b></li>}
            </ul>
            {!!i.top_hooks?.length && <><h3>Hooks that worked</h3><ul className="bmx-facts">{i.top_hooks.map((h) => <li key={h}>“{h}”</li>)}</ul></>}
            {!!i.underperforming?.length && <><h3>Underperforming</h3><ul className="bmx-facts">{i.underperforming.map((u) => <li key={u.id}>{u.topic} · {num(u.views)} views</li>)}</ul></>}
            {i.category_multipliers && Object.keys(i.category_multipliers).length > 0 && (
              <p className="bmx-fine">Topic ranking adjusts gently from this: {Object.entries(i.category_multipliers).map(([k, m]) => `${k} ×${m}`).join(" · ")}.</p>
            )}
          </section>
          <section className="bmx-card">
            <h2>By category</h2><GroupTable rows={i.by_category ?? []} />
            <h2>By length</h2><GroupTable rows={i.by_length ?? []} />
          </section>
        </div>
      )}
      <section className="bmx-card">
        <h2>Measured videos</h2>
        {data.videos.length === 0 ? <p className="muted">No TikTok numbers yet.</p> : (
          <table className="bmx-table">
            <thead><tr><th>Video</th><th>Views</th><th>Likes</th><th>Comments</th><th>Shares</th><th>Engagement</th></tr></thead>
            <tbody>{data.videos.map((v) => {
              const m = v.metrics ?? { views: null, likes: null, comments: null, shares: null };
              const eng = m.views ? (((m.likes ?? 0) + (m.comments ?? 0) + (m.shares ?? 0)) / m.views * 100).toFixed(1) + "%" : "—";
              return <tr key={v.id}><td><VideoLine v={v} /></td><td>{num(m.views)}</td><td>{num(m.likes)}</td><td>{num(m.comments)}</td><td>{num(m.shares)}</td><td>{eng}</td></tr>;
            })}</tbody>
          </table>
        )}
      </section>
    </>
  );
}

function GroupTable({ rows }: { rows: Group[] }) {
  if (!rows.length) return <p className="muted">—</p>;
  return (
    <table className="bmx-table"><thead><tr><th></th><th>Videos</th><th>Avg views</th><th>Engagement</th></tr></thead>
      <tbody>{rows.map((r) => <tr key={r.key}><td>{r.key}</td><td>{r.videos}</td><td>{num(r.avg_views)}</td><td>{(r.engagement * 100).toFixed(1)}%</td></tr>)}</tbody></table>
  );
}

function Spark({ points }: { points: number[] }) {
  const lo = Math.min(...points), hi = Math.max(...points), span = hi - lo || 1;
  const d = points.map((p, k) => `${k ? "L" : "M"}${(k / (points.length - 1)) * 600},${100 - ((p - lo) / span) * 90}`).join(" ");
  return (
    <div className="bmx-spark">
      <svg viewBox="0 0 600 100" preserveAspectRatio="none" role="img" aria-label={`Followers from ${lo} to ${points[points.length - 1]}`}><path d={d} /></svg>
      <p className="muted">{num(points[0])} → <b>{num(points[points.length - 1])}</b></p>
    </div>
  );
}
