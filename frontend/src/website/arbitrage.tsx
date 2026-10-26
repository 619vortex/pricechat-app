import { useEffect, useState } from "react";
import { SitePage } from "./community";
import { pageHead } from "./seo";
import { memberApi } from "./members";
import "./tradingbot.css";
import "./arbitrage.css";
import { FundingAverages, FundingStart } from "./fundingstart";

export type EarnTab = "bot" | "lunch" | "funding" | "committee" | "kalshi";
const TABS: { key: EarnTab; href: string; icon: string; title: string; sub: string }[] = [
  { key: "bot", href: "/earn", icon: "🥝", title: "Trading Bot", sub: "Kiwi's signals on Hyperliquid" },
  { key: "lunch", href: "/earn/lunch", icon: "🍱", title: "Lunch Bot", sub: "pump.fun launches · paper" },
  { key: "funding", href: "/earn/funding", icon: "⚖️", title: "Funding Arb", sub: "funding gaps across 4 DEXs" },
  { key: "committee", href: "/earn/committee", icon: "🏛️", title: "AI Committee", sub: "research only · no trades" },
  { key: "kalshi", href: "/earn/kalshi", icon: "🎯", title: "Kalshi", sub: "your predictions, live · read-only" },
];

/** Earn's products as big tabs across the top of every Earn page: the trading bot first. */
export function EarnTabs({ current }: { current: EarnTab }) {
  return (
    <nav className="earn-tabs" aria-label="Earn products">
      {TABS.map((t) => (
        <a key={t.key} href={t.href} className={current === t.key ? "is-on" : ""} aria-current={current === t.key ? "page" : undefined}>
          <span className="earn-tab-icon" aria-hidden>{t.icon}</span>
          <span className="earn-tab-text"><b>{t.title}</b><small>{t.sub}</small></span>
        </a>
      ))}
    </nav>
  );
}

/** Polls a public Earn endpoint every `ms`; keeps the last good answer if a refresh fails. */
function usePoll<T>(path: string, ms: number): [T | null, string | null] {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => memberApi<T>(path).then((d) => { if (alive) { setData(d); setErr(null); } })
      .catch((e: unknown) => { if (alive) setErr(e instanceof Error ? e.message : "Couldn't load this right now."); });
    void load();
    // nobody looking: no polling; coming back refreshes at once
    const t = window.setInterval(() => { if (!document.hidden) void load(); }, ms);
    const vis = () => { if (!document.hidden) void load(); };
    document.addEventListener("visibilitychange", vis);
    return () => { alive = false; window.clearInterval(t); document.removeEventListener("visibilitychange", vis); };
  }, [path, ms]);
  return [data, err];
}

export type LunchStrategy = "starter" | "graduation";
export type LunchStats = { trades: number; wins: number; win_rate: number | null; pnl_sol: number; avg_pnl_sol: number | null; best_sol: number | null;
  worst_sol: number | null; by_exit: Record<string, number>; open: number; open_value_sol: number; open_cost_sol: number };
const DISH: Record<LunchStrategy, [string, string, string]> = {
  starter: ["🥗", "Starter", "early momentum on brand-new launches"],
  graduation: ["🍰", "Graduation Run", "a nearly full curve, held to graduation"],
};

interface LunchTrade {
  id: number; strategy?: LunchStrategy; mint: string; symbol: string; name: string; image_uri: string | null; status: "open" | "closed"; launched_at: string; opened_at: string;
  closed_at: string | null; entry_sol: number; entry_mc_sol: number; value_sol: number | null; pnl_sol: number | null; pnl_pct: number | null;
  exit_reason: string | null; signal: { real_sol: number; growth_pct: number; minutes_after_launch: number } | null; awaiting_pool: boolean;
  pump_url: string; solscan_url: string;
}
export interface Lunch {
  mode: "paper";
  stats: LunchStats;
  by_strategy?: Partial<Record<LunchStrategy, LunchStats>>;
  open: LunchTrade[]; closed: LunchTrade[];
  graduations: { measured: number; avg_gap_pct: number | null; median_gap_pct: number | null; positive: number; after_costs_positive: number;
                 recent: { mint: string; symbol: string; curve_price: number; pool_price: number | null; gap_pct: number | null; dex: string | null; at: string }[] };
  days: { day: string; launches_seen: number; entries: number }[];
  scoreboard?: Scoreboard;
  menu: { size_sol: number; watch_minutes: number; min_real_sol: number; min_growth_pct: number; target_pct: number; stop_pct: number; timeout_minutes: number; max_open: number;
          graduation?: { min_progress_pct: number; max_progress_pct: number; min_rise_pts: number; stop_pct: number; timeout_minutes: number; max_open: number; near_now: number } };
  costs: { curve_fee_pct: number; pool_fee_pct: number; tx_cost_sol: number };
  scanner: { last_run_at: string | null; watching: number; rate_limited_until: string | null; last_error: string | null;
             live?: { connected: boolean; events: number; fast_check_seconds: number; fast_triggers: number; trade_feed: boolean } | null };
}

