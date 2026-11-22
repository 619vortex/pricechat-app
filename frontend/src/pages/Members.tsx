import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useApi } from "../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Loading, PageHeader, Stat, Tabs, formatTime, relativeTime } from "../components/ui";
import { DailyColumns, LabelledBars } from "./members/charts";
import type { MemberDetail, MembersConfig, RuleRow } from "./members/types";
import { Fold } from "../components/hub";

type Tab = "overview" | "members" | "missions" | "reputation" | "rules" | "ranks" | "setup" | "audit";
const PLATFORM = { telegram: "Telegram", x: "X", google: "Google" } as Record<string, string>;
const SOURCE = { website: "Website", telegram: "Telegram", x: "X", google: "Google", moderator: "Moderators", admin: "Staff adjustments", partner: "Partners" } as Record<string, string>;

/** Dashboard › Members: Price Chat community accounts and the Kiwi XP system. Every change is audited. */
export function MembersPage() {
  // Admin redesign (owner, 2026-11-22): four real tabs, no "More" menu; how XP is earned, XP sources, sign-in setup and the change
  // log fold away below (?tab=rules|reputation|setup|audit opens the Overview with that fold open).
  const asked = new URLSearchParams(window.location.search).get("tab") as Tab | null;
  const folded: Tab[] = ["rules", "reputation", "setup", "audit"];
  const [tab, setTab] = useState<Tab>(() => (asked && !folded.includes(asked) ? asked : "overview"));
  const fold = asked && folded.includes(asked) ? asked : undefined;
  return (
    <>
      <PageHeader title="Members" subtitle="Price Chat community accounts, Kiwi ranks and verified XP. Every change made here is recorded in the audit log." />
      <Tabs
        tabs={[
          { key: "overview", label: "Overview" },
          { key: "members", label: "Find a member" },
          { key: "missions", label: "Missions" },
          { key: "ranks", label: "Ranks" },
        ]}
        value={tab}
        onChange={setTab}
      />
      {tab === "overview" && <Overview />}
      {tab === "members" && <MemberList />}
      {tab === "missions" && <Missions />}
      {tab === "ranks" && <RanksAndUnlocks />}
      <Fold id="members-rules" title="How XP is earned" summary="the rules members earn XP by" focus={fold === "rules"}><Rules /></Fold>
      <Fold id="members-reputation" title="XP sources" summary="where reputation XP comes from" focus={fold === "reputation"}><ReputationSourcesTab /></Fold>
      <Fold id="members-setup" title="Sign-in setup" summary="Telegram, X and Google sign-in" focus={fold === "setup"}><SignInSetup /></Fold>
      <Fold id="members-audit" title="Change log" summary="every change made here" focus={fold === "audit"}><Audit /></Fold>
    </>
  );
}

// --- overview ---------------------------------------------------------------------------------------------------

function Overview() {
  const { data, failure } = useApi(api.membersOverview, 30000);
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const t = data.totals;
  return (
    <>
      <div className="stats">
        <Stat label="Members" value={t.members} />
        <Stat label="Active (7 days)" value={t.active_7d} />
        <Stat label="New (7 days)" value={t.new_7d} />
        <Stat label="XP awarded today" value={t.xp_today} />
        <Stat label="Open flags" value={t.open_flags} tone={t.open_flags ? "warn" : "neutral"} hint="suspicious activity to review" />
        <Stat label="XP paused / banned" value={`${t.xp_suspended} / ${t.banned}`} />
      </div>
      <div className="grid-2">
        <Card title="Members over time">
          <DailyColumns title="Total members" unit="members" points={data.members_over_time} />
        </Card>
        <Card title="Active members">
          <DailyColumns title="Members who earned XP" unit="members" points={data.active_per_day} />
        </Card>
        <Card title="Rank distribution">
          <LabelledBars title="Members per Kiwi rank" unit="members" rows={data.rank_distribution.map((r) => ({ label: `${r.level}. ${r.name}`, value: r.members }))} />
        </Card>
        <Card title="XP earned by source (30 days)">
          <LabelledBars title="Reputation XP" unit="XP" rows={data.xp_by_source.map((r) => ({ label: SOURCE[r.source] ?? r.source, value: r.xp }))} />
          <h4 className="small muted" style={{ margin: "18px 0 6px" }}>Platform linkage</h4>
          <LabelledBars title="Members with a linked account" unit="members" rows={(["telegram", "x", "google"] as const).map((p) => ({ label: PLATFORM[p], value: data.linked[p] }))} />
        </Card>
      </div>
    </>
  );
}

// --- members ----------------------------------------------------------------------------------------------------

