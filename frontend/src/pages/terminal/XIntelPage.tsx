import { useState } from "react";
import { request } from "../../api/client";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Loading, PageHeader, relativeTime, type Tone } from "../../components/ui";
import { StoryRow, type Story } from "./classic";
import "./terminal.css";

/** Admin › Price Chat Terminal › X Intelligence (owner, 2026-11-18): narratives from watched X accounts with credibility tiers, through
 *  the official X API. Reading other accounts is paid on X's pay-per-use plan, so it stays off until the owner confirms the price. */

type Watch = { handle: string; group: string; tier: "A" | "B" | "C"; note: string; on?: boolean; posts_24h?: number; last_post?: string | null };
type LedgerRow = { title: string; x_first: string; other_first: string | null; x_led: boolean; lead_min: number | null; accounts: string[];
  verified_at: string | null; rumor: boolean; claim: string | null; published?: boolean; rejected?: boolean };
type Pilot = { started: string | null; spend_usd: number; posts_read: number; unique_posts: number; duplicates_returned: number; stories: number; x_led: number;
  before_other_sources: number; lead_min: { median: number | null; max: number | null; n: number }; verified: number; useful_discoveries: number;
  verified_published: number; false_positives: number; cost_per_useful: number | null; budget_check: string | null; days: Record<string, Record<string, number>>;
  recent: LedgerRow[] };
type Cap = { status: string; detail: string; checked_at?: string };
type Access = { configured: boolean; app_only: boolean; user_context: boolean; status: string; account: string | null; capabilities: Record<string, Cap>; tested: boolean;
  last_error: string | null; usage_today: Record<string, number>; usage_month: Record<string, number>; budget: { daily_usd: number; monthly_usd: number }; plan: string;
  prices: Record<string, number>; pricing_source: string; search_ok: boolean };
type XInfo = { posts: number; independent: number; independent_accounts: string[]; copies: number; coordinated: boolean; tiers: Record<string, number>;
  velocity: { per_min: number; between_reads: number; basis: string }; links: { handle: string; tier: string; url: string; text: string; at: string | null; engagement: number }[] };
type Narrative = Story & { x: XInfo };
type Estimate = { accounts: number; queries: number; posts_per_day: number; usd_per_day: number; usd_per_month: number; assumed_posts_per_account_day: number; note: string };
export type XOverview = { access: Access; enabled: boolean; daily_usd: number; max_posts: number; spent_today: number; spent_month: number;
  last_read: { at: string; posts: number; requests: number; usd: number; problem: string | null } | null; estimate: Estimate; groups: Record<string, string>;
  tiers: Record<string, string>; watchlist: Watch[]; emerging: { term: string; kind: string; accounts: number; ratio: number }[]; narratives: Narrative[]; posts_24h: number;
  health: { status: string; problem: string | null; last_scan?: string } | null; pilot?: Pilot; paused?: { at: string; why: string } | null; protect?: string | null };

const CAP_TONE: Record<string, Tone> = { available: "good", unavailable: "bad", not_configured: "neutral", no_credit: "bad", unknown: "warn" };
const TIER_TONE: Record<string, Tone> = { A: "good", B: "info", C: "neutral" };
const usd = (n: number | undefined) => `$${(n ?? 0).toFixed(2)}`;

