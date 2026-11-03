/** What a signal has behind it, in plain words: how this setup's earlier signals ended, and who is really in. Real money and
 *  practice are never added together, a small sample says so, and a record is never shown as the next signal's chance of winning. */

export interface SignalRecord { closed: number; wins: number; losses: number; win_rate: number; avg_pct_after_costs: number; enough: boolean; min_sample: number; label: string }
export interface Sig { id: number; number: number; coin: string; side: string; status: string; status_label: string; entry: number[]; stop: number;
  target: number; rr: number; leverage: number[]; last_price: number | null; published_at: string | null; participation: Record<string, number>;
  strategy: string; timeframe?: string; reason?: string; tp2?: number; activated_at?: string | null; closed_at?: string | null;
  model_pnl_pct?: number | null; exit_price?: number | null; created_at?: string; record?: SignalRecord | null }

export function RecordLine({ record: r, strategy }: { record?: SignalRecord | null; strategy: string }) {
  if (strategy === "test") return <p className="kt-record">📊 Manual setup: no track record.</p>;
  if (!r) return <p className="kt-record">📊 New setup: no closed signals yet, so no track record.</p>;
  const avg = `${r.avg_pct_after_costs > 0 ? "+" : ""}${r.avg_pct_after_costs.toFixed(2)}%`;
  return (
    <p className="kt-record" title={r.label}>📊 This setup so far: <b>{r.wins} {r.wins === 1 ? "win" : "wins"}, {r.losses} {r.losses === 1 ? "loss" : "losses"}</b> ({r.win_rate}%) ·{" "}
      <b className={r.avg_pct_after_costs > 0 ? "is-up" : r.avg_pct_after_costs < 0 ? "is-down" : ""}>{avg}</b> average per trade after costs
      {!r.enough && <> · <em>too few to judge ({r.closed} of {r.min_sample}+)</em></>}
      <small> Model results without leverage, not real trades. Past results don't predict this signal.</small></p>
  );
}

export function CrowdLine({ x }: { x: Pick<Sig, "participation"> }) {
  const { live = 0, practice = 0, live_open_usd: usd } = x.participation;
  const none = live === 0 && practice === 0;
  return (
    <p className={`kt-kiwis${live > 0 ? " is-in" : ""}`} title="Members with a filled position in this signal. Others being in doesn't make a trade good.">
      👥 {none ? "No one in yet" : <>{live} real money{usd ? ` · ≈$${usd.toLocaleString("en-US")} open` : ""} · {practice} practice</>}</p>
  );
}
