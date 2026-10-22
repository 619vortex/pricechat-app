import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { request } from "../api/client";
import { Advanced, ApiErrorBanner, Badge, Card, Loading, PageHeader, Stat, relativeTime } from "../components/ui";

/** Invite Bot: consent-based Telegram invitations for the Price Chat community. Several Telegram accounts watch the groups
 * they're approved for; they invite privately ONLY people who asked, and post in groups only where admins agreed. */

interface Overview {
  settings: {
    api_id: number | null; api_hash_saved: boolean; invite_link: string; dm_message: string; group_message: string; daily_limit: number;
    group_post_interval_days: number; opt_in_keywords: string; opt_out_words: string; active_days: number; retention_days: number; monitoring: boolean;
    group_posts_enabled: boolean; dm_interval_seconds: number; auto_send: boolean; invite_direct_contacts: boolean;
  };
  accounts: {
    id: number; name: string; telegram: string | number | null; phone_hint: string; status: string; paused_until: string | null; pause_reason: string | null;
    sent_today: number; daily_limit: number; groups: number; queue: number; last_poll_at: string | null; last_error: string | null; sent_total: number; failed_total: number;
  }[];
  pending_logins: Record<string, string>;
  groups: { id: number; account_id: number; title: string; username: string | null; allow_posts: boolean; last_post_at: string | null }[];
  stats: { members: number; opt_ins: number; opt_outs: number; dm_sent: number; group_sent: number; failed: number };
  run: { state: string; trigger?: string; dry_run?: boolean; started_at?: string; finished_at?: string; totals?: Record<string, number>; log?: string[]; error?: string };
}

const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body), headers: { "Content-Type": "application/json" } });

export function InviteBotPage() {
  const [o, setO] = useState<Overview | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(() => void request<Overview>("/invites").then((d) => { setO(d); setErr(null); }).catch(setErr), []);
  useEffect(() => { load(); const t = window.setInterval(load, 10000); return () => window.clearInterval(t); }, [load]);
  const act = async (label: string, fn: () => Promise<unknown>, done?: string) => {
    setBusy(label); setErr(null); setNote(null);
    try { const r = await fn(); if (r && typeof r === "object" && "accounts" in (r as object)) setO(r as Overview); else load(); if (done) setNote(done); }
    catch (e) { setErr(e); } finally { setBusy(null); }
  };
  const setUp = !!o && !!o.settings.api_id && o.settings.api_hash_saved && o.accounts.length > 0;
  return (
    <>
      <PageHeader title="Invite Bot" subtitle="Grows Price Chat through approved group posts and invitations to people who asked for one."
                  actions={<Link className="btn btn-primary" to="/invite-bot/send">➜ Send invites one by one</Link>} />
      {note && <div className="banner banner-good">{note}</div>}
      <ApiErrorBanner error={err} />
      {!o ? <Loading /> : (
        <div className="stack">
          {/* everyday use first once set up; the setup steps fold away */}
          <div className="stats">
            <Stat label="Asked for an invite" value={o.stats.opt_ins} tone="good" />
            <Stat label="Invites sent" value={o.stats.dm_sent + o.stats.group_sent} hint={`${o.stats.dm_sent} private · ${o.stats.group_sent} group posts`} />
            <Stat label="Failed" value={o.stats.failed} tone={o.stats.failed ? "warn" : "neutral"} />
          </div>
          {setUp ? (
            <>
              <Send o={o} act={act} busy={busy} load={load} />
              <Queue manual={!o.settings.auto_send} busy={o.run.state === "running"} act={act} />
              <Pace o={o} act={act} />
              <Advanced label="Setup: Telegram login, accounts, groups and messages">
                <ApiSetup o={o} act={act} busy={busy} />
                <Accounts o={o} act={act} busy={busy} />
                <Groups o={o} act={act} busy={busy} />
                <SettingsCard o={o} act={act} busy={busy} />
              </Advanced>
            </>
          ) : (
            <>
              <p className="banner banner-info">Finish the 3 setup steps below, then you can start sending invites.</p>
              {(!o.settings.api_id || !o.settings.api_hash_saved) && <ApiSetup o={o} act={act} busy={busy} />}
              <Accounts o={o} act={act} busy={busy} />
              <Groups o={o} act={act} busy={busy} />
              <Pace o={o} act={act} />
              <SettingsCard o={o} act={act} busy={busy} />
            </>
          )}
          <Advanced label={`People active in your groups (last ${o.settings.active_days} days), and the built-in rules`}>
            <Members days={o.settings.active_days} />
          <Card title="Rules (built in)">
            <ul className="muted small">
              <li>Private invitations go only to people who chatted in a watched group in the last {o.settings.active_days} days <b>and</b> asked: they messaged the account or wrote <b>{o.settings.opt_in_keywords}</b>. Random contacts, Telegram's own accounts and bots are never invited.</li>
              <li>At most one invitation per person, ever. Never again after “{o.settings.opt_out_words}”, a block or a failed delivery.</li>
              <li>Group posts are {o.settings.group_posts_enabled ? "ON" : "OFF (DM-only)"}; when on, only where you ticked “admins allow posts”, at most once every {o.settings.group_post_interval_days} days.</li>
              <li>Max {o.settings.daily_limit} invitations per account per day (an operational limit, not a guarantee against Telegram restrictions). One invitation every {gapLabel(o.settings.dm_interval_seconds)} (±15%).</li>
              <li>Any Telegram rate limit stops everything and pauses that account; no other account takes over.</li>
              <li>Stored: numeric id, public username, group, last activity. Never message text, names or phone numbers; activity older than {o.settings.retention_days} days is deleted.</li>
            </ul>
          </Card>
          </Advanced>
        </div>
      )}
    </>
  );
}

