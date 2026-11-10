import { useState } from "react";
import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Stat, relativeTime } from "../../components/ui";

/** Admin › X › News Desk: picture backgrounds painted on the owner's GPU PC (app/newsdesk/render_queue). Shows whether the PC is
 * online, the queue, speed and success over 24 hours, and the recent renders; one switch turns the GPU backgrounds off. */

type Quality = { mean?: number; spread?: number; detail?: number; nearest?: number };
type Job = { id: number; event: number; status: string; scene: string | null; model: string | null; seconds: number | null; quality: Quality | null; error: string | null; at: string | null };
type Worker = { id: number; name: string; online: boolean; last_seen_at: string | null; info: { gpu?: string | null; model?: string; models?: string[]; loaded?: boolean | string | null; version?: string; ocr?: boolean } };
export type RenderHealth = { enabled: boolean; online: boolean; queued: number; workers: Worker[]; last_24h: Record<string, number>; success_rate: number;
  avg_seconds: number | null; p95_seconds: number | null; cost_usd: number; models?: { slot: string; breaking: string }; recent: Job[] };

const MODEL_NAME: Record<string, string> = { qwen: "Qwen-Image 2512", klein: "FLUX.2 klein 4B", zimage: "Z-Image Turbo" };
const TONE: Record<string, "good" | "warn" | "bad" | "info"> = { done: "good", queued: "info", claimed: "info", failed: "bad", cancelled: "warn" };

export function GpuRender() {
  const { data, failure, reload } = useApi(() => request<RenderHealth>("/x/newsdesk/render/health"), 30000);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{ id: number; image: string; prompt?: string } | null>(null);
  if (failure) return <Card title="GPU pictures"><ApiErrorBanner error={failure} /></Card>;
  if (!data) return null;
  const toggle = async () => {
    setBusy(true); setError(null);
    try { await request("/x/newsdesk/render", { method: "PUT", body: JSON.stringify({ enabled: !data.enabled }), headers: { "Content-Type": "application/json" } }); await reload(); }
    catch (e) { setError(e); } finally { setBusy(false); }
  };
  const show = async (id: number) => {
    if (preview?.id === id) { setPreview(null); return; }
    try { setPreview(await request<{ id: number; image: string; prompt?: string }>(`/x/newsdesk/render/jobs/${id}/image`)); } catch (e) { setError(e); }
  };
  const w = data.workers[0];
  const done = data.last_24h.done ?? 0;
  return (
    <Card title="GPU pictures">
      {error ? <ApiErrorBanner error={error} /> : null}
      <p className="muted">Story backgrounds are painted on your PC's graphics card (no paid image service) and Price Chat draws every headline, figure and credit itself. If the PC is off or slow, posts go with the desk's own visuals.</p>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <Badge tone={!data.enabled ? "warn" : data.online ? "good" : "bad"}>{!data.enabled ? "Turned off" : data.online ? "PC online" : "PC offline: posts use the desk's own visuals"}</Badge>
        {w && <span className="small muted">{w.info.gpu ?? "GPU"} · seen {w.last_seen_at ? relativeTime(w.last_seen_at) : "never"}{w.info.loaded ? ` · ${w.info.loaded === true ? "model loaded" : `${w.info.loaded} loaded`}` : " · idle"}{w.info.ocr ? " · text check on" : ""}</span>}
        <button className="btn btn-sm" disabled={busy} onClick={() => void toggle()}>{data.enabled ? "Turn GPU pictures off" : "Turn GPU pictures on"}</button>
      </div>
      {data.models && <p className="small muted">Scheduled slots: {MODEL_NAME[data.models.slot] ?? data.models.slot}, painted before the slot opens · Breaking: {MODEL_NAME[data.models.breaking] ?? data.models.breaking}</p>}
      <div className="stat-grid">
        <Stat label="Rendered, 24 hours" value={String(done)} />
        <Stat label="Success rate" value={`${Math.round(data.success_rate * 100)}%`} />
        <Stat label="Average / p95" value={data.avg_seconds != null ? `${data.avg_seconds}s / ${data.p95_seconds}s` : "-"} />
        <Stat label="In queue" value={String(data.queued)} />
      </div>
      {data.recent.length === 0 ? <Empty>No renders in the last 24 hours.</Empty> : (
        <table className="table small">
          <thead><tr><th>Job</th><th>Event</th><th>Status</th><th>Scene</th><th>Model</th><th>Time</th><th>When</th></tr></thead>
          <tbody>
            {data.recent.map((j) => (
              <tr key={j.id}>
                <td>{j.status === "done" ? <button className="btn btn-sm" onClick={() => void show(j.id)}>{preview?.id === j.id ? "Hide" : `#${j.id}`}</button> : `#${j.id}`}</td>
                <td>{j.event}</td>
                <td><Badge tone={TONE[j.status] ?? "info"}>{j.status}</Badge>{j.error ? <div className="muted">{j.error}</div> : null}</td>
                <td>{j.scene ?? ""}</td>
                <td>{j.model ? j.model.split("/").pop() : ""}</td>
                <td>{j.seconds != null ? `${j.seconds}s` : ""}</td>
                <td>{j.at ? relativeTime(j.at) : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {preview && <figure style={{ margin: "12px 0 0" }}><img src={preview.image} alt="GPU background, text-free" style={{ width: "100%", borderRadius: 8 }} />{preview.prompt && <figcaption className="small muted">{preview.prompt}</figcaption>}</figure>}
    </Card>
  );
}