export interface SeriesPoint { day: string; trades: number; wins: number; pnl_sol: number; cumulative: number }
export interface GapStat { count: number; avg_gap_pct: number | null; median_gap_pct: number | null; positive: number }
export interface Scoreboard { days: number; series: Partial<Record<LunchStrategy, SeriesPoint[]>>; gaps: { all: GapStat; last_7d: GapStat; costs_pct: number } }

// The graduation-gap measurement stays hidden until its pool data is validated (some pools' first candle reads ~9x the curve);
// the backend keeps measuring meanwhile.
export const SHOW_GRADUATIONS = false;
// The scoreboard's one-line gap summary (implausible >±50% candles already left out server-side); flip off to hide it too.
export const SHOW_GAP_STAT = false;  // off until the pool data is trusted (the detailed gap table is hidden for the same reason)
const EXIT: Record<string, string> = { graduated: "Graduated", target: "Target", stop: "Stop", timeout: "Timeout", gone: "Gone" };
const signed = (v: number | null | undefined, d = 3) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}`);
/** a P&L in SOL: always signed, so a gain never reads like a balance */
const pnlSol = (v: number | null | undefined) => (v == null ? "—" : `${signed(v)} SOL`);
const pct = (v: number | null | undefined) => (v == null ? "—" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)}%`);
const ago = (iso: string | null) => {
  if (!iso) return "not yet";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  return s < 90 ? `${Math.round(s)}s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
};

/** /earn/lunch: Lunch Bot, paper trading pump.fun launches at real on-chain prices. */
export function LunchPage() {
  const [data, err] = usePoll<Lunch>("/members/arbitrage/lunch", 30000);
  return (
    <SitePage {...pageHead("earn/lunch")}>
      <div className="tb arb">
        <EarnTabs current="lunch" />
        {err && <p className="site-error" role="alert">{err}</p>}
        {data ? <LunchBot d={data} /> : !err && <p className="arb-muted">Loading Lunch Bot…</p>}
      </div>
    </SitePage>
  );
}

export interface FundingLeg { venue: string; rate_raw: number; interval_hours: number; interval_source: string; kind: string; apr_pct: number }
export interface FundingBoard {
  as_of: string; pairs_found: number; note: string;
  venues: Record<string, { ok: boolean; symbols: number; error: string | null; interval_hours: number[]; interval_source: string | null }>;
  rows: { symbol: string; legs: FundingLeg[]; short: string; long: string; gross_spread_apr_pct: number; capital_apr_pct?: number; stats?: FundingStats | null }[];
  assumptions?: { leverage: number; buffer: number; capital_per_notional: number; stability_k: number; history_days: number };
}
export interface FundingStats { hours: number; mean_apr_pct?: number; std_apr_pct?: number; z?: number | null; paid_share?: number; score_pct?: number }
const VENUE: Record<string, string> = { hyperliquid: "Hyperliquid", lighter: "Lighter", orderly: "Orderly", backpack: "Backpack" };
const apr = (v: number) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)}%`;

/** /earn/funding: Funding Arb, a read-only board of perp funding across four venues. */
export function FundingPage() {
  const [data, err] = usePoll<FundingBoard>("/members/arbitrage/funding", 60000);
  return (
    <SitePage {...pageHead("earn/funding")}>
      <div className="tb arb">
        <EarnTabs current="funding" />
        {err && <p className="site-error" role="alert">{err}</p>}
        {data ? <FundingArb d={data} /> : !err && <p className="arb-muted">Reading four venues…</p>}
      </div>
    </SitePage>
  );
}

const FUNDING_ROWS = 12;

export function FundingArb({ d }: { d: FundingBoard }) {
  const [all, setAll] = useState(false);
  const [pick, setPick] = useState<string | null>(null);
  const use = (sym: string) => {
    setPick(sym);
    document.getElementById("arb-start-title")?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  };
  const rows = all ? d.rows : d.rows.slice(0, FUNDING_ROWS);
  return (
    <section className="arb-bot" aria-label="Funding Arb">
      <header className="arb-head">
        <span className="arb-big" aria-hidden>⚖️</span>
        <div>
          <h1>Funding Arb</h1>
          <p>Short the perp where funding is highest, long the same perp where it's lowest: price moves cancel out, both funding payments are yours.
            <b className="arb-paper"> SCANNER ONLY</b>: no trades.</p>
          <p className="arb-muted">Updated {ago(d.as_of)} · {d.pairs_found} perps listed on 2 or more venues</p>
        </div>
      </header>
      <FundingAverages d={d} />
      <div className="arb-stats">
        {Object.entries(d.venues).map(([k, v]) => (
          <div key={k} className={`arb-stat ${v.ok ? "" : "is-bad"}`}>
            <span>{VENUE[k] ?? k}</span>
            <b>{v.ok ? `${v.symbols} perps` : "offline"}</b>
            <small>{v.ok ? `funding every ${v.interval_hours.map((h) => `${h}h`).join(" / ")}` : v.error}</small>
          </div>
        ))}
      </div>
      <section className="arb-card">
        <h2>The steadiest funding gaps</h2>
        {d.rows.length === 0 ? <p className="arb-muted">No perp is listed on two venues that answered.</p> : (
          <table className="arb-table">
            <thead><tr><th>Perp</th><th>Gap now</th><th>On capital</th><th>7-day avg ± swing</th><th>Now vs avg</th><th>Paid</th>
              <th>Short on</th><th>Long on</th><th>All venues</th><th><span className="arb-sr">Start</span></th></tr></thead>
            <tbody>{rows.map((r) => {
              const hi = r.legs.find((l) => l.venue === r.short)!, lo = r.legs.find((l) => l.venue === r.long)!;
              const st = r.stats && r.stats.mean_apr_pct != null ? r.stats : null;
              return (
                <tr key={r.symbol}>
                  <td><b>{r.symbol.replace("-PERP", "")}</b></td>
                  <td className="is-good">{apr(r.gross_spread_apr_pct)}</td>
                  <td>{r.capital_apr_pct == null ? "—" : apr(r.capital_apr_pct)}</td>
                  <td>{st ? `${apr(st.mean_apr_pct!)} ± ${st.std_apr_pct!.toFixed(1)}%` : <span className="arb-muted">—</span>}</td>
                  <td className={st?.z != null && Math.abs(st.z) >= 2 ? "is-bad" : ""}
                      title={st?.z != null && Math.abs(st.z) >= 2 ? "An extreme reading: it usually drifts back toward the average" : undefined}>
                    {st?.z == null ? "—" : `${st.z > 0 ? "+" : ""}${st.z.toFixed(1)}σ`}</td>
                  <td>{st?.paid_share == null ? "—" : `${Math.round(st.paid_share * 100)}%`}</td>
                  <td>{VENUE[r.short]} <small className="arb-muted">{apr(hi.apr_pct)}</small></td>
                  <td>{VENUE[r.long]} <small className="arb-muted">{apr(lo.apr_pct)}</small></td>
                  <td className="arb-legs">{r.legs.map((l) => (
                    <span key={l.venue} title={`${l.rate_raw} per ${l.interval_hours}h (${l.interval_source}, ${l.kind})`}>
                      {VENUE[l.venue] ?? l.venue} {apr(l.apr_pct)} <small>/{l.interval_hours}h</small>
                    </span>
                  ))}</td>
                  <td><button type="button" className="arb-use" onClick={() => use(r.symbol)} aria-label={`Use ${r.symbol.replace("-PERP", "")} in Start`}>Use</button></td>
                </tr>
              );
            })}</tbody>
          </table>
        )}
        {d.rows.length > FUNDING_ROWS && (
          <button type="button" className="arb-more" onClick={() => setAll((a) => !a)} aria-expanded={all}>
            {all ? `Show the top ${FUNDING_ROWS}` : `Show all ${d.rows.length} pairs`}</button>
        )}
        <p className="arb-fine">APR = the venue's rate ÷ its own funding interval × 8,760 hours. Lighter shows its last settled rate; the others show the
          rate for the running interval.{d.assumptions && <> “On capital”: both legs need their own margin
          ({d.assumptions.leverage}x each plus a {Math.round(d.assumptions.buffer * 100)}% buffer). Ordered by the steadiest carry: the
          {` ${d.assumptions.history_days}`}-day average minus {d.assumptions.stability_k}× its swing, so a gap that pays every hour beats a bigger one that comes and goes.
          “Now vs avg” beyond ±2σ is an extreme that usually fades.</>} {d.note}</p>
      </section>
      <FundingStart d={d} pick={pick} onPick={setPick} />
      <p className="tb-disclose">Not financial advice. Funding rates change every interval, gaps close, and holding two positions on two venues carries
        fees, liquidation and venue risk.</p>
    </section>
  );
}

export function LunchBot({ d }: { d: Lunch }) {
  const s = d.stats, g = d.graduations, m = d.menu;
  const today = d.days[d.days.length - 1];
  return (
    <section className="arb-bot" aria-label="Lunch Bot">
      <header className="arb-head">
        <span className="arb-big" aria-hidden>🍱</span>
        <div>
          <h1>Lunch Bot</h1>
          <p>Catches pump.fun launches while they're hot, at the real prices on Solana. <b className="arb-paper">PAPER TRADING</b>: no real SOL is spent, ever.</p>
          <p className="arb-muted">
            Scanner {d.scanner.last_run_at ? `ran ${ago(d.scanner.last_run_at)}` : "starting"} · watching {d.scanner.watching} launches
            {today ? ` · ${today.launches_seen.toLocaleString()} launches seen today` : ""}
            {d.scanner.rate_limited_until && " · pump.fun asked us to slow down, back shortly"}
            {d.scanner.live?.connected && ` · ⚡ live feed on: new launches in seconds, entries checked every ${d.scanner.live.fast_check_seconds}s`}
          </p>
        </div>
      </header>

      <div className="arb-stats">
        <Stat label="Paper P&L" value={pnlSol(s.pnl_sol)} tone={s.pnl_sol > 0 ? "good" : s.pnl_sol < 0 ? "bad" : ""} />
        <Stat label="Trades closed" value={String(s.trades)} />
        <Stat label="Win rate" value={s.win_rate == null ? "—" : `${s.win_rate}%`} />
        <Stat label="Open now" value={`${s.open}`} sub={s.open ? `${signed(s.open_value_sol - s.open_cost_sol)} SOL unrealized` : undefined} />
      </div>

      {d.scoreboard ? <LunchScoreboard sb={d.scoreboard} by={d.by_strategy} /> : d.by_strategy && (
        <div className="arb-dishes" aria-label="Strategies">
          {(Object.keys(DISH) as LunchStrategy[]).map((k) => {
            const st = d.by_strategy?.[k];
            const [icon, name, what] = DISH[k];
            return (
              <article key={k} className="arb-dish">
                <header><span aria-hidden>{icon}</span><div><b>{name}</b><small>{what}</small></div></header>
                {st ? (
                  <p>
                    <b className={st.pnl_sol > 0 ? "is-good" : st.pnl_sol < 0 ? "is-bad" : ""}>{pnlSol(st.pnl_sol)}</b>
                    {" · "}{st.trades} closed{st.win_rate != null ? ` · ${st.win_rate}% wins` : ""} · {st.open} open
                  </p>
                ) : <p className="arb-muted">Not started yet.</p>}
              </article>
            );
          })}
        </div>
      )}

      <div className="arb-cols">
        <section className="arb-card">
          <h2>Open lunches</h2>
          {d.open.length === 0 ? <p className="arb-muted">Nothing on the plate. Lunch Bot buys when a launch shows real momentum.</p> : <Trades rows={d.open} />}
        </section>
        <section className="arb-card">
          <h2>The menu</h2>
          <h3 className="arb-dish-title">🥗 Starter</h3>
          <ol className="arb-menu">
            <li>Watch every new SOL-quoted pump.fun launch for {m.watch_minutes} minutes.</li>
            <li>Buy {m.size_sol} SOL when buyers have put at least {m.min_real_sol} SOL in and it's up {m.min_growth_pct}% since we first saw it.</li>
            <li>Sell when it graduates to its PumpSwap pool, at +{m.target_pct}%, at {m.stop_pct}%, or after {m.timeout_minutes} minutes.</li>
          </ol>
          {m.graduation && (
            <>
              <h3 className="arb-dish-title">🍰 Graduation Run</h3>
              <ol className="arb-menu">
                <li>Track pump.fun's fullest curves ({m.graduation.near_now} right now).</li>
                <li>Buy {m.size_sol} SOL when a curve is {m.graduation.min_progress_pct}–{m.graduation.max_progress_pct}% full and still filling
                  (up {m.graduation.min_rise_pts}+ points in a minute).</li>
                <li>Hold to graduation and sell in its PumpSwap pool; stop at {m.graduation.stop_pct}%, or out after {m.graduation.timeout_minutes} minutes.</li>
              </ol>
            </>
          )}
          <p className="arb-fine">Assumed costs: {d.costs.curve_fee_pct}% per curve trade, {d.costs.pool_fee_pct}% per pool trade, {d.costs.tx_cost_sol} SOL per transaction.
            Paper entries are at the price the scanner saw, up to a minute after the move; a real bot would also lose some races to other bots.</p>
        </section>
      </div>

      {SHOW_GRADUATIONS && <section className="arb-card">
        <h2>The "graduation arbitrage", measured</h2>
        <p>Strategy write-ups say buying before a launch graduates and selling the moment it migrates is a guaranteed profit. Lunch Bot checks it on every graduation it can price:
          the curve's last price vs the first price its new pool traded at.</p>
        {g.measured === 0 ? <p className="arb-muted">No graduations measured yet. They're rare (most launches never fill their curve); results appear here as they happen.</p> : (
          <>
            <p className="arb-verdict">
              {g.measured} graduations: average gap <b>{pct(g.avg_gap_pct)}</b>, median <b>{pct(g.median_gap_pct)}</b>.
              {" "}{g.positive} opened above the curve; {g.after_costs_positive} beat the fees.
            </p>
            <table className="arb-table">
              <thead><tr><th>Token</th><th>Curve's last price</th><th>Pool's first price</th><th>Gap</th></tr></thead>
              <tbody>{g.recent.map((r) => (
                <tr key={r.mint}><td><a href={`https://pump.fun/coin/${r.mint}`} target="_blank" rel="noopener noreferrer">${r.symbol}</a></td>
                  <td>{r.curve_price.toExponential(3)}</td><td>{r.pool_price == null ? "no pool yet" : r.pool_price.toExponential(3)}</td>
                  <td>{r.gap_pct == null && r.pool_price != null ? "bad candle data" : pct(r.gap_pct)}</td></tr>
              ))}</tbody>
            </table>
            <p className="arb-fine">Prices in SOL per token. The curve's last price is computed from pump.fun's curve maths; the pool's is the open of its first
              one-minute candle (GeckoTerminal). Gaps over ±50% are treated as bad candle data and left out.</p>
          </>
        )}
      </section>}

      {d.closed.length > 0 && (
        <section className="arb-card">
          <h2>Recent lunches</h2>
          <Trades rows={d.closed} />
          {Object.keys(s.by_exit).length > 0 && (
            <p className="arb-fine">Exits: {Object.entries(s.by_exit).map(([k, n]) => `${EXIT[k] ?? k} ${n}`).join(" · ")} · best {pnlSol(s.best_sol)} · worst {pnlSol(s.worst_sol)}</p>
          )}
        </section>
      )}
      <p className="tb-disclose">Paper trading on real data, not financial advice. Most pump.fun launches go to zero; a real version of this bot would risk the whole stake on each trade.</p>
    </section>
  );
}

