import { useRef, useState } from "react";
import { request } from "../api/client";
import { useApi } from "../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Loading, PageHeader, Stat, relativeTime } from "../components/ui";

/* Hyperliquid affiliate wallet (OWNER ONLY): what Hyperliquid reports for our referral code, each referred account (live
   balance) and the Kiwi Passport member it belongs to. Growth charts come from hourly snapshots we store ourselves. */

type Mem = { id: number; username: string; display_name: string; rank_level: number; xp: number; status: string; joined_at: string | null;
  telegram: string | null; x: string | null; google: boolean; bot: { mode: string | null; auto: boolean; open: number; closed: number; net: number } };
type Acc = { perps_value?: number; spot_usdc?: number | null; positions?: { coin: string; side: string; value: number; upnl: number }[]; notional?: number; error?: string };
type Ref = { address: string; joined_at: string | null; volume: number; fees_paid: number; rewarded_to_us: number; member: Mem | null; match: string | null; account: Acc | null };
type Report = {
  at: string; source: string;
  master: { wallet: string; stage: string; code: string; n_referrals: number; referred_by: { referrer: string; code: string } | null; own_volume: number;
    unclaimed: number; claimed: number; builder_rewards: number; reward_history: { at: string | null; amount: number }[] };
  totals: { referrals: number; matched: number; volume: number; fees_paid: number; rewarded_to_us: number; new_7d: number; new_30d: number; active: number;
    balances: number | null; ref_clicks_signed_in: number; ref_clicks_anonymous: number };
  referrals: Ref[];
  not_listed: { address: string; via: string; member: Mem; our_check: string | null; code_seen: string | null; checked_at: string | null }[];
  clicked_no_wallet: { member: Mem; clicks: number; last_click: string | null }[];
  history: { points: { at: string; n_referrals: number; volume: number; rewarded_to_us: number; unclaimed: number }[]; volume_change: Record<string, { "24h"?: number; "7d"?: number }>; since: string | null };
};

type Win = { "24h": number; "7d": number; "30d": number };
type Clicks = { placements: ({ placement: string } & Win)[]; totals: Win; sources_30d: Record<string, number> };
const SPOT: Record<string, string> = { card: "Signal card", footer: "Reply footer", start: "/start", help: "/help", other: "Other" };
const spot = (p: string) => SPOT[p] ?? (p.startsWith("site_") ? `Website · ${p.slice(5).replace(/_/g, "-")}` : p);

/* Tracked referral-link clicks per placement (/api/r/{placement}): which spot sends people to Hyperliquid. No IP / user agent kept. */
function ClicksCard() {
  const c = useApi(() => request<Clicks>("/tradingbot/admin/referral-clicks"), 120_000);
  const d = c.data;
  return (
    <Card title="Referral clicks by placement">
      {c.failure ? <ApiErrorBanner error={c.failure} /> : null}
      {!d ? (c.loading ? <Loading /> : null) : !d.placements.length ? <Empty>No tracked clicks yet.</Empty> : (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Placement</th><th>24h</th><th>7d</th><th>30d</th></tr></thead>
          <tbody>{d.placements.map((r) => <tr key={r.placement}><td>{spot(r.placement)}</td><td>{r["24h"]}</td><td>{r["7d"]}</td><td>{r["30d"]}</td></tr>)}
            <tr><td><b>All</b></td><td><b>{d.totals["24h"]}</b></td><td><b>{d.totals["7d"]}</b></td><td><b>{d.totals["30d"]}</b></td></tr></tbody></table></div>)}
      {d && Object.keys(d.sources_30d).length > 0 && <p className="muted small">30d by source: {Object.entries(d.sources_30d).map(([k, v]) => `${k} ${v}`).join(" · ")}</p>}
      <p className="muted small">Counted when someone opens a tracked link (bots and link previews skipped). A click isn't a sign-up: compare with new referred accounts.</p>
    </Card>
  );
}

const usd = (x: number | null | undefined, d = 2) => (x == null ? "—" : `$${x.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`);
const short = (a: string) => `${a.slice(0, 6)}…${a.slice(-4)}`;
const explorer = (a: string) => `https://app.hyperliquid.xyz/explorer/address/${a}`;

function Who({ m }: { m: Mem | null }) {
  if (!m) return <span className="muted">not matched</span>;
  return (
    <span><b>{m.display_name || m.username}</b> <span className="muted">@{m.username} · #{m.id} · rank {m.rank_level}</span>
      {m.telegram && <span className="muted"> · tg @{m.telegram}</span>}{m.x && <span className="muted"> · x @{m.x}</span>}
      {m.status !== "active" && <Badge tone="bad">{m.status}</Badge>}</span>
  );
}

