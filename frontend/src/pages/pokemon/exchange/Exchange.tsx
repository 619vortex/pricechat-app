import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { openLogin } from "../../../website/emailLogin";
import { useSession } from "../../../website/members";
import { playSound, warmSounds } from "../../../website/sound";
import { exApi, newId, px, signed, type Board, type Candle, type ExMarket, type Level, type Me, type Position, type Trade } from "./api";
import { OrderBook } from "./Book";
import { MarketShell } from "../mx";
import { PriceChart } from "./Chart";
import { BattleBox, BigPnl, CardStage, Mascot, PokeBurst, flavourFor, type Battle } from "./fun";
import { PerpsTerminal } from "./Perps";
import "./exchange.css";

/**
 * The Pokémon Paper Exchange (/pokemon/exchange). PAPER TRADING ONLY: paper USDT, synthetic contracts (1 contract =
 * 1/1,000 of a graded card's reference value), no real money, no ownership of any card.
 *
 * One layout for spot and perps: the card and its price on top; chart | order book | order form; your position under
 * the chart; one tab bar (positions, orders, history, leaderboard, how it works) at the bottom.
 */

export const INTERVALS = ["5m", "15m", "1h", "4h", "1D"];
const BUYS = [50_00, 100_00, 250_00, 500_00];
type BottomTab = "positions" | "open" | "orders" | "fills" | "board" | "rules";

export function ExchangePage() {
  const [mode, setMode] = useState<"spot" | "perps">(() => (new URLSearchParams(location.search).get("mode") === "perps" ? "perps" : "spot"));
  const [sym, setSym] = useState<string>(() => (new URLSearchParams(location.search).get("m") ?? "CHAR1ST").replace(/-PERP$/, ""));
  useEffect(() => { try { history.replaceState(null, "", `/pokemon/exchange?${mode === "perps" ? "mode=perps&" : ""}m=${sym}`); } catch { /* fine */ } }, [sym, mode]);
  const modeSwitch = (
    <div className="ex-top">
      <div className="ex-mode" role="tablist" aria-label="Spot or perpetuals">
        <button type="button" role="tab" aria-selected={mode === "spot"} className={mode === "spot" ? "is-on" : ""} onClick={() => { setMode("spot"); playSound("tick"); }}>
          🃏 Spot <small>buy & own contracts</small></button>
        <button type="button" role="tab" aria-selected={mode === "perps"} className={mode === "perps" ? "is-on" : ""} onClick={() => { setMode("perps"); playSound("tick"); }}>
          ⚡ Perps <small>up / down · up to 10x</small></button>
      </div>
    </div>
  );
  // part of the Pokémon Market: its tabs and breadcrumb on top, like the Marketplace
  return (
    <MarketShell crumbs={[["Markets", "/pokemon"], ["Exchange", null]]} compact>
      {mode === "perps" ? <PerpsTerminal sym={sym} setSym={setSym} modeSwitch={modeSwitch} /> : <SpotTerminal sym={sym} setSym={setSym} modeSwitch={modeSwitch} />}
    </MarketShell>
  );
}

function HoldingCard({ p, image, onSell, onBuy, busy }: { p: Position; image?: string | null; onSell: () => void; onBuy: () => void; busy: boolean }) {
  const pct = p.cost ? (p.unrealized / p.cost) * 100 : null;
  return (
    <article className={`fx-pos is-long${p.unrealized < 0 ? " is-red" : ""}`} aria-label={`${p.market} holding`}>
      <CardStage image={image} name={p.market} mood={p.unrealized > 0 ? "up" : p.unrealized < 0 ? "down" : "flat"} small />
      <div className="fx-pos-body">
        <div className="fx-pos-head"><span className="fx-side up">🃏 YOU OWN {p.qty}</span><b>{p.market}</b></div>
        <BigPnl cents={p.unrealized} pct={pct} />
        <dl className="fx-pos-facts">
          <div><dt>Avg cost</dt><dd>{px(p.avg_cost)}</dd></div>
          <div><dt>Now (real card)</dt><dd>{px(p.mark)}</dd></div>
          <div><dt>Worth</dt><dd>{px(p.value)}</dd></div>
          <div><dt>Realized</dt><dd className={p.realized >= 0 ? "up" : "down"}>{signed(p.realized)}</dd></div>
        </dl>
      </div>
      <Mascot pnl={p.unrealized} />
      <div className="fx-pos-actions">
        <button type="button" className="fx-close" disabled={busy || p.qty - p.held < 1} onClick={onSell}>{busy ? "…" : `Sell all ${p.qty - p.held} ${p.unrealized >= 0 ? "💰" : "✋"}`}</button>
        <button type="button" className="fx-more" disabled={busy} onClick={onBuy}>Buy more</button>
      </div>
    </article>
  );
}

