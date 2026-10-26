import { useCallback, useEffect, useRef, useState } from "react";
import { SitePage } from "./community";
import { pageHead } from "./seo";
import { memberApi } from "./members";
import { openLogin } from "./emailLogin";
import { EarnTabs } from "./arbitrage";
import { BotBrain } from "./kalshiBrain";
import "./tradingbot.css";
import "./arbitrage.css";
import "./kalshi.css";

/* Earn › Kalshi (/earn/kalshi): a member links their own Kalshi API key and watches their account live. A member's link is
   READ-ONLY: the server only sends Kalshi GET requests with it; nothing here can place, change or cancel their orders.
   Above it, BotBrain shows the owner's own bot (it trades the owner's account from the owner's PC, never members'). */

type Link = { env: "production" | "demo"; key_id: string; key_type: string; connected_at: string; last_ok_at: string | null; last_error: string | null };
type FeeRow = { P: number; taker_1_cents: number; need_hold_cents: number; fee_pct_of_price: number };
type Watch = { received_at: string; markets: number | null; events: number | null; two_sided: number | null; with_volume_24h: number | null;
  categories: Record<string, number>; fee_curve: FeeRow[]; fee_gate_min_cents: number | null } | null;
export type KalshiState = { open: boolean; owner: boolean; available: boolean; signed_in?: boolean; link?: Link | null; watch: Watch };
export type Position = { ticker: string; side: "yes" | "no"; count: number; cost_usd: number; title?: string; subtitle?: string; category?: string | null;
  close_time?: string | null; bid?: number; yes_bid?: number; yes_ask?: number; value_now_usd?: number; exit_fee_usd?: number | null;
  net_if_sold_usd?: number | null; pnl_if_sold_usd?: number | null; pays_if_right_usd?: number; fee_model?: string; error?: string };
export type Account = { env: string; as_of: string; balance_usd: number; portfolio_value_usd: number; positions: Position[]; positions_total: number;
  totals: { cost_usd: number; net_if_sold_usd: number; pnl_if_sold_usd: number; unpriced: number };
  orders: { ticker: string; side: string; action: string; type: string; price: number; remaining: number; created_time: string | null }[];
  fills: { ticker: string; side: string; action: string; count: number; price: number; is_taker: boolean | null; created_time: string | null }[];
  settlements: { ticker: string; result: string; settled_time: string | null; yes_count: number; no_count: number; cost_usd: number; fee_usd: number; revenue_usd: number }[];
  fee_coefficient: number };

const POLL_MS = 10_000;
const usd = (n: number | null | undefined, sign = false) => n == null ? "—"
  : `${sign && n > 0 ? "+" : n < 0 ? "−" : ""}$${Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const cents = (p: number | undefined) => p == null ? "—" : `${Math.round(p * 100)}¢`;
const tone = (n: number | null | undefined) => n == null || n === 0 ? "" : n > 0 ? "is-good" : "is-bad";
const when = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  return s < 60 ? `${Math.round(s)}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : s < 86400 ? `${Math.round(s / 3600)}h ago` : new Date(iso).toLocaleDateString();
};
const errText = (e: unknown) => (e instanceof Error ? e.message : "Something went wrong.");

