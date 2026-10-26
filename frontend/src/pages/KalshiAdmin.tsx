import { useState } from "react";
import { request } from "../api/client";
import { useApi } from "../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, ErrorBanner, Loading, PageHeader, Stat, relativeTime } from "../components/ui";

/* Kalshi bot (OWNER ONLY): the prediction-market bot that runs on the owner's PC (C:\AI\kalshi-bot). It keeps its Kalshi key
   there and pushes read-only reports here with its own token; nothing on this page can make it trade. */

type FeeRow = { P: number; taker_1_cents: number; taker_per_contract_at_10_cents: number; maker_per_contract_at_10_cents: number;
  brief_formula_cents: number; fee_pct_of_price: number; need_hold_cents: number; need_round_trip_cents: number };
type Phase = { id: string; name: string; state: "done" | "next" | "todo" };
type KalshiStatus = {
  received_at: string; env: string; base_url: string; balance_usd: number; portfolio_value_usd: number;
  health: { signing: string; exchange_active: boolean; trading_active: boolean; rate_limits: { tier: string } };
  positions: { open: number; exposure_usd: number; fees_paid_usd: number; realized_pnl_usd: number; markets: { ticker: string; position: number; exposure_usd: number }[] };
  store: { last_quote_ts: string | null; last_quote_age_s: number | null; counts: Record<string, number> } | null;
  kill_switch: { execution_armed: boolean; order_code: string };
  fee_gate_min_cents: number; fee_curve: FeeRow[]; fee_settings: { coefficient: number; balance_precision: number }; phases: Phase[];
};
type Sample = { kind: string; market_id: string; title: string; category: string; yes_bid: number; yes_ask: number; volume_24h: number };
type KalshiDiscovery = {
  received_at: string; run_id: string; markets: number; events: number; series: number; two_sided: number; with_volume_24h: number;
  quote_warnings: number; unknown_fee_model: number; threshold_ladder_events: number; elapsed_s: number; calls: number; rate_limited: number;
  quotes_written: number; categories: Record<string, number>; fee_models?: Record<string, number>; samples?: Sample[];
};
type HistoryRow = { received_at: string; markets: number; two_sided: number; with_volume_24h: number; elapsed_s: number; quote_warnings: number; rate_limited: number };
export type KalshiOverview = {
  connected: boolean; token_created_at: string | null; last_seen_at: string | null; stale: boolean;
  bot: { version: string; env: string; host: string } | null; status: KalshiStatus | null; discovery: KalshiDiscovery | null;
  history: HistoryRow[]; reports: number;
  members?: { open: boolean; linked: number; production: number; erroring: number };
};

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const usd = (n: number | undefined | null) => `$${(n ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const int = (n: number | undefined | null) => (n ?? 0).toLocaleString();
const RUN = "cd C:\\AI\\kalshi-bot\n$env:KALSHI_ENV=\"production\"; .venv\\Scripts\\python -m pmbot discover --every 15 --report";

function Connect({ connected, onDone }: { connected: boolean; onDone: () => void }) {
  const [token, setToken] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function make() {
    if (connected && !window.confirm("Make a new token? The bot stops reporting until you put the new one in its .env.")) return;
    setBusy(true); setErr(null);
    try {
      setToken((await request<{ token: string }>("/kalshi/admin/token", { method: "POST" })).token);
      onDone();
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  }
  async function disconnect() {
    if (!window.confirm("Disconnect the bot? Its reports will be refused until you make a new token.")) return;
    await request("/kalshi/admin/token", { method: "DELETE" });
    setToken(null);
    onDone();
  }
  return (
    <Card title={connected ? "Connection" : "Connect the bot"}
          actions={connected ? <button className="btn btn-ghost" onClick={() => void disconnect()}>Disconnect</button> : null}>
      {!connected && !token && <p className="muted">The bot runs on your PC and sends this page what it sees. It keeps the Kalshi key on your PC; this page
        only gets a token that lets it report.</p>}
      {token ? (
        <div className="stack">
          <p><b>Copy this token now:</b> it is shown once. Add it to <span className="mono">C:\AI\kalshi-bot\.env</span>:</p>
          <pre className="mono" style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>PRICECHAT_KALSHI_TOKEN={token}</pre>
          <p>Then start the bot (it sweeps every 15 minutes and reports here):</p>
          <pre className="mono" style={{ whiteSpace: "pre-wrap" }}>{RUN}</pre>
        </div>
      ) : (
        <button className="btn btn-primary" onClick={() => void make()} disabled={busy}>{connected ? "Make a new token" : "Create bot token"}</button>
      )}
      <ErrorBanner message={err} />
    </Card>
  );
}

function Members({ m, onDone }: { m: NonNullable<KalshiOverview["members"]>; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function flip() {
    if (!m.open && !window.confirm("Open Earn › Kalshi to every member? They'll be able to link their own Kalshi keys (read-only).")) return;
    setBusy(true); setErr(null);
    try {
      await request("/kalshi/admin/members", { method: "POST", body: JSON.stringify({ open: !m.open }) });
      onDone();
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card title="Earn › Kalshi (members)"
          actions={<button className={`btn ${m.open ? "btn-ghost" : "btn-primary"}`} onClick={() => void flip()} disabled={busy}>{m.open ? "Close to members" : "Open to members"}</button>}>
      <div className="stats">
        <Stat label="Tab" value={m.open ? "Open to all" : "Owner only"} tone={m.open ? "good" : "neutral"} hint="pricechat.live/earn/kalshi" />
        <Stat label="Linked accounts" value={m.linked} hint={`${m.production} real money`} />
        <Stat label="Failing reads" value={m.erroring} tone={m.erroring ? "warn" : "good"} hint="key deleted on Kalshi, or a location block" />
      </div>
      <p className="muted small">Members paste their own Kalshi API key; it's checked with Kalshi, stored encrypted and only used for read-only GETs.
        Nothing on Price Chat can place a Kalshi order. While the tab is owner-only you can still link your own account there to try it.</p>
      <ErrorBanner message={err} />
    </Card>
  );
}

function Phases({ phases }: { phases: Phase[] }) {
  const tone = { done: "good", next: "info", todo: "neutral" } as const;
  return (
    <Card title="Build progress">
      <ol style={{ listStyle: "none", padding: 0, display: "grid", gap: 6 }}>
        {phases.map((p) => <li key={p.id}><Badge tone={tone[p.state]}>{p.state === "done" ? "Done" : p.state === "next" ? "Next" : "Later"}</Badge>{" "}
          Phase {p.id}: {p.name}</li>)}
      </ol>
      <p className="muted small">Nothing trades until Phase 7, and then only with both LIVE_TRADING and CONFIRM_TOKEN set on the PC.</p>
    </Card>
  );
}

function FeeCurve({ st }: { st: KalshiStatus }) {
  return (
    <Card title="Fee curve (standard series)">
      <div className="table-wrap"><table className="table">
        <thead><tr><th>Price</th><th>Fee, 1 contract</th><th>Per contract in a 10-lot</th><th>Maker</th><th>Fee / price</th><th>Edge needed (hold)</th><th>Edge needed (in + out)</th></tr></thead>
        <tbody>{st.fee_curve.map((r) => (
          <tr key={r.P}><td>{Math.round(r.P * 100)}¢</td><td>{r.taker_1_cents.toFixed(2)}¢</td><td>{r.taker_per_contract_at_10_cents.toFixed(2)}¢</td>
            <td>{r.maker_per_contract_at_10_cents.toFixed(3)}¢</td><td>{r.fee_pct_of_price.toFixed(2)}%</td>
            <td>{r.need_hold_cents.toFixed(2)}¢</td><td>{r.need_round_trip_cents.toFixed(2)}¢</td></tr>
        ))}</tbody>
      </table></div>
      <p className="muted small">Kalshi fee = {st.fee_settings.coefficient} × P × (1 − P) per contract, rounded per order. Every idea must clear the fee
        plus the {st.fee_gate_min_cents}¢ safety gate, or the bot rejects it. Cheapest at the extremes: a 95¢ contract loses ~0.35% to fees, a 5¢ one ~6.7%.</p>
    </Card>
  );
}

function Discovery({ d, history }: { d: KalshiDiscovery; history: HistoryRow[] }) {
  const cats = Object.entries(d.categories);
  const max = Math.max(1, ...cats.map(([, n]) => n));
  return (
    <Card title={`Markets (last sweep ${relativeTime(d.received_at)})`}>
      <div className="stats">
        <Stat label="Open markets" value={int(d.markets)} hint={`${int(d.events)} events · ${int(d.series)} series`} />
        <Stat label="Quoted both sides" value={int(d.two_sided)} />
        <Stat label="Traded in 24h" value={int(d.with_volume_24h)} />
        <Stat label="Price-check warnings" value={int(d.quote_warnings)} tone={d.quote_warnings ? "warn" : "good"} hint="YES vs NO disagree by over 1¢" />
        <Stat label="Unknown fee model" value={int(d.unknown_fee_model)} tone={d.unknown_fee_model ? "warn" : "good"} hint="can't be traded" />
        <Stat label="Sweep time" value={`${Math.round(d.elapsed_s)} s`} hint={`${d.calls} calls · ${d.rate_limited} rate-limited`} tone={d.rate_limited ? "warn" : "neutral"} />
      </div>
      <h3>By category</h3>
      <div className="stack" style={{ gap: 4 }}>
        {cats.map(([k, n]) => (
          <div key={k} style={{ display: "grid", gridTemplateColumns: "minmax(120px, 200px) 1fr 70px", gap: 8, alignItems: "center" }}>
            <span className="small">{k}</span>
            <span style={{ background: "var(--info)", height: 8, borderRadius: 4, width: `${Math.max(1, (n / max) * 100)}%` }} />
            <span className="small mono" style={{ textAlign: "right" }}>{int(n)}</span>
          </div>
        ))}
      </div>
      {d.samples?.length ? <>
        <h3>Examples</h3>
        <ul style={{ listStyle: "none", padding: 0, display: "grid", gap: 6 }}>{d.samples.map((s) => (
          <li key={s.market_id}><Badge>{s.kind.replace("_", " ")}</Badge> {s.title} <span className="muted small">({s.category} · bid {Math.round(s.yes_bid * 100)}¢ /
            ask {Math.round(s.yes_ask * 100)}¢ · {int(s.volume_24h)} traded 24h)</span></li>
        ))}</ul>
      </> : null}
      {history.length > 1 && <>
        <h3>Recent sweeps</h3>
        <div className="table-wrap"><table className="table">
          <thead><tr><th>When</th><th>Markets</th><th>Two-sided</th><th>Traded 24h</th><th>Time</th><th>Warnings</th></tr></thead>
          <tbody>{history.slice(-10).reverse().map((h) => (
            <tr key={h.received_at}><td>{relativeTime(h.received_at)}</td><td>{int(h.markets)}</td><td>{int(h.two_sided)}</td><td>{int(h.with_volume_24h)}</td>
              <td>{Math.round(h.elapsed_s)} s</td><td>{h.quote_warnings}</td></tr>
          ))}</tbody>
        </table></div>
      </>}
    </Card>
  );
}

export function KalshiAdminPage() {
  const r = useApi(() => request<KalshiOverview>("/kalshi/admin/overview"), 60_000);
  const d = r.data;
  const st = d?.status;
  return (
    <div className="stack">
      <PageHeader title="Kalshi bot" subtitle="Your prediction-market bot on your PC: balance, fees and the markets it watches. Read-only: it can't place orders."
        actions={<button className="btn" onClick={() => void r.reload()} disabled={r.loading}>Refresh</button>} />
      {r.failure ? <ApiErrorBanner error={r.failure} /> : null}
      {r.loading && !d ? <Loading /> : null}
      {d && <>
        {d.connected && st && (
          <div className="stats">
            <Stat label="Account" value={st.env === "production" ? "Real money" : "Demo"} tone={st.env === "production" ? "warn" : "info"} hint={st.base_url} />
            <Stat label="Balance" value={usd(st.balance_usd)} hint={`portfolio ${usd(st.portfolio_value_usd)}`} />
            <Stat label="Open positions" value={st.positions.open} hint={`exposure ${usd(st.positions.exposure_usd)}`} />
            <Stat label="Last report" value={relativeTime(d.last_seen_at)} tone={d.stale ? "bad" : "good"}
                  hint={d.stale ? "the bot hasn't reported for 45+ min: is the PC on?" : `${d.bot?.host ?? ""} · v ${d.bot?.version ?? "?"}`} />
            <Stat label="Trading" value={st.kill_switch.execution_armed ? "ARMED" : "Off"} tone={st.kill_switch.execution_armed ? "bad" : "good"}
                  hint={st.kill_switch.order_code} />
          </div>
        )}
        {d.connected && !st && <Empty>Connected. Waiting for the bot's first report: start it on your PC (see Connection below).</Empty>}
        {st && <Phases phases={st.phases} />}
        {d.discovery && <Discovery d={d.discovery} history={d.history} />}
        {st && <FeeCurve st={st} />}
        {d.members && <Members m={d.members} onDone={() => void r.reload()} />}
        <Connect connected={d.connected} onDone={() => void r.reload()} />
      </>}
    </div>
  );
}