function SpotTerminal({ sym, setSym, modeSwitch }: { sym: string; setSym: (s: string) => void; modeSwitch: React.ReactNode }) {
  const member = useSession();
  const [markets, setMarkets] = useState<ExMarket[]>([]);
  const [detail, setDetail] = useState<{ market: ExMarket; book: { bids: Level[]; asks: Level[] }; trades: Trade[] } | null>(null);
  const [interval, setIntervalKey] = useState("15m");
  const [chart, setChart] = useState<{ candles: Candle[]; reference: { t: number; v: number }[] }>({ candles: [], reference: [] });
  const [me, setMe] = useState<Me | null>(null);
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [type, setType] = useState<"market" | "limit">("market");
  const [qty, setQty] = useState("1");
  const [price, setPrice] = useState("");
  const [busy, setBusy] = useState(false);
  const [battle, setBattle] = useState<Battle | null>(null);
  const [burst, setBurst] = useState(0);
  const [tab, setTab] = useState<BottomTab>("positions");
  const [board, setBoard] = useState<Board | null>(null);
  const [boardKey, setBoardKey] = useState("equity");

  const loadMarkets = useCallback(() => exApi.markets().then((r) => setMarkets(r.markets)).catch(() => undefined), []);
  const loadDetail = useCallback(() => exApi.market(sym).then(setDetail).catch(() => undefined), [sym]);
  const loadChart = useCallback(() => exApi.candles(sym, interval).then(setChart).catch(() => undefined), [sym, interval]);
  const loadMe = useCallback(() => (member ? exApi.me().then(setMe).catch(() => undefined) : Promise.resolve()), [member]);

  useEffect(() => { void loadMarkets(); const t = window.setInterval(loadMarkets, 10_000); return () => window.clearInterval(t); }, [loadMarkets]);
  useEffect(() => { setDetail(null); void loadDetail(); const t = window.setInterval(loadDetail, 2_000); return () => window.clearInterval(t); }, [loadDetail]);
  useEffect(() => { void loadChart(); const t = window.setInterval(loadChart, 10_000); return () => window.clearInterval(t); }, [loadChart]);
  useEffect(() => { void loadMe(); const t = window.setInterval(loadMe, 4_000); return () => window.clearInterval(t); }, [loadMe]);
  useEffect(() => { if (tab === "board") void exApi.board(boardKey).then(setBoard).catch(() => undefined); }, [tab, boardKey]);

  const m = detail?.market ?? markets.find((x) => x.symbol === sym) ?? null;
  const q = Math.max(0, Math.floor(Number(qty) || 0));
  const limitCents = Math.round(Number(price) * 100);
  const estPrice = type === "limit" ? limitCents : side === "buy" ? m?.ask ?? null : m?.bid ?? null;
  const estTotal = estPrice ? estPrice * q : null;
  const estFee = estTotal && m ? Math.floor((estTotal * m.rules.taker_fee_bps) / 10_000) : 0;
  const held = me?.positions.filter((p) => p.qty > 0) ?? [];
  const pos = held.find((p) => p.market === sym);
  const ownedFree = pos ? pos.qty - pos.held : 0;
  const maxBuy = m && estPrice ? Math.floor((me?.usdt.available ?? 0) / (estPrice * (1 + m.rules.taker_fee_bps / 10_000))) : 0;
  const chg = m?.stats_24h.change_pct ?? null;

  function pick(s: "buy" | "sell", p: number) {
    setSide(s);
    setType("limit");
    setPrice((p / 100).toFixed(2));
    playSound("tick");
  }

  async function trade(s: "buy" | "sell", n: number, symbol = m?.symbol, forceMarket = false) {
    if (!member) { openLogin(); return; }
    if (!symbol || busy || n < 1) return;
    warmSounds();
    setBusy(true);
    setBattle(null);
    const before = me?.positions.find((p) => p.market === symbol);
    const lim = !forceMarket && symbol === m?.symbol && type === "limit";
    try {
      const r = await exApi.order({ symbol, side: s, type: lim ? "limit" : "market", qty: n, ...(lim ? { price: limitCents } : {}), client_id: newId() });
      const filled = r.fills.reduce((t, f) => t + f.qty, 0);
      const avg = filled ? r.fills.reduce((t, f) => t + f.price * f.qty, 0) / filled : 0;
      playSound(filled ? (s === "buy" ? "coin" : "cash") : "listed");
      if (filled) setBurst(Date.now());
      const text = filled ? `${s === "buy" ? "Bought" : "Sold"} ${filled} ${symbol} @ ${px(avg)}${r.order.status === "open" ? ` · ${n - filled} waiting in the book` : ""}`
        : r.order.status === "open" ? `Limit order placed: ${s} ${n} @ ${px(r.order.price)} (see Open orders)` : `Not filled: ${r.order.reason ?? "no liquidity"}`;
      if (filled && s === "sell" && before?.avg_cost) {
        const pnl = Math.round((avg - before.avg_cost) * filled);
        setBattle({ ...flavourFor(pnl, (pnl / (before.avg_cost * filled)) * 100), text: `${text} · ≈ ${signed(pnl)} USDT`, at: Date.now() });
      } else {
        setBattle({ kind: filled ? "go" : r.order.status === "open" ? "info" : "bad", flavour: filled && s === "buy" ? `${symbol}, I choose you!` : undefined, text, at: Date.now() });
      }
      if (!filled && r.order.status === "open") setTab("open");
      void Promise.all([loadDetail(), loadMe(), loadChart(), loadMarkets()]);
    } catch (e) {
      playSound("nope");
      setBattle({ kind: "bad", flavour: "But it failed!", text: e instanceof Error ? e.message : "Order refused.", at: Date.now() });
    } finally {
      setBusy(false);
    }
  }

  async function cancel(id: number) {
    try { await exApi.cancel(id); playSound("listed"); void Promise.all([loadDetail(), loadMe()]); }
    catch (e) { setBattle({ kind: "bad", text: e instanceof Error ? e.message : "Couldn't cancel.", at: Date.now() }); }
  }

  const tabs: [BottomTab, string][] = [["positions", `My cards${held.length ? ` (${held.length})` : ""}`], ["open", `Open orders${me?.open_orders.length ? ` (${me.open_orders.length})` : ""}`],
    ["orders", "Order history"], ["fills", "Trade history"], ["board", "🏆 Leaderboard"], ["rules", "🎓 How it works"]];

  return (
    <div className="ex">
      <div className="ex-paper" role="note">🧪 <b>PAPER TRADING</b> · play money (paper USDT) · no real money, deposits or withdrawals · contracts don't own any card</div>
      {modeSwitch}

      <nav className="ex-markets" aria-label="Markets">
        {markets.map((x) => (
          <button key={x.symbol} type="button" className={`ex-mk${x.symbol === sym ? " is-on" : ""}${x.tradable ? "" : " is-halted"}`} onClick={() => { setSym(x.symbol); playSound("tick"); }}>
            {x.card?.image && <img src={x.card.image} alt="" loading="lazy" />}
            <span><b>{x.symbol}</b><small>{px(x.last ?? x.reference.contract)}</small>
              <i className={(x.stats_24h.change_pct ?? 0) >= 0 ? "up" : "down"}>{x.stats_24h.change_pct != null ? `${x.stats_24h.change_pct >= 0 ? "+" : ""}${x.stats_24h.change_pct}%` : "—"}</i></span>
          </button>
        ))}
      </nav>

      {m && (
        <header className="ex-head">
          <CardStage image={m.card?.image} name={m.card?.name ?? m.symbol} mood={(chg ?? 0) > 0 ? "up" : (chg ?? 0) < 0 ? "down" : "flat"} />
          <div className="ex-hero">
            <h1>{m.symbol} <small>{m.title}</small></h1>
            <div className="ex-bigprice"><span className="ex-pricelabel">Last price</span><b className={(chg ?? 0) >= 0 ? "up" : "down"}>{px(m.last ?? m.reference.contract)}</b><small>USDT</small>
              <span className={`ex-chip ${(chg ?? 0) >= 0 ? "up" : "down"}`}>{chg != null ? `${chg >= 0 ? "▲ +" : "▼ "}${chg}% 24h` : "new"}</span></div>
            <div className="ex-tags">
              <RefBadge m={m} />
              {m.auto && <span className="ex-badge is-sim" title="The owner's simulated auto-trader is active: its trades are labelled SIM">🤖 Simulated activity on</span>}
            </div>
            <dl className="ex-stats">
              <div><dt>Paper price</dt><dd>{px(m.last)}</dd></div>
              <div><dt>Real card (per contract)</dt><dd>{px(m.reference.contract)}</dd></div>
              <div><dt>Real card value</dt><dd>${m.reference.card != null ? (m.reference.card / 100).toLocaleString("en-US", { maximumFractionDigits: 0 }) : "—"}</dd></div>
              <div><dt>vs real card</dt><dd className={(m.premium_pct ?? 0) >= 0 ? "up" : "down"}>{m.premium_pct != null ? `${m.premium_pct >= 0 ? "+" : ""}${m.premium_pct}%` : "—"}</dd></div>
              <div><dt>24h volume</dt><dd>{m.stats_24h.volume} <small>ct</small></dd></div>
            </dl>
          </div>
        </header>
      )}

      <div className="ex-grid">
        <section className="ex-panel ex-chart-panel" aria-label="Chart">
          <div className="ex-intervals">
            {INTERVALS.map((k) => <button key={k} type="button" className={interval === k ? "is-on" : ""} onClick={() => setIntervalKey(k)}>{k}</button>)}
            <span className="ex-legend"><i className="c" /> paper trades <i className="r" /> real-card price</span>
          </div>
          <PriceChart candles={chart.candles} reference={chart.reference} refNow={m?.reference.contract} />
        </section>

        <OrderBook book={detail?.book ?? null} trades={detail?.trades ?? null} mid={m?.last ?? m?.reference.contract} midLabel="last price" midUp={(chg ?? 0) >= 0} onPick={pick} />

        <section className="ex-panel ex-entry" aria-label="Place an order">
          {member && me && (
            <div className="ex-wallet">
              <div><span>Cash</span><b>{px(me.usdt.available)} <small>USDT</small></b></div>
              <div><span>You own</span><b>{pos?.qty ?? 0} <small>{m?.symbol}</small></b></div>
            </div>
          )}
          <div className="ex-sides is-big">
            <button type="button" className={`buy${side === "buy" ? " is-on" : ""}`} onClick={() => { setSide("buy"); playSound("tick"); }}>🛒 Buy</button>
            <button type="button" className={`sell${side === "sell" ? " is-on" : ""}`} onClick={() => { setSide("sell"); playSound("tick"); }}>💸 Sell</button>
          </div>
          <div className="ex-types">
            <button type="button" className={type === "market" ? "is-on" : ""} onClick={() => setType("market")}>Market <small>now</small></button>
            <button type="button" className={type === "limit" ? "is-on" : ""} onClick={() => { setType("limit"); if (!price && m) setPrice((((side === "buy" ? m.bid : m.ask) ?? m.reference.contract ?? 0) / 100).toFixed(2)); }}>Limit <small>my price</small></button>
          </div>
          {type === "limit" && (
            <label className="ex-field"><span>Price</span><input type="number" inputMode="decimal" step="0.01" min="0.01" value={price} onChange={(e) => setPrice(e.target.value)} /><i>USDT</i></label>
          )}
          <div className="ex-label">{side === "buy" ? <>Quick: spend <small>USDT</small></> : "Quick: how much"}</div>
          <div className="ex-quick">
            {side === "buy"
              ? <>{BUYS.map((c) => <button key={c} type="button" onClick={() => setQty(String(estPrice ? Math.max(1, Math.floor(c / estPrice)) : 1))}>{px(c, 0)}</button>)}
                <button type="button" onClick={() => setQty(String(Math.max(1, maxBuy)))}>Max</button></>
              : [25, 50, 100].map((pct) => <button key={pct} type="button" onClick={() => setQty(String(Math.max(1, Math.floor((ownedFree * pct) / 100))))}>{pct === 100 ? "All" : `${pct}%`}</button>)}
          </div>
          <label className="ex-field"><span>Quantity</span><input type="number" inputMode="numeric" step="1" min="1" value={qty} onChange={(e) => setQty(e.target.value)} /><i>contracts</i></label>
          <dl className="ex-est">
            <div><dt>Price {type === "market" ? "(best now)" : "(yours)"}</dt><dd>{estPrice ? px(estPrice) : "—"}</dd></div>
            <div><dt>{side === "buy" ? "You pay" : "You get"}</dt><dd><b>{estTotal ? `${px(side === "buy" ? estTotal + estFee : estTotal - estFee)} USDT` : "—"}</b></dd></div>
            <div><dt>incl. fee ({(m?.rules.taker_fee_bps ?? 10) / 100}%)</dt><dd>{estFee ? px(estFee) : "—"}</dd></div>
          </dl>
          <div className="fx-btnwrap">
            <button type="button" className={`ex-submit ${side}`} disabled={busy || !m || !m.tradable || q < 1 || (type === "limit" && !(limitCents > 0))} onClick={() => void trade(side, q)}>
              {!member ? "Sign in to trade" : busy ? "…" : `${side === "buy" ? "Buy" : "Sell"} ${q || ""} ${m?.symbol ?? ""}`}</button>
            <PokeBurst at={burst} />
          </div>
          {m && !m.tradable && <p className="ex-msg is-bad">Trading is halted: not enough reliable real-card price evidence right now.</p>}
          <p className="ex-fine">{type === "market" ? "Market: fills right now at the best prices in the book." : "Limit: waits in the book until someone matches your price."} Want to go short or use leverage? Switch to <b>⚡ Perps</b>.</p>
        </section>

        <section className="ex-mine" aria-label="Your holding">
          {member === null ? (
            <div className="ex-panel ex-signin"><p><b>Start with 10,000 paper USDT.</b> Sign in with your Kiwi Passport to trade.</p>
              <button type="button" className="ex-submit buy" onClick={openLogin}>Sign in</button></div>
          ) : pos && m ? (
            <HoldingCard p={pos} image={m.card?.image} busy={busy} onSell={() => void trade("sell", ownedFree, pos.market, true)} onBuy={() => { setSide("buy"); playSound("tick"); }} />
          ) : (
            <div className="ex-panel fx-empty"><span className="fx-ball is-idle" /><p><b>You don't own any {m?.symbol ?? ""} yet.</b> Pick an amount on the right and press Buy.
              {held.length > 0 && <> You hold {held.length} other card{held.length > 1 ? "s" : ""}: see <button type="button" className="ex-link" onClick={() => setTab("positions")}>My cards</button> below.</>}</p></div>
          )}
          {me && (
            <dl className="ex-acct is-mini">
              <div><dt>Equity</dt><dd>{px(me.equity)}</dd></div>
              <div><dt>Total P&L</dt><dd className={me.pnl >= 0 ? "up" : "down"}>{signed(me.pnl)} ({((me.pnl / me.start) * 100).toFixed(2)}%)</dd></div>
              <div><dt>In open orders</dt><dd>{px(me.usdt.held)}</dd></div>
            </dl>
          )}
        </section>
      </div>

      <section className="ex-panel ex-bottom">
        <div className="ex-subtabs">
          {tabs.map(([k, l]) => <button key={k} type="button" className={tab === k ? "is-on" : ""} onClick={() => setTab(k)}>{l}</button>)}
        </div>
        {tab === "positions" && (
          held.length === 0 ? <p className="ex-none">{member ? "You don't own any contracts yet." : "Sign in to see your cards."}</p> : (
            <div className="ex-table-wrap"><table className="ex-table"><thead><tr><th>Market</th><th>Qty</th><th>Avg cost</th><th>Now (real)</th><th>Worth</th><th>P&L</th><th /></tr></thead>
              <tbody>{held.map((p) => (
                <tr key={p.market} className={p.market === sym ? "is-current" : ""} onClick={() => setSym(p.market)}><td><b>{p.market}</b></td><td>{p.qty}</td><td>{px(p.avg_cost)}</td><td>{px(p.mark)}</td><td>{px(p.value)}</td>
                  <td className={p.unrealized >= 0 ? "up" : "down"}>{signed(p.unrealized)}</td>
                  <td><button type="button" className="ex-cancel" disabled={busy || p.qty - p.held < 1} onClick={(e) => { e.stopPropagation(); void trade("sell", p.qty - p.held, p.market, true); }}>Sell all</button></td></tr>
              ))}</tbody></table></div>
          )
        )}
        {tab === "open" && (
          !me || me.open_orders.length === 0 ? <p className="ex-none">No open orders. Limit orders wait here until they fill.</p> : (
            <div className="ex-table-wrap"><table className="ex-table"><thead><tr><th>Market</th><th>Side</th><th>Price</th><th>Filled</th><th /></tr></thead>
              <tbody>{me.open_orders.map((o) => (
                <tr key={o.id}><td><b>{o.market}</b></td><td className={o.side === "buy" ? "up" : "down"}>{o.side}</td><td>{px(o.price)}</td><td>{o.filled} / {o.qty}</td>
                  <td><button type="button" className="ex-cancel" onClick={() => void cancel(o.id)}>Cancel</button></td></tr>
              ))}</tbody></table></div>
          )
        )}
        {tab === "orders" && (
          !me || me.orders.length === 0 ? <p className="ex-none">No past orders.</p> : (
            <div className="ex-table-wrap"><table className="ex-table"><thead><tr><th>Market</th><th>Side</th><th>Type</th><th>Price</th><th>Filled</th><th>Status</th></tr></thead>
              <tbody>{me.orders.map((o) => (
                <tr key={o.id}><td>{o.market}</td><td className={o.side === "buy" ? "up" : "down"}>{o.side}</td><td>{o.type}</td><td>{px(o.price)}</td><td>{o.filled} / {o.qty}</td>
                  <td title={o.reason ?? ""}>{o.status}</td></tr>
              ))}</tbody></table></div>
          )
        )}
        {tab === "fills" && (
          !me || me.history.length === 0 ? <p className="ex-none">No trades yet.</p> : (
            <div className="ex-table-wrap"><table className="ex-table"><thead><tr><th>Time</th><th>Market</th><th>Side</th><th>Price</th><th>Qty</th><th>Fee</th><th>Realized</th></tr></thead>
              <tbody>{me.history.map((h) => (
                <tr key={h.id}><td>{new Date(h.at).toLocaleString()}</td><td>{h.market}</td><td className={h.side === "buy" ? "up" : "down"}>{h.side}{h.maker ? " (maker)" : ""}</td>
                  <td>{px(h.price)}</td><td>{h.qty}</td><td>{px(h.fee)}</td><td className={(h.realized ?? 0) >= 0 ? "up" : "down"}>{h.realized != null ? signed(h.realized) : "—"}</td></tr>
              ))}</tbody></table></div>
          )
        )}
        {tab === "board" && <BoardView board={board} boardKey={boardKey} setBoardKey={setBoardKey} me={me} />}
        {tab === "rules" && m && <SpotRules m={m} />}
      </section>
      <p className="ex-back"><Link to="/pokemon">← Pokémon Market</Link> · <Link to="/pokemon/marketplace">Card Marketplace</Link></p>
      <BattleBox b={battle} onDone={() => setBattle(null)} />
    </div>
  );
}

