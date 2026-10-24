import { useEffect, useMemo, useState } from "react";
import { discover, walletError, SUGGESTED, type Discovered } from "../pages/pokemon/marketplace/wallet";
import type { FundingBoard } from "./arbitrage";

/* Earn › Funding Arb: the board's average APR, and a guided start (connect a wallet, size a pair, open both legs yourself on
   the venues). Price Chat never places these orders: the wallet is connected read-only (its public address, no signature),
   and the only account data read is Hyperliquid's public record for that address. */

type Row = FundingBoard["rows"][number];
export const VENUE_NAME: Record<string, string> = { hyperliquid: "Hyperliquid", lighter: "Lighter", orderly: "Orderly", backpack: "Backpack" };
/** Estimated base-tier taker fees, bps (the same assumptions as the funding alerts: Orderly / Backpack only tell a signed-in account). */
export const TAKER_BPS: Record<string, number> = { hyperliquid: 4.5, lighter: 2, orderly: 5, backpack: 5 };
/** How to get an account on each venue (all take USDC as margin). */
const ONBOARD: Record<string, { url: string; how: string }> = {
  hyperliquid: { url: "https://app.hyperliquid.xyz/", how: "Connect an EVM wallet, deposit USDC (Arbitrum)." },
  lighter: { url: "https://app.lighter.xyz/", how: "Connect an EVM wallet, deposit USDC." },
  orderly: { url: "https://pro.woofi.com/", how: "Orderly has no app of its own: trade it through WOOFi Pro with an EVM wallet." },
  backpack: { url: "https://backpack.exchange/", how: "A Backpack Exchange account (email sign-up and ID check), then deposit USDC." },
};
const base = (sym: string) => sym.replace(/-PERP$/, "");

/** The venue's own trade page for the perp. Thousand-unit contracts: Hyperliquid and Backpack write 1000PEPE as kPEPE. */
export function tradeUrl(venue: string, sym: string): string {
  const b = base(sym);
  const k = b.startsWith("1000") && b.length > 4 ? `k${b.slice(4)}` : b;
  switch (venue) {
    case "hyperliquid": return `https://app.hyperliquid.xyz/trade/${encodeURIComponent(k)}`;
    case "lighter": return `https://app.lighter.xyz/trade/${encodeURIComponent(b)}`;
    case "backpack": return `https://backpack.exchange/trade/${encodeURIComponent(k)}_USDC_PERP`;
    case "orderly": return `https://pro.woofi.com/en/trade/PERP_${encodeURIComponent(b)}_USDC`;
    default: return "#";
  }
}

const pct = (v: number | null | undefined, d = 1) => (v == null || !Number.isFinite(v) ? "—" : `${v.toFixed(d)}%`);
const usd = (v: number, d = 2) => `$${v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d })}`;
const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
const TOP_N = 10;

/** The rows that have a 7-day record (the board already lists them steadiest first). */
const scored = (rows: Row[]) => rows.filter((r) => r.stats?.mean_apr_pct != null);

/** Average APR across the board's steadiest gaps: the 7-day average, today's gap, and on the capital both legs tie up. */
export function FundingAverages({ d }: { d: FundingBoard }) {
  const top = scored(d.rows).slice(0, TOP_N);
  const rows = top.length ? top : d.rows.slice(0, TOP_N);
  const avg7 = mean(top.map((r) => r.stats!.mean_apr_pct!));
  const now = mean(rows.map((r) => r.gross_spread_apr_pct));
  const perCap = d.assumptions?.capital_per_notional;
  const best = top[0];
  return (
    <section className="arb-avg" aria-label="Average APR">
      <div className="arb-avg-tile is-main">
        <span>Average APR, 7 days</span>
        <b>{avg7 == null ? "—" : pct(avg7)}</b>
        <small>{top.length ? `the ${top.length} steadiest gaps, before fees` : "building the 7-day record…"}</small>
      </div>
      <div className="arb-avg-tile">
        <span>Average gap now</span>
        <b>{pct(now)}</b>
        <small>same {rows.length} pairs, this interval</small>
      </div>
      <div className="arb-avg-tile">
        <span>On your capital</span>
        <b>{avg7 == null || !perCap ? "—" : pct(avg7 / perCap)}</b>
        <small>{d.assumptions ? `both legs at ${d.assumptions.leverage}x + ${Math.round(d.assumptions.buffer * 100)}% buffer` : "—"}</small>
      </div>
      <div className="arb-avg-tile">
        <span>Steadiest pair</span>
        <b>{best ? base(best.symbol) : "—"}</b>
        <small>{best ? `${pct(best.stats!.mean_apr_pct)} avg · paid ${Math.round((best.stats!.paid_share ?? 0) * 100)}% of hours` : "—"}</small>
      </div>
    </section>
  );
}

interface HlState { accountValue: number; positions: { coin: string; szi: number; ntl: number }[]; funding30d: number }

