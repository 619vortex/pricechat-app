import { useCallback, useEffect, useRef, useState } from "react";
import { MemberApiError, memberApi } from "../members";

/* Kiwi Brain 2.0 data layer. Every type mirrors a backend record; nothing here is generated client-side except arithmetic
   on those records (indicators from real candles, sums of real trades). */

export type Ev = { id: number; at: string; kind: string; coin: string | null; signal_id: number | null; title: string;
  data: Record<string, unknown>; source: string | null; source_at: string | null; mine: boolean };
export type Check = { node: string; rule: string; ok: boolean | null; value: string; strategy?: string };
export type Job = { enabled: boolean; every_s: number; last_status: string; last_run_at: string | null; last_finished_at: string | null;
  next_run_at: string | null; last_error: string | null; duration_ms: number | null; age_s: number | null } | null;
export type Position = { id: number; signal_id: number; mode: string; coin: string; side: string; status: string; size: number; size_usd: number | null;
  entry_px: number | null; mark: number | null; leverage: number | null; margin: number | null; liq_px: number | null; upnl: number | null;
  upnl_pct: number | null; stop: number; tp1: number; tp2: number; tp1_done: boolean; fees: number; funding: number | null; opened_at: string | null;
  warnings: string[]; test: boolean; strategy: string | null; strategy_name: string | null; signal_number: number | null; signal_status: string | null;
  age_s: number | null; protection: { stop: string; tp1: string; tp2: string; verified: boolean; label: string }; managed: string;
  loss_at_stop: number; protected: boolean };
export type Overview = {
  mode: string; auto: boolean; auto_until: string | null; paused: boolean; global_paused: boolean;
  health: {
    exchange: { state: string; label: string; at?: string | null; age_s?: number | null; error?: string | null };
    market_feed: { state: string; at?: string; age_s?: number; error?: string; source: string; engine_snapshot_at: string | null };
    engine: { state: string; reason: string; strategies: { id: string; name: string; on: boolean }[]; job: Job; next_at: string | null;
      last_evaluation_at: string | null };
    monitoring: { state: string; label: string; job: Job };
    protection: { state: string; open: number; verified: number };
  };
  positions: Position[];
  exposure: { notional: number; loss_at_stop: number; equity: number | null; notional_pct: number | null; loss_at_stop_pct: number | null;
    unrealized: number | null; realized_today: number | null };
  limits: { max_leverage: number; max_loss_per_trade_usd: number; max_positions: number; daily_loss_limit_usd: number; allowed_coins: string;
    max_allocation_pct: number };
  config: KiwiConfig;
};
export type Node = { status: string; output?: string; checks?: Check[]; ms?: number; source?: string; at?: string | null; note?: string;
  plan?: Record<string, number> | null };
export type Evaluation = { coin: string; at: string; engine: string; nodes: Record<string, Node>;
  runs: { strategy: string; name: string; enabled: boolean; decision: string; reasons: string[]; checks: Check[]; indicators: Record<string, unknown>;
    entry: number[] | null; stop: number | null; target: number | null; rr: number | null; ms: number }[];
  book: { bid: number; ask: number; spread_bps: number; bid_depth_usd: number; ask_depth_usd: number; imbalance: number | null } };
export type Snap = { coin: string; timeframe: string; stale: boolean;
  market: { source: string; at: string | null; mark: number; funding_h: number; open_interest_usd: number; volume_24h_usd: number; prev_day: number; max_leverage: number };
  candles: { source: string; at: string | null; age_s: number | null };
  indicators: Record<string, number | string | null> };
export type Candles = { coin: string; timeframe: string; source: string; candles: [number, number, number, number, number, number][];
  markers: { signal_id: number; number: number; side: string; at: string; entry: number; stop: number; target: number; status: string; test: boolean }[] };
export type Flow = { coin: string; recording_since: number; now: number; samples: number; sample_every_s: number; source: string;
  book: { bids: [number, number][]; asks: [number, number][]; mid: number; t: number } | null; imbalance: number | null;
  depth_top20: { bid_usd: number; ask_usd: number }; heat: { t: number; mid: number; bids: [number, number][]; asks: [number, number][] }[];
  volume: { t: number; buy: number; sell: number; cvd: number }[]; large_trades: { t: number; px: number; sz: number; usd: number; side: string }[];
  trades_recorded: number; profile: { px: number; usd: number }[]; ctx: { t: number; mark: number; funding_h: number; oi_usd: number }[];
  ctx_history: { t: number; oi_usd: number; funding_h: number; mark: number }[]; liquidations: { available: boolean; why: string } };
