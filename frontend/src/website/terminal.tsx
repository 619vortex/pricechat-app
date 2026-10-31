import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { MemberApiError, memberApi, trackedRef } from "./members";
import { reducedMotion } from "./motion";
import { okToSaveLimits } from "./riskLimits";
import { playSound } from "./sound";
import { walletError } from "./wallet";
import { site } from "./websiteData";
import "./terminal.css";

/** KIWI TRADING BOT: what a connected trader sees on /earn. Three things, top to bottom: Kiwi's chat (the real published
 *  signals as messages, plus what actually happened to them and to your own positions), your positions (exchange style, with
 *  the balance strip above them) and a collapsed history. Setup and limits live in two dialogs, never on the page.
 *  Every number and every action goes through the server (the same records the Telegram bot uses). Simulated (paper) or
 *  test-network activity is always labelled as such, and nothing trades without the member's confirmation. */

export type Mode = "PAPER" | "TESTNET" | "LIVE";
type Network = "TESTNET" | "LIVE";
interface Limits { max_leverage: number; max_loss_per_trade_usd: number; max_allocation_pct: number; max_positions: number; daily_loss_limit_usd: number; allowed_coins: string }
interface Result { trades: number; net: number; fees: number; win_rate: number | null }
export interface Account {
  mode: Mode; paused: boolean; paused_reason: string | null; limits: Limits; caps: Record<string, [number, number]>;
  auto: { enabled: boolean; until: string | null };
  agents: Record<Network, { address: string | null; approved: boolean }>;
  live: { allowed: boolean; enabled: boolean; checks: { at?: string; passed?: boolean; items?: Record<string, { ok: boolean; detail: string }> } };
  paper_equity: number; daily_pnl: number; positions: { id: number; mode: Mode; status: string }[]; results: Record<Mode, Result>;
  style?: Style;
}
export interface Style {
  timeframe: "1h" | "15m" | "both"; frequency: "standard" | "active"; max_trades_per_day: number; cooldown_minutes: number;
  effective: { trades_per_day: number; cooldown_minutes: number }; trades_today: number; intraday_live: boolean;
  intraday_live_available: boolean; intraday_live_phrase: string; caps: { max_trades_per_day: [number, number]; cooldown_minutes: [number, number] };
}

/** Trading style: which signals AUTO takes (1h, 15m or both) and how often (standard, or active with your own daily cap and
 *  cooldown). 15m signals trade PAPER until you type the opt-in phrase, and only once the owner switched 15m money trading on. */
function StyleSection({ s, mode, busy, put }: { s: Style; mode: Mode; busy: string | null; put: (b: Record<string, unknown>, ok?: string) => Promise<unknown> }) {
  const [perDay, setPerDay] = useState(s.max_trades_per_day);
  const [cool, setCool] = useState(s.cooldown_minutes);
  const [phrase, setPhrase] = useState("");
  const tf: [Style["timeframe"], string][] = [["1h", "1h signals"], ["15m", "15m signals"], ["both", "Both"]];
  return (
    <>
      <h4>Trading style</h4>
      <div className="td-row" role="group" aria-label="Timeframe">
        {tf.map(([v, label]) => (
          <button key={v} className={`td-mode${s.timeframe === v ? " is-on" : ""}`} aria-pressed={s.timeframe === v} disabled={busy !== null}
                  onClick={() => void put({ timeframe: v }, `AUTO takes ${label.toLowerCase()} ✓`)}>{label}</button>
        ))}
      </div>
      <p className="tb-sub">15m signals: a 15-minute entry confirmed by the 30-minute candle (the Kiwi Kitchen). More trades, each held up to 3 hours.</p>
      <div className="td-row" role="group" aria-label="Frequency">
        {(["standard", "active"] as const).map((v) => (
          <button key={v} className={`td-mode${s.frequency === v ? " is-on" : ""}`} aria-pressed={s.frequency === v} disabled={busy !== null}
                  onClick={() => void put({ frequency: v }, v === "active" ? "Active trading ✓" : "Standard trading ✓")}>{v === "active" ? "Active" : "Standard"}</button>
        ))}
      </div>
      {s.frequency === "active" && (
        <>
          <label className="td-field"><span>Trades per day</span>
            <input type="number" inputMode="numeric" value={perDay} min={s.caps.max_trades_per_day[0]} max={s.caps.max_trades_per_day[1]}
                   onChange={(e) => setPerDay(Number(e.target.value))} /><small>max</small></label>
          <label className="td-field"><span>Cooldown per coin</span>
            <input type="number" inputMode="numeric" value={cool} min={s.caps.cooldown_minutes[0]} max={s.caps.cooldown_minutes[1]}
                   onChange={(e) => setCool(Number(e.target.value))} /><small>min</small></label>
          <button className="kt-btn" disabled={busy !== null} onClick={() => void put({ max_trades_per_day: perDay, cooldown_minutes: cool }, "Frequency saved ✓")}>Save frequency</button>
        </>
      )}
      <p className="tb-sub">Now: up to {s.effective.trades_per_day} new trades a day ({s.trades_today} today)
        {s.effective.cooldown_minutes ? `, ${s.effective.cooldown_minutes} min before re-entering a coin` : ""}. The bot never trades just to reach a number.</p>
      {s.timeframe !== "1h" && (s.intraday_live ? (
        <div className="td-row"><span>15m signals may trade {mode === "PAPER" ? "TESTNET / LIVE" : mode} money{s.intraday_live_available ? "" : " (once the owner switches it on)"}</span>
          <button className="kt-btn" disabled={busy !== null} onClick={() => void put({ intraday_live: false }, "15m signals: paper only")}>Paper only</button></div>
      ) : (
        <>
          <p className="tb-sub">15m signals trade <b>PAPER</b> only. To let them use real money, type <b>{s.intraday_live_phrase}</b>.
            {s.intraday_live_available ? "" : " (Still being validated: real-money 15m trading isn't open yet, so this only records your choice.)"}</p>
          <div className="td-row">
            <input aria-label="Opt-in phrase" value={phrase} onChange={(e) => setPhrase(e.target.value)} placeholder={s.intraday_live_phrase} autoComplete="off" />
            <button className="kt-btn" disabled={busy !== null || phrase.trim().toUpperCase() !== s.intraday_live_phrase}
                    onClick={() => void put({ intraday_live: true, intraday_phrase: phrase }, "15m opt-in saved ✓")}>Opt in</button>
          </div>
        </>
      ))}
    </>
  );
}
type OrderState = "ACTIVE" | "SIMULATED" | "UNVERIFIED" | "NOT_ON_EXCHANGE" | "REJECTED" | "MISSING" | "CANCELLED" | "FILLED";
export interface Pos {
  id: number; signal_id: number; mode: Mode; coin: string; side: "LONG" | "SHORT"; status: string; via: string; size: number; size_usd: number | null;
  entry_px: number | null; mark: number | null; leverage: number; margin: number | null; liq_px: number | null; upnl: number | null; upnl_pct: number | null;
  stop: number; tp1: number; tp2: number; tp1_done: boolean; sl_status: OrderState; tp1_status: OrderState; tp2_status: OrderState;
  fees: number; funding: number | null; realized: number; opened_at: string | null; tp1_at?: string | null; warnings: string[]; error: string | null; test?: boolean;
}
interface Other { coin: string; side: string; size: number; entry: number; mark: number | null; value: number; upnl: number; margin: number; liq: number | null; leverage: number | null }
interface Hist { id: number; signal_id: number; test?: boolean; mode: Mode; coin: string; side: string; entry_px: number | null; exit_px: number | null; size: number;
  size_usd: number; leverage: number; net_pnl: number; fees: number; funding: number | null; duration_s: number | null; reason: string | null; closed_at: string | null }
type MyStatus = "NOT_ENTERED" | "ORDER_PENDING" | "POSITION_OPEN" | "POSITION_CLOSED" | "REJECTED";
/** How long AUTO runs: hours, or 0 = until the member turns it off. */
const AUTO_DURATIONS = [1, 4, 24, 72, 168, 720, 2160, 4320, 0];
function autoLabel(h: number): string {
  if (h === 0) return "Until I turn it off";
  if (h < 24) return `${h} hour${h > 1 ? "s" : ""}`;
  return `${h / 24} day${h > 24 ? "s" : ""}`;
}

interface Overview { mode: Mode; auto: boolean; auto_until: string | null; paused: boolean; realized_today: number; simulated: boolean;
  equity?: number | null; available?: number | null; margin_used?: number | null; unrealized?: number | null; exposure?: number | null }
export interface Terminal {
  overview: Overview; sync: { source: string; at: string | null; age_s: number | null; stale: boolean; error: string | null };
  positions: Pos[]; other_positions: Other[]; history: Hist[]; signals: Record<string, { status: MyStatus; position_id?: number; mode?: Mode; detail?: string }>;
  account: Account; global_paused: boolean;
}
export interface Sig { id: number; number: number; coin: string; side: string; status: string; status_label: string; entry: number[]; stop: number;
  target: number; rr: number; leverage: number[]; last_price: number | null; published_at: string | null; participation: Record<string, number>;
  strategy: string; timeframe?: string; reason?: string; tp2?: number; activated_at?: string | null; closed_at?: string | null;
  model_pnl_pct?: number | null; exit_price?: number | null; created_at?: string }
export interface Connection { rankLevel: number | null; rankOk: boolean; minRank: number; wallet: string | null; referral: boolean; group: boolean;
  bot: boolean; botUsername: string; groupUrl: string }