function RefBadge({ m }: { m: ExMarket }) {
  const s = m.reference.status;
  return <span className={`ex-badge is-${s}`} title={s === "ok" ? "Fresh real-card evidence" : s === "stale" ? "Real-card evidence is old: the simulated market maker stopped quoting"
    : "Not enough reliable evidence: trading halted"}>{s === "ok" ? `🟢 Real price · ${m.reference.confidence ?? "—"} confidence` : s === "stale" ? "🟡 Reference stale" : "Halted"}</span>;
}

export function BoardView({ board, boardKey, setBoardKey, me }: { board: Board | null; boardKey: string; setBoardKey: (k: string) => void; me: Me | null }) {
  return (
    <div>
      <div className="ex-subtabs is-small">
        {[["equity", "Highest equity"], ["7d", "Best 7-day return"], ["30d", "Best 30-day return"], ["trades", "Most profitable trades"]].map(([k, l]) => (
          <button key={k} type="button" className={boardKey === k ? "is-on" : ""} onClick={() => setBoardKey(k)}>{l}</button>
        ))}
      </div>
      {!board || board.rows.length === 0 ? <p className="ex-none">Nobody ranked yet: make 3 trades to appear.</p> : (
        <div className="ex-table-wrap"><table className="ex-table"><tbody>{board.rows.map((r) => (
          <tr key={String(r.position) + String(r.username)}><td>{Number(r.position) === 1 ? "🥇" : Number(r.position) === 2 ? "🥈" : Number(r.position) === 3 ? "🥉" : `#${String(r.position)}`}</td><td><b>{String(r.username)}</b></td>
            {boardKey === "trades" ? <><td>{String(r.market)}</td><td>{String(r.qty)} @ {px(Number(r.price))}</td><td className="up">{signed(Number(r.profit))}</td></>
              : <><td>{px(Number(r.equity))} USDT</td>{"return_pct" in r && <td className={Number(r.return_pct) >= 0 ? "up" : "down"}>{String(r.return_pct)}%</td>}<td>{String(r.trades)} trades</td></>}
          </tr>
        ))}</tbody></table></div>
      )}
      {board && <ul className="ex-rules">{board.rules.map((r) => <li key={r}>{r}</li>)}</ul>}
      {me && <div className="ex-ach">{me.achievements.map((a) => <span key={a.key} className={a.earned ? "is-on" : ""} title={a.how}>{a.earned ? "🏅" : "🔒"} {a.name}</span>)}</div>}
    </div>
  );
}