export type Trade = { id: number; coin: string; side: string; mode: string; strategy: string | null; signal_number: number | null; test: boolean;
  entry: number | null; exit: number | null; net: number; fees: number; funding: number | null; notional: number; reason: string | null;
  opened_at: string | null; closed_at: string | null; duration_s: number | null; regime: string | null };
export type Backtest = { label: string; rows: { strategy: string; name: string; trades: number; expectancy_r: number; ci90: number[]; win_rate: number;
  holdout_r: number; max_dd_pct: number; period: string }[] };
export type Perf = { trades: Trade[]; label: string; backtest: Backtest };
export type KiwiConfig = { strategies: Record<string, boolean>; sides: string; coins: string[]; timeframes: string[]; min_rr: number | null;
  funding: string; max_volatility_pct: number | null; schedule: { days: number[]; from_h: number; to_h: number } | null };
export type ConfigState = { version: number; config: KiwiConfig; note: string | null; at: string | null;
  versions: { version: number; config: KiwiConfig; note: string | null; at: string | null }[]; defaults: KiwiConfig };
export type Impact = { signals: number; allowed_before: number; allowed_after: number; removed: string[]; added: string[] };
export type Replay = { signal: { id: number; number: number; coin: string; side: string; entry: number[]; stop: number; target: number; created_at: string;
    activated_at: string | null; closed_at: string | null; status: string; timeframe: string; test: boolean };
  interval: string; candles: [number, number, number, number, number, number][]; source: string; events: Ev[];
  report: Record<string, number | string | null>; gap: string | null };

export const fmt = {
  n: (x: unknown, d = 2) => (typeof x === "number" && Number.isFinite(x) ? x.toLocaleString("en-US", { maximumFractionDigits: d }) : "—"),
  px: (x: unknown) => (typeof x === "number" && Number.isFinite(x) ? (Math.abs(x) >= 1000 ? fmt.n(x, 1) : Math.abs(x) >= 1 ? fmt.n(x, 4) : fmt.n(x, 6)) : "—"),
  usd: (x: number | null | undefined, d = 2) => (x == null || !Number.isFinite(x) ? "—"
    : `${x < 0 ? "−" : ""}$${Math.abs(x).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d })}`),
  /** a profit or loss: always signed ("+$4.20" / "−$3.10"), so a gain never reads like a balance */
  pnl: (x: number | null | undefined, d = 2) => (x == null || !Number.isFinite(x) ? "—" : `${x > 0 ? "+" : ""}${fmt.usd(x, d)}`),
  big: (x: number | null | undefined) => (x == null ? "—" : Math.abs(x) >= 1e9 ? `$${(x / 1e9).toFixed(2)}B` : Math.abs(x) >= 1e6 ? `$${(x / 1e6).toFixed(1)}M`
    : Math.abs(x) >= 1e3 ? `$${(x / 1e3).toFixed(1)}k` : `$${x.toFixed(0)}`),
  pct: (x: number | null | undefined, d = 2) => (x == null || !Number.isFinite(x) ? "—" : `${x >= 0 ? "+" : ""}${x.toFixed(d)}%`),
  time: (iso: string | number) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }),
  day: (iso: string | number) => new Date(iso).toLocaleDateString([], { month: "short", day: "numeric" }),
  ago(iso: string | number | null | undefined): string {
    if (iso == null) return "—";
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    return s < 60 ? `${Math.round(s)}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : s < 86400 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`;
  },
  until(iso: string | null | undefined): string {
    if (!iso) return "—";
    const s = (new Date(iso).getTime() - Date.now()) / 1000;
    return s <= 0 ? "due now" : s < 60 ? `in ${Math.round(s)}s` : `in ${Math.round(s / 60)}m`;
  },
  dur(s: number | null | undefined): string {
    if (s == null) return "—";
    return s < 3600 ? `${Math.round(s / 60)}m` : s < 86400 ? `${(s / 3600).toFixed(1)}h` : `${(s / 86400).toFixed(1)}d`;
  },
};