type Act = (label: string, fn: () => Promise<unknown>, done?: string) => Promise<void>;

function ApiSetup({ o, act, busy }: { o: Overview; act: Act; busy: string | null }) {
  const [id, setId] = useState(o.settings.api_id ? String(o.settings.api_id) : "");
  const [hash, setHash] = useState("");
  return (
    <Card title="1 · Telegram API credentials">
      <p className="muted small">Log in at <a href="https://my.telegram.org" target="_blank" rel="noreferrer">my.telegram.org</a> › API development tools › create an app. One pair works for every account. The hash is stored encrypted and never shown again.</p>
      <div className="row ib-row">
        <input className="input" placeholder="API ID" inputMode="numeric" value={id} onChange={(e) => setId(e.target.value.replace(/\D/g, ""))} />
        <input className="input" placeholder={o.settings.api_hash_saved ? "API hash (saved)" : "API hash"} value={hash} onChange={(e) => setHash(e.target.value.trim())} type="password" autoComplete="off" />
        <button className="btn" disabled={!id || busy === "api"} onClick={() => void act("api", () => request("/invites/settings", json("PUT", { api_id: Number(id), ...(hash ? { api_hash: hash } : {}) })), "API credentials saved.")}>Save</button>
      </div>
    </Card>
  );
}

