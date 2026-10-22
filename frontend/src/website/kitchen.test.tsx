import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { KiwiKitchen, MlPanel, RiskTeamPanel, type KMarket, type KSnapshot } from "./kitchen";

/** TEST FIXTURES only: the kitchen snapshot API and a controllable EventSource. */
const checks = (n: number, failAt?: number): KMarket["checks"] =>
  ["setup", "confirm_30m", "liquidity", "crowding", "costs", "quality", "capacity"].map((id, i) => ({
    id, label: id, status: failAt === i ? "fail" : i < n ? "pass" : "skip", detail: `${id} detail`,
  }));
const market = (o: Partial<KMarket> = {}): KMarket => ({
  symbol: "BTC", timeframe: "15m", confirm_timeframe: "30m", state: "SEARCHING", reason: "Kiwi is looking for ingredients: A: no pullback",
  checks: checks(0), passed: 0, total: 7, setup: null, proposal: null,
  market: { mark: 64000, funding_h: 0.00001, oi_usd: 2.1e9, spread_bps: 0.4, day_volume: 3e9, change_24h: 1.2, regime: "STRONG_BULL" }, ...o,
});
const snap = (markets: KMarket[], o: Partial<KSnapshot> = {}): KSnapshot => ({
  strategy: "intraday_15m_v1", label: "15m / 30m intraday", timeframe: "15m", confirm_timeframe: "30m", shadow: false, live: false, paused: false,
  markets, events: [], last_id: 41, ...o,
});
const setup = { label: "Trend pullback", side: "LONG" as const, ref: 64000, stop: 63500, tp1: 64500, tp2: 65000, max_hold_min: 180,
  reasons: ["Uptrend on 1h/4h, 15m pulled back to EMA20"], cost_r: 0.11 };

function serve(s: KSnapshot) {
  vi.stubGlobal("fetch", vi.fn(async (input: string) => input.startsWith("/api/members/trading-bot/kitchen")
    ? new Response(JSON.stringify(s), { status: 200 }) : new Response("{}", { status: 404 })));
}
class FakeES {
  static last: FakeES | null = null;
  static CLOSED = 2;
  readyState = 1;
  url: string;
  handlers: Record<string, ((e: unknown) => void)[]> = {};
  constructor(url: string) { this.url = url; FakeES.last = this; }
  addEventListener(k: string, f: (e: unknown) => void) { (this.handlers[k] ??= []).push(f); }
  emit(k: string, data?: unknown) { for (const f of this.handlers[k] ?? []) f({ data: JSON.stringify(data) }); }
  close() { this.readyState = 2; }
}
const flush = async () => { for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };

afterEach(() => { cleanup(); vi.unstubAllGlobals(); FakeES.last = null; });

describe("Kiwi Kitchen", () => {
  const openChef = () => fireEvent.click(screen.getByRole("button", { name: /kiwi chef/i }));

  it("shows the state as animation only: no text next to the chef; tapping it explains (searching, no made-up progress)", async () => {
    vi.stubGlobal("EventSource", undefined);
    serve(snap([market()], { shadow: true }));
    const { container } = render(<KiwiKitchen />);
    await flush();
    expect(container.querySelector(".kk-svg.kk-searching")).toBeTruthy();
    expect(container.querySelector(".kk")!.textContent!.replace(/\?/g, "").trim()).toBe("");  // the scene only (its "?" prop aside)
    openChef();
    const d = screen.getByRole("dialog");
    expect(d.textContent).toContain("Kiwi is looking for ingredients.");
    expect(d.textContent).toContain("Shadow mode");
    expect(screen.queryByRole("progressbar")).toBeNull();  // no setup: no fake progress bar
  });

  it("READY: the dish, the real checks count and approval waiting are in the analysis; the most interesting market comes first", async () => {
    vi.stubGlobal("EventSource", undefined);
    serve(snap([market({ symbol: "ETH" }), market({ state: "READY", reason: "BTC LONG: every check passed", checks: checks(7), passed: 7, setup,
      proposal: { signal_id: 9, number: 9, side: "LONG", entry: [63990, 64010], stop: 63500, target: 64500, status: "PENDING_REVIEW", approval_required: true, shadow: false } })]));
    const { container } = render(<KiwiKitchen />);
    await flush();
    expect(container.querySelector(".kk-svg.kk-ready")).toBeTruthy();
    openChef();
    expect(container.querySelector(".kk-dish")!.textContent).toMatch(/BTC LONG.*Trend pullback.*waiting for approval/);
    expect(screen.getByRole("progressbar").textContent).toBe("Ingredients: 7/7 checks passed");
    expect(screen.getByRole("dialog").textContent).toContain("PAPER");  // live 15m trading not switched on
    fireEvent.click(screen.getByRole("tab", { name: "ETH" }));  // the other markets, switched from the analysis
    expect(container.querySelector(".kk-svg.kk-searching")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "ETH trade analysis" })).toBeTruthy();
  });

  it("clicking the chef opens the trade analysis: passed and failed checks, market, entry, risk, and the Kiwi Brain", async () => {
    vi.stubGlobal("EventSource", undefined);
    serve(snap([market({ state: "BURNT", reason: "Funding & open interest: longs pay 0.0500%/h funding (crowded)", checks: checks(3, 3), passed: 3, setup,
      risk: [{ id: "crowding", label: "Funding & open interest", status: "fail", detail: "longs pay 0.0500%/h funding (crowded)" }] })]));
    render(<KiwiKitchen />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: /kiwi chef: btc burnt/i }));
    const d = screen.getByRole("dialog", { name: "BTC trade analysis" });
    expect(d.textContent).toContain("Why it burnt");
    expect(d.textContent).toContain("Ingredients · 3/7 checks passed");
    expect(d.textContent).toContain("3 passed · 1 failed");
    expect(d.textContent).toContain("not a chance of winning");
    expect(d.textContent).toMatch(/Entry criteria.*BTC LONG.*stop 63,500/);
    expect(d.textContent).toContain("Funding0.0010%/h");
    expect(d.textContent).toContain("Risk checks");
    expect(screen.getByRole("link", { name: /full kiwi brain/i }).getAttribute("href")).toBe("/earn/brain");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("follows the live event stream from the snapshot's last event (no timers)", async () => {
    vi.stubGlobal("EventSource", FakeES);
    serve(snap([market({ state: "COOKING", reason: "BTC LONG candidate: waiting for the 30m candle closing at 10:30 UTC", checks: checks(1), passed: 1, setup })]));
    const { container } = render(<KiwiKitchen />);
    await flush();
    expect(FakeES.last!.url).toBe("/api/members/trading-bot/kitchen/stream?after=41");
    expect(container.querySelector(".kk-svg.kk-cooking")).toBeTruthy();
    act(() => { FakeES.last!.emit("open"); });
    act(() => {
      FakeES.last!.emit("kitchen", { id: 42, at: new Date().toISOString(), title: "🔥 BTC kitchen: BURNT", symbol: "BTC", state: "BURNT",
        reason: "30m confirmation: 30m close below EMA20", checks: checks(1, 1), passed: 1, total: 7 });
    });
    expect(container.querySelector(".kk-svg.kk-burnt")).toBeTruthy();  // the chef acts it out right away
    openChef();
    expect(screen.getByRole("dialog").textContent).toContain("30m confirmation: 30m close below EMA20");
  });
});

