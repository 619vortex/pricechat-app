import { useCallback, useEffect, useState } from "react";
import { MemberApiError, memberApi } from "./members";
import "./workers.css";

/* Kiwi Passport Worker System (Phase 1: Telegram invite workers). The worker's tab and the manager's review queue. Every
   number comes from the server (which also enforces who may see and do what). */

type Sub = { id: number; username: string | null; tg_user_id: number | null; source: string; status: string; submitted_at: string; verified_at: string | null;
  joined_at: string | null; engagement: number; engagement_at: string | null; still_member: boolean | null; reject_reason: string | null;
  report_id: number | null; note: string | null; evidence?: Record<string, unknown> };
type Day = { day: string; target: number; verified: number; status: string; report_id: number | null; reason: string | null };
type Me = { roles: string[]; assignment?: { id: number; job_type: string; daily_target: number; since: string } | null; today?: Day; timezone?: string;
  links?: { bot: string; chat: string | null; chat_unavailable: string | null };
  totals?: { submissions: number; verified: number; approved: number; pending: number; rejected: number; engagement: number };
  submissions?: Sub[]; page?: number; pages?: number };
type Report = { id: number; worker: { id: number; username: string }; day: string; target: number; verified_count: number; status: string;
  submitted_at: string; decided_at: string | null; decided_by: string | null; reason: string | null; submissions?: Sub[]; engagement_total?: number;
  reviews?: { at: string; actor_kind: string; actor: string; action: string; reason: string | null }[] };
type Mgr = { workers: { id: number; username: string; active_worker: boolean; assignment: { daily_target: number }; today: Day; last14: Record<string, number> }[];
  reports: Report[]; page: number; pages: number };

const STATUS: Record<string, [string, string]> = {
  SUBMITTED: ["Submitted", "wait"], VERIFIED: ["Membership verified", "ok"], PENDING_REVIEW: ["Pending review", "info"], APPROVED: ["Approved", "ok"],
  REJECTED: ["Rejected", "bad"], IN_PROGRESS: ["In progress", "wait"], READY_TO_SUBMIT: ["Ready to submit", "info"], PENDING_APPROVAL: ["Pending manager approval", "info"],
  PENDING: ["Pending manager approval", "info"],
};
const SOURCE: Record<string, string> = { form: "typed", chat_link: "your invite link", bot_start: "your bot link" };
const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—");

function Pill({ s }: { s: string }) {
  const [label, tone] = STATUS[s] ?? [s, "wait"];
  return <span className={`wk-pill is-${tone}`}>{label}</span>;
}

export function WorkerBadge({ size = "md" }: { size?: "sm" | "md" }) {
  return <span className={`wk-badge is-${size}`} title="Price Chat Worker"><i aria-hidden>👷</i>WORKER</span>;
}

export function ManagerBadge() {
  return <span className="wk-badge is-mgr" title="Price Chat Manager"><i aria-hidden>🧭</i>MANAGER</span>;
}

function Progress({ d }: { d: Day }) {
  const pct = Math.min(100, (d.verified / Math.max(1, d.target)) * 100);
  return (
    <div className="wk-progress" role="progressbar" aria-valuemin={0} aria-valuemax={d.target} aria-valuenow={d.verified} aria-label="Verified invites today">
      <div className="wk-progress-bar"><span style={{ width: `${pct}%` }} /></div>
      <b>{d.verified} / {d.target}</b>
    </div>
  );
}

