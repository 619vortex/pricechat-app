import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KalshiAdminPage, type KalshiOverview } from "./KalshiAdmin";

/** TEST FIXTURES only: a fake Kalshi bot admin API. No real account, key or token. */
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const flush = async () => { for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };

const now = new Date().toISOString();
const ROW = { P: 0.5, taker_1_cents: 1.75, taker_per_contract_at_10_cents: 1.75, maker_per_contract_at_10_cents: 0.4375, brief_formula_cents: 2,
  fee_pct_of_price: 3.5, need_hold_cents: 3.25, need_round_trip_cents: 5 };
const overview = (over: Partial<KalshiOverview> = {}): KalshiOverview => ({
  connected: true, token_created_at: now, last_seen_at: now, stale: false, bot: { version: "abc123", env: "production", host: "PC" },
  status: {
    received_at: now, env: "production", base_url: "https://external-api.kalshi.com", balance_usd: 348.18, portfolio_value_usd: 0,
    health: { signing: "ed25519", exchange_active: true, trading_active: true, rate_limits: { tier: "basic" } },
    positions: { open: 0, exposure_usd: 0, fees_paid_usd: 0, realized_pnl_usd: 0, markets: [] },
    store: { last_quote_ts: now, last_quote_age_s: 60, counts: {} },
    kill_switch: { execution_armed: false, order_code: "none (no order path exists before Phase 7)" },
    fee_gate_min_cents: 1.5, fee_curve: [ROW], fee_settings: { coefficient: 0.07, balance_precision: 0.0001 },
    phases: [{ id: "1", name: "Auth", state: "done" }, { id: "3", name: "Backtester", state: "next" }],
  },
  discovery: { received_at: now, run_id: "k1", markets: 151715, events: 15054, series: 15393, two_sided: 91809, with_volume_24h: 22135, quote_warnings: 0,
    unknown_fee_model: 0, threshold_ladder_events: 5333, elapsed_s: 98, calls: 260, rate_limited: 0, quotes_written: 20879,
    categories: { Sports: 87716, "Climate and Weather": 1236 }, samples: [{ kind: "weather_bin", market_id: "M", title: "Max temp 64-65°", category: "Climate and Weather",
      yes_bid: 0.3, yes_ask: 0.34, volume_24h: 900 }] },
  history: [], reports: 2, ...over,
});

let calls: string[] = [];
function serve(handler: (key: string) => [number, unknown]) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${input}`;
    calls.push(key);
    const [status, body] = handler(key);
    return new Response(JSON.stringify(body), { status });
  }));
}

describe("Kalshi bot admin", () => {
  it("shows the account, balance, trading off, the markets and the fee curve", async () => {
    serve(() => [200, overview()]);
    const { container } = render(<KalshiAdminPage />);
    await flush();
    const t = container.textContent!;
    expect(t).toContain("Real money");
    expect(t).toContain("$348.18");
    expect(t).toContain("Off");
    expect(t).toContain("151,715");
    expect(t).toContain("Climate and Weather");
    expect(t).toContain("Max temp 64-65°");
    expect(t).toContain("3.25¢");
    expect(t).toContain("Phase 3: Backtester");
  });

  it("warns when the bot has gone quiet", async () => {
    serve(() => [200, overview({ stale: true })]);
    const { container } = render(<KalshiAdminPage />);
    await flush();
    expect(container.textContent).toContain("hasn't reported for 45+ min");
  });

  it("creates a token once and shows how to start the bot", async () => {
    serve((key) => key.startsWith("POST") ? [200, { token: "pck_TESTONLY" }] : [200, overview({ connected: false, status: null, discovery: null })]);
    render(<KalshiAdminPage />);
    await flush();
    fireEvent.click(screen.getByText("Create bot token"));
    await flush();
    expect(calls.some((c) => c.startsWith("POST") && c.includes("/kalshi/admin/token"))).toBe(true);
    expect(screen.getByText(/PRICECHAT_KALSHI_TOKEN=pck_TESTONLY/)).toBeTruthy();
    expect(screen.getByText(/pmbot discover --every 15 --report/)).toBeTruthy();
  });
});
