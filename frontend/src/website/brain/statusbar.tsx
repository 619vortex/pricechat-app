import { useState } from "react";
import { fmt, post, type Overview } from "./api";

function Dot({ s }: { s: string }) {
  const tone = ["live", "connected", "ok", "active", "paper"].includes(s) ? "ok" : ["shadow", "stale", "late", "unverified", "none"].includes(s) ? "warn" : "bad";
  return <i className={`kbs-dot is-${tone}`} aria-hidden />;
}

/** The persistent top bar. Each component has its own state: an open position never makes the engine look healthy. */
export function StatusBar({ o, onChanged, onAsk, onControl }: { o: Overview; onChanged: () => void; onAsk: () => void; onControl: () => void }) {
  const [ask, setAsk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const h = o.health;
  const paused = o.paused;
  async function toggle(p: boolean) {
    setBusy(true);
    setErr(null);
    try {
      await post("/members/trading/settings", { paused: p }, "PUT");
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
      setAsk(false);
    }
  }
  const upnl = o.exposure.unrealized;
  const mode = o.mode === "LIVE" ? (o.auto ? "LIVE · AUTO" : "LIVE · APPROVAL") : o.mode;
  return (
    <div className="kbs" role="region" aria-label="Kiwi Brain status">
      <div className="kbs-cells">
        <span className="kbs-cell" title={h.exchange.error ?? h.exchange.label}><Dot s={h.exchange.state} /><small>Exchange</small>
          <b>{h.exchange.state === "paper" ? "Paper" : h.exchange.state === "connected" ? "Hyperliquid" : h.exchange.state.toUpperCase()}</b></span>
        <span className={`kbs-cell kbs-mode is-${o.mode.toLowerCase()}`}><small>Mode</small><b>{mode}</b></span>
        <span className="kbs-cell" title={h.market_feed.source}><Dot s={h.market_feed.state} /><small>Market data</small>
          <b>{h.market_feed.state === "live" ? `Live · ${fmt.n(h.market_feed.age_s ?? 0, 0)}s` : h.market_feed.state.toUpperCase()}</b></span>
        <span className="kbs-cell" title={h.engine.reason}><Dot s={h.engine.state} /><small>Strategy engine</small><b>{h.engine.state.toUpperCase()}</b></span>
        <span className="kbs-cell" title={h.monitoring.label}><Dot s={h.monitoring.state} /><small>Monitoring</small><b>{h.monitoring.state === "ok" ? "OK · 60s" : h.monitoring.state.toUpperCase()}</b></span>
        <span className="kbs-cell"><Dot s={h.protection.state} /><small>Positions</small>
          <b>{h.protection.open} open{h.protection.open ? ` · ${h.protection.state === "ok" ? "🛡 stops on" : h.protection.state}` : ""}</b></span>
        <span className="kbs-cell"><small>Unrealized</small><b className={upnl == null ? "" : upnl >= 0 ? "is-up" : "is-down"}>{fmt.usd(upnl)}</b></span>
        <span className="kbs-cell" title="Loss if every stop-loss is hit, as % of equity"><small>Risk at stops</small>
          <b>{fmt.usd(o.exposure.loss_at_stop)}{o.exposure.loss_at_stop_pct != null ? ` · ${o.exposure.loss_at_stop_pct}%` : ""}</b></span>
        <span className="kbs-cell"><small>Last evaluation</small><b>{fmt.ago(h.engine.last_evaluation_at)}</b></span>
      </div>
      <div className="kbs-actions">
        <button className="kbs-btn is-ask" onClick={onAsk}>ASK KIWI 🥝</button>
        <button className="kbs-btn" onClick={onControl} aria-label="Control room">⚙ CONTROL</button>
        {paused ? (
          <button className="kbs-btn is-resume" disabled={busy || o.global_paused} onClick={() => void toggle(false)}>RESUME NEW TRADES</button>
        ) : (
          <button className="kbs-btn is-pause" disabled={busy} onClick={() => setAsk(true)}>⏸ EMERGENCY PAUSE</button>
        )}
      </div>
      {err && <p className="kbs-err" role="alert">{err}</p>}
      {ask && (
        <div className="kb-dialog" role="dialog" aria-modal="true" aria-label="Pause new trades">
          <div className="kb-dialog-in">
            <h3>Pause all new trades?</h3>
            <p>No new entries (manual or AUTO) until you resume. Open positions keep their stop-loss and take-profit orders on the exchange:
              pausing never removes protection. To exit a position, close it in the terminal.</p>
            <div className="kb-dialog-row">
              <button className="kbs-btn" onClick={() => setAsk(false)}>Cancel</button>
              <button className="kbs-btn is-pause" disabled={busy} onClick={() => void toggle(true)}>PAUSE NEW TRADES</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