const shortDay = (iso: string) => new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });

/** Per-strategy scorecards (all-time stats from SQL) with each one's 30-day cumulative paper P&L, plus the graduation gap. */
export function LunchScoreboard({ sb, by }: { sb: Scoreboard; by?: Lunch["by_strategy"] }) {
  const g7 = sb.gaps.last_7d, ga = sb.gaps.all;
  return (
    <section className="arb-card arb-score" aria-label="Scoreboard">
      <h2>Scoreboard <b className="arb-paper">PAPER</b> <small className="arb-muted">· chart: last {sb.days} days, UTC</small></h2>
      <div className="arb-score-grid">
        {(Object.keys(DISH) as LunchStrategy[]).map((k) => {
          const st = by?.[k], pts = sb.series[k] ?? [];
          const [icon, name, what] = DISH[k];
          const total = pts.length ? pts[pts.length - 1].cumulative : 0;
          return (
            <article key={k} className="arb-dish arb-scorecard" aria-label={`${name} scorecard`}>
              <header><span aria-hidden>{icon}</span><div><b>{name}</b><small>{what}</small></div></header>
              <p className="arb-score-pnl"><b className={tone(st?.pnl_sol)}>{pnlSol(st?.pnl_sol ?? null)}</b> <small className="arb-muted">all-time paper P&L</small></p>
              <dl className="arb-score-kv">
                <div><dt>Win rate</dt><dd>{st?.win_rate == null ? "—" : `${st.win_rate}%`}</dd></div>
                <div><dt>Trades</dt><dd>{st ? st.trades : 0}</dd></div>
                <div><dt>Best</dt><dd className={tone(st?.best_sol)}>{signed(st?.best_sol ?? null)}</dd></div>
                <div><dt>Worst</dt><dd className={tone(st?.worst_sol)}>{signed(st?.worst_sol ?? null)}</dd></div>
              </dl>
              <PnlLine pts={pts} label={`${name}: ${signed(total)} SOL over ${sb.days} days`} />
              {pts.length > 0 && <p className="arb-score-axis"><span>{shortDay(pts[0].day)}</span><span>{signed(total)} SOL in {sb.days} d</span>
                <span>{shortDay(pts[pts.length - 1].day)}</span></p>}
            </article>
          );
        })}
      </div>
      {SHOW_GAP_STAT && (
        <p className="arb-score-gap">
          <b>Curve → pool gap at graduation</b>{" "}
          {ga.count === 0 ? <span className="arb-muted">none measured yet</span> : <>
            median <b>{pct(g7.median_gap_pct)}</b> over the last 7 days ({g7.count}), <b>{pct(ga.median_gap_pct)}</b> all time ({ga.count}; avg {pct(ga.avg_gap_pct)}).
            {" "}<span className="arb-muted">Round-trip fees are about {sb.gaps.costs_pct}%: the gap has to beat that to pay.</span></>}
        </p>
      )}
    </section>
  );
}

