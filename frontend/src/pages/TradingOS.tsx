import { useState } from "react";
import { request } from "../api/client";
import { useApi } from "../api/useApi";
import { Advanced, ApiErrorBanner, Badge, Card, Empty, Loading, PageHeader, Stat, Tabs, relativeTime, type Tone } from "../components/ui";

/** Admin → Trading OS (Phase 1-2): real Hyperliquid market data, the deterministic scanner, signal review and publishing
 *  to the Telegram signal group, model performance, execution (accounts, positions, orders, the LIVE allowlist) and the audit log. */

interface Settings { scanner_enabled: boolean; auto_publish: boolean; paused: boolean; publish_chat: string; main_chat: string; chart_routing: string; brief_enabled: boolean; brief_hour: number; timeframe: string; universe_size: number;
  min_volume_usd: number; max_spread_bps: number; max_open_signals: number; min_rr: number; strategies: Record<string, unknown>; allowed_coins: string }
interface Overview { settings: Settings; exchange: { ok: boolean; markets?: number; error?: string }; counts: Record<string, number>;
  stats: { label: string; total: number; open: number; closed: number; win_rate: number | null; avg_gain: number | null; avg_loss: number | null; net_return: number; max_drawdown: number };
  execution: string; vortex_assist?: VortexCfg; kitchen?: KitchenModes }
interface ModeInfo { coins: string[]; models: string[]; max_open: number; confirm_30m: boolean; market_entry: boolean; auto_publish: boolean;
  cooldown_min: number; min_quality: number; research: { trades_per_day: number; avg_r: number; note: string } }
type Mode = "standard" | "degen";
interface KitchenModes { current: Mode; publishing: boolean; live: boolean; signals_24h: number; signals_7d: number; modes: Record<Mode, ModeInfo> }
interface VortexCfg { on: boolean | null; enabled: boolean; token_set: boolean; daily_public: number; daily_ranked: number; min_rank: number;
  last_seen: string | null; online: boolean; pending: number }
interface Sig { id: number; number: number; coin: string; side: string; status: string; status_label: string; entry: number[]; stop: number; target: number; rr: number;
  leverage: number[]; reason: string; created_at: string; published_at: string | null; last_price: number | null; model_pnl_pct: number | null;
  participation: Record<string, number>; card: string; tg_error: string | null; timeframe: string }
interface Mkt { coin: string; price: number; change_24h: number; funding_h: number; oi_usd: number; volume_24h: number; spread_bps: number | null; max_leverage: number }

const TONE: Record<string, Tone> = { PENDING_REVIEW: "warn", WAITING: "info", ACTIVE: "good", TARGET_HIT: "good", STOP_HIT: "bad", CLOSED: "neutral", EXPIRED: "neutral", CANCELLED: "neutral" };
const post = <T,>(path: string, body: unknown = {}) => request<T>(path, { method: "POST", body: JSON.stringify(body) });
/** signed dollars: "+$4.20" / "−$3.10" (never "$-3.10") */
const sUsd = (n: number) => `${n > 0 ? "+" : n < 0 ? "−" : ""}$${Math.abs(n).toFixed(2)}`;
const usdM = (n: number) => `$${(n / 1e6).toLocaleString(undefined, { maximumFractionDigits: 0 })}M`;

export function TradingOSPage() {
  const ov = useApi(() => request<Overview>("/tradingos/admin/overview"));
  const [tab, setTab] = useState<"signals" | "performance" | "decisions" | "execution" | "markets" | "settings" | "audit">("signals");
  const [pushed, setPushed] = useState<{ msg: string | null; err: unknown; n: number }>({ msg: null, err: null, n: 0 });
  return (
    <div className="stack">
      <PageHeader title="Trading signals" subtitle="Signals waiting for your OK, open and past signals, and the switches that control them." />
      {ov.failure ? <ApiErrorBanner error={ov.failure} /> : null}
      <PushNow onDone={(m) => { setPushed((p) => ({ msg: m, err: null, n: p.n + 1 })); setTab("signals"); void ov.reload(); }}
               onError={(e) => setPushed((p) => ({ ...p, msg: null, err: e }))} />
      {pushed.msg && <p className="banner banner-good">✓ {pushed.msg}</p>}
      {pushed.err ? <ApiErrorBanner error={pushed.err} /> : null}
      {ov.data ? <Top o={ov.data} reload={() => void ov.reload()} /> : <Loading />}
      <Tabs tabs={[{ key: "signals", label: "Signals" }, { key: "performance", label: "Performance" }, { key: "decisions", label: "Decisions & shadow" }, { key: "settings", label: "Settings" }, { key: "execution", label: "Members' trades" }, { key: "markets", label: "Markets", more: true }, { key: "audit", label: "Change log", more: true }]}
            value={tab} onChange={setTab} />
      {tab === "signals" && <Signals key={pushed.n} onChange={() => void ov.reload()} />}
      {tab === "performance" && <Performance />}
      {tab === "decisions" && <Decisions />}
      {tab === "execution" && <Execution />}
      {tab === "markets" && <Markets />}
      {tab === "settings" && ov.data?.kitchen && <TradingMode k={ov.data.kitchen} onSaved={() => void ov.reload()} />}
      {tab === "settings" && ov.data && <SettingsForm s={ov.data.settings} onSaved={() => void ov.reload()} />}
      {tab === "settings" && ov.data?.vortex_assist && <VortexAssist v={ov.data.vortex_assist} onSaved={() => void ov.reload()} />}
      {tab === "audit" && <Audit />}
    </div>
  );
}

