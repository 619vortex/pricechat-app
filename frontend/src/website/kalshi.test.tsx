import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KalshiSection, type Account, type KalshiState } from "./kalshi";

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

  it("says offline when the bot stopped reporting, and shows the owner its orders", async () => {
    const recent = [{ ts: now, event_ticker: "E", ticker: "E-0", count: 3, no_price: "0.55", status: "filled" }];
    serve((key) => key.includes("/activity") ? [200, { owner: true, activity: { ...ACT, live: false, equity_usd: 348.18, deployable_usd: 174.09, recent_trades: recent } }]
      : [200, state({ link: null })]);
    render(<KalshiSection />);
    await flush();
    const t = document.body.textContent!;
    expect(t).toContain("OFFLINE");
    expect(t).toContain("isn't running right now");
    expect(t).toContain("3 NO @ 55¢");
    expect(t).toContain("$174.09");
  });
});
