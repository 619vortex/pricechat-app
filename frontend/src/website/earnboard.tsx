import { useEffect, useState } from "react";
import { memberApi } from "./members";
import { money } from "./pnl";

/** Earn leaderboard: every member with a valid Hyperliquid referral (our code), their trades and P&L over 30 days.
 *  Hyperliquid's own numbers, ranked by the server; "—" where Hyperliquid couldn't be read. */

export interface BoardRow {
  rank: number;
  username: string;
  me: boolean;
  trades_30d: number | null;
  trades_capped: boolean;
  pnl_30d: number | null;
  pnl_all: number | null;
}
interface Board { items: BoardRow[]; me: BoardRow | null; count: number; metric: string }

const pnlCell = (n: number | null) => n == null ? <td className="eb-num">—</td>
  : <td className={`eb-num ${n > 0 ? "is-up" : n < 0 ? "is-down" : ""}`}>{money(n, true)}</td>;

/** `minTraders`: on the sign-up page the table waits until that many members trade (a board of 2-3 small accounts says
 *  nothing about the bot); connected members always see it. */
export function EarnLeaderboard({ minTraders = 0 }: { minTraders?: number }) {
  const [b, setB] = useState<Board | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => { void memberApi<Board>("/members/trading-bot/leaderboard").then(setB).catch(() => setErr(true)); }, []);
  if (err) return null;
  return (
    <section className="eb" aria-label="Leaderboard">
      <header className="eb-head">
        <h2>Leaderboard</h2>
        {b && <span className="eb-sub">{b.count.toLocaleString()} {b.count === 1 ? "trader" : "traders"} · last 30 days</span>}
      </header>
      {!b ? <div className="tb-skel" aria-busy="true" />
        : b.items.length === 0 ? <p className="eb-empty">No one has joined Hyperliquid with our link yet. Be the first.</p>
        : b.count < minTraders ? <p className="eb-empty">The leaderboard opens once {minTraders} members are trading. Be one of the first.</p>
        : (
          <div className="eb-scroll">
            <table className="eb-table">
              <thead><tr><th scope="col">#</th><th scope="col">Member</th><th scope="col" className="eb-num">Trades</th>
                <th scope="col" className="eb-num">P&amp;L 30d</th><th scope="col" className="eb-num eb-all">All time</th></tr></thead>
              <tbody>
                {b.items.map((r) => (
                  <tr key={r.username} className={r.me ? "is-me" : undefined}>
                    <td className="eb-rank">{r.rank <= 3 ? ["🥇", "🥈", "🥉"][r.rank - 1] : r.rank}</td>
                    <td className="eb-name">{r.username}{r.me && <span className="eb-you">you</span>}</td>
                    <td className="eb-num">{r.trades_30d == null ? "—" : `${r.trades_30d.toLocaleString()}${r.trades_capped ? "+" : ""}`}</td>
                    {pnlCell(r.pnl_30d)}
                    {pnlCell(r.pnl_all)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      {b?.me && b.me.rank > 10 && <p className="eb-mine">You're <b>#{b.me.rank}</b> of {b.count}.</p>}
      {b && b.count >= minTraders && <p className="eb-fine">{b.metric}</p>}
    </section>
  );
}
