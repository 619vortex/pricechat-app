import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("lightweight-charts", () => {
  const series = { setData: vi.fn(), createPriceLine: vi.fn(() => ({})), removePriceLine: vi.fn(), priceScale: () => ({ applyOptions: vi.fn() }) };
  const chart = { addSeries: vi.fn(() => series), subscribeClick: vi.fn(), remove: vi.fn(),
    timeScale: () => ({ fitContent: vi.fn(), setVisibleLogicalRange: vi.fn() }) };
  return { createChart: vi.fn(() => chart), createSeriesMarkers: vi.fn(() => ({ setMarkers: vi.fn() })), CandlestickSeries: {}, HistogramSeries: {},
    LineSeries: {}, ColorType: { Solid: "solid" }, CrosshairMode: { Normal: 0 }, LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 } };
});

import { KiwiBrainPage } from "./kiwibrain";
import { fmt } from "./brain/api";
import { group } from "./brain/livebrain";
import { moodOf } from "./brain/mood";
import { simulate } from "./brain/simulator";
import { BRAIN_PATH, KiwiButt } from "./kiwibutt";
import type { Ev } from "./brain/api";

const now = new Date().toISOString();
const POS = { id: 4, signal_id: 7, mode: "LIVE", coin: "BTC", side: "SHORT", status: "OPEN", size: 0.00013, size_usd: 10.7, entry_px: 82164, mark: 82000,
  leverage: 3, margin: 3.5, liq_px: 109000, upnl: 0.02, upnl_pct: 0.6, stop: 82835, tp1: 80800, tp2: 80100, tp1_done: false, fees: 0.005, funding: -0.0001,
  opened_at: now, warnings: [], test: true, strategy: "test", strategy_name: "Manual setup", signal_number: 7, signal_status: "ACTIVE", age_s: 3600,
  protection: { stop: "ACTIVE", tp1: "ACTIVE", tp2: "ACTIVE", verified: true, label: "Stop-loss resting on Hyperliquid (verified in the open orders)" },
  managed: "The exchange-side stop and take-profits close it.", loss_at_stop: 0.09, protected: true };
const OV = {
  mode: "LIVE", auto: false, auto_until: null, paused: false, global_paused: false,
  health: { exchange: { state: "connected", label: "Hyperliquid live", age_s: 2 }, market_feed: { state: "live", age_s: 1, source: "hyperliquid:info allMids", engine_snapshot_at: now },
    engine: { state: "shadow", reason: "Shadow mode: both strategies are switched off.", strategies: [{ id: "trend_pullback_v1", name: "Trend pullback (1h)", on: false }],
      job: null, next_at: now, last_evaluation_at: now },
    monitoring: { state: "ok", label: "Positions reconciled every minute", job: null }, protection: { state: "ok", open: 1, verified: 1 } },
  positions: [POS], other_positions: [],
  exposure: { notional: 10.7, loss_at_stop: 0.09, equity: 10, notional_pct: 107, loss_at_stop_pct: 0.9, unrealized: 0.02, realized_today: 0 },
  limits: { max_leverage: 3, max_loss_per_trade_usd: 5, max_positions: 2, daily_loss_limit_usd: 15, allowed_coins: "", max_allocation_pct: 10 },
  config: { strategies: { trend_pullback_v1: true, breakout_4h_v1: true, test: true }, sides: "both", coins: [], timeframes: ["1h", "4h"], min_rr: null, funding: "any",
    max_volatility_pct: null, schedule: null },
};
const node = (status: string, output = "") => ({ status, output, checks: [] });
const EVAL = { coin: "BTC", at: now, engine: "shadow", book: { bid: 1, ask: 1, spread_bps: 0.1, bid_depth_usd: 1e6, ask_depth_usd: 1e6, imbalance: 0 },
  nodes: { market: node("pass", "BTC 82,000"), trend: { status: "fail", checks: [{ node: "trend", rule: "EMA20/EMA50 aligned", ok: false, value: "EMA20 1 · EMA50 2", strategy: "trend_pullback_v1" }] },
    momentum: node("waiting"), orderflow: node("pass"), signal: node("fail", "NO TRADE from both strategies"), risk: node("waiting", "No proposal to check."),
    execution: node("waiting"), position: node("open", "SHORT") },
  runs: [{ strategy: "trend_pullback_v1", name: "Trend pullback (1h)", enabled: false, decision: "NO TRADE", reasons: ["no clear trend"], indicators: {}, ms: 3,
    checks: [{ node: "trend", rule: "EMA20/EMA50 aligned", ok: false, value: "EMA20 1 · EMA50 2" }], entry: null, stop: null, target: null, rr: null }] };
const ev = (id: number, kind: string, title: string, data: Record<string, unknown> = {}, coin = "ETH", minsAgo = 1): Ev =>
  ({ id, at: new Date(Date.now() - minsAgo * 60_000).toISOString(), kind, coin, signal_id: null, title, data, source: "x", source_at: null, mine: false });
let calls: string[] = [];

