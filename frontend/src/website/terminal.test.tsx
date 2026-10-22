import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./sound", () => ({ playSound: () => undefined, warmSounds: () => undefined }));

import { TradingTerminal, type Account, type Connection, type Pos, type Sig, type Terminal } from "./terminal";

/** TEST FIXTURES only: a fake API answering by method + path (a function answers dynamically). */
const calls: { key: string; body: unknown }[] = [];
function serve(routes: Record<string, unknown>) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${input.split("?")[0]}`;
    const body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ key, body });
    let out = routes[key];
    if (typeof out === "function") out = (out as (b: unknown) => unknown)(body);
    if (out instanceof Response) return out;
    return out === undefined ? new Response(JSON.stringify({ detail: "nope" }), { status: 404 }) : new Response(JSON.stringify(out), { status: 200 });
  }));
}
const flush = async (n = 5) => { for (let i = 0; i < n; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
const result = { trades: 0, net: 0, fees: 0, win_rate: null };
const account = (o: Partial<Account> = {}): Account => ({
  mode: "LIVE", paused: false, paused_reason: null,
  limits: { max_leverage: 3, max_loss_per_trade_usd: 5, max_allocation_pct: 10, max_positions: 2, daily_loss_limit_usd: 15, allowed_coins: "" },
  caps: { max_leverage: [1, 5], max_loss_per_trade_usd: [1, 500], max_allocation_pct: [1, 50], max_positions: [1, 3], daily_loss_limit_usd: [1, 2000] },
  auto: { enabled: true, until: null }, agents: { TESTNET: { address: "0xabc", approved: true }, LIVE: { address: "0xdef", approved: true } },
  live: { allowed: true, enabled: true, checks: {} }, paper_equity: 1000, daily_pnl: 0, positions: [{ id: 5, mode: "LIVE", status: "OPEN" }],
  results: { PAPER: result, TESTNET: result, LIVE: { trades: 1, net: 4.2, fees: 0.3, win_rate: 100 } }, ...o,
});
const pos = (o: Partial<Pos> = {}): Pos => ({
  id: 5, signal_id: 9, mode: "LIVE", coin: "SOL", side: "LONG", status: "OPEN", via: "enter", size: 2, size_usd: 300, entry_px: 145, mark: 150, leverage: 3,
  margin: 96.67, liq_px: 99.5, upnl: 10, upnl_pct: 10.34, stop: 140, tp1: 155, tp2: 160, tp1_done: false, sl_status: "ACTIVE", tp1_status: "ACTIVE",
  tp2_status: "ACTIVE", fees: 0.13, funding: 0.02, realized: 0, opened_at: "2026-10-15T09:00:00+00:00", warnings: [], error: null, ...o,
});
const sig = (o: Partial<Sig> = {}): Sig => ({ id: 9, number: 4, coin: "SOL", side: "LONG", status: "ACTIVE", status_label: "ACTIVE", entry: [144, 146], stop: 140,
  target: 155, rr: 2.1, leverage: [2, 3], last_price: 150, published_at: "2026-10-15T08:00:00+00:00", participation: { enter: 3 }, strategy: "trend_pullback_v1", ...o });
const term = (o: Partial<Terminal> = {}): Terminal => ({
  overview: { mode: "LIVE", auto: true, auto_until: null, paused: false, realized_today: 4.2, simulated: false, equity: 1520.5, available: 1300, margin_used: 96.67,
              unrealized: 10, exposure: 300 },
  sync: { source: "hyperliquid", at: "2026-10-15T10:00:00+00:00", age_s: 2, stale: false, error: null },
  positions: [pos()], other_positions: [], history: [], signals: { 9: { status: "POSITION_OPEN", position_id: 5, mode: "LIVE" } },
  account: account(), global_paused: false, ...o,
});
const conn: Connection = { rankLevel: 4, rankOk: true, minRank: 3, wallet: "0x1234567890abcdef1234567890abcdef12345678", referral: true, group: true, bot: true,
  botUsername: "cryptopricechatbot", groupUrl: "https://t.me/botpricechat" };
const T = "GET /api/members/trading/terminal", S = "GET /api/members/trading-os/signals";

beforeEach(() => { window.matchMedia = ((q: string) => ({ matches: q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {} })) as never; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const open = async (routes: Record<string, unknown>) => { serve(routes); const r = render(<TradingTerminal conn={conn} />); await flush(); return r; };

describe("Kiwi trading bot", () => {
  it("leads with Kiwi's chat: the real signal as a message, Kiwi's own reason, and no developer controls", async () => {
    const { container } = await open({ [T]: term(), [S]: { items: [sig({ reason: "Uptrend on 1h, pullback to EMA20.", timeframe: "1h", tp2: 160.5 })] } });
    const chat = screen.getByLabelText("Kiwi trading bot chat");
    expect(within(chat).getByRole("heading", { level: 1 }).textContent).toBe("KIWI TRADING BOT");
    expect(chat.textContent).toMatch(/Online/);
    const card = within(chat).getAllByLabelText("Signal 004 SOL LONG")[0].textContent!;
    expect(card).toMatch(/Entry\$144–\$146/); expect(card).toMatch(/Stop Loss\$140/); expect(card).toMatch(/Take Profit 1\$155/);
    expect(card).toMatch(/Take Profit 2≈ \$160\.5/); expect(card).toMatch(/Leverage2–3x/); expect(card).toMatch(/Risk\/Reward1:2\.1/);
    expect(card).toMatch(/Kiwi says: “Uptrend on 1h, pullback to EMA20\.”/);
    expect(card).toMatch(/👥 3 Kiwis in/);  // live entry count (participation.enter)
    expect(within(chat).getAllByRole("link", { name: "VIEW POSITION" })[0].getAttribute("href")).toBe("#kt-pos-5");  // already entered
    expect(container.textContent).not.toMatch(/TESTNET|TRADING CONTROLS|Risk limits|TRADING TERMINAL/);
    expect(screen.queryByRole("radiogroup")).toBeNull();  // no mode selector
  });

  it("only says what really happened: a trigger and a position open come from the records, nothing else is invented", async () => {
    await open({ [T]: term(), [S]: { items: [sig({ activated_at: "2026-10-15T08:30:00+00:00" })] } });
    const texts = [...document.querySelectorAll(".kt-text")].map((e) => e.textContent);
    expect(texts).toEqual(["SOL LONG triggered: price reached the entry zone.", "Your SOL LONG position is open at 145."]);
    cleanup();
    await open({ [T]: term({ positions: [], signals: {} }), [S]: { items: [sig({ status: "WAITING" })] } });
    expect(document.querySelectorAll(".kt-text").length).toBe(0);  // just the signal: no fake activity
    expect(screen.getAllByRole("button", { name: "ENTER TRADE" })[0]).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "SKIP" })[0]).toBeTruthy();
  });

  it("shows positions like an exchange: one row with the essentials, details on tap, CLOSE always there", async () => {
    await open({ [T]: term(), [S]: { items: [] } });
    const row = screen.getByLabelText("SOL LONG position");
    expect(row.textContent).toMatch(/SOL.*LONG 3x.*\$300\.00.*145.*150.*\+\$10\.00.*\+10\.34% ROE.*99\.5/);
    expect(screen.getByLabelText("Kiwi trading bot chat").textContent).toMatch(/LIVE · REAL MONEY/);  // a live account says so up top
    expect(row.textContent).not.toMatch(/Margin/);
    fireEvent.click(within(row).getByRole("button", { expanded: false }));
    expect(row.textContent).toMatch(/Margin\$96\.67/); expect(row.textContent).toMatch(/Contracts2 SOL/); expect(row.textContent).toMatch(/Funding\$0\.02/);
    expect(within(row).getByRole("button", { name: "Edit stop / take profit" })).toBeTruthy();
    const bal = screen.getByLabelText("My balance").textContent!;
    expect(bal).toMatch(/Account equity\$1,520\.50/); expect(bal).toMatch(/Available\$1,300\.00/); expect(bal).toMatch(/Unrealized P&L\+\$10\.00/);
  });

  it("CLOSE asks first, then sends one reduce-only close", async () => {
    await open({ [T]: term(), [S]: { items: [] },
                 "POST /api/members/trading/positions/5/close": { ...account(), close: { status: "CLOSED", filled: 2, avg_px: 150, remaining: 0, note: null } } });
    fireEvent.click(screen.getByRole("button", { name: "Close SOL LONG position" }));  // each row's CLOSE names its position for screen readers
    expect(calls.some((c) => c.key.includes("/close"))).toBe(false);
    expect(screen.getByRole("dialog", { name: "Close position" }).textContent).toMatch(/LIVE · real money/);
    fireEvent.click(screen.getByRole("button", { name: /Confirm: close 100%/ }));
    await flush();
    expect(calls.find((c) => c.key === "POST /api/members/trading/positions/5/close")!.body).toEqual({ fraction: 1 });
    expect(screen.getAllByRole("status").some((e) => /Closed ✓/.test(e.textContent ?? ""))).toBe(true);
  });

  it("CLOSE ALL sits by the heading and needs confirmation", async () => {
    await open({ [T]: term(), [S]: { items: [] },
                 "POST /api/members/trading/positions/close-all": { ...account(), close_all: { requested: 1, closed: [{ coin: "SOL" }], partial: [], failed: [] } } });
    fireEvent.click(screen.getByRole("button", { name: "CLOSE ALL" }));
    const go = screen.getByRole("button", { name: "Close all 1" }) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(go);
    await flush();
    expect(calls.find((c) => c.key.endsWith("/close-all"))!.body).toEqual({ confirm_count: 1 });
  });

  it("ENTER TRADE shows a simple confirmation; nothing is sent until CONFIRM TRADE", async () => {
    await open({ [T]: term({ positions: [], signals: {} }), [S]: { items: [sig()] },
                 "POST /api/members/trading/preview": { token: "tok12345", mode: "LIVE", text: "", plan: { entry: 145, size: 0.69, notional: 100, leverage: 3, margin: 33.3,
                                                         stop: 140, tp1: 155, tp2: 160, max_loss_usd: 4.9 } },
                 "POST /api/members/trading/confirm": { position: { id: 6, coin: "SOL", side: "LONG", mode: "LIVE", status: "OPEN", error: null } } });
    fireEvent.click(screen.getAllByRole("button", { name: "ENTER TRADE" })[0]);
    await flush();
    const dlg = screen.getByRole("dialog", { name: "Confirm trade" }).textContent!;
    expect(dlg).toMatch(/SOL LONG/); expect(dlg).toMatch(/Position size\$100\.00 \(0\.69 SOL\)/); expect(dlg).toMatch(/Leverage3x/); expect(dlg).toMatch(/Stop loss\$140/);
    expect(dlg).toMatch(/LIVE · REAL MONEY/); expect(dlg).toMatch(/Entry \(market\)≈ \$145/); expect(dlg).toMatch(/Margin used\$33\.30/);
    expect(dlg).toMatch(/Estimated maximum loss: \$4\.90, excluding possible slippage/);
    expect(calls.some((c) => c.key.endsWith("/confirm"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: /CONFIRM TRADE · REAL MONEY/ }));
    await flush();
    expect(calls.find((c) => c.key.endsWith("/confirm"))!.body).toEqual({ token: "tok12345" });
  });

  it("paper mode: a clearly labelled practice balance, ENTER TRADE previews a practice trade, and connecting stays one tap away", async () => {
    await open({ [T]: paper(), [S]: { items: [sig()] }, "GET /api/members/trading/connection": conn0(),
                 "POST /api/members/trading/preview": { token: "tok99999", mode: "PAPER", text: "", plan: { entry: 145, size: 0.69, notional: 100, leverage: 3,
                                                         margin: 33.3, stop: 140, tp1: 155, tp2: 160, max_loss_usd: 4.9 } } });
    expect(screen.queryByLabelText("My balance")).toBeNull();  // never shown as an exchange balance
    expect(screen.getByLabelText("Practice balance").textContent).toMatch(/Practice balance \(simulated\)\$1,000\.00.*not real money/);
    expect(screen.getByRole("button", { name: "CONNECT HYPERLIQUID" })).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "ENTER TRADE" })[0]);
    await flush();
    const dlg = screen.getByRole("dialog", { name: "Confirm trade" }).textContent!;
    expect(dlg).toMatch(/PRACTICE · SIMULATED/); expect(dlg).not.toMatch(/REAL MONEY/);
  });

  it("AUTO ON shows a small badge with a one-tap pause that only stops new entries", async () => {
    await open({ [T]: term(), [S]: { items: [] }, "PUT /api/members/trading/settings": account({ auto: { enabled: false, until: null } }) });
    const sw = screen.getByRole("switch", { name: /AUTO ON/ });
    expect(sw.getAttribute("aria-checked")).toBe("true");
    fireEvent.click(sw);  // one tap: off
    await flush();
    expect(calls.find((c) => c.key === "PUT /api/members/trading/settings")!.body).toEqual({ auto_enabled: false });
    expect(calls.some((c) => c.key.includes("/close"))).toBe(false);
  });

  it("when a refresh fails, the numbers still on screen say how old they are", async () => {
    let n = 0;
    await open({ [T]: () => (n++ === 0 ? term() : new Response("down", { status: 502 })), [S]: { items: [] },
                 "PUT /api/members/trading/settings": account({ auto: { enabled: false, until: null } }) });
    fireEvent.click(screen.getByRole("switch", { name: /AUTO ON/ }));  // any action re-reads the terminal
    await flush();
    const alert = screen.getAllByRole("alert").find((e) => /may be out of date/.test(e.textContent ?? ""));
    expect(alert?.textContent).toMatch(/Balances and positions below are from \d/);
    expect(screen.getByLabelText("SOL LONG position")).toBeTruthy();  // still shown, never blanked
  });

  it("finished signals say so: CLOSED with the real P&L, or EXPIRED", async () => {
    const hist = { id: 5, signal_id: 9, mode: "LIVE" as const, coin: "SOL", side: "LONG", entry_px: 145, exit_px: 155, size: 2, size_usd: 300, leverage: 3,
                   net_pnl: 19.4, fees: 0.3, funding: 0, duration_s: 3600, reason: "tp2", closed_at: "2026-10-15T12:00:00+00:00" };
    await open({ [T]: term({ positions: [], history: [hist], signals: { 9: { status: "POSITION_CLOSED", position_id: 5, mode: "LIVE" } } }),
                 [S]: { items: [sig({ status: "TARGET_HIT", status_label: "TARGET HIT" }), sig({ id: 10, number: 5, coin: "ETH", status: "EXPIRED", status_label: "EXPIRED" })] } });
    expect(screen.getAllByLabelText("Signal 004 SOL LONG")[0].textContent).toMatch(/CLOSED · \+\$19\.40/);
    expect(screen.getAllByLabelText("Signal 005 ETH LONG")[0].textContent).toMatch(/EXPIRED/);
    expect(screen.getByText("No open positions.")).toBeTruthy();
    expect(document.querySelector(".kt-hist")!.textContent).toMatch(/HISTORY.*1 closed/);
  });
});

const conn0 = (o: Record<string, unknown> = {}) => ({
  eligible: false, blocker: "Hyperliquid doesn't show the Price Chat referral (BANTER) on this wallet.", rank: { ok: true, required_level: 3, level: 4 },
  wallet: { address: "0x1234567890abcdef1234567890abcdef12345678" },
  referral: { status: "NOT_VERIFIED", code: "BANTER", code_seen: null, url: "https://app.hyperliquid.xyz/join/BANTER", checked_at: "2026-10-16T01:00:00+00:00" },
  deposit: { status: "verified", usd: 25, min_usd: 10 },
  authorization: { status: "NONE", agent: null, approved_at: null, valid_until: null, revoke_url: "https://app.hyperliquid.xyz/API" },
  live: { enabled: false, mode: "PAPER" }, auto: false, connected: false,
  permissions: ["Place, cancel and change orders on your Hyperliquid account", "Price Chat never sends withdrawals or transfers with it. You can revoke it on Hyperliquid at any time"], ...o,
});
const paper = () => term({ overview: { mode: "PAPER", auto: false, auto_until: null, paused: false, realized_today: 0, simulated: true, equity: 1000 },
  positions: [], signals: {}, account: account({ mode: "PAPER", live: { allowed: false, enabled: false, checks: {} }, auto: { enabled: false, until: null } }) });
const C = "GET /api/members/trading/connection";

describe("Connect Hyperliquid", () => {
  it("connects with an API key made on Hyperliquid: no wallet connection, the key is sent once and cleared", async () => {
    await open({ [T]: paper(), [S]: { items: [] }, [C]: conn0({ wallet: null, referral: { ...conn0().referral, status: "PENDING" } }),
                 "POST /api/members/trading/connection/api-key": conn0({ balances: { perps: 0, spot_usdc: 9.99 },
                   authorization: { ...conn0().authorization, status: "ACTIVE", agent: "0x9999999999999999999999999999999999990000" } }) });
    fireEvent.click(screen.getByRole("button", { name: "CONNECT HYPERLIQUID" }));
    await flush();
    const dlg = screen.getByRole("dialog", { name: "Connect Hyperliquid" });
    expect(dlg.textContent).not.toMatch(/Connect wallet|sign a free message/);
    expect(within(dlg).getByRole("link", { name: /Open the API page/ }).getAttribute("href")).toBe("https://app.hyperliquid.xyz/API");
    expect(dlg.textContent).toMatch(/never your main wallet's private key or seed phrase/); expect(dlg.textContent).toMatch(/keeps you in paper trading/);
    const go = within(dlg).getByRole("button", { name: "CONNECT" }) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    expect(within(dlg).queryByLabelText("Your Hyperliquid address")).toBeNull();  // only the key: the account comes from Hyperliquid
    const key = within(dlg).getByLabelText("API wallet private key") as HTMLInputElement;
    expect(key.type).toBe("password");
    fireEvent.change(key, { target: { value: "ab".repeat(32) } });
    expect(go.disabled).toBe(false);  // connecting needs no consent: it stays in paper (live is a separate, confirmed step)
    expect(within(dlg).queryByRole("checkbox")).toBeNull();
    fireEvent.click(go);
    await flush();
    const sent = calls.find((c) => c.key === "POST /api/members/trading/connection/api-key")!.body as Record<string, unknown>;
    expect(sent).toEqual({ secret: "ab".repeat(32), consent: false, replace: false });  // connects in paper
    expect(calls.some((c) => c.key.includes("/wallet/") || c.key.includes("/agent/"))).toBe(false);
    expect(dlg.textContent).toMatch(/API keyActive/); expect(dlg.textContent).toMatch(/NOT VERIFIED/);
    expect(dlg.textContent).toMatch(/Your Hyperliquid account0x1234…5678/);
    expect(dlg.textContent).toMatch(/BalancePerps \$0\.00 · Spot \$9\.99/); expect(dlg.textContent).toMatch(/move USDC\s+from Spot to Perps/);
  });

  it("referral not detected: says so, links our referral and retries against Hyperliquid's record", async () => {
    await open({ [T]: paper(), [S]: { items: [] }, [C]: conn0({ authorization: { ...conn0().authorization, status: "ACTIVE", agent: "0x99" } }),
                 "POST /api/members/trading/connection/recheck": conn0({ eligible: true, blocker: null, referral: { ...conn0().referral, status: "VERIFIED", code_seen: "BANTER" },
                                                                        authorization: { ...conn0().authorization, status: "ACTIVE", agent: "0x99" } }) });
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "FINISH CONNECTING" }));
    await flush();
    const dlg = screen.getByRole("dialog", { name: "Connect Hyperliquid" });
    expect(dlg.textContent).toMatch(/NOT VERIFIED/); expect(dlg.textContent).toMatch(/Opening the link alone doesn't count/);
    expect(within(dlg).getByRole("link", { name: /Open Hyperliquid/ }).getAttribute("href")).toBe("/api/r/site_earn?src=web");
    fireEvent.click(within(dlg).getByRole("button", { name: "RETRY VERIFICATION" }));
    await flush();
    expect(calls.some((c) => c.key === "POST /api/members/trading/connection/recheck")).toBe(true);
    expect(within(dlg).getByRole("button", { name: "ENABLE LIVE TRADING" })).toBeTruthy();  // eligible now: live is one more explicit step
    expect(dlg.textContent).toMatch(/● Connected · paper trading/);
  });

  it("an account referred by someone else is explained, never promised a switch", async () => {
    await open({ [T]: paper(), [S]: { items: [] }, [C]: conn0({ referral: { ...conn0().referral, status: "EXISTING_ACCOUNT", code_seen: "OTHERCODE" } }) });
    fireEvent.click(screen.getByRole("button", { name: "CONNECT HYPERLIQUID" }));
    await flush();
    const t = screen.getByRole("dialog", { name: "Connect Hyperliquid" }).textContent!;
    expect(t).toMatch(/EXISTING ACCOUNT/); expect(t).toMatch(/already linked to another referral \(OTHERCODE\)/); expect(t).toMatch(/doesn't allow changing/);
  });

  it("shows KIWI IS CONNECTED only when the server says every step is verified, with AUTO off", async () => {
    await open({ [T]: paper(), [S]: { items: [] }, [C]: conn0({ eligible: true, connected: true, referral: { ...conn0().referral, status: "VERIFIED" },
      authorization: { status: "ACTIVE", agent: "0xabcdef0000000000000000000000000000001234", approved_at: "2026-10-16T02:00:00+00:00", valid_until: null, revoke_url: "" },
      live: { enabled: true, mode: "LIVE" } }) });
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "ENABLE LIVE TRADING" }));
    await flush();
    const t = screen.getByRole("dialog", { name: "Connect Hyperliquid" }).textContent!;
    expect(t).toMatch(/KIWI IS CONNECTED/); expect(t).toMatch(/ReferralVerified/); expect(t).toMatch(/Trading authorizationActive/); expect(t).toMatch(/Kiwi's trading key0xabcd…1234/); expect(t).toMatch(/never holds\s+money/);
    expect(t).toMatch(/Auto tradingOFF/);
    expect(screen.getByRole("button", { name: "OPEN TRADING TERMINAL" })).toBeTruthy();
  });

  it("a revoked permission blocks ENTER TRADE and says how to fix it; positions keep their stop-loss", async () => {
    await open({ [T]: term({ positions: [], signals: {} }), [S]: { items: [sig()] },
                 [C]: conn0({ eligible: true, authorization: { ...conn0().authorization, status: "REVOKED" }, live: { enabled: true, mode: "LIVE" } }) });
    await flush();
    expect(screen.getByRole("alert").textContent).toMatch(/isn't active on Hyperliquid.*new trades are blocked.*keep their stop-loss/s);
    expect((screen.getAllByRole("button", { name: "ENTER TRADE" })[0] as HTMLButtonElement).disabled).toBe(true);
  });

  it("a connected key that isn't live yet says exactly what's missing, never just 'connect' again", async () => {
    await open({ [T]: paper(), [S]: { items: [] }, [C]: conn0({ blocker: "Hyperliquid shows $9.99 put into this account; $10 is the minimum.",
      referral: { ...conn0().referral, status: "VERIFIED" }, authorization: { ...conn0().authorization, status: "ACTIVE", agent: "0x99" } }) });
    await flush();
    expect(screen.getByText(/API key connected ✓ · Not live yet: Hyperliquid shows \$9\.99/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "FINISH CONNECTING" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "CONNECT HYPERLIQUID" })).toBeNull();
  });

  it("an empty live account opens with $0 and a DEPOSIT button showing the member's own address (never the trading key's), USDC only", async () => {
    const live = conn0({ eligible: true, connected: true, referral: { ...conn0().referral, status: "VERIFIED" }, balances: { perps: 0, spot_usdc: 9.99 },
      authorization: { status: "ACTIVE", agent: "0x1c21d8bb4ae7d5ebace054733041be50d4512c11", approved_at: null, valid_until: null, revoke_url: "" },
      live: { enabled: true, mode: "LIVE" } });
    await open({ [T]: term({ overview: { ...term().overview, equity: 0, available: 0, unrealized: 0 }, positions: [], signals: {} }), [S]: { items: [] }, [C]: live });
    await flush();
    expect(screen.getByLabelText("My balance").textContent).toMatch(/Account equity\$0\.00/);
    expect(screen.getByText("Move USDC from Spot to Perps to start trading")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "DEPOSIT" }));
    await flush();
    const t = screen.getByRole("dialog", { name: "Deposit USDC" }).textContent!;
    expect(t).toContain("0x1234567890abcdef1234567890abcdef12345678");  // the member's Hyperliquid account
    expect(t).not.toContain("0x1c21d8bb4ae7d5ebace054733041be50d4512c11");  // never the trading key
    expect(t).toMatch(/USDC only/); expect(t).toMatch(/Kiwi trades from Perps/);
  });

  it("a unified account shows its USDC as one trading balance, with no 'move to Perps' advice", async () => {
    await open({ [T]: paper(), [S]: { items: [] }, [C]: conn0({ balances: { perps: 0, spot_usdc: 11.5, unified: true, trading: 11.5 },
      referral: { ...conn0().referral, status: "VERIFIED" }, authorization: { ...conn0().authorization, status: "ACTIVE", agent: "0x99" } }) });
    fireEvent.click(screen.getByRole("button", { name: "FINISH CONNECTING" }));
    await flush();
    const t = screen.getByRole("dialog", { name: "Connect Hyperliquid" }).textContent!;
    expect(t).toMatch(/Balance\$11\.50 USDC \(unified account\)/);
    expect(t).not.toMatch(/from Spot to Perps/);
  });

  it("pins the newest signal above the chat and shows the community's messages next to Kiwi's", async () => {
    await open({ [T]: term({ positions: [], signals: {} }),
                 [S]: { items: [sig(), sig({ id: 12, number: 7, coin: "BTC", side: "SHORT", published_at: "2026-10-15T09:00:00+00:00", entry: [82000, 82300], stop: 83000, target: 80800 })] },
                 "GET /api/website/chat": { enabled: true, link: "https://t.me/thepricechat", messages: [
                   { id: 1, name: "Ana", text: "nice entry on SOL", at: "2026-10-15T08:30:00+00:00" },
                   { id: 2, name: "Ben", text: "BTC looks heavy", at: "2026-10-15T09:10:00+00:00" }] } });
    const pin = screen.getByLabelText("Pinned signal");
    expect(pin.textContent).toMatch(/📌 PINNED SIGNAL/);
    expect(within(pin).getByLabelText("Signal 007 BTC SHORT").textContent).toMatch(/Entry\$82,000–\$82,300.*Stop Loss\$83,000/);  // the full card, newest first
    expect(within(pin).getByRole("button", { name: "ENTER TRADE" })).toBeTruthy();
    const members = [...document.querySelectorAll(".kt-msg.is-member")].map((e) => e.textContent);
    expect(members[0]).toMatch(/Ana.*nice entry on SOL/); expect(members[1]).toMatch(/Ben.*BTC looks heavy/);
    expect(screen.getByRole("link", { name: /Chat with the community in Telegram/ }).getAttribute("href")).toBe("https://t.me/thepricechat");
  });

  it("there's always one obvious place to add or replace the Hyperliquid API key", async () => {
    await open({ [T]: paper(), [S]: { items: [] }, [C]: conn0({ wallet: null }) });
    fireEvent.click(screen.getByRole("button", { name: /Add Hyperliquid API/ }));
    await flush();
    expect(within(screen.getByRole("dialog", { name: "Connect Hyperliquid" })).getByLabelText("API wallet private key")).toBeTruthy();
  });

  it("AUTO toggle next to the API button: turning it on asks once (duration, limits, real money), then follows every signal", async () => {
    await open({ [T]: term({ overview: { ...term().overview, auto: false }, account: account({ auto: { enabled: false, until: null } }) }), [S]: { items: [] },
                 "PUT /api/members/trading/settings": account() });
    const sw = screen.getByRole("switch", { name: /AUTO OFF/ });
    fireEvent.click(sw);
    const dlg = screen.getByRole("dialog", { name: "Turn AUTO on" });
    expect(dlg.textContent).toMatch(/enters every new signal for you/); expect(dlg.textContent).toMatch(/real-money trades/);
    expect(calls.some((c) => c.key.startsWith("PUT"))).toBe(false);  // nothing until confirmed
    fireEvent.change(within(dlg).getByLabelText("AUTO duration"), { target: { value: "72" } });
    fireEvent.click(within(dlg).getByRole("button", { name: "TURN AUTO ON" }));
    await flush();
    expect(calls.find((c) => c.key === "PUT /api/members/trading/settings")!.body).toEqual({ auto_enabled: true, auto_hours: 72 });
    // longer runs: 30 / 90 / 180 days, or until the member turns it off
    const opts = [...within(dlg).getByLabelText("AUTO duration").querySelectorAll("option")].map((o) => o.textContent);
    expect(opts).toEqual(expect.arrayContaining(["30 days", "90 days", "180 days", "Until I turn it off"]));
  });

  it("turning AUTO on from ⚙ settings goes through the same real-money confirmation, never straight to the server", async () => {
    await open({ [T]: term({ overview: { ...term().overview, auto: false }, account: account({ auto: { enabled: false, until: null } }) }), [S]: { items: [] },
                 "PUT /api/members/trading/settings": account() });
    fireEvent.click(screen.getByRole("button", { name: "Trading settings" }));
    const set = screen.getByRole("dialog", { name: "Trading settings" });
    fireEvent.change(within(set).getByLabelText("AUTO duration"), { target: { value: "4" } });
    fireEvent.click(within(set).getByRole("button", { name: "Turn AUTO on…" }));
    expect(calls.some((c) => c.key.startsWith("PUT"))).toBe(false);
    const dlg = screen.getByRole("dialog", { name: "Turn AUTO on" });
    expect(dlg.textContent).toMatch(/real-money trades/);
    expect((within(dlg).getByLabelText("AUTO duration") as HTMLSelectElement).value).toBe("4");  // the chosen duration carries over
    fireEvent.click(within(dlg).getByRole("button", { name: "TURN AUTO ON" }));
    await flush();
    expect(calls.find((c) => c.key === "PUT /api/members/trading/settings")!.body).toEqual({ auto_enabled: true, auto_hours: 4 });
  });
});

