/** PnL track record (Hyperliquid's own numbers, read-only): 24h / 7d / 30d / all-time with a sparkline. */

export interface PnlWindow { pnl: number; value: number | null; series: number[] }
export type Pnl = Partial<Record<"24h" | "7d" | "30d" | "all", PnlWindow>>;

export const money = (n: number, sign = false) =>
  `${sign ? (n > 0 ? "+" : n < 0 ? "−" : "") : n < 0 ? "−" : ""}$${Math.abs(n).toLocaleString("en-US", Math.abs(n) >= 1000
    ? { maximumFractionDigits: 0 } : { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;  // "$12.50", never "$12.5"

function Spark({ series }: { series: number[] }) {
  if (series.length < 2) return null;
  const min = Math.min(...series), max = Math.max(...series), span = max - min || 1;
  const pts = series.map((v, i) => `${(i / (series.length - 1)) * 100},${30 - ((v - min) / span) * 28 - 1}`).join(" ");
  const up = series[series.length - 1] >= series[0];
  return (
    <svg className={`pnl-spark ${up ? "is-up" : "is-down"}`} viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden>
      <polyline points={pts} fill="none" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export function PnlCard({ title, pnl, sub }: { title: string; pnl: Pnl | null | undefined; sub?: React.ReactNode }) {
  if (!pnl) return <div className="pnl-card"><b>{title}</b><p className="pnl-sub">Hyperliquid couldn't be read right now.</p></div>;
  const all = pnl.all, wins = (["24h", "7d", "30d", "all"] as const).filter((k) => pnl[k]);
  return (
    <div className="pnl-card">
      <div className="pnl-head"><b>{title}</b>{all?.value != null && <span className="pnl-sub">Account {money(all.value)}</span>}</div>
      {all && <Spark series={all.series} />}
      <dl className="pnl-grid">
        {wins.map((k) => (
          <div key={k}><dt>{k === "all" ? "All time" : k}</dt><dd className={pnl[k]!.pnl > 0 ? "is-up" : pnl[k]!.pnl < 0 ? "is-down" : ""}>{money(pnl[k]!.pnl, true)}</dd></div>
        ))}
      </dl>
      {sub && <p className="pnl-sub">{sub}</p>}
    </div>
  );
}
