import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { Badge, Card, relativeTime } from "../components/ui";

/** Dashboard › Website › Exchange auto-trader. Owner only: switches the labelled SIMULATED AUTO-TRADER on the paper
 *  exchange so charts form before there are enough members. Never on the public site. */

export interface AutoConfig { enabled: boolean; spot: boolean; perps: boolean; activity: number; swing_pct: number; max_trade_value: number }
export interface AutoStatus {
  config: AutoConfig; last_tick: string | null; tick_seconds: number; usdt: number; trades_last_hour: { spot: number; perps: number };
  markets: { symbol: string; reference: number | null; ref_status: string; path_bps: number; center: number; last: number | null; inventory: number; perp_position: number }[];
}

const MOODS: { key: string; emoji: string; label: string; hint: string; activity: number; swing_pct: number }[] = [
  { key: "calm", emoji: "😴", label: "Snorlax", hint: "1 trade/min · ±1%", activity: 1, swing_pct: 1 },
  { key: "lively", emoji: "⚡", label: "Pikachu", hint: "4 trades/min · ±2%", activity: 4, swing_pct: 2 },
  { key: "wild", emoji: "🔥", label: "Charizard", hint: "10 trades/min · ±3.5%", activity: 10, swing_pct: 3.5 },
];

const px = (c: number | null | undefined) => (c == null ? "—" : (c / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

export function ExchangeAutoCard() {
  const [st, setSt] = useState<AutoStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api.exAuto().then((s) => { setSt(s); setErr(null); }).catch((e) => setErr(e instanceof Error ? e.message : "Couldn't load")), []);
  useEffect(() => { void load(); const t = window.setInterval(load, 10_000); return () => window.clearInterval(t); }, [load]);

  async function set(body: Partial<AutoConfig>) {
    setBusy(true);
    try { setSt(await api.setExAuto(body)); setErr(null); } catch (e) { setErr(e instanceof Error ? e.message : "Couldn't save"); } finally { setBusy(false); }
  }
  async function kick() {
    setBusy(true);
    try { await api.exAutoTick(); await load(); } catch (e) { setErr(e instanceof Error ? e.message : "Couldn't run"); } finally { setBusy(false); }
  }

  const c = st?.config;
  const mood = c ? MOODS.find((m) => m.activity === c.activity && m.swing_pct === c.swing_pct)?.key : undefined;
  return (
    <Card title="📈 Exchange auto-trader" actions={c && <Badge tone={c.enabled ? "good" : "neutral"}>{c.enabled ? "Trading" : "Off"}</Badge>}>
      <p className="muted small" style={{ marginTop: 0 }}>
        Makes the paper exchange feel alive: each card gets a gentle simulated price path around its <b>real reference</b>, and a labelled
        "SIMULATED AUTO-TRADER" account trades against the market maker so charts form. Its trades show as <b>SIM</b> on the site and it's never ranked.
      </p>
      {err && <p className="small" style={{ color: "var(--bad)" }}>{err}</p>}
      {!c ? <p className="muted small">Loading…</p> : (
        <>
          <button type="button" className={`btn ${c.enabled ? "btn-kill" : "btn-primary"}`} style={{ width: "100%", height: 44, fontSize: 15, marginTop: 0 }} disabled={busy}
            onClick={() => void set({ enabled: !c.enabled })}>{c.enabled ? "⏸ Stop auto-trading" : "▶ Start auto-trading"}</button>

          <div className="xa-moods" role="radiogroup" aria-label="Mood">
            {MOODS.map((m) => (
              <button key={m.key} type="button" role="radio" aria-checked={mood === m.key} className={`xa-mood${mood === m.key ? " is-on" : ""}`} disabled={busy}
                onClick={() => void set({ activity: m.activity, swing_pct: m.swing_pct })}>
                <span className="xa-emoji">{m.emoji}</span><b>{m.label}</b><small>{m.hint}</small>
              </button>
            ))}
          </div>

          <div className="xa-row">
            <label>Trades per minute, per market <b>{c.activity}</b>
              <input type="range" min={0.5} max={12} step={0.5} value={c.activity} disabled={busy} onChange={(e) => void set({ activity: Number(e.target.value) })} /></label>
            <label>Max swing from the real price <b>±{c.swing_pct}%</b>
              <input type="range" min={0.3} max={4} step={0.1} value={c.swing_pct} disabled={busy} onChange={(e) => void set({ swing_pct: Number(e.target.value) })} /></label>
            <label>Max trade size <b>{px(c.max_trade_value)} USDT</b>
              <input type="range" min={1000} max={200000} step={1000} value={c.max_trade_value} disabled={busy} onChange={(e) => void set({ max_trade_value: Number(e.target.value) })} /></label>
          </div>
          <div className="btn-group" style={{ margin: "8px 0" }}>
            <label className="small"><input type="checkbox" checked={c.spot} disabled={busy} onChange={(e) => void set({ spot: e.target.checked })} /> Spot</label>
            <label className="small"><input type="checkbox" checked={c.perps} disabled={busy} onChange={(e) => void set({ perps: e.target.checked })} /> Perps</label>
            <button type="button" className="btn btn-sm" disabled={busy || !c.enabled} onClick={() => void kick()}>Run a round now</button>
            <a className="link small" href="/pokemon/exchange" target="_blank" rel="noreferrer">Open the exchange ↗</a>
          </div>

          <dl className="kv">
            <dt>Last round</dt><dd>{st.last_tick ? relativeTime(st.last_tick) : "never"} <span className="muted small">(every {st.tick_seconds}s while on)</span></dd>
            <dt>Trades, last hour</dt><dd>{st.trades_last_hour.spot} spot · {st.trades_last_hour.perps} perps</dd>
            <dt>Bot paper USDT</dt><dd>{px(st.usdt)}</dd>
          </dl>
          <div className="table-wrap" style={{ marginTop: 8 }}>
            <table className="table small">
              <thead><tr><th>Market</th><th>Real ref.</th><th>Sim path</th><th>Last</th><th>Bot spot</th><th>Bot perp</th></tr></thead>
              <tbody>{st.markets.map((m) => (
                <tr key={m.symbol}><td><b>{m.symbol}</b>{m.ref_status !== "ok" && <> <Badge tone="warn">{m.ref_status}</Badge></>}</td><td>{px(m.reference)}</td>
                  <td style={{ color: m.path_bps >= 0 ? "var(--good)" : "var(--bad)" }}>{m.reference ? `${px(m.center)} (${m.path_bps >= 0 ? "+" : ""}${(m.path_bps / 100).toFixed(2)}%)` : "—"}</td>
                  <td>{px(m.last)}</td><td>{m.inventory}</td><td>{m.perp_position}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}
