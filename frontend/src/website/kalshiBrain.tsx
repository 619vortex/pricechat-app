import { useEffect, useRef, useState } from "react";
import { memberApi } from "./members";

/* Earn › Kalshi: the owner's Kalshi bot, live. Everything shown is what the bot reported for its last cycle (its steps, the
   closest baskets and why each was or wasn't bought); nothing is invented client-side. Account figures and orders only
   reach the signed-in owner (the server strips them for everyone else). */

type Step = { ms: number; stage: string; text: string };
type Basket = { event: string; title: string; legs: number; count: number; sum_yes_bids: number; gap_cents: number; edge_cents: number;
  edge_usd: number; cost_usd: number; apr: number; days_to_close: number; reason: string; checked_book: boolean };
type Trade = { event: string; title?: string; status: string; edge_usd: number; cost_usd: number; orders: unknown[] };
type Recent = { ts: string; event_ticker: string; ticker: string; count: number; no_price: string; status: string };
type Hist = { ts: string; mode: string; best_edge_cents: number | null; over_one_dollar: number | null; trades: number | null; error: boolean };
export type Activity = {
  received_at: string; live: boolean; cycle: number; mode: "paper" | "live" | "halted"; strategy?: string; elapsed_s?: number; error?: string;
  counts?: { events: number; markets: number; me_events: number; priced: number; over_one_dollar: number };
  gates?: { min_net_edge_cents: number; min_arb_apr: number; max_trade_fraction: number; max_deployed_fraction: number };
  closest?: Basket[]; steps: Step[]; history: Hist[];
  equity_usd?: number; deployable_usd?: number; trades?: Trade[]; recent_trades?: Recent[];
};

const POLL_MS = 5_000;
const STAGES: [string, string][] = [["account", "Account"], ["scan", "Scan"], ["filter", "Filter"], ["think", "Think"], ["gate", "Fee gate"],
  ["book", "Order book"], ["trade", "Trade"]];
