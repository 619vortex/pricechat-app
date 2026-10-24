import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { FundingArb, type FundingBoard } from "./arbitrage";
import { plan, tradeUrl } from "./fundingstart";

/** TEST FIXTURES only: a made-up funding board. */
const now = new Date().toISOString();
const leg = (venue: string, apr: number) => ({ venue, rate_raw: apr / 876000, interval_hours: 1, interval_source: "api", kind: "predicted", apr_pct: apr });
const row = (symbol: string, short: string, long: string, gap: number, mean?: number) => ({
  symbol, short, long, gross_spread_apr_pct: gap, capital_apr_pct: gap / 1.25, legs: [leg(short, gap / 2), leg(long, -gap / 2)],
  stats: mean == null ? null : { hours: 168, mean_apr_pct: mean, std_apr_pct: 5, z: 0.4, paid_share: 0.95, score_pct: mean - 10 },
});
const board = (): FundingBoard => ({
  as_of: now, pairs_found: 3, note: "", venues: {},
  rows: [row("XPL-PERP", "backpack", "orderly", 40, 30), row("1000BONK-PERP", "hyperliquid", "lighter", 30, 20), row("ZEC-PERP", "backpack", "orderly", 90)],
  assumptions: { leverage: 2, buffer: 0.25, capital_per_notional: 1.25, stability_k: 2, history_days: 7 },
});
afterEach(cleanup);

describe("Earn › Funding Arb › average APR and Start", () => {
  it("averages the 7-day APR over the pairs that have a record, and on capital", () => {
    render(<FundingArb d={board()} />);
    const avg = screen.getByRole("region", { name: "Average APR" });
    expect(avg.textContent).toContain("25.0%");  // (30 + 20) / 2
    expect(avg.textContent).toContain("20.0%");  // 25 / 1.25 on capital
    expect(avg.textContent).toContain("XPL");
  });

  it("sizes a pair: margin per leg, position, funding a day at the 7-day average, fees and break-even", () => {
    const p = plan(board().rows[0], 1000, 1.25);
    expect(p.margin).toBe(500);
    expect(p.notional).toBe(800);
    expect(p.perDay).toBeCloseTo(800 * 0.30 / 365, 6);
    expect(p.fees).toBeCloseTo(800 * 2 * (5 + 5) / 1e4, 6);  // Backpack + Orderly taker, open and close
    expect(p.fromAvg).toBe(true);
    expect(plan(board().rows[2], 1000, 1.25).fromAvg).toBe(false);  // no record yet: today's gap
  });

  it("links each leg to the venue's own market, with the thousand-unit names each venue uses", () => {
    expect(tradeUrl("hyperliquid", "1000BONK-PERP")).toBe("https://app.hyperliquid.xyz/trade/kBONK");
    expect(tradeUrl("lighter", "1000BONK-PERP")).toBe("https://app.lighter.xyz/trade/1000BONK");
    expect(tradeUrl("backpack", "XPL-PERP")).toBe("https://backpack.exchange/trade/XPL_USDC_PERP");
    expect(tradeUrl("orderly", "XPL-PERP")).toBe("https://pro.woofi.com/en/trade/PERP_XPL_USDC");
  });

  it("'Use' on a board row loads that pair into Start with both legs", () => {
    render(<FundingArb d={board()} />);
    fireEvent.click(screen.getByRole("button", { name: "Use 1000BONK in Start" }));
    const start = screen.getByRole("region", { name: "Start: run a funding pair yourself" });
    expect((start.querySelector("select") as HTMLSelectElement).value).toBe("1000BONK-PERP");
    const links = [...start.querySelectorAll("a.tb-btn")].map((a) => a.getAttribute("href"));
    expect(links).toEqual(["https://app.hyperliquid.xyz/trade/kBONK", "https://app.lighter.xyz/trade/1000BONK"]);
    expect(start.textContent).toContain("never places an order");
  });
});