const tone = (v: number | null | undefined) => (v == null ? "" : v > 0 ? "is-good" : v < 0 ? "is-bad" : "");

/** Cumulative P&L as an inline SVG line with a zero baseline (no chart library). */
export function PnlLine({ pts, label }: { pts: SeriesPoint[]; label: string }) {
  const W = 300, H = 72, P = 4;
  if (pts.length < 2) return <p className="arb-muted">No closed trades in the window yet.</p>;
  const ys = pts.map((p) => p.cumulative);
  const lo = Math.min(0, ...ys), hi = Math.max(0, ...ys), span = hi - lo || 1;
  const x = (i: number) => (i / (pts.length - 1)) * W;
  const y = (v: number) => P + (1 - (v - lo) / span) * (H - 2 * P);
  const line = pts.map((p, i) => `${x(i).toFixed(1)},${y(p.cumulative).toFixed(1)}`).join(" ");
  const last = ys[ys.length - 1];
  return (
    <svg className={`arb-spark ${last > 0 ? "is-up" : last < 0 ? "is-down" : ""}`} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}>
      <title>{label}</title>
      <polygon className="arb-spark-area" points={`0,${y(0).toFixed(1)} ${line} ${W},${y(0).toFixed(1)}`} />
      <line className="arb-spark-zero" x1="0" x2={W} y1={y(0)} y2={y(0)} vectorEffect="non-scaling-stroke" />
      <polyline className="arb-spark-line" points={line} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function Stat({ label, value, sub, tone = "" }: { label: string; value: string; sub?: string; tone?: string }) {
  return <div className={`arb-stat ${tone ? `is-${tone}` : ""}`}><span>{label}</span><b>{value}</b>{sub && <small>{sub}</small>}</div>;
}

function Trades({ rows }: { rows: LunchTrade[] }) {
  return (
    <table className="arb-table">
      <thead><tr><th>Token</th><th>In (SOL)</th><th>Now / out (SOL)</th><th>P&L</th></tr></thead>
      <tbody>{rows.map((t) => (
        <tr key={t.id}>
          <td>{t.strategy && <span className="arb-dish-chip" title={DISH[t.strategy][1]}>{DISH[t.strategy][0]}</span>}
            <a href={t.pump_url} target="_blank" rel="noopener noreferrer">${t.symbol}</a>
            <small className="arb-muted"> {t.status === "open" ? (t.awaiting_pool ? "graduated, pricing the pool" : `bought ${ago(t.opened_at)}`) : EXIT[t.exit_reason ?? ""] ?? t.exit_reason}</small></td>
          <td>{t.entry_sol.toFixed(3)}</td>
          <td>{t.value_sol == null ? "—" : t.value_sol.toFixed(3)}</td>
          <td className={t.pnl_sol == null ? "" : t.pnl_sol > 0 ? "is-good" : t.pnl_sol < 0 ? "is-bad" : ""}>{pct(t.pnl_pct)}</td>
        </tr>
      ))}</tbody>
    </table>
  );
}
