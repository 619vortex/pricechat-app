import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { EarnTabs, FundingArb, LunchBot, PnlLine, SHOW_GRADUATIONS, type FundingBoard, type Lunch, type Scoreboard, type SeriesPoint } from "./arbitrage";

/** TEST FIXTURES only: made-up Lunch Bot paper results. */
const now = new Date().toISOString();
const trade = (over: Record<string, unknown>) => ({ id: 1, mint: "m1", symbol: "HOT", name: "Hot", image_uri: null, status: "open", launched_at: now, opened_at: now,
  closed_at: null, entry_sol: 0.101, entry_mc_sol: 60, value_sol: 0.15, pnl_sol: 0.049, pnl_pct: 48.5, exit_reason: null,
  signal: { real_sol: 6, growth_pct: 80, minutes_after_launch: 2 }, awaiting_pool: false, pump_url: "https://pump.fun/coin/m1", solscan_url: "", ...over });
const data = (over: Partial<Lunch> = {}): Lunch => ({
  mode: "paper",
  stats: { trades: 4, wins: 1, win_rate: 25, pnl_sol: -0.12, avg_pnl_sol: -0.03, best_sol: 0.09, worst_sol: -0.06, by_exit: { stop: 2, timeout: 1, target: 1 },
           open: 1, open_value_sol: 0.15, open_cost_sol: 0.101 },
  open: [trade({}) as never],
  closed: [trade({ id: 2, symbol: "DUD", status: "closed", exit_reason: "stop", value_sol: 0.05, pnl_sol: -0.051, pnl_pct: -50.5 }) as never],
  graduations: { measured: 3, avg_gap_pct: -0.4, median_gap_pct: -0.2, positive: 1, after_costs_positive: 0,
                 recent: [{ mint: "g1", symbol: "GRAD", curve_price: 4.1e-7, pool_price: 4.08e-7, gap_pct: -0.49, dex: "pumpswap", at: now }] },
  days: [{ day: "2026-10-21", launches_seen: 1234, entries: 5 }],
  menu: { size_sol: 0.1, watch_minutes: 10, min_real_sol: 2, min_growth_pct: 30, target_pct: 100, stop_pct: -40, timeout_minutes: 30, max_open: 20 },
  costs: { curve_fee_pct: 1.25, pool_fee_pct: 0.3, tx_cost_sol: 0.001 },
  scanner: { last_run_at: now, watching: 17, rate_limited_until: null, last_error: null },
  ...over,
});
afterEach(cleanup);