function Spark({ pts, label }: { pts: number[]; label: string }) {
  if (pts.length < 2) return <Empty>Building up: one snapshot is stored every hour.</Empty>;
  const W = 520, H = 90, lo = Math.min(...pts), hi = Math.max(...pts);
  const y = (v: number) => H - 4 - ((v - lo) / (hi - lo || 1)) * (H - 8);
  return (
    <figure style={{ margin: 0 }}><figcaption className="muted small">{label}: {pts[0].toLocaleString()} → {pts[pts.length - 1].toLocaleString()}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: 90 }} role="img" aria-label={label}>
        <polyline fill="none" stroke="#00b373" strokeWidth={2} points={pts.map((v, i) => `${(i / (pts.length - 1)) * W},${y(v)}`).join(" ")} />
      </svg></figure>
  );
}

export function AffiliateAdminPage() {
  const force = useRef(false);
  const r = useApi(() => request<Report>(`/tradingbot/admin/affiliate${force.current ? "?refresh=true" : ""}`).finally(() => { force.current = false; }), 120_000);
  const refreshNow = () => { force.current = true; void r.reload(); };
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState("");
  const d = r.data;
  const rows = (d?.referrals ?? []).filter((x) => !q || x.address.includes(q.toLowerCase()) || (x.member && `${x.member.username} ${x.member.display_name} ${x.member.telegram ?? ""}`.toLowerCase().includes(q.toLowerCase())));
  async function snap() {
    setBusy(true);
    try { await request("/tradingbot/admin/affiliate/snapshot", { method: "POST" }); refreshNow(); } finally { setBusy(false); }
  }
  return (
    <div className="stack">
      <PageHeader title="Hyperliquid Affiliate" subtitle="Our referral wallet on Hyperliquid, every referred account, and which Kiwi Passport member it is. Owner only."
        actions={<><button className="btn" onClick={refreshNow} disabled={r.loading}>Refresh from Hyperliquid</button>
          <button className="btn" onClick={() => void snap()} disabled={busy}>Save snapshot now</button></>} />
      {r.failure ? <ApiErrorBanner error={r.failure} /> : null}
      {r.loading && !d ? <Loading /> : null}
      {d && <>
        <div className="stats">
          <Stat label="Referred accounts" value={d.totals.referrals} hint={`${d.totals.new_7d} new in 7 days · ${d.totals.new_30d} in 30`} />
          <Stat label="Matched to Passport" value={`${d.totals.matched} / ${d.totals.referrals}`} tone={d.totals.matched < d.totals.referrals ? "warn" : "good"} />
          <Stat label="Referred volume" value={usd(d.totals.volume, 0)} hint="cumulative, since each joined" />
          <Stat label="Fees they paid" value={usd(d.totals.fees_paid, 4)} />
          <Stat label="Rewarded to us" value={usd(d.totals.rewarded_to_us, 4)} tone="good" />
          <Stat label="Unclaimed rewards" value={usd(d.master.unclaimed, 4)} hint={`claimed ${usd(d.master.claimed, 4)}`} tone={d.master.unclaimed > 0 ? "info" : "neutral"} />
          <Stat label="Balances of referred accounts" value={usd(d.totals.balances)} hint={`${d.totals.active} with open positions`} />
          <Stat label="Referral-link clicks" value={d.totals.ref_clicks_signed_in + d.totals.ref_clicks_anonymous} hint={`${d.totals.ref_clicks_signed_in} signed in · ${d.totals.ref_clicks_anonymous} anonymous`} />
        </div>
        <ClicksCard />
        <div className="grid-2">
          <Card title="Referral wallet">
            <p className="small"><b>Code {d.master.code}</b> <Badge tone={d.master.stage === "ready" ? "good" : "warn"}>{d.master.stage}</Badge></p>
            <p className="small mono"><a href={explorer(d.master.wallet)} target="_blank" rel="noreferrer">{d.master.wallet}</a></p>
            <p className="small">Own trading volume {usd(d.master.own_volume, 0)} · builder rewards {usd(d.master.builder_rewards, 4)}</p>
            {d.master.referred_by && <p className="small muted">This wallet was itself referred by {short(d.master.referred_by.referrer)} (code {d.master.referred_by.code}).</p>}
            <p className="small muted">Read {relativeTime(d.at)} from {d.source}. Rewards are claimed on Hyperliquid by the wallet owner; this page never signs or sends anything.</p>
            {d.master.reward_history.length > 0 && <p className="small">Reward history: {d.master.reward_history.map((h) => `${h.at?.slice(0, 10)} ${usd(h.amount, 4)}`).join(" · ")}</p>}
          </Card>
          <Card title="Growth (hourly snapshots)">
            {d.history.since && <p className="muted small">Recorded since {new Date(d.history.since).toLocaleString()}. Earlier history doesn't exist: Hyperliquid only gives today's totals.</p>}
            <Spark pts={d.history.points.map((p) => p.n_referrals)} label="Referred accounts" />
            <Spark pts={d.history.points.map((p) => p.volume)} label="Referred volume (USD)" />
            <Spark pts={d.history.points.map((p) => p.rewarded_to_us)} label="Rewarded to us (USD)" />
          </Card>
        </div>
        <Card title={`Referred accounts (${d.referrals.length})`} actions={<input className="input" placeholder="Search address or member" value={q} onChange={(e) => setQ(e.target.value)} />}>
          {!rows.length ? <Empty>No referred accounts{q ? " match" : " yet"}.</Empty> : (
            <div className="table-wrap"><table className="table">
              <thead><tr><th>Account</th><th>Joined</th><th>Passport member</th><th>Volume</th><th>24h / 7d</th><th>Fees paid</th><th>To us</th><th>Balance</th><th>Positions</th><th>Bot</th></tr></thead>
              <tbody>{rows.map((x) => { const a = x.account ?? {}; const ch = d.history.volume_change[x.address] ?? {}; return (
                <tr key={x.address}>
                  <td className="mono"><a href={explorer(x.address)} target="_blank" rel="noreferrer">{short(x.address)}</a></td>
                  <td>{x.joined_at ? new Date(x.joined_at).toLocaleDateString() : "—"}</td>
                  <td><Who m={x.member} />{x.match && <> <Badge tone={x.match === "wallet" ? "good" : "info"} title={x.match === "wallet" ? "the member signed a message with this wallet" : "the member's trading account (from their API key)"}>{x.match === "wallet" ? "verified wallet" : "trading account"}</Badge></>}</td>
                  <td>{usd(x.volume, 0)}</td>
                  <td className="small">{ch["24h"] != null ? usd(ch["24h"], 0) : "—"} / {ch["7d"] != null ? usd(ch["7d"], 0) : "—"}</td>
                  <td>{usd(x.fees_paid, 4)}</td><td>{usd(x.rewarded_to_us, 4)}</td>
                  <td>{a.error ? <span className="muted">{a.error}</span> : <>{usd((a.perps_value ?? 0) + (a.spot_usdc ?? 0))}<br /><span className="muted small">perps {usd(a.perps_value)} · spot {usd(a.spot_usdc)}</span></>}</td>
                  <td className="small">{a.positions?.length ? a.positions.map((p) => `${p.coin} ${p.side} ${usd(p.value, 0)}`).join(", ") : "—"}</td>
                  <td className="small">{x.member?.bot.mode ? `${x.member.bot.mode}${x.member.bot.auto ? " · AUTO" : ""} · ${x.member.bot.open} open · ${x.member.bot.closed} closed (${usd(x.member.bot.net)})` : "—"}</td>
                </tr>); })}</tbody></table></div>)}
          <p className="muted small">Matching: a verified Passport wallet (signed message) first, then the trading account behind a member's API key. Unmatched accounts signed
            up with our code but never connected that wallet to a Passport.</p>
        </Card>
        <div className="grid-2">
          <Card title={`Passport wallets NOT under our code (${d.not_listed.length})`}>
            {!d.not_listed.length ? <Empty>Every known Passport wallet is a referral.</Empty> : <div className="table-wrap"><table className="table">
              <thead><tr><th>Member</th><th>Wallet</th><th>Our check</th></tr></thead>
              <tbody>{d.not_listed.map((x) => <tr key={x.address}><td><Who m={x.member} /></td>
                <td className="mono"><a href={explorer(x.address)} target="_blank" rel="noreferrer">{short(x.address)}</a> <span className="muted small">({x.via})</span></td>
                <td>{x.our_check ? <Badge tone={x.our_check === "verified" ? "good" : x.our_check === "not_referred" ? "bad" : "warn"}>{x.our_check}</Badge> : "—"}
                  {x.code_seen && <span className="muted small"> code {x.code_seen}</span>}</td></tr>)}</tbody></table></div>}
            <p className="muted small">Members whose Hyperliquid account Hyperliquid doesn't list under BANTER: they joined without the link, used another code, or traded from a different wallet.</p>
          </Card>
          <Card title={`Clicked the referral link, no wallet yet (${d.clicked_no_wallet.length})`}>
            {!d.clicked_no_wallet.length ? <Empty>None.</Empty> : <div className="table-wrap"><table className="table">
              <thead><tr><th>Member</th><th>Clicks</th><th>Last click</th></tr></thead>
              <tbody>{d.clicked_no_wallet.map((x) => <tr key={x.member.id}><td><Who m={x.member} /></td><td>{x.clicks}</td><td>{x.last_click ? relativeTime(x.last_click) : "—"}</td></tr>)}</tbody></table></div>}
            <p className="muted small">They opened Hyperliquid from Earn while signed in; we can't see whether they signed up until they verify a wallet.</p>
          </Card>
        </div>
      </>}
    </div>
  );
}
