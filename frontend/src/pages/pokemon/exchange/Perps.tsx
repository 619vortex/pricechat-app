import { useCallback, useEffect, useState } from "react";
import { openLogin } from "../../../website/emailLogin";
import { useSession } from "../../../website/members";
import { playSound, warmSounds } from "../../../website/sound";
import { exApi, newId, px, signed, type Candle, type Level, type Me, type PerpMarket, type PerpPosition, type Trade } from "./api";
import { OrderBook } from "./Book";
import { PriceChart } from "./Chart";
import { BattleBox, BigPnl, CardStage, HpBar, Mascot, PokeBurst, flavourFor, type Battle } from "./fun";

/**
 * Perpetual futures on the same 10 card markets (PAPER TRADING ONLY). Go UP (long) or DOWN (short), 1x-10x, isolated
 * margin in paper USDT. The mark is the perp price clamped to within 5% of the real-card index; hourly funding pulls the
 * perp toward the index; a position that falls to its maintenance margin is taken over by the insurance fund.
 * Same layout as spot: card + price, chart | order book | order form, your position under the chart, one tab bar.
 */

const INTERVALS = ["5m", "15m", "1h", "4h", "1D"];
const LEVERAGES = [1, 2, 3, 5, 10];
const STAKES = [50_00, 100_00, 250_00, 500_00];
type BottomTab = "positions" | "open" | "orders" | "fills" | "rules";

/** ppm per hour → a percentage string */
const rate = (ppm: number) => `${ppm >= 0 ? "+" : "−"}${(Math.abs(ppm) / 10_000).toFixed(4)}%`;

function useNow(ms = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), ms); return () => window.clearInterval(t); }, [ms]);
  return now;
}

