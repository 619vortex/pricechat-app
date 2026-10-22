import { useState } from "react";
import { request } from "../api/client";
import { useApi } from "../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Loading, PageHeader, Stat, relativeTime } from "../components/ui";

/* Worker System (owner): grant/revoke WORKER and MANAGER, assign the Telegram invites job and its daily target, see every
   worker and report, decide reports, and read the full (append-only) history. */

type Day = { day: string; target: number; verified: number; status: string; report_id: number | null };
type Worker = { id: number; username: string; active_worker: boolean; assignment: { id: number; job_type: string; daily_target: number; manager_id: number | null };
  today: Day; last14: Record<string, number> };
type Report = { id: number; worker: { id: number; username: string }; day: string; target: number; verified_count: number; status: string; submitted_at: string;
  decided_at: string | null; decided_by: string | null; reason: string | null;
  submissions?: { id: number; username: string | null; tg_user_id: number | null; source: string; status: string; joined_at: string | null; engagement: number;
    still_member: boolean | null; evidence?: Record<string, unknown> }[];
  reviews?: { at: string; actor_kind: string; actor: string; action: string; reason: string | null }[]; engagement_total?: number };
type Role = { id: number; member_id: number; username: string; role: string; granted_by: string; granted_at: string; revoked_at: string | null; revoked_by: string | null; note: string | null };
type Overview = { workers: Worker[]; reports: Report[]; page: number; pages: number; roles: Role[];
  settings: { timezone: string; community_chat: string; default_target: number; join_window_hours: number; community_chat_id: number | null } };
type Found = { id: number; username: string; display_name: string; rank_level: number; roles: string[] };
type History = { reviews: { at: string; report_id: number; actor_kind: string; actor: string; action: string; reason: string | null }[];
  audit: { at: string; actor: string; action: string; member_id: number | null; details: Record<string, unknown> }[] };

const TONE: Record<string, "good" | "warn" | "bad" | "info" | "neutral"> = { APPROVED: "good", PENDING: "info", REJECTED: "bad", READY_TO_SUBMIT: "info",
  IN_PROGRESS: "warn", PENDING_APPROVAL: "info", VERIFIED: "good", SUBMITTED: "warn", PENDING_REVIEW: "info" };

function ReportDetail({ id, onDone }: { id: number; onDone: () => void }) {
  const r = useApi(() => request<Report>(`/workers/admin/reports/${id}`));
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  async function decide(approve: boolean) {
    setErr(null);
    try { await request(`/workers/admin/reports/${id}/decision`, { method: "POST", body: JSON.stringify({ approve, reason: reason || null }) }); await r.reload(); onDone(); }
    catch (e) { setErr(e instanceof Error ? e.message : "Failed"); }
  }
  if (!r.data) return <Loading />;
  const d = r.data;
  return (
    <div className="stack">
      <div className="table-wrap"><table className="table"><thead><tr><th>Telegram</th><th>Numeric id</th><th>Via</th><th>Joined</th><th>Evidence</th><th>In group</th><th>Messages</th></tr></thead>
        <tbody>{d.submissions?.map((s) => <tr key={s.id}><td>{s.username ? `@${s.username}` : "—"}</td><td className="mono">{s.tg_user_id ?? "—"}</td><td>{s.source}</td>
          <td>{s.joined_at ? relativeTime(s.joined_at) : "—"}</td>
          <td className="small muted">{[s.evidence?.resolved_by, s.evidence?.joined_via_link ? `link ${String(s.evidence.joined_via_link)}` : null].filter(Boolean).join(" · ") || "—"}</td>
          <td>{s.still_member === false ? <Badge tone="bad">left</Badge> : s.still_member ? "yes" : "—"}</td><td>{s.engagement}</td></tr>)}</tbody></table></div>
      {d.status === "PENDING" ? <div className="row">
        <input className="input" placeholder="Reason (required to reject)" value={reason} onChange={(e) => setReason(e.target.value)} />
        <button className="btn btn-primary" onClick={() => void decide(true)}>Approve</button>
        <button className="btn" onClick={() => void decide(false)}>Reject</button></div>
        : <p className="small muted">Decided {d.decided_at ? relativeTime(d.decided_at) : ""} by {d.decided_by}{d.reason ? `: ${d.reason}` : ""}. Final.</p>}
      {err && <p className="banner banner-warn">{err}</p>}
      <ol className="small muted">{d.reviews?.map((v, i) => <li key={i}>{new Date(v.at).toLocaleString()} · {v.actor_kind} {v.actor} · {v.action}{v.reason ? ` · ${v.reason}` : ""}</li>)}</ol>
    </div>
  );
}

