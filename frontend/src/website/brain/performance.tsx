import { useMemo, useState } from "react";
import { STRAT, fmt, usePoll, type Overview, type Perf, type Trade } from "./api";

/* PERFORMANCE: the member's own closed positions. Live and paper are never combined; manual (test) setups are excluded unless
   asked for; small samples are flagged instead of ranked; backtests are a separate, HYPOTHETICAL table. */

type Mode = "LIVE" | "PAPER";

function stats(ts: Trade[]) {
  const pnl = ts.map((t) => t.net);
  const wins = pnl.filter((x) => x > 0), losses = pnl.filter((x) => x <= 0);
  let eq = 0, peak = 0, dd = 0;
  const curve = ts.map((t) => { eq += t.net; peak = Math.max(peak, eq); dd = Math.max(dd, peak - eq); return { t: t.closed_at!, eq, dd: eq - peak }; });
  const days = new Map<string, number>();
  for (const t of ts) { const d = (t.closed_at ?? "").slice(0, 10); days.set(d, (days.get(d) ?? 0) + t.net); }
  const daily = [...days.values()];
  const mean = daily.length ? daily.reduce((a, b) => a + b, 0) / daily.length : 0;
  const sd = daily.length > 1 ? Math.sqrt(daily.reduce((a, b) => a + (b - mean) ** 2, 0) / (daily.length - 1)) : 0;
  return {
    n: ts.length, net: pnl.reduce((a, b) => a + b, 0), fees: ts.reduce((a, t) => a + t.fees, 0), funding: ts.reduce((a, t) => a + (t.funding ?? 0), 0),
    win: ts.length ? (wins.length / ts.length) * 100 : null, avgW: wins.length ? wins.reduce((a, b) => a + b, 0) / wins.length : null,
    avgL: losses.length ? losses.reduce((a, b) => a + b, 0) / losses.length : null,
    pf: wins.length && losses.length && losses.reduce((a, b) => a + b, 0) < 0 ? wins.reduce((a, b) => a + b, 0) / -losses.reduce((a, b) => a + b, 0) : null,
    dd, curve, days, sharpe: daily.length >= 20 && sd > 0 ? (mean / sd) * Math.sqrt(365) : null, dailyN: daily.length,
    slip: ts.filter((t) => (t as Trade & { slippage_bps?: number | null }).slippage_bps != null).map((t) => (t as Trade & { slippage_bps: number }).slippage_bps),
  };
}

function Line({ pts, color, label, zero = true }: { pts: number[]; color: string; label: string; zero?: boolean }) {
  if (pts.length < 2) return <p className="kb-empty">Needs at least 2 closed trades.</p>;
  const W = 600, H = 140, min = Math.min(...pts, zero ? 0 : Infinity), max = Math.max(...pts, zero ? 0 : -Infinity);
  const Y = (v: number) => H - 4 - ((v - min) / (max - min || 1)) * (H - 8);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="kbpf-line" role="img" aria-label={label}>
      {zero && <line x1={0} x2={W} y1={Y(0)} y2={Y(0)} stroke="rgba(255,255,255,.12)" />}
      <polyline fill="none" stroke={color} strokeWidth={2} points={pts.map((v, i) => `${(i / (pts.length - 1)) * W},${Y(v)}`).join(" ")} />
    </svg>
  );
}

function Calendar({ days }: { days: Map<string, number> }) {
  const end = new Date(); const cells: { d: string; v: number | undefined }[] = [];
  for (let i = 83; i >= 0; i--) { const d = new Date(end.getTime() - i * 86400_000).toISOString().slice(0, 10); cells.push({ d, v: days.get(d) }); }
  const max = Math.max(...[...days.values()].map(Math.abs), 1e-9);
  return (
    <div className="kbpf-cal" role="img" aria-label="Daily P&L, last 12 weeks">
      {cells.map((c) => <span key={c.d} title={`${c.d}: ${c.v == null ? "no closed trades" : fmt.usd(c.v)}`}
        style={{ background: c.v == null ? undefined : c.v >= 0 ? `rgba(0,229,143,${0.25 + 0.75 * Math.abs(c.v) / max})` : `rgba(255,84,104,${0.25 + 0.75 * Math.abs(c.v) / max})` }} />)}
    </div>
  );
}