function SpotRules({ m }: { m: ExMarket }) {
  return (
    <ul className="ex-rules">
      <li><b>Paper trading only.</b> Paper USDT has no value and can't be deposited, withdrawn or redeemed. Contracts are game pieces and don't own any card.</li>
      <li><b>Contract:</b> {m.rules.multiplier}, fixed at launch. {m.title}. English, graded {m.grade}.</li>
      <li><b>Market orders</b> fill right now at the best prices in the book (up to {m.rules.market_slippage_pct}% away; the rest is cancelled). <b>Limit orders</b> wait in the
        book at your price (within ±{m.rules.limit_band_pct}% of the real price) until someone matches. Whole contracts, {m.rules.min_qty}–{m.rules.max_qty} per order.</li>
      <li><b>The order book:</b> red = people selling (asks), green = people buying (bids). Best prices meet in the middle. Tap a price to use it.</li>
      <li><b>Fees:</b> {m.rules.taker_fee_bps / 100}% when your order fills straight away, {m.rules.maker_fee_bps / 100}% when it waited in the book. You can only sell what you own.</li>
      <li><b>Real price:</b> the median of recent verified PSA 10 sales of this exact card, averaged with Scrydex when they agree; one update moves it at most 15%. Your cards are valued at it.</li>
      <li><b>SIM:</b> the labelled simulated market maker quotes around the price; when the owner switches on the simulated auto-trader, its trades are labelled SIM too. Neither is a person or ever ranked.</li>
    </ul>
  );
}