function Connect({ onDone }: { onDone: () => void }) {
  const [env, setEnv] = useState<"production" | "demo">("production");
  const [keyId, setKeyId] = useState("");
  const [pem, setPem] = useState("");
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function readFile(f: File | undefined) {
    if (f) setPem((await f.text()).trim());
  }
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      await memberApi("/members/kalshi/connect", { method: "POST", body: JSON.stringify({ env, key_id: keyId.trim(), private_key: pem, confirm: ok }) });
      setPem("");
      onDone();
    } catch (x) {
      setErr(errText(x));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="arb-card arb-start" aria-labelledby="ks-connect">
      <h2 id="ks-connect">Connect your Kalshi account</h2>
      <ol className="arb-steps">
        <li className="arb-step">
          <h3><span className="arb-step-n">1</span>Make an API key on Kalshi</h3>
          <p>On Kalshi open <a href="https://kalshi.com/account/profile" target="_blank" rel="noreferrer">Account → API keys</a> and create a key.
            Kalshi shows a <b>Key ID</b> and downloads a <b>private key</b> file once. Keep that file.</p>
        </li>
        <li className="arb-step">
          <h3><span className="arb-step-n">2</span>Paste it here</h3>
          <form className="ks-form" onSubmit={(e) => void submit(e)}>
            <div className="ks-env" role="radiogroup" aria-label="Account">
              {(["production", "demo"] as const).map((k) => (
                <label key={k} className={env === k ? "is-on" : ""}>
                  <input type="radio" name="ks-env" checked={env === k} onChange={() => setEnv(k)} />
                  {k === "production" ? "Real money" : "Demo"}
                </label>
              ))}
            </div>
            <label>Key ID<input value={keyId} onChange={(e) => setKeyId(e.target.value)} placeholder="0a472aed-cf27-4666-…" autoComplete="off" spellCheck={false} required /></label>
            <label>Private key
              <textarea value={pem} onChange={(e) => setPem(e.target.value)} rows={5} spellCheck={false} autoComplete="off" required
                        placeholder={"-----BEGIN PRIVATE KEY-----\n…\n-----END PRIVATE KEY-----"} />
            </label>
            <label className="ks-file">or pick the file Kalshi downloaded<input type="file" accept=".txt,.pem,.key,text/plain" onChange={(e) => void readFile(e.target.files?.[0])} /></label>
            <label className="ks-check"><input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} />
              <span>It's my own Kalshi account and Kalshi lets me use it where I live.</span></label>
            <button className="tb-btn" type="submit" disabled={busy || !ok || !keyId || !pem}>{busy ? "Checking with Kalshi…" : "Connect"}</button>
            {err && <p className="site-error" role="alert">{err}</p>}
          </form>
        </li>
      </ol>
      <ul className="arb-rules ks-rules">
        <li>We check the key with Kalshi before keeping it, then store it encrypted, and use it to show your balance and positions.</li>
        <li>The bot trades its own account, never yours. Disconnect any time; deleting the key on Kalshi also cuts us off.</li>
        <li>Prediction markets can lose your whole stake. This is a view of your account, not advice.</li>
      </ul>
    </section>
  );
}

function usePolling<T>(path: string | null, ms: number): { data: T | null; err: string | null; at: number; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [at, setAt] = useState(0);
  const tick = useRef(0);
  const load = useCallback((fresh = false) => {
    if (!path) return;
    const n = ++tick.current;
    memberApi<T>(`${path}${fresh ? "?fresh=true" : ""}`).then((d) => { if (n === tick.current) { setData(d); setErr(null); setAt(Date.now()); } })
      .catch((e: unknown) => { if (n === tick.current) setErr(errText(e)); });
  }, [path]);
  useEffect(() => {
    if (!path) return;
    load();
    const id = window.setInterval(() => { if (document.visibilityState !== "hidden") load(); }, ms);
    const back = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", back);
    return () => { window.clearInterval(id); document.removeEventListener("visibilitychange", back); };
  }, [path, ms, load]);
  return { data, err, at, reload: () => load(true) };
}

/** Seconds since the last good refresh, ticking each second. */
function Age({ at }: { at: number }) {
  const [, setNow] = useState(0);
  useEffect(() => { const id = window.setInterval(() => setNow((n) => n + 1), 1000); return () => window.clearInterval(id); }, []);
  return <>{at ? `${Math.max(0, Math.round((Date.now() - at) / 1000))}s ago` : "…"}</>;
}

