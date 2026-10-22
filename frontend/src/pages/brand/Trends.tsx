import { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Loading, relativeTime } from "../../components/ui";
import { bm, num, type Trend, type TrendStatus } from "./api";

const BADGE: Record<TrendStatus, [string, string]> = {
  new: ["New", "is-good"], rising: ["Rising", "is-good"], searched: ["Searched", "is-info"], measuring: ["Measuring", "is-info"],
  steady: ["Steady", ""], cooling: ["Cooling", "is-warn"],
};
const LIVE: TrendStatus[] = ["new", "rising", "searched", "measuring"];

/** The words finance and crypto headlines suddenly use: counted from linked headlines, compared with the past week. */
export function TrendsTab() {
  const { data, failure, reload } = useApi(bm.trends, 120000);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const navigate = useNavigate();
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const run = async (key: string, f: () => Promise<unknown>) => {
    setBusy(key);
    setError(null);
    try { await f(); } catch (e) { setError(e); } finally { setBusy(null); }
  };
  const make = (t: Trend) => run(t.term, async () => { const v = await bm.trendVideo(t.term); navigate(`/brand-manager/studio/${v.id}`); });
  const shown = all ? data.trends : data.trends.filter((t) => LIVE.includes(t.status));
  const l = data.learned;
  return (
    <>
      <div className="bmx-toolbar">
        <p className="muted">
          Headlines from {data.outlets.join(", ")}. Scanned {relativeTime(data.scanned_at)}; every 30 minutes.
        </p>
        <button className="btn" disabled={!!busy} onClick={() => void run("scan", async () => { await bm.refreshTrends(); await reload(); })}>
          {busy === "scan" ? "Scanning…" : "Scan now"}
        </button>
      </div>
      <ApiErrorBanner error={error} />
      {!data.category_on && (
        <p className="bmx-note">“Finance trends” stories are off in <NavLink to="/brand-manager/settings">Settings › Strategy</NavLink>. Trend words still boost the topics that mention them.</p>
      )}
      <section className="bmx-card">
        <h2>How Content Studio uses this</h2>
        <ul className="bmx-facts">
          <li>A topic that mentions a <b>new, rising or searched</b> word ranks higher, and the writer is told to use that word in the hook if it fits.</li>
          <li>A one-word trend can become a hashtag on that video.</li>
          <li>Strong trends (3+ headlines from 2+ outlets) can become their own story: the headlines, attributed, plus these counts.</li>
          <li>
            {l.multiplier != null
              ? <>Learned from TikTok: trend videos average <b>{num(l.by_trend[0]?.avg_views)}</b> views vs <b>{num(l.by_trend[1]?.avg_views)}</b>, so the boost is ×{l.multiplier}.</>
              : <>Learning: once 3 trend videos and 3 others have TikTok views, the boost follows what actually performed{l.by_trend[0]?.videos ? ` (${l.by_trend[0].videos} trend videos measured so far)` : ""}.</>}
          </li>
        </ul>
        {l.words.length > 0 && <p className="bmx-fine">Trend words that beat your average: {l.words.map((w) => `${w.label} (${num(w.avg_views)} views)`).join(" · ")}</p>}
      </section>
      <section className="bmx-card">
        <div className="bmx-toolbar">
          <h2>{all ? "All words today" : "Trending now"}</h2>
          <button className="btn btn-ghost" onClick={() => setAll(!all)}>{all ? "Only trending" : "Show steady and cooling too"}</button>
        </div>
        {shown.length === 0 ? (
          <p className="muted">{data.trends.length ? "Nothing new or rising right now." : "No scan yet. Press Scan now."}</p>
        ) : (
          <table className="bmx-table bmx-trends">
            <thead><tr><th>Word</th><th>Status</th><th>Headlines 24h</th><th>Past 7 days → today</th><th>Your videos</th><th></th></tr></thead>
            <tbody>{shown.map((t) => (
              <TrendRow key={t.term} t={t} open={open === t.term} onToggle={() => setOpen(open === t.term ? null : t.term)}
                        busy={busy === t.term} disabled={!!busy} onMake={() => void make(t)} />
            ))}</tbody>
          </table>
        )}
      </section>
    </>
  );
}

function TrendRow({ t, open, onToggle, busy, disabled, onMake }: { t: Trend; open: boolean; onToggle: () => void; busy: boolean; disabled: boolean; onMake: () => void }) {
  const [label, cls] = BADGE[t.status];
  const canStory = t.kind === "headline" && t.docs >= 3 && t.outlets.length >= 2;
  const views = t.videos.filter((v) => v.views != null);
  return (
    <>
      <tr>
        <td><button className="bmx-linkbtn" aria-expanded={open} onClick={onToggle}><b>{t.label}</b></button></td>
        <td><span className={`bmx-badge ${cls}`}>{label}</span></td>
        <td>
          {t.kind === "search" ? `#${t.examples[0]?.rank ?? "?"} on CoinGecko` : <>{t.docs} · {t.outlets.length} outlets</>}
          {t.baseline != null && t.kind === "headline" && <span className="muted"> (avg {t.baseline}/day)</span>}
        </td>
        <td>{t.kind === "headline" ? <Bars series={t.series} /> : <span className="muted">—</span>}</td>
        <td>{t.videos.length ? `${t.videos.length}${views.length ? ` · ${num(Math.round(views.reduce((a, v) => a + (v.views ?? 0), 0) / views.length))} avg views` : ""}` : "—"}</td>
        <td>
          {canStory && t.videos.length === 0
            ? <button className="btn" disabled={disabled} onClick={onMake}>{busy ? "Planning…" : "Make a video"}</button>
            : <span className="muted bmx-fine">{t.videos.length ? "Made today" : "Boosts matching topics"}</span>}
        </td>
      </tr>
      {open && (
        <tr className="bmx-trend-more">
          <td colSpan={6}>
            <ul className="bmx-facts">
              {t.examples.map((e) => (
                <li key={e.url + e.title}>{e.url ? <a href={e.url} target="_blank" rel="noreferrer noopener">{e.title}</a> : e.title} <span className="muted">· {e.source}</span></li>
              ))}
            </ul>
            {t.videos.length > 0 && (
              <p className="bmx-fine">Used in: {t.videos.map((v, i) => (
                <span key={v.id}>{i ? " · " : ""}<NavLink to={`/brand-manager/studio/${v.id}`}>{v.topic}</NavLink>{v.views != null ? ` (${num(v.views)} views)` : ""}</span>
              ))}</p>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

/** Headlines per day: the previous 7 days, then today (highlighted). */
function Bars({ series }: { series: number[] }) {
  const hi = Math.max(1, ...series);
  return (
    <svg className="bmx-bars" viewBox={`0 0 ${series.length * 10} 24`} role="img" aria-label={`Headlines per day: ${series.join(", ")}`}>
      {series.map((v, i) => {
        const h = Math.max(1, (v / hi) * 22);
        return <rect key={i} x={i * 10 + 1} y={24 - h} width={8} height={h} className={i === series.length - 1 ? "is-today" : ""} />;
      })}
    </svg>
  );
}
