import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NewsDesk, type Desk } from "./NewsDesk";

const POST = { id: 1, kind: "news", category: "economics", priority: "scheduled", status: "queued", text: "🏦 Fed holds rates\n\nvia BBC", edited: false, score: 4, sources: [{ name: "BBC", url: "https://b.test/1", at: null }], checks: [], error: null, url: null, metrics: {}, created_at: "2026-11-04T10:00:00Z", published_at: null, expires_at: null, facts: {} };
const desk = (over: Partial<Desk["settings"]> = {}, top: Partial<Desk> = {}): Desk => ({
  settings: { automation: true, mode: "dry_run", kill_switch: false, paused_until: null, pause_reason: null, target_per_day: 12, max_breaking_per_day: 6, breaking_gap_minutes: 20, sensitivity: "medium", categories: { crypto: true, stocks: true, commodities: true, politics: true, economics: true, viral: true }, feeds: [{ name: "BBC", url: "https://b.test/f", tier: 1, category: null }], watchlist: [{ symbol: "BTC-USD", name: "Bitcoin", group: "crypto_major", category: "crypto" }], ...over },
  blocked: null, last_run: { at: "2026-11-04T10:00:00Z" }, feed_problems: {}, x: { configured: true, can_write: true, status: "connected", account: "pricechat", blocked_until: null }, writer: "headlines",
  today: { published: 2, breaking: 1, target: 12 }, cost: { per_post_usd: 0.015, posted_30d: 0, estimate_30d_usd: 0, budget: { daily_usd: 5, monthly_usd: 30 }, x_spend_today_usd: 0, x_spend_month_usd: 1.5 }, queue: [POST], published: [], problems: [], ...top,
});