export function XIntelPage() {
  const { data, error, loading, reload } = useApi(() => request<XOverview>("/terminal/x"), 60_000);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<number | null>(null);

  async function send(path: string, method: string, body: unknown, done: string) {
    setBusy(true); setMsg(null);
    try { await request(path, { method, body: body === undefined ? undefined : JSON.stringify(body) }); setMsg({ ok: true, text: done }); await reload(); }
    catch (e) { setMsg({ ok: false, text: String((e as Error).message) }); }
    finally { setBusy(false); }
  }

  if (loading && !data) return <Loading />;
  if (!data) return <ApiErrorBanner error={error} />;
  const a = data.access;
  const e = data.estimate;
  function turnOn() {
    const ok = window.confirm(`Turn on X Intelligence? This reads the watchlist through X's paid API: $${a.prices.post_read} per post and $${a.prices.user_read} per account a day.\n\n`
      + `Estimate for ${e.accounts} accounts: about ${e.posts_per_day} posts a day = ${usd(e.usd_per_day)}/day, ${usd(e.usd_per_month)}/month (${e.note}).\n`
      + `Stops at ${usd(data!.daily_usd)} a day here, and at the account's X budget (${usd(a.budget.daily_usd)}/day, ${usd(a.budget.monthly_usd)}/month).`);
    if (ok) void send("/terminal/x/settings", "PUT", { on: true, confirm_paid: true }, "X Intelligence is on.");
  }
  return (
    <div className="pt">
      <PageHeader title="X INTELLIGENCE" subtitle="Narratives from watched X accounts, scored for reach and for evidence separately. Official X API only: no scraping, no estimated engagement." />
      {msg && <div className={`banner ${msg.ok ? "banner-info" : "banner-error"}`} role="status">{msg.text}</div>}
      <div className="pt-status">
        <Badge tone={data.enabled ? "good" : "neutral"}>{data.enabled ? "Reading X: ON" : "Reading X: OFF (paid)"}</Badge>
        <Badge tone={a.configured ? (a.status === "connected" ? "good" : "warn") : "bad"}>{a.configured ? `X ${a.status}${a.account ? ` as @${a.account}` : ""}` : "X not connected"}</Badge>
        <span className="small muted">today {usd(data.spent_today)} of {usd(data.daily_usd)} · this month {usd(data.spent_month)} · {data.posts_24h} posts in 24 h</span>
        <span className="pt-grow" />
        {data.enabled ? <>
          <button className="btn btn-sm" disabled={busy} onClick={() => void send("/terminal/x/read", "POST", undefined, "Read the watchlist.")}>Read now</button>
          <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => void send("/terminal/x/settings", "PUT", { on: false }, "X Intelligence is off.")}>Turn off</button>
        </> : <button className="btn btn-sm btn-primary" disabled={busy || !a.configured} onClick={turnOn}>Turn on (paid)…</button>}
      </div>
      <div className="pt-grid">
        <div className="pt-main">
          <Card title={`Live narratives (${data.narratives.length})`}>
            {data.narratives.length === 0 ? <Empty>{data.enabled ? "No narrative from the watchlist in 24 h yet." : "Nothing read from X: X Intelligence is off."}</Empty>
              : data.narratives.map((n) => (
                <div key={n.id}>
                  <StoryRow s={n} open={open === n.id} onToggle={() => setOpen(open === n.id ? null : n.id)} onChanged={() => void reload()} />
                  <div className="pt-xline small">
                    <Badge tone={n.x.independent >= 3 ? "good" : n.x.independent >= 2 ? "info" : "neutral"}>{n.x.independent} independent</Badge>
                    {n.x.copies > 0 && <Badge tone="neutral">{n.x.copies} copies</Badge>}
                    {n.x.coordinated && <Badge tone="bad">coordinated amplification</Badge>}
                    {Object.entries(n.x.tiers).map(([t, c]) => <Badge key={t} tone={TIER_TONE[t] ?? "neutral"}>tier {t}: {c}</Badge>)}
                    <span className="muted">velocity {n.x.velocity.per_min}/min ({n.x.velocity.between_reads ? "between reads" : "since posting"}; {n.x.velocity.basis})</span>
                    <div className="pt-xposts">{n.x.links.slice(0, 5).map((l) => (
                      <a key={l.url} className="link" href={l.url} target="_blank" rel="noreferrer">@{l.handle} [{l.tier}]: {l.text.slice(0, 90)}</a>))}</div>
                  </div>
                </div>))}
          </Card>
        </div>
        <aside className="pt-side">
          <Card title="Emerging on X (30 min)">
            {data.emerging.length === 0 ? <Empty>No term jumped across several watched accounts.</Empty> : (
              <div className="pt-parts">{data.emerging.map((t) => <span key={t.term} className="pt-chip" title={`${t.accounts} accounts, ${t.ratio}x the 6 h rate`}>{t.term} · {t.accounts}</span>)}</div>)}
          </Card>
          {data.pilot && <PilotCard p={data.pilot} paused={data.paused ?? null} protect={data.protect ?? null} />}
          <AccessCard data={data} />
          <Watchlist data={data} busy={busy} save={(accounts) => void send("/terminal/x/watchlist", "PUT", { accounts }, "Watchlist saved.")} />
        </aside>
      </div>
    </div>
  );
}