beforeEach(() => {
  localStorage.clear();
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${url}`);
    const body = url.includes("/overview") ? OV : url.includes("/evaluate") ? EVAL : url.includes("/events")
      ? { items: [ev(3, "strategy", "ETH: no trade · a", { decision: "NO TRADE", reasons: ["a"] }), ev(2, "strategy", "ETH: no trade · b", { decision: "NO TRADE", reasons: ["a"] }),
        ev(1, "risk", "BTC #007: risk check rejected · limit", { decision: "rejected" }, "BTC")] }
      : url.includes("/notifications") ? { items: [], last_id: 5 } : url.includes("/trading/settings") ? { ok: true } : {};
    return new Response(JSON.stringify(body), { status: 200 });
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

describe("Kiwi Brain 2.0", () => {
  it("the Kiwi's butt reveals the brain, then opens the bookmarkable page", () => {
    vi.useFakeTimers();
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    render(<KiwiButt />);
    fireEvent.click(screen.getByRole("button", { name: /kiwi/i }));
    expect(screen.getByRole("status").textContent).toContain("YOU FOUND THE KIWI'S BRAIN");
    act(() => { vi.advanceTimersByTime(2000); });
    expect(assign).toHaveBeenCalledWith(BRAIN_PATH);
  });

  it("has no label: once found, tapping it goes straight to the brain", () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    localStorage.setItem("pc-kiwi-brain-found", "1");
    const { container } = render(<KiwiButt />);
    expect(container.textContent).toBe("");  // animation only
    fireEvent.click(screen.getByRole("button", { name: "Kiwi Brain" }));
    expect(assign).toHaveBeenCalledWith(BRAIN_PATH);
    expect(screen.queryByRole("status")).toBeNull();
    localStorage.removeItem("pc-kiwi-brain-found");
  });

  it("separates exchange, market feed, engine, monitoring and protection; shows the position and its verified stop", async () => {
    render(<KiwiBrainPage />);
    await waitFor(() => expect(screen.getAllByText("SHADOW").length).toBeGreaterThan(0));
    expect(screen.getByText("Hyperliquid")).toBeTruthy();
    expect(screen.getByText(/Live · 1s/)).toBeTruthy();
    expect(screen.getByText("LIVE · APPROVAL")).toBeTruthy();
    expect(screen.getByText(/1 open · 🛡 stops on/)).toBeTruthy();
    await waitFor(() => expect(screen.getByText(/Stop-loss resting on Hyperliquid/)).toBeTruthy());
    expect(screen.getAllByText(/Shadow mode/).length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.getByText("Trend analysis")).toBeTruthy());  // the pipeline, from the live evaluation
    fireEvent.click(screen.getByText("Trend analysis"));
    expect(screen.getAllByText(/EMA20\/EMA50 aligned/).length).toBeGreaterThan(0);
  });

  it("emergency pause asks first, explains protection stays, then pauses new trades only", async () => {
    render(<KiwiBrainPage />);
    await waitFor(() => screen.getByText(/EMERGENCY PAUSE/));
    fireEvent.click(screen.getByText(/EMERGENCY PAUSE/));
    expect(screen.getByRole("dialog").textContent).toContain("never removes protection");
    fireEvent.click(screen.getByText("PAUSE NEW TRADES"));
    await waitFor(() => expect(calls.some((c) => c === "PUT /api/members/trading/settings")).toBe(true));
  });

  it("groups repetitive no-trade evaluations but keeps every event", () => {
    const rows = group([ev(3, "strategy", "x", { decision: "NO TRADE", reasons: ["a"] }), ev(2, "risk", "r"), ev(1, "strategy", "y", { decision: "NO TRADE", reasons: ["a"] })]);
    const g = rows.find((r) => r.kind === "group");
    expect(g && g.kind === "group" && g.items.length).toBe(2);
    expect(rows.length).toBe(2);
  });

  it("the Kiwi only celebrates or wears glasses on real position events; a rejection gets the head shake", () => {
    expect(moodOf([]).key).toBe("idle");
    expect(moodOf([ev(1, "risk", "rejected", { decision: "rejected" })]).line).toContain("EXIT LIQUIDITY");
    expect(moodOf([ev(1, "strategy", "s", { decision: "LONG", shadow: true }, "SOL")]).line).toContain("shadow: not traded");
    expect(moodOf([ev(1, "position", "BTC SHORT opened: 1 @ 2")]).key).toBe("glasses");
    expect(moodOf([ev(1, "position", "BTC closed", { net_pnl: -1, reason: "stop" })]).key).toBe("sad");
    expect(moodOf([ev(1, "position", "BTC opened", {}, "BTC", 120)]).key).toBe("idle");  // old news: no reaction
  });

  it("the simulator's numbers: loss at stop, profit at target, R/R and the estimated liquidation", () => {
    const r = simulate({ side: "LONG", entry: 100, stop: 98, tp: 104, size: 1000, lev: 5, slip_bps: 0, fee_bps: 0 }, { max_leverage: 20, funding_h: 0.0001 }, 2000);
    expect(r.lossAtStop).toBeCloseTo(20);
    expect(r.profitAtTp).toBeCloseTo(40);
    expect(r.rr).toBeCloseTo(2);
    expect(r.margin).toBe(200);
    expect(r.liq).toBeCloseTo(100 * (1 - (0.2 - 0.025)));
    expect(r.exposurePct).toBe(50);
    expect(r.fundingDay).toBeCloseTo(2.4);  // a long pays positive funding
    const bad = simulate({ side: "LONG", entry: 100, stop: 101, tp: 104, size: 1000, lev: 5, slip_bps: 0, fee_bps: 0 }, { max_leverage: 20, funding_h: 0 }, null);
    expect(bad.valid).toBe(false);
  });

  it("asks a signed-out visitor to sign in instead of showing anything", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Sign in" }), { status: 401 })));
    render(<KiwiBrainPage />);
    await waitFor(() => expect(screen.getByText(/sign in first/)).toBeTruthy());
  });
});

describe("Kiwi Brain money formatting", () => {
  it("profits and losses are always signed; amounts use a real minus sign", () => {
    expect(fmt.pnl(4.2)).toBe("+$4.20"); expect(fmt.pnl(-3.1)).toBe("−$3.10"); expect(fmt.pnl(0)).toBe("$0.00"); expect(fmt.pnl(null)).toBe("—");
    expect(fmt.usd(-3.1)).toBe("−$3.10");
  });
});
