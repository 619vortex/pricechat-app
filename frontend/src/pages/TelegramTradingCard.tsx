import { useState } from "react";
import { request } from "../api/client";
import { useApi } from "../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Loading, Stat, relativeTime } from "../components/ui";

/** Admin → Trading signals → Members' trades: Telegram trading on Hyperliquid (backend app/tradingos/tgtrade.py).
 *  The emergency stop (instant, either way), mainnet execution from Telegram (on only with a typed phrase, never while the
 *  stop is on), the referral codes, and the last day's activity and failures. */

export interface TgStatus {
  switches: { live: boolean; kill: boolean; killed_at: string | null; gate_signal_enter: boolean; global_paused: boolean; mainnet_execution: boolean; live_phrase: string };
  referral: { codes: string[]; url: string };
  window_hours: number;
  activity: Record<string, number>;
  failures: Record<string, number>;
  open_positions: Record<string, number>;
  pending_previews: number;
  recent: { at: string; actor: string; action: string; details: Record<string, unknown> }[];
}

const PATH = "/tradingos/admin/telegram-trading";
const LABEL: Record<string, string> = { manual_preview: "/open previews", manual_open: "/open trades", tg_mainnet_preview: "Mainnet previews",
  tg_signal_enter: "Signal entries", settings_telegram: "Setting changes", order_cancelled: "Targets cancelled", telegram_trading_switch: "Switch changes",
  entry_error: "Entry errors", entry_not_filled: "Not filled", agent_not_listed: "API wallet not listed", protection_failed: "Protection failed" };

export function TelegramTradingCard() {
  const st = useApi(() => request<TgStatus>(PATH), 30000);
  const [phrase, setPhrase] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  if (st.failure) return <ApiErrorBanner error={st.failure} />;
  if (!st.data) return <Loading />;
  const d = st.data;
  const s = d.switches;
  async function put(body: Record<string, unknown>) {
    setBusy(true);
    setErr(null);
    try { await request(PATH, { method: "PUT", body: JSON.stringify(body) }); setPhrase(""); await st.reload(); } catch (e) { setErr(e); } finally { setBusy(false); }
  }
  const failures = Object.values(d.failures).reduce((a, b) => a + b, 0);
  return (
    <Card title="Telegram trading">
      {err ? <ApiErrorBanner error={err} /> : null}
      {s.kill && <p className="banner banner-error"><strong>Emergency stop is on.</strong> No new Telegram trades or confirmations; nothing reaches mainnet from Telegram.
        Open positions keep their exchange-side stops.{s.killed_at ? ` Since ${new Date(s.killed_at).toLocaleString()}.` : ""}</p>}
      <div className="stats">
        <Stat label="Mainnet from Telegram" value={s.mainnet_execution ? "ON" : "OFF"} tone={s.mainnet_execution ? "warn" : "good"}
              hint={s.mainnet_execution ? "real-money orders allowed" : "previews only, nothing signed"} />
        <Stat label={`Activity · ${d.window_hours}h`} value={Object.values(d.activity).reduce((a, b) => a + b, 0)} />
        <Stat label={`Failures · ${d.window_hours}h`} value={failures} tone={failures ? "warn" : "neutral"} />
        <Stat label="Open Telegram positions" value={Object.values(d.open_positions).reduce((a, b) => a + b, 0)}
              hint={Object.entries(d.open_positions).map(([m, n]) => `${m} ${n}`).join(" · ") || "none"} />
      </div>
      <div className="btn-group">
        <button className={`btn ${s.kill ? "btn-primary" : "btn-kill-sm"}`} disabled={busy}
                onClick={() => {
                  if (!s.kill && !window.confirm("Turn on the emergency stop? Telegram stops taking new trades and confirmations at once.")) return;
                  void put({ kill: !s.kill });
                }}>
          {s.kill ? "Turn the emergency stop off" : "Emergency stop"}
        </button>
        {s.live && <button className="btn btn-sm" disabled={busy} onClick={() => void put({ live: false })}>Switch mainnet off</button>}
      </div>
      {!s.live && (
        <form className="btn-group" onSubmit={(ev) => {
          ev.preventDefault();
          if (!window.confirm("Allow REAL-MONEY orders from Telegram? Members on LIVE can then confirm mainnet trades in the bot.")) return;
          void put({ live: true, phrase });
        }}>
          <input className="input" aria-label="Confirmation phrase" placeholder={`Type ${s.live_phrase}`} value={phrase} onChange={(e) => setPhrase(e.target.value)} />
          <button className="btn btn-sm" disabled={busy || s.kill || phrase.trim().toUpperCase() !== s.live_phrase}>Switch mainnet on</button>
        </form>
      )}
      <p className="muted small">Referral: {d.referral.codes.join(", ")} · <a href={d.referral.url} target="_blank" rel="noreferrer">{d.referral.url}</a>
        {" "}· Referral gate on signal ENTER: {s.gate_signal_enter ? "on" : "off"}{s.global_paused ? " · all new signals stopped" : ""} · previews waiting: {d.pending_previews}</p>
      <div className="small">{Object.entries({ ...d.activity, ...d.failures }).map(([k, n]) => (
        <Badge key={k} tone={k in d.failures ? "warn" : "neutral"}>{LABEL[k] ?? k}: {n}</Badge>
      ))}</div>
      {!d.recent.length ? <Empty>No Telegram trading yet.</Empty> : (
        <ul className="tba-audit">{d.recent.slice(0, 15).map((x, i) => (
          <li key={i}><span className="muted">{relativeTime(x.at)}</span> <b>{LABEL[x.action] ?? x.action}</b> by {x.actor}
            {Object.keys(x.details).length ? <code> {JSON.stringify(x.details)}</code> : null}</li>
        ))}</ul>
      )}
    </Card>
  );
}