/** how often the terminal re-reads the server; the server shares one Hyperliquid read per account for 6 s (terminal.py SNAP_TTL) */
export const TERMINAL_POLL_MS = 7000;
const SLIPPAGE = 0.0008, TAKER_FEE = 0.00045;  // the server's own estimates (risk.py); the close's limit price allows 3× the slippage
const usd = (n: number | null | undefined, sign = false) => n == null ? "—"
  : `${n < 0 ? "−" : sign && n > 0 ? "+" : ""}$${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const px = (n: number | null | undefined) => (n == null ? "—" : n.toLocaleString("en-US", { maximumFractionDigits: n >= 1000 ? 1 : n >= 100 ? 2 : n >= 1 ? 4 : 6 }));
const qty = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 6 });
const tone = (n: number | null | undefined) => (n == null || n === 0 ? "" : n > 0 ? " is-up" : " is-down");
const pct = (n: number | null | undefined) => (n == null ? "" : `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(2)}%`);
const clock = (iso: string) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); };
const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" }) : "—");
const num = (n: number) => String(n).padStart(3, "0");
const lev = (l: number[]) => (l[0] === l[1] ? `${l[0]}x` : `${l[0]}–${l[1]}x`);
const ORDER_LABEL: Record<OrderState, string> = { ACTIVE: "Active on exchange", SIMULATED: "Simulated (paper)", UNVERIFIED: "Can't verify now",
  NOT_ON_EXCHANGE: "Not on exchange", REJECTED: "Rejected", MISSING: "Missing", CANCELLED: "Cancelled", FILLED: "Filled" };
const REASON: Record<string, string> = { manual_close: "closed by you", stop: "stop-loss", tp1: "TP1", tp2: "take profit", exchange: "closed on exchange",
  emergency_close: "emergency close" };
const LIMIT_LABEL: [keyof Limits, string, string][] = [["max_leverage", "Max leverage", "x"], ["max_loss_per_trade_usd", "Max loss per trade", "$"],
  ["max_allocation_pct", "Max margin per trade", "% equity"], ["max_positions", "Max open positions", ""], ["daily_loss_limit_usd", "Daily loss limit", "$"]];
const HL_DIRECT = "Manage it directly on Hyperliquid: app.hyperliquid.xyz → Positions → Close. Exchange-side stop-loss orders stay in place.";
/** what a non-live account is called on screen: never mistaken for real money */
const NOT_LIVE: Record<Exclude<Mode, "LIVE">, string> = { PAPER: "PRACTICE · SIMULATED", TESTNET: "TEST FUNDS · NOT REAL MONEY" };
const CLOSED_SIG = new Set(["TARGET_HIT", "STOP_HIT", "CLOSED", "EXPIRED", "CANCELLED"]);

function errText(e: unknown): string {
  if (e instanceof MemberApiError) return e.message;
  return `The server couldn't be reached. ${HL_DIRECT}`;
}

/** A number that eases its color when it changes (no flashing; nothing at all under reduced motion). */
function Live({ value, children, className = "" }: { value: number | null | undefined; children: ReactNode; className?: string }) {
  const prev = useRef(value);
  const [dir, setDir] = useState<"" | " is-tick-up" | " is-tick-down">("");
  useEffect(() => {
    if (prev.current != null && value != null && value !== prev.current && !reducedMotion()) {
      setDir(value > prev.current ? " is-tick-up" : " is-tick-down");
      const id = window.setTimeout(() => setDir(""), 900);
      prev.current = value;
      return () => window.clearTimeout(id);
    }
    prev.current = value;
  }, [value]);
  return <span className={`tt-live${tone(value)}${dir} ${className}`}>{children}</span>;
}

function Modal({ label, onClose, children }: { label: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="tt-veil" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="tt-modal" role="dialog" aria-modal="true" aria-label={label}>
        <button className="tt-x" aria-label="Close dialog" onClick={onClose}>✕</button>
        {children}
      </div>
    </div>
  );
}
/** "Synced 12s ago": the real age of the exchange read (the server's age at load + time since), ticking every second. */
function SyncedAgo({ sync, loadedAt }: { sync: Terminal["sync"]; loadedAt: number | null }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const id = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(id); }, []);
  if (sync.age_s == null || loadedAt == null) return null;
  const age = Math.max(0, sync.age_s + (now - loadedAt) / 1000);
  const ago = age < 90 ? `${Math.round(age)}s` : `${Math.round(age / 60)} min`;
  const src = sync.source === "paper" ? "Prices" : "Synced with Hyperliquid";
  return <p className={sync.stale ? "kt-warn" : "kt-sync"} aria-live="off">{src} {ago} ago{sync.stale ? ` · may be out of date${sync.error ? ` (${sync.error})` : ""}` : ""}</p>;
}

// ---------------------------------------------------------------- dialogs
function CloseDialog({ p, onDone, onClose }: { p: Pos; onDone: (a: Account) => void; onClose: () => void }) {
  const [frac, setFrac] = useState<0.25 | 0.5 | 1>(1);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<{ status: string; filled: number | null; avg_px: number | null; remaining: number; note: string | null } | null>(null);
  const price = p.mark ?? p.entry_px ?? 0;
  const notional = (p.size_usd ?? p.size * price) * frac;
  async function go() {
    setBusy(true); setErr(null);
    try {
      const out = await memberApi<Account & { close: NonNullable<typeof res> }>(`/members/trading/positions/${p.id}/close`,
        { method: "POST", body: JSON.stringify({ fraction: frac }) });
      setRes(out.close);
      playSound(out.close.status === "CLOSED" ? "cash" : "coin");
      onDone(out);
    } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  }
  return (
    <Modal label="Close position" onClose={onClose}>
      <h3>CLOSE POSITION</h3>
      <p className="tt-dl-line"><b className={`tt-side is-${p.side.toLowerCase()}`}>{p.coin} {p.side}</b> · {qty(p.size)} {p.coin} ({usd(p.size_usd)}) · {p.mode === "LIVE" ? "LIVE · real money" : NOT_LIVE[p.mode]}</p>
      <p className="tt-dl-line">Current price <b>{px(p.mark)}</b> · entry {px(p.entry_px)} · P&L <Live value={p.upnl}>{usd(p.upnl, true)}</Live></p>
      {res ? (
        <div className="tt-result" role="status">
          {res.status === "CLOSED" ? <p><b>Closed ✓</b> {p.mode === "PAPER" ? "Simulated fill:" : "Hyperliquid filled"} {qty(res.filled ?? 0)} {p.coin} at {px(res.avg_px)}.</p>
            : <p><b>Partly closed.</b> Filled {qty(res.filled ?? 0)} {p.coin} at {px(res.avg_px)}. Still open: {qty(res.remaining)} {p.coin}, with the
              stop-loss and targets resized to it.</p>}
          {res.note && <p className="tt-warn">{res.note}</p>}
          <button className="tb-btn" onClick={onClose}>Done</button>
        </div>
      ) : (
        <>
          <div className="tt-seg" role="radiogroup" aria-label="How much to close">
            {([0.25, 0.5, 1] as const).map((f) => (
              <button key={f} role="radio" aria-checked={frac === f} className={frac === f ? "is-on" : ""} onClick={() => setFrac(f)}>CLOSE {f * 100}%</button>
            ))}
          </div>
          <dl className="tt-mini">
            <div><dt>Closing</dt><dd>{qty(p.size * frac)} {p.coin} ≈ {usd(notional)}</dd></div>
            <div><dt>Est. execution impact</dt><dd>≈ {usd(notional * (SLIPPAGE + TAKER_FEE))}</dd></div>
            <div><dt>Worst accepted price</dt><dd>{px(price * (p.side === "LONG" ? 1 - 3 * SLIPPAGE : 1 + 3 * SLIPPAGE))}</dd></div>
          </dl>
          {p.mode === "PAPER" && <p className="kt-explain">Practice position: the close is simulated at the market price. No order reaches Hyperliquid.</p>}
          <p className="tb-sub">A reduce-only market order (it can only shrink this position, never open the other side). Market orders can fill
            worse than the current price (slippage), and pay the taker fee. {frac < 1 ? "The stop-loss and targets are resized to what's left." : "Its stop-loss and targets are cancelled after the fill."}</p>
          {err && <p className="site-error" role="alert">{err}</p>}
          <div className="tb-row">
            <button className="tt-danger" disabled={busy} onClick={() => void go()}>{busy ? "Sending…" : `Confirm: close ${frac * 100}%`}</button>
            <button className="tb-btn tb-btn-ghost" onClick={onClose}>Cancel</button>
          </div>
        </>
      )}
    </Modal>
  );
}