function Hist({ vals }: { vals: number[] }) {
  if (vals.length < 3) return <p className="kb-empty">Needs at least 3 closed trades.</p>;
  const lo = Math.min(...vals), hi = Math.max(...vals), bins = 10, w = (hi - lo) / bins || 1;
  const counts = Array.from({ length: bins }, (_, i) => vals.filter((v) => (i === bins - 1 ? v >= lo + i * w : v >= lo + i * w && v < lo + (i + 1) * w)).length);
  const max = Math.max(...counts, 1);
  return (
    <div className="kbpf-hist" role="img" aria-label="Distribution of trade results">
      {counts.map((c, i) => <span key={i} title={`${fmt.usd(lo + i * w)} to ${fmt.usd(lo + (i + 1) * w)}: ${c} trades`}
        className={lo + (i + 0.5) * w >= 0 ? "is-up" : "is-down"} style={{ height: `${(c / max) * 100}%` }} />)}
    </div>
  );
}

function groupBy(ts: Trade[], key: (t: Trade) => string) {
  const g = new Map<string, Trade[]>();
  for (const t of ts) g.set(key(t), [...(g.get(key(t)) ?? []), t]);
  return [...g.entries()].map(([k, v]) => {
    const s = stats(v);
    const pnl = v.map((x) => x.net), m = s.net / v.length;
    const sd = v.length > 1 ? Math.sqrt(pnl.reduce((a, b) => a + (b - m) ** 2, 0) / (v.length - 1)) : 0;
    return { k, n: v.length, net: s.net, win: s.win, avg: m, ci: v.length >= 5 ? [m - 1.645 * sd / Math.sqrt(v.length), m + 1.645 * sd / Math.sqrt(v.length)] : null };
  }).sort((a, b) => (b.n >= 5 ? 1 : 0) - (a.n >= 5 ? 1 : 0) || b.net - a.net);
}

function Table({ title, rows }: { title: string; rows: ReturnType<typeof groupBy> }) {
  return (
    <div className="kbpf-tbl"><h4>{title}</h4>
      <table><thead><tr><th /><th>Trades</th><th>Net</th><th>Win</th><th>Avg / trade (90% range)</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.k}><td>{r.k}</td><td>{r.n}</td><td className={r.net >= 0 ? "is-up" : "is-down"}>{fmt.pnl(r.net)}</td>
          <td>{r.win != null ? `${fmt.n(r.win, 0)}%` : "—"}</td>
          <td>{fmt.usd(r.avg)} {r.ci ? <small>({fmt.usd(r.ci[0])} to {fmt.usd(r.ci[1])})</small> : <small className="is-warn">too few to judge</small>}</td></tr>)}</tbody></table></div>
  );
}

