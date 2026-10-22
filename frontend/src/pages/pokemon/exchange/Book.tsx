import { useMemo, useState } from "react";
import { px, type Level } from "./api";

/** The order book (asks on top, best prices meeting in the middle, bids below) and recent trades, shared by spot and
 *  perps. Clicking a price fills the order form with a limit order at that price. */

export interface BookTrade { id: number; price: number; qty: number; side: "buy" | "sell"; at: string; simulated: boolean; kind?: string }

export function OrderBook({ book, trades, mid, midLabel, midUp, onPick, rows = 8 }: {
  book: { bids: Level[]; asks: Level[] } | null; trades: BookTrade[] | null; mid: number | null | undefined; midLabel: string; midUp: boolean;
  onPick: (side: "buy" | "sell", price: number) => void; rows?: number;
}) {
  const [tab, setTab] = useState<"book" | "trades">("book");
  const asks = useMemo(() => (book?.asks ?? []).slice(0, rows).reverse(), [book, rows]);
  const bids = useMemo(() => (book?.bids ?? []).slice(0, rows), [book, rows]);
  const max = Math.max(1, ...asks.map((l) => l.qty), ...bids.map((l) => l.qty));
  const bestBid = bids[0]?.price, bestAsk = book?.asks[0]?.price;
  const spread = bestBid && bestAsk ? bestAsk - bestBid : null;

  const row = (l: Level, side: "ask" | "bid") => (
    <li key={l.price} style={{ "--w": `${(l.qty / max) * 100}%` } as React.CSSProperties} title={side === "ask" ? "Click: buy at this price (limit)" : "Click: sell at this price (limit)"}
      onClick={() => onPick(side === "ask" ? "buy" : "sell", l.price)}>
      <span className={side === "ask" ? "down" : "up"}>{px(l.price)}</span>
      <span>{l.qty}{l.simulated_qty > 0 && <em title={`${l.simulated_qty} from the simulated market maker`}>SIM</em>}</span>
      <span>{px(l.price * l.qty)}</span>
    </li>
  );

  return (
    <section className="ex-panel ex-book" aria-label="Order book">
      <div className="ex-subtabs">
        <button type="button" className={tab === "book" ? "is-on" : ""} onClick={() => setTab("book")}>Order book</button>
        <button type="button" className={tab === "trades" ? "is-on" : ""} onClick={() => setTab("trades")}>Recent trades</button>
      </div>
      {tab === "book" ? (
        <div className="ex-ob">
          <div className="ex-ob-head"><span>Price</span><span>Qty</span><span>Total</span></div>
          <p className="ex-ob-side down">Sellers (asks)</p>
          <ul className="ex-asks">{asks.map((l) => row(l, "ask"))}{asks.length === 0 && <li className="ex-none">No sellers</li>}</ul>
          <div className="ex-spread">
            <b className={midUp ? "up" : "down"}>{px(mid)}</b>
            <span>{midLabel}{spread != null && <> · spread {px(spread)}</>}</span>
          </div>
          <ul className="ex-bids">{bids.map((l) => row(l, "bid"))}{bids.length === 0 && <li className="ex-none">No buyers</li>}</ul>
          <p className="ex-ob-side up">Buyers (bids)</p>
          <p className="ex-fine">Tap a price to place a limit order there. <em>SIM</em> = the labelled simulated market maker.</p>
        </div>
      ) : (
        <ul className="ex-trades">
          <li className="ex-ob-head"><span>Price</span><span>Qty</span><span>Time</span></li>
          {(trades ?? []).slice(0, 30).map((t) => (
            <li key={t.id}><span className={t.side === "buy" ? "up" : "down"}>{px(t.price)}</span>
              <span>{t.qty}{t.kind === "liquidation" && <em title="Liquidation: taken over by the insurance fund">LIQ</em>}</span>
              <span>{new Date(t.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}{t.simulated && <em>SIM</em>}</span></li>
          ))}
          {trades && trades.length === 0 && <li className="ex-none">No trades yet</li>}
        </ul>
      )}
    </section>
  );
}