let calls: string[] = [];
function serve(state: Desk) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input} ${init?.body ?? ""}`.trim());
    return new Response(JSON.stringify(state), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 20)); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("News Desk", () => {
  it("says plainly that a dry run sends nothing, and shows the queue with sources", async () => {
    serve(desk());
    render(<NewsDesk />);
    await settle();
    expect(screen.getByText("Dry run: nothing goes to X")).toBeTruthy();
    expect(screen.getByRole("link", { name: "BBC" }).getAttribute("href")).toBe("https://b.test/1");
    expect(screen.getByText(/Attributed headlines|attributed headlines/)).toBeTruthy();
    expect(document.body.textContent).toMatch(/\$1\.50 \/ \$30/);
  });

  it("asks before going live, and won't without confirmation", async () => {
    serve(desk());
    const ask = vi.fn(() => false);
    vi.stubGlobal("confirm", ask);
    render(<NewsDesk />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Go live" }));
    expect(ask).toHaveBeenCalled();
    expect(calls.some((c) => c.includes('"mode":"live"'))).toBe(false);
    ask.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Go live" }));
    await settle();
    expect(calls.some((c) => c.includes('"mode":"live"'))).toBe(true);
  });

  it("can't go live without posting access", async () => {
    serve(desk({}, { x: { configured: true, can_write: false, status: "connected", account: null, blocked_until: null } }));
    render(<NewsDesk />);
    await settle();
    expect((screen.getByRole("button", { name: "Go live" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/without posting access/)).toBeTruthy();
  });

  it("has an emergency stop, and shows why it's stopped", async () => {
    serve(desk({ kill_switch: true }, { blocked: "Emergency stop is on" }));
    render(<NewsDesk />);
    await settle();
    expect(screen.getByRole("button", { name: "Release emergency stop" })).toBeTruthy();
    expect(screen.getByText("Emergency stop is on")).toBeTruthy();
  });

  it("switches a category off and rejects a queued post", async () => {
    serve(desk());
    render(<NewsDesk />);
    await settle();
    fireEvent.click(screen.getByLabelText("Politics"));
    await settle();
    expect(calls.some((c) => c.startsWith("PUT") && c.includes('"politics":false'))).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    await settle();
    expect(calls.some((c) => c.includes("POST") && c.includes("/x/newsdesk/posts/1/reject"))).toBe(true);
  });

  it("shows real engagement once X has reported it", async () => {
    serve(desk({}, { queue: [], published: [{ ...POST, status: "published", url: "https://x.com/i/status/9", metrics: { impression_count: 500, like_count: 7, retweet_count: 2, reply_count: 1 } }] }));
    render(<NewsDesk />);
    await settle();
    expect(screen.getByText(/500 views · 7 likes · 2 reposts · 1 replies/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open on X" })).toBeTruthy();
  });
});

describe("News Desk: Telegram", () => {
  it("shows what Telegram confirmed, and stops with one press", async () => {
    const lane = { enabled: true, max_per_day: 10, gap_minutes: 45, channel: "@Pricechatupdates", problem: null, paused_until: null, last_run: null,
      posts: [{ id: 1, status: "delivered", headline: "Samsung Wallet adds USDC", source: "Decrypt", message_id: 144, url: "https://t.me/Pricechatupdates/144", error: null, created_at: null, delivered_at: null },
        { id: 2, status: "uncertain", headline: "Another story", source: "SEC", message_id: null, url: null, error: "Telegram didn't answer", created_at: null, delivered_at: null }] };
    const puts: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
      if (init?.method === "PUT") puts.push(`${input} ${init.body}`);
      return new Response(JSON.stringify(input.includes("/telegram") ? lane : desk()), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    render(<NewsDesk />);
    await settle();
    expect(screen.getByRole("link", { name: "Samsung Wallet adds USDC" }).getAttribute("href")).toBe("https://t.me/Pricechatupdates/144");
    expect(screen.getByText("Message 144")).toBeTruthy();
    expect(screen.getByText("Check the channel")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Stop posting to Telegram" }));
    await settle();
    expect(puts[0]).toMatch(/\/x\/newsdesk\/telegram .*"enabled":false/);
  });
});

describe("News Desk: X publishing", () => {
  it("shows X's last error and only sends the test post after a preview and a confirmation", async () => {
    const lane = { mode: "live", live: true, paused: null, last_ok: null,
      last_error: { at: "2026-11-10T04:15:02Z", post_id: 7, event_id: 3, outcome: "failed", stage: "post", message: "PermissionError at the post: Write blocked: this permit was already used" },
      events: [{ id: 3, headline: "Oil falls 6%", released_at: null, telegram: "delivered", telegram_message_id: 154, x: "failed", note: "permit was already used", post_id: 7, x_id: null, url: null, post_status: "failed" }] };
    const preview = { event_id: 3, headline: "Oil falls 6%", text: "OIL (WTI) DOWN 6.0%\n\nSource: Yahoo Finance", ok: true, reason: "", x_status: "failed", image: "data:image/png;base64,AA==" };
    const posts: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
      if (init?.method === "POST") posts.push(`${input} ${init.body}`);
      const out = input.includes("/test-post") ? (init?.method === "POST" ? { ...POST, status: "published", url: "https://x.com/i/status/42" } : preview) : input.includes("/telegram") ? { posts: [] } : desk({ mode: "live" }, { x_lane: lane });
      return new Response(JSON.stringify(out), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    const ask = vi.fn(() => false);
    vi.stubGlobal("confirm", ask);
    render(<NewsDesk />);
    await settle();
    expect(screen.getByText(/Last X error .*permit was already used/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Preview a test post" }));
    await settle();
    expect(screen.getByAltText("The picture the test post would carry")).toBeTruthy();
    expect(posts).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Send this test post to X" }));
    await settle();
    expect(ask).toHaveBeenCalled();
    expect(posts).toHaveLength(0);
    ask.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Send this test post to X" }));
    await settle();
    expect(posts).toHaveLength(1);
    expect(posts[0]).toMatch(/\/x\/newsdesk\/test-post .*"event_id":3,"confirm":true/);
    expect(screen.getByRole("link", { name: "open on X" }).getAttribute("href")).toBe("https://x.com/i/status/42");
  });
});