/** Poll a member endpoint; pauses while the tab is hidden (no load on the backend nobody is looking at). */
export function usePoll<T>(path: string | null, every: number): { data: T | null; err: string | null; at: number | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [at, setAt] = useState<number | null>(null);
  const [tick, setTick] = useState(0);
  const pathRef = useRef(path);
  pathRef.current = path;
  useEffect(() => {
    if (!path) return;
    let live = true;
    const load = () => {
      if (typeof document !== "undefined" && document.hidden) return;
      memberApi<T>(path).then((d) => { if (live && pathRef.current === path) { setData(d); setErr(null); setAt(Date.now()); } })
        .catch((e: unknown) => { if (live) setErr(e instanceof MemberApiError && e.status === 401 ? "signin" : e instanceof Error ? e.message : "Unavailable"); });
    };
    load();
    const id = window.setInterval(load, every);
    const vis = () => { if (!document.hidden) load(); };
    document.addEventListener("visibilitychange", vis);
    return () => { live = false; window.clearInterval(id); document.removeEventListener("visibilitychange", vis); };
  }, [path, every, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, err, at, reload };
}

export async function post<T>(path: string, body: unknown, method = "POST"): Promise<T> {
  return memberApi<T>(path, { method, body: JSON.stringify(body) });
}

/* indicators, computed from the candles passed in (only those: the Time Machine passes only what existed at each step) */
export function emaSeries(v: number[], p: number): (number | null)[] {
  const out: (number | null)[] = v.map(() => null);
  if (v.length < p) return out;
  const k = 2 / (p + 1);
  let x = v.slice(0, p).reduce((a, b) => a + b, 0) / p;
  out[p - 1] = x;
  for (let i = p; i < v.length; i++) { x = v[i] * k + x * (1 - k); out[i] = x; }
  return out;
}
export function rsiLast(c: number[], p = 14): number | null {
  if (c.length <= p) return null;
  let g = 0, l = 0;
  for (let i = 1; i <= p; i++) { const d = c[i] - c[i - 1]; g += Math.max(d, 0); l += Math.max(-d, 0); }
  g /= p; l /= p;
  for (let i = p + 1; i < c.length; i++) { const d = c[i] - c[i - 1]; g = (g * (p - 1) + Math.max(d, 0)) / p; l = (l * (p - 1) + Math.max(-d, 0)) / p; }
  return l === 0 ? 100 : 100 - 100 / (1 + g / l);
}
export function bollinger(c: number[], p = 20, k = 2): { mid: (number | null)[]; up: (number | null)[]; lo: (number | null)[] } {
  const mid: (number | null)[] = [], up: (number | null)[] = [], lo: (number | null)[] = [];
  for (let i = 0; i < c.length; i++) {
    if (i < p - 1) { mid.push(null); up.push(null); lo.push(null); continue; }
    const w = c.slice(i - p + 1, i + 1), m = w.reduce((a, b) => a + b, 0) / p;
    const sd = Math.sqrt(w.reduce((a, b) => a + (b - m) ** 2, 0) / p);
    mid.push(m); up.push(m + k * sd); lo.push(m - k * sd);
  }
  return { mid, up, lo };
}
export function vwapSeries(cs: [number, number, number, number, number, number][], p = 24): (number | null)[] {
  return cs.map((_, i) => {
    if (i < p - 1) return null;
    let pv = 0, vv = 0;
    for (let j = i - p + 1; j <= i; j++) { const tp = (cs[j][2] + cs[j][3] + cs[j][4]) / 3; pv += tp * cs[j][5]; vv += cs[j][5]; }
    return vv ? pv / vv : null;
  });
}
export function macdHist(c: number[]): number | null {
  const f = emaSeries(c, 12), s = emaSeries(c, 26);
  const line = c.map((_, i) => (f[i] != null && s[i] != null ? (f[i] as number) - (s[i] as number) : null)).filter((x): x is number => x != null);
  if (line.length < 9) return null;
  const sig = emaSeries(line, 9);
  const last = sig[sig.length - 1];
  return last == null ? null : line[line.length - 1] - last;
}

export const NODE_LABEL: Record<string, string> = { market: "Market data", trend: "Trend analysis", momentum: "Momentum", orderflow: "Order flow",
  signal: "Signal generation", risk: "Risk engine", execution: "Trade execution", position: "Position monitoring" };
export const NODES = ["market", "trend", "momentum", "orderflow", "signal", "risk", "execution", "position"] as const;
export const STRAT: Record<string, string> = { trend_pullback_v1: "Trend pullback 1h", breakout_4h_v1: "4h breakout", test: "Manual setup" };