function countdown(to: string, now: number) {
  const s = Math.max(0, Math.round((new Date(to).getTime() - now) / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/** The liquidation price for a new isolated position (fees ignored): where margin + P&L = 5% maintenance. */
export function estLiq(entry: number, long: boolean, lev: number, mmrBps: number) {
  const mmr = mmrBps / 10_000;
  return Math.max(0, Math.round(long ? (entry * (1 - 1 / lev)) / (1 - mmr) : (entry * (1 + 1 / lev)) / (1 + mmr)));
}

/** 1 at (or better than) entry, 0 at the liquidation price. */
export function health(p: PerpPosition) {
  if (!p.size || p.liq_price == null) return 1;
  const h = p.size > 0 ? (p.mark - p.liq_price) / (p.entry - p.liq_price) : (p.liq_price - p.mark) / (p.liq_price - p.entry);
  return Math.max(0, Math.min(1, Number.isFinite(h) ? h : 1));
}

export function PerpPositionCard({ p, image, onClose, busy }: { p: PerpPosition; image?: string | null; onClose: () => void; busy: boolean }) {
  const hp = health(p);
  const long = p.size > 0;
  return (
    <article className={`fx-pos ${long ? "is-long" : "is-short"}${hp < 0.2 ? " is-danger" : ""}`} aria-label={`${p.market} position`}>
      <CardStage image={image} name={p.market} mood={p.unrealized > 0 ? "up" : p.unrealized < 0 ? "down" : "flat"} small />
      <div className="fx-pos-body">
        <div className="fx-pos-head">
          <span className={`fx-side ${long ? "up" : "down"}`}>{long ? "📈 LONG" : "📉 SHORT"} · {p.leverage}x</span>
          <b>{p.market}</b>
        </div>
        <BigPnl cents={p.unrealized} pct={p.roe_pct} />
        <HpBar health={hp} />
        <dl className="fx-pos-facts">
          <div><dt>Size</dt><dd>{Math.abs(p.size)} ct</dd></div>
          <div><dt>Entry</dt><dd>{px(p.entry)}</dd></div>
          <div><dt>Now (mark)</dt><dd>{px(p.mark)}</dd></div>
          <div><dt>Liquidation</dt><dd className="down">{px(p.liq_price)}</dd></div>
          <div><dt>Margin</dt><dd>{px(p.margin)}</dd></div>
          <div><dt>Funding</dt><dd className={p.funding >= 0 ? "up" : "down"}>{signed(p.funding)}</dd></div>
        </dl>
      </div>
      <Mascot pnl={p.unrealized} health={hp} />
      <button type="button" className="fx-close" disabled={busy} onClick={onClose}>{busy ? "…" : `Close position ${p.unrealized >= 0 ? "& take profit 💰" : "✋"}`}</button>
    </article>
  );
}

export function PerpsTerminal({ sym, setSym, modeSwitch }: { sym: string; setSym: (s: string) => void; modeSwitch: React.ReactNode }) {
  const member = useSession();
  const now = useNow();
  const [markets, setMarkets] = useState<PerpMarket[]>([]);
  const [detail, setDetail] = useState<{ market: PerpMarket; book: { bids: Level[]; asks: Level[] }; trades: (Trade & { kind: string })[] } | null>(null);
  const [interval, setIntervalKey] = useState("15m");
  const [chart, setChart] = useState<{ candles: Candle[]; reference: { t: number; v: number }[] }>({ candles: [], reference: [] });
  const [me, setMe] = useState<Me | null>(null);
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [type, setType] = useState<"market" | "limit">("market");
  const [qty, setQty] = useState("1");
  const [price, setPrice] = useState("");
  const [lev, setLev] = useState(5);
  const [reduceOnly, setReduceOnly] = useState(false);
  const [busy, setBusy] = useState(false);
  const [closing, setClosing] = useState<string | null>(null);
  const [battle, setBattle] = useState<Battle | null>(null);
  const [burst, setBurst] = useState(0);
  const [tab, setTab] = useState<BottomTab>("positions");

  const psym = `${sym}-PERP`;
  const loadMarkets = useCallback(() => exApi.perps().then((r) => setMarkets(r.markets)).catch(() => undefined), []);
  const loadDetail = useCallback(() => exApi.perp(psym).then(setDetail).catch(() => undefined), [psym]);
  const loadChart = useCallback(() => exApi.perpCandles(psym, interval).then(setChart).catch(() => undefined), [psym, interval]);
  const loadMe = useCallback(() => (member ? exApi.me().then(setMe).catch(() => undefined) : Promise.resolve()), [member]);

  useEffect(() => { void loadMarkets(); const t = window.setInterval(loadMarkets, 10_000); return () => window.clearInterval(t); }, [loadMarkets]);
  useEffect(() => { setDetail(null); void loadDetail(); const t = window.setInterval(loadDetail, 2_000); return () => window.clearInterval(t); }, [loadDetail]);
  useEffect(() => { void loadChart(); const t = window.setInterval(loadChart, 10_000); return () => window.clearInterval(t); }, [loadChart]);
  useEffect(() => { void loadMe(); const t = window.setInterval(loadMe, 3_000); return () => window.clearInterval(t); }, [loadMe]);

  const m = detail?.market ?? markets.find((x) => x.symbol === psym) ?? null;
  const perps = me?.perps;
  const open = perps?.positions.filter((p) => p.size !== 0) ?? [];
  const pos = open.find((p) => p.market === psym);
  const others = open.filter((p) => p.market !== psym);
  const q = Math.max(0, Math.floor(Number(qty) || 0));
  const limitCents = Math.round(Number(price) * 100);
  const estPrice = type === "limit" ? limitCents : side === "buy" ? m?.ask ?? m?.mark ?? null : m?.bid ?? m?.mark ?? null;
  const closingQty = pos && (pos.size > 0) !== (side === "buy") ? Math.min(q, Math.abs(pos.size)) : 0;
  const opening = reduceOnly ? 0 : q - closingQty;
  const notional = estPrice ? estPrice * q : 0;
  const estMargin = estPrice ? Math.ceil((estPrice * opening) / lev) : 0;
  const estFee = m ? Math.floor((notional * m.taker_fee_bps) / 10_000) : 0;
  const liq = estPrice && opening > 0 && m ? estLiq(estPrice, side === "buy", pos && closingQty === 0 ? pos.leverage : lev, m.mmr_bps) : null;
  const avail = me?.usdt.available ?? 0;
  const maxOpen = m && estPrice ? Math.floor(avail / (estPrice / lev + (estPrice * m.taker_fee_bps) / 10_000)) : 0;
  const premUp = (m?.premium_pct ?? 0) >= 0;
  const refresh = () => void Promise.all([loadDetail(), loadMe(), loadChart(), loadMarkets()]);
  const stake = (cents: number) => (estPrice ? Math.max(1, Math.floor((cents * lev) / estPrice)) : 1);
  const action = reduceOnly || (closingQty > 0 && opening === 0) ? "Close" : side === "buy" ? "Long" : "Short";

  function pick(s: "buy" | "sell", p: number) {
    setSide(s);
    setType("limit");
    setPrice((p / 100).toFixed(2));
    playSound("tick");
  }

  async function submit() {
    if (!member) { openLogin(); return; }
    if (!m || busy) return;
    warmSounds();
    setBusy(true);
    setBattle(null);
    try {
      const r = await exApi.perpOrder({ symbol: m.symbol, side, type, qty: q, ...(type === "limit" ? { price: limitCents } : {}), leverage: lev, reduce_only: reduceOnly, client_id: newId() });
      const filled = r.fills.reduce((t, f) => t + f.qty, 0);
      const avg = filled ? r.fills.reduce((t, f) => t + f.price * f.qty, 0) / filled : 0;
      playSound(filled ? (side === "buy" ? "coin" : "cash") : "listed");
      if (filled) setBurst(Date.now());
      setBattle(filled ? { kind: "go", flavour: `Go, ${m.symbol}!`, text: `${side === "buy" ? "Long" : "Short"} ${filled} ${m.symbol} @ ${px(avg)}${r.order.status === "open" ? ` · ${q - filled} waiting in the book` : ""}`, at: Date.now() }
        : r.order.status === "open" ? { kind: "info", text: `Limit order placed: ${side === "buy" ? "long" : "short"} ${q} @ ${px(r.order.price)} (see Open orders)`, at: Date.now() }
          : { kind: "bad", text: `Not filled: ${r.order.reason ?? "no liquidity"}`, at: Date.now() });
      if (!filled && r.order.status === "open") setTab("open");
      refresh();
    } catch (e) {
      playSound("nope");
      setBattle({ kind: "bad", flavour: "But it failed!", text: e instanceof Error ? e.message : "Order refused.", at: Date.now() });
    } finally {
      setBusy(false);
    }
  }

  async function close(p: PerpPosition) {
    setClosing(p.market);
    try {
      const r = await exApi.perpClose(p.market);
      const filled = r.fills.reduce((t, f) => t + f.qty, 0);
      const avg = filled ? r.fills.reduce((t, f) => t + f.price * f.qty, 0) / filled : 0;
      const pnl = filled ? Math.round((avg - p.entry) * Math.min(filled, Math.abs(p.size)) * Math.sign(p.size)) : 0;
      const pct = p.margin ? (pnl / p.margin) * 100 : null;
      playSound(pnl >= 0 ? "cash" : "nope");
      if (filled && pnl > 0) setBurst(Date.now());
      setBattle(filled ? { ...flavourFor(pnl, pct), text: `Closed ${filled} ${p.market} · ≈ ${signed(pnl)} USDT`, at: Date.now() }
        : { kind: "bad", text: `Not closed: ${r.order.reason ?? "no liquidity"}`, at: Date.now() });
      refresh();
    } catch (e) {
      playSound("nope");
      setBattle({ kind: "bad", flavour: "But it failed!", text: e instanceof Error ? e.message : "Couldn't close.", at: Date.now() });
    } finally {
      setClosing(null);
    }
  }

  async function cancel(id: number) {
    try { await exApi.perpCancel(id); playSound("listed"); void Promise.all([loadDetail(), loadMe()]); }
    catch (e) { setBattle({ kind: "bad", text: e instanceof Error ? e.message : "Couldn't cancel.", at: Date.now() }); }
  }

  const tabs: [BottomTab, string][] = [["positions", `Positions${open.length ? ` (${open.length})` : ""}`], ["open", `Open orders${perps?.open_orders.length ? ` (${perps.open_orders.length})` : ""}`],
    ["orders", "Order history"], ["fills", "Trade history"], ["rules", "🎓 How perps work"]];

  return (
    <div className="ex is-perps">
      <div className="ex-paper" role="note">🧪 <b>PAPER TRADING</b> · play money (paper USDT) · leverage multiplies paper gains <i>and</i> losses · no real money</div>
      {modeSwitch}

      <nav className="ex-markets" aria-label="Perpetual markets">
        {markets.map((x) => (
          <button key={x.symbol} type="button" className={`ex-mk${x.symbol === psym ? " is-on" : ""}${x.tradable ? "" : " is-halted"}`} onClick={() => { setSym(x.spot); playSound("tick"); }}>
            {x.card?.image && <img src={x.card.image} alt="" loading="lazy" />}
            <span><b>{x.symbol.replace(/-PERP$/, "")}</b><small>{px(x.mark)}</small>
              <i className={(x.premium_pct ?? 0) >= 0 ? "up" : "down"}>{x.premium_pct != null ? `${x.premium_pct >= 0 ? "+" : ""}${x.premium_pct}%` : "—"}</i></span>
          </button>
        ))}
      </nav>

      {m && (
        <header className="ex-head">
          <CardStage image={m.card?.image} name={m.card?.name ?? m.symbol} mood={premUp ? "up" : "down"} />
          <div className="ex-hero">
            <h1>{m.symbol} <small>{m.title}</small></h1>
            <div className="ex-bigprice"><span className="ex-pricelabel">Mark price</span><b className={premUp ? "up" : "down"}>{px(m.mark)}</b><small>USDT</small>
              <span className={`ex-chip ${premUp ? "up" : "down"}`}>{m.premium_pct != null ? `${m.premium_pct >= 0 ? "+" : ""}${m.premium_pct}% vs real card` : "—"}</span></div>
            <div className="ex-tags">
              <span className={`ex-badge is-${m.reference.status}`}>{m.reference.status === "ok" ? "🟢 Index live" : m.reference.status === "stale" ? "🟡 Index stale" : "🔴 Closing only"}</span>
              {m.auto && <span className="ex-badge is-sim" title="The owner's simulated auto-trader is active: its trades are labelled SIM">🤖 Simulated activity on</span>}
              <span className="ex-badge">up to {m.max_leverage}x</span>
            </div>
            <dl className="ex-stats">
              <div><dt>Mark</dt><dd>{px(m.mark)}</dd></div>
              <div><dt>Index (real card)</dt><dd>{px(m.index)}</dd></div>
              <div><dt>Funding / next</dt><dd><span className={m.funding.predicted_ppm > 0 ? "down" : "up"}>{rate(m.funding.predicted_ppm)}</span> <small className="ex-cd">{countdown(m.funding.next_at, now)}</small></dd></div>
              <div><dt>Open interest</dt><dd>{m.open_interest} <small>ct</small></dd></div>
              <div><dt>24h volume</dt><dd>{m.stats_24h.volume} <small>ct</small></dd></div>
            </dl>
          </div>
        </header>
      )}

      <div className="ex-grid">
        <section className="ex-panel ex-chart-panel" aria-label="Chart">
          <div className="ex-intervals">
            {INTERVALS.map((k) => <button key={k} type="button" className={interval === k ? "is-on" : ""} onClick={() => setIntervalKey(k)}>{k}</button>)}
            <span className="ex-legend"><i className="c" /> perp trades <i className="r" /> real-card index</span>
          </div>
          <PriceChart candles={chart.candles} reference={chart.reference} refNow={m?.index} />
        </section>

        <OrderBook book={detail?.book ?? null} trades={detail?.trades ?? null} mid={m?.mark} midLabel="mark price" midUp={premUp} onPick={pick} />

        <section className="ex-panel ex-entry" aria-label="Open a position">
          {member && me && (
            <div className="ex-wallet">
              <div><span>Available</span><b>{px(avail)} <small>USDT</small></b></div>
              <div><span>Position here</span><b className={pos ? (pos.size > 0 ? "up" : "down") : ""}>{pos ? `${pos.size > 0 ? "Long" : "Short"} ${Math.abs(pos.size)}` : "None"}</b></div>
            </div>
          )}
          <div className="ex-sides is-big">
            <button type="button" className={`buy${side === "buy" ? " is-on" : ""}`} onClick={() => { setSide("buy"); playSound("tick"); }}>📈 Up<small>Long</small></button>
            <button type="button" className={`sell${side === "sell" ? " is-on" : ""}`} onClick={() => { setSide("sell"); playSound("tick"); }}>📉 Down<small>Short</small></button>
          </div>
          <div className="ex-label">Leverage <b className={lev >= 8 ? "down" : lev >= 4 ? "warn" : "up"}>{lev}x {lev >= 10 ? "🔥" : lev >= 5 ? "⚡" : "🌱"}</b></div>
          <div className="ex-lev-marks">{LEVERAGES.map((k) => <button key={k} type="button" className={lev === k ? "is-on" : ""} onClick={() => { setLev(k); playSound("tick"); }}>{k}x</button>)}</div>
          <div className="ex-types">
            <button type="button" className={type === "market" ? "is-on" : ""} onClick={() => setType("market")}>Market <small>now</small></button>
            <button type="button" className={type === "limit" ? "is-on" : ""} onClick={() => { setType("limit"); if (!price && m) setPrice((((side === "buy" ? m.bid : m.ask) ?? m.mark ?? 0) / 100).toFixed(2)); }}>Limit <small>my price</small></button>
          </div>
          {type === "limit" && (
            <label className="ex-field"><span>Price</span><input type="number" inputMode="decimal" step="0.01" min="0.01" value={price} onChange={(e) => setPrice(e.target.value)} /><i>USDT</i></label>
          )}
          <div className="ex-label">Quick: put in <small>USDT of margin</small></div>
          <div className="ex-quick">
            {STAKES.map((c) => <button key={c} type="button" onClick={() => setQty(String(stake(c)))}>{px(c, 0)}</button>)}
            <button type="button" onClick={() => setQty(String(Math.max(1, maxOpen)))}>Max</button>
          </div>
          <label className="ex-field"><span>Size</span><input type="number" inputMode="numeric" step="1" min="1" value={qty} onChange={(e) => setQty(e.target.value)} /><i>contracts</i></label>
          <label className="ex-check"><input type="checkbox" checked={reduceOnly} onChange={(e) => setReduceOnly(e.target.checked)} /> Only close <small>(never opens a new position)</small></label>
          <dl className="ex-est">
            <div><dt>Margin you put in</dt><dd><b>{estMargin ? `${px(estMargin)} USDT` : closingQty ? "— (closing)" : "—"}</b></dd></div>
            <div><dt>Position value</dt><dd>{notional ? `${px(notional)} USDT` : "—"}</dd></div>
            <div><dt>Liquidation price</dt><dd className="down">{liq ? px(liq) : "—"}</dd></div>
            <div><dt>Fee ({(m?.taker_fee_bps ?? 10) / 100}%)</dt><dd>{estFee ? `≈ ${px(estFee)}` : "—"}</dd></div>
          </dl>
          <div className="fx-btnwrap">
            <button type="button" className={`ex-submit ${side}`} disabled={busy || !m || (!m.tradable && !reduceOnly) || q < 1 || (type === "limit" && !(limitCents > 0))} onClick={() => void submit()}>
              {!member ? "Sign in to trade" : busy ? "…" : `${action} ${q || ""} ${m?.symbol ?? ""} · ${lev}x`}</button>
            <PokeBurst at={burst} />
          </div>
          <p className="ex-fine">You can lose at most the margin you put in, never more. Liquidation uses the <b>mark</b>, which can't stray more than 5% from the real-card price.</p>
        </section>

        <section className="ex-mine" aria-label="Your position">
          {member === null ? (
            <div className="ex-panel ex-signin"><p><b>Start with 10,000 paper USDT.</b> Sign in with your Kiwi Passport to trade perps.</p>
              <button type="button" className="ex-submit buy" onClick={openLogin}>Sign in</button></div>
          ) : pos ? (
            <PerpPositionCard p={pos} image={m?.card?.image} busy={closing === pos.market} onClose={() => void close(pos)} />
          ) : (
            <div className="ex-panel fx-empty"><span className="fx-ball is-idle" /><p><b>No {m?.symbol ?? ""} position yet.</b> Pick Up or Down on the right, choose leverage and go!
              {others.length > 0 && <> You have {others.length} other position{others.length > 1 ? "s" : ""}: see <button type="button" className="ex-link" onClick={() => setTab("positions")}>Positions</button> below.</>}</p></div>
          )}
          {me && (
            <dl className="ex-acct is-mini">
              <div><dt>Equity</dt><dd>{px(me.equity)}</dd></div>
              <div><dt>Total P&L</dt><dd className={me.pnl >= 0 ? "up" : "down"}>{signed(me.pnl)}</dd></div>
              <div><dt>In margin</dt><dd>{px(open.reduce((t, p) => t + p.margin, 0))}</dd></div>
            </dl>
          )}
        </section>
      </div>

      <section className="ex-panel ex-bottom">
        <div className="ex-subtabs">
          {tabs.map(([k, l]) => <button key={k} type="button" className={tab === k ? "is-on" : ""} onClick={() => setTab(k)}>{l}</button>)}
        </div>
        {tab === "positions" && (
          open.length === 0 ? <p className="ex-none">{member ? "No open positions." : "Sign in to see your positions."}</p> : (
            <div className="ex-table-wrap"><table className="ex-table"><thead><tr><th>Market</th><th>Side</th><th>Size</th><th>Entry</th><th>Mark</th><th>Liq. price</th><th>Margin</th><th>P&L (ROE)</th><th /></tr></thead>
              <tbody>{open.map((p) => (
                <tr key={p.market} className={p.market === psym ? "is-current" : ""} onClick={() => setSym(p.market.replace(/-PERP$/, ""))}>
                  <td><b>{p.market}</b></td><td className={p.size > 0 ? "up" : "down"}>{p.size > 0 ? "long" : "short"} {p.leverage}x</td><td>{Math.abs(p.size)}</td>
                  <td>{px(p.entry)}</td><td>{px(p.mark)}</td><td className="down">{px(p.liq_price)}</td><td>{px(p.margin)}</td>
                  <td className={p.unrealized >= 0 ? "up" : "down"}>{signed(p.unrealized)}{p.roe_pct != null ? ` (${p.roe_pct}%)` : ""}</td>
                  <td><button type="button" className="ex-cancel" disabled={closing === p.market} onClick={(e) => { e.stopPropagation(); void close(p); }}>Close</button></td></tr>
              ))}</tbody></table></div>
          )
        )}
        {tab === "open" && (
          !perps || perps.open_orders.length === 0 ? <p className="ex-none">No open orders. Limit orders wait here until they fill.</p> : (
            <div className="ex-table-wrap"><table className="ex-table"><thead><tr><th>Market</th><th>Side</th><th>Price</th><th>Filled</th><th>Lev.</th><th /></tr></thead>
              <tbody>{perps.open_orders.map((o) => (
                <tr key={o.id}><td><b>{o.market}</b></td><td className={o.side === "buy" ? "up" : "down"}>{o.side === "buy" ? "long" : "short"}{o.reduce_only ? " (close only)" : ""}</td><td>{px(o.price)}</td>
                  <td>{o.filled} / {o.qty}</td><td>{o.leverage}x</td><td><button type="button" className="ex-cancel" onClick={() => void cancel(o.id)}>Cancel</button></td></tr>
              ))}</tbody></table></div>
          )
        )}
        {tab === "orders" && (
          !perps || perps.orders.length === 0 ? <p className="ex-none">No past orders.</p> : (
            <div className="ex-table-wrap"><table className="ex-table"><thead><tr><th>Market</th><th>Side</th><th>Type</th><th>Price</th><th>Filled</th><th>Lev.</th><th>Status</th></tr></thead>
              <tbody>{perps.orders.map((o) => (
                <tr key={o.id}><td>{o.market}</td><td className={o.side === "buy" ? "up" : "down"}>{o.side === "buy" ? "long" : "short"}</td><td>{o.type}</td><td>{px(o.price)}</td><td>{o.filled} / {o.qty}</td><td>{o.leverage}x</td>
                  <td title={o.reason ?? ""}>{o.status}</td></tr>
              ))}</tbody></table></div>
          )
        )}
        {tab === "fills" && (
          !perps || perps.history.length === 0 ? <p className="ex-none">No trades yet.</p> : (
            <div className="ex-table-wrap"><table className="ex-table"><thead><tr><th>Time</th><th>Market</th><th>Side</th><th>Price</th><th>Qty</th><th>Fee</th><th>Realized</th></tr></thead>
              <tbody>{perps.history.map((h) => (
                <tr key={h.id}><td>{new Date(h.at).toLocaleString()}</td><td>{h.market}</td><td className={h.side === "buy" ? "up" : "down"}>{h.side === "buy" ? "buy" : "sell"}{h.kind === "liquidation" ? " (liquidation)" : ""}</td>
                  <td>{px(h.price)}</td><td>{h.qty}</td><td>{px(h.fee)}</td><td className={(h.realized ?? 0) >= 0 ? "up" : "down"}>{h.realized != null ? signed(h.realized) : "—"}</td></tr>
              ))}</tbody></table></div>
          )
        )}
        {tab === "rules" && m && <PerpRules m={m} />}
      </section>
      <BattleBox b={battle} onDone={() => setBattle(null)} />
    </div>
  );
}

function PerpRules({ m }: { m: PerpMarket }) {
  return (
    <ul className="ex-rules">
      <li><b>Paper trading only.</b> Paper USDT, no real money. A perp never expires and never owns any card: it tracks the card's real price.</li>
      <li><b>Up (long) or Down (short), 1x to {m.max_leverage}x.</b> Margin = position value ÷ leverage, set aside for that position only. The most you can lose on a position is its margin.</li>
      <li><b>Index</b> = the real-card price (verified PSA 10 sales + Scrydex). <b>Mark</b> = the perp price, kept within 5% of the index, so a thin book can't knock anyone out.</li>
      <li><b>The order book:</b> red = sellers (asks), green = buyers (bids). Market orders fill now at the best prices; limit orders wait at your price. Tap a book price to use it.</li>
      <li><b>Funding every hour:</b> (mark − index) ÷ index ÷ 24, capped at ±{m.funding.cap_ppm / 10_000}%. Perp above the real card: longs pay shorts. Below: shorts pay longs.</li>
      <li><b>HP bar & liquidation:</b> HP is how far the mark is from your liquidation price. At 0 HP ({m.mmr_bps / 100}% of the position value left) the insurance fund takes the position over at the mark, with a 1% fee.</li>
      <li><b>Fees:</b> {m.taker_fee_bps / 100}% taker, {m.maker_fee_bps / 100}% maker. Trades marked <em>SIM</em> come from the labelled simulated market maker or auto-trader.</li>
    </ul>
  );
}