export function LiveAccount({ link, onDisconnect }: { link: Link; onDisconnect: () => void }) {
  const { data: a, err, at, reload } = usePolling<Account>("/members/kalshi/account", POLL_MS);
  const [busy, setBusy] = useState(false);
  async function disconnect() {
    if (!window.confirm("Disconnect Kalshi? We delete the key we stored. You can also delete it on Kalshi.")) return;
    setBusy(true);
    try { await memberApi("/members/kalshi", { method: "DELETE" }); onDisconnect(); } finally { setBusy(false); }
  }
  return (
    <section className="ks-live" aria-label="Your Kalshi account">
      <div className="ks-bar">
        <span className={`ks-dot ${err ? "is-bad" : ""}`} aria-hidden />
        <span><b>{link.env === "production" ? "Real money" : "Demo"}</b> · key {link.key_id} · {err ? "not updating" : <>live, updated <Age at={at} /></>}</span>
        <span className="ks-bar-actions">
          <button className="arb-linkbtn" onClick={reload}>Refresh</button>
          <button className="arb-linkbtn" onClick={() => void disconnect()} disabled={busy}>Disconnect</button>
        </span>
      </div>
      {err && <p className="site-error" role="alert">{err}</p>}
      {!a ? (!err && <div className="tb-skel" aria-busy="true" />) : <>
        <div className="arb-stats">
          <div className="arb-stat"><span>Cash</span><b>{usd(a.balance_usd)}</b><small>available on Kalshi</small></div>
          <div className="arb-stat"><span>Positions</span><b>{a.positions_total}</b><small>cost {usd(a.totals.cost_usd)}</small></div>
          <div className="arb-stat"><span>If you sold now</span><b>{usd(a.totals.net_if_sold_usd)}</b><small>at the bid, after Kalshi's fee</small></div>
          <div className={`arb-stat ${tone(a.totals.pnl_if_sold_usd)}`}><span>Profit if sold now</span><b>{usd(a.totals.pnl_if_sold_usd, true)}</b>
            <small>{a.totals.unpriced ? `${a.totals.unpriced} position(s) without a known fee left out` : "vs what you paid"}</small></div>
        </div>
        <section className="arb-card">
          <h2>Open positions</h2>
          {a.positions.length === 0 ? <p className="arb-muted">No open positions right now.</p> : (
            <table className="arb-table ks-table">
              <thead><tr><th>Market</th><th>Side</th><th>Contracts</th><th>Bid now</th><th>Paid</th><th>Sell now (after fee)</th><th>Profit if sold</th><th>Pays if right</th></tr></thead>
              <tbody>{a.positions.map((p) => (
                <tr key={p.ticker}>
                  <td className="ks-market"><b>{p.title ?? p.ticker}</b>{p.subtitle ? <small>{p.subtitle}</small> : null}
                    <small>{p.category ? `${p.category} · ` : ""}{p.close_time ? `closes ${new Date(p.close_time).toLocaleDateString()}` : p.ticker}</small></td>
                  <td><span className={`ks-side is-${p.side}`}>{p.side.toUpperCase()}</span></td>
                  <td>{p.count.toLocaleString()}</td>
                  <td>{cents(p.bid)}</td>
                  <td>{usd(p.cost_usd)}</td>
                  <td>{p.error ? <span className="arb-muted">{p.error}</span> : p.net_if_sold_usd == null
                    ? <span className="arb-muted" title={`Kalshi fee model "${p.fee_model}" isn't one we can price`}>fee unknown</span>
                    : <>{usd(p.net_if_sold_usd)} <small className="arb-muted">fee {usd(p.exit_fee_usd)}</small></>}</td>
                  <td className={tone(p.pnl_if_sold_usd)}>{usd(p.pnl_if_sold_usd, true)}</td>
                  <td>{usd(p.pays_if_right_usd)}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
          {a.positions_total > a.positions.length && <p className="arb-fine">Showing {a.positions.length} of {a.positions_total}.</p>}
        </section>
        <div className="arb-cols">
          <section className="arb-card">
            <h2>Resting orders</h2>
            {a.orders.length === 0 ? <p className="arb-muted">None.</p> : (
              <table className="arb-table"><thead><tr><th>Market</th><th>Order</th><th>Price</th><th>Left</th></tr></thead>
                <tbody>{a.orders.map((o, i) => <tr key={i}><td>{o.ticker}</td><td>{o.action} {o.side.toUpperCase()}</td><td>{cents(o.price)}</td><td>{o.remaining}</td></tr>)}</tbody></table>
            )}
            <h2 className="ks-h2">Recent fills</h2>
            {a.fills.length === 0 ? <p className="arb-muted">None yet.</p> : (
              <table className="arb-table"><thead><tr><th>When</th><th>Market</th><th>Trade</th><th>Price</th></tr></thead>
                <tbody>{a.fills.map((f, i) => <tr key={i}><td>{when(f.created_time)}</td><td>{f.ticker}</td><td>{f.action} {f.count} {f.side.toUpperCase()}</td><td>{cents(f.price)}</td></tr>)}</tbody></table>
            )}
          </section>
          <section className="arb-card">
            <h2>Settled</h2>
            {a.settlements.length === 0 ? <p className="arb-muted">Nothing settled yet.</p> : (
              <table className="arb-table"><thead><tr><th>Market</th><th>Result</th><th>Held</th><th>Cost</th><th>Fees</th><th>Paid out</th></tr></thead>
                <tbody>{a.settlements.map((s, i) => (
                  <tr key={i}><td>{s.ticker}<small className="ks-sub">{when(s.settled_time)}</small></td><td>{s.result.toUpperCase()}</td>
                    <td>{[s.yes_count ? `${s.yes_count} YES` : "", s.no_count ? `${s.no_count} NO` : ""].filter(Boolean).join(" · ") || "—"}</td>
                    <td>{usd(s.cost_usd)}</td><td>{usd(s.fee_usd)}</td><td>{usd(s.revenue_usd)}</td></tr>
                ))}</tbody></table>
            )}
            <p className="arb-fine">Kalshi's own numbers. "Paid out" is what settlement credited; contracts you closed before settlement were paid when you sold.</p>
          </section>
        </div>
        <p className="arb-fine">Fees use each market's own Kalshi fee model ({a.fee_coefficient} × P × (1 − P) per contract, rounded up). Updates every
          {" "}{POLL_MS / 1000}s while this tab is open.</p>
      </>}
    </section>
  );
}

function WatchBoard({ w }: { w: NonNullable<Watch> }) {
  const cats = Object.entries(w.categories);
  const max = Math.max(1, ...cats.map(([, n]) => n));
  const curve = w.fee_curve.filter((r) => [0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95].some((p) => Math.abs(r.P - p) < 1e-9));
  return (
    <div className="arb-cols">
      <section className="arb-card">
        <h2>What Kiwi's Kalshi bot watches</h2>
        <p className="arb-muted">Last sweep {when(w.received_at)}: <b>{(w.markets ?? 0).toLocaleString()}</b> open markets, {(w.two_sided ?? 0).toLocaleString()} with
          prices on both sides, {(w.with_volume_24h ?? 0).toLocaleString()} traded in the last day.</p>
        <div className="ks-cats">{cats.map(([k, n]) => (
          <div key={k}><span>{k}</span><i style={{ width: `${Math.max(2, (n / max) * 100)}%` }} /><b>{n.toLocaleString()}</b></div>
        ))}</div>
      </section>
      {curve.length > 0 && <section className="arb-card">
        <h2>What Kalshi's fee costs</h2>
        <table className="arb-table"><thead><tr><th>Price</th><th>Fee per contract</th><th>Fee / price</th><th>Edge needed</th></tr></thead>
          <tbody>{curve.map((r) => <tr key={r.P}><td>{Math.round(r.P * 100)}¢</td><td>{r.taker_1_cents.toFixed(2)}¢</td><td>{r.fee_pct_of_price.toFixed(1)}%</td>
            <td>{r.need_hold_cents.toFixed(2)}¢</td></tr>)}</tbody></table>
        <p className="arb-fine">Cheap long shots pay the most fee for their price. The bot skips any idea that doesn't clear the fee plus a {w.fee_gate_min_cents ?? 1.5}¢ margin.</p>
      </section>}
    </div>
  );
}

export function KalshiPage() {
  return (
    <SitePage {...pageHead("earn/kalshi")}>
      <div className="tb arb">
        <EarnTabs current="kalshi" />
        <KalshiSection />
      </div>
    </SitePage>
  );
}

/** The tab's content without the site shell (tested on its own). */
export function KalshiSection() {
  const [s, setS] = useState<KalshiState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => {
    memberApi<KalshiState>("/members/kalshi").then((d) => { setS(d); setErr(null); }).catch((e: unknown) => setErr(errText(e)));
  }, []);
  useEffect(load, [load]);
  return (
    <section className="arb-bot" aria-label="Kalshi">
      <header className="arb-head">
        <span className="arb-big" aria-hidden>🎯</span>
        <div>
          <h1>Kalshi</h1>
          <p>Kiwi's Kalshi bot, trading live: watch it scan every market, weigh each bet against Kalshi's fees and buy when the numbers pay.
            Link your own Kalshi account to follow your positions next to it, valued at the bid after Kalshi's fee.</p>
          {s?.owner && !s.open && <p className="arb-stale">Owner preview: members can't see this tab until you open it in Admin › Kalshi bot.</p>}
        </div>
      </header>
      {err && <p className="site-error" role="alert">{err}</p>}
      <BotBrain />
      {!s ? (!err && <div className="tb-skel" aria-busy="true" />)
        : !s.available ? <section className="arb-card"><h2>Coming soon</h2><p>Kalshi on Earn opens to members soon.</p></section>
        : !s.signed_in ? <section className="arb-card"><h2>Sign in first</h2><p>Your Kalshi link belongs to your Kiwi Passport.</p>
            <button className="tb-btn" onClick={openLogin}>Sign in</button></section>
        : s.link ? <LiveAccount link={s.link} onDisconnect={load} />
        : <Connect onDone={load} />}
      {s?.watch && <WatchBoard w={s.watch} />}
    </section>
  );
}