describe("Earn › Arbitrage", () => {
  it("has five tabs, the trading bot first", () => {
    render(<EarnTabs current="lunch" />);
    const tabs = screen.getAllByRole("link");
    expect(tabs.map((t) => t.querySelector("b")?.textContent)).toEqual(["Trading Bot", "Lunch Bot", "Funding Arb", "AI Committee", "Kalshi"]);
    expect(tabs.map((t) => t.getAttribute("href"))).toEqual(["/earn", "/earn/lunch", "/earn/funding", "/earn/committee", "/earn/kalshi"]);
    expect(tabs[1].getAttribute("aria-current")).toBe("page");
  });

  it("Funding Arb lists the gaps with each venue's own interval and names a venue that's down", () => {
    const board: FundingBoard = {
      as_of: now, pairs_found: 2, note: "Gross funding spread before fees.",
      venues: { hyperliquid: { ok: true, symbols: 178, error: null, interval_hours: [1], interval_source: "derived" },
                orderly: { ok: true, symbols: 73, error: null, interval_hours: [4, 8], interval_source: "api" },
                lighter: { ok: false, symbols: 0, error: "VenueUnavailable: timeout", interval_hours: [], interval_source: null } },
      rows: [{ symbol: "JTO-PERP", short: "backpack", long: "hyperliquid", gross_spread_apr_pct: 120.21, legs: [
        { venue: "backpack", rate_raw: 0.00014972, interval_hours: 1, interval_source: "api", kind: "predicted", apr_pct: 131.159 },
        { venue: "hyperliquid", rate_raw: 0.0000125, interval_hours: 1, interval_source: "derived", kind: "predicted", apr_pct: 10.95 }] }],
    };
    render(<FundingArb d={board} />);
    expect(screen.getByRole("heading", { name: "Funding Arb" })).toBeTruthy();
    expect(screen.getByText("SCANNER ONLY")).toBeTruthy();
    expect(screen.getByText("JTO")).toBeTruthy();
    expect(screen.getAllByText("—", { selector: "td" }).length).toBeGreaterThan(0);  // no 7-day stats in this row: dashes, never zeros
    expect(screen.getByText("+120.21%")).toBeTruthy();
    expect(screen.getByText("funding every 4h / 8h")).toBeTruthy();
    expect(screen.getByText("offline")).toBeTruthy();
    expect(screen.getByText("VenueUnavailable: timeout")).toBeTruthy();
  });

  it("Funding Arb shows the 7-day average, the swing, the z-score and capital APR, and warns on extremes", () => {
    const leg = (venue: string, apr_pct: number) => ({ venue, rate_raw: apr_pct / 876000, interval_hours: 1, interval_source: "api", kind: "predicted", apr_pct });
    const board: FundingBoard = { as_of: now, pairs_found: 1, note: "", venues: {},
      assumptions: { leverage: 2, buffer: 0.25, capital_per_notional: 1.25, stability_k: 2, history_days: 7 },
      rows: [{ symbol: "NEAR-PERP", short: "hyperliquid", long: "lighter", gross_spread_apr_pct: 77.4, capital_apr_pct: 61.92,
               legs: [leg("hyperliquid", 87.9), leg("lighter", 10.5)], stats: { hours: 168, mean_apr_pct: 0.28, std_apr_pct: 9.53, z: 8.1, paid_share: 0.77, score_pct: -15 } }] };
    render(<FundingArb d={board} />);
    expect(screen.getByText("+61.92%")).toBeTruthy();
    expect(screen.getByText("+0.28% ± 9.5%")).toBeTruthy();
    expect(screen.getByText("+8.1σ").getAttribute("title")).toContain("usually drifts back");
    expect(screen.getByText("77%")).toBeTruthy();
    expect(screen.getByText(/2x each plus a 25% buffer/)).toBeTruthy();
  });

  it("Lunch Bot shows its paper results, the rules and the costs (the graduation gap stays hidden for now)", () => {
    render(<LunchBot d={data()} />);
    expect(screen.getByRole("heading", { name: "Lunch Bot" })).toBeTruthy();
    expect(screen.getByText("PAPER TRADING")).toBeTruthy();
    expect(screen.getByText("−0.120 SOL")).toBeTruthy();
    expect(screen.getByText("25%")).toBeTruthy();
    expect(screen.getByText(/1,234 launches seen today/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "$HOT" }).getAttribute("href")).toBe("https://pump.fun/coin/m1");
    expect(screen.getByText("+48.5%")).toBeTruthy();
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(expect.arrayContaining(["In (SOL)", "Now / out (SOL)"]));  // units on the trade table
    expect(screen.getByText(/Buy 0.1 SOL when buyers have put at least 2 SOL in/)).toBeTruthy();
    expect(screen.getByText(/1.25% per curve trade, 0.3% per pool trade, 0.001 SOL per transaction/)).toBeTruthy();
    expect(SHOW_GRADUATIONS).toBe(false);
    expect(screen.queryByText(/graduation arbitrage/)).toBeNull();
  });

  it("Lunch Bot scores its two strategies apart and lists both menus", () => {
    const st = (pnl: number, open: number) => ({ trades: 3, wins: 1, win_rate: 33.3, pnl_sol: pnl, avg_pnl_sol: null, best_sol: null, worst_sol: null, by_exit: {},
      open, open_value_sol: 0, open_cost_sol: 0 });
    const d = data({ by_strategy: { starter: st(-0.05, 1), graduation: st(0.12, 2) },
      menu: { ...data().menu, graduation: { min_progress_pct: 80, max_progress_pct: 97, min_rise_pts: 1, stop_pct: -25, timeout_minutes: 90, max_open: 10, near_now: 37 } },
      open: [trade({ strategy: "graduation" }) as never] });
    render(<LunchBot d={d} />);
    expect(screen.getByText("Graduation Run")).toBeTruthy();
    expect(screen.getByText("+0.120 SOL")).toBeTruthy();  // a gain is signed, never read as a balance
    expect(screen.getByText("−0.050 SOL")).toBeTruthy();
    expect(screen.getByText(/Track pump.fun's fullest curves \(37 right now\)/)).toBeTruthy();
    expect(screen.getByText(/80–97% full and still filling/)).toBeTruthy();
    expect(screen.getByTitle("Graduation Run").textContent).toBe("🍰");
  });

  it("Lunch Bot's scoreboard: PAPER, per-strategy cards with win rate, P&L, trades, best / worst, a cumulative line (gap stat hidden for now)", () => {
    const series = (cum: number[]): SeriesPoint[] => cum.map((c, i) => ({ day: `2026-10-${String(i + 1).padStart(2, "0")}`, trades: 1, wins: 0, pnl_sol: 0, cumulative: c }));
    const st = (pnl: number, wr: number, best: number, worst: number) => ({ trades: 12, wins: 5, win_rate: wr, pnl_sol: pnl, avg_pnl_sol: null, best_sol: best,
      worst_sol: worst, by_exit: {}, open: 0, open_value_sol: 0, open_cost_sol: 0 });
    const sb: Scoreboard = { days: 30, series: { starter: series([0, -0.02, -0.05]), graduation: series([0.01, 0.03, 0.08]) },
      gaps: { all: { count: 40, avg_gap_pct: -1.2, median_gap_pct: -0.8, positive: 9 }, last_7d: { count: 6, avg_gap_pct: 0.5, median_gap_pct: 0.4, positive: 3 }, costs_pct: 1.55 } };
    render(<LunchBot d={data({ scoreboard: sb, by_strategy: { starter: st(-0.05, 41.7, 0.04, -0.03), graduation: st(0.211, 58.3, 0.12, -0.025) } })} />);
    const board = screen.getByRole("region", { name: "Scoreboard" });
    expect(board.textContent).toContain("PAPER");
    const grad = screen.getByLabelText("Graduation Run scorecard");
    expect(grad.textContent).toContain("0.211 SOL");
    expect(grad.textContent).toContain("58.3%");
    expect(grad.textContent).toContain("12");
    expect(grad.textContent).toContain("+0.120");
    expect(grad.textContent).toContain("−0.025");
    const line = screen.getByRole("img", { name: /Graduation Run: \+0\.080 SOL over 30 days/ });
    expect(line.getAttribute("class")).toContain("is-up");
    expect(screen.getByRole("img", { name: /Starter: −0\.050 SOL/ }).getAttribute("class")).toContain("is-down");
    expect(board.textContent).not.toContain("median");  // SHOW_GAP_STAT is off until the pool data is trusted
  });

  it("the P&L line is an inline SVG polyline scaled to include zero, and says so when there's nothing to draw", () => {
    const { container } = render(<PnlLine label="x" pts={[{ day: "2026-10-01", trades: 0, wins: 0, pnl_sol: 0, cumulative: 0.1 },
                                                           { day: "2026-10-02", trades: 1, wins: 1, pnl_sol: 0.1, cumulative: 0.2 }]} />);
    const pts = container.querySelector("polyline")!.getAttribute("points")!.split(" ").map((p) => p.split(",").map(Number));
    expect(pts[0][0]).toBe(0);
    expect(pts[1][0]).toBe(300);
    expect(pts[1][1]).toBeLessThan(pts[0][1]);  // higher P&L is higher on screen
    expect(Number(container.querySelector("line")!.getAttribute("y1"))).toBeCloseTo(68);  // zero sits at the bottom (all values ≥ 0)
    cleanup();
    render(<PnlLine label="x" pts={[]} />);
    expect(screen.getByText(/No closed trades in the window yet/)).toBeTruthy();
  });

  it("says plainly when nothing is open", () => {
    render(<LunchBot d={data({ open: [] })} />);
    expect(screen.getByText(/Nothing on the plate/)).toBeTruthy();
  });
});