export function WorkersAdminPage() {
  const [status, setStatus] = useState("PENDING");
  const ov = useApi(() => request<Overview>(`/workers/admin/overview?status=${status}`));
  const hist = useApi(() => request<History>("/workers/admin/history"));
  const [q, setQ] = useState("");
  const [found, setFound] = useState<Found[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [target, setTarget] = useState<Record<number, number>>({});
  const [mgr, setMgr] = useState<Record<number, string>>({});
  const [set, setSet] = useState<Overview["settings"] | null>(null);
  const d = ov.data;
  const managers = (d?.roles ?? []).filter((r) => r.role === "MANAGER" && !r.revoked_at);
  async function act(fn: () => Promise<unknown>, ok: string) {
    setErr(null); setMsg(null);
    try { await fn(); setMsg(ok); await ov.reload(); await hist.reload(); if (q) setFound((await request<{ items: Found[] }>(`/workers/admin/members?q=${encodeURIComponent(q)}`)).items); }
    catch (e) { setErr(e instanceof Error ? e.message : "Failed"); }
  }
  const role = (member_id: number, r: string, grant: boolean) => act(() => request("/workers/admin/roles", { method: "POST", body: JSON.stringify({ member_id, role: r, grant }) }),
    `${grant ? "Granted" : "Revoked"} ${r}.`);
  const assign = (member_id: number) => act(() => request("/workers/admin/assignments", { method: "POST", body: JSON.stringify({
    member_id, job_type: "TELEGRAM_INVITES", daily_target: target[member_id] || null, manager_id: mgr[member_id] ? Number(mgr[member_id]) : null }) }), "Job assigned.");
  const s = set ?? d?.settings ?? null;
  return (
    <div className="stack">
      <PageHeader title="Workers" subtitle="Kiwi Passport Worker System · Phase 1: Telegram invite workers. Roles, jobs, targets, reports and the approval history."
        actions={<button className="btn" onClick={() => void act(() => request("/workers/admin/verify", { method: "POST" }), "Verification ran.")}>Run verification now</button>} />
      {ov.failure ? <ApiErrorBanner error={ov.failure} /> : null}
      {msg && <p className="banner">{msg}</p>}
      {err && <p className="banner banner-warn">{err}</p>}
      {!d ? <Loading /> : <>
        <div className="stats">
          <Stat label="Active workers" value={d.workers.filter((w) => w.active_worker).length} />
          <Stat label="Managers" value={managers.length} />
          <Stat label="Hit today's target" value={d.workers.filter((w) => w.today.verified >= w.today.target).length} />
          <Stat label={`Reports ${status.toLowerCase()}`} value={d.reports.length} tone={status === "PENDING" && d.reports.length ? "warn" : "neutral"} />
        </div>
        <div className="grid-2">
          <Card title="Grant roles">
            <form className="row" onSubmit={async (e) => { e.preventDefault(); setFound((await request<{ items: Found[] }>(`/workers/admin/members?q=${encodeURIComponent(q)}`)).items); }}>
              <input className="input" placeholder="Search username, name, Telegram @ or member id" value={q} onChange={(e) => setQ(e.target.value)} />
              <button className="btn">Search</button>
            </form>
            {found.length > 0 && <div className="table-wrap"><table className="table"><tbody>{found.map((m) => <tr key={m.id}>
              <td><b>{m.display_name || m.username}</b> <span className="muted small">@{m.username} · #{m.id} · rank {m.rank_level}</span></td>
              <td>{(["WORKER", "MANAGER"] as const).map((r) => m.roles.includes(r)
                ? <button key={r} className="btn btn-ghost" onClick={() => void role(m.id, r, false)}>Revoke {r.toLowerCase()}</button>
                : <button key={r} className="btn" onClick={() => void role(m.id, r, true)}>Make {r.toLowerCase()}</button>)}</td></tr>)}</tbody></table></div>}
            <p className="small muted">Roles sit next to the Kiwi rank: XP, levels and rewards never change. Revoking keeps all history.</p>
          </Card>
          <Card title="Settings">
            {s && <div className="stack">
              <label className="small">Report timezone <input className="input" value={s.timezone} onChange={(e) => setSet({ ...s, timezone: e.target.value })} /></label>
              <label className="small">Community group <input className="input" value={s.community_chat} onChange={(e) => setSet({ ...s, community_chat: e.target.value })} /></label>
              <label className="small">Default daily target <input className="input" type="number" min={1} value={s.default_target} onChange={(e) => setSet({ ...s, default_target: Number(e.target.value) })} /></label>
              <label className="small">A join up to N hours before a submission still counts <input className="input" type="number" min={1} value={s.join_window_hours}
                onChange={(e) => setSet({ ...s, join_window_hours: Number(e.target.value) })} /></label>
              <button className="btn" disabled={!set} onClick={() => void act(() => request("/workers/admin/settings", { method: "PUT", body: JSON.stringify({
                timezone: s.timezone, community_chat: s.community_chat, default_target: s.default_target, join_window_hours: s.join_window_hours }) }).then(() => setSet(null)), "Settings saved.")}>Save settings</button>
              <p className="small muted">Group id {s.community_chat_id ?? "not learned yet (the bot learns it from the group)"}.</p>
            </div>}
          </Card>
        </div>
        <Card title={`Workers (${d.workers.length})`}>
          {!d.workers.length ? <Empty>No jobs assigned yet. Make someone a worker, then assign the job below.</Empty> : <div className="table-wrap"><table className="table">
            <thead><tr><th>Worker</th><th>Today</th><th>Target</th><th>Manager</th><th /></tr></thead>
            <tbody>{d.workers.map((w) => <tr key={w.id}>
              <td>{w.username} {!w.active_worker && <Badge tone="bad">role revoked</Badge>}</td>
              <td>{w.today.verified} / {w.today.target} <Badge tone={TONE[w.today.status]}>{w.today.status.replace(/_/g, " ").toLowerCase()}</Badge></td>
              <td><input className="input" style={{ width: 80 }} type="number" min={1} placeholder={String(w.assignment.daily_target)} value={target[w.id] ?? ""}
                onChange={(e) => setTarget({ ...target, [w.id]: Number(e.target.value) })} /></td>
              <td><select className="input" value={mgr[w.id] ?? String(w.assignment.manager_id ?? "")} onChange={(e) => setMgr({ ...mgr, [w.id]: e.target.value })}>
                <option value="">— none —</option>{managers.filter((m) => m.member_id !== w.id).map((m) => <option key={m.member_id} value={m.member_id}>{m.username}</option>)}</select></td>
              <td><button className="btn" onClick={() => void assign(w.id)}>Update job</button>{" "}
                <button className="btn btn-ghost" onClick={() => void act(() => request(`/workers/admin/assignments/${w.assignment.id}/end`, { method: "POST" }), "Job ended.")}>End job</button></td></tr>)}</tbody></table></div>}
          {(d.roles.filter((r) => r.role === "WORKER" && !r.revoked_at && !d.workers.some((w) => w.id === r.member_id))).map((r) => (
            <p key={r.id} className="row small">{r.username} is a worker without a job ·
              <input className="input" style={{ width: 80 }} type="number" min={1} placeholder={String(d.settings.default_target)} value={target[r.member_id] ?? ""}
                onChange={(e) => setTarget({ ...target, [r.member_id]: Number(e.target.value) })} />
              <select className="input" value={mgr[r.member_id] ?? ""} onChange={(e) => setMgr({ ...mgr, [r.member_id]: e.target.value })}>
                <option value="">no manager</option>{managers.filter((m) => m.member_id !== r.member_id).map((m) => <option key={m.member_id} value={m.member_id}>{m.username}</option>)}</select>
              <button className="btn btn-primary" onClick={() => void assign(r.member_id)}>Assign Telegram invites</button></p>))}
        </Card>
        <Card title="Reports" actions={<span className="row">{["PENDING", "APPROVED", "REJECTED"].map((x) => <button key={x} className={`btn${status === x ? " btn-primary" : ""}`}
          onClick={() => { setStatus(x); setOpen(null); }}>{x.toLowerCase()}</button>)}</span>}>
          {!d.reports.length ? <Empty>None.</Empty> : d.reports.map((r) => <div key={r.id} className="stack">
            <button className="btn btn-ghost" style={{ textAlign: "left" }} onClick={() => setOpen(open === r.id ? null : r.id)}>
              <b>{r.worker.username}</b> · {r.day} · {r.verified_count}/{r.target} · <Badge tone={TONE[r.status]}>{r.status.toLowerCase()}</Badge> · sent {relativeTime(r.submitted_at)}</button>
            {open === r.id && <ReportDetail id={r.id} onDone={() => void ov.reload()} />}</div>)}
        </Card>
        <div className="grid-2">
          <Card title="Approval history (append-only)">
            {!hist.data?.reviews.length ? <Empty>No reviews yet.</Empty> : <ol className="small">{hist.data.reviews.map((v, i) =>
              <li key={i}>{new Date(v.at).toLocaleString()} · report #{v.report_id} · {v.actor_kind} {v.actor} · <b>{v.action}</b>{v.reason ? ` · ${v.reason}` : ""}</li>)}</ol>}
          </Card>
          <Card title="Role & job changes">
            {!hist.data?.audit.length ? <Empty>None yet.</Empty> : <ol className="small">{hist.data.audit.map((a, i) =>
              <li key={i}>{new Date(a.at).toLocaleString()} · {a.actor} · <b>{a.action.replace(/_/g, " ")}</b>{a.member_id ? ` · member #${a.member_id}` : ""} <span className="muted">{JSON.stringify(a.details)}</span></li>)}</ol>}
          </Card>
        </div>
      </>}
    </div>
  );
}
