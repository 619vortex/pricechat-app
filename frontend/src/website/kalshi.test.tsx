import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KalshiSection, settledPnl, type Account, type KalshiState } from "./kalshi";
import { WeatherDesk, type WeatherBrief } from "./kalshiWeather";
import { isStale } from "./kalshiBrain";

/** TEST FIXTURES only: a fake Earn › Kalshi API. No real account or key. */
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const flush = async () => { for (let i = 0; i < 5; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };

const now = new Date().toISOString();
const LINK = { env: "production" as const, key_id: "0a472aed…5fef", key_type: "ed25519", connected_at: now, last_ok_at: now, last_error: null };
const WATCH = { received_at: now, markets: 151715, events: 15054, two_sided: 91809, with_volume_24h: 22135, categories: { Sports: 87716, "Climate and Weather": 1236 },
  fee_curve: [{ P: 0.5, taker_1_cents: 1.75, need_hold_cents: 3.25, fee_pct_of_price: 3.5 }], fee_gate_min_cents: 1.5 };
const state = (over: Partial<KalshiState> = {}): KalshiState => ({ open: true, owner: false, available: true, signed_in: true, link: null, watch: WATCH, ...over });
const ACCOUNT: Account = {
  env: "production", as_of: now, balance_usd: 348.18, portfolio_value_usd: 12, positions_total: 2,
  positions: [
    { ticker: "M-YES", side: "yes", count: 10, cost_usd: 4, title: "Rain in NYC?", category: "Climate and Weather", bid: 0.5, value_now_usd: 5, exit_fee_usd: 0.175,
      net_if_sold_usd: 4.825, pnl_if_sold_usd: 0.825, pays_if_right_usd: 10, fee_model: "quadratic" },
    { ticker: "M-NO", side: "no", count: 5, cost_usd: 2, title: "Flat-fee market", bid: 0.67, value_now_usd: 3.35, exit_fee_usd: null, net_if_sold_usd: null,
      pnl_if_sold_usd: null, pays_if_right_usd: 5, fee_model: "flat" }],
  totals: { cost_usd: 6, net_if_sold_usd: 4.83, pnl_if_sold_usd: 0.83, unpriced: 1 },
  orders: [], fills: [], settlements: [{ ticker: "S", result: "yes", settled_time: now, yes_count: 2, no_count: 0, cost_usd: 1.2, fee_usd: 0.03, revenue_usd: 2 }],
  fee_coefficient: 0.07,
};

let calls: { key: string; body?: string }[] = [];
function serve(handler: (key: string) => [number, unknown]) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${input}`;
    calls.push({ key, body: init?.body as string | undefined });
    const [status, body] = handler(key);
    return new Response(JSON.stringify(body), { status });
  }));
}

describe("Earn › Kalshi", () => {
  it("connects a key: needs the confirmation, sends env + key, then shows the live account", async () => {
    let linked = false;
    serve((key) => {
      if (key.startsWith("POST")) { linked = true; return [200, { link: LINK, balance_usd: 348.18 }]; }
      if (key.includes("/account")) return [200, ACCOUNT];
      return [200, state({ link: linked ? LINK : null })];
    });
    render(<KalshiSection />);
    await flush();
    const button = screen.getByRole("button", { name: "Connect" }) as HTMLButtonElement;
    fireEvent.change(screen.getByLabelText("Key ID"), { target: { value: "0a472aed-0000-4666-97fe-10e018905fef" } });
    fireEvent.change(screen.getByLabelText("Private key"), { target: { value: "-----BEGIN PRIVATE KEY-----\nTESTONLY\n-----END PRIVATE KEY-----" } });
    expect(button.disabled).toBe(true);  // not until they confirm it's their own account, allowed where they live
    fireEvent.click(screen.getByRole("checkbox"));
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    await flush();
    const post = calls.find((c) => c.key.startsWith("POST"))!;
    expect(post.key).toContain("/api/members/kalshi/connect");
    expect(JSON.parse(post.body!)).toMatchObject({ env: "production", key_id: "0a472aed-0000-4666-97fe-10e018905fef", confirm: true });
    const t = document.body.textContent!;
    expect(t).toContain("$348.18");
    expect(t).toContain("Rain in NYC?");
    expect(t).toContain("+$0.83");
    expect(t).toContain("fee unknown");  // a flat-fee series is never priced with a guessed fee
    expect(t).toContain("live, updated");
  });

  it("polls the account every 10 seconds while open", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      serve((key) => key.includes("/account") ? [200, ACCOUNT] : [200, state({ link: LINK })]);
      render(<KalshiSection />);
      await flush();
      const n = calls.filter((c) => c.key.includes("/account")).length;
      await act(async () => { vi.advanceTimersByTime(10_000); });
      await flush();
      expect(calls.filter((c) => c.key.includes("/account")).length).toBe(n + 1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("says coming soon while closed, asks to sign in, and shows the bot's market board", async () => {
    serve(() => [200, state({ open: false, available: false, signed_in: undefined, link: undefined })]);
    const { unmount } = render(<KalshiSection />);
    await flush();
    expect(document.body.textContent).toContain("Coming soon");
    expect(document.body.textContent).toContain("151,715");
    unmount();
    serve(() => [200, state({ signed_in: false })]);
    render(<KalshiSection />);
    await flush();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeTruthy();
  });
});

describe("Earn › Kalshi bot brain", () => {
  const ACT = {
    received_at: now, live: true, cycle: 12, mode: "paper", elapsed_s: 22.4,
    counts: { events: 13684, markets: 143446, me_events: 5597, priced: 5171, over_one_dollar: 46 },
    gates: { min_net_edge_cents: 1.5, min_arb_apr: 0.1, max_trade_fraction: 0.1, max_deployed_fraction: 0.5 },
    closest: [{ event: "KXATTYGENCO-26", title: "Colorado Attorney General", legs: 2, count: 0, sum_yes_bids: 1.007, gap_cents: 0.7, edge_cents: -0.121,
      edge_usd: -0.001, cost_usd: 1, apr: -0.01, days_to_close: 373, reason: "no edge: the YES bids don't sum past $1 plus fees", checked_book: false }],
    steps: [{ ms: 0, stage: "wake", text: "cycle 12: mode PAPER (no real orders)" }, { ms: 21857, stage: "scan", text: "read 13,684 open events, 143,446 markets" }],
    history: [{ ts: now, mode: "paper", best_edge_cents: -0.2, over_one_dollar: 40, trades: null, error: false },
      { ts: now, mode: "paper", best_edge_cents: -0.121, over_one_dollar: 46, trades: null, error: false }],
  };

  it("shows the bot's mode, its own steps, the closest basket and the scan counts", async () => {
    serve((key) => key.includes("/activity") ? [200, { owner: false, activity: ACT }] : [200, state({ link: null })]);
    render(<KalshiSection />);
    await flush();
    const t = document.body.textContent!;
    expect(t).toContain("PAPER");
    expect(t).toContain("cycle 12");
    expect(t).toContain("read 13,684 open events");
    expect(t).toContain("Colorado Attorney General");
    expect(t).toContain("-0.12¢");
    expect(t).toContain("143,446");
    expect(t).not.toContain("Orders (you only)");
  });

  it("says STALE (with the absolute last-seen time and pid) when the bot ran and stopped, and shows the owner its orders", async () => {
    const recent = [{ ts: now, event_ticker: "E", ticker: "E-0", count: 3, no_price: "0.55", status: "filled" }];
    const seen = "2026-10-20T06:29:15.000Z";  // days ago: far more than 2 x (60 s + 22.4 s)
    const scan_record = { scan_id: "s1", status: "ok", halt_reason: null, finished_at_utc: seen, duration_ms: 26114,
      instance: { pid: 15100, started_at_utc: "2026-10-28T06:28:21Z", host: "DESKTOP" } };
    serve((key) => key.includes("/activity") ? [200, { owner: true, activity: { ...ACT, received_at: seen, live: false, every_s: 60, scan_record,
      equity_usd: 348.18, deployable_usd: 174.09, recent_trades: recent } }] : [200, state({ link: null })]);
    render(<KalshiSection />);
    await flush();
    const t = document.body.textContent!;
    expect(t).toContain("STALE");
    expect(t).not.toContain("STANDBY");
    expect(t).toContain("Last report 2026-10-20 06:29:15 UTC");
    expect(t).toContain("pid 15100 on DESKTOP");
    expect(t).toContain("3 NO @ 55¢");
    expect(t).toContain("$174.09");
  });
});

describe("Earn › Kalshi weather desk", () => {
  const brief = (over: Partial<WeatherBrief> = {}): WeatherBrief => ({
    enabled: false, reason: "model adds no skill over the market alone", version: "v1", computed_at: new Date(Date.now() - 864e5).toISOString(),
    recheck_at: new Date(Date.now() + 29 * 864e5 + 3 * 36e5 + 30e3).toISOString(), stale_at: now, bss: 0.004, bss_ci90: [0.0018, 0.0061], floor: 0,
    contribution_ci90: [-0.0006, 0.0032], market_only: 0.0026, model_alone: -0.21,
    tested: { markets: 43488, events: 7248, from: "2026-04-29", to: "2026-10-25", days: 180, history_markets: 89636 },
    pnl: [{ min_edge: 0.06, trades: 630, mean_cents: 3.395, total_usd: 21.39, total_ci90_by_day: [3.49, 40.85], hit_rate: 0.57 }], ...over,
  });

  it("tells the viewer to expect no weather trades yet, with the counts and a ticking re-test countdown", () => {
    render(<WeatherDesk w={brief()} />);
    expect(screen.getByText("WATCHING · NOT TRADING")).toBeTruthy();
    expect(screen.getByText("no weather trades yet.")).toBeTruthy();
    expect(screen.getByText("43,488")).toBeTruthy();
    expect(screen.getByText("-0.06% to +0.32%")).toBeTruthy();
    expect(screen.getByRole("timer").textContent).toMatch(/next re-test in29d03h/);
    expect(screen.getByText(/630 one-contract trades/).textContent).toMatch(/gate stays shut/);
  });

  it("says weather trades can appear once the gate opens", () => {
    render(<WeatherDesk w={brief({ enabled: true, contribution_ci90: [0.001, 0.004] })} />);
    expect(screen.getByText("CLEARED TO TRADE")).toBeTruthy();
    expect(screen.getByText(/weather trades can now appear/)).toBeTruthy();
    expect(screen.queryByText(/gate stays shut/)).toBeNull();
  });
});

describe("Earn › Kalshi liveness", () => {
  const base = { received_at: new Date().toISOString(), live: true, cycle: 3, mode: "paper" as const, steps: [], history: [], every_s: 60, elapsed_s: 25 };

  it("STANDBY only when the bot never reported; a fresh report is not STALE", async () => {
    serve((key) => key.includes("/activity") ? [200, { owner: false, activity: null }] : [200, state({ link: null })]);
    render(<KalshiSection />);
    await flush();
    expect(document.body.textContent).toContain("STANDBY");
    cleanup();
    serve((key) => key.includes("/activity") ? [200, { owner: false, activity: base }] : [200, state({ link: null })]);
    render(<KalshiSection />);
    await flush();
    expect(document.body.textContent).toContain("PAPER");
    expect(document.body.textContent).not.toContain("STALE");
  });

  it("goes STALE after more than 2 scan intervals (sleep + scan time)", () => {
    const t0 = Date.parse("2026-10-28T06:00:00Z");
    const a = { ...base, received_at: "2026-10-28T06:00:00Z" };
    expect(isStale(a, t0 + 2 * 85 * 1000)).toBe(false);
    expect(isStale(a, t0 + 2 * 85 * 1000 + 1)).toBe(true);
  });
});

describe("Earn › Kalshi settled rows", () => {
  // Kalshi GET /portfolio/settlements for the owner's account (2026-10-26)
  const LULA = { ticker: "KXBRPRES-26-LULA", result: "yes", settled_time: "2026-10-26T01:09:02Z", yes_count: 166, no_count: 166, cost_usd: 174.96,
    fee_usd: 5.73, revenue_usd: 0 };

  it("shows a matched YES/NO round trip as the loss it was", () => {
    expect(settledPnl(LULA)).toEqual({ payout: 166, pnl: -14.69 });
    expect(settledPnl({ ...LULA, yes_count: 2, no_count: 0, cost_usd: 1.2, fee_usd: 0.03, revenue_usd: 2 })).toEqual({ payout: 2, pnl: 0.77 });
  });

  it("renders Payout and Realized P&L columns, the loss in red", async () => {
    serve((key) => key.includes("/account") ? [200, { ...ACCOUNT, settlements: [LULA] }] : key.includes("/activity") ? [200, { owner: false, activity: null }]
      : [200, state({ link: LINK })]);
    render(<KalshiSection />);
    await flush();
    const row = screen.getByText("KXBRPRES-26-LULA").closest("tr")!;
    const heads = Array.from(row.closest("table")!.querySelectorAll("th")).map((th) => th.textContent);
    // P&L right after what was held, before the cost columns: never scrolled off the right edge
    expect(heads).toEqual(["Market", "Result", "Held", "Realized P&L", "Payout", "Cost", "Fees"]);
    const pnl = row.querySelectorAll("td")[3];
    expect(pnl.textContent).toBe("−$14.69");
    expect(pnl.className).toBe("is-bad");
    expect(row.querySelectorAll("td")[4].textContent).toBe("$166.00");
  });

  it("puts lifetime realized P&L, settled bets and win rate in the stat row instead of the sold-now pair", async () => {
    const lifetime = { realized_pnl_usd: -13.92, settled: 2, wins: 1, losses: 1, win_rate: 0.5, complete: true };
    serve((key) => key.includes("/account") ? [200, { ...ACCOUNT, positions: [], positions_total: 0, settlements: [LULA], lifetime }]
      : key.includes("/activity") ? [200, { owner: false, activity: null }] : [200, state({ link: LINK })]);
    render(<KalshiSection />);
    await flush();
    const stats = Array.from(document.querySelectorAll(".arb-stat")).map((s) => s.textContent);
    expect(stats.some((s) => s!.includes("Realized P&L") && s!.includes("−$13.92") && s!.includes("lifetime"))).toBe(true);
    expect(stats.some((s) => s!.includes("Settled bets") && s!.includes("1 won · 1 lost"))).toBe(true);
    expect(stats.some((s) => s!.includes("Win rate") && s!.includes("50%"))).toBe(true);
    expect(document.body.textContent).not.toMatch(/if (you )?sold now/i);  // flat: nothing to sell, so no $0.00 pair
  });

  it("falls back to the rows sent, marked as not lifetime, when the server has no totals", async () => {
    serve((key) => key.includes("/account") ? [200, { ...ACCOUNT, settlements: [LULA] }]
      : key.includes("/activity") ? [200, { owner: false, activity: null }] : [200, state({ link: LINK })]);
    render(<KalshiSection />);
    await flush();
    expect(document.body.textContent).toContain("latest 1 settled markets");
    expect(document.body.textContent).toContain("1+");
  });
});

describe("Earn › Kalshi header", () => {
  const act = (mode: string, received_at = new Date().toISOString()) =>
    ({ received_at, live: true, cycle: 1, mode, steps: [], history: [], every_s: 60, elapsed_s: 25 });
  const head = async (activity: unknown) => {
    serve((key) => key.includes("/activity") ? [200, { owner: false, activity }] : [200, state({ link: null })]);
    render(<KalshiSection />);
    await flush();
    return document.querySelector(".arb-head p")!.textContent!;
  };

  it("reads the mode from the bot instead of claiming it trades live", async () => {
    expect(await head(act("paper"))).toMatch(/paper mode.*places no real orders/);
    cleanup();
    expect(await head(act("live"))).toMatch(/trading live with real money/);
    cleanup();
    expect(await head(act("halted"))).toMatch(/halted/);
    cleanup();
    expect(await head(act("live", "2026-10-20T00:00:00Z"))).toMatch(/stopped reporting/);
    cleanup();
    expect(await head(null)).toMatch(/hasn't reported/);
  });
});
