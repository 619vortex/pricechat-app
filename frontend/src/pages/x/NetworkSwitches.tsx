import { useState } from "react";
import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Badge, Card } from "../../components/ui";

/** Admin › X › News Desk: Price Chat News switches (app/newsdesk/network). The desk discovers, verifies, picks pictures and publishes on
 * its own; these are the owner's controls: an emergency pause for Telegram and X together, and source links in X posts. */

export type NetworkState = { x_links: boolean; x_cost_per_post_usd: number; emergency: boolean; x_paused: string; telegram_paused: string;
  post_style?: PostStyle };

export type PostStyle = "wire" | "clean" | "classic";

const STYLES: { id: PostStyle; name: string; hint: string }[] = [
  { id: "wire", name: "Wire", hint: "Opt-in: short factual headline, BREAKING / JUST IN / UPDATE only when earned, a verified chart or a real credited photo when it adds something, text-only otherwise; sources and @thepricechat on one line" },
  { id: "clean", name: "Clean", hint: "Default: picture first (real photo, photo + chart, chart, or a labelled AI illustration), 1-3 sentences, sources, @thepricechat" },
  { id: "classic", name: "Classic", hint: "The older layout: headline on the picture, caption with byline and why it matters" },
];

export function NetworkSwitches() {
  const { data, failure, reload } = useApi(() => request<NetworkState>("/x/newsdesk/network"), 30000);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (failure) return <Card title="Price Chat News"><ApiErrorBanner error={failure} /></Card>;
  if (!data || typeof data.x_links !== "boolean") return null;
  const put = async (body: Partial<Pick<NetworkState, "x_links" | "emergency" | "post_style">>) => {
    setBusy(true); setError(null);
    try { await request("/x/newsdesk/network", { method: "PUT", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } }); await reload(); }
    catch (e) { setError(e); } finally { setBusy(false); }
  };
  const pauseLabel = data.emergency ? "Resume posting" : "Emergency pause";
  return (
    <Card title="Price Chat News">
      {error ? <ApiErrorBanner error={error} /> : null}
      <p className="muted">Fully automatic: stories are found, verified, given a picture and posted to Telegram and X without approval. Every post links the original reporting.</p>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <Badge tone={data.emergency ? "bad" : "good"}>{data.emergency ? "Paused: nothing is posted" : "Posting automatically"}</Badge>
        <button className={data.emergency ? "btn btn-sm" : "btn btn-sm btn-danger"} disabled={busy}
          onClick={() => { if (data.emergency || window.confirm("Stop all News Desk posts on Telegram and X until you resume?")) void put({ emergency: !data.emergency }); }}>
          {pauseLabel}
        </button>
      </div>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center", marginTop: 8 }}>
        <span>Source links on X: <strong>{data.x_links ? "on" : "off"}</strong></span>
        <span className="small muted">${data.x_cost_per_post_usd.toFixed(3)} per X post ({data.x_links ? "X charges $0.20 for a post with a link" : "named sources only"})</span>
        <button className="btn btn-sm" disabled={busy} onClick={() => void put({ x_links: !data.x_links })}>{data.x_links ? "Turn links off" : "Turn links on"}</button>
      </div>
      {!data.emergency && (data.x_paused || data.telegram_paused) ? (
        <p className="small muted">Lane pauses: {[data.x_paused && `X: ${data.x_paused}`, data.telegram_paused && `Telegram: ${data.telegram_paused}`].filter(Boolean).join(" · ")}</p>
      ) : null}
    </Card>
  );
}

/** Admin › Social Media › X/Twitter › News Desk, at the top: how posts look on Telegram and X. Clean is the default; Wire and Classic
 * are opt-in. Only the look changes: publishing (live/dry run, automation, limits) is set elsewhere and never touched here. */
export function PostStyleSwitch() {
  const { data, failure, reload } = useApi(() => request<NetworkState>("/x/newsdesk/network"), 30000);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  if (failure) return <Card title="Post style"><ApiErrorBanner error={failure} /></Card>;
  if (!data) return null;
  const current = STYLES.find((x) => x.id === data.post_style) ?? STYLES[1]; // clean is the default
  const pick = async (id: PostStyle) => {
    if (id === current.id) return;
    setBusy(true); setError(null);
    try { await request("/x/newsdesk/network", { method: "PUT", body: JSON.stringify({ post_style: id }), headers: { "Content-Type": "application/json" } }); await reload(); }
    catch (e) { setError(e); } finally { setBusy(false); }
  };
  return (
    <Card title="Post style">
      {error ? <ApiErrorBanner error={error} /> : null}
      <div className="row" role="group" aria-label="Post style" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        {STYLES.map((x) => (
          <button key={x.id} type="button" aria-pressed={x.id === current.id} disabled={busy}
                  className={x.id === current.id ? "btn btn-primary" : "btn"} onClick={() => void pick(x.id)}>
            {x.name}{x.id === "clean" ? " (default)" : ""}
          </button>
        ))}
      </div>
      <p className="small" style={{ marginTop: 8 }}>Now: <strong>{current.name}</strong> · <span>{current.hint}</span></p>
      <p className="small muted">Changes only how posts look. Publishing settings stay as they are.</p>
    </Card>
  );
}