function Accounts({ o, act, busy }: { o: Overview; act: Act; busy: string | null }) {
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [pw, setPw] = useState("");
  const pending = name && o.pending_logins[name];
  const ready = !!o.settings.api_id && o.settings.api_hash_saved;
  return (
    <Card title="2 · Telegram accounts">
      {o.accounts.length > 0 && (
        <table className="table">
          <thead><tr><th>Name</th><th>Telegram</th><th>Status</th><th>Today</th><th>Groups</th><th>Queue</th><th>Last check</th><th /></tr></thead>
          <tbody>{o.accounts.map((a) => (
            <tr key={a.id}>
              <td><b>{a.name}</b> <span className="muted small">…{a.phone_hint}</span></td>
              <td>{a.telegram ?? "—"}</td>
              <td><Badge tone={a.status === "connected" ? "good" : a.status === "paused" ? "warn" : "neutral"} title={a.pause_reason ?? a.last_error ?? undefined}>{a.status}</Badge>
                {a.paused_until && <div className="muted small">until {new Date(a.paused_until).toLocaleString()}</div>}
                {a.last_error && <div className="muted small">{a.last_error}</div>}</td>
              <td>{a.sent_today}/{a.daily_limit}</td><td>{a.groups}</td><td>{a.queue}</td>
              <td className="muted small">{a.last_poll_at ? relativeTime(a.last_poll_at) : "—"}</td>
              <td>{a.status !== "disconnected" && <button className="btn btn-sm btn-ghost" onClick={() => { if (window.confirm(`Disconnect ${a.name}? Its session is deleted (history kept).`)) void act("dc", () => request(`/invites/accounts/${a.id}/disconnect`, json("POST"))); }}>Disconnect</button>}</td>
            </tr>))}</tbody>
        </table>
      )}
      <h3>Add an account</h3>
      {!ready ? <p className="muted small">Save the API credentials first.</p> : !pending ? (
        <div className="row ib-row">
          <input className="input" placeholder="Short name (e.g. main)" value={name} onChange={(e) => setName(e.target.value)} maxLength={40} />
          <input className="input" placeholder="Phone (+971…)" value={phone} onChange={(e) => setPhone(e.target.value)} />
          <button className="btn" disabled={!name || !phone || busy === "login"} onClick={() => void act("login", () => request("/invites/accounts/login", json("POST", { name, phone })), "Telegram sent a login code to that account's Telegram app.")}>Send login code</button>
        </div>
      ) : pending === "code" ? (
        <div className="row ib-row">
          <input className="input" placeholder="Login code" value={code} onChange={(e) => setCode(e.target.value.trim())} autoComplete="one-time-code" />
          <button className="btn" disabled={!code || busy === "code"} onClick={() => void act("code", async () => { await request("/invites/accounts/code", json("POST", { name, code })); setCode(""); })}>Confirm</button>
          <button className="btn btn-ghost" onClick={() => void act("cancel", () => request("/invites/accounts/cancel", json("POST", { name })))}>Cancel</button>
        </div>
      ) : (
        <div className="row ib-row">
          <input className="input" type="password" placeholder="Two-step verification password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="off" />
          <button className="btn" disabled={!pw || busy === "pw"} onClick={() => void act("pw", async () => { await request("/invites/accounts/password", json("POST", { name, password: pw })); setPw(""); setName(""); setPhone(""); }, "Account connected.")}>Confirm</button>
        </div>
      )}
      <p className="muted small">Use accounts you own. Codes and passwords go straight to Telegram and are never stored. Sessions are stored encrypted.</p>
    </Card>
  );
}

