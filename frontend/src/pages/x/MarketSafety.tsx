import { useState } from "react";
import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, relativeTime } from "../../components/ui";

/** Phase 1 (owner, 2026-11-21): the market-data pause (desk-measured market posts held, news goes on; the safety stop switches it on by
 * itself when figures can't be believed) and the correction workflow (edit, reply or delete a published post, two-step confirm, audited). */

const json = (method: string, value: unknown): RequestInit => ({ method, body: JSON.stringify(value), headers: { "Content-Type": "application/json" } });

export type HeldFigure = { kind: string; subject: string; why: string; at: string };

export type ClassPause = Record<string, { at?: string; reason?: string }>;
export type PauseChange = { market_pause?: boolean; liq_pause?: boolean; class_pause_clear?: string[] };
const CLASS_NAME: Record<string, string> = { crypto: "Crypto", index: "Index", stock: "Stock", commodity: "Commodity" };

export function MarketPause({ paused, reason, held, busy, save, liqPaused = false, liqReason = null, classPause = {} }: {
  paused: boolean; reason: string | null; held: HeldFigure[]; busy: boolean; save: (change: PauseChange) => Promise<void>;
  liqPaused?: boolean; liqReason?: string | null; classPause?: ClassPause;
}) {
  const classes = Object.entries(classPause);
  return (
    <Card title="Market-data posts">
      <p className="muted">Price moves, data posts, market checks and liquidation flushes measured by the desk. News posts are not affected.</p>
      <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <Badge tone={paused ? "warn" : "good"}>{paused ? "Paused: nothing market-related is posted" : "On"}</Badge>
        {paused
          ? <button className="btn btn-sm" disabled={busy} onClick={() => { if (window.confirm("Resume market-data posts? The desk will post measured price moves, data posts and liquidation flushes again, within its checks and limits.")) void save({ market_pause: false }); }}>Resume market-data posts</button>
          : <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => void save({ market_pause: true })}>Pause market-data posts</button>}
      </div>
      {paused && reason && <p className="small" role="status">Why: {reason}</p>}
      {liqPaused ? (
        <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <Badge tone="warn">Liquidation posts paused</Badge>
          <span className="small">Price news checked against two markets goes on.{liqReason ? ` Why: ${liqReason}` : ""}</span>
          <button className="btn btn-sm" disabled={busy} onClick={() => { if (window.confirm("Resume liquidation posts? Liquidation figures will be published again, within the desk's checks.")) void save({ liq_pause: false }); }}>Resume liquidation posts</button>
        </div>
      ) : (
        <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <Badge tone="good">Liquidation posts on</Badge>
          <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => void save({ liq_pause: true })}>Pause liquidation posts</button>
        </div>
      )}
      {classes.map(([cls, info]) => (
        <div key={cls} className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <Badge tone="warn">{`${CLASS_NAME[cls] ?? cls} market posts paused`}</Badge>
          <span className="small">Every other asset class and the news go on.{info.reason ? ` Why: ${info.reason}` : ""}{info.at ? ` (${relativeTime(info.at)})` : ""}</span>
          <button className="btn btn-sm" disabled={busy} onClick={() => { if (window.confirm(`Resume ${(CLASS_NAME[cls] ?? cls).toLowerCase()} market posts?`)) void save({ class_pause_clear: [cls] }); }}>{`Resume ${(CLASS_NAME[cls] ?? cls).toLowerCase()} posts`}</button>
        </div>
      ))}
      {held.length > 0 && (
        <details>
          <summary>Figures held as implausible or contradicted ({held.length})</summary>
          <ul className="small">{held.slice().reverse().map((h, i) => <li key={i}>{relativeTime(h.at)}: {h.subject} ({h.kind.replace(/_/g, " ")}): {h.why}</li>)}</ul>
        </details>
      )}
    </Card>
  );
}

type Action = "tg_edit" | "tg_reply" | "tg_delete" | "x_delete" | "x_reply";
const ACTIONS: { value: Action; label: string; text: boolean }[] = [
  { value: "tg_edit", label: "Telegram: edit the post", text: true },
  { value: "tg_reply", label: "Telegram: reply under the post", text: true },
  { value: "tg_delete", label: "Telegram: delete the post", text: false },
  { value: "x_reply", label: "X: reply under the post", text: true },
  { value: "x_delete", label: "X: delete the post", text: false },
];
type Preview = { platform: string; current_text: string | null; new_text: string | null; chars: number; limit: number | null; problems: string[]; warnings: string[];
  emergency_stop: boolean; dry_run?: boolean; confirm_token?: string; expires_at?: string; target?: { url?: string } };
