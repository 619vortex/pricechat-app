import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { refreshSession } from "./members";

const played: string[] = [];
vi.mock("./sound", () => ({ playSound: (n: string) => played.push(n), warmSounds: () => undefined }));

import { TradingBotPage } from "./tradingbot";

/** TEST FIXTURES only: a fake API answering by method + path. */
let routes: Record<string, unknown> = {};
const calls: string[] = [];
function serve(r: Record<string, unknown>) {
  routes = r;
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${input.split("?")[0]}`;
    calls.push(key);
    const body = routes[key] ?? routes[input.split("?")[0]];
    return body === undefined ? new Response(JSON.stringify({ detail: "nope" }), { status: 404 }) : new Response(JSON.stringify(body), { status: 200 });
  }));
}
const flush = async (n = 4) => { for (let i = 0; i < n; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };

const site = { "/api/website/home": { sections: {}, latest: [], trending: [], whats_it_worth: [], originals: [], price_board: [], categories: [], links: {}, site_url: null, trading_bot: true } };
const art = { treatment: "none", overlay: null, placeholder: null, slot: null };
const rank = (level: number) => ({ level, key: `r${level}`, name: `Rank ${level} Kiwi`, min_xp: level * 100, description: "", art });
const rules = { min_rank_level: 3, min_deposit_usd: 10, referral_code: "BANTER", referral_url: "https://app.hyperliquid.xyz/join/BANTER", telegram_ready: true };
const state = (o: Record<string, unknown> = {}) => ({
  open: true, preview: false, signed_in: true, stage: "IN_PROGRESS", journey: 1, rules, steps: {}, member: { username: "kiwi", id: "m1" },
  rank: { current: rank(3), required: rank(3), ok: true, xp: 420, score: 300, score_needed: 300, remaining: 0, progress: 1 },
  wallet: null, telegram: { linked: true, username: "kiwi" }, application: null, can_apply: false,
  bot: { username: "cryptopricechatbot", track_record: false },
  flow: { hyperliquid: false, first_trade: false, has_wallet: false },
  access: { ready: false, group: false, bot: false, group_url: "https://t.me/botpricechat" }, ...o,
});
const render$ = async () => { const r = render(<MemoryRouter><TradingBotPage /></MemoryRouter>); await flush(); return r; };

beforeEach(() => {
  played.length = 0;
  localStorage.clear();
  refreshSession();
  window.matchMedia = ((q: string) => ({ matches: q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {} })) as never;
  window.scrollTo = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Earn: the trading bot funnel", () => {
  it("shows only the next step: Join Hyperliquid (one tap, then it's done and gone), with the disclosures and no inputs", async () => {
    serve({ ...site, "GET /api/members/trading-bot": state(),
            "POST /api/members/trading-bot/hyperliquid/joined": { xp: 25, state: state({ flow: { hyperliquid: true, first_trade: false, has_wallet: false },
                                                                                     access: { ready: true, group: false, bot: false, group_url: "https://t.me/botpricechat" } }) } });
    const { container } = await render$();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/meet the kiwi trading bot/i);
    expect(screen.getByLabelText("Done").textContent).toContain("✓ Kiwi Rank 3");
    expect(screen.queryByLabelText(/^Step 1:/)).toBeNull();  // done steps disappear
    const ref = screen.getByRole("link", { name: /open hyperliquid/i });
    expect(ref.getAttribute("href")).toBe("/api/r/site_trading_bot?src=web");
    expect(container.textContent).toMatch(/may earn a referral fee/i);
    expect(container.textContent).toMatch(/you can lose money/i);
    expect(container.querySelectorAll("input").length).toBe(0);  // nothing to type: never a seed phrase, key or password
    expect(screen.queryByLabelText("Live signals")).toBeNull();  // onboarding shows no trades
    expect(calls).not.toContain("GET /api/members/trading-os/signals");
    fireEvent.click(ref);
    await flush();
    expect(calls).toContain("POST /api/members/trading-bot/hyperliquid/joined");
    expect(played).toEqual([]);  // Earn is quiet: only a signal opening or closing makes a sound
    expect(screen.queryByRole("link", { name: /open hyperliquid/i })).toBeNull();  // gone after joining
    expect(screen.getByRole("button", { name: /i joined/i })).toBeTruthy();  // the next step appears
  });

  it("sends a below-rank Kiwi to level up first", async () => {
    serve({ ...site, "GET /api/members/trading-bot": state({ rank: { current: rank(1), required: rank(3), ok: false, xp: 20, score: 20, score_needed: 300, remaining: 280, progress: 0.07 } }) });
    await render$();
    expect(screen.getByRole("link", { name: /level up my kiwi/i }).getAttribute("href")).toBe("/passport");
    expect(screen.queryByRole("link", { name: /open hyperliquid/i })).toBeNull();
  });

  it("connect the bot: one button, no 'did you press Start?' confirm: the step finishes by itself when the bot sees Start", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      serve({ ...site, "GET /api/members/trading-bot": state({ flow: { hyperliquid: true, first_trade: false, has_wallet: false },
                                                              access: { ready: true, group: true, bot: false, group_url: null } }) });
      await render$();
      const link = screen.getByRole("link", { name: /connect the bot/i });
      expect(link.getAttribute("href")).toBe("https://t.me/cryptopricechatbot");
      fireEvent.click(link);
      await flush();
      expect(screen.queryByText(/did you press start/i)).toBeNull();
      expect(screen.queryByRole("button", { name: /^confirm$/i })).toBeNull();
      expect(screen.getByText(/waiting for you to press start/i)).toBeTruthy();
      routes["GET /api/members/trading-bot"] = state({ flow: { hyperliquid: true, first_trade: false, has_wallet: false },
                                                      access: { ready: true, group: true, bot: true, group_url: null } });  // the bot saw Start
      await act(async () => { vi.advanceTimersByTime(3100); });
      await flush();
      expect(screen.getByText(/connect your hyperliquid trading account in under a minute/i)).toBeTruthy();  // on to step 4 by itself
    } finally { vi.useRealTimers(); }
  });

  it("step 4: Connect Hyperliquid API (primary) or Use Paper Trading (secondary); paper opens the terminal", async () => {
    const before = state({ flow: { hyperliquid: true, first_trade: false, has_wallet: false }, access: { ready: true, group: true, bot: true, group_url: null },
                           trading: { api: false, paper: false, live: false } });
    serve({ ...site, "GET /api/members/trading-bot": before, "POST /api/members/trading/paper": { ok: true } });
    await render$();
    expect(screen.getByText(/connect your hyperliquid trading account in under a minute/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Connect Hyperliquid API" }).className).toMatch(/tb-cta/);  // the primary action
    expect(screen.getByText(/live trading needs your explicit confirmation/i)).toBeTruthy();
    routes["GET /api/members/trading-bot"] = { ...before, trading: { api: false, paper: true, live: false } };
    fireEvent.click(screen.getByRole("button", { name: "Use Paper Trading" }));
    await flush(6);
    expect(calls).toContain("POST /api/members/trading/paper");
    expect(screen.getByLabelText("Kiwi trading bot chat")).toBeTruthy();  // the terminal, in paper
  });

  it("a connected trader gets Kiwi's chat, then their positions: no hero, no onboarding, no leaderboard or control panels", async () => {
    serve({ ...site, "GET /api/members/trading-bot": state({ flow: { hyperliquid: true, first_trade: true, has_wallet: true },
                                                            access: { ready: true, group: true, bot: true, group_url: null } }) });
    const { container } = await render$();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/kiwi trading bot/i);
    expect(container.textContent).not.toMatch(/unlock the private/i);
    expect(screen.queryByRole("list", { name: /progress/i })).toBeNull();
    expect(container.querySelector(".tb-step, .tb-journey, .tb-hero")).toBeNull();
    expect(screen.queryByLabelText("Leaderboard")).toBeNull();
    const order = ["Kiwi trading bot chat", "My positions"].map((l) => screen.getByLabelText(l));
    expect(order[0].compareDocumentPosition(order[1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.queryByRole("link", { name: /open hyperliquid/i })).toBeNull();
  });

  it("the bot's track record says when Hyperliquid was read (it's cached for a few minutes)", async () => {
    serve({ ...site, "GET /api/members/trading-bot": state(), "GET /api/members/trading-bot/track-record": { configured: true, bot: "cryptopricechatbot",
      address: "0x1234567890abcdef1234567890abcdef12345678", as_of: "2026-10-22T08:05:00+00:00", pnl: { all: { pnl: 250.5, value: 1250.5, series: [0, 250.5] } } } });
    await render$();
    const card = screen.getByLabelText("Bot track record").textContent!;
    expect(card).toMatch(/From Hyperliquid, as of \d/); expect(card).toMatch(/\+\$250\.50/); expect(card).toMatch(/Account \$1,251/);
  });

  it("stays closed to the public until the owner opens it", async () => {
    serve({ ...site, "GET /api/members/trading-bot": { ...state(), open: false, preview: false } });
    await render$();
    expect(screen.getByText(/opening soon/i)).toBeTruthy();
    expect(screen.queryByRole("link", { name: /open hyperliquid/i })).toBeNull();
  });

  it("shows the leaderboard of valid referrals with trades and P&L", async () => {
    const row = (rank: number, username: string, o: Record<string, unknown> = {}) =>
      ({ rank, username, me: false, trades_30d: 12, trades_capped: false, pnl_30d: 100, pnl_all: 250.5, value: 1000, ...o });
    const items = [row(1, "alice", { trades_30d: 42, pnl_30d: 1234.5 }), row(2, "kiwi", { me: true, pnl_30d: -12 }), row(3, "carol", { trades_30d: null, pnl_30d: null, pnl_all: null })];
    serve({ ...site, "GET /api/members/trading-bot": state(),
            "GET /api/members/trading-bot/leaderboard": { items, me: items[1], count: 5, metric: "Members whose wallet Hyperliquid lists as referred." } });
    await render$();
    const board = screen.getByLabelText("Leaderboard");
    expect(board.textContent).toContain("5 traders");
    const rows = board.querySelectorAll("tbody tr");
    expect(rows.length).toBe(3);
    expect(rows[0].textContent).toContain("alice");
    expect(rows[0].textContent).toContain("42");
    expect(rows[0].textContent).toContain("+$1,235");
    expect(rows[1].classList.contains("is-me")).toBe(true);
    expect(rows[1].textContent).toContain("−$12");
    expect(rows[2].textContent).toContain("—");  // Hyperliquid couldn't be read: no made-up zero
  });

  it("keeps the sign-up page's leaderboard closed until 5 members trade", async () => {
    const items = [{ rank: 1, username: "alice", me: false, trades_30d: 3, trades_capped: false, pnl_30d: -0.2, pnl_all: -0.2 }];
    serve({ ...site, "GET /api/members/trading-bot": state(),
            "GET /api/members/trading-bot/leaderboard": { items, me: null, count: 1, metric: "Members whose wallet Hyperliquid lists as referred." } });
    await render$();
    const board = screen.getByLabelText("Leaderboard");
    expect(board.querySelectorAll("tbody tr").length).toBe(0);
    expect(board.textContent).toMatch(/opens once 5 members are trading/);
  });
});
