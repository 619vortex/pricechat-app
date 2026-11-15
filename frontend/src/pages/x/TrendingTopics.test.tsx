import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TrendingTopics, type Trending, type TrendPost, type Topic } from "./TrendingTopics";

/** TEST FIXTURES: made-up topics and tweets. */
const POST: TrendPost = { id: 7, topic_id: 3, category: "crypto", status: "pending", text: "Exchange Bybaz halts withdrawals after $40 million hack (via CoinDesk)\n\nSearches for Bybaz have topped 200K on Google in the US.",
  edited: false, angle: "interest", writer: "rules", score: 81, sources: [{ name: "CoinDesk", url: "https://coindesk.test/1", title: "Exchange Bybaz halts withdrawals" }], checks: [],
  hold_reason: null, note: null, error: null, x_id: null, url: null, metrics: {}, created_at: "2026-11-14T10:00:00Z", published_at: null, expires_at: null, cashtags: [] };
const HELD: TrendPost = { ...POST, id: 8, status: "held", text: "(no tweet: bandy)", sources: [], hold_reason: "No reliable source backs this topic yet (only social signals): held until an outlet reports it" };
const DONE: TrendPost = { ...POST, id: 9, status: "published", x_id: "123", url: "https://x.com/i/status/123", metrics: { impression_count: 1200, like_count: 40, retweet_count: 6, reply_count: 3 } };
const TOPIC: Topic = { id: 3, title: "Exchange Bybaz halts withdrawals after $40 million hack", category: "crypto", status: "active", score: 81, peak_score: 81, first_seen: "2026-11-14T09:40:00Z",
  last_seen: "2026-11-14T10:00:00Z", parts: { velocity: 22, engagement: 8, freshness: 25, impact: 15, surprise: 5, relevance: 10, learned: 1, why: "81: fresh (0.5 h since the first report); momentum: +2 outlets",
    timeline: { event_time: "2026-11-14T09:30:00Z", first_seen: "2026-11-14T09:40:00Z", last_update: "2026-11-14T09:55:00Z", source_count: 3, outlets: 2, outlets_1h: 2,
      items_per_hour: 4, developing: false, new_outlets_2h: 2 },
    latency: { post_id: 9, status: "published", at: "2026-11-14T10:00:00Z", event_to_publish_min: 30, detect_to_publish_min: 20, written_to_publish_min: 0 }, sources: ["news", "google"], outlets: ["CoinDesk", "Reuters"],
    growth: { evidence: ["scans", "google"], notes: ["+2 outlets, +3 items in 60 min", "Google 20K+ -> 200K+"], measured: true } },
  sources: [{ source: "news", outlet: "CoinDesk", title: "Exchange Bybaz halts withdrawals", url: "https://coindesk.test/1", reliable: true, engagement: 2, at: null },
    { source: "google", outlet: "Google Trends", title: "bybaz", url: "", reliable: false, engagement: 200000, at: null, extra: { approx_traffic: "200K+" } }],
  metrics: { signals: 4, google: 200000 }, posted_at: null, post: POST };

const state = (over: Partial<Trending["settings"]> = {}, top: Partial<Trending> = {}): Trending => ({
  settings: { enabled: true, kill_switch: false, mode: "automatic", automatic: true, auto_confirmed_at: "2026-11-14T00:00:00Z", live: true, hot_score: 75, suggest_score: 45, max_per_day: 3, gap_minutes: 180,
    topic_cooldown_hours: 24, newsdesk_gap_minutes: 30, shared_daily_cap: 20, categories: { crypto: true, stocks: true, markets: true, geopolitics: true, technology: true, world: true },
    sources: { news: { on: true }, google: { on: true }, reddit: { on: true, public_json: false }, x_trends: { on: false }, x_accounts: { on: false } }, ...over },
  last_run: { at: "2026-11-14T10:00:00Z" },
  health: { news: { name: "News Desk feeds", status: "ok", problem: null, items: 300, last_scan: "2026-11-14T10:00:00Z" },
    google: { name: "Google Trends", status: "ok", problem: null, items: 78, last_scan: "2026-11-14T10:00:00Z" },
    reddit: { name: "Reddit", status: "unavailable", problem: "needs setup: add REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET", items: 0 },
    x_trends: { name: "X trends", status: "unavailable", plan: true, problem: "not available on your X plan (X answered 403)", items: 0 } },
  usage: { requests: { google: 2 }, cost_usd: {} }, topics: [TOPIC], queue: [POST], held: [HELD], published: [DONE],
  coordination: { posts_24h: { newsdesk: 5, trending: 1, total: 6 }, shared_cap: 20, next_newsdesk_slot: "2026-11-14T12:00:00Z", desk_blocked: null, spacing: null, cap: null, newsdesk_x_mode: "dry_run", x_live: false },
  learning: { measured_posts: 1, learning: false, needs: 5, weights: {}, totals: { impression_count: 1200, like_count: 40, retweet_count: 6, reply_count: 3 }, bounds: [0.8, 1.2],
    per_category: { crypto: { posts: 1, impressions: 1200, likes: 40, reposts: 6, replies: 3, rate: 0.0508 } } },
  writer: "rules", x: { configured: true, can_write: true, status: "connected", last_ok: null, last_error: null }, cost: { per_post_usd: 0.015, x_trends_per_request_usd: 0.01, x_post_read_usd: 0.005 }, ...top,
});