type AuditRow = { at: string; event: number; action: string; outcome: string; error: string | null; new_text: string | null; old_text: string | null };

export function Corrections() {
  const audit = useApi(() => request<{ rows: AuditRow[] } | AuditRow[]>("/x/newsdesk/corrections"), 60000);
  const [eventId, setEventId] = useState("");
  const [action, setAction] = useState<Action>("tg_edit");
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState("");
  const [busy, setBusy] = useState(false);
  const needsText = ACTIONS.find((a) => a.value === action)?.text ?? false;
  const payload = { action, text: needsText ? text : null };
  const rows: AuditRow[] = Array.isArray(audit.data) ? audit.data : Array.isArray((audit.data as { rows?: AuditRow[] } | null)?.rows) ? (audit.data as { rows: AuditRow[] }).rows : [];

  async function doPreview() {
    setBusy(true); setError(null); setDone(""); setPreview(null);
    try { setPreview(await request<Preview>(`/x/newsdesk/events/${Number(eventId)}/corrections/preview`, json("POST", payload))); }
    catch (e) { setError(e); } finally { setBusy(false); }
  }
  async function doConfirm() {
    if (!preview?.confirm_token) return;
    if (!window.confirm(`Send this ${preview.platform} correction now? It is public and can't be taken back.`)) return;
    setBusy(true); setError(null);
    try {
      const row = await request<AuditRow>(`/x/newsdesk/events/${Number(eventId)}/corrections`, json("POST", { ...payload, confirm_token: preview.confirm_token }));
      setDone(`Done: ${row.outcome}${row.error ? ` (${row.error})` : ""}`); setPreview(null); await audit.reload();
    } catch (e) { setError(e); } finally { setBusy(false); }
  }

  return (
    <Card title="Corrections">
      <p className="muted">Correct a published post: edit it, reply under it, or delete it. Preview first: nothing is sent until you confirm. Every action is recorded.</p>
      {error ? <ApiErrorBanner error={error} /> : null}
      <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label className="small">Event #<input className="input" style={{ width: 90 }} inputMode="numeric" value={eventId} onChange={(e) => { setEventId(e.target.value.replace(/\D/g, "")); setPreview(null); }} /></label>
        <label className="small">Action<select className="input" value={action} onChange={(e) => { setAction(e.target.value as Action); setPreview(null); }}>
          {ACTIONS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
        </select></label>
      </div>
      {needsText && <textarea className="input" rows={6} style={{ width: "100%" }} placeholder="The correction. Telegram formatting works: <b>bold</b>, <s>struck through</s>." value={text} onChange={(e) => { setText(e.target.value); setPreview(null); }} />}
      <div className="row" style={{ gap: 8 }}>
        <button className="btn btn-sm" disabled={busy || !eventId || (needsText && !text.trim())} onClick={() => void doPreview()}>Preview</button>
        {preview?.confirm_token && <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => void doConfirm()}>Confirm and send</button>}
      </div>
      {done && <p className="small" role="status">{done}</p>}
      {preview && (
        <div className="small" role="region" aria-label="Correction preview">
          {preview.emergency_stop && <p className="banner banner-warn">The emergency stop is on. Corrections still go out: they are your own repairs.</p>}
          {preview.dry_run && <p className="banner banner-warn">X is in dry run: this is recorded, not sent.</p>}
          <p><b>Now:</b> {preview.current_text ?? "(not stored)"}</p>
          {preview.new_text && <p><b>After:</b> {preview.new_text}</p>}
          {preview.limit && <p>{preview.chars} / {preview.limit} characters</p>}
          {preview.problems.length > 0 && <ul className="bad">{preview.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
          {preview.warnings.length > 0 && <ul>{preview.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
        </div>
      )}
      <h4>Recorded corrections</h4>
      {rows.length === 0 ? <Empty>None yet.</Empty> : (
        <ul className="small">{rows.slice(0, 20).map((r, i) => <li key={i}>{relativeTime(r.at)}: event #{r.event}, {r.action.replace("_", " ")}: {r.outcome}{r.error ? ` (${r.error})` : ""}</li>)}</ul>
      )}
    </Card>
  );
}