/** Hyperliquid's public record for an address: account value, open perps, funding received over 30 days. */
async function readHyperliquid(address: string): Promise<HlState> {
  const post = (body: unknown) => fetch("https://api.hyperliquid.xyz/info", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
    .then((r) => { if (!r.ok) throw new Error(`Hyperliquid answered ${r.status}`); return r.json(); });
  const [st, fund] = await Promise.all([
    post({ type: "clearinghouseState", user: address }),
    post({ type: "userFunding", user: address, startTime: Date.now() - 30 * 86400_000 }),
  ]) as [{ marginSummary?: { accountValue?: string }; assetPositions?: { position: { coin: string; szi: string; positionValue: string } }[] },
         { delta?: { usdc?: string } }[]];
  return {
    accountValue: Number(st.marginSummary?.accountValue ?? 0),
    positions: (st.assetPositions ?? []).map((p) => ({ coin: p.position.coin, szi: Number(p.position.szi), ntl: Number(p.position.positionValue) })),
    funding30d: (fund ?? []).reduce((a, f) => a + Number(f.delta?.usdc ?? 0), 0),
  };
}

const short = (a: string) => `${a.slice(0, 6)}…${a.slice(-4)}`;
const ADDR_KEY = "pc.fundarb.wallet";
const loadAddr = () => { try { return window.localStorage.getItem(ADDR_KEY); } catch { return null; } };
const saveAddr = (a: string | null) => { try { if (a) window.localStorage.setItem(ADDR_KEY, a); else window.localStorage.removeItem(ADDR_KEY); } catch { /* storage blocked */ } };

function WalletStep() {
  const [addr, setAddr] = useState<string | null>(loadAddr);
  const [wallets, setWallets] = useState<Discovered[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [hl, setHl] = useState<HlState | null>(null);
  useEffect(() => { void discover().then(setWallets); }, []);
  useEffect(() => {
    setHl(null);
    if (!addr) return;
    let alive = true;
    readHyperliquid(addr).then((s) => { if (alive) setHl(s); }).catch(() => { if (alive) setMsg("Couldn't read Hyperliquid for this address right now."); });
    return () => { alive = false; };
  }, [addr]);
  const connect = async (w: Discovered) => {
    setBusy(true);
    setMsg(null);
    try {
      const accounts = (await w.provider.request({ method: "eth_requestAccounts" })) as string[];
      if (!accounts?.[0]) throw new Error("The wallet didn't share an address.");
      setAddr(accounts[0]);
      saveAddr(accounts[0]);
    } catch (e) {
      setMsg(walletError(e).replace("Nothing was linked.", "Nothing was connected."));
    } finally {
      setBusy(false);
    }
  };
  const forget = () => { setAddr(null); saveAddr(null); setMsg(null); };
  return (
    <li className="arb-step">
      <h3><span className="arb-step-n">1</span> Connect your wallet</h3>
      <p className="arb-muted">Read-only: the page sees your public address and nothing else. No signature, no approval, no keys.</p>
      {addr ? (
        <div className="arb-wallet">
          <p><b>{short(addr)}</b> connected <button type="button" className="arb-linkbtn" onClick={forget}>Disconnect</button></p>
          {hl ? (
            <dl className="arb-kv">
              <div><dt>Hyperliquid account</dt><dd>{usd(hl.accountValue)}</dd></div>
              <div><dt>Open perps</dt><dd>{hl.positions.length ? hl.positions.map((p) => `${p.szi < 0 ? "short" : "long"} ${p.coin}`).join(", ") : "none"}</dd></div>
              <div><dt>Funding, last 30 days</dt><dd className={hl.funding30d >= 0 ? "is-good" : "is-bad"}>{hl.funding30d >= 0 ? "+" : "−"}{usd(Math.abs(hl.funding30d))}</dd></div>
            </dl>
          ) : !msg && <p className="arb-muted">Reading Hyperliquid…</p>}
        </div>
      ) : wallets == null ? <p className="arb-muted">Looking for a wallet in this browser…</p> : wallets.length ? (
        <div className="arb-wallet-row">
          {wallets.map((w) => (
            <button key={w.info.uuid} type="button" className="tb-btn" disabled={busy} onClick={() => void connect(w)}>
              {w.info.icon && <img src={w.info.icon} alt="" width={18} height={18} />} {busy ? "Waiting for the wallet…" : `Connect ${w.info.name}`}
            </button>
          ))}
        </div>
      ) : (
        <p className="arb-muted">No browser wallet found. Install one ({SUGGESTED.map((s, i) => <span key={s.name}>{i ? ", " : ""}<a href={s.url} target="_blank" rel="noopener noreferrer">{s.name}</a></span>)}),
          or open this page in your wallet app's browser.</p>
      )}
      {msg && <p className="arb-stale" role="status">{msg}</p>}
    </li>
  );
}

/** Sizing for one pair: margin per leg, notional per leg, funding per day at the 7-day average, fees and break-even. */
export function plan(r: Row, capital: number, capitalPerNotional: number) {
  const notional = capital / capitalPerNotional;  // per leg (each leg's own margin + buffer is inside capitalPerNotional)
  const apr = r.stats?.mean_apr_pct ?? r.gross_spread_apr_pct;
  const perDay = notional * apr / 100 / 365;
  const fees = notional * 2 * ((TAKER_BPS[r.long] ?? 5) + (TAKER_BPS[r.short] ?? 5)) / 1e4;  // open + close, both legs, taker
  return { notional, margin: capital / 2, apr, perDay, perMonth: perDay * 30, fees, breakevenDays: perDay > 0 ? fees / perDay : null, fromAvg: r.stats?.mean_apr_pct != null };
}

export function FundingStart({ d, pick, onPick }: { d: FundingBoard; pick: string | null; onPick: (sym: string) => void }) {
  const choices = useMemo(() => {
    const s = scored(d.rows);
    const list = (s.length ? s : d.rows).slice(0, 15);
    const picked = d.rows.find((x) => x.symbol === pick);  // "Use" on any board row adds it to the list
    return picked && !list.includes(picked) ? [picked, ...list] : list;
  }, [d.rows, pick]);
  const [cap, setCap] = useState("1000");
  const r = choices.find((x) => x.symbol === pick) ?? choices[0];
  const capital = Math.max(0, Number(cap.replace(/[^0-9.]/g, "")) || 0);
  const lev = d.assumptions?.leverage ?? 2;
  if (!r) return null;
  const p = plan(r, capital, d.assumptions?.capital_per_notional ?? 1.25);
  const legs = [["short", r.short], ["long", r.long]] as const;
  return (
    <section className="arb-card arb-start" aria-labelledby="arb-start-title">
      <h2 id="arb-start-title">Start: run a funding pair yourself</h2>
      <p className="arb-muted">You hold both positions on your own accounts. Price Chat shows the numbers and the steps; it never places an order or holds your funds.</p>
      <ol className="arb-steps">
        <WalletStep />
        <li className="arb-step">
          <h3><span className="arb-step-n">2</span> Pick a pair and an amount</h3>
          <div className="arb-form">
            <label>Pair<select value={r.symbol} onChange={(e) => onPick(e.target.value)}>
              {choices.map((c) => <option key={c.symbol} value={c.symbol}>{base(c.symbol)}: short {VENUE_NAME[c.short]}, long {VENUE_NAME[c.long]}</option>)}
            </select></label>
            <label>Capital (USDC)<input inputMode="decimal" value={cap} onChange={(e) => setCap(e.target.value)} maxLength={12} /></label>
          </div>
          <dl className="arb-kv">
            <div><dt>Per leg</dt><dd>{usd(p.margin, 0)} margin → {usd(p.notional, 0)} position at {lev}x</dd></div>
            <div><dt>{p.fromAvg ? "At the 7-day average" : "At today's gap"} ({pct(p.apr)} APR)</dt><dd className="is-good">≈ {usd(p.perDay)} a day · {usd(p.perMonth)} a month</dd></div>
            <div><dt>Fees to open and close</dt><dd>≈ {usd(p.fees)} <small className="arb-muted">(taker, both legs)</small></dd></div>
            <div><dt>Break-even</dt><dd>{p.breakevenDays == null ? "never at this rate" : `${p.breakevenDays < 1 ? "under a day" : `${p.breakevenDays.toFixed(1)} days`} of funding`}</dd></div>
          </dl>
        </li>
        <li className="arb-step">
          <h3><span className="arb-step-n">3</span> Open both legs, same size, at the same time</h3>
          <div className="arb-legs-grid">
            {legs.map(([side, v]) => (
              <div key={side} className={`arb-leg is-${side}`}>
                <span className="arb-leg-side">{side === "short" ? "SHORT" : "LONG"}</span>
                <b>{base(r.symbol)} on {VENUE_NAME[v] ?? v}</b>
                <small>{usd(p.notional, 0)} position · {lev}x leverage</small>
                <small className="arb-muted">{side === "short" ? "this venue pays the most to shorts" : "this venue charges longs the least"}</small>
                <div className="arb-leg-links">
                  <a className="tb-btn" href={tradeUrl(v, r.symbol)} target="_blank" rel="noopener noreferrer">Open on {VENUE_NAME[v] ?? v}</a>
                  <a className="arb-linkbtn" href={ONBOARD[v]?.url} target="_blank" rel="noopener noreferrer" title={ONBOARD[v]?.how}>No account yet?</a>
                </div>
                <small className="arb-muted">{ONBOARD[v]?.how}</small>
              </div>
            ))}
          </div>
          <ul className="arb-rules">
            <li>Use limit orders where you can: fees above assume taker on both sides.</li>
            <li>Keep spare USDC on both venues: a fast move squeezes one leg's margin even though the pair is hedged.</li>
            <li>Close both legs together when the 7-day average falls below the fees, or the gap flips.</li>
          </ul>
        </li>
      </ol>
    </section>
  );
}