function Top({ o, reload }: { o: Overview; reload: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  async function pause(paused: boolean) {
    setBusy(true);
    setErr(null);
    try { await request("/tradingos/admin/settings", { method: "PUT", body: JSON.stringify({ paused }) }); reload(); } catch (e) { setErr(e); } finally { setBusy(false); }
  }
  return (
    <>
      {o.settings.paused && <p className="banner banner-warn"><strong>Signals are stopped.</strong> No new signals are created or posted until you resume.</p>}
      {!o.exchange.ok && <p className="banner banner-error">Can't reach Hyperliquid right now{o.exchange.error ? `: ${o.exchange.error}` : ""}.</p>}
      <div className="stats">
        <Stat label="Waiting for your OK" value={o.counts.PENDING_REVIEW ?? 0} tone={(o.counts.PENDING_REVIEW ?? 0) ? "warn" : "neutral"} />
        <Stat label="Open now" value={(o.counts.WAITING ?? 0) + (o.counts.ACTIVE ?? 0)} />
        <Stat label="Win rate" value={o.stats.win_rate == null ? "—" : `${o.stats.win_rate}%`} hint={`${o.stats.closed} finished · total ${o.stats.net_return > 0 ? "+" : ""}${o.stats.net_return}%`} />
      </div>
      <div className="btn-group">
        <button className={`btn ${o.settings.paused ? "btn-primary" : "btn-kill-sm"}`} disabled={busy} onClick={() => void pause(!o.settings.paused)}>
          {o.settings.paused ? "Resume signals" : "Stop all new signals"}
        </button>
        <span className="muted small">Results are without leverage, after estimated fees. Worst drop so far: {o.stats.max_drawdown}%.</span>
      </div>
      {err ? <ApiErrorBanner error={err} /> : null}
    </>
  );
}

function Signals({ onChange }: { onChange: () => void }) {
  const list = useApi(() => request<{ items: Sig[] }>("/tradingos/admin/signals"));
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [coin, setCoin] = useState("");
  const [analysis, setAnalysis] = useState<{ coin: string; decision: string; reasons: string[] } | null>(null);
  async function act(fn: () => Promise<unknown>, ok: string) {
    setErr(null);
    try { await fn(); setMsg(ok); await list.reload(); onChange(); } catch (e) { setErr(e); }
  }
  return (
    <Card title="Signals" actions={<button className="btn btn-sm btn-primary" onClick={() => void act(async () => {
      const r = await post<{ checked: number; created: number[]; no_trade: Record<string, string[]>; skipped?: string }>("/tradingos/admin/scan");
      setMsg(r.skipped ? `Not checked: ${r.skipped}` : `Checked ${r.checked} markets: ${r.created.length ? `new signal #${r.created.join(", #")}` : "no clear setup anywhere right now"}`);
    }, "")}>Check markets now</button>}>
      {msg && <p className="banner banner-info">{msg}</p>}
      {err ? <ApiErrorBanner error={err} /> : null}
      <form className="btn-group" onSubmit={(e) => { e.preventDefault(); void request<typeof analysis>(`/tradingos/admin/analyze?coin=${encodeURIComponent(coin)}`).then(setAnalysis).catch(setErr); }}>
        <input className="input" placeholder="Check one coin (e.g. SOL)" value={coin} onChange={(e) => setCoin(e.target.value.toUpperCase())} />
        <button className="btn btn-sm" disabled={!coin}>Check</button>
      </form>
      {analysis && <p className="small"><b>{analysis.coin}: {analysis.decision}</b> · {analysis.reasons.join(" · ")}</p>}
      {list.loading && !list.data ? <Loading /> : !list.data?.items.length ? <Empty>No signals yet. A signal is only made when a setup is clear, so most checks find nothing.</Empty> : (
        <table className="table">
          <thead><tr><th>#</th><th>Market</th><th>Prices</th><th>Status</th><th>Members in</th><th>Result</th><th /></tr></thead>
          <tbody>
            {list.data.items.map((s) => (
              <tr key={s.id}>
                <td>#{String(s.number).padStart(3, "0")}</td>
                <td><b>{s.coin} {s.side}</b><div className="muted small">{s.timeframe} · {relativeTime(s.created_at)}</div></td>
                <td className="small">Buy zone {s.entry[0]}–{s.entry[1]}<br />Stop {s.stop} · Target {s.target} · {s.leverage[0]}–{s.leverage[1]}x</td>
                <td><Badge tone={TONE[s.status]}>{s.status_label}</Badge>{s.tg_error && <div className="small" style={{ color: "#ffb4ad" }}>{s.tg_error}</div>}</td>
                <td className="small">{s.participation.enter ?? 0} in · {s.participation.reject ?? 0} out</td>
                <td>{s.model_pnl_pct == null ? "—" : `${s.model_pnl_pct > 0 ? "+" : ""}${s.model_pnl_pct}%`}</td>
                <td className="btn-group">
                  {(s.status === "PENDING_REVIEW" || (s.status === "WAITING" && !s.published_at)) &&
                    <button className="btn btn-sm btn-primary" onClick={() => {
                      if (window.confirm(`Publish #${s.number} ${s.coin} ${s.side} to the Telegram signal group? Members with AUTO on will enter it (real money for LIVE accounts).`))
                        void act(() => post(`/tradingos/admin/signals/${s.id}/publish`), `#${s.number} published to Telegram.`);
                    }}>Publish</button>}
                  {!["TARGET_HIT", "STOP_HIT", "CLOSED", "EXPIRED", "CANCELLED"].includes(s.status) &&
                    <button className="btn btn-sm btn-ghost" onClick={() => {
                      if (window.confirm(`Cancel signal #${s.number} ${s.coin} ${s.side}? It stops being tracked; members' open positions keep their own stop-loss and take-profit.`))
                        void act(() => post(`/tradingos/admin/signals/${s.id}/cancel`), `#${s.number} cancelled.`);
                    }}>Cancel</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

/** Push a REAL signal to Telegram right now, by hand: real prices, levels from the market's own volatility (ATR), long or
 *  short from its trend. The card says "Manual setup"; members with AUTO on enter it (within their limits, in their own mode)
 *  and it counts in the track record. Tracked like any other signal. */
export function PushNow({ onDone, onError }: { onDone: (msg: string) => void; onError: (e: unknown) => void }) {
  const [coin, setCoin] = useState("BTC");
  const [busy, setBusy] = useState(false);
  async function push() {
    const c = coin.trim().toUpperCase();
    if (!c || !window.confirm(`Post a real ${c} signal now? It goes to the Telegram signal group, and members with AUTO on will enter it.`)) return;
    setBusy(true);
    try {
      const sig = await post<Sig>("/tradingos/admin/manual-signal", { coin: c });
      onDone(`#${String(sig.number).padStart(3, "0")} ${sig.coin} ${sig.side} posted to Telegram.`);
    } catch (e) { onError(e); } finally { setBusy(false); }
  }
  return (
    <div className="tos-push">
      <b>📣 Push a signal now</b>
      <span className="muted small">Real prices, stop and target from the market's volatility, long or short from its trend. Marked "Manual setup". Members with AUTO on enter it within their own limits, and it counts in the track record.</span>
      <div className="btn-group">
        <input className="input" aria-label="Market" placeholder="BTC" value={coin} onChange={(e) => setCoin(e.target.value.toUpperCase())} style={{ maxWidth: 120 }} />
        {["BTC", "ETH", "SOL"].map((c) => <button key={c} type="button" className="btn btn-sm btn-ghost" onClick={() => setCoin(c)}>{c}</button>)}
        <button type="button" className="btn btn-primary" disabled={busy || !coin.trim()} onClick={() => void push()}>{busy ? "Posting…" : "Push to Telegram"}</button>
      </div>
    </div>
  );
}

function Markets() {
  const m = useApi(() => request<{ items: Mkt[] }>("/tradingos/admin/markets"), 15000);
  if (m.failure) return <ApiErrorBanner error={m.failure} />;
  if (!m.data) return <Loading />;
  return (
    <Card title="Markets (live, Hyperliquid)">
      <table className="table">
        <thead><tr><th>Market</th><th>Price</th><th>24h</th><th>Funding/h</th><th>OI</th><th>24h volume</th><th>Spread</th><th>Max lev</th></tr></thead>
        <tbody>{m.data.items.map((x) => (
          <tr key={x.coin}><td><b>{x.coin}</b></td><td>{x.price}</td><td style={{ color: x.change_24h >= 0 ? "#00e58f" : "#ff6b6b" }}>{x.change_24h > 0 ? "+" : ""}{x.change_24h}%</td>
            <td>{(x.funding_h * 100).toFixed(4)}%</td><td>{usdM(x.oi_usd)}</td><td>{usdM(x.volume_24h)}</td>
            <td>{x.spread_bps == null ? "—" : `${x.spread_bps.toFixed(1)} bps`}</td><td>{x.max_leverage}x</td></tr>
        ))}</tbody>
      </table>
    </Card>
  );
}

const MODE_NAME: Record<Mode, string> = { standard: "Normal", degen: "Degen" };
const MODE_PITCH: Record<Mode, string> = { standard: "Fewer, stricter 15-minute signals.",
                                           degen: "Many fast trades: more markets, opened at market straight away." };

/** Admin's Normal / Degen switch for the 15-minute Kiwi Kitchen: what each mode does, the backtest behind it, and how much it
 *  actually traded lately. Switching never touches signals that are already open. */
function TradingMode({ k, onSaved }: { k: KitchenModes; onSaved: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  async function pick(m: Mode) {
    if (m === k.current || busy) return;
    const d = k.modes.degen;
    const ask = m === "degen"
      ? `Switch to DEGEN mode?\n\n• ${d.coins.length} markets, up to ${d.max_open} signals open at once\n• ${d.auto_publish ? "signals post themselves" : "signals wait for your review"}${d.market_entry ? " and open at market" : ""}\n• backtest: about ${d.research.trades_per_day} trades a day at ${d.research.avg_r > 0 ? "+" : ""}${d.research.avg_r}R a trade (${d.research.note})`
      : "Switch to NORMAL mode?\n\nFewer, stricter 15-minute signals. Signals already open are not touched.";
    if (!window.confirm(ask)) return;
    setBusy(true); setErr(null);
    try {
      await request("/tradingos/admin/settings", { method: "PUT", body: JSON.stringify({ kitchen_mode: m }) });
      onSaved();
    } catch (e) { setErr(e); } finally { setBusy(false); }
  }
  const yes = (b: boolean, y: string, n: string) => (b ? y : n);
  const card = (m: Mode) => {
    const x = k.modes[m];
    const on = k.current === m;
    const r = x.research;
    return (
      <button key={m} type="button" role="radio" aria-checked={on} disabled={busy} onClick={() => void pick(m)}
              className={`tmode-card ${m === "degen" ? "is-degen" : "is-normal"}`}>
        <span className="tmode-head"><b>{MODE_NAME[m]}</b>{on ? <Badge tone={m === "degen" ? "warn" : "good"}>Active</Badge> : <span className="muted">Switch</span>}</span>
        <small className="muted">{MODE_PITCH[m]}</small>
        <dl>
          <dt>Markets</dt><dd>{x.coins.join(", ") || "—"}</dd>
          <dt>Setups</dt><dd>{x.models.length}</dd>
          <dt>30-minute check</dt><dd>{yes(x.confirm_30m, "Waits for it", "Skipped")}</dd>
          <dt>Entry</dt><dd>{yes(x.market_entry, "At market, straight away", "Inside the entry zone")}</dd>
          <dt>Posting</dt><dd>{yes(x.auto_publish, "Posts itself", "Waits for your Publish")}</dd>
          <dt>Most open at once</dt><dd>{x.max_open}</dd>
          <dt>Pause after a close</dt><dd>{x.cooldown_min} min</dd>
          <dt>Backtest</dt><dd>~{r.trades_per_day} trades/day · {r.avg_r > 0 ? "+" : ""}{r.avg_r}R a trade<br /><small className="muted">{r.note}</small></dd>
        </dl>
      </button>
    );
  };
  return (
    <Card title="Trading mode (15-minute Kiwi Kitchen)">
      <div className="tmode-grid" role="radiogroup" aria-label="Trading mode">{card("standard")}{card("degen")}</div>
      <p className="tmode-foot">
        Opened by the Kitchen: <b>{k.signals_24h}</b> in the last 24 h · <b>{k.signals_7d}</b> in 7 days.{" "}
        {!k.publishing && <span className="muted">15-minute signals are off (shadow): the mode applies once they're switched on below. </span>}
        {k.live ? <Badge tone="bad">Real money on for opted-in members</Badge> : <span className="muted">Paper only for members.</span>}
      </p>
      {err ? <ApiErrorBanner error={err} /> : null}
    </Card>
  );
}

function SettingsForm({ s, onSaved }: { s: Settings; onSaved: () => void }) {
  const [v, setV] = useState({ ...s, trend_pullback_v1: !!s.strategies.trend_pullback_v1, intraday_15m_v1: !!s.strategies.intraday_15m_v1,
                               intraday_live: !!s.strategies.intraday_live,
                               risk_v2_mode: (s.strategies.risk_v2 as { mode?: string } | undefined)?.mode === "enforce" ? "enforce" : "advisory",
                               funding_alerts_on: (s.strategies.funding_alerts as { on?: boolean } | undefined)?.on ?? false,
                               funding_alert_min_apr: (s.strategies.funding_alerts as { min_net_apr?: number } | undefined)?.min_net_apr ?? 50,
                               main_chat_autodelete_min: (s.strategies.autodelete as { minutes?: number } | undefined)?.minutes ?? 5 });
  const [err, setErr] = useState<unknown>(null);
  const [ok, setOk] = useState(false);
  const set = (k: string, val: unknown) => { setV({ ...v, [k]: val }); setOk(false); };
  async function save() {
    setErr(null);
    // the two switches that let money move without the owner pressing anything: ask once when one is being turned on
    const risky = [v.intraday_live && !s.strategies.intraday_live && "15-minute signals will trade REAL MONEY for members who opted in",
                   v.auto_publish && !s.auto_publish && "new signals will be posted to Telegram (and entered by AUTO members) without your review"].filter(Boolean);
    if (risky.length && !window.confirm(`Save these settings?\n\n• ${risky.join("\n• ")}`)) return;
    try {
      const { strategies: _s, ...body } = v;  // eslint-disable-line @typescript-eslint/no-unused-vars
      await request("/tradingos/admin/settings", { method: "PUT", body: JSON.stringify(body) });
      setOk(true);
      onSaved();
    } catch (e) { setErr(e); }
  }
  const check = (k: "scanner_enabled" | "auto_publish" | "brief_enabled" | "trend_pullback_v1" | "intraday_15m_v1" | "intraday_live", label: string, hint: string) => (
    <label className="tba-check"><input type="checkbox" checked={v[k]} onChange={(e) => set(k, e.target.checked)} /><span><b>{label}</b><small className="muted">{hint}</small></span></label>
  );
  const num = (k: "brief_hour" | "universe_size" | "min_volume_usd" | "max_spread_bps" | "max_open_signals" | "min_rr", label: string) => (
    <label className="tba-field"><span>{label}</span><input type="number" value={v[k]} onChange={(e) => set(k, Number(e.target.value))} /></label>
  );
  return (
    <Card title="Signal settings">
      <div className="tba-settings">
        <h3 className="tos-h">Where signals come from</h3>
        {check("scanner_enabled", "Look for signals", "Checks the biggest markets every 15 minutes. Off: no new signals at all.")}
        {check("trend_pullback_v1", "1-hour trend signals", "Waits for a market in a clear trend to dip back, then signals in the trend's direction. A few a week.")}
        {check("intraday_15m_v1", "15-minute Kiwi Kitchen signals", "Faster signals on BTC and ETH, checked every 45 seconds. Off: the Kitchen only shows what it would do on the Earn page.")}
        <h3 className="tos-h">Posting</h3>
        {check("auto_publish", "Post signals without asking me", "Off: each new signal waits here for you to press Publish. On: posted to Telegram straight away.")}
        {check("brief_enabled", "Daily brief in the signal group", "Once a day: market overview, the most promising setup with its chart, and how /signal ideas played out.")}
        <div className="tba-grid">
          <label className="tba-field"><span>Signal group on Telegram</span><input value={v.publish_chat} onChange={(e) => set("publish_chat", e.target.value)} /></label>
          <label className="tba-field"><span>Main chat: also announce open / close (empty = off)</span><input value={v.main_chat ?? ""} placeholder="@thepricechat" onChange={(e) => set("main_chat", e.target.value)} /></label>
          <label className="tba-field"><span>Delete the bot's messages in the main chat after (minutes, 0 = keep; never in Bot Chat)</span>
            <input type="number" min={0} max={1440} value={v.main_chat_autodelete_min} onChange={(e) => set("main_chat_autodelete_min", Number(e.target.value))} /></label>
          {num("max_open_signals", "Most signals open at once")}
          {num("brief_hour", "Daily brief hour (UTC, 0-23)")}
          <label className="tba-field"><span>/chart asked in the main chat</span>
            <select className="input" value={v.chart_routing ?? "anywhere"} onChange={(e) => set("chart_routing", e.target.value)}>
              <option value="anywhere">Answer in the main chat (as before)</option>
              <option value="bot_chat">Post the chart in the signal group (Bot Chat), reply with a link</option>
            </select></label>
        </div>
        <h3 className="tos-h">Funding Arb alerts</h3>
        <label className="tba-check"><input type="checkbox" checked={v.funding_alerts_on} onChange={(e) => set("funding_alerts_on", e.target.checked)} />
          <span><b>Post wide funding gaps to the signal group</b><small className="muted">Every 10 minutes: a coin whose best cross-venue funding gap, after estimated taker fees on both legs, clears the line below. The widest one only, once per coin per 24 h unless the gap doubles, at most 4 a day.</small></span></label>
        <div className="tba-grid">
          <label className="tba-field"><span>Post when the net gap is at least (% a year)</span>
            <input type="number" value={v.funding_alert_min_apr} onChange={(e) => set("funding_alert_min_apr", Number(e.target.value))} /></label>
        </div>
        <Advanced label="Fine-tuning (markets, filters, real money)">
          {check("intraday_live", "Let 15-minute signals trade real money", "Off: 15-minute signals are practice (paper) trades only. On: also real trades, only for members who opted in.")}
          <div className="tba-grid">
            <label className="tba-field"><span>Extra risk checks on every entry (spread, slippage, exposure, correlation, margin, costs, 7-day loss)</span>
              <select className="input" value={v.risk_v2_mode} onChange={(e) => set("risk_v2_mode", e.target.value)}>
                <option value="advisory">Show warnings only (trades as before)</option>
                <option value="enforce">Refuse entries that fail a check</option>
              </select></label>
            <label className="tba-field"><span>Chart timeframe (1-hour signals)</span>
              <select className="input" value={v.timeframe} onChange={(e) => set("timeframe", e.target.value)}>{["15m", "1h", "4h"].map((t) => <option key={t}>{t}</option>)}</select></label>
            {num("universe_size", "How many markets to check (biggest first)")}
            {num("min_volume_usd", "Skip markets that trade less than (USD a day)")}
            {num("max_spread_bps", "Skip markets with a spread over (bps)")}
            {num("min_rr", "Smallest reward vs risk (after costs)")}
            <label className="tba-field"><span>Only these markets (optional)</span><input placeholder="BTC,ETH,SOL" value={v.allowed_coins} onChange={(e) => set("allowed_coins", e.target.value.toUpperCase())} /></label>
          </div>
        </Advanced>
        <div className="btn-group"><button className="btn btn-primary" onClick={() => void save()}>Save</button>{ok && <Badge tone="good">Saved</Badge>}</div>
        {err ? <ApiErrorBanner error={err} /> : null}
      </div>
    </Card>
  );
}

/** Vortex trade checks (/help <trade> in the bot): on/off, daily limits, the boost rank, the worker token (write-only) and
 *  when Vortex last asked for work. */
function VortexAssist({ v, onSaved }: { v: VortexCfg; onSaved: () => void }) {
  const [f, setF] = useState({ vortex_on: v.enabled, vortex_daily_public: v.daily_public, vortex_daily_ranked: v.daily_ranked, vortex_min_rank: v.min_rank });
  const [tok, setTok] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [ok, setOk] = useState<string | null>(null);
  const set = (k: keyof typeof f, val: unknown) => { setF({ ...f, [k]: val }); setOk(null); };
  async function call(path: string, method: string, body?: unknown, done = "Saved"): Promise<boolean> {
    setErr(null);
    try {
      await request(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
      setOk(done);
      onSaved();
      return true;
    } catch (e) { setErr(e); return false; }
  }
  function generate() {
    const b = new Uint8Array(24);
    crypto.getRandomValues(b);
    setTok("vx_" + Array.from(b, (x) => x.toString(16).padStart(2, "0")).join(""));
  }
  const num = (k: "vortex_daily_public" | "vortex_daily_ranked" | "vortex_min_rank", label: string) => (
    <label className="tba-field"><span>{label}</span><input type="number" min={0} value={f[k]} onChange={(e) => set(k, Number(e.target.value))} /></label>
  );
  return (
    <Card title="Vortex trade checks (/help <trade>)">
      <div className="tba-settings">
        <p className="muted">
          Vortex {v.online ? <Badge tone="good">online</Badge> : <Badge tone="neutral">away</Badge>} · last seen {relativeTime(v.last_seen)}
          {v.pending ? ` · ${v.pending} waiting` : ""}. While Vortex is away the bot answers with its own assessment.
        </p>
        <label className="tba-check"><input type="checkbox" checked={f.vortex_on} onChange={(e) => set("vortex_on", e.target.checked)} />
          <span><b>Answer "/help &lt;trade&gt;" with a risk check</b><small className="muted">In groups and private chats. /help alone still lists the commands.</small></span></label>
        <div className="tba-grid">
          {num("vortex_daily_public", "Checks a day per Telegram user")}
          {num("vortex_daily_ranked", "Checks a day with a linked Kiwi Passport at the rank below")}
          {num("vortex_min_rank", "Kiwi Rank for the higher limit")}
        </div>
        <div className="btn-group"><button className="btn btn-primary" onClick={() => void call("/tradingos/admin/settings", "PUT", f)}>Save</button></div>
        <h3 className="tos-h">Vortex's token</h3>
        <p className="muted">{v.token_set ? "A token is set. It's never shown again: set a new one to replace it." : "No token yet: Vortex can't connect."}
          {" "}Put the same value in Vortex's .env as VORTEX_ASSIST_TOKEN.</p>
        <div className="tba-grid">
          <label className="tba-field"><span>New token (at least 24 characters)</span>
            <input value={tok} autoComplete="off" spellCheck={false} onChange={(e) => setTok(e.target.value.trim())} /></label>
        </div>
        <div className="btn-group">
          <button className="btn" onClick={generate}>Generate</button>
          <button className="btn btn-primary" disabled={tok.length < 24} onClick={() => void call("/tradingos/admin/vortex-token", "PUT", { token: tok }, "Token saved").then((done) => { if (done) setTok(""); })}>Save token</button>
          {v.token_set && <button className="btn" onClick={() => void call("/tradingos/admin/vortex-token", "DELETE", undefined, "Token cleared")}>Clear token</button>}
          {ok && <Badge tone="good">{ok}</Badge>}
        </div>
        {err ? <ApiErrorBanner error={err} /> : null}
      </div>
    </Card>
  );
}

function Audit() {
  const a = useApi(() => request<{ items: { at: string; actor: string; action: string; signal_id: number | null; details: Record<string, unknown> }[] }>("/tradingos/admin/audit"));
  if (a.failure) return <ApiErrorBanner error={a.failure} />;
  if (!a.data) return <Loading />;
  return (
    <Card title="Audit log">
      {!a.data.items.length ? <Empty>Nothing yet.</Empty> : (
        <ul className="tba-audit">{a.data.items.map((x, i) => (
          <li key={i}><span className="muted">{new Date(x.at).toLocaleString()}</span> <b>{x.action}</b> by {x.actor}{x.signal_id ? ` · signal ${x.signal_id}` : ""}
            {Object.keys(x.details).length ? <code> {JSON.stringify(x.details)}</code> : null}</li>
        ))}</ul>
      )}
    </Card>
  );
}

interface ExecPos { id: number; account_id: number; mode: string; coin: string; side: string; status: string; size: number; entry_px: number | null; stop: number;
  tp1: number; tp2: number; realized_pnl: number; error: string | null; opened_at: string }
interface Exec { live_allowlist: string; global_paused: boolean; exchange: { ok: boolean; error?: string };
  health: { positions_by_status: Record<string, number>; protection_failed: number; live_accounts: number };
  accounts: { id: number; username: string | null; mode: string; paused: boolean; paused_reason: string | null; auto: boolean; testnet_agent: boolean;
    mainnet_agent: boolean; live_enabled: boolean; max_leverage: number; max_loss: number; checks_passed: boolean }[];
  positions: ExecPos[]; orders: { id: number; position_id: number; kind: string; is_buy: boolean; size: number; price: number; status: string; oid: number | null;
    filled: number; avg_px: number | null; error: string | null; at: string }[];
  results: Record<string, { trades: number; net: number; fees: number; win_rate: number | null }> }
const POS_TONE: Record<string, Tone> = { OPEN: "good", OPENING: "info", CLOSED: "neutral", FAILED: "warn", PROTECTION_FAILED: "bad" };

function Execution() {
  const e = useApi(() => request<Exec>("/tradingos/admin/execution"), 15000);
  const [allow, setAllow] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  if (e.failure) return <ApiErrorBanner error={e.failure} />;
  if (!e.data) return <Loading />;
  const d = e.data;
  async function act(fn: () => Promise<unknown>) { setErr(null); try { await fn(); await e.reload(); } catch (x) { setErr(x); } }
  return (
    <div className="stack">
      {err ? <ApiErrorBanner error={err} /> : null}
      {d.health.protection_failed > 0 && <p className="banner banner-warn">{d.health.protection_failed} position(s) had a protection failure: check them below.</p>}
      <div className="stats">
        <Stat label="Exchange" value={d.exchange.ok ? "Connected" : "Down"} tone={d.exchange.ok ? "good" : "bad"} hint={d.exchange.error} />
        <Stat label="Open positions" value={d.health.positions_by_status.OPEN ?? 0} />
        <Stat label="LIVE accounts" value={d.health.live_accounts} tone={d.health.live_accounts ? "warn" : "neutral"} />
        {(["PAPER", "TESTNET", "LIVE"] as const).map((m) => (
          <Stat key={m} label={`${m} results`} value={sUsd(d.results[m].net)}
                hint={`${d.results[m].trades} closed${d.results[m].win_rate != null ? ` · ${d.results[m].win_rate}% wins` : ""}`} />
        ))}
      </div>
      <p className="muted small">Telegram trading (mainnet switch, emergency stop) is under <b>Telegram → Telegram trading</b> in the sidebar.</p>
      <Card title="LIVE allowlist">
        <p className="muted small">Kiwi Passport usernames allowed to authorize a mainnet API wallet and, after passing the pre-live checks, enable LIVE themselves.
          Everyone else stays in PAPER or TESTNET.</p>
        <form className="btn-group" onSubmit={(ev) => { ev.preventDefault();
          if (!window.confirm(`Save the LIVE allowlist as "${allow ?? d.live_allowlist}"? Listed members can enable real-money trading after passing the pre-live checks.`)) return;
          void act(() => request("/tradingos/admin/execution/allowlist", { method: "PUT", body: JSON.stringify({ live_allowlist: allow ?? d.live_allowlist }) })); }}>
          <input className="input" placeholder="username" value={allow ?? d.live_allowlist} onChange={(ev) => setAllow(ev.target.value)} />
          <button className="btn btn-sm btn-primary">Save</button>
        </form>
      </Card>
      <Card title="Accounts">
        {!d.accounts.length ? <Empty>No trading accounts yet.</Empty> : (
          <table className="table">
            <thead><tr><th>Member</th><th>Mode</th><th>API wallets</th><th>Limits</th><th>State</th><th /></tr></thead>
            <tbody>{d.accounts.map((a) => (
              <tr key={a.id}>
                <td><b>{a.username ?? `#${a.id}`}</b></td>
                <td><Badge tone={a.mode === "LIVE" ? "warn" : "neutral"}>{a.mode}</Badge></td>
                <td className="small">testnet {a.testnet_agent ? "✓" : "—"} · mainnet {a.mainnet_agent ? "✓" : "—"}{a.checks_passed ? " · checks ✓" : ""}</td>
                <td className="small">{a.max_leverage}x · max loss ${a.max_loss}{a.auto ? " · AUTO" : ""}</td>
                <td className="small">{a.paused ? `stopped${a.paused_reason ? `: ${a.paused_reason}` : ""}` : "active"}</td>
                <td>{!a.paused && <button className="btn btn-sm btn-kill-sm" onClick={() => void act(() => post(`/tradingos/admin/execution/accounts/${a.id}/stop`))}>Stop new trades</button>}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>
      <Card title="Positions">
        {!d.positions.length ? <Empty>No positions yet.</Empty> : (
          <table className="table">
            <thead><tr><th>#</th><th>Mode</th><th>Market</th><th>Size @ entry</th><th>SL · TP1 · TP2</th><th>Status</th><th>P&amp;L</th></tr></thead>
            <tbody>{d.positions.map((p) => (
              <tr key={p.id}>
                <td>{p.id}<div className="muted small">acct {p.account_id} · {relativeTime(p.opened_at)}</div></td><td>{p.mode}</td><td><b>{p.coin} {p.side}</b></td>
                <td className="small">{p.size} @ {p.entry_px ?? "—"}</td><td className="small">{p.stop} · {p.tp1} · {p.tp2}</td>
                <td><Badge tone={POS_TONE[p.status] ?? "neutral"}>{p.status}</Badge>{p.error && <div className="small" style={{ color: "#ffb4ad" }}>{p.error}</div>}</td>
                <td>{sUsd(p.realized_pnl)}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>
      <Card title="Execution log (orders)">
        {!d.orders.length ? <Empty>No orders yet.</Empty> : (
          <table className="table">
            <thead><tr><th>When</th><th>Pos</th><th>Kind</th><th>Side · size · price</th><th>Exchange answer</th></tr></thead>
            <tbody>{d.orders.map((o) => (
              <tr key={o.id}>
                <td className="small">{relativeTime(o.at)}</td><td>{o.position_id}</td><td>{o.kind}</td>
                <td className="small">{o.is_buy ? "buy" : "sell"} {o.size} @ {o.price}</td>
                <td className="small"><b>{o.status}</b>{o.oid ? ` · oid ${o.oid}` : ""}{o.filled ? ` · filled ${o.filled} @ ${o.avg_px}` : ""}{o.error ? ` · ${o.error}` : ""}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

interface PerfGroup { strategy?: string; timeframe?: string; signals: number; closed: number; outcomes: Record<string, number>; win_rate: number | null;
  avg_pct: number | null; avg_pct_after_slippage: number | null; avg_r: number | null; total_r: number | null; profit_factor: number | null;
  total_pct: number; max_drawdown_pct: number; expiry_rate: number | null; fill_rate: number | null; median_hold_h: number | null; open: number;
  unrealized_pct: number; fees_pct_per_trade: number; slippage_pct_per_trade: number | null; findings?: string[] }
interface Perf { label: string; generated_at: string; overall: PerfGroup; by_strategy: PerfGroup[]; findings: string[]; recommendations: string[]; backtest_reference: string }

const n = (x: number | null | undefined, suffix = "") => (x == null ? "—" : `${x}${suffix}`);

/** The internal signal performance report: real published signals only, costs shown, findings from the numbers. */
function Performance() {
  const p = useApi(() => request<Perf>("/tradingos/admin/performance"));
  if (p.failure) return <ApiErrorBanner error={p.failure} />;
  if (!p.data) return <Loading />;
  const r = p.data;
  const rows = [{ ...r.overall, strategy: "All signals", timeframe: "" }, ...r.by_strategy];
  return (
    <Card title="Signal performance (internal)">
      <p className="muted small">{r.label} · {relativeTime(r.generated_at)}</p>
      <table className="table">
        <thead><tr><th>Strategy</th><th>Closed</th><th>Win</th><th>Avg</th><th>After slippage</th><th>Avg R</th><th>PF</th><th>Expired</th><th>Hold (h)</th><th>Open · unrealized</th></tr></thead>
        <tbody>{rows.map((g) => (
          <tr key={`${g.strategy}${g.timeframe}`}><td><b>{g.strategy}</b> {g.timeframe}</td><td>{g.closed} / {g.signals}</td><td>{n(g.win_rate, "%")}</td>
            <td>{n(g.avg_pct, "%")}</td><td style={{ color: (g.avg_pct_after_slippage ?? 0) < 0 ? "#ff6b6b" : undefined }}>{n(g.avg_pct_after_slippage, "%")}</td>
            <td>{n(g.avg_r)}</td><td>{n(g.profit_factor)}</td><td>{n(g.expiry_rate, "%")}</td><td>{n(g.median_hold_h)}</td><td>{g.open} · {g.unrealized_pct}%</td></tr>
        ))}</tbody>
      </table>
      <h3 className="tos-h">Findings</h3>
      {r.findings.length ? <ul>{r.findings.map((f) => <li key={f}>{f}</li>)}</ul> : <Empty>No problems found in the numbers.</Empty>}
      <h3 className="tos-h">Recommendations</h3>
      <ul>{r.recommendations.map((f) => <li key={f}>{f}</li>)}</ul>
      <p className="muted small">{r.backtest_reference} Nothing on this page changes a strategy.</p>
    </Card>
  );
}

interface DM { n: number; win_rate?: number; avg_r?: number; ci90?: number[] | null; total_r?: number; profit_factor?: number | null; max_drawdown_r?: number;
  avg_mae_r?: number | null; avg_mfe_r?: number | null; avg_cost_r?: number | null; enough?: boolean }
interface DecisionRec { id: number; symbol: string; direction: string; model: string; strategy_version: string; market_regime: string | null; market_regime_label: string | null;
  regime_confidence: number | null; signal_status: string; rejected_by: string | null; reason: string; score: number | null; risk_reward_ratio: number | null;
  estimated_cost_r: number | null; timestamp: string; signal_id: number | null; outcome: { status: string; r_net: number | null; mae_r: number | null; mfe_r: number | null; exit: string | null } }
interface DReport { note: string; counts: Record<string, number>; approved: { taken: DM; ready_shadow: DM; all: DM }; rejected: DM;
  filters: (DM & { check: string; verdict: string })[]; by_regime: (DM & { regime: string; label: string })[]; by_version: (DM & { version: string })[];
  calibration: Record<string, { by: string; verdict: string; buckets: (DM & { bucket: string })[] }>; rules: string[] }

const r2 = (x: number | null | undefined) => (x == null ? "—" : `${x > 0 ? "+" : ""}${x.toFixed(2)}R`);
const DTONE: Record<string, Tone> = { READY: "good", REJECTED: "bad", WAITING_CONFIRMATION: "warn" };

function DRow({ name, m, extra }: { name: string; m: DM; extra?: string }) {
  return (
    <tr><td><b>{name}</b>{extra ? <small className="muted"> {extra}</small> : null}</td><td>{m.n}{m.n && !m.enough ? <small className="muted"> (small)</small> : null}</td>
      <td>{m.win_rate == null ? "—" : `${Math.round(m.win_rate * 100)}%`}</td>
      <td style={{ color: (m.avg_r ?? 0) < 0 ? "#ff6b6b" : undefined }}>{r2(m.avg_r)}</td>
      <td>{m.ci90 ? `${r2(m.ci90[0])} … ${r2(m.ci90[1])}` : "—"}</td><td>{m.profit_factor ?? "—"}</td><td>{r2(m.avg_mae_r)} / {r2(m.avg_mfe_r)}</td><td>{r2(m.avg_cost_r)}</td></tr>
  );
}

const DHEAD = <thead><tr><th>Group</th><th>Resolved</th><th>Win</th><th>Avg (net)</th><th>90% range</th><th>PF</th><th>MAE / MFE</th><th>Costs</th></tr></thead>;

/** Engine v2: every kitchen candidate as a structured decision, and its SHADOW outcome (hypothetical, after costs). */
function Decisions() {
  const d = useApi(() => request<{ report: DReport; recent: DecisionRec[] }>("/tradingos/admin/decisions?days=30&limit=40"));
  if (d.failure) return <ApiErrorBanner error={d.failure} />;
  if (!d.data) return <Loading />;
  const { report: r, recent } = d.data;
  return (
    <>
      <Card title="Approved vs rejected (shadow trading, last 30 days)">
        <p className="muted small"><Badge tone="warn">HYPOTHETICAL</Badge> {r.note}</p>
        <div className="tba-grid">
          <Stat label="Candidates" value={String((r.counts.READY ?? 0) + (r.counts.REJECTED ?? 0) + (r.counts.WAITING_CONFIRMATION ?? 0))} />
          <Stat label="Passed every check" value={String(r.counts.READY ?? 0)} />
          <Stat label="Rejected" value={String(r.counts.REJECTED ?? 0)} />
          <Stat label="Outcome pending" value={String(r.counts.outcome_pending ?? 0)} />
        </div>
        <table className="table">{DHEAD}<tbody>
          <DRow name="Passed: published" m={r.approved.taken} />
          <DRow name="Passed: shadow (strategy off)" m={r.approved.ready_shadow} />
          <DRow name="Rejected (all filters)" m={r.rejected} />
        </tbody></table>
      </Card>
      <Card title="Each filter: what it rejected">
        {!r.filters.length ? <Empty>No rejected candidates resolved yet.</Empty> : (
          <table className="table">{DHEAD}<tbody>{r.filters.map((f) => <DRow key={f.check} name={f.check} m={f} extra={f.verdict} />)}</tbody></table>
        )}
      </Card>
      <Card title="By market regime and strategy version">
        <table className="table">{DHEAD}<tbody>
          {r.by_regime.map((g) => <DRow key={g.regime} name={g.label} m={g} />)}
          {r.by_version.map((g) => <DRow key={g.version} name={g.version} m={g} />)}
        </tbody></table>
      </Card>
      <Card title="Do higher scores do better? (calibration)">
        {Object.values(r.calibration).map((c) => (
          <div key={c.by}>
            <h3 className="tos-h">{c.by === "score" ? "Signal quality score" : "Kiwi ML estimate"} · <span className="muted">{c.verdict}</span></h3>
            <table className="table">{DHEAD}<tbody>{c.buckets.map((b) => <DRow key={b.bucket} name={b.bucket} m={b} />)}</tbody></table>
          </div>
        ))}
        <ul className="muted small">{r.rules.map((x) => <li key={x}>{x}</li>)}</ul>
      </Card>
      <Card title="Latest decisions">
        {!recent.length ? <Empty>No candidates yet: the kitchen records one each time a 15-minute setup appears.</Empty> : (
          <table className="table">
            <thead><tr><th>When</th><th>Market</th><th>Decision</th><th>Regime</th><th>Score</th><th>R after costs</th><th>Outcome</th></tr></thead>
            <tbody>{recent.map((x) => (
              <tr key={x.id}><td>{relativeTime(x.timestamp)}</td><td><b>{x.symbol}</b> {x.direction} <small className="muted">{x.model} · {x.strategy_version}</small></td>
                <td><Badge tone={DTONE[x.signal_status] ?? "neutral"}>{x.signal_status}</Badge>{x.signal_id ? <small className="muted"> signal</small> : null}
                  {x.rejected_by ? <div className="muted small">{x.reason}</div> : null}</td>
                <td>{x.market_regime_label ?? "—"}{x.regime_confidence != null ? <small className="muted"> {Math.round(x.regime_confidence * 100)}%</small> : null}</td>
                <td>{x.score ?? "—"}</td><td>{r2(x.risk_reward_ratio)}</td>
                <td>{x.outcome.status === "DONE" ? <>{r2(x.outcome.r_net)} <small className="muted">{x.outcome.exit}</small></> : <small className="muted">{x.outcome.status.toLowerCase()}</small>}</td></tr>
            ))}</tbody>
          </table>
        )}
      </Card>
    </>
  );
}