export function Performance({ o }: { o: Overview }) {
  const perf = usePoll<Perf>("/members/kiwi-brain/performance", 60_000);
  const [mode, setMode] = useState<Mode>(o.mode === "LIVE" ? "LIVE" : "PAPER");
  const [coin, setCoin] = useState("ALL");
  const [period, setPeriod] = useState("all");
  const [manual, setManual] = useState(false);
  const all = perf.data?.trades ?? [];
  const ts = useMemo(() => all.filter((t) => t.mode === mode && (manual || !t.test) && (coin === "ALL" || t.coin === coin)
    && (period === "all" || (t.closed_at && Date.now() - new Date(t.closed_at).getTime() < Number(period) * 86400_000))), [all, mode, coin, period, manual]);
  const s = stats(ts);
  const coins = Array.from(new Set(all.map((t) => t.coin)));
  return (
    <div className="kbpf">
      <section className="kbp-panel">
        <header className="kbp-ph kbpf-filters">
          <div className="kb-tabs" role="tablist">{(["LIVE", "PAPER"] as Mode[]).map((m) => <button key={m} role="tab" aria-selected={mode === m} onClick={() => setMode(m)}>
            {m === "LIVE" ? "LIVE" : "PAPER (simulated)"}</button>)}</div>
          <select value={coin} onChange={(e) => setCoin(e.target.value)} aria-label="Pair"><option value="ALL">All pairs</option>{coins.map((c) => <option key={c}>{c}</option>)}</select>
          <select value={period} onChange={(e) => setPeriod(e.target.value)} aria-label="Period"><option value="all">All time</option><option value="7">7 days</option><option value="30">30 days</option></select>
          <label className="kb-check"><input type="checkbox" checked={manual} onChange={(e) => setManual(e.target.checked)} /> include manual setups</label>
        </header>
        <p className="kb-src">{perf.data?.label} {mode === "PAPER" ? "Paper results are simulated fills on real prices." : ""} Unrealized P&L of open positions: {fmt.pnl(o.exposure.unrealized)} (not included below).</p>
        {perf.err && <p className="kb-warn">{perf.err}</p>}
        {s.n === 0 ? <p className="kb-empty">No closed {mode.toLowerCase()} trades{coin !== "ALL" ? ` on ${coin}` : ""} yet. Nothing to chart.</p> : (
          <div className="kbpf-kpis">
            {[["Net P&L", fmt.pnl(s.net), s.net >= 0 ? "is-up" : "is-down"], ["Trades", String(s.n), ""], ["Win rate", s.win != null ? `${fmt.n(s.win, 1)}%` : "—", ""],
              ["Profit factor", s.pf != null ? fmt.n(s.pf, 2) : "—", ""], ["Avg winner", fmt.usd(s.avgW), "is-up"], ["Avg loser", fmt.usd(s.avgL), "is-down"],
              ["Max drawdown", fmt.usd(-s.dd), "is-down"], ["Fees", fmt.usd(-s.fees), ""], ["Funding", mode === "LIVE" ? fmt.usd(-s.funding, 4) : "n/a", ""],
              ["Entry slippage", s.slip.length ? `${fmt.n(s.slip.reduce((a, b) => a + b, 0) / s.slip.length, 1)} bps avg` : "—", ""],
              ["Sharpe (daily)", s.sharpe != null ? fmt.n(s.sharpe, 2) : `not meaningful (${s.dailyN} days)`, ""]].map(([k, v, c]) =>
              <div key={k} className="kbpf-kpi"><small>{k}</small><b className={c}>{v}</b></div>)}
          </div>)}
        {s.n < 10 && s.n > 0 && <p className="kb-warn">Only {s.n} trade{s.n === 1 ? "" : "s"}: far too few to say anything about the strategy's edge.</p>}
      </section>
      {s.n > 0 && <div className="kbpf-grid">
        <section className="kbp-panel"><header className="kbp-ph"><h3>Equity curve (net P&L)</h3></header><Line pts={s.curve.map((c) => c.eq)} color="#00e58f" label="Cumulative net P&L" /></section>
        <section className="kbp-panel"><header className="kbp-ph"><h3>Drawdown</h3></header><Line pts={s.curve.map((c) => c.dd)} color="#ff5468" label="Drawdown from the peak" /></section>
        <section className="kbp-panel"><header className="kbp-ph"><h3>Daily P&L · 12 weeks</h3></header><Calendar days={s.days} /></section>
        <section className="kbp-panel"><header className="kbp-ph"><h3>Trade results</h3></header><Hist vals={ts.map((t) => t.net)} /></section>
        <section className="kbp-panel kbpf-wide"><header className="kbp-ph"><h3>Breakdown</h3></header>
          <div className="kbpf-tbls">
            <Table title="By strategy" rows={groupBy(ts, (t) => STRAT[t.strategy ?? ""] ?? t.strategy ?? "—")} />
            <Table title="By pair" rows={groupBy(ts, (t) => t.coin)} />
            <Table title="Long vs short" rows={groupBy(ts, (t) => t.side)} />
            <Table title="By market regime at the signal" rows={groupBy(ts, (t) => t.regime ?? "unknown")} />
          </div>
          <p className="kb-src">Fees {fmt.usd(s.fees)} · funding {mode === "LIVE" ? fmt.usd(s.funding, 4) : "n/a (paper)"} · gross before costs {fmt.usd(s.net + s.fees + (mode === "LIVE" ? s.funding : 0))}.</p>
        </section>
      </div>}
      {perf.data && <section className="kbp-panel">
        <header className="kbp-ph"><h3>Strategy leaderboard · backtests</h3><span className="kb-pill is-hypo">HYPOTHETICAL</span></header>
        <p className="kb-src">{perf.data.backtest.label}. R = the amount risked per trade. Ranked by the most recent (holdout) period, the part no rule was tuned on.</p>
        <table className="kbpf-bt"><thead><tr><th>Strategy</th><th>Trades</th><th>Per trade</th><th>90% range</th><th>Win</th><th>Last 40%</th><th>Max DD</th><th>Period</th></tr></thead>
          <tbody>{[...perf.data.backtest.rows].sort((a, b) => b.holdout_r - a.holdout_r).map((r) => <tr key={r.name}><td>{r.name}</td><td>{r.trades}</td>
            <td className={r.expectancy_r >= 0 ? "is-up" : "is-down"}>{r.expectancy_r > 0 ? "+" : ""}{r.expectancy_r}R</td><td>{r.ci90[0]} to {r.ci90[1]}</td><td>{r.win_rate}%</td>
            <td className={r.holdout_r >= 0 ? "is-up" : "is-down"}>{r.holdout_r}R</td><td>{r.max_dd_pct}%</td><td>{r.period}</td></tr>)}</tbody></table>
        <p className="kb-warn">No strategy is positive on the holdout, which is why both live strategies are switched off. Past and simulated results don't predict future ones.</p>
      </section>}
    </div>
  );
}
