import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { refreshSession } from "../../../website/members";

vi.mock("./Chart", () => ({ PriceChart: ({ candles }: { candles: unknown[] }) => <div data-testid="chart">{candles.length} candles</div> }));
vi.mock("../../../website/sound", () => ({ playSound: () => undefined, warmSounds: () => undefined }));

import { ExchangePage } from "./Exchange";

/** TEST FIXTURES only: a fake exchange API. */
let routes: Record<string, unknown> = {};
const calls: { method: string; path: string; body: unknown }[] = [];
function serve(r: Record<string, unknown>) {
  routes = r;
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const path = String(input).split("?")[0];
    calls.push({ method: init?.method ?? "GET", path, body: init?.body ? JSON.parse(String(init.body)) : null });
    const body = routes[`${init?.method ?? "GET"} ${path}`] ?? routes[path];
    return body === undefined ? new Response(JSON.stringify({ detail: "nope" }), { status: 404 }) : new Response(JSON.stringify(body), { status: 200 });
  }));
}
const flush = async (n = 8) => { for (let i = 0; i < n; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
const market = { symbol: "CHAR1ST", title: "Charizard · Base Set 1st Edition · PSA 10", grade: "PSA 10", status: "active", tradable: true,
  card: { id: 1, name: "Charizard", set: "Base Set", number: "4/102", edition: "1st Edition", year: 1999, image: null },
  reference: { contract: 45027, card: 45027000, status: "ok", confidence: "medium", at: "2026-10-10T10:00:00Z" },
  last: 45100, bid: 44800, ask: 45300, premium_pct: 0.16,
  rules: { multiplier: "1/1000 of the card's reference value", tick: 1, min_qty: 1, max_qty: 1000, taker_fee_bps: 10, maker_fee_bps: 0, limit_band_pct: 50, market_slippage_pct: 5 },
  stats_24h: { trades: 3, volume: 7, turnover: 315000, high: 45300, low: 44800, change_pct: 0.67 } };
const me = { account: { id: 1, name: "ash" }, usdt: { available: 1_000_000, held: 0 }, equity: 1_000_000, start: 1_000_000, pnl: 0, realized: 0, unrealized: 0,
  positions: [], open_orders: [], orders: [], history: [], achievements: [{ key: "first_trade", name: "First trade", how: "x", earned: false }] };
const session = { signed_in: true, member: { id: "m1", username: "ash", xp: 1, rank: { level: 1, key: "egg", name: "The Egg", min_xp: 0, description: "", art: { treatment: "egg", overlay: null, placeholder: null, slot: null } } } };

afterEach(() => { cleanup(); vi.unstubAllGlobals(); localStorage.clear(); });

describe("Paper Exchange terminal", () => {
  it("is labelled paper trading, shows both prices, the book with SIM liquidity, and places a market buy", async () => {
    refreshSession();
    serve({
      "/api/members/session": session,
      "/api/members/exchange/markets": { paper_trading: true, markets: [market] },
      "/api/members/exchange/markets/CHAR1ST": { market, book: { bids: [{ price: 44800, qty: 3, orders: 1, simulated_qty: 3 }], asks: [{ price: 45300, qty: 3, orders: 1, simulated_qty: 3 }] },
                                                trades: [{ id: 1, price: 45100, qty: 1, side: "buy", at: "2026-10-10T10:00:00Z", simulated: true }] },
      "/api/members/exchange/markets/CHAR1ST/candles": { candles: [{ t: 1, o: 45000, h: 45300, l: 44800, c: 45100, v: 7 }], reference: [] },
      "/api/members/exchange/me": me,
      "POST /api/members/exchange/orders": { order: { id: 9, market: "CHAR1ST", side: "buy", type: "market", price: 47565, qty: 2, filled: 2, status: "filled", reason: null, client_id: "x", created_at: "" },
                                              fills: [{ price: 45300, qty: 2 }], duplicate: false },
    });
    const { container } = render(<MemoryRouter><ExchangePage /></MemoryRouter>);
    await flush();
    expect(container.querySelector(".ex-paper")!.textContent).toContain("PAPER TRADING");
    const stats = container.querySelector(".ex-stats")!.textContent!;
    expect(stats).toContain("451.00");  // paper price
    expect(stats).toContain("450.27");  // reference contract
    expect(stats).toContain("$450,270");  // the card's real reference value
    expect(container.querySelectorAll(".ex-ob li em").length).toBe(2);  // SIM labels on the maker's levels
    expect(screen.getByTestId("chart").textContent).toBe("1 candles");
    const qty = container.querySelectorAll<HTMLInputElement>(".ex-field input")[0];
    await act(async () => { qty.focus(); });
    const { fireEvent } = await import("@testing-library/react");
    fireEvent.change(qty, { target: { value: "2" } });
    await act(async () => { screen.getByRole("button", { name: "Buy 2 CHAR1ST" }).click(); });
    await flush();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.body).toMatchObject({ symbol: "CHAR1ST", side: "buy", type: "market", qty: 2 });
    expect(String((post.body as { client_id: string }).client_id)).toMatch(/^w/);  // an idempotency key per order
    expect(container.querySelector(".ex-msg")!.textContent).toContain("Bought 2 CHAR1ST @ 453.00");
  });

  it("asks visitors to sign in; a halted market can't be traded", async () => {
    refreshSession();
    const halted = { ...market, tradable: false, reference: { ...market.reference, status: "insufficient" } };
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/members/exchange/markets": { paper_trading: true, markets: [halted] },
            "/api/members/exchange/markets/CHAR1ST": { market: halted, book: { bids: [], asks: [] }, trades: [] },
            "/api/members/exchange/markets/CHAR1ST/candles": { candles: [], reference: [] } });
    const { container } = render(<MemoryRouter><ExchangePage /></MemoryRouter>);
    await flush();
    expect(container.querySelector(".ex-signin")!.textContent).toContain("10,000 paper USDT");
    expect(container.querySelector(".ex-badge")!.textContent).toBe("Halted");
    expect(container.querySelector(".ex-msg.is-bad")!.textContent).toContain("halted");
  });

  it("switches to Perps: mark, index, funding, leverage, est. liquidation, a 5x long, and the position with a Close button", async () => {
    refreshSession();
    const perp = { symbol: "CHAR1ST-PERP", spot: "CHAR1ST", title: market.title, grade: "PSA 10", tradable: true, card: market.card, reference: market.reference,
      index: 45000, mark: 45100, last: 45100, bid: 44900, ask: 45200, premium_pct: 0.22,
      funding: { last_ppm: 0, predicted_ppm: 92, next_at: new Date(Date.now() + 600_000).toISOString(), every_minutes: 60, cap_ppm: 500 },
      open_interest: 12, max_leverage: 10, mmr_bps: 500, taker_fee_bps: 10, maker_fee_bps: 0, stats_24h: { trades: 2, volume: 5, high: 45200, low: 44900, change_pct: 0.1 } };
    const position = { market: "CHAR1ST-PERP", side: "long", size: 4, entry: 45200, mark: 45100, liq_price: 42989, margin: 36160, leverage: 5, unrealized: -400,
      roe_pct: -1.11, realized: 0, funding: 0, liquidations: 0, value: 35760 };
    const withPerps = { ...me, perps: { positions: [position], open_orders: [], orders: [], history: [], equity_in_perps: 35760 } };
    serve({
      "/api/members/session": session,
      "/api/members/exchange/markets": { paper_trading: true, markets: [market] },
      "/api/members/exchange/markets/CHAR1ST": { market, book: { bids: [], asks: [] }, trades: [] },
      "/api/members/exchange/markets/CHAR1ST/candles": { candles: [], reference: [] },
      "/api/members/exchange/perps": { paper_trading: true, markets: [perp] },
      "/api/members/exchange/perps/CHAR1ST-PERP": { market: perp, book: { bids: [{ price: 44900, qty: 5, orders: 1, simulated_qty: 5 }], asks: [{ price: 45200, qty: 5, orders: 1, simulated_qty: 5 }] }, trades: [] },
      "/api/members/exchange/perps/CHAR1ST-PERP/candles": { candles: [], reference: [] },
      "/api/members/exchange/me": withPerps,
      "POST /api/members/exchange/perps/orders": { order: { id: 3, market: "CHAR1ST-PERP", side: "buy", type: "market", price: 47460, qty: 4, filled: 4, leverage: 5, reduce_only: false,
        status: "filled", reason: null, created_at: "" }, fills: [{ price: 45200, qty: 4 }], duplicate: false },
      "POST /api/members/exchange/perps/CHAR1ST-PERP/close": { order: { id: 4, status: "filled", reason: null }, fills: [{ price: 44900, qty: 4 }] },
    });
    const { container } = render(<MemoryRouter><ExchangePage /></MemoryRouter>);
    await flush();
    await act(async () => { screen.getByRole("tab", { name: /Perps/ }).click(); });
    await flush();
    expect(container.querySelector(".ex-paper")!.textContent).toContain("PAPER TRADING");
    const stats = container.querySelector(".ex-stats")!.textContent!;
    expect(stats).toContain("451.00");  // mark
    expect(stats).toContain("450.00");  // index
    expect(stats).toContain("+0.0092%");  // predicted hourly funding
    const { fireEvent } = await import("@testing-library/react");
    await act(async () => { screen.getByRole("button", { name: "5x" }).click(); });
    fireEvent.change(container.querySelectorAll<HTMLInputElement>(".ex-field input")[0], { target: { value: "4" } });
    const est = container.querySelector(".ex-est")!.textContent!;
    expect(est).toContain("361.60");  // margin = 4 x 452.00 / 5
    expect(est).toContain("Liquidation price");
    await act(async () => { screen.getByRole("button", { name: "Long 4 CHAR1ST-PERP · 5x" }).click(); });
    await flush();
    const post = calls.find((c) => c.method === "POST" && c.path.endsWith("/perps/orders"))!;
    expect(post.body).toMatchObject({ symbol: "CHAR1ST-PERP", side: "buy", type: "market", qty: 4, leverage: 5, reduce_only: false });
    expect(container.querySelector(".ex-msg")!.textContent).toContain("Long 4 CHAR1ST-PERP @ 452.00");
    const card = container.querySelector(".fx-pos")!;
    expect(card.textContent).toContain("LONG · 5x");
    expect(card.textContent).toContain("429.89");  // faints (liquidation) at
    expect(card.querySelector(".fx-pnl")!.textContent).toContain("−4.00");  // the big P&L
    expect(card.querySelector(".fx-stage .fx-card")).not.toBeNull();  // the card is on show
    expect(Number(card.querySelector("[role=meter]")!.getAttribute("aria-valuenow"))).toBeGreaterThan(90);  // HP: barely moved
    expect(container.querySelectorAll(".ex-ob li em").length).toBe(2);  // the order book is always there, SIM labelled
    await act(async () => { screen.getByRole("button", { name: /Close position/ }).click(); });
    await flush();
    expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/perps/CHAR1ST-PERP/close"))).toBe(true);
    const msg = container.querySelector(".ex-msg")!.textContent!;
    expect(msg).toContain("Closed 4 CHAR1ST-PERP");
    expect(msg).toContain("not very effective");
  });
});
