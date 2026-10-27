import { useEffect, useState } from "react";
import { CountUp } from "./kalshiFx";

/* Earn › Kalshi: the bot's weather desk. Shows, from the bot's last calibration (sent with every activity report), whether the
   daily-temperature strategy may trade, how far its model is from the bar, what it was tested on and a live countdown to its
   next re-test, so a viewer knows what to expect before any weather trade happens. Nothing here is computed client-side
   except the countdown. */

type Pnl = { min_edge: number; trades: number; mean_cents: number; total_usd: number; total_ci90_by_day: [number, number]; hit_rate: number };
export type WeatherBrief = {
  enabled: boolean; reason: string; version: string; computed_at: string; recheck_at: string; stale_at: string;
  bss: number | null; bss_ci90: [number, number] | null; floor: number | null; contribution_ci90: [number, number] | null;
  market_only: number | null; model_alone: number | null;
  tested: { markets: number | null; events: number | null; from: string | null; to: string | null; days: number | null; history_markets: number | null };
  pnl: Pnl[];
};

const pct = (v: number) => `${v > 0 ? "+" : ""}${(v * 100).toFixed(2)}%`;

function useNow(ms: number) {
  const [now, set] = useState(() => Date.now());
  useEffect(() => { const id = window.setInterval(() => set(Date.now()), ms); return () => window.clearInterval(id); }, [ms]);
  return now;
}

/** Ticking days / hours / minutes / seconds to the next re-test; a ring empties over the 30-day cycle. */
function Countdown({ from, to }: { from: string; to: string }) {
  const now = useNow(1000);
  const start = new Date(from).getTime(), end = new Date(to).getTime();
  const left = Math.max(0, end - now);
  const frac = end > start ? left / (end - start) : 0;
  const parts: [number, string][] = [[Math.floor(left / 864e5), "d"], [Math.floor(left / 36e5) % 24, "h"], [Math.floor(left / 6e4) % 60, "m"],
    [Math.floor(left / 1e3) % 60, "s"]];
  const C = 2 * Math.PI * 16;
  return (
    <div className="kw-count" role="timer" aria-label="Time until the weather model is re-tested">
      <svg viewBox="0 0 40 40" aria-hidden><circle cx="20" cy="20" r="16" className="kx-ring-bg" />
        <circle cx="20" cy="20" r="16" className="kw-ring-fg" strokeDasharray={C} strokeDashoffset={C * (1 - frac)} /></svg>
      <div>
        <small>{left > 0 ? "next re-test in" : "re-test due now"}</small>
        <p>{parts.map(([v, u]) => <span key={u}><b key={`${u}${v}`}>{String(v).padStart(2, "0")}</b>{u}</span>)}</p>
      </div>
    </div>
  );
}

/** The bar the model has to clear: its own edge over the market (90% range) against zero. Whole range right of zero = it may trade. */
function SkillBar({ ci }: { ci: [number, number] }) {
  const lo = Math.min(-0.005, ci[0] * 1.3), hi = Math.max(0.01, ci[1] * 1.3);
  const x = (v: number) => ((v - lo) / (hi - lo)) * 100;
  const clears = ci[0] > 0;
  return (
    <figure className="kw-bar">
      <div className="kw-track" aria-hidden>
        <i className={`kw-range ${clears ? "is-good" : ""}`} style={{ left: `${x(ci[0])}%`, width: `${x(ci[1]) - x(ci[0])}%` }} />
        <em style={{ left: `${x(0)}%` }} />
      </div>
      <figcaption>
        Model's own edge over the market: <b className={clears ? "is-good" : "is-warm"}>{pct(ci[0])} to {pct(ci[1])}</b>.
        {clears ? " The whole range clears the bar." : " Part of the range is below zero (the gold line), so it isn't proven yet."}
      </figcaption>
    </figure>
  );
}

function Sky({ enabled }: { enabled: boolean }) {
  return (
    <svg className={`kw-sky ${enabled ? "is-clear" : ""}`} viewBox="0 0 64 48" aria-hidden>
      <g className="kw-sun"><circle cx="24" cy="20" r="9" />
        {Array.from({ length: 8 }, (_, i) => <line key={i} x1="24" y1="4" x2="24" y2="8" transform={`rotate(${i * 45} 24 20)`} />)}</g>
      <path className="kw-cloud" d="M18 40h30a9 9 0 0 0 0-18 12 12 0 0 0-23 3 8 8 0 0 0-7 15z" />
    </svg>
  );
}

export function WeatherDesk({ w }: { w: WeatherBrief }) {
  const t = w.tested;
  const strict = w.pnl.find((p) => p.min_edge >= 0.05) ?? w.pnl[0];
  return (
    <section className={`kw ${w.enabled ? "is-on" : ""}`} aria-label="Weather desk">
      <header className="kw-head">
        <Sky enabled={w.enabled} />
        <div>
          <span className={`kb-mode ${w.enabled ? "is-live" : "is-paper"}`}>{w.enabled ? "CLEARED TO TRADE" : "WATCHING · NOT TRADING"}</span>
          <h3 className="kb-h3">Weather desk: daily high and low temperature markets</h3>
        </div>
      </header>
      <p className="kw-expect">
        {w.enabled
          ? <>What to expect: the weather model passed its out-of-sample test, so weather trades can now appear, but only where its price beats the market's after fees.</>
          : <>What to expect: <b>no weather trades yet.</b> The model is tested against settled markets and has to add skill <i>on top of</i> the market's own price
            before it may place a single order. It's re-tested every 30 days; nothing changes until then.</>}
      </p>
      <dl className="kb-counts kw-counts">
        <div><dt>markets in history</dt><dd><CountUp start={0} value={t.history_markets ?? 0} /></dd></div>
        <div><dt>markets in the test</dt><dd><CountUp start={0} value={t.markets ?? 0} /></dd></div>
        <div><dt>days tested</dt><dd><CountUp start={0} value={t.days ?? 0} /></dd></div>
        <div><dt>model vs market</dt><dd className={(w.bss ?? 0) > 0 ? "is-good" : "is-bad"}>{w.bss == null ? "—" : <CountUp start={0} value={w.bss * 100} digits={2} prefix={w.bss > 0 ? "+" : ""} />}%</dd></div>
      </dl>
      {w.contribution_ci90 && <SkillBar ci={w.contribution_ci90} />}
      <div className="kw-foot">
        <Countdown from={w.computed_at} to={w.recheck_at} />
        {strict && <p className="arb-muted kw-paper">Paper backtest, not real fills: {strict.trades.toLocaleString()} one-contract trades at {Math.round(strict.min_edge * 100)}¢+
          edge would have made {strict.mean_cents > 0 ? "+" : ""}{strict.mean_cents.toFixed(1)}¢ each ({t.from} to {t.to}).{w.enabled ? "" : " Encouraging, not proof, which is why the gate stays shut."}</p>}
      </div>
    </section>
  );
}