export function WorkerTab() {
  const [me, setMe] = useState<Me | null>(null);
  const [page, setPage] = useState(1);
  const [name, setName] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reports, setReports] = useState<{ items: Report[]; pages: number } | null>(null);
  const [rpage, setRpage] = useState(1);
  const load = useCallback(() => memberApi<Me>(`/members/workers/me?page=${page}`).then(setMe).catch((e) => setErr(e instanceof Error ? e.message : "Unavailable")), [page]);
  useEffect(() => { void load(); const id = window.setInterval(() => void load(), 60_000); return () => window.clearInterval(id); }, [load]);
  useEffect(() => { void memberApi<{ items: Report[]; pages: number }>(`/members/workers/reports?page=${rpage}`).then(setReports).catch(() => undefined); }, [rpage, me?.today?.status]);
  if (!me) return <p className="site-empty">{err ?? "Loading your work…"}</p>;
  if (!me.roles.includes("WORKER")) return null;
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null); setMsg(null);
    try {
      const s = await memberApi<Sub>("/members/workers/submissions", { method: "POST", body: JSON.stringify({ username: name }) });
      setMsg(`@${s.username} submitted. It counts once the bot sees them join the community.`);
      setName("");
      setPage(1);
      await load();
    } catch (e2) {
      setErr(e2 instanceof MemberApiError || e2 instanceof Error ? e2.message : "Failed");
    } finally { setBusy(false); }
  }
  async function sendReport() {
    setBusy(true); setErr(null);
    try { await memberApi("/members/workers/reports/submit", { method: "POST" }); setMsg("Report sent for manager approval."); await load(); }
    catch (e2) { setErr(e2 instanceof Error ? e2.message : "Failed"); } finally { setBusy(false); }
  }
  const t = me.totals!;
  const d = me.today!;
  async function copy(link: string) { try { await navigator.clipboard.writeText(link); setMsg("Link copied ✓"); } catch { /* blocked */ } }
  return (
    <div className="wk">
      <section className="wk-hero">
        <div>
          <WorkerBadge />
          <h2>{me.assignment ? "Telegram invites" : "No job yet"}</h2>
          <p>{me.assignment ? `Bring ${me.assignment.daily_target} new people into the Price Chat Telegram today. Only real joins count.`
            : "An admin will assign you a job soon."}</p>
        </div>
        {me.assignment && <div className="wk-today"><span>Today ({me.timezone})</span><Progress d={d} /><Pill s={d.status} />
          {d.status === "READY_TO_SUBMIT" && <button className="wk-btn" disabled={busy} onClick={() => void sendReport()}>Submit today's report</button>}
          {d.status === "REJECTED" && d.reason && <small className="wk-bad">Rejected: {d.reason}</small>}</div>}
      </section>
      {msg && <p className="site-banner" role="status">{msg}</p>}
      {err && <p className="site-error" role="alert">{err}</p>}
      {me.assignment && <>
        <section className="wk-card">
          <h3>Your invite links</h3>
          <p className="wk-muted">Invites through your links are credited automatically when the person joins. Typed usernames need the bot to see them join.</p>
          <div className="wk-links">
            {me.links?.chat ? <div><span>Group invite link</span><code>{me.links.chat}</code><button className="wk-btn is-ghost" onClick={() => void copy(me.links!.chat!)}>Copy</button></div>
              : <div><span>Group invite link</span><em className="wk-muted">unavailable (the bot needs to be a group admin with "Invite users")</em></div>}
            <div><span>Bot link</span><code>{me.links?.bot}</code><button className="wk-btn is-ghost" onClick={() => void copy(me.links!.bot)}>Copy</button></div>
          </div>
        </section>
        <section className="wk-card">
          <h3>Submit an invited member</h3>
          <form className="wk-form" onSubmit={(e) => void submit(e)}>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="@telegram_username" maxLength={80} aria-label="Telegram username" autoComplete="off" />
            <button className="wk-btn" disabled={busy || name.trim().length < 3}>Submit</button>
          </form>
        </section>
      </>}
      <div className="wk-stats">
        {([["Submissions", t.submissions], ["Verified invites", t.verified], ["Approved", t.approved], ["Pending", t.pending], ["Rejected", t.rejected],
          ["Engagement (messages)", t.engagement]] as [string, number][]).map(([k, v]) => <div key={k}><small>{k}</small><b>{v}</b></div>)}
      </div>
      <section className="wk-card">
        <h3>Submissions</h3>
        {!me.submissions?.length ? <p className="wk-muted">Nothing yet.</p> : (
          <div className="wk-table"><table>
            <thead><tr><th>Telegram</th><th>Status</th><th>Messages</th><th>Submitted</th><th>Detail</th></tr></thead>
            <tbody>{me.submissions.map((s) => <tr key={s.id}>
              <td>{s.username ? `@${s.username}` : <em className="wk-muted">no username</em>}<small className="wk-muted"> · {SOURCE[s.source] ?? s.source}</small></td>
              <td><Pill s={s.status} />{s.still_member === false && <small className="wk-bad"> left</small>}</td>
              <td className="wk-num">{["VERIFIED", "PENDING_REVIEW", "APPROVED"].includes(s.status) ? <b>💬 {s.engagement}</b> : "—"}</td>
              <td>{when(s.submitted_at)}</td>
              <td className="wk-muted">{s.reject_reason ?? s.note ?? (s.verified_at ? `verified ${when(s.verified_at)}` : "")}</td></tr>)}</tbody>
          </table></div>)}
        {(me.pages ?? 1) > 1 && <div className="wk-pages"><button className="wk-btn is-ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>← Newer</button>
          <span>{page} / {me.pages}</span><button className="wk-btn is-ghost" disabled={page >= (me.pages ?? 1)} onClick={() => setPage(page + 1)}>Older →</button></div>}
      </section>
      <section className="wk-card">
        <h3>Report history</h3>
        {!reports?.items.length ? <p className="wk-muted">No reports yet.</p> : (
          <div className="wk-table"><table><thead><tr><th>Day</th><th>Verified</th><th>Status</th><th>Decided</th></tr></thead>
            <tbody>{reports.items.map((r) => <tr key={r.id}><td>{r.day}</td><td>{r.verified_count} / {r.target}</td><td><Pill s={r.status} />{r.reason && <small className="wk-muted"> {r.reason}</small>}</td>
              <td>{r.decided_at ? `${when(r.decided_at)} · ${r.decided_by}` : "—"}</td></tr>)}</tbody></table></div>)}
        {(reports?.pages ?? 1) > 1 && <div className="wk-pages"><button className="wk-btn is-ghost" disabled={rpage <= 1} onClick={() => setRpage(rpage - 1)}>← Newer</button>
          <span>{rpage} / {reports?.pages}</span><button className="wk-btn is-ghost" disabled={rpage >= (reports?.pages ?? 1)} onClick={() => setRpage(rpage + 1)}>Older →</button></div>}
      </section>
    </div>
  );
}