function AccessCard({ data }: { data: XOverview }) {
  const a = data.access;
  const e = data.estimate;
  return (
    <Card title="X access (audit)">
      <p className="small">Plan: <strong>{a.plan}</strong>. {a.pricing_source}. Post read ${a.prices.post_read}, account read ${a.prices.user_read}, own posts ${a.prices.owned_read}, trends ${a.prices.trends_request}/request.</p>
      <p className="small">Auth: {a.app_only ? "app (Bearer)" : "no Bearer"} · {a.user_context ? "account keys" : "no account keys"} · account budget {usd(a.budget.daily_usd)}/day, {usd(a.budget.monthly_usd)}/month · X spend this month {usd(a.usage_month.cost)} ({Math.round(a.usage_month.posts ?? 0)} posts read)</p>
      {!a.tested ? <p className="small warn-text">The connection was never tested here: X › Connection › Test connection shows exactly what this plan can read.</p> : (
        <ul className="pt-list">{Object.entries(a.capabilities).map(([k, c]) => (
          <li key={k}><Badge tone={CAP_TONE[c.status] ?? "neutral"}>{c.status}</Badge> {k.replace(/_/g, " ")} <span className="small muted">{c.detail}</span></li>))}</ul>)}
      {a.last_error && <p className="small bad-text">{a.last_error}</p>}
      <p className="small">Watchlist estimate: {e.accounts} accounts in {e.queries} requests a read, ~{e.posts_per_day} posts a day ({e.assumed_posts_per_account_day} per account) = <strong>{usd(e.usd_per_day)}/day, {usd(e.usd_per_month)}/month</strong>. {e.note}.</p>
      {data.last_read && <p className="small muted">Last read {relativeTime(data.last_read.at)}: {data.last_read.posts} posts, {data.last_read.requests} requests, {usd(data.last_read.usd)}{data.last_read.problem ? ` · ${data.last_read.problem}` : ""}</p>}
    </Card>
  );
}

function Watchlist({ data, busy, save }: { data: XOverview; busy: boolean; save: (rows: Watch[]) => void }) {
  const strip = (w: Watch[]) => w.map(({ handle, group, tier, note, on }) => ({ handle, group, tier, note, on: on !== false }));
  const [rows, setRows] = useState<Watch[]>(strip(data.watchlist));
  const [handle, setHandle] = useState("");
  const [group, setGroup] = useState("breaking");
  const [tier, setTier] = useState<"A" | "B" | "C">("C");
  const stats = Object.fromEntries(data.watchlist.map((w) => [w.handle.toLowerCase(), w]));
  const dirty = JSON.stringify(rows) !== JSON.stringify(strip(data.watchlist));
  const set = (i: number, patch: Partial<Watch>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <Card title={`Watchlist (${rows.length})`}>
      <p className="small muted">{rows.filter((r) => r.on !== false).length} of {rows.length} switched on (only those are read and paid for). Tiers: A = {data.tiers.A}. B = {data.tiers.B}. C = {data.tiers.C}. Follower counts are never used as proof.</p>
      {Object.entries(data.groups).map(([g, label]) => {
        const mine = rows.map((r, i) => ({ r, i })).filter(({ r }) => r.group === g);
        return (
          <div key={g} className="pt-watch">
            <h4>{label} ({mine.length})</h4>
            {mine.map(({ r, i }) => (
              <div key={r.handle} className="pt-watch-row">
                <input type="checkbox" aria-label={`Read ${r.handle}`} checked={r.on !== false} onChange={(e) => set(i, { on: e.target.checked })} />
                <a className={`link${r.on === false ? " muted" : ""}`} href={`https://x.com/${r.handle}`} target="_blank" rel="noreferrer">@{r.handle}</a>
                <select className="input" aria-label={`Tier of ${r.handle}`} value={r.tier} onChange={(e) => set(i, { tier: e.target.value as Watch["tier"] })}>
                  {["A", "B", "C"].map((t) => <option key={t}>{t}</option>)}</select>
                <span className="small muted">{stats[r.handle.toLowerCase()]?.posts_24h ?? 0} in 24 h</span>
                <button className="btn btn-sm" aria-label={`Remove ${r.handle}`} onClick={() => setRows(rows.filter((_, j) => j !== i))}>×</button>
              </div>))}
          </div>);
      })}
      <div className="pt-watch-row">
        <input className="input" placeholder="@handle" value={handle} onChange={(e) => setHandle(e.target.value.trim())} aria-label="New handle" />
        <select className="input" value={group} onChange={(e) => setGroup(e.target.value)} aria-label="Group">{Object.entries(data.groups).map(([g, l]) => <option key={g} value={g}>{l}</option>)}</select>
        <select className="input" value={tier} onChange={(e) => setTier(e.target.value as Watch["tier"])} aria-label="Tier">{["A", "B", "C"].map((t) => <option key={t}>{t}</option>)}</select>
        <button className="btn btn-sm" disabled={!/^@?\w{1,15}$/.test(handle)} onClick={() => { setRows([...rows, { handle: handle.replace(/^@/, ""), group, tier, note: "", on: true }]); setHandle(""); }}>Add</button>
      </div>
      <button className="btn btn-sm btn-primary" disabled={busy || !dirty} onClick={() => save(rows)}>Save watchlist</button>
    </Card>
  );
}

