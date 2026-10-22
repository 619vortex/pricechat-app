import { useState } from "react";
import { api } from "../api/client";
import type { ContentItemRow } from "../api/types";
import { compact } from "./social";
import { ApiErrorBanner, Badge, Empty, formatTime, relativeTime } from "./ui";

const TYPE_LABEL: Record<string, string> = { short: "Short", long_form: "Long form" };

export function duration(s: number | null): string {
  if (s == null) return "-";
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
}

function syncStatus(i: ContentItemRow): { tone: "good" | "warn" | "bad" | "neutral"; text: string } {
  if (!i.available) return { tone: "bad", text: "no longer on YouTube" };
  if (i.privacy_status !== "public") return { tone: "warn", text: i.privacy_status };
  return { tone: "good", text: `synced ${relativeTime(i.last_synced_at)}` };
}

/** Published content (YouTube today) with website controls: show/hide, featured, type override. */
export function ContentItemsTable({ items, onChanged }: { items: ContentItemRow[]; onChanged: (row: ContentItemRow) => void }) {
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<number | null>(null);
  async function update(i: ContentItemRow, body: Parameters<typeof api.updateContentItem>[1]) {
    setBusy(i.id);
    try {
      onChanged(await api.updateContentItem(i.id, body));
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }
  if (items.length === 0) return <Empty>No synced content yet. Connect YouTube and run Sync now.</Empty>;
  return (
    <>
      <ApiErrorBanner error={error} />
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th />
              <th>Title</th>
              <th>Platform / type</th>
              <th>Published</th>
              <th>Website</th>
              <th>Views</th>
              <th>Sync</th>
            </tr>
          </thead>
          <tbody>
            {items.map((i) => {
              const st = syncStatus(i);
              return (
                <tr key={i.id}>
                  <td style={{ width: 96 }}>
                    {i.thumbnail_url ? <img src={i.thumbnail_url} alt="" loading="lazy" className="content-thumb" /> : <span className="content-thumb" />}
                  </td>
                  <td style={{ maxWidth: 340 }}>
                    <a className="link" href={i.url} target="_blank" rel="noreferrer">
                      {i.title || "(untitled)"}
                    </a>
                    {i.featured && (
                      <>
                        {" "}
                        <Badge tone="warn">featured</Badge>
                      </>
                    )}
                    <div className="muted small">
                      {duration(i.duration_seconds)} · {i.metrics.likes ?? "-"} likes · {i.metrics.comments ?? "-"} comments
                    </div>
                  </td>
                  <td className="small">
                    <div>YouTube</div>
                    <select
                      className="input input-sm"
                      style={{ minWidth: 150 }}
                      value={i.type_source === "manual" ? i.content_type : "auto"}
                      disabled={busy === i.id}
                      title={i.type_reason}
                      onChange={(e) => void update(i, { content_type: e.target.value as "short" | "long_form" | "auto" })}
                    >
                      <option value="auto">Auto: {TYPE_LABEL[i.content_type] ?? i.content_type}</option>
                      <option value="short">Short</option>
                      <option value="long_form">Long form</option>
                    </select>
                    <select
                      className="input input-sm"
                      style={{ minWidth: 150, marginTop: 4, display: "block" }}
                      aria-label="Website series"
                      value={i.series ?? "none"}
                      disabled={busy === i.id}
                      onChange={(e) => void update(i, { series: e.target.value as "whats_it_worth" | "originals" | "none" })}
                    >
                      <option value="none">No series</option>
                      <option value="whats_it_worth">What's It Worth?</option>
                      <option value="originals">Originals</option>
                    </select>
                    <select
                      className="input input-sm"
                      style={{ minWidth: 150, marginTop: 4, display: "block" }}
                      aria-label="Website category"
                      value={i.category ?? "none"}
                      disabled={busy === i.id}
                      onChange={(e) => void update(i, { category: e.target.value as "markets" | "crypto" | "money" | "culture" | "none" })}
                    >
                      <option value="none">No category</option>
                      <option value="markets">Markets</option>
                      <option value="crypto">Crypto</option>
                      <option value="money">Money</option>
                      <option value="culture">Culture</option>
                    </select>
                  </td>
                  <td className="small nowrap">{formatTime(i.published_at)}</td>
                  <td className="small">
                    <Badge tone={i.on_website ? "good" : "neutral"}>{i.on_website ? "on website" : "hidden"}</Badge>
                    {i.visibility_source === "manual" && <span className="muted"> (your choice)</span>}
                    <div className="btn-group" style={{ marginTop: 4 }}>
                      {i.website_visible ? (
                        <button className="btn btn-sm" disabled={busy === i.id} onClick={() => void update(i, { website_visible: false })}>
                          Hide from website
                        </button>
                      ) : (
                        <button className="btn btn-sm" disabled={busy === i.id || !i.available} onClick={() => void update(i, { website_visible: true })}>
                          Show on website
                        </button>
                      )}
                      <button className="btn btn-sm btn-ghost" disabled={busy === i.id} onClick={() => void update(i, { featured: !i.featured })}>
                        {i.featured ? "Unfeature" : "Feature"}
                      </button>
                      {i.visibility_source === "manual" && (
                        <button className="btn btn-sm btn-ghost" disabled={busy === i.id} onClick={() => void update(i, { visibility: "auto" })} title="Automatic: public videos are shown">
                          Auto
                        </button>
                      )}
                    </div>
                  </td>
                  <td>{i.metrics.views == null ? "n/a" : compact(i.metrics.views)}</td>
                  <td className="small">
                    <Badge tone={st.tone}>{st.text}</Badge>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="muted small">
        Website: newest first; the newest public upload is the featured item, then the next two. Public videos appear automatically; hidden, private,
        unlisted or deleted ones never do. "Featured" is stored for later priority placement. Type is detected automatically (hover the selector for
        why). Series puts a video in What's It Worth? or Originals; category adds it to Markets / Crypto / Money / Culture (those menu links appear
        once something is filed there). Syncing never changes series or category.
      </p>
    </>
  );
}