function ReportReview({ id, onDone }: { id: number; onDone: () => void }) {
  const [r, setR] = useState<Report | null>(null);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { void memberApi<Report>(`/members/workers/manager/reports/${id}`).then(setR).catch((e) => setErr(e instanceof Error ? e.message : "Unavailable")); }, [id]);
  async function decide(approve: boolean) {
    setErr(null);
    try {
      setR(await memberApi<Report>(`/members/workers/manager/reports/${id}/decision`, { method: "POST", body: JSON.stringify({ approve, reason: reason || null }) }));
      onDone();
    } catch (e) { setErr(e instanceof Error ? e.message : "Failed"); }
  }
  if (!r) return <p className="wk-muted">{err ?? "Loading…"}</p>;
  return (
    <div className="wk-review">
      <p><b>{r.worker.username}</b> · {r.day} · {r.verified_count} / {r.target} verified · engagement 💬 {r.engagement_total} <Pill s={r.status} /></p>
      <div className="wk-table"><table><thead><tr><th>Telegram</th><th>Telegram id</th><th>Via</th><th>Joined</th><th>In group</th><th>Messages</th></tr></thead>
        <tbody>{r.submissions?.map((s) => <tr key={s.id}><td>{s.username ? `@${s.username}` : "—"}</td><td className="wk-num">{s.tg_user_id ?? "—"}</td>
          <td>{SOURCE[s.source] ?? s.source}{(s.evidence?.joined_via_link as string) ? " ✓ link" : ""}</td><td>{when(s.joined_at)}</td>
          <td>{s.still_member === false ? <span className="wk-bad">left</span> : s.still_member ? "yes" : "—"}</td><td className="wk-num">💬 {s.engagement}</td></tr>)}</tbody></table></div>
      {r.status === "PENDING" ? <div className="wk-decide">
        <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason (required to reject)" maxLength={1000} aria-label="Reason" />
        <button className="wk-btn" onClick={() => void decide(true)}>Approve</button>
        <button className="wk-btn is-bad" onClick={() => void decide(false)}>Reject</button></div>
        : <p className="wk-muted">Decided {when(r.decided_at)} by {r.decided_by}{r.reason ? `: ${r.reason}` : ""}. Decisions are final.</p>}
      {err && <p className="site-error">{err}</p>}
      <ol className="wk-trail">{r.reviews?.map((v, i) => <li key={i}>{when(v.at)} · {v.actor_kind} {v.actor} · {v.action}{v.reason ? ` · ${v.reason}` : ""}</li>)}</ol>
    </div>
  );
}

