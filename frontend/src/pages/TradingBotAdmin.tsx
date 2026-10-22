import { useState } from "react";
import { request } from "../api/client";
import { useApi } from "../api/useApi";
import { Advanced, ApiErrorBanner, Badge, Card, Empty, Loading, PageHeader, Stat, Tabs, relativeTime, type Tone } from "../components/ui";

/** Admin → Trading Bot Access: the funnel, the settings, and the approval queue with evidence and audit history.
 *  Invite links and credentials are never shown here: members get their own link from /trading-bot once approved. */

interface Settings {
  public_enabled: boolean; min_rank_level: number; min_deposit_usd: number; referral_code: string; referral_url: string;
  telegram_group_id: string | null; bot_username: string; bot_wallet: string | null; auto_verify: boolean; manual_approval: boolean; applications_per_day: number; invite_ttl_hours: number; review_rules: string;
}
interface Stats {
  days: number;
  funnel: { key: string; label: string; n: number }[];
  below_rank: number; rank_rate: number | null;
  wallets: { total: number; referral: Record<string, number>; deposit: Record<string, number> };
  applications: Record<string, number>;
  in_group: number;
}
interface App {
  id: string; status: string; created_at: string; member: { username: string; id: string }; rank_level: number; xp: number; wallet: string;
  referral: string; deposit: string; deposit_usd: number; telegram: { id: number | null; username: string | null }; manual_review: boolean;
  notes: string; reviewed_by: string | null; reviewed_at: string | null;
  invite: { created_at: string | null; expires_at: string | null; error: string | null }; joined_at: string | null; left_at: string | null;
  evidence: Record<string, unknown>; audit: { at: string; actor: string; action: string; details: Record<string, unknown> }[];
}

const TONE: Record<string, Tone> = { verified: "good", not_referred: "bad", insufficient: "bad", unverified: "warn", pending: "neutral",
  PENDING_APPROVAL: "warn", APPROVED: "good", REJECTED: "bad", REVOKED: "bad" };
const post = <T,>(path: string, body: unknown = {}) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

function Funnel({ s }: { s: Stats }) {
  const top = Math.max(1, ...s.funnel.map((f) => f.n));
  return (
    <div className="tba-funnel">
      {s.funnel.map((f) => (
        <div key={f.key} className="tba-frow">
          <span className="tba-flabel">{f.label}</span>
          <span className="tba-fbar"><i style={{ width: `${(f.n / top) * 100}%` }} /></span>
          <b>{f.n.toLocaleString()}</b>
        </div>
      ))}
      <p className="muted small">Last {s.days} days. Each step counts distinct people (views: distinct visitors per day). A referral click is only a click; referral and
        deposit counts come from Hyperliquid's own records, never from what a member says.</p>
    </div>
  );
}

function SettingsForm({ initial, onSaved }: { initial: Settings; onSaved: () => void }) {
  const [v, setV] = useState<Settings>(initial);
  const [err, setErr] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const set = <K extends keyof Settings>(k: K, val: Settings[K]) => { setV({ ...v, [k]: val }); setSaved(false); };
  async function save() {
    setErr(null);
    try {
      await request("/tradingbot/admin/settings", { method: "PUT", body: JSON.stringify({ ...v, telegram_group_id: v.telegram_group_id ?? "", bot_wallet: v.bot_wallet ?? "" }) });
      setSaved(true);
      onSaved();
    } catch (e) {
      setErr(e);
    }
  }
  const num = (k: "min_rank_level" | "min_deposit_usd" | "applications_per_day" | "invite_ttl_hours", label: string, hint?: string) => (
    <label className="tba-field"><span>{label}</span><input type="number" value={v[k]} onChange={(e) => set(k, Number(e.target.value))} />{hint && <small className="muted">{hint}</small>}</label>
  );
  const bool = (k: "public_enabled" | "auto_verify" | "manual_approval", label: string, hint: string) => (
    <label className="tba-check"><input type="checkbox" checked={v[k]} onChange={(e) => set(k, e.target.checked)} /><span><b>{label}</b><small className="muted">{hint}</small></span></label>
  );
  return (
    <div className="tba-settings">
      {bool("public_enabled", "Open to the public", "Off: only you (signed in here) can use /trading-bot, and the website tab and Passport card are hidden. Turn on after the launch checklist.")}
      {bool("auto_verify", "Check Hyperliquid automatically", "Read referral and deposit records as soon as a wallet is verified.")}
      {bool("manual_approval", "Every application needs my approval", "Off: applications with everything verified are approved automatically. Anything unverified always waits for you.")}
      <div className="tba-grid">
        {num("min_rank_level", "Minimum Kiwi rank")}
        {num("min_deposit_usd", "Minimum deposit (USD)", "Counted from Hyperliquid deposit records only")}
        <label className="tba-field"><span>Referral codes (comma-separated; first is shown)</span><input value={v.referral_code} onChange={(e) => set("referral_code", e.target.value)} /></label>
        <label className="tba-field"><span>Referral link</span><input value={v.referral_url} onChange={(e) => set("referral_url", e.target.value)} /></label>
        <label className="tba-field"><span>VIP Telegram group (@username or id)</span><input placeholder="@botpricechat" value={v.telegram_group_id ?? ""} onChange={(e) => set("telegram_group_id", e.target.value)} />
          <small className="muted">The Price Chat bot must be an admin there with "Invite users" and "Ban users". A public group must have "Approve new members" on, or anyone can join without approval.</small></label>
        <label className="tba-field"><span>Trading bot (Telegram)</span><input placeholder="cryptopricechatbot" value={v.bot_username} onChange={(e) => set("bot_username", e.target.value)} /></label>
        <label className="tba-field"><span>Bot's Hyperliquid address (public track record)</span><input placeholder="0x…" value={v.bot_wallet ?? ""} onChange={(e) => set("bot_wallet", e.target.value)} />
          <small className="muted">Its PnL is shown on Earn and in every Passport, live from Hyperliquid.</small></label>
        {num("applications_per_day", "Applications per day (everyone)")}
        {num("invite_ttl_hours", "Invite link lifetime (hours)")}
      </div>
      <label className="tba-field"><span>Membership review rules (for reviewers)</span>
        <textarea rows={3} value={v.review_rules} onChange={(e) => set("review_rules", e.target.value)} /></label>
      <div className="btn-group"><button className="btn btn-primary" onClick={save}>Save settings</button>{saved && <Badge tone="good">Saved</Badge>}</div>
      {err ? <ApiErrorBanner error={err} /> : null}
    </div>
  );
}