function Groups({ o, act, busy }: { o: Overview; act: Act; busy: string | null }) {
  const [acc, setAcc] = useState<number | null>(null);
  const [found, setFound] = useState<{ chat_id: number; title: string; username: string | null; approved: boolean }[] | null>(null);
  const connected = o.accounts.filter((a) => a.status !== "disconnected");
  const name = (id: number) => o.accounts.find((a) => a.id === id)?.name ?? id;
  return (
    <Card title="3 · Groups to watch">
      {o.groups.length > 0 ? (
        <table className="table">
          <thead><tr><th>Group</th><th>Account</th><th>Invitation posts</th><th>Last post</th><th /></tr></thead>
          <tbody>{o.groups.map((g) => (
            <tr key={g.id}>
              <td><b>{g.title}</b> {g.username && <span className="muted small">@{g.username}</span>}</td>
              <td>{name(g.account_id)}</td>
              <td><label className="small" title={o.settings.group_posts_enabled ? undefined : "Group posts are disabled (Mode & pace)"} style={o.settings.group_posts_enabled ? undefined : { opacity: 0.5 }}><input type="checkbox" checked={g.allow_posts} onChange={(e) => void act("gp", () => request(`/invites/groups/${g.id}`, json("PATCH", { allow_posts: e.target.checked })))} /> admins allow posts</label></td>
              <td className="muted small">{g.last_post_at ? relativeTime(g.last_post_at) : "never"}</td>
              <td><button className="btn btn-sm btn-ghost" onClick={() => void act("rg", () => request(`/invites/groups/${g.id}`, json("DELETE")))}>Remove</button></td>
            </tr>))}</tbody>
        </table>
      ) : <p className="muted small">No groups yet. Pick the groups each account should watch for people asking to join (only groups it's already in).</p>}
      {connected.length > 0 && (
        <div className="row ib-row">
          <select className="input" value={acc ?? ""} onChange={(e) => { setAcc(Number(e.target.value) || null); setFound(null); }}>
            <option value="">Choose an account…</option>{connected.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
          <button className="btn" disabled={!acc || busy === "find"} onClick={() => void act("find", async () => setFound((await request<{ groups: NonNullable<typeof found> }>(`/invites/accounts/${acc}/groups`)).groups))}>
            {busy === "find" ? "Loading…" : "Show its groups"}</button>
        </div>
      )}
      {found && acc && (
        <table className="table">
          <tbody>{found.map((g) => (
            <tr key={g.chat_id}><td>{g.title} {g.username && <span className="muted small">@{g.username}</span>}</td>
              <td>{g.approved ? <Badge tone="good">watching</Badge> : <>
                <button className="btn btn-sm" onClick={() => void act("ag", async () => { const r = await request<Overview>("/invites/groups", json("POST", { account_id: acc, chat_id: g.chat_id, title: g.title, username: g.username, allow_posts: false })); setFound(found.map((x) => x.chat_id === g.chat_id ? { ...x, approved: true } : x)); return r; })}>Watch this group</button>{" "}
                <button className="btn btn-sm btn-ghost" onClick={() => { if (window.confirm("Only if the group's admins agreed to an occasional Price Chat invitation post. Continue?")) void act("ag", async () => { const r = await request<Overview>("/invites/groups", json("POST", { account_id: acc, chat_id: g.chat_id, title: g.title, username: g.username, allow_posts: true })); setFound(found.map((x) => x.chat_id === g.chat_id ? { ...x, approved: true } : x)); return r; }); }}>Watch + allow posts</button></>}</td>
            </tr>))}</tbody>
        </table>
      )}
    </Card>
  );
}

/** The interval bar: 1 invitation every 10 s … every 1 h (stepped, so both ends are easy to hit). */
const STEPS = [10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 420, 600, 900, 1200, 1800, 2700, 3600];

export function gapLabel(sec: number): string {
  if (sec < 60) return `${sec} sec`;
  if (sec < 3600) return `${Math.round(sec / 60)} min`;
  return "1 hour";
}

function duration(sec: number): string {
  const h = Math.floor(sec / 3600);
  const m = Math.round((sec % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}

function Pace({ o, act }: { o: Overview; act: Act }) {
  const nearest = (v: number) => STEPS.reduce((best, x, i) => (Math.abs(x - v) < Math.abs(STEPS[best] - v) ? i : best), 0);
  const [idx, setIdx] = useState(nearest(o.settings.dm_interval_seconds));
  useEffect(() => setIdx(nearest(o.settings.dm_interval_seconds)), [o.settings.dm_interval_seconds]); // eslint-disable-line react-hooks/exhaustive-deps
  const sec = STEPS[idx];
  const perRun = o.accounts.filter((a) => a.status === "connected").reduce((n, a) => n + Math.min(a.queue, Math.max(0, a.daily_limit - a.sent_today)), 0);
  const save = (v: number) => { if (v !== o.settings.dm_interval_seconds) void act("pace", () => request("/invites/settings", json("PUT", { dm_interval_seconds: v })), `Pace saved: 1 invitation every ${gapLabel(v)}.`); };
  return (
    <Card title="Mode & pace">
      <div className="ib-mode">
        <div>
          <b>Sending</b>{" "}
          <div className="btn-group" role="group" aria-label="Sending mode">
            <button className={`btn btn-sm${o.settings.auto_send ? " btn-primary" : ""}`} onClick={() => void act("auto", () => request("/invites/settings", json("PUT", { auto_send: true })), "Auto: the bot now sends by itself.")}>Auto</button>
            <button className={`btn btn-sm${!o.settings.auto_send ? " btn-primary" : ""}`} onClick={() => void act("manual", () => request("/invites/settings", json("PUT", { auto_send: false })), "Manual: approve invitations yourself in the queue.")}>Manual</button>
          </div>
          <p className="muted small">{o.settings.auto_send ? "Auto: whenever someone asks, the bot DMs them by itself at the pace below." : "Manual: nothing is sent until you approve people in the Invite queue."}
            {" "}DMs go only to people who chatted in a watched group in the last {o.settings.active_days} days AND asked (messaged the account or used the opt-in keyword). Random contacts are never invited.</p>
        </div>
        <div>
          <b>Group posts</b>{" "}
          <div className="btn-group" role="group" aria-label="Group posts">
            <button className={`btn btn-sm${o.settings.group_posts_enabled ? " btn-primary" : ""}`} onClick={() => void act("gpe", () => request("/invites/settings", json("PUT", { group_posts_enabled: true })), "Group posts enabled (only in groups where admins allow them).")}>Enabled</button>
            <button className={`btn btn-sm${!o.settings.group_posts_enabled ? " btn-primary" : ""}`} onClick={() => void act("gpd", () => request("/invites/settings", json("PUT", { group_posts_enabled: false })), "Group posts disabled: DM-only.")}>Disabled</button>
          </div>
          <p className="muted small">{o.settings.group_posts_enabled ? "Posts go only to groups marked “admins allow posts”." : "DM-only: no group posts are sent, whatever the group settings."}</p>
        </div>
      </div>
      <div className="ib-mode" style={{ gridTemplateColumns: "1fr" }}>
        <div>
          <b>People who message the account directly</b>{" "}
          <div className="btn-group" role="group" aria-label="Direct contacts">
            <button className={`btn btn-sm${o.settings.invite_direct_contacts ? " btn-primary" : ""}`} onClick={() => void act("dc1", () => request("/invites/settings", json("PUT", { invite_direct_contacts: true })), "People who message your accounts will now get the invite.")}>Invite them</button>
            <button className={`btn btn-sm${!o.settings.invite_direct_contacts ? " btn-primary" : ""}`} onClick={() => void act("dc0", () => request("/invites/settings", json("PUT", { invite_direct_contacts: false })), "Only people active in watched groups are invited.")}>Only if active in watched groups</button>
          </div>
          <p className="muted small">{o.settings.invite_direct_contacts ? "Anyone who messages one of your accounts gets one invite, even if they never chatted in a watched group." : "Someone who messages your account is invited only if they also chatted in a watched group recently."}</p>
        </div>
      </div>
      <label className="field">
        <span><b>1 invitation every {gapLabel(sec)}</b> <span className="muted small">(10 sec … 1 hour)</span></span>
        <input type="range" className="ib-range" min={0} max={STEPS.length - 1} step={1} value={idx} aria-label="Time between invitations"
               onChange={(e) => setIdx(Number(e.target.value))} onMouseUp={() => save(STEPS[idx])} onTouchEnd={() => save(STEPS[idx])} onKeyUp={() => save(STEPS[idx])} />
        <span className="ib-scale muted small">{([[10, "10 sec"], [60, "1 min"], [300, "5 min"], [900, "15 min"], [3600, "1 hour"]] as const).map(([v, t]) => (
          <span key={v} style={{ left: `${(STEPS.indexOf(v) / (STEPS.length - 1)) * 100}%` }}>{t}</span>))}</span>
      </label>
      {sec < 60 && <p className="small" style={{ color: "var(--warn)" }}>⚠ A fast pace makes Telegram limits more likely. If Telegram objects, everything stops and the account pauses.</p>}
      <p className="muted small">{perRun > 0 ? `${perRun} invitation${perRun === 1 ? "" : "s"} ready today → about ${duration(perRun * sec)} at this pace.` : "Nothing ready to send right now."} Daily cap: {o.settings.daily_limit} per account.</p>
    </Card>
  );
}

function Send({ o, act, busy, load }: { o: Overview; act: Act; busy: string | null; load: () => void }) {
  const running = o.run.state === "running";
  useEffect(() => { if (!running) return; const t = window.setInterval(load, 3000); return () => window.clearInterval(t); }, [running, load]);
  return (
    <Card title="4 · Send approved invitations">
      <div className="row ib-row">
        <button className="btn" disabled={running || busy === "poll"} onClick={() => void act("poll", () => request("/invites/poll", json("POST")), "Checked groups and private chats for new activity and opt-ins.")}>Check for new opt-ins now</button>
        <button className="btn btn-ghost" disabled={running} onClick={() => void act("dry", () => request("/invites/send", json("POST", { dry_run: true })))}>Dry run</button>
        {!o.settings.auto_send && <button className="btn btn-primary" disabled={running} onClick={() => { if (window.confirm("Approve and send everyone in the queue (within daily limits)?")) void act("send", () => request("/invites/send", json("POST", { dry_run: false }))); }}>Approve all & send</button>}
        {running && <button className="btn" onClick={() => void act("stop", () => request("/invites/stop", json("POST")), "Stopping after the current step.")}>Stop</button>}
      </div>
      <p className="muted small">The monitor checks every 3 minutes{o.settings.monitoring ? "" : " (paused in settings)"}. {o.settings.auto_send ? "Auto is on: new people who asked are messaged by themselves." : "Manual: approve people in the queue below, or Approve all."}</p>
      {o.run.state !== "idle" && (
        <div>
          <p><Badge tone={running ? "warn" : o.run.state === "error" ? "bad" : "good"}>{o.run.dry_run ? "dry run · " : o.run.trigger === "auto" ? "auto · " : ""}{o.run.state}</Badge>
            {o.run.totals && <span className="muted small"> {Object.entries(o.run.totals).map(([k, v]) => `${k.replace("_", " ")}: ${v}`).join(" · ")}</span>}</p>
          {o.run.log && o.run.log.length > 0 && <pre className="small" style={{ maxHeight: 220, overflow: "auto", whiteSpace: "pre-wrap" }}>{o.run.log.join("\n")}</pre>}
        </div>
      )}
    </Card>
  );
}

function Queue({ manual, busy, act }: { manual: boolean; busy: boolean; act: Act }) {
  const [q, setQ] = useState<{ account: string; user_id?: number; target: string; how: string; since: string | null }[] | null>(null);
  useEffect(() => { void request<{ queue: NonNullable<typeof q> }>("/invites/queue").then((r) => setQ(r.queue)).catch(() => setQ([])); }, []);
  return (
    <Card title="Invite queue" actions={<button className="btn btn-sm btn-ghost" onClick={() => void request<{ queue: NonNullable<typeof q> }>("/invites/queue").then((r) => setQ(r.queue))}>Refresh</button>}>
      {!q ? <Loading /> : q.length === 0 ? <p className="muted small">Nobody is waiting. People join the queue by messaging an account or writing the opt-in keyword in an approved group.</p> : (
        <table className="table"><thead><tr><th>Account</th><th>Invite</th><th>Why</th><th>Since</th></tr></thead>
          <tbody>{q.map((x, i) => (
            <tr key={i}><td>{x.account}</td><td>{x.target}</td><td className="muted small">{x.how}</td><td className="muted small">{x.since ? relativeTime(x.since) : "—"}</td>
              <td>{manual && x.user_id ? <>
                <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void act("ap", () => request("/invites/send", json("POST", { user_ids: [x.user_id] })), `Approved: inviting ${x.target}.`)}>Approve</button>{" "}
                <button className="btn btn-sm btn-ghost" onClick={() => void act("sk", async () => setQ((await request<{ queue: NonNullable<typeof q> }>(`/invites/queue/${x.user_id}/skip`, json("POST"))).queue))}>Skip</button></> : !manual && x.user_id ? <span className="muted small">auto</span> : null}</td></tr>))}</tbody></table>
      )}
    </Card>
  );
}

function Members({ days }: { days: number }) {
  const [m, setM] = useState<{ username: string | null; group: string | null; last_active_at: string; opted_in: string; invitation: string | null }[] | null>(null);
  useEffect(() => { void request<{ members: NonNullable<typeof m> }>(`/invites/members?days=${days}`).then((r) => setM(r.members)).catch(() => setM([])); }, [days]);
  return (
    <Card title={`Active members (last ${days} days)`}>
      {!m ? <Loading /> : m.length === 0 ? <p className="muted small">No activity recorded yet in approved groups.</p> : (
        <table className="table"><thead><tr><th>Username</th><th>Group</th><th>Last active</th><th>Asked for invite</th><th>Invitation</th></tr></thead>
          <tbody>{m.map((x, i) => (
            <tr key={i}><td>{x.username ? `@${x.username}` : <span className="muted">(no public username)</span>}</td><td>{x.group ?? "—"}</td>
              <td className="muted small">{relativeTime(x.last_active_at)}</td>
              <td><Badge tone={x.opted_in === "yes" ? "good" : "neutral"}>{x.opted_in}</Badge></td><td>{x.invitation ?? "—"}</td></tr>))}</tbody></table>
      )}
      <p className="muted small">Seeing someone active doesn't mean they get a message: only people who asked are invited.</p>
    </Card>
  );
}

function SettingsCard({ o, act, busy }: { o: Overview; act: Act; busy: string | null }) {
  const [s, setS] = useState(o.settings);
  const f = (k: keyof Overview["settings"], label: string, type: "text" | "number" | "area" = "text") => (
    <label className="field"><span className="small muted">{label}</span>
      {type === "area" ? <textarea className="input" rows={3} value={String(s[k])} onChange={(e) => setS({ ...s, [k]: e.target.value })} />
        : <input className="input" type={type} value={String(s[k])} onChange={(e) => setS({ ...s, [k]: type === "number" ? Number(e.target.value) : e.target.value })} />}
    </label>
  );
  return (
    <Card title="Messages & limits">
      <div className="grid-2">
        {f("invite_link", "Invite link (https://t.me/…)")}
        {f("daily_limit", "Max invitations per account per day (1–20)", "number")}
        {f("dm_message", "Private invitation ({link} = invite link)", "area")}
        {f("group_message", "Group post ({link} = invite link)", "area")}
        {f("opt_in_keywords", "Opt-in keywords (comma-separated)")}
        {f("opt_out_words", "Opt-out replies (comma-separated)")}
        {f("group_post_interval_days", "Days between posts in the same group", "number")}
        {f("active_days", "“Active” = seen in the last N days", "number")}
      </div>
      <label className="small"><input type="checkbox" checked={s.monitoring} onChange={(e) => setS({ ...s, monitoring: e.target.checked })} /> Monitor approved groups and private chats every 3 minutes</label>
      <div className="row ib-row">
        <button className="btn" disabled={busy === "set"} onClick={() => void act("set", () => request("/invites/settings", json("PUT", {
          invite_link: s.invite_link, dm_message: s.dm_message, group_message: s.group_message, daily_limit: s.daily_limit, opt_in_keywords: s.opt_in_keywords,
          opt_out_words: s.opt_out_words, group_post_interval_days: s.group_post_interval_days, active_days: s.active_days, monitoring: s.monitoring })), "Saved.")}>Save settings</button>
      </div>
      {!o.settings.api_id || !o.settings.api_hash_saved ? null : <p className="muted small">Telegram API ID {o.settings.api_id} · hash saved (encrypted).</p>}
    </Card>
  );
}