let calls: string[] = [];
function serve(data: Trending) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input} ${init?.body ?? ""}`.trim());
    return new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 20)); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Trending Topics", () => {
  it("shows the dashboard: topics with scores and sources, health, suggested and held tweets, performance", async () => {
    serve(state());
    render(<TrendingTopics />);
    await settle();
    expect(screen.getByText("Automatic: hot topics publish by themselves")).toBeTruthy();
    expect(screen.getByText("Dry run: the News Desk's X lane is in dry run")).toBeTruthy();
    expect(screen.getAllByText("Exchange Bybaz halts withdrawals after $40 million hack").length).toBeGreaterThan(0);
    expect(screen.getByText("not on your X plan")).toBeTruthy();
    expect(document.body.textContent).toMatch(/Google 200K\+ searches/);
    expect(document.body.textContent).toMatch(/Held: No reliable source/);
    expect(document.body.textContent).toMatch(/1200 views · 40 likes · 6 reposts · 3 replies/);
    expect(document.body.textContent).toMatch(/6 \/ 20/);
    expect(screen.getByText("×1")).toBeTruthy();
  });

  it("shows the measured growth and the sensitivity of each topic", async () => {
    const flat: Topic = { ...TOPIC, id: 4, title: "Senator accused of taking bribes, prosecutors say", score: 58, post: null,
      parts: { ...TOPIC.parts, growth: { evidence: [], notes: [], measured: false, capped: true }, sensitive: { kind: "allegation", label: "allegations about a named person", tier: "approval" } } };
    serve(state({}, { topics: [TOPIC, flat] }));
    render(<TrendingTopics />);
    await settle();
    expect(document.body.textContent).toMatch(/Growth: more outlets across scans, Google Trends · \+2 outlets, \+3 items in 60 min; Google 20K\+ -> 200K\+/);
    expect(document.body.textContent).toMatch(/No measured growth \(capped below the automatic line\): approval only/);
    expect(screen.getByText(/sensitive: approval only · allegations about a named person/)).toBeTruthy();
  });

  it("shows each story's timeline, why it was selected and how fast it went out", async () => {
    serve(state({}, { topics: [TOPIC], latency: { event_to_publish: { median: 30, max: 95, n: 4 }, detect_to_publish: { median: 20, max: 60, n: 4 },
      written_to_publish: { median: 0, max: 40, n: 4 }, recent: [TOPIC.parts.latency!] } }));
    render(<TrendingTopics />);
    await settle();
    const text = document.body.textContent ?? "";
    expect(text).toMatch(/3 sources · 4\/h · 2 outlets in the last hour/);
    expect(text).toMatch(/event .* · first seen .* · last update/);
    expect(text).toMatch(/Why: 81: fresh \(0.5 h since the first report\); momentum: \+2 outlets/);
    expect(text).toMatch(/posted: event → sent 30 min, first seen → sent 20 min/);
    expect(text).toMatch(/Speed: detection to publication/);
    expect(text).toMatch(/1.6 h/);  // worst event -> sent, 95 min
    expect(text).toMatch(/Market impact/);
  });

  it("asks before enabling automatic publishing again, and sends the confirmation only after a yes", async () => {
    serve(state({ mode: "approval", automatic: false, auto_confirmed_at: null }));
    const ask = vi.fn(() => false);
    vi.stubGlobal("confirm", ask);
    render(<TrendingTopics />);
    await settle();
    expect(screen.getByText("Approval mode: nothing goes out without you")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Enable automatic publishing" }));
    expect(ask).toHaveBeenCalled();
    expect(calls.some((c) => c.includes('"mode":"automatic"'))).toBe(false);
    ask.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Enable automatic publishing" }));
    await settle();
    expect(calls.some((c) => c.includes('"mode":"automatic","confirm_automatic":true'))).toBe(true);
  });

  it("has its own stop button, and approve / reject on suggestions", async () => {
    serve(state());
    render(<TrendingTopics />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Stop trend posts" }));
    await settle();
    expect(calls.some((c) => c.startsWith("PUT /api/x/trending/settings") && c.includes('"kill_switch":true'))).toBe(true);
    fireEvent.click(screen.getAllByRole("button", { name: "Approve and post" })[0]);
    await settle();
    expect(calls.some((c) => c.startsWith("POST /api/x/trending/posts/7/approve"))).toBe(true);
  });

  it("says when a stop is on", async () => {
    serve(state({ kill_switch: true }, { coordination: { ...state().coordination, desk_blocked: "Trending Topics' stop is on" } }));
    render(<TrendingTopics />);
    await settle();
    expect(screen.getByText("Stopped: no trend post goes out")).toBeTruthy();
    expect(screen.getByText(/Posting waits: Trending Topics' stop is on/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Release stop" })).toBeTruthy();
  });
});