function CloseAllDialog({ positions, onDone, onClose }: { positions: Pos[]; onDone: (a: Account) => void; onClose: () => void }) {
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<{ requested: number; closed: { coin: string }[]; partial: { coin: string; remaining: number }[];
    failed: { coin: string; error: string }[] } | null>(null);
  const exposure = positions.reduce((s, p) => s + (p.size_usd ?? p.size * (p.mark ?? p.entry_px ?? 0)), 0);
  async function go() {
    setBusy(true); setErr(null);
    try {
      const out = await memberApi<Account & { close_all: NonNullable<typeof res> }>("/members/trading/positions/close-all",
        { method: "POST", body: JSON.stringify({ confirm_count: positions.length }) });
      setRes(out.close_all);
      playSound(out.close_all.failed.length ? "coin" : "cash");
      onDone(out);
    } catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  }
  return (
    <Modal label="Close all bot positions" onClose={onClose}>
      <h3 className="tt-red">CLOSE ALL BOT POSITIONS</h3>
      {res ? (
        <div className="tt-result" role="status">
          <p><b>{res.closed.length} of {res.requested} closed</b>{res.closed.length ? `: ${res.closed.map((c) => c.coin).join(", ")}` : ""}.</p>
          {res.partial.map((x) => <p key={x.coin} className="tt-warn">{x.coin}: partly closed, {qty(x.remaining)} still open (stop-loss resized).</p>)}
          {res.failed.map((x) => <p key={x.coin} className="tt-warn">{x.coin}: NOT closed. {x.error}</p>)}
          {(res.failed.length > 0 || res.partial.length > 0) && <p className="tb-sub">{HL_DIRECT}</p>}
          <button className="tb-btn" onClick={onClose}>Done</button>
        </div>
      ) : (
        <>
          <p className="tt-dl-line">This closes <b>{positions.length}</b> bot position{positions.length === 1 ? "" : "s"} (≈ <b>{usd(exposure)}</b> exposure) at
            market: {positions.map((p) => `${p.coin} ${p.side}`).join(", ")}.</p>
          {positions.every((p) => p.mode === "PAPER") && <p className="kt-explain">These are practice positions: the closes are simulated, no order reaches Hyperliquid.</p>}
          {positions.some((p) => p.mode === "LIVE") && <p className="kt-warn">Includes LIVE positions: real orders on your Hyperliquid account.</p>}
          <p className="tb-sub">Reduce-only market orders, one per position: they can't open reverse positions. Each one is checked against
            Hyperliquid's answer; anything not filled is reported, never shown as closed. Your other Hyperliquid positions aren't touched.</p>
          <label className="tt-check"><input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} /> I understand market orders can
            slip and this can't be undone.</label>
          {err && <p className="site-error" role="alert">{err}</p>}
          <div className="tb-row">
            <button className="tt-danger" disabled={!ok || busy} onClick={() => void go()}>{busy ? "Closing…" : `Close all ${positions.length}`}</button>
            <button className="tb-btn tb-btn-ghost" onClick={onClose}>Cancel</button>
          </div>
        </>
      )}
    </Modal>
  );
}

