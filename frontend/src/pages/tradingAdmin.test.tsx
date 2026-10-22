import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AffiliateAdminPage } from "./AffiliateAdmin";
import { ExchangeAutoCard } from "./ExchangeAutoCard";
import { TradingOSPage } from "./TradingOS";

/** TEST FIXTURES only: fake owner APIs answering by method + path. Nothing reaches Telegram or an exchange. */
const calls: { key: string; body: unknown }[] = [];
function serve(routes: Record<string, unknown>) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${input.split("?")[0]}`;
    calls.push({ key, body: init?.body ? JSON.parse(String(init.body)) : null });
    let out = routes[key];
    if (typeof out === "function") out = (out as () => unknown)();
    if (out instanceof Response) return out;
    return out === undefined ? new Response(JSON.stringify({ detail: "nope" }), { status: 404 }) : new Response(JSON.stringify(out));
  }));
}
const flush = async () => { for (let i = 0; i < 5; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const settings = { scanner_enabled: true, auto_publish: false, paused: false, publish_chat: "@sig", main_chat: "", chart_routing: "anywhere", brief_enabled: false,
  brief_hour: 8, timeframe: "1h", universe_size: 20, min_volume_usd: 1, max_spread_bps: 5, max_open_signals: 3, min_rr: 1.5, strategies: {}, allowed_coins: "" };
const overview = { settings, exchange: { ok: true }, counts: { PENDING_REVIEW: 1 },
  stats: { label: "", total: 3, open: 1, closed: 2, win_rate: 50, avg_gain: 2, avg_loss: -1, net_return: 1.2, max_drawdown: 3 }, execution: "" };
const pending = { id: 4, number: 12, coin: "SOL", side: "LONG", status: "PENDING_REVIEW", status_label: "Waiting", entry: [144, 146], stop: 140, target: 155, rr: 2,
  leverage: [2, 3], reason: "", created_at: "2026-10-22T08:00:00Z", published_at: null, last_price: 150, model_pnl_pct: null, participation: {}, card: "",
  tg_error: null, timeframe: "1h" };

describe("Trading OS (owner)", () => {
  it("Publish and Cancel ask first: they reach every AUTO member's account", async () => {
    serve({ "GET /api/tradingos/admin/overview": overview, "GET /api/tradingos/admin/signals": { items: [pending] },
            "POST /api/tradingos/admin/signals/4/publish": { ok: true } });
    render(<TradingOSPage />);
    await flush();
    const ask = vi.fn(() => false);
    vi.stubGlobal("confirm", ask);
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await flush();
    expect(ask).toHaveBeenCalledTimes(2);
    expect(String((ask.mock.calls[0] as unknown[])[0])).toMatch(/Members with AUTO on will enter it/);
    expect(calls.some((c) => c.key.startsWith("POST"))).toBe(false);
    vi.stubGlobal("confirm", vi.fn(() => true));
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    await flush();
    expect(calls.some((c) => c.key === "POST /api/tradingos/admin/signals/4/publish")).toBe(true);
  });

  it("a failed Stop all new signals says so instead of failing silently", async () => {
    serve({ "GET /api/tradingos/admin/overview": overview, "GET /api/tradingos/admin/signals": { items: [] },
            "PUT /api/tradingos/admin/settings": new Response(JSON.stringify({ detail: "Database is busy" }), { status: 503 }) });
    render(<TradingOSPage />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Stop all new signals" }));
    await flush();
    expect(document.body.textContent).toMatch(/Database is busy/);
  });

  it("members' results show signed dollars (−$3.10, never $-3.10)", async () => {
    const r = (net: number) => ({ trades: 1, net, fees: 0.1, win_rate: null });
    serve({ "GET /api/tradingos/admin/overview": overview, "GET /api/tradingos/admin/signals": { items: [] },
            "GET /api/tradingos/admin/execution": { live_allowlist: "", global_paused: false, exchange: { ok: true },
              health: { positions_by_status: {}, protection_failed: 0, live_accounts: 0 }, accounts: [], orders: [],
              positions: [{ id: 1, account_id: 2, mode: "LIVE", coin: "BTC", side: "SHORT", status: "CLOSED", size: 0.01, entry_px: 80000, stop: 81000, tp1: 79000,
                            tp2: 78000, realized_pnl: -3.1, error: null, opened_at: "2026-10-22T08:00:00Z" }],
              results: { PAPER: r(4.2), TESTNET: r(0), LIVE: r(-3.1) } } });
    render(<TradingOSPage />);
    await flush();
    fireEvent.click(screen.getByRole("tab", { name: "Members' trades" }));
    await flush();
    const t = document.body.textContent!;
    expect(t).toMatch(/\+\$4\.20/); expect(t).toMatch(/−\$3\.10/); expect(t).not.toMatch(/\$-/);
  });
});

describe("Exchange auto-trader (owner)", () => {
  const status = { config: { enabled: true, spot: true, perps: true, activity: 4, swing_pct: 2, max_trade_value: 50000 }, last_tick: null, tick_seconds: 20,
    usdt: 100000, trades_last_hour: { spot: 1, perps: 2 }, markets: [] };
  it("a slider saves once, when it's let go, not on every step", async () => {
    serve({ "GET /api/exchange/admin/auto": status, "PUT /api/exchange/admin/auto": status });
    render(<ExchangeAutoCard />);
    await flush();
    const range = screen.getAllByRole("slider")[0];
    fireEvent.change(range, { target: { value: "5" } });
    fireEvent.change(range, { target: { value: "6" } });
    fireEvent.change(range, { target: { value: "7" } });
    expect(calls.filter((c) => c.key.startsWith("PUT"))).toHaveLength(0);
    expect(document.body.textContent).toMatch(/Trades per minute, per market 7/);  // the label follows the knob
    fireEvent.keyUp(range);
    await flush();
    const puts = calls.filter((c) => c.key.startsWith("PUT"));
    expect(puts).toHaveLength(1);
    expect(puts[0].body).toEqual({ activity: 7 });
  });
});

describe("Hyperliquid affiliate (owner)", () => {
  it("a failed snapshot is reported, and negative bot results read −$", async () => {
    const mem = { id: 1, username: "ana", display_name: "Ana", rank_level: 3, xp: 10, status: "active", joined_at: null, telegram: null, x: null, google: false,
      bot: { mode: "LIVE", auto: false, open: 0, closed: 2, net: -4.5 } };
    const report = { at: "2026-10-22T08:00:00Z", source: "hyperliquid",
      master: { wallet: "0xabc", stage: "ready", code: "KIWI", n_referrals: 1, referred_by: null, own_volume: 0, unclaimed: 0, claimed: 0, builder_rewards: 0, reward_history: [] },
      totals: { referrals: 1, matched: 1, volume: 100, fees_paid: 0.1, rewarded_to_us: 0.01, new_7d: 1, new_30d: 1, active: 0, balances: 10, ref_clicks_signed_in: 0,
                ref_clicks_anonymous: 0 },
      referrals: [{ address: "0x1111111111111111111111111111111111111111", joined_at: null, volume: 100, fees_paid: 0.1, rewarded_to_us: 0.01, member: mem,
                    match: "wallet", account: { perps_value: 10, spot_usdc: 0 } }],
      not_listed: [], clicked_no_wallet: [], history: { points: [], volume_change: {}, since: null } };
    serve({ "GET /api/tradingbot/admin/affiliate": report, "GET /api/tradingbot/admin/referral-clicks": { placements: [], totals: { "24h": 0, "7d": 0, "30d": 0 }, sources_30d: {} },
            "POST /api/tradingbot/admin/affiliate/snapshot": new Response(JSON.stringify({ detail: "Hyperliquid timed out" }), { status: 502 }) });
    render(<AffiliateAdminPage />);
    await flush();
    expect(document.body.textContent).toMatch(/2 closed \(−\$4\.50\)/);
    expect(document.body.textContent).toMatch(/doesn't list under KIWI/);
    fireEvent.click(screen.getByRole("button", { name: "Save snapshot now" }));
    await flush();
    expect(document.body.textContent).toMatch(/Hyperliquid timed out/);
  });
});