function PilotCard({ p, paused, protect }: { p: Pilot; paused: { at: string; why: string } | null; protect: string | null }) {
  const days = Object.entries(p.days).slice(-3);
  return (
    <Card title="Pilot results">
      {p.started ? <p className="small muted">Since {p.started.slice(0, 16).replace("T", " ")} UTC. {p.budget_check}</p>
        : <p className="small warn-text">Not started: {p.budget_check ?? "waiting for the first scan"}</p>}
      {(paused || protect) && <p className="small warn-text">Paid reads paused to protect X posting: {paused?.why ?? protect}</p>}
      <table className="table small"><tbody>
        <tr><td>X spend (pilot)</td><td><strong>{usd(p.spend_usd)}</strong></td></tr>
        <tr><td>Posts read / unique / duplicates</td><td>{p.posts_read} / {p.unique_posts} / {p.duplicates_returned}</td></tr>
        <tr><td>Stories X touched / X had first</td><td>{p.stories} / {p.x_led}</td></tr>
        <tr><td>Before any other source</td><td>{p.before_other_sources}{p.lead_min.median !== null ? ` (median lead ${p.lead_min.median} min, max ${p.lead_min.max})` : ""}</td></tr>
        <tr><td>Verified / verified and published</td><td>{p.verified} / {p.verified_published}</td></tr>
        <tr><td>Useful discoveries (X first, then verified)</td><td><strong>{p.useful_discoveries}</strong></td></tr>
        <tr><td>False positives (X first, unverified after 6 h or rejected)</td><td>{p.false_positives}</td></tr>
        <tr><td>Cost per useful discovery</td><td><strong>{p.cost_per_useful !== null ? usd(p.cost_per_useful) : "none yet"}</strong></td></tr>
      </tbody></table>
      {days.length > 0 && <p className="small muted">{days.map(([d, v]) => `${d.slice(5)}: ${usd(v.usd)}, ${v.new ?? 0} new posts`).join(" · ")}</p>}
      {p.recent.length > 0 && <ul className="pt-list">{p.recent.slice(0, 6).map((e, i) => (
        <li key={i}><Badge tone={e.verified_at ? "good" : e.rumor ? "warn" : "neutral"}>{e.verified_at ? "verified" : e.claim ?? "open"}</Badge> {e.title}
          <div className="small muted">X {e.x_first.slice(11, 16)}Z · {e.other_first ? `others ${e.other_first.slice(11, 16)}Z${e.lead_min !== null ? ` (+${e.lead_min} min)` : ""}` : "X only so far"} · {e.accounts.join(", ")}</div></li>))}</ul>}
    </Card>
  );
}