describe("Kiwi ML panel", () => {
  const ml = { mode: "auto", filtering: false, validated: false, profitable: false, threshold: 0.41, trained_at: "2026-10-18T00:00:00+00:00",
    period: ["2024-10-10", "2026-09-30"], source: "binance perps, 15m", samples: 28783, auc: 0.518,
    without_ml: { trades: 763, win_rate: 42.3, expectancy_r: -0.157, trades_per_day: 2.65 },
    with_ml: { trades: 511, win_rate: 44.6, expectancy_r: -0.123, trades_per_day: 1.78 } };
  it("shows this setup's estimate and the model's honest holdout record", () => {
    render(<MlPanel ml={ml} p={0.473} />);
    expect(screen.getByText(/advisory: shown, blocks nothing/)).toBeTruthy();
    expect(screen.getByText("47%")).toBeTruthy();
    const fine = screen.getByText(/28,783 past setups/).textContent ?? "";
    expect(fine).toContain("-0.16R without it");
    expect(fine).toContain("-0.12R with it");
    expect(fine).toContain("Neither is profitable");
  });
  it("says when it filters", () => {
    render(<MlPanel ml={{ ...ml, filtering: true, validated: true }} p={null} />);
    expect(screen.getByText(/filtering: setups under the bar are skipped/)).toBeTruthy();
    expect(screen.queryByText(/pre-set validation bar/)).toBeNull();
  });
});

describe("AI Risk Team panel", () => {
  const votes = [
    { desk: "exposure", label: "Correlation desk", vote: "veto" as const, detail: "#001 ETH LONG is already open" },
    { desk: "edge", label: "Edge desk", vote: "warn" as const, detail: "no proven edge" },
  ];
  it("shows the AI memo, every desk's vote, and that the AI doesn't decide", () => {
    render(<RiskTeamPanel rt={{ verdict: "VETO", reason: "Correlation desk: #001 ETH LONG is already open", votes,
      memo: { text: "The team blocked BTC LONG.", by: "gemma3" } }} />);
    expect(screen.getByText(/vetoed/)).toBeTruthy();
    expect(screen.getByText("The team blocked BTC LONG.")).toBeTruthy();
    expect(screen.getByText("Chief Risk Officer (AI)")).toBeTruthy();
    expect(screen.getByText(/no proven edge/)).toBeTruthy();
    expect(screen.getByText(/it doesn't decide/)).toBeTruthy();
  });
  it("without a memo yet shows the desks' reason", () => {
    render(<RiskTeamPanel rt={{ verdict: "APPROVE", reason: "approved by 4 of 5 desks", votes: votes.slice(1), memo: null }} />);
    expect(screen.getByText("approved by 4 of 5 desks")).toBeTruthy();
  });
});