function MemberList() {
  const [q, setQ] = useState("");
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState<string | null>(null);
  const { data, failure, reload } = useApi(() => api.membersList(q, filter), 0);
  useEffect(() => {
    const t = window.setTimeout(() => void reload(), 250);
    return () => window.clearTimeout(t);
  }, [q, filter]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      <Card title="Members">
        <div className="row" style={{ justifyContent: "flex-start" }}>
          <input className="input" placeholder="Search username" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 260 }} />
          <select className="input" value={filter} onChange={(e) => setFilter(e.target.value)} style={{ maxWidth: 200 }}>
            <option value="all">All members</option>
            <option value="flagged">Flagged</option>
            <option value="suspended">XP paused</option>
            <option value="banned">Banned</option>
          </select>
          {data && <span className="muted small">{data.total} member(s)</span>}
        </div>
        <ApiErrorBanner error={failure} />
        {!data ? (
          <Loading />
        ) : data.members.length === 0 ? (
          <Empty>No members yet. They appear here when people join on the website.</Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Username</th>
                  <th>Rank</th>
                  <th>XP</th>
                  <th>Linked</th>
                  <th>Last active</th>
                  <th>Joined</th>
                  <th>Status</th>
                  <th>Team role</th>
                </tr>
              </thead>
              <tbody>
                {data.members.map((m) => (
                  <tr key={m.id} className={selected === m.id ? "is-selected" : ""}>
                    <td>
                      <button className="link" onClick={() => setSelected(m.id)}>
                        {m.username}
                      </button>
                      {m.open_flags > 0 && <> <Badge tone="warn">{m.open_flags} flag{m.open_flags > 1 ? "s" : ""}</Badge></>}
                    </td>
                    <td>{m.rank_level}. {m.rank}</td>
                    <td>{m.xp.toLocaleString("en-GB")}</td>
                    <td className="small">{m.linked.map((p) => PLATFORM[p] ?? p).join(", ") || "-"}</td>
                    <td className="small">{m.last_active_at ? relativeTime(m.last_active_at) : "-"}</td>
                    <td className="small nowrap">{formatTime(m.joined_at)}</td>
                    <td>
                      {m.status === "banned" ? <Badge tone="bad">banned</Badge> : m.xp_suspended ? <Badge tone="warn">XP paused</Badge> : <Badge tone="good">active</Badge>}
                    </td>
                    <td><RoleButtons m={m} onChanged={() => void reload()} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {selected && <MemberPanel id={selected} onChanged={() => void reload()} onClose={() => setSelected(null)} />}
    </>
  );
}

const ROLE_LABEL = { WORKER: "Team member", MANAGER: "Manager" } as const;

/** One click to make a Passport member a team member (worker) or a manager, and one click to undo it. */
function RoleButtons({ m, onChanged }: { m: import("./members/types").MemberRow; onChanged: () => void }) {
  const [roles, setRoles] = useState(m.roles ?? []);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => setRoles(m.roles ?? []), [m.roles]);
  async function toggle(role: "WORKER" | "MANAGER") {
    const has = roles.includes(role);
    if (has && !window.confirm(`Remove ${m.username}'s ${ROLE_LABEL[role]} role?`)) return;
    setBusy(true); setErr(null);
    try {
      const row = await api.memberRole(m.id, role, !has);
      setRoles(row.roles ?? []);
      onChanged();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return (
    <div className="role-btns">
      {(["WORKER", "MANAGER"] as const).map((r) => {
        const has = roles.includes(r);
        return (
          <button key={r} className={`btn btn-sm${has ? " btn-primary" : ""}`} disabled={busy} aria-pressed={has} onClick={() => void toggle(r)}
                  title={has ? `Click to remove the ${ROLE_LABEL[r]} role` : `Make ${m.username} a ${ROLE_LABEL[r].toLowerCase()}`}>
            {has ? `✓ ${ROLE_LABEL[r]}` : `+ ${ROLE_LABEL[r]}`}
          </button>
        );
      })}
      {err && <span className="tone-bad small">{err}</span>}
    </div>
  );
}

function MemberPanel({ id, onChanged, onClose }: { id: string; onChanged: () => void; onClose: () => void }) {
  const [d, setD] = useState<MemberDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [badge, setBadge] = useState("og");
  const [award, setAward] = useState<"community.event" | "telegram.helpful_reply">("community.event");
  const [reference, setReference] = useState("");
  useEffect(() => {
    setD(null);
    api.memberDetail(id).then(setD).catch(setError);
  }, [id]);

  async function run(fn: () => Promise<MemberDetail | unknown>) {
    if (!reason.trim() || reason.trim().length < 3) {
      setError(new Error("Write a reason first (it goes into the audit log)."));
      return;
    }
    setBusy(true);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "member" in r) setD(r as MemberDetail);
      setError(null);
      onChanged();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  if (!d) return <Card title="Member">{error ? <ApiErrorBanner error={error} /> : <Loading />}</Card>;
  const m = d.member;
  const why = reason.trim();
  return (
    <Card title={`${m.username} · ${d.rank.current.name}`} actions={<button className="btn btn-sm btn-ghost" onClick={onClose}>Close</button>}>
      <ApiErrorBanner error={error} />
      <div className="grid-2">
        <div className="stack">
          <dl className="kv">
            <dt>XP</dt>
            <dd>
              {m.xp.toLocaleString("en-GB")} reputation{m.bonus_xp ? ` · ${m.bonus_xp} premium (never counts to rank)` : ""}
              {d.rank.next && <span className="muted small"> · {d.rank.xp_to_next} to {d.rank.next.name}</span>}
            </dd>
            <dt>Joined</dt>
            <dd>{formatTime(m.joined_at)}</dd>
            <dt>Status</dt>
            <dd>{m.status}{m.xp_suspended ? " · XP paused" : ""}{m.leaderboard_opt_out ? " · hidden from leaderboard" : ""}</dd>
            <dt>Linked</dt>
            <dd>
              {d.identities.length === 0 ? "-" : d.identities.map((i) => (
                <div key={i.platform} className="small">
                  {PLATFORM[i.platform]}{i.username ? ` @${i.username}` : ""} <span className="muted">· id {i.platform_user_id} · verified {formatTime(i.verified_at)}</span>
                </div>
              ))}
            </dd>
            {d.x_connection && (
              <>
                <dt>X connection</dt>
                <dd className="small">
                  {d.x_connection.status === "active" ? "Active" : "Needs reconnect"} · scopes {d.x_connection.scopes.join(" ") || "-"}
                  <div className="muted">
                    {d.x_connection.has_refresh_token ? "refreshable" : "no refresh token"}
                    {d.x_connection.last_used_at ? ` · last action ${formatTime(d.x_connection.last_used_at)}` : " · no actions yet"}
                    {d.x_connection.last_error ? ` · last problem: ${d.x_connection.last_error}` : ""}
                  </div>
                </dd>
              </>
            )}
            {d.telegram && (
              <>
                <dt>Telegram today</dt>
                <dd className="small">
                  {d.telegram.messages_today} messages, {d.telegram.eligible_today} counted · +{d.telegram.xp_today} / {d.telegram.daily_cap} XP
                  <div className="muted">
                    7 days: {d.telegram.messages_7d ?? 0} messages, +{d.telegram.xp_7d ?? 0} XP, {d.telegram.active_days_7d} active days
                    {d.telegram.last_verified_at ? ` · last counted ${formatTime(d.telegram.last_verified_at)}` : ""}
                  </div>
                </dd>
              </>
            )}
            <dt>Badges</dt>
            <dd>{d.badges.map((b) => b.name).join(", ") || "-"}</dd>
            {d.reputation && (
              <>
                <dt>Rank score</dt>
                <dd>
                  <strong>{d.reputation.rank_score}</strong>{" "}
                  <span className="muted small">
                    = {d.reputation.price_chat_points} Price Chat + {d.reputation.legacy_points} legacy
                    {d.reputation.combot ? ` (Combot XP ${d.reputation.combot.xp}, ${d.reputation.combot.source_type}, imported ${formatTime(d.reputation.combot.imported_at)}${d.reputation.capped ? ", capped" : ""}${d.reputation.combot_counts ? "" : ", not counting"})` : " (no Combot record)"}
                  </span>
                  <div className="muted small">{d.reputation.formula}</div>
                </dd>
              </>
            )}
          </dl>
          <div className="form">
            <label className="field">
              <span className="field-label">Reason (required for every action, saved in the audit log)</span>
              <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Hosted the community AMA" />
            </label>
            <div className="row" style={{ justifyContent: "flex-start" }}>
              <input className="input input-num" type="number" value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="±XP" style={{ width: 100 }} />
              <button className="btn btn-sm" disabled={busy || !Number(amount)} onClick={() => void run(() => api.memberAction(id, "xp", { amount: Number(amount), reason: why }))}>
                Adjust XP
              </button>
            </div>
            <div className="row" style={{ justifyContent: "flex-start" }}>
              <select className="input" value={award} onChange={(e) => setAward(e.target.value as typeof award)} style={{ maxWidth: 220 }}>
                <option value="community.event">Community event (+25)</option>
                <option value="telegram.helpful_reply">Helpful Telegram reply (+10)</option>
              </select>
              <input className="input" value={reference} onChange={(e) => setReference(e.target.value)} placeholder="Event name / message link" style={{ maxWidth: 220 }} />
              <button className="btn btn-sm" disabled={busy || !reference.trim()}
                      onClick={() => void run(() => api.memberAction(id, "award", { event_type: award, reason: why, reference: reference.trim() }))}>
                Award
              </button>
            </div>
            <div className="row" style={{ justifyContent: "flex-start" }}>
              <select className="input" value={badge} onChange={(e) => setBadge(e.target.value)} style={{ maxWidth: 220 }}>
                <option value="og">OG</option>
                <option value="market_nerd">Market Nerd</option>
                <option value="collector">Collector</option>
              </select>
              <button className="btn btn-sm" disabled={busy} onClick={() => void run(() => api.memberAction(id, "badges", { badge, reason: why }))}>
                Award badge
              </button>
            </div>
            <div className="btn-group">
              <button className="btn btn-sm" disabled={busy} onClick={() => void run(() => api.memberAction(id, "status", { xp_suspended: !m.xp_suspended, reason: why }))}>
                {m.xp_suspended ? "Resume XP earning" : "Pause XP earning"}
              </button>
              <button className="btn btn-sm" disabled={busy} onClick={() => void run(() => api.memberAction(id, "flags", { reason: why }))}>
                Flag
              </button>
              <button className="btn btn-sm btn-danger" disabled={busy}
                      onClick={() => void run(() => api.memberAction(id, "status", { status: m.status === "banned" ? "active" : "banned", reason: why }))}>
                {m.status === "banned" ? "Unban" : "Ban"}
              </button>
              <button className="btn btn-sm btn-ghost" disabled={busy}
                      onClick={() => window.confirm(`Delete ${m.username}'s account and all their data? This cannot be undone.`) &&
                        void run(() => api.memberDelete(id, why).then((r) => { onClose(); return r; }))}>
                Delete account
              </button>
            </div>
          </div>
          {d.flags.length > 0 && (
            <>
              <h4>Flags</h4>
              <ul className="feed">
                {d.flags.map((f) => (
                  <li key={f.id}>
                    <div className="feed-body">
                      <span>
                        <Badge tone={f.status === "open" ? "warn" : "neutral"}>{f.status}</Badge> {f.reason}
                      </span>
                      <span className="muted small">{f.kind} · {f.created_by} · {relativeTime(f.created_at)}{f.resolution ? ` · ${f.resolution}` : ""}</span>
                      {f.status === "open" && f.kind === "identity_conflict" && (
                        <span className="small muted">
                          Wants {String(f.details?.platform)} account {String(f.details?.username ?? f.details?.platform_user_id)} (id {String(f.details?.platform_user_id)}),
                          linked to another member.{" "}
                          <button className="btn btn-sm" disabled={busy}
                                  onClick={() => void run(() => api.transferIdentity(f.id, String(f.details?.platform), id, why))}>
                            Move it to this member
                          </button>
                        </span>
                      )}
                      {f.status === "open" && (
                        <span className="btn-group">
                          <button className="btn btn-sm" disabled={busy} onClick={() => void run(() => api.resolveFlag(f.id, "resolved", why))}>Resolve</button>
                          <button className="btn btn-sm btn-ghost" disabled={busy} onClick={() => void run(() => api.resolveFlag(f.id, "dismissed", why))}>Dismiss</button>
                        </span>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
        <div className="stack">
          <h4 style={{ margin: 0 }}>XP ledger</h4>
          <div className="table-wrap" style={{ maxHeight: 420, overflow: "auto" }}>
            <table className="table">
              <tbody>
                {d.events.map((e) => (
                  <tr key={e.id}>
                    <td className="nowrap" style={{ color: e.xp < 0 ? "var(--bad, #f87171)" : undefined, textDecoration: e.reversed ? "line-through" : undefined }}>
                      {e.xp > 0 ? "+" : ""}{e.xp}
                    </td>
                    <td className="small">
                      {e.reason}
                      <div className="muted">{e.event_type} · {e.source} · {e.actor} · {relativeTime(e.occurred_at)}</div>
                    </td>
                    <td>
                      {!e.reversed && !e.is_reversal && (
                        <button className="btn btn-sm btn-ghost" disabled={busy} onClick={() => void run(() => api.reverseXp(e.id, why))}>Reverse</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <h4 style={{ margin: 0 }}>Audit</h4>
          <ul className="feed small">
            {d.audit.slice(0, 20).map((a, i) => (
              <li key={i}>
                <div className="feed-body">
                  <span>{a.action}</span>
                  <span className="muted small">{a.actor} · {relativeTime(a.at)} · {JSON.stringify(a.details)}</span>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Card>
  );
}

// --- missions -------------------------------------------------------------------------------------------------

function Missions() {
  const [expired, setExpired] = useState(false);
  const { data, failure, reload } = useApi(() => api.missionsAdmin(expired), 30000);
  const [error, setError] = useState<unknown>(null);
  const [form, setForm] = useState({ title: "", url: "", platform: "youtube", xp: "3", days: "7", description: "" });
  useEffect(() => void reload(), [expired]); // eslint-disable-line react-hooks/exhaustive-deps
  async function run(fn: () => Promise<unknown>) {
    try {
      await fn();
      setError(null);
      await reload();
    } catch (e) {
      setError(e);
    }
  }
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  return (
    <>
      <Card
        title="Missions"
        actions={
          <span className="btn-group">
            <label className="small muted" style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
              <input type="checkbox" checked={expired} onChange={(e) => setExpired(e.target.checked)} /> show expired
            </label>
            <button className="btn btn-sm" onClick={() => void run(api.generateMissions)}>Generate from latest content</button>
          </span>
        }
      >
        <p className="muted small">
          Content missions are created automatically from the newest Short, X post and Price Chat reply (one active per kind, each expires).
          Only verifiable missions pay XP; at most {data.mission_daily_cap ?? "-"} mission XP per member per day (XP rules › Mission completed).
          Missions never target third-party accounts.
        </p>
        <ApiErrorBanner error={error} />
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Mission</th>
                <th>Platform</th>
                <th>Verification</th>
                <th>XP</th>
                <th>Daily cap</th>
                <th>Done</th>
                <th>XP issued</th>
                <th>Expires</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {data.missions.map((m) => <MissionAdminRow key={m.id} m={m} onRun={run} />)}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title="Create a mission">
        <p className="muted small">
          Manual missions can only point at Price Chat's own content (a synced video, a Price Chat X post, the Price Chat Telegram or a
          website page) and are verified as an open from the site only.
        </p>
        <form className="form" onSubmit={(e) => {
          e.preventDefault();
          void run(() => api.createMission({ title: form.title, url: form.url, platform: form.platform, xp: Number(form.xp),
                                             days: Number(form.days), description: form.description }));
        }}>
          <div className="row" style={{ justifyContent: "flex-start" }}>
            <input className="input" placeholder="Title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} style={{ maxWidth: 280 }} />
            <input className="input" placeholder="Price Chat URL" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} style={{ maxWidth: 360 }} />
            <select className="input" value={form.platform} onChange={(e) => setForm({ ...form, platform: e.target.value })} style={{ maxWidth: 140 }}>
              <option value="youtube">YouTube</option>
              <option value="x">X</option>
              <option value="telegram">Telegram</option>
              <option value="website">Website</option>
            </select>
            <input className="input input-num" value={form.xp} onChange={(e) => setForm({ ...form, xp: e.target.value })} aria-label="XP" /> XP
            <input className="input input-num" value={form.days} onChange={(e) => setForm({ ...form, days: e.target.value })} aria-label="Days" /> days
            <button className="btn btn-primary btn-sm" type="submit" disabled={!form.title || !form.url}>Create</button>
          </div>
        </form>
      </Card>
    </>
  );
}

function MissionAdminRow({ m, onRun }: { m: import("./members/types").MissionRow; onRun: (fn: () => Promise<unknown>) => Promise<void> }) {
  const [xp, setXp] = useState(String(m.xp));
  return (
    <tr>
      <td>
        {m.title}
        <div className="muted small">{m.kind} · {m.cadence}{m.goal > 1 ? ` · goal ${m.goal}` : ""}{m.target.text ? ` · “${m.target.text.slice(0, 60)}”` : m.target.title ? ` · ${m.target.title}` : ""}</div>
      </td>
      <td className="small">{m.platform}</td>
      <td className="small">{m.verification_label}<div className="muted">{m.verification_method}</div></td>
      <td className="nowrap">
        {m.pays_xp === false ? (
          <span title={m.no_xp_reason ?? ""}>0 <span className="muted small">(X policy)</span></span>
        ) : m.xp_from_rule ? (
          <span title="Paid by its XP rule">{m.xp} <span className="muted small">(rule)</span></span>
        ) : (
          <>
            <input className="input input-num" value={xp} onChange={(e) => setXp(e.target.value)} disabled={m.status === "expired"} />
            {Number(xp) !== m.xp && <button className="btn btn-sm" onClick={() => void onRun(() => api.updateMission(m.id, { xp: Number(xp) }))}>Save</button>}
          </>
        )}
      </td>
      <td className="small">{m.daily_cap ?? "-"}</td>
      <td>{m.completed}{m.pending ? <span className="muted small"> +{m.pending} pending</span> : null}</td>
      <td>{m.xp_issued}</td>
      <td className="small nowrap">{m.expires_at ? formatTime(m.expires_at) : "-"}</td>
      <td className="nowrap">
        <Badge tone={m.status === "active" ? "good" : m.status === "paused" ? "warn" : "neutral"}>{m.status}</Badge>{" "}
        {m.status === "active" && <button className="btn btn-sm btn-ghost" onClick={() => void onRun(() => api.updateMission(m.id, { status: "paused" }))}>Pause</button>}
        {m.status === "paused" && <button className="btn btn-sm btn-ghost" onClick={() => void onRun(() => api.updateMission(m.id, { status: "active" }))}>Resume</button>}
        {m.status !== "disabled" && m.status !== "expired" && (
          <button className="btn btn-sm btn-ghost" onClick={() => void onRun(() => api.updateMission(m.id, { status: "disabled" }))}>Disable</button>
        )}
      </td>
    </tr>
  );
}

// --- rules ------------------------------------------------------------------------------------------------------

function Rules() {
  const { data, failure, setData } = useApi(api.membersConfig, 0);
  const [error, setError] = useState<unknown>(null);
  async function save(r: RuleRow, body: Partial<RuleRow>) {
    try {
      setData(await api.updateXpRule(r.event_type, body));
      setError(null);
    } catch (e) {
      setError(e);
    }
  }
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const groups: [string, RuleRow[]][] = [
    ["Active", data.rules.filter((r) => r.availability === "available")],
    ["Not available (cannot be verified yet)", data.rules.filter((r) => r.availability === "unavailable")],
    ["Future (premium, architecture only)", data.rules.filter((r) => r.availability === "future")],
  ];
  return (
    <Card title="XP rules">
      <p className="muted small">
        Each rule pays for one verified event. Daily caps, cooldowns and one-time limits stop farming; on top of that every member can earn at most{" "}
        {data.daily_xp_cap} automatic XP per day. Staff awards and adjustments are not capped but always need a reason. Rules that cannot be verified cannot be switched on.
      </p>
      <ApiErrorBanner error={error} />
      {groups.map(([title, rules]) => (
        <div key={title}>
          <h4>{title}</h4>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Rule</th>
                  <th>Verified by</th>
                  <th>XP</th>
                  <th>Daily cap</th>
                  <th>Cooldown</th>
                  <th>On</th>
                </tr>
              </thead>
              <tbody>
                {rules.map((r) => (
                  <RuleEditor key={r.event_type} r={r} onSave={(b) => void save(r, b)} />
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </Card>
  );
}

function RuleEditor({ r, onSave }: { r: RuleRow; onSave: (body: Partial<RuleRow>) => void }) {
  const editable = r.availability === "available" && r.event_type !== "manual.award";
  const [xp, setXp] = useState(String(r.xp));
  const [cap, setCap] = useState(r.daily_cap == null ? "" : String(r.daily_cap));
  const [cool, setCool] = useState(String(Math.round(r.cooldown_seconds / 60)));
  const dirty = xp !== String(r.xp) || cap !== (r.daily_cap == null ? "" : String(r.daily_cap)) || cool !== String(Math.round(r.cooldown_seconds / 60));
  return (
    <tr>
      <td>
        {r.label}
        <div className="muted small">{r.event_type} · {r.availability === "available" ? r.description : r.availability_note}</div>
      </td>
      <td className="small">{r.verification_source}</td>
      <td>{editable ? <input className="input input-num" value={xp} onChange={(e) => setXp(e.target.value)} /> : r.event_type === "manual.award" ? "any" : r.xp}</td>
      <td>{editable ? <input className="input input-num" value={cap} placeholder="none" onChange={(e) => setCap(e.target.value)} /> : r.daily_cap ?? "-"}</td>
      <td className="nowrap">
        {editable ? (
          <>
            <input className="input input-num" value={cool} onChange={(e) => setCool(e.target.value)} /> min
          </>
        ) : r.cooldown_seconds ? `${Math.round(r.cooldown_seconds / 60)} min` : "-"}
      </td>
      <td className="nowrap">
        <label className="toggle" title={editable ? undefined : r.availability_note}>
          <input type="checkbox" checked={r.enabled} disabled={r.availability !== "available"} aria-label={`Enable ${r.label}`}
                 onChange={(e) => onSave({ enabled: e.target.checked })} />
          <span />
        </label>{" "}
        {dirty && (
          <button className="btn btn-sm" onClick={() => onSave({ xp: Number(xp), cooldown_seconds: Number(cool) * 60, ...(cap !== "" ? { daily_cap: Number(cap) } : {}) })}>
            Save
          </button>
        )}
      </td>
    </tr>
  );
}

// --- ranks, badges, unlocks -------------------------------------------------------------------------------------

function RanksAndUnlocks() {
  const { data, failure, setData } = useApi(api.membersConfig, 0);
  const [error, setError] = useState<unknown>(null);
  async function wrap(fn: () => Promise<MembersConfig>) {
    try {
      setData(await fn());
      setError(null);
    } catch (e) {
      setError(e);
    }
  }
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  return (
    <div className="grid-2">
      <Card title="Kiwi ranks">
        <ApiErrorBanner error={error} />
        <p className="muted small">Rank comes from reputation XP only. Thresholds must increase; changing one updates every member's rank.</p>
        <table className="table">
          <tbody>
            {data.ranks.map((r) => (
              <tr key={r.level}>
                <td>{r.level}</td>
                <td>{r.name}<div className="muted small">art: {r.art.treatment}{r.art.placeholder ? ` + placeholder ${r.art.placeholder} (${r.art.slot})` : ""}</div></td>
                <td>
                  <input className="input input-num" defaultValue={r.min_xp} disabled={r.level === 1} style={{ width: 90 }}
                         onBlur={(e) => Number(e.target.value) !== r.min_xp && void wrap(() => api.updateRank(r.level, { min_xp: Number(e.target.value) }))} /> XP
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <div className="stack">
        <Card title="Unlocks (entitlements)">
          <p className="muted small">Defined for future gating. Nothing is created or gated automatically yet (no VIP groups).</p>
          {data.entitlements.map((e) => (
            <div className="row" key={e.key}>
              <span>
                {e.name} <span className="muted small">· rank {e.required_rank ?? "-"} · {e.description}</span>
              </span>
              <label className="toggle">
                <input type="checkbox" checked={e.enabled} aria-label={`Enable ${e.name}`} onChange={(ev) => void wrap(() => api.updateEntitlement(e.key, { enabled: ev.target.checked }))} />
                <span />
              </label>
            </div>
          ))}
        </Card>
        <Card title="Badges">
          {data.badges.map((b) => (
            <div className="row" key={b.key}>
              <span>
                {b.name} <span className="muted small">· {b.kind}{b.enabled ? "" : " (not active)"} · {b.description}</span>
              </span>
            </div>
          ))}
        </Card>
      </div>
    </div>
  );
}

// --- sign-in setup ----------------------------------------------------------------------------------------------

function SignInSetup() {
  const { data, failure } = useApi(api.memberAuthProviders, 0);
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  return (
    <Card title="Sign-in providers">
      <p className="muted small">
        Members sign in with official login flows; Price Chat never sees passwords. Every address below is built by the server from one
        setting (<code>MEMBERS_PUBLIC_URL</code> = <code>{data.public_url}</code>): open the website at exactly that address while testing.
      </p>
      <dl className="kv">
        <dt>Google</dt>
        <dd>
          <Badge tone={data.google.available ? "good" : "warn"}>{data.google.available ? "ready" : "not set up"}</Badge> {data.google.note}
          <div className="small">
            Google Cloud › APIs &amp; Services › Credentials › OAuth client <code>{data.google.client_id ?? "(none saved)"}</code> (type: Web application):
            <div>Authorized JavaScript origin: <code>{data.google.javascript_origin}</code></div>
            <div>Authorized redirect URI: <code>{data.google.callback}</code></div>
            <div className="muted">Exact match: scheme, host, port and path, no trailing slash. localhost and 127.0.0.1 are different addresses.</div>
          </div>
        </dd>
        <dt>X</dt>
        <dd>
          <Badge tone={data.x.available ? "good" : "warn"}>{data.x.available ? "ready" : "not set up"}</Badge> {data.x.note}
          <div className="small">
            Member sign-in uses <strong>OAuth 2.0 + PKCE</strong> with its own Client ID, separate from the @pricechat connector. In the X developer
            portal › your app › User authentication settings: enable OAuth 2.0, type <em>Web App</em>, App permissions <em>Read and write</em>
            (members press Reply / Repost in their Passport and Price Chat sends it as them; never likes, never XP for X actions), and add this{" "}
            <strong>Callback URI</strong>: <code>{data.x.callback}</code>. Then paste the OAuth 2.0 Client ID and Client Secret here.
            <div>Website URL: <code>{data.x.website_url}</code> · Scopes requested: <code>{data.x.scopes}</code>
              {data.x.client_id ? <> · Client ID in use: <code>{data.x.client_id}</code></> : null}</div>
          </div>
          <XLoginForm />
        </dd>
        <dt>Telegram</dt>
        <dd>
          <Badge tone={data.telegram.available ? "good" : "warn"}>{data.telegram.available ? "ready" : "not set up"}</Badge> {data.telegram.note}
          <div className="small">
            {data.telegram.mode === "deeplink"
              ? `Local mode: members press Start on @${data.telegram.bot ?? "the bot"} with a one-time link; the bot never replies. On the public domain, switch to Telegram's Login Widget (BotFather › /setdomain).`
              : "Login Widget mode."}
          </div>
        </dd>
      </dl>
    </Card>
  );
}

function XLoginForm() {
  const [cid, setCid] = useState("");
  const [secret, setSecret] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  return (
    <form className="row" style={{ justifyContent: "flex-start", marginTop: 8 }} onSubmit={(e) => {
      e.preventDefault();
      api.saveXLoginClient(cid.trim(), secret.trim() || undefined).then(() => { setMsg("Saved (encrypted)."); setCid(""); setSecret(""); setError(null); })
        .catch(setError);
    }}>
      <input className="input" placeholder="OAuth 2.0 Client ID" value={cid} onChange={(e) => setCid(e.target.value)} style={{ maxWidth: 300 }} autoComplete="off" />
      <input className="input" type="password" placeholder="Client Secret (Web App)" value={secret} onChange={(e) => setSecret(e.target.value)}
             style={{ maxWidth: 260 }} autoComplete="new-password" />
      <button className="btn btn-sm btn-primary" type="submit" disabled={cid.trim().length < 10}>Save</button>
      {msg && <span className="small tone-good">{msg}</span>}
      <ApiErrorBanner error={error} />
    </form>
  );
}

// --- reputation sources ---------------------------------------------------------------------------------------

function ReputationSourcesTab() {
  const { data, failure, setData } = useApi(api.reputationSources, 60000);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [key, setKey] = useState("");
  const [chat, setChat] = useState("");
  const [preview, setPreview] = useState<{ id: number; stats: Record<string, unknown> } | null>(null);
  const [sourceDate, setSourceDate] = useState("");
  async function run(fn: () => Promise<import("./members/types").ReputationSources | unknown>) {
    setBusy(true);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "combot" in r) setData(r as import("./members/types").ReputationSources);
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  async function upload(file: File) {
    const fmt = file.name.toLowerCase().endsWith(".json") ? "json" : "csv";
    const content = await file.text();
    await run(async () => {
      const p = await api.previewCombotImport({ filename: file.name, format: fmt, content, ...(sourceDate ? { source_date: sourceDate } : {}) });
      setPreview(p);
      return p;
    });
  }
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const s = data.settings;
  const c = data.combot;
  const st = preview?.stats as Record<string, number | string | unknown[]> | undefined;
  return (
    <>
      <ApiErrorBanner error={error} />
      <div className="grid-2">
        <Card title="Price Chat Telegram">
          <dl className="kv">
            <dt>Status</dt><dd><Badge tone="good">{data.price_chat_telegram.status}</Badge> the source of truth going forward</dd>
            <dt>Linked members</dt><dd>{data.price_chat_telegram.linked_members}</dd>
            <dt>XP events</dt><dd>{data.price_chat_telegram.xp_events}</dd>
          </dl>
          <p className="muted small">Counted from authorised Price Chat chats for linked members only, matched by numeric Telegram id (XP rules › Telegram).</p>
        </Card>
        <Card title="Combot (historical reputation)">
          <dl className="kv">
            <dt>Status</dt>
            <dd><Badge tone={c.status === "connected" ? "good" : c.status === "manual import" ? "warn" : "neutral"}>{c.status}</Badge>
              {c.last_error && <span className="small tone-bad"> · {c.last_error}</span>}</dd>
            <dt>Records</dt><dd>{c.records}</dd>
            <dt>Linked members</dt><dd>{c.matched} matched · {c.unmatched} without a Combot record</dd>
            <dt>Last sync</dt><dd>{c.last_sync_at ? relativeTime(c.last_sync_at) : "never"}{c.last_success_at ? ` · last success ${relativeTime(c.last_success_at)} (${c.last_records} records)` : ""}</dd>
          </dl>
          {c.status === "connected" ? (
            <div className="btn-group">
              <button className="btn btn-sm" disabled={busy} onClick={() => void run(api.syncCombot)}>Sync Combot now</button>
              <button className="btn btn-sm btn-ghost" disabled={busy} onClick={() => void run(api.disconnectCombot)}>Disconnect</button>
            </div>
          ) : (
            <form className="row" style={{ justifyContent: "flex-start" }} onSubmit={(e) => { e.preventDefault(); void run(() => api.connectCombot(key.trim(), chat.trim())); }}>
              <input className="input" type="password" placeholder="Combot API key" value={key} onChange={(e) => setKey(e.target.value)} style={{ maxWidth: 240 }} autoComplete="new-password" />
              <input className="input" placeholder="Group id, e.g. -100…" value={chat} onChange={(e) => setChat(e.target.value)} style={{ maxWidth: 200 }} />
              <button className="btn btn-sm btn-primary" type="submit" disabled={busy || !key.trim() || !chat.trim()}>Connect</button>
            </form>
          )}
          <p className="muted small">
            Uses Combot's official API (getChatUsers on Pro; XP/reputation leaderboards otherwise), every 6 hours. API key: combot.org › Settings,
            from a Telegram admin of the group. Members are matched by numeric Telegram id only.
          </p>
        </Card>
      </div>
      <div className="grid-2">
        <Card title="Kiwi Rank Score">
          <p className="muted small">{data.formula}. Changing a value recalculates every member.</p>
          <RepSettingsForm s={s} busy={busy} onSave={(body) => void run(() => api.updateReputation(body))} />
        </Card>
        <Card title="Import a Combot export (CSV / JSON)">
          <p className="muted small">
            Columns: <code>user_id</code> (numeric Telegram id, required), <code>xp</code>, <code>messages</code>, <code>reputation</code>, <code>rank</code>,
            <code>username</code>. Rows without a numeric id are rejected. Nothing is written until you apply the preview; older data never replaces newer.
          </p>
          <div className="row" style={{ justifyContent: "flex-start" }}>
            <label className="small">Export date <input className="input" type="date" value={sourceDate} onChange={(e) => setSourceDate(e.target.value)} /></label>
            <input type="file" accept=".csv,.json" disabled={busy} onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])} />
          </div>
          {preview && st && (
            <div className="stack" style={{ marginTop: 10 }}>
              <dl className="kv">
                <dt>Rows</dt><dd>{String(st.rows)} · {String(st.valid)} valid · {String(st.invalid)} invalid · {String(st.duplicates)} duplicates</dd>
                <dt>Members</dt><dd>{String(st.matched)} matched (linked) · {String(st.unmatched)} not linked yet</dd>
                <dt>Changes</dt><dd>{String(st.new)} new · {String(st.updates)} updates · {String(st.older_skipped)} older (kept existing) · {String(st.username_changed)} username changed</dd>
              </dl>
              {Array.isArray(st.invalid_examples) && st.invalid_examples.length > 0 && (
                <p className="small muted">Invalid: {(st.invalid_examples as { row: number; reason: string }[]).map((x) => `row ${x.row}: ${x.reason}`).join(" · ")}</p>
              )}
              <div className="btn-group">
                <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void run(async () => { const r = await api.applyCombotImport(preview.id); setPreview(null); return r; })}>
                  Apply import
                </button>
                <button className="btn btn-sm btn-ghost" disabled={busy} onClick={() => void run(async () => { const r = await api.discardCombotImport(preview.id); setPreview(null); return r; })}>
                  Discard
                </button>
              </div>
            </div>
          )}
          {data.imports.length > 0 && (
            <ul className="feed small" style={{ marginTop: 12 }}>
              {data.imports.map((i) => (
                <li key={i.id}>
                  <div className="feed-body">
                    <span>{i.filename ?? i.source_type} <Badge tone={i.status === "applied" ? "good" : "neutral"}>{i.status}</Badge></span>
                    <span className="muted small">{formatTime(i.created_at)} · {i.created_by} · {String(i.stats.valid ?? 0)} valid{i.stats.written != null ? ` · ${String(i.stats.written)} written` : ""}</span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}

function RepSettingsForm({ s, busy, onSave }: { s: import("./members/types").ReputationSources["settings"]; busy: boolean; onSave: (b: object) => void }) {
  const [pc, setPc] = useState(String(s.price_chat_weight));
  const [w, setW] = useState(String(s.combot_weight));
  const [cap, setCap] = useState(String(s.legacy_cap));
  const [tgCap, setTgCap] = useState(String(s.telegram_daily_cap ?? 10));
  const [watchSecs, setWatchSecs] = useState(String(s.watch_seconds ?? 30));
  return (
    <form className="form" onSubmit={(e) => {
      e.preventDefault();
      onSave({ price_chat_weight: Number(pc), combot_weight: Number(w), legacy_cap: Number(cap), telegram_daily_cap: Number(tgCap),
               watch_seconds: Number(watchSecs) });
    }}>
      <div className="row" style={{ justifyContent: "flex-start" }}>
        <label className="small">Price Chat XP weight <input className="input input-num" value={pc} onChange={(e) => setPc(e.target.value)} /></label>
        <label className="small">Combot XP weight <input className="input input-num" value={w} onChange={(e) => setW(e.target.value)} /></label>
        <label className="small">Legacy cap (points, 0 = none) <input className="input input-num" value={cap} onChange={(e) => setCap(e.target.value)} style={{ width: 90 }} /></label>
        <label className="small">Telegram XP per day (all rules) <input className="input input-num" value={tgCap} onChange={(e) => setTgCap(e.target.value)} /></label>
        <label className="small" title="Watch & Earn: seconds a video must play inside the Kiwi Passport before it earns XP (5-600). Shorter videos need only their own length.">
          Watch &amp; Earn seconds <input className="input input-num" value={watchSecs} onChange={(e) => setWatchSecs(e.target.value)} />
        </label>
        <button className="btn btn-sm btn-primary" type="submit" disabled={busy}>Save & recalculate</button>
      </div>
      <label className="row" style={{ justifyContent: "flex-start" }}>
        <span className="toggle">
          <input type="checkbox" checked={s.combot_enabled} disabled={busy} onChange={(e) => onSave({ combot_enabled: e.target.checked })} aria-label="Combot counts towards rank" />
          <span />
        </span>
        <span className="small">Historical Combot XP counts towards rank</span>
      </label>
      <p className="muted small">Example: 2,905 Combot XP x {w} = {Math.round(2905 * Number(w || 0))} {Number(cap) && Math.round(2905 * Number(w || 0)) > Number(cap) ? `→ capped at ${cap}` : ""} legacy points.</p>
    </form>
  );
}

// --- audit ------------------------------------------------------------------------------------------------------

function Audit() {
  const { data, failure } = useApi(api.membersAudit, 30000);
  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  return (
    <Card title="Audit log">
      {data.entries.length === 0 ? (
        <Empty>Nothing yet.</Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>When</th>
                <th>Member</th>
                <th>Who</th>
                <th>Action</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {data.entries.map((a) => (
                <tr key={a.id}>
                  <td className="small nowrap">{formatTime(a.at)}</td>
                  <td>{a.member ?? <span className="muted">(deleted)</span>}</td>
                  <td className="small">{a.actor}</td>
                  <td>{a.action}</td>
                  <td className="small muted">{JSON.stringify(a.details)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