export function ManagerTab() {
  const [status, setStatus] = useState("PENDING");
  const [page, setPage] = useState(1);
  const [m, setM] = useState<Mgr | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => memberApi<Mgr>(`/members/workers/manager?status=${status}&page=${page}`).then(setM).catch((e) => setErr(e instanceof Error ? e.message : "Unavailable")),
    [status, page]);
  useEffect(() => { void load(); }, [load]);
  if (!m) return <p className="site-empty">{err ?? "Loading…"}</p>;
  const days = Array.from({ length: 14 }, (_, i) => new Date(Date.now() - (13 - i) * 86400_000).toISOString().slice(0, 10));
  return (
    <div className="wk">
      <section className="wk-hero"><div><ManagerBadge /><h2>Your workers</h2><p>Review daily reports, check the evidence, approve or reject with a reason.</p></div></section>
      <section className="wk-card">
        <h3>Workers ({m.workers.length})</h3>
        {!m.workers.length ? <p className="wk-muted">No workers are assigned to you yet.</p> : (
          <div className="wk-table"><table><thead><tr><th>Worker</th><th>Today</th><th>Status</th><th>Last 14 days (verified per day)</th></tr></thead>
            <tbody>{m.workers.map((w) => { const max = Math.max(1, ...Object.values(w.last14)); return <tr key={w.id}><td>{w.username}{!w.active_worker && <small className="wk-bad"> role revoked</small>}</td>
              <td><Progress d={w.today} /></td><td><Pill s={w.today.status} /></td>
              <td><span className="wk-spark">{days.map((d) => <i key={d} title={`${d}: ${w.last14[d] ?? 0}`} style={{ height: `${((w.last14[d] ?? 0) / max) * 100}%` }} />)}</span></td></tr>; })}</tbody></table></div>)}
      </section>
      <section className="wk-card">
        <h3>Reports</h3>
        <div className="wk-tabs">{["PENDING", "APPROVED", "REJECTED"].map((s) => <button key={s} aria-pressed={status === s} onClick={() => { setStatus(s); setPage(1); setOpen(null); }}>{STATUS[s][0]}</button>)}</div>
        {!m.reports.length ? <p className="wk-muted">None.</p> : <ul className="wk-reports">{m.reports.map((r) => <li key={r.id}>
          <button onClick={() => setOpen(open === r.id ? null : r.id)} aria-expanded={open === r.id}><b>{r.worker.username}</b> · {r.day} · {r.verified_count}/{r.target} <Pill s={r.status} /></button>
          {open === r.id && <ReportReview id={r.id} onDone={() => void load()} />}</li>)}</ul>}
        {m.pages > 1 && <div className="wk-pages"><button className="wk-btn is-ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>← Newer</button>
          <span>{page} / {m.pages}</span><button className="wk-btn is-ghost" disabled={page >= m.pages} onClick={() => setPage(page + 1)}>Older →</button></div>}
      </section>
    </div>
  );
}