const ago = (iso: string) => {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return s < 90 ? `${s}s ago` : s < 5400 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`;
};

function useTick(ms: number) {
  const [, set] = useState(0);
  useEffect(() => { const id = window.setInterval(() => set((n) => n + 1), ms); return () => window.clearInterval(id); }, [ms]);
}

/** Radar: each blip is one of the closest baskets; the nearer the centre, the nearer its YES bids are to paying for a free bet. */
function Radar({ closest, live }: { closest: Basket[]; live: boolean }) {
  const blips = closest.slice(0, 8).map((b, i) => {
    const miss = Math.max(0, -b.edge_cents);  // cents short of a profit, 0 = profitable
    const r = b.edge_cents > 0 ? 8 : Math.min(44, 12 + miss * 9);
    const a = (i / 8) * Math.PI * 2 + 0.6;
    return { b, x: 50 + r * Math.cos(a), y: 50 + r * Math.sin(a), hit: b.edge_cents > 0 };
  });
  return (
    <div className={`kb-radar ${live ? "is-live" : ""}`}>
      <svg viewBox="0 0 100 100" role="img" aria-label="Radar of the closest baskets">
        {[46, 32, 18].map((r) => <circle key={r} cx="50" cy="50" r={r} className="kb-ring" />)}
        <line x1="4" y1="50" x2="96" y2="50" className="kb-ring" /><line x1="50" y1="4" x2="50" y2="96" className="kb-ring" />
        {blips.map(({ b, x, y, hit }, i) => (
          <g key={b.event} className={`kb-blip ${hit ? "is-hit" : ""}`} style={{ animationDelay: `${i * 0.25}s` }}>
            <circle cx={x} cy={y} r={hit ? 3 : 2} /><title>{`${b.event}: ${b.edge_cents.toFixed(2)}¢ per basket`}</title>
          </g>
        ))}
        <circle cx="50" cy="50" r="2.2" className="kb-core" />
      </svg>
      <div className="kb-sweep" aria-hidden />
    </div>
  );
}

/** The cycle's path through the pipeline: lit stages ran this cycle; a packet runs along the wire each new cycle. */
function Pipeline({ steps, cycle }: { steps: Step[]; cycle: number }) {
  const ran = new Map(steps.map((s) => [s.stage, s]));
  return (
    <ol className="kb-pipe" key={cycle} aria-label="This cycle's steps">
      <span className="kb-packet" aria-hidden />
      {STAGES.map(([k, label], i) => {
        const s = ran.get(k);
        return (
          <li key={k} className={s ? "is-on" : ""} style={{ animationDelay: `${i * 0.18}s` }}>
            <i aria-hidden /><b>{label}</b><small>{s ? `${(s.ms / 1000).toFixed(1)}s` : "—"}</small>
          </li>
        );
      })}
    </ol>
  );
}

/** The bot's own words for this cycle, revealed line by line. */
function Thoughts({ steps, cycle }: { steps: Step[]; cycle: number }) {
  return (
    <div className="kb-term" key={cycle} role="log" aria-label="What the bot did this cycle">
      {steps.map((s, i) => (
        <p key={i} className={`kb-line is-${s.stage}`} style={{ animationDelay: `${i * 0.45}s` }}>
          <span className="kb-ms">{(s.ms / 1000).toFixed(1).padStart(5, " ")}s</span><span className="kb-stage">{s.stage}</span>{s.text}
        </p>
      ))}
      <p className="kb-line kb-caret" style={{ animationDelay: `${steps.length * 0.45}s` }}>waiting for the next cycle<span aria-hidden>▍</span></p>
    </div>
  );
}

/** Best edge per cycle; above the line = a basket that pays after fees. */
function Pulse({ history }: { history: Hist[] }) {
  const pts = history.filter((h) => h.best_edge_cents != null);
  if (pts.length < 2) return null;
  const vals = pts.map((h) => h.best_edge_cents as number);
  const lo = Math.min(-1, ...vals), hi = Math.max(0.5, ...vals);
  const y = (v: number) => 36 - ((v - lo) / (hi - lo)) * 32;
  const line = pts.map((h, i) => `${(i / (pts.length - 1)) * 100},${y(h.best_edge_cents as number).toFixed(2)}`).join(" ");
  return (
    <figure className="kb-pulse">
      <svg viewBox="0 0 100 40" preserveAspectRatio="none" role="img" aria-label="Best edge per cycle">
        <line x1="0" x2="100" y1={y(0)} y2={y(0)} className="kb-zero" />
        <polyline points={line} className="kb-trace" vectorEffect="non-scaling-stroke" />
      </svg>
      <figcaption>Best basket per cycle, last {pts.length} cycles. Above the line = it would pay after fees. Now {vals[vals.length - 1].toFixed(2)}¢.</figcaption>
    </figure>
  );
}

export function BotBrain() {
  const [a, setA] = useState<Activity | null>(null);
  const [owner, setOwner] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const seq = useRef(0);
  useTick(1000);
  useEffect(() => {
    const load = () => {
      const n = ++seq.current;
      memberApi<{ owner: boolean; activity: Activity | null }>("/members/kalshi/activity")
        .then((d) => { if (n === seq.current) { setA(d.activity); setOwner(d.owner); setErr(null); } })
        .catch((e: unknown) => { if (n === seq.current) setErr(e instanceof Error ? e.message : "Couldn't reach the bot feed."); });
    };
    load();
    const id = window.setInterval(() => { if (document.visibilityState !== "hidden") load(); }, POLL_MS);
    return () => window.clearInterval(id);
  }, []);
  if (!a) {
    return err ? <p className="site-error" role="alert">{err}</p> : null;
  }
  const state = !a.live ? "offline" : a.mode;
  const c = a.counts;
  const closest = a.closest ?? [];
  return (
    <section className="arb-card kb" aria-label="Kiwi's Kalshi bot, live">
      <header className="kb-head">
        <span className={`kb-mode is-${state}`}>{state === "offline" ? "OFFLINE" : state === "live" ? "LIVE · REAL ORDERS" : state === "halted" ? "HALTED" : "PAPER"}</span>
        <h2>Kiwi's Kalshi bot</h2>
        <span className="kb-meta">cycle {a.cycle} · {ago(a.received_at)}{a.elapsed_s ? ` · took ${a.elapsed_s}s` : ""}</span>
      </header>
      <p className="arb-muted kb-what">Hunting for <b>free bets</b>: events where only one outcome can win, yet the YES bids add up to more than $1. Buying NO on
        every outcome then pays at least one dollar less than the number of outcomes, whatever happens. It only buys when that still pays after Kalshi's fees
        {a.gates ? <> (more than {a.gates.min_net_edge_cents}¢ per basket, {Math.round(a.gates.min_arb_apr * 100)}%+ a year)</> : null}.</p>
      {state === "offline" && <p className="arb-stale">The bot's last report is {ago(a.received_at)} old: it isn't running right now.</p>}
      {state === "halted" && <p className="arb-stale">Kill switch on: the bot stopped trading and needs a human look before it trades again.</p>}
      {a.error && <p className="arb-stale">Last cycle failed: {a.error}</p>}
      <div className="kb-grid">
        <div className="kb-left">
          <Radar closest={closest} live={a.live} />
          {c && <dl className="kb-counts">
            <div><dt>markets read</dt><dd>{c.markets.toLocaleString()}</dd></div>
            <div><dt>one-winner events</dt><dd>{c.me_events.toLocaleString()}</dd></div>
            <div><dt>priced</dt><dd>{c.priced.toLocaleString()}</dd></div>
            <div><dt>bids past $1</dt><dd className={c.over_one_dollar ? "is-warm" : ""}>{c.over_one_dollar}</dd></div>
          </dl>}
          {owner && a.equity_usd != null && <dl className="kb-counts">
            <div><dt>equity</dt><dd>${a.equity_usd.toFixed(2)}</dd></div>
            <div><dt>may deploy</dt><dd>${(a.deployable_usd ?? 0).toFixed(2)}</dd></div>
          </dl>}
        </div>
        <div className="kb-right">
          <Pipeline steps={a.steps} cycle={a.cycle} />
          <Thoughts steps={a.steps} cycle={a.cycle} />
        </div>
      </div>
      {closest.length > 0 && <>
        <h3 className="kb-h3">Closest to a free bet right now</h3>
        <ul className="kb-baskets">{closest.slice(0, 6).map((b) => {
          const fill = Math.max(0, Math.min(1, (b.sum_yes_bids - 0.95) / 0.1));  // 95¢ … $1.05
          return (
            <li key={b.event} className={b.edge_cents > 0 ? "is-hit" : ""}>
              <div className="kb-b-top"><b title={b.event}>{b.title || b.event}</b>
                <span className={b.edge_cents > 0 ? "is-good" : "is-bad"}>{b.edge_cents > 0 ? "+" : ""}{b.edge_cents.toFixed(2)}¢</span></div>
              <div className="kb-meter" aria-hidden><i style={{ width: `${fill * 100}%` }} /><em /></div>
              <small>{b.legs} outcomes · YES bids sum ${b.sum_yes_bids.toFixed(4)} · {b.reason || "passes every gate"}</small>
            </li>
          );
        })}</ul>
      </>}
      <Pulse history={a.history} />
      {owner && (a.recent_trades?.length ?? 0) > 0 && <>
        <h3 className="kb-h3">Orders (you only)</h3>
        <table className="arb-table"><thead><tr><th>When</th><th>Market</th><th>Order</th><th>Status</th></tr></thead>
          <tbody>{a.recent_trades!.map((t, i) => (
            <tr key={i}><td>{ago(t.ts)}</td><td>{t.ticker}</td><td>{t.count} NO @ {Math.round(Number(t.no_price) * 100)}¢</td>
              <td><span className={`kb-st is-${t.status.replace(/\s/g, "-")}`}>{t.status}</span></td></tr>
          ))}</tbody></table>
      </>}
    </section>
  );
}