function AdjustDialog({ p, onDone, onClose }: { p: Pos; onDone: (a: Account) => void; onClose: () => void }) {
  const [v, setV] = useState({ stop: String(p.stop), tp1: String(p.tp1), tp2: String(p.tp2) });
  const [review, setReview] = useState<{ changes: string[]; loss_at_stop_usd: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const body = (dry: boolean) => {
    const b: Record<string, unknown> = { dry_run: dry };
    if (Number(v.stop) !== p.stop) b.stop = Number(v.stop);
    if (!p.tp1_done && Number(v.tp1) !== p.tp1) b.tp1 = Number(v.tp1);
    if (Number(v.tp2) !== p.tp2) b.tp2 = Number(v.tp2);
    return JSON.stringify(b);
  };
  async function send(dry: boolean) {
    setBusy(true); setErr(null);
    try {
      const out = await memberApi<{ result: { changes: string[]; loss_at_stop_usd: number }; account?: Account }>(`/members/trading/positions/${p.id}/protection`,
        { method: "POST", body: body(dry) });
      if (dry) setReview(out.result);
      else { setDone(true); if (out.account) onDone(out.account); }
    } catch (e) { setErr(errText(e)); setReview(null); } finally { setBusy(false); }
  }
  const edit = (k: keyof typeof v) => (e: React.ChangeEvent<HTMLInputElement>) => { setV({ ...v, [k]: e.target.value }); setReview(null); };
  return (
    <Modal label="Adjust stop-loss and take-profit" onClose={onClose}>
      <h3>RISK CONTROLS · {p.coin} {p.side}</h3>
      {done ? (
        <div className="tt-result" role="status"><p><b>Updated ✓</b> Hyperliquid accepted the new orders; the old ones were cancelled after that.</p>
          <button className="tb-btn" onClick={onClose}>Done</button></div>
      ) : (
        <>
          <p className="tb-sub">Now {px(p.mark)} · entry {px(p.entry_px)}. The stop can only move closer to the price (less risk, never more).
            Size and leverage never change here; to reduce the size, use Close 25% / 50%.</p>
          <label className="td-field"><span>Stop-loss</span><input type="number" inputMode="decimal" value={v.stop} onChange={edit("stop")} /><small>{ORDER_LABEL[p.sl_status]}</small></label>
          <label className="td-field"><span>TP1 (half)</span><input type="number" inputMode="decimal" value={v.tp1} disabled={p.tp1_done} onChange={edit("tp1")} />
            <small>{p.tp1_done ? "taken" : ORDER_LABEL[p.tp1_status]}</small></label>
          <label className="td-field"><span>TP2</span><input type="number" inputMode="decimal" value={v.tp2} onChange={edit("tp2")} /><small>{ORDER_LABEL[p.tp2_status]}</small></label>
          {review && (
            <div className="tt-result" role="status">
              {review.changes.map((c) => <p key={c}>{c}</p>)}
              <p>Loss if the stop is hit: <b>{usd(review.loss_at_stop_usd)}</b> (incl. fees)</p>
            </div>
          )}
          {err && <p className="site-error" role="alert">{err}</p>}
          <div className="tb-row">
            {review ? <button className="tb-cta" disabled={busy} onClick={() => void send(false)}>{busy ? "Sending…" : "Confirm changes"}</button>
              : <button className="tb-btn" disabled={busy} onClick={() => void send(true)}>{busy ? "Checking…" : "Review changes"}</button>}
            <button className="tb-btn tb-btn-ghost" onClick={onClose}>Cancel</button>
          </div>
        </>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------- Kiwi chat
function KiwiAvatar({ big = false }: { big?: boolean }) {
  return <img className={`kt-av${big ? " is-big" : ""}`} src={site.kiwi.src} width={site.kiwi.width} height={site.kiwi.height} alt="" aria-hidden decoding="async" />;
}

type Msg = { key: string; at: string; sig?: Sig; text?: string; tone?: "up" | "down" | "info"; member?: string };
/** The public Price Chat Telegram group (the same feed as the site's chat bubble): first names only, bots and commands left out. */
interface GroupChat { enabled: boolean; link?: string; title?: string; messages?: { id: number; name: string; text: string; at: string }[] }
const AV_COLORS = ["#9b5cff", "#2aabee", "#ff8a3d", "#ff5ea8", "#3ddc97", "#ffc93c"];
const avColor = (name: string) => AV_COLORS[[...name].reduce((a, ch) => a + ch.charCodeAt(0), 0) % AV_COLORS.length];

/** Kiwi's messages, oldest first: each published signal, then only what really happened (the signal's own recorded trigger and
 *  close, and the member's own positions opening, taking TP1 and closing). Nothing is generated to look busy. */
function feed(sigs: Sig[], t: Terminal | null, chat: GroupChat | null = null): Msg[] {
  const out: Msg[] = [];
  const tag = (m: Mode) => (m === "LIVE" ? "" : m === "TESTNET" ? " (test funds)" : " (practice)");
  for (const s of sigs) {
    if (!s.published_at) continue;
    out.push({ key: `s${s.id}`, at: s.published_at, sig: s });
    if (s.activated_at) out.push({ key: `a${s.id}`, at: s.activated_at, text: `${s.coin} ${s.side} triggered: price reached the entry zone.`, tone: "info" });
    if (s.closed_at && CLOSED_SIG.has(s.status)) {
      const res = s.model_pnl_pct != null && (s.status === "TARGET_HIT" || s.status === "STOP_HIT" || s.status === "CLOSED")
        ? ` Signal result ${pct(s.model_pnl_pct)} (no leverage, after estimated costs).` : "";
      const text = s.status === "TARGET_HIT" ? `🎯 ${s.coin} reached its target.${res}` : s.status === "STOP_HIT" ? `${s.coin} hit its stop-loss.${res}`
        : s.status === "EXPIRED" ? `#${num(s.number)} ${s.coin} expired.` : s.status === "CANCELLED" ? `#${num(s.number)} ${s.coin} was cancelled.`
        : `#${num(s.number)} ${s.coin} closed.${res}`;
      out.push({ key: `c${s.id}`, at: s.closed_at, text, tone: s.status === "TARGET_HIT" ? "up" : s.status === "STOP_HIT" ? "down" : undefined });
    }
  }
  for (const p of t?.positions ?? []) {
    if (p.opened_at) out.push({ key: `po${p.id}`, at: p.opened_at, text: `Your ${p.coin} ${p.side} position is open${p.entry_px ? ` at ${px(p.entry_px)}` : ""}${tag(p.mode)}.`, tone: "info" });
    if (p.tp1_at) out.push({ key: `pt${p.id}`, at: p.tp1_at, text: `TP1 reached on your ${p.coin} position: half taken${tag(p.mode)}.`, tone: "up" });
  }
  for (const h of t?.history ?? []) {
    if (h.closed_at) out.push({ key: `pc${h.id}`, at: h.closed_at, tone: h.net_pnl > 0 ? "up" : h.net_pnl < 0 ? "down" : undefined,
      text: `Your ${h.coin} position closed${h.reason ? ` (${REASON[h.reason] ?? h.reason})` : ""}: ${usd(h.net_pnl, true)}${tag(h.mode)}.` });
  }
  for (const m of (chat?.messages ?? []).slice(-25)) out.push({ key: `g${m.id}`, at: m.at, text: m.text, member: m.name || "Member" });
  return out.sort((a, b) => a.at.localeCompare(b.at)).slice(-50);
}

function SignalMsg({ x, my, closedPnl, canEnter, busy, onEnter, onSkip }: {
  x: Sig; my?: Terminal["signals"][string]; closedPnl: number | null; canEnter: boolean; busy: boolean; onEnter: () => void; onSkip: () => void;
}) {
  const [chart, setChart] = useState(false);
  const status = my?.status ?? "NOT_ENTERED";
  const open = x.status === "ACTIVE" || x.status === "WAITING";
  const test = x.strategy === "test";
  let action: ReactNode;
  if ((status === "POSITION_OPEN" || status === "ORDER_PENDING") && my?.position_id) {
    action = <a className="kt-btn" href={`#kt-pos-${my.position_id}`}>VIEW POSITION</a>;
  } else if (status === "POSITION_CLOSED") {
    action = <span className={`kt-state${tone(closedPnl)}`}>CLOSED{closedPnl != null ? ` · ${usd(closedPnl, true)}` : ""}</span>;
  } else if (!open) {
    action = <span className="kt-state">{x.status === "EXPIRED" ? "EXPIRED" : x.status_label}</span>;
  } else if (status === "REJECTED") {
    action = <span className="kt-state">SKIPPED</span>;
  } else {
    action = (
      <>
        <button className="kt-btn is-go" disabled={busy || !canEnter} onClick={onEnter}>ENTER TRADE</button>
        <button className="kt-btn" disabled={busy} onClick={onSkip}>SKIP</button>
      </>
    );
  }
  return (
    <div className={`kt-card is-${x.side.toLowerCase()}`} aria-label={`Signal ${num(x.number)} ${x.coin} ${x.side}`}>
      <p className="kt-card-title"><span aria-hidden>{x.side === "LONG" ? "🟢" : "🔴"}</span> <b>{x.coin} {x.side}</b>
        {test && <span className="kt-tag">Manual setup</span>}
        <span className={`kt-kiwis${(x.participation.enter ?? 0) > 0 ? " is-in" : ""}`} title="Kiwis with a position in this signal (live)">
          👥 {x.participation.enter ?? 0} {(x.participation.enter ?? 0) === 1 ? "Kiwi" : "Kiwis"} in</span>
        <span className="kt-num">#{num(x.number)}</span></p>
      <dl className="kt-levels">
        <div><dt>Entry</dt><dd>${px(x.entry[0])}–${px(x.entry[1])}</dd></div>
        <div><dt>Stop Loss</dt><dd className="is-down">${px(x.stop)}</dd></div>
        <div><dt>Take Profit 1</dt><dd className="is-up">${px(x.target)}</dd></div>
        {x.tp2 != null && <div><dt>Take Profit 2</dt><dd className="is-up">≈ ${px(x.tp2)}</dd></div>}
        <div><dt>Leverage</dt><dd>{lev(x.leverage)}</dd></div>
        <div><dt>Risk/Reward</dt><dd>1:{x.rr.toFixed(1)}</dd></div>
      </dl>
      <button className="kt-chart-btn" aria-expanded={chart} onClick={() => setChart(!chart)}>📊 {x.timeframe ?? ""} chart</button>
      {chart && <img className="kt-chart" src={`/api/members/trading-os/signals/${x.id}/chart.png`} alt={`${x.coin} chart with the signal's entry, stop and target`}
                     loading="lazy" width={1200} height={675} />}
      {x.reason && <p className="kt-says"><b>Kiwi says:</b> “{x.reason}”</p>}
      <div className="kt-actions">{action}</div>
    </div>
  );
}

// ---------------------------------------------------------------- positions
function PositionRow({ p, onClose, onAdjust }: { p: Pos; onClose: () => void; onAdjust: () => void }) {
  const [open, setOpen] = useState(false);
  const live = p.status === "OPEN";
  return (
    <div id={`kt-pos-${p.id}`} className={`kt-row${open ? " is-open" : ""}${p.warnings.length ? " has-warn" : ""}`} role="row" aria-label={`${p.coin} ${p.side} position`}>
      <button className="kt-row-main" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="kt-c kt-mkt" data-l="Market"><b>{p.coin}</b>{p.mode !== "LIVE" && <small className="kt-tag">{p.mode === "TESTNET" ? "TEST" : "PRACTICE"}</small>}</span>
        <span className={`kt-c kt-side is-${p.side.toLowerCase()}`} data-l="Side">{p.side} {p.leverage}x</span>
        <span className="kt-c" data-l="Size">{usd(p.size_usd)}</span>
        <span className="kt-c" data-l="Entry">{px(p.entry_px)}</span>
        <span className="kt-c" data-l="Mark"><Live value={p.mark}>{px(p.mark)}</Live></span>
        <span className="kt-c kt-pnl" data-l="P&L"><Live value={p.upnl}>{usd(p.upnl, true)}</Live><small className={tone(p.upnl_pct)}
          title="Return on the margin this position uses (ROE)">{pct(p.upnl_pct)}{p.upnl_pct != null ? " ROE" : ""}</small></span>
        <span className="kt-c" data-l="Liq. Price">{px(p.liq_px)}</span>
      </button>
      <span className="kt-c kt-close"><button className="kt-close-btn" aria-label={`Close ${p.coin} ${p.side} position`} onClick={onClose}>CLOSE</button></span>
      {p.warnings.map((w) => <p key={w} className="kt-warn" role="alert">⚠ {w}</p>)}
      {open && (
        <div className="kt-more">
          <dl>
            <div><dt>Margin</dt><dd>{usd(p.margin)}</dd></div>
            <div><dt>Contracts</dt><dd>{qty(p.size)} {p.coin}</dd></div>
            <div><dt>Stop loss</dt><dd>{px(p.stop)} <small>{ORDER_LABEL[p.sl_status]}</small></dd></div>
            <div><dt>Take profit</dt><dd>{p.tp1_done ? "TP1 ✓" : px(p.tp1)} · {px(p.tp2)} <small>{ORDER_LABEL[p.tp2_status]}</small></dd></div>
            <div><dt>Fees</dt><dd>{usd(p.fees)}</dd></div>
            <div><dt>Funding</dt><dd>{p.funding == null ? "—" : usd(p.funding)}</dd></div>
          </dl>
          {live && (
            <div className="kt-more-actions">
              <button className="kt-btn" onClick={onClose}>Reduce position</button>
              <button className="kt-btn" onClick={onAdjust}>Edit stop / take profit</button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- setup + settings (dialogs, never on the page)
type Run = <T>(key: string, fn: () => Promise<T>, ok?: string) => Promise<T | null>;

type RefState = "VERIFIED" | "NOT_VERIFIED" | "EXISTING_ACCOUNT" | "PENDING";
type AuthState = "ACTIVE" | "REVOKED" | "NONE" | "UNKNOWN" | "WALLET_CHANGED";
export interface Conn {
  eligible: boolean; blocker: string | null; rank: { ok: boolean; required_level: number; level: number };
  wallet: { address: string } | null;
  referral: { status: RefState; code: string; code_seen: string | null; url: string; checked_at: string | null };
  deposit: { status: string; usd: number; min_usd: number };
  authorization: { status: AuthState; agent: string | null; approved_at: string | null; valid_until: number | null; revoke_url: string };
  live: { enabled: boolean; mode: Mode }; auto: boolean; connected: boolean; permissions: string[];
  balances?: { perps: number; spot_usdc: number; unified?: boolean; trading?: number } | null;
}

/** Your account (where the money is) vs Kiwi's trading key (signs orders for it, holds nothing), and where the money sits. */
function AccountLines({ c }: { c: Conn }) {
  const b = c.balances;
  return (
    <>
      <div><dt>Your Hyperliquid account</dt><dd>{short(c.wallet?.address)}</dd></div>
      <div><dt>Balance</dt><dd>{!b ? "Can't read right now" : b.unified ? `$${(b.trading ?? b.spot_usdc).toFixed(2)} USDC (unified account)`
        : `Perps $${b.perps.toFixed(2)} · Spot $${b.spot_usdc.toFixed(2)}`}</dd></div>
      {b && !b.unified && b.perps < 10 && b.spot_usdc > 0 && <p className="kt-explain">Kiwi trades from your <b>Perps</b> balance. On Hyperliquid, press <b>Transfer</b> and move USDC
        from Spot to Perps.</p>}
      {c.authorization.agent && <div><dt>Kiwi's trading key</dt><dd>{short(c.authorization.agent)}</dd></div>}
      {c.authorization.agent && <p className="kt-explain">The trading key is a separate address Hyperliquid created to sign orders for your account. It never holds
        money, so it shows $0 on Hyperliquid: your funds stay in your account.</p>}
    </>
  );
}
const REF_LABEL: Record<RefState, string> = { VERIFIED: "VERIFIED", NOT_VERIFIED: "NOT VERIFIED", EXISTING_ACCOUNT: "EXISTING ACCOUNT", PENDING: "PENDING" };
const short = (a: string | null | undefined) => (a ? `${a.slice(0, 6)}…${a.slice(-4)}` : "");

/** CONNECT HYPERLIQUID with an API key: (1) join Hyperliquid through the Price Chat referral, (2) create an API wallet on
 *  Hyperliquid's own API page, (3) paste the account address and that key here. No wallet connection on our site: Hyperliquid
 *  listing the key for that address proves the account is the member's. Every status comes from the server; "connected"
 *  only once the key, the referral, the deposit and the Passport rules are all verified. */
export function ConnectDialog({ onClose, onChanged }: { onClose: () => void; onChanged: () => void }) {
  const [c, setC] = useState<Conn | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const [secret, setSecret] = useState("");
  const [edit, setEdit] = useState(false);
  const [replace, setReplace] = useState<boolean | null>(null);  // null: not asked; the server asks before switching accounts
  const load = useCallback(async () => {
    try { setC(await memberApi<Conn>("/members/trading/connection")); } catch (e) { setErr(errText(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  async function step(key: string, fn: () => Promise<Conn & { note?: string }>) {
    setBusy(key); setErr(null);
    try {
      const out = await fn();
      setC(out);
      if (out.note) setErr(out.note);
      onChanged();
    } catch (e) {
      const msg = e instanceof MemberApiError ? e.message : errText(e);
      setErr(msg);
      if (msg.includes("Switch my Passport")) setReplace(false);
    } finally { setBusy(null); }
  }
  const disconnect = () => step("disconnect", async () => {
    await memberApi("/members/trading/agent/remove", { method: "POST", body: JSON.stringify({ network: "LIVE" }) });
    return memberApi<Conn>("/members/trading/connection");
  });
  const connect = () => step("connect", async () => {
    const out = await memberApi<Conn & { note?: string }>("/members/trading/connection/api-key", { method: "POST",
      body: JSON.stringify({ secret: secret.trim(), consent: false, replace: !!replace }) });  // connects in paper; live is a separate step
    setSecret("");  // the key never stays in the page
    setReplace(null);
    setEdit(false);
    return out;
  });
  const recheck = () => step("recheck", () => memberApi<Conn>("/members/trading/connection/recheck", { method: "POST" }));
  const activate = () => step("enable", () => memberApi<Conn>("/members/trading/connection/activate", { method: "POST", body: JSON.stringify({ consent: true }) }));

  const ref = c?.referral.status ?? "PENDING";
  const auth = c?.authorization.status ?? "NONE";
  const keyOk = auth === "ACTIVE";
  const showForm = !!c && (!keyOk || edit);
  const validKey = /^(0x)?[0-9a-fA-F]{64}$/.test(secret.trim());
  return (
    <Modal label="Connect Hyperliquid" onClose={onClose}>
      {!c ? <p className="tt-dl-line">Loading…</p> : c.connected && !edit ? (
        <div className="kt-done">
          <h3>🥝 KIWI IS CONNECTED</h3>
          <dl className="kt-confirm">
            <div><dt>Hyperliquid</dt><dd className="is-up">Connected</dd></div>
            <div><dt>Referral</dt><dd className="is-up">Verified</dd></div>
            <div><dt>Trading authorization</dt><dd className="is-up">Active</dd></div>
            <AccountLines c={c} />
            <div><dt>Bot</dt><dd className="is-up">Online</dd></div>
            <div><dt>Auto trading</dt><dd>{c.auto ? "ON" : "OFF"}</dd></div>
          </dl>
          <p className="tb-sub">{c.auto ? "AUTO is on: Kiwi enters each new signal within your limits, every trade with its stop-loss. Turn AUTO off in ⚙ settings to trade only when you press ENTER TRADE."
              : "Kiwi trades only when you press ENTER TRADE and confirm. Turn AUTO on in ⚙ settings to follow every signal."}
            {c.authorization.valid_until ? ` Your API key expires ${new Date(c.authorization.valid_until).toLocaleDateString()}: create a new one on Hyperliquid before then.` : ""}</p>
          <button className="kt-btn is-go is-wide" onClick={onClose}>OPEN TRADING TERMINAL</button>
          <button className="kt-btn is-wide" onClick={() => setEdit(true)}>REPLACE API KEY</button>
        </div>
      ) : (
        <>
          <h3>CONNECT HYPERLIQUID</h3>
          {showForm && (
            <ol className="kt-steps">
              <li>
                <b>Join Hyperliquid with our referral</b>
                <span>New to Hyperliquid? Sign up through our link. Already have an account? Use it as it is: we check the referral in a moment.</span>
                <a className="kt-btn" href={trackedRef(c.referral.url, "earn")} target="_blank" rel="noopener noreferrer">Open Hyperliquid ↗</a>
              </li>
              <li>
                <b>Create your API key</b>
                <span>On Hyperliquid, open <b>More → API</b>. Type the name <b>PriceChat</b>, press <b>Generate</b>, then <b>Authorize API Wallet</b> (choose the
                  longest validity). Copy the <b>private key</b> it shows: Hyperliquid shows it only once.</span>
                <a className="kt-btn" href="https://app.hyperliquid.xyz/API" target="_blank" rel="noopener noreferrer">Open the API page ↗</a>
              </li>
              <li>
                <b>Paste the key here</b>
                <span>Just the private key: Kiwi finds your Hyperliquid account from it.</span>
                <label className="kt-field"><span>API wallet private key</span>
                  <input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="0x… (64 characters)" autoComplete="off" spellCheck={false} /></label>
                <span className="kt-note">Only the API wallet key from that page: never your main wallet's private key or seed phrase. It's sent over HTTPS,
                  stored encrypted on our server and never shown again.</span>
                <ul className="kt-perms">{c.permissions.map((p) => <li key={p}>{p}</li>)}</ul>
                <span className="kt-note">Connecting keeps you in <b>paper trading</b>. Live trading is a separate step you confirm yourself.</span>
                {replace !== null && <label className="tt-check"><input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
                  Switch my Kiwi Passport to this Hyperliquid account</label>}
                <button className="kt-btn is-go" disabled={busy !== null || !validKey || replace === false} onClick={() => void connect()}>
                  {busy === "connect" ? "Checking with Hyperliquid…" : "CONNECT"}</button>
              </li>
            </ol>
          )}
          {c.wallet && !edit && (
            <dl className="kt-status">
              {keyOk && <p className="kt-connected">● Connected{c.live.enabled && c.live.mode === "LIVE" ? " · live trading" : " · paper trading"}</p>}
              <AccountLines c={c} />
              {keyOk && <div><dt>Expires</dt><dd>{c.authorization.valid_until ? new Date(c.authorization.valid_until).toLocaleDateString() : "No expiry set"}</dd></div>}
              <div><dt>API key</dt><dd className={keyOk ? "is-up" : "is-down"}>{keyOk ? "Active"
                : auth === "REVOKED" ? "Not active on Hyperliquid (revoked or expired): paste a new one" : auth === "UNKNOWN" ? "Can't check right now" : "Not added yet"}</dd></div>
              <div><dt>Referral</dt><dd><span className={`kt-ref is-${ref.toLowerCase()}`}>{REF_LABEL[ref]}</span></dd></div>
              {ref === "NOT_VERIFIED" && <p className="kt-explain">Hyperliquid doesn't show the Price Chat referral on this account. Sign up or enter the code through our
                link with this same account, then retry. Opening the link alone doesn't count: only Hyperliquid's record does.</p>}
              {ref === "EXISTING_ACCOUNT" && <p className="kt-explain">This Hyperliquid account is already linked to another referral ({c.referral.code_seen}).
                Hyperliquid doesn't allow changing an existing referral, so nobody (including us) can switch it. You can connect a different account.</p>}
              {ref !== "VERIFIED" && <p className="kt-row-btns">
                {ref !== "EXISTING_ACCOUNT" && <a className="kt-btn" href={trackedRef(c.referral.url, "earn")} target="_blank" rel="noopener noreferrer">Open Hyperliquid ↗</a>}
                <button className="kt-btn" disabled={busy !== null} onClick={() => void recheck()}>{busy === "recheck" ? "Checking…" : "RETRY VERIFICATION"}</button></p>}
              {!c.eligible && ref === "VERIFIED" && c.deposit.status !== "verified" && <p className="kt-explain">Deposit: ${c.deposit.usd.toFixed(2)} of the ${c.deposit.min_usd.toFixed(0)} minimum
                on Hyperliquid. <button className="kt-inline" disabled={busy !== null} onClick={() => void recheck()}>Check again</button></p>}
              {!c.eligible && !c.rank.ok && <p className="kt-explain">Kiwi Rank {c.rank.required_level} is needed (you're Rank {c.rank.level}). <a href="/passport">Level up</a></p>}
              {keyOk && c.eligible && !c.connected && (
                <p className="kt-row-btns">
                  <label className="tt-check"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} /> I understand live trading
                    places real orders on my Hyperliquid account and can lose money. AUTO turns on: Kiwi enters every signal within my limits.</label>
                  <button className="kt-btn is-go" disabled={busy !== null || !consent} onClick={() => void activate()}>{busy === "enable" ? "Checking…" : "ENABLE LIVE TRADING"}</button>
                </p>
              )}
              {keyOk && (
                <p className="kt-row-btns">
                  <button className="kt-btn" disabled={busy !== null} onClick={() => window.confirm("Disconnect this API key from Price Chat? We delete our copy at once. "
                    + "To revoke it on Hyperliquid too, remove it on app.hyperliquid.xyz/API.") && void disconnect()}>DISCONNECT</button>
                  <a className="kt-btn" href="https://app.hyperliquid.xyz/API" target="_blank" rel="noopener noreferrer">Revoke on Hyperliquid ↗</a>
                </p>
              )}
            </dl>
          )}
        </>
      )}
      {err && <p className="site-error" role="alert">{err}</p>}
    </Modal>
  );
}

/** DEPOSIT: the member's own Hyperliquid account address (never the trading key's), as text + QR, USDC only, with the two ways
 *  money really arrives on Hyperliquid. No funds move through Price Chat. */
function DepositDialog({ c, onClose }: { c: Conn; onClose: () => void }) {
  const address = c.wallet?.address ?? "";
  const [qr, setQr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    let live = true;
    import("qrcode").then((Q) => Q.default.toString(address, { type: "svg", margin: 1, color: { dark: "#05120c", light: "#ffffff" } }))
      .then((svg) => { if (live) setQr(`data:image/svg+xml;utf8,${encodeURIComponent(svg)}`); }).catch(() => undefined);
    return () => { live = false; };
  }, [address]);
  const b = c.balances;
  return (
    <Modal label="Deposit USDC" onClose={onClose}>
      <h3>DEPOSIT USDC</h3>
      <p className="tt-dl-line">Your Hyperliquid account: this is where your money goes, and what Kiwi trades for.</p>
      <div className="kt-deposit">
        {qr && <img className="kt-qr" src={qr} width={168} height={168} alt={`QR code of your Hyperliquid address ${address}`} />}
        <code className="kt-addr">{address}</code>
        <button className="kt-btn" onClick={() => { void navigator.clipboard?.writeText(address).then(() => setCopied(true)).catch(() => undefined); }}>
          {copied ? "Copied ✓" : "Copy address"}</button>
      </div>
      {b && <p className="kt-explain">Now: {b.unified ? `$${(b.trading ?? b.spot_usdc).toFixed(2)} USDC (unified account: all of it can trade)`
        : `Perps $${b.perps.toFixed(2)} · Spot $${b.spot_usdc.toFixed(2)}`}</p>}
      <ul className="kt-perms">
        <li><b>USDC only.</b></li>
        <li><b>From another Hyperliquid account</b>, or an exchange that offers the <b>Hyperliquid</b> network for USDC: send it to this address.</li>
        <li><b>From your own wallet (USDC on Arbitrum):</b> use <b>Deposit</b> on Hyperliquid. Sending Arbitrum USDC straight to this address leaves it in your
          Arbitrum wallet, not on Hyperliquid.</li>
        {!b?.unified && <li><b>Kiwi trades from Perps.</b> If it lands in Spot, press <b>Transfer</b> on Hyperliquid and move it to Perps.</li>}
        <li>Never send money to Kiwi's trading key address: it isn't your account.</li>
      </ul>
      <a className="kt-btn is-go is-wide" href="https://app.hyperliquid.xyz/trade" target="_blank" rel="noopener noreferrer">Open Hyperliquid (Deposit / Transfer) ↗</a>
    </Modal>
  );
}

function SettingsDialog({ a, conn, busy, err, run, put, onClose, onAskAuto }: {
  a: Account; conn: Connection; busy: string | null; err: string | null; run: Run; put: (b: Record<string, unknown>, ok?: string) => Promise<unknown>; onClose: () => void;
  onAskAuto: (hours: number) => void;  // AUTO is only ever turned on through its confirmation (limits + real-money warning)
}) {
  const [draft, setDraft] = useState<Limits>(a.limits);
  const [hours, setHours] = useState(24);
  const safety = a.paused && !!a.paused_reason?.startsWith("Protection failed");
  return (
    <Modal label="Trading settings" onClose={onClose}>
      <h3>SETTINGS</h3>
      <h4>Auto trading</h4>
      {a.auto.enabled ? (
        <div className="td-row"><span>AUTO is on{a.auto.until ? ` until ${new Date(a.auto.until).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}` : " until you turn it off"}.</span>
          <button className="kt-btn" disabled={busy !== null} onClick={() => void put({ auto_enabled: false }, "AUTO paused: no new automatic entries")}>Pause auto</button></div>
      ) : a.mode === "PAPER" ? (
        <p className="tb-sub">Connect your trading account first.</p>
      ) : (
        <div className="td-row">
          <select aria-label="AUTO duration" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            {AUTO_DURATIONS.map((h) => <option key={h} value={h}>{autoLabel(h)}</option>)}
          </select>
          <button className="kt-btn is-go" disabled={busy !== null} onClick={() => onAskAuto(hours)}>Turn AUTO on…</button>
        </div>
      )}
      <p className="tb-sub">AUTO enters each new signal within your limits below. Every trade still gets an exchange-side stop-loss.</p>
      {a.style && <StyleSection s={a.style} mode={a.mode} busy={busy} put={put} />}
      <h4>New trades</h4>
      <div className="td-row"><span>{a.paused ? `Paused${a.paused_reason ? ` (${a.paused_reason})` : ""}` : "Allowed"}</span>
        <button className="kt-btn" disabled={busy !== null} onClick={() => {
          if (safety && !window.confirm(`New trades were stopped by a safety check:\n${a.paused_reason}\n\nHave you reviewed it?`)) return;
          void put(a.paused ? { paused: false, ack_protection: safety || undefined } : { paused: true }, a.paused ? "New trades allowed" : "New trades paused");
        }}>{a.paused ? "Resume" : "Pause new trades"}</button></div>
      <p className="tb-sub">Pausing never closes positions or removes their stop-loss and take-profit orders.</p>
      <h4>Risk limits</h4>
      {LIMIT_LABEL.map(([k, label, unit]) => (
        <label key={k} className="td-field"><span>{label}</span>
          <input type="number" inputMode="decimal" value={draft[k] as number} min={a.caps[k]?.[0]} max={a.caps[k]?.[1]}
                 onChange={(e) => setDraft({ ...draft, [k]: Number(e.target.value) })} /><small>{unit}</small></label>
      ))}
      <button className="kt-btn" disabled={busy !== null} onClick={() => {
        if (!okToSaveLimits(a.mode, a.limits, draft)) return;
        void put({ ...draft }, "Limits saved ✓");
      }}>Save limits</button>
      <h4>Connection</h4>
      {(["LIVE", "TESTNET"] as Network[]).filter((n) => a.agents[n].approved).map((n) => (
        <div key={n} className="td-row"><span>{n === "LIVE" ? "Trading authorization" : "Test wallet"} ✓ {a.agents[n].address?.slice(0, 8)}…</span>
          <button className="kt-btn" disabled={busy !== null} onClick={() => window.confirm(n === "LIVE"
            ? "Revoke Kiwi's trading access?\n\nPrice Chat deletes its trading key at once, so it can't place orders any more. Hyperliquid will still list the API wallet until you remove it there too (app.hyperliquid.xyz/API)."
            : "Remove the test wallet here?")
            && void run("agent-rm", () => memberApi<Account>("/members/trading/agent/remove", { method: "POST", body: JSON.stringify({ network: n }) }),
              n === "LIVE" ? "Trading access revoked here. Also remove it on Hyperliquid → API." : "Test wallet removed")}>{n === "LIVE" ? "REVOKE TRADING ACCESS" : "Remove"}</button></div>
      ))}
      {a.agents.LIVE.approved && <p className="tb-sub">To revoke it on Hyperliquid itself: <a href="https://app.hyperliquid.xyz/API" target="_blank" rel="noopener noreferrer">app.hyperliquid.xyz/API ↗</a> → remove "PriceChat".
        Revoking here deletes Price Chat's key; removing it there makes Hyperliquid reject it too.</p>}
      {a.live.enabled && <div className="td-row"><span>Live trading on</span>
        <button className="kt-btn" disabled={busy !== null} onClick={() => void run("live", () => memberApi<Account>("/members/trading/live/disable", { method: "POST" }), "Live trading off")}>Turn off</button></div>}
      <a className="kt-link" href={`https://t.me/${conn.botUsername}`} target="_blank" rel="noopener noreferrer">Open @{conn.botUsername} in Telegram ↗</a>
      {err && <p className="site-error" role="alert">{err}</p>}
    </Modal>
  );
}

interface Plan { entry: number; size: number; notional: number; leverage: number; margin: number; stop: number; tp1: number; tp2: number; max_loss_usd: number }

// ---------------------------------------------------------------- the page
export function TradingTerminal({ conn, after }: { conn: Connection; after?: ReactNode }) {
  const [t, setT] = useState<Terminal | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [loadedAt, setLoadedAt] = useState<number | null>(null);  // when the numbers on screen were last read
  const [sigs, setSigs] = useState<Sig[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [closing, setClosing] = useState<Pos | null>(null);
  const [adjusting, setAdjusting] = useState<Pos | null>(null);
  const [closeAll, setCloseAll] = useState(false);
  const [connect, setConnect] = useState(false);
  const [settings, setSettings] = useState(false);
  const [deposit, setDeposit] = useState(false);
  const [pinOpen, setPinOpen] = useState(true);
  const [autoAsk, setAutoAsk] = useState(false);
  const [autoHours, setAutoHours] = useState(24);
  useEffect(() => { if (typeof location !== "undefined" && location.hash === "#settings") setSettings(true); }, []);
  const [pv, setPv] = useState<{ token: string; mode: Mode; plan: Plan; sig: Sig } | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const lastKey = useRef<string | null>(null);

  const load = useCallback(async () => {
    try {
      setT(await memberApi<Terminal>("/members/trading/terminal"));
      setLoadedAt(Date.now());
      setLoadErr(null);
    } catch (e) { setLoadErr(errText(e)); }
  }, []);
  useEffect(() => {
    void load();
    const id = window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, TERMINAL_POLL_MS);
    return () => window.clearInterval(id);
  }, [load]);
  useEffect(() => {
    let stop = false;
    const tick = () => memberApi<{ items: Sig[] }>("/members/trading-os/signals").then((d) => { if (!stop) setSigs(d.items); }).catch(() => undefined);
    void tick();
    const id = window.setInterval(() => { if (document.visibilityState === "visible") void tick(); }, 10000);
    return () => { stop = true; window.clearInterval(id); };
  }, []);

  async function run<T>(key: string, fn: () => Promise<T>, ok?: string): Promise<T | null> {
    setBusy(key); setErr(null); setNote(null);
    try {
      const out = await fn();
      if (ok) { setNote(ok); window.setTimeout(() => setNote(null), 3500); }
      void load();
      return out;
    } catch (e) {
      setErr(e instanceof MemberApiError ? e.message : key.startsWith("agent-") ? walletError(e) : errText(e));
      return null;
    } finally { setBusy(null); }
  }
  const put = (body: Record<string, unknown>, ok?: string) =>
    run("settings", () => memberApi<Account>("/members/trading/settings", { method: "PUT", body: JSON.stringify(body) }), ok);
  const refreshed = () => void load();

  const a = t?.account;
  const ov = t?.overview;
  const mode: Mode = ov?.mode ?? a?.mode ?? "PAPER";
  const connected = mode !== "PAPER";  // a real (or clearly labelled test) Hyperliquid account; paper is never shown as one
  const positions = t?.positions ?? [];
  const closable = positions.filter((p) => (p.status === "OPEN" || p.status === "PROTECTION_FAILED") && p.size > 0);
  const paused = ov?.paused ?? a?.paused ?? false;
  const autoOn = ov?.auto ?? a?.auto.enabled ?? false;
  const restricted = [!conn.rankOk && `Kiwi Rank ${conn.minRank} is needed for new trades`, !conn.wallet && "your wallet isn't verified",
    !conn.bot && `@${conn.botUsername} isn't linked`].filter(Boolean) as string[];
  const [chat, setChat] = useState<GroupChat | null>(null);
  useEffect(() => {
    let stop = false;
    const tick = () => memberApi<GroupChat>("/website/chat").then((d) => { if (!stop) setChat(d.enabled ? d : null); }).catch(() => undefined);
    void tick();
    const id = window.setInterval(() => { if (document.visibilityState === "visible") void tick(); }, 15000);
    return () => { stop = true; window.clearInterval(id); };
  }, []);
  const msgs = useMemo(() => feed(sigs, t, chat), [sigs, t, chat]);
  // the newest published signal stays pinned above the chat (like a pinned message in Telegram)
  const pinned = useMemo(() => [...sigs].filter((x) => x.published_at).sort((a, b) => (b.published_at ?? "").localeCompare(a.published_at ?? ""))[0] ?? null, [sigs]);
  const hist = t?.history ?? [];

  // a new message: scroll the chat to it (inside the chat only). No chime: Earn only sounds when a trade opens or closes
  useEffect(() => {
    const last = msgs[msgs.length - 1]?.key ?? null;
    const el = bodyRef.current;
    if (el && last !== lastKey.current) {
      el.scrollTop = el.scrollHeight;
      lastKey.current = last;
    }
  }, [msgs]);

  async function enter(s: Sig) {
    const p = await run("preview", () => memberApi<{ token: string; mode: Mode; plan: Plan }>("/members/trading/preview",
      { method: "POST", body: JSON.stringify({ signal_id: s.id }) }));
    if (p) setPv({ ...p, sig: s });
  }
  function cancelPreview() {
    if (pv) void memberApi("/members/trading/cancel", { method: "POST", body: JSON.stringify({ token: pv.token }) }).catch(() => undefined);
    setPv(null);
  }
  async function confirm() {
    if (!pv) return;
    const out = await run("confirm", () => memberApi<{ position: { id: number; coin: string; side: string; mode: Mode; status: string; error: string | null } }>(
      "/members/trading/confirm", { method: "POST", body: JSON.stringify({ token: pv.token }) }));
    setPv(null);
    if (out) {
      if (out.position.status === "OPEN") { setNote(`Filled: ${out.position.coin} ${out.position.side} ✓ stop-loss and take profit placed`); playSound("cash"); }
      else setErr(`${out.position.status.replace(/_/g, " ")}${out.position.error ? `: ${out.position.error}` : ""}`);
    }
  }
  // LIVE: is Kiwi's permission still listed on Hyperliquid? (revoked / expired / wallet changed → new entries are blocked)
  const [cx, setCx] = useState<Conn | null>(null);
  const loadConn = useCallback(() => memberApi<Conn>("/members/trading/connection").then(setCx).catch(() => undefined), []);
  useEffect(() => {
    void loadConn();
    const id = window.setInterval(() => { if (document.visibilityState === "visible") void loadConn(); }, 60000);
    return () => window.clearInterval(id);
  }, [loadConn, mode]);
  const auth = mode === "LIVE" ? cx?.authorization.status ?? null : null;
  const authBad = auth === "REVOKED" || auth === "WALLET_CHANGED" || auth === "NONE";
  // a key is connected but trading isn't live yet: say exactly what's missing instead of "connect" again
  const halfway = mode !== "LIVE" && !!cx && cx.authorization.status === "ACTIVE";
  const missing = !cx ? "" : cx.blocker ? `Not live yet: ${cx.blocker}` : "Paper trading until you enable live trading";

  return (
    <div className="kt" aria-label="Kiwi trading bot">
      {/* 1 · Kiwi's chat */}
      <section className="kt-chat" aria-label="Kiwi trading bot chat">
        <header className="kt-head">
          <KiwiAvatar big />
          <div className="kt-who">
            <h1>KIWI TRADING BOT</h1>
            <span className={`kt-online${t?.global_paused ? " is-off" : ""}`}><i aria-hidden />{t?.global_paused ? "Paused" : "Online"}</span>
          </div>
          <div className="kt-head-right">
            {mode !== "LIVE" ? <button className={`kt-mode is-${mode.toLowerCase()}`} onClick={() => setConnect(true)}>{NOT_LIVE[mode]}</button>
              : <span className="kt-mode is-live" title="Trades on this account use real money on Hyperliquid">LIVE · REAL MONEY</span>}
            {paused && <button className="kt-paused" onClick={() => setSettings(true)}>NEW TRADES PAUSED</button>}
            <button className={`kt-api${mode === "LIVE" && !authBad ? " is-on" : ""}`} onClick={() => setConnect(true)}>
              🔑 {mode === "LIVE" && !authBad ? "API connected" : "Add Hyperliquid API"}</button>
            <button role="switch" aria-checked={autoOn} className={`kt-autobtn${autoOn ? " is-on" : ""}`} disabled={busy !== null}
                    onClick={() => autoOn ? void put({ auto_enabled: false }, "AUTO off: no new automatic entries (open trades keep their stops)") : setAutoAsk(true)}>
              <i aria-hidden /> AUTO {autoOn ? "ON" : "OFF"}</button>
            <button className="kt-gear" aria-label="Trading settings" onClick={() => setSettings(true)}>⚙</button>
          </div>
        </header>
        {pinned && (
          <div className={`kt-pin is-${pinned.side.toLowerCase()}`} aria-label="Pinned signal">
            <div className="kt-pin-head">
              <span className="kt-pin-tag">📌 PINNED SIGNAL</span>
              <span className="kt-pin-state">{pinned.status_label}</span>
              <button className="kt-inline" aria-expanded={pinOpen} onClick={() => setPinOpen(!pinOpen)}>{pinOpen ? "Hide" : "Show"}</button>
            </div>
            {pinOpen ? (
              <div className="kt-pin-card">
                <SignalMsg x={pinned} my={t?.signals[String(pinned.id)]} busy={busy !== null} canEnter={!paused && restricted.length === 0 && !authBad}
                           closedPnl={hist.find((h) => h.signal_id === pinned.id)?.net_pnl ?? null} onEnter={() => void enter(pinned)}
                           onSkip={() => void run("reject", () => memberApi(`/members/trading/signals/${pinned.id}/reject`, { method: "POST" }), `Skipped #${num(pinned.number)}`)} />
              </div>
            ) : (
              <p className="kt-pin-line"><b>#{num(pinned.number)} {pinned.coin} {pinned.side}</b> · Entry ${px(pinned.entry[0])}–${px(pinned.entry[1])} · SL ${px(pinned.stop)} · TP ${px(pinned.target)}</p>
            )}
          </div>
        )}
        <div className="kt-body" ref={bodyRef} aria-live="polite">
          {msgs.length === 0 && <p className="kt-empty">{t || sigs.length ? "No signals yet. Kiwi posts here the moment it finds a setup." : "Loading…"}</p>}
          {msgs.map((m) => m.member ? (
            <div key={m.key} className="kt-msg is-member">
              <span className="kt-av kt-av-member" style={{ background: avColor(m.member) }} aria-hidden>{[...m.member][0]?.toUpperCase()}</span>
              <div className="kt-bubble">
                <p className="kt-meta" style={{ color: avColor(m.member) }}>{m.member} · {clock(m.at)}</p>
                <p className="kt-text">{m.text}</p>
              </div>
            </div>
          ) : (
            <div key={m.key} id={m.sig ? `kt-sig-${m.sig.id}` : undefined} className={`kt-msg${m.sig ? " is-signal" : ""}${m.tone ? ` is-${m.tone}` : ""}`}>
              <KiwiAvatar />
              <div className="kt-bubble">
                <p className="kt-meta">KIWI BOT · {clock(m.at)}</p>
                {m.sig ? (
                  <SignalMsg x={m.sig} my={t?.signals[String(m.sig.id)]} busy={busy !== null} canEnter={!paused && restricted.length === 0 && !authBad}
                             closedPnl={hist.find((h) => h.signal_id === m.sig!.id)?.net_pnl ?? null}
                             onEnter={() => void enter(m.sig!)}
                             onSkip={() => void run("reject", () => memberApi(`/members/trading/signals/${m.sig!.id}/reject`, { method: "POST" }), `Skipped #${num(m.sig!.number)}`)} />
                ) : <p className="kt-text">{m.text}</p>}
              </div>
            </div>
          ))}
          <div className="kt-typing" aria-hidden><i /><i /><i /></div>
        </div>
        {chat?.link && <a className="kt-join" href={chat.link} target="_blank" rel="noopener noreferrer">💬 Chat with the community in Telegram ↗</a>}
        {authBad && (
          <p className="kt-restricted" role="alert">Kiwi's trading permission isn't active on Hyperliquid{auth === "WALLET_CHANGED" ? " for your current wallet" : ""}:
            new trades are blocked. Open positions keep their stop-loss. <button className="kt-inline" onClick={() => setConnect(true)}>Enable bot trading again</button></p>
        )}
        {restricted.length > 0 && (
          <p className="kt-restricted" role="alert">New trades need: {restricted.join(" · ")}.{" "}
            {!conn.rankOk ? <a href="/passport">Level up my Kiwi</a> : !conn.wallet ? <a href="/passport#account-wallet">Reconnect wallet</a>
              : <a href={`https://t.me/${conn.botUsername}`} target="_blank" rel="noopener noreferrer">Open the bot ↗</a>}</p>
        )}
      </section>

      {loadErr && <p className="site-error" role="alert">{loadErr}{loadedAt
        ? ` Balances and positions below are from ${new Date(loadedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })} and may be out of date.` : ""}</p>}
      {err && <p className="site-error" role="alert">{err}</p>}
      {note && <p className="tb-toast" role="status">{note}</p>}

      {/* 2 · balance + positions */}
      <section className="kt-section" id="kt-positions" aria-label="My positions">
        <div className="kt-sec-head">
          <h2>MY POSITIONS</h2>
          {closable.length > 0 && <button className="kt-closeall" onClick={() => setCloseAll(true)}>CLOSE ALL</button>}
        </div>
        {connected ? (
          <dl className="kt-balance" aria-label="My balance">
            <div><dt>Account equity</dt><dd>{usd(ov?.equity)}</dd></div>
            <div><dt>Available</dt><dd>{usd(ov?.available)}</dd></div>
            <div><dt>Unrealized P&L</dt><dd><Live value={ov?.unrealized}>{usd(ov?.unrealized, true)}</Live></dd></div>
            {mode === "LIVE" && cx?.wallet && (
              <div className="kt-deposit-cta">
                {(ov?.equity ?? 0) < 10 && <small>{!cx.balances?.unified && (cx.balances?.spot_usdc ?? 0) > 0 ? "Move USDC from Spot to Perps to start trading" : "Add USDC to start trading"}</small>}
                <button className="kt-btn is-small is-go" onClick={() => setDeposit(true)}>DEPOSIT</button>
              </div>
            )}
            {mode === "TESTNET" && <p className="kt-balance-note">Test funds · not real money</p>}
          </dl>
        ) : (
          <>
            {mode === "PAPER" && ov?.equity != null && (
              <dl className="kt-balance is-paper" aria-label="Practice balance">
                <div><dt>Practice balance (simulated)</dt><dd>{usd(ov.equity)}</dd></div>
                <div><dt>Unrealized P&L</dt><dd><Live value={ov.unrealized}>{usd(ov.unrealized, true)}</Live></dd></div>
                <p className="kt-balance-note">Paper trading on real prices · not real money</p>
              </dl>
            )}
            <div className="kt-balance is-empty">{halfway
              ? <><span>API key connected ✓ · {missing}</span>
                  <button className="kt-btn is-go" onClick={() => setConnect(true)}>{cx?.eligible ? "ENABLE LIVE TRADING" : "FINISH CONNECTING"}</button></>
              : <><span>Trade with real money: connect your Hyperliquid account.</span>
                  <button className="kt-btn is-go" onClick={() => setConnect(true)}>CONNECT HYPERLIQUID</button></>}</div>
          </>
        )}
        {t && <SyncedAgo sync={t.sync} loadedAt={loadedAt} />}
        {positions.length > 0 ? (
          <div className="kt-table" role="table" aria-label="Open positions">
            <div className="kt-row kt-thead" role="row">
              <span className="kt-row-main">{["Market", "Side", "Size", "Entry", "Mark", "P&L", "Liq. Price"].map((h) => <span key={h} className="kt-c">{h}</span>)}</span>
              <span className="kt-c kt-close">Close</span>
            </div>
            {positions.map((p) => <PositionRow key={p.id} p={p} onClose={() => setClosing(p)} onAdjust={() => setAdjusting(p)} />)}
          </div>
        ) : t && <p className="kt-none">No open positions.</p>}
        {(t?.other_positions.length ?? 0) > 0 && (
          <details className="kt-others">
            <summary>{t!.other_positions.length} other position{t!.other_positions.length === 1 ? "" : "s"} on this account · not managed by Kiwi</summary>
            <ul>{t!.other_positions.map((o) => <li key={o.coin}><b>{o.coin} {o.side}</b> {usd(o.value)} · entry {px(o.entry)} · <span className={tone(o.upnl)}>{usd(o.upnl, true)}</span></li>)}</ul>
          </details>
        )}
      </section>

      {/* 3 · history (collapsed) */}
      {hist.length > 0 && (
        <details className="kt-section kt-hist">
          <summary><h2>HISTORY</h2><span>{hist.length} closed</span></summary>
          <table className="kt-htable">
            <thead><tr><th>Market</th><th>Side</th><th>Entry</th><th>Exit</th><th title="After trading fees">Net P&L</th><th>Closed</th></tr></thead>
            <tbody>
              {hist.map((h) => (
                <tr key={h.id}>
                  <td><b>{h.coin}</b>{h.mode !== "LIVE" && <small className="kt-tag">{h.mode === "TESTNET" ? "TEST" : "PRACTICE"}</small>}</td>
                  <td className={`kt-side is-${h.side.toLowerCase()}`}>{h.side} {h.leverage}x</td>
                  <td>{px(h.entry_px)}</td><td>{px(h.exit_px)}</td>
                  <td className={tone(h.net_pnl)}><b>{usd(h.net_pnl, true)}</b></td><td>{day(h.closed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      {after}

      {/* dialogs */}
      {pv && (
        <Modal label="Confirm trade" onClose={cancelPreview}>
          <h3 className={`kt-confirm-title is-${pv.sig.side.toLowerCase()}`}>{pv.sig.coin} {pv.sig.side}</h3>
          <p className={`kt-mode is-${pv.mode.toLowerCase()}`}>{pv.mode === "LIVE" ? "LIVE · REAL MONEY" : NOT_LIVE[pv.mode]}</p>
          <dl className="kt-confirm">
            <div><dt>Entry (market)</dt><dd>≈ ${px(pv.plan.entry)}</dd></div>
            <div><dt>Position size</dt><dd>{usd(pv.plan.notional)} <small>({qty(pv.plan.size)} {pv.sig.coin})</small></dd></div>
            <div><dt>Leverage</dt><dd>{pv.plan.leverage}x</dd></div>
            <div><dt>Margin used</dt><dd>{usd(pv.plan.margin)}</dd></div>
            <div><dt>Stop loss</dt><dd>${px(pv.plan.stop)}</dd></div>
            <div><dt>Take profit</dt><dd>${px(pv.plan.tp1)} · ${px(pv.plan.tp2)}</dd></div>
          </dl>
          <p className="kt-maxloss">Estimated maximum loss: <b>{usd(pv.plan.max_loss_usd)}</b>, excluding possible slippage and additional costs.</p>
          <button className="kt-btn is-go is-wide" disabled={busy !== null} onClick={() => void confirm()}>
            {busy === "confirm" ? "Sending…" : pv.mode === "LIVE" ? "CONFIRM TRADE · REAL MONEY" : "CONFIRM TRADE"}</button>
          <button className="kt-link" onClick={cancelPreview}>Cancel</button>
        </Modal>
      )}
      {closing && <CloseDialog p={closing} onDone={refreshed} onClose={() => { setClosing(null); void load(); }} />}
      {adjusting && <AdjustDialog p={adjusting} onDone={refreshed} onClose={() => { setAdjusting(null); void load(); }} />}
      {closeAll && <CloseAllDialog positions={closable} onDone={refreshed} onClose={() => { setCloseAll(false); void load(); }} />}
      {connect && <ConnectDialog onChanged={() => { refreshed(); void loadConn(); }} onClose={() => { setConnect(false); void load(); void loadConn(); }} />}
      {autoAsk && a && (
        <Modal label="Turn AUTO on" onClose={() => setAutoAsk(false)}>
          <h3>AUTO: FOLLOW KIWI</h3>
          <p className="tt-dl-line">Kiwi enters every new signal for you, at market, within your limits: max {a.limits.max_leverage}x, {usd(a.limits.max_loss_per_trade_usd)} max
            loss per trade, {a.limits.max_positions} positions at once, {usd(a.limits.daily_loss_limit_usd)} daily loss limit.</p>
          {mode === "LIVE" ? <p className="kt-warn">LIVE: these are real-money trades on your Hyperliquid account, and they can lose money.</p>
            : <p className="kt-explain">{NOT_LIVE[mode]}: practice trades, no real money.</p>}
          <label className="td-field"><span>For</span>
            <select aria-label="AUTO duration" value={autoHours} onChange={(e) => setAutoHours(Number(e.target.value))}>
              {AUTO_DURATIONS.map((h) => <option key={h} value={h}>{autoLabel(h)}</option>)}
            </select><small /></label>
          <p className="kt-explain">Every trade still gets its stop-loss on Hyperliquid. Tap AUTO again (or /pause in Telegram) to stop new entries at any time.</p>
          <button className="kt-btn is-go is-wide" disabled={busy !== null}
                  onClick={() => { setAutoAsk(false); void put({ auto_enabled: true, auto_hours: autoHours }, autoHours ? `AUTO on for ${autoLabel(autoHours)} ✓` : "AUTO on until you turn it off ✓"); }}>
            TURN AUTO ON</button>
        </Modal>
      )}
      {deposit && cx?.wallet && <DepositDialog c={cx} onClose={() => { setDeposit(false); void load(); void loadConn(); }} />}
      {settings && a && <SettingsDialog a={a} conn={conn} busy={busy} err={err} run={run} put={put} onClose={() => setSettings(false)}
                                        onAskAuto={(h) => { setAutoHours(h); setSettings(false); setAutoAsk(true); }} />}
    </div>
  );
}