function AppCard({ a, onChange }: { a: App; onChange: (items: App[]) => void }) {
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  async function go(action: "approve" | "reject" | "revoke" | "recheck") {
    if (action === "revoke" && !confirm(`Revoke ${a.member.username}'s access? Their invite stops working and they're removed from the group.`)) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await post<{ items: App[] }>(`/tradingbot/admin/applications/${a.id}/${action}`, action === "recheck" ? undefined : { notes });
      onChange(r.items);
      setNotes("");
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card title={<span>{a.member.username} <Badge tone={TONE[a.status] ?? "neutral"}>{a.status.replace("_", " ")}</Badge> {a.manual_review && <Badge tone="warn">manual review</Badge>}</span>}
          actions={<span className="muted small">applied {relativeTime(a.created_at)}</span>}>
      <dl className="tba-evidence">
        <div><dt>Kiwi rank at applying</dt><dd>Rank {a.rank_level} · {a.xp.toLocaleString()} XP</dd></div>
        <div><dt>Wallet (signature-verified)</dt><dd><a href={`https://app.hyperliquid.xyz/explorer/address/${a.wallet}`} target="_blank" rel="noopener noreferrer">{a.wallet}</a></dd></div>
        <div><dt>Referral</dt><dd><Badge tone={TONE[a.referral]}>{a.referral}</Badge></dd></div>
        <div><dt>Deposit</dt><dd><Badge tone={TONE[a.deposit]}>{a.deposit}</Badge> ${a.deposit_usd.toLocaleString()} in bridge deposits</dd></div>
        <div><dt>Telegram</dt><dd>{a.telegram.username ? `@${a.telegram.username}` : "—"} · id {a.telegram.id ?? "—"}</dd></div>
        <div><dt>Group</dt><dd>{a.joined_at ? (a.left_at ? `joined, left ${relativeTime(a.left_at)}` : `joined ${relativeTime(a.joined_at)}`) : a.invite.created_at ? "invite issued, not joined yet" : "—"}
          {a.invite.error && <span className="bad small"> · invite error: {a.invite.error}</span>}</dd></div>
      </dl>
      <details><summary>Hyperliquid evidence (as read at the time)</summary><pre className="tba-pre">{JSON.stringify(a.evidence, null, 2)}</pre></details>
      <details><summary>Audit history ({a.audit.length})</summary>
        <ul className="tba-audit">{a.audit.map((x, i) => <li key={i}><span className="muted">{new Date(x.at).toLocaleString()}</span> <b>{x.action}</b> by {x.actor}
          {Object.keys(x.details).length ? <code> {JSON.stringify(x.details)}</code> : null}</li>)}</ul>
      </details>
      {a.notes && <p className="small"><b>Notes:</b> {a.notes}</p>}
      {(a.status === "PENDING_APPROVAL" || a.status === "APPROVED") && (
        <div className="tba-decide">
          <input placeholder={a.status === "PENDING_APPROVAL" ? "Notes (required to reject; start with \"member:\" to show it to them)" : "Reason (for the record)"}
                 value={notes} onChange={(e) => setNotes(e.target.value)} />
          {a.status === "PENDING_APPROVAL" && <button className="btn btn-primary" disabled={busy} onClick={() => go("approve")}>Approve</button>}
          {a.status === "PENDING_APPROVAL" && <button className="btn" disabled={busy} onClick={() => go("reject")}>Reject</button>}
          {a.status === "APPROVED" && <button className="btn btn-kill-sm" disabled={busy} onClick={() => go("revoke")}>Revoke access</button>}
          <button className="btn btn-ghost" disabled={busy} onClick={() => go("recheck")}>Re-check Hyperliquid</button>
        </div>
      )}
      {err ? <ApiErrorBanner error={err} /> : null}
    </Card>
  );
}

const CHECKLIST = [
  "Referral promotion: whether promoting Hyperliquid with referral compensation needs disclosures or registration where your members live (and ad-platform rules).",
  "Derivatives marketing: perpetual futures are restricted or banned for retail in some places (e.g. the US, UK retail rules, the EU/MiCA and ESMA guidance); decide where the page is shown and block or label regions.",
  "Trading signals / bot: whether giving trading signals or running a bot group counts as investment advice or a regulated service where you operate; check the wording of everything said in the group.",
  "Private access: the membership terms (who can join, review rules, revocation, no guarantees) and how it's described.",
  "Hyperliquid's own terms: their referral program rules and restricted jurisdictions, and that nothing implies endorsement.",
  "Privacy: the policy covers wallet addresses, Telegram ids and the funnel analytics stored here.",
  "Then: turn on \"Open to the public\" above.",
];

export function TradingBotAdminPage() {
  const ov = useApi(() => request<{ stats: Stats; settings: Settings }>("/tradingbot/admin/overview"));
  const [tab, setTab] = useState<"PENDING_APPROVAL" | "APPROVED" | "REJECTED" | "REVOKED" | "all">("PENDING_APPROVAL");
  const apps = useApi(() => request<{ items: App[] }>(`/tradingbot/admin/applications${tab === "all" ? "" : `?status=${tab}`}`));
  const [list, setList] = useState<App[] | null>(null);
  const items = (list ?? apps.data?.items ?? []).filter((a) => tab === "all" || a.status === tab);
  const s = ov.data?.stats;
  return (
    <div className="stack">
      <PageHeader title="Trading bot access" subtitle="Who asked to join the VIP trading bot, and the rules to get in." />
      {ov.failure ? <ApiErrorBanner error={ov.failure} /> : null}
      {ov.loading && !ov.data ? <Loading /> : null}
      {s && ov.data && (
        <>
          {!ov.data.settings.public_enabled && <p className="banner banner-warn">The bot is closed to the public right now. Read the launch checklist at the bottom before opening it.</p>}
          <div className="stats">
            <Stat label="Waiting for you" value={s.applications.PENDING_APPROVAL ?? 0} tone={(s.applications.PENDING_APPROVAL ?? 0) ? "warn" : "neutral"} />
            <Stat label="Approved" value={s.applications.APPROVED ?? 0} tone="good" />
            <Stat label="In the Telegram group" value={s.in_group} />
          </div>
        </>
      )}

      <Card title="Requests to join">
        <Tabs tabs={[{ key: "PENDING_APPROVAL", label: "Waiting" }, { key: "APPROVED", label: "Approved" }, { key: "REJECTED", label: "Rejected", more: true },
                     { key: "REVOKED", label: "Removed", more: true }, { key: "all", label: "All", more: true }]} value={tab}
              onChange={(k) => { setTab(k); setList(null); }} />
        {apps.loading && !apps.data ? <Loading /> : items.length ? items.map((a) => <AppCard key={a.id} a={a} onChange={(x) => { setList(x); void ov.reload(); }} />)
          : <Empty>Nothing here.</Empty>}
      </Card>

      {ov.data && <Card title="Rules to get in"><SettingsForm initial={ov.data.settings} onSaved={() => void ov.reload()} /></Card>}

      {s && (
        <Advanced label="Where people drop off, and wallet checks">
          <div className="stats">
            <Stat label="Rejected" value={s.applications.REJECTED ?? 0} />
            <Stat label="Wallets checked" value={s.wallets.total} />
            <Stat label="Rank too low" value={s.below_rank} hint={s.rank_rate != null ? `${Math.round(s.rank_rate * 100)}% of signed-in visitors qualify` : undefined} />
          </div>
          <div className="grid-2">
            <Card title="Step by step (last 30 days)"><Funnel s={s} /></Card>
            <Card title="Hyperliquid checks">
              <p className="small"><b>Referral:</b> {Object.entries(s.wallets.referral).map(([k, n]) => <Badge key={k} tone={TONE[k]}>{k} {n}</Badge>)}</p>
              <p className="small"><b>Deposit:</b> {Object.entries(s.wallets.deposit).map(([k, n]) => <Badge key={k} tone={TONE[k]}>{k} {n}</Badge>)}</p>
              <p className="muted small">"unverified" means Hyperliquid couldn't confirm it (an outage, or money sent as a transfer instead of a deposit). Those
                requests come to you to decide and are never approved automatically.</p>
            </Card>
          </div>
        </Advanced>
      )}

      <Advanced label="Launch checklist: legal questions before opening to the public">
        <ol className="tba-checklist">{CHECKLIST.map((c) => <li key={c}>{c}</li>)}</ol>
        <p className="muted small">This is a list of things to get advice on, not legal advice. Have someone qualified in the jurisdictions you serve review it.</p>
      </Advanced>
    </div>
  );
}
