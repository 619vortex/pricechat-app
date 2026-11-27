import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { locate } from "../../components/nav";
import { OriginalsPage, type OriginalDraft, type OriginalsList, type TopPick } from "./OriginalsPage";
import type { PerfReport, PerfSummary } from "./OriginalsPerformance";

/** TEST FIXTURES: made-up findings and figures. */
const draft = (over: Partial<OriginalDraft>): OriginalDraft => ({
  id: 7, finding_key: "rarity:BTC:30d:2026-11-24", format: "extremes", detector: "rarity", status: "pending",
  text: "Fixturecoin −40.0% in 30 days.\nIn Hyperliquid data since March 2023, only 1.1% of all 30-day stretches were this bad.", chars: 120,
  original_text: "same", writer: "template", score: 91.2, as_of: "2026-11-24", headline: "Fixturecoin −40.0% in 30 days", window: "30d",
  calculation: "30d return = 60 / 100 − 1 = −40.0%", sources: [{ name: "hyperliquid", label: "Hyperliquid", attribution: "Data: Hyperliquid", terms_url: "", commercial_ok: true }],
  attribution: ["Data: Hyperliquid"], rarity: 0.95, novelty: 1, notes: [], chart: { kind: "histogram_marker", title: "Every 30-day move" }, version: 1,
  created_at: new Date().toISOString(), decided_at: null, decided_by: "", reject_reason: "", writer_pending: false, gate_problems: [], ...over,
});

const summary = (over: Partial<PerfSummary>): PerfSummary => ({
  posts: 0, measured_24h: 0, median_impressions_24h: null, median_engagement_rate_24h: null, engagement_rate_24h: null, reposts: 0, bookmarks: 0,
  replies: 0, likes: 0, quotes: 0, profile_visits: 0, impressions: 0, followers_est_24h: null, followers_per_1k_impressions: null, ...over,
});
const perf = (over: Partial<PerfReport> = {}): PerfReport => ({
  days: 30, as_of: "2026-11-27T12:00:00+00:00", overall: summary({ posts: 10 }), formats: [], winners: [], pictures: {}, originals: [], top_posts: [],
  groups: { original: summary({ label: "Originals" }), news: summary({ label: "News Desk", posts: 9, measured_24h: 4, median_impressions_24h: 23, engagement_rate_24h: 0.004 }),
    trend: summary({ label: "Trending Topics" }), other: summary({ label: "Other posts" }) },
  followers_now: 57, notes: ["No Original has been posted yet."], originals_vs_news: { enough_data: false },
  method: { min_posts_for_a_winner: 3, ranking: "lift", followers: "estimate", cost: "no X reads" }, ...over,
});

let calls: string[] = [];
function serve(drafts: OriginalDraft[], top: TopPick[] = [], report: PerfReport = perf()) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input}${init?.body ? " " + String(init.body) : ""}`);
    const body: OriginalsList = { drafts, counts: { pending: drafts.filter((d) => d.status === "pending").length, approved: 0, rejected: 0, regenerating: 0 },
      publishing: "manual", switches: { growth_enabled: false, growth_collect_enabled: false, intel_enabled: false }, top };
    if (String(input).includes("/originals/performance")) return new Response(JSON.stringify(report), { status: 200, headers: { "Content-Type": "application/json" } });
    if (String(input).includes("/generate")) return new Response(JSON.stringify({ drafted: 2, found: 5, errors: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
    return new Response(JSON.stringify(String(input).includes("/drafts/7/") ? drafts[0] : body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 20)); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Originals", () => {
  it("lives in Publishing next to X", () => {
    expect(locate("/x/originals").area.key).toBe("publishing");
    expect(locate("/x/originals").page.label).toBe("Originals");
  });

  it("shows the graphic, text, count, sources and calculation, and approve only calls approve", async () => {
    serve([draft({})]);
    render(<OriginalsPage />);
    await settle();
    expect(screen.getByTestId("tweet-text").textContent).toContain("−40.0% in 30 days");
    expect(screen.getByText("120/280")).toBeTruthy();
    const img = screen.getByRole("img") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("/api/originals/drafts/7/image.png?fmt=x&v=1");
    expect(screen.getByText(/Hyperliquid/, { selector: "div" })).toBeTruthy();
    expect(screen.getByText(/60 \/ 100 − 1/)).toBeTruthy();
    expect(screen.getByText(/Nothing here is posted automatically/)).toBeTruthy();
    fireEvent.click(screen.getByText("Approve"));
    await settle();
    expect(calls.some((c) => c.startsWith("POST /api/originals/drafts/7/approve"))).toBe(true);
    expect(calls.every((c) => !/twitter|x\.com|telegram/i.test(c))).toBe(true);
  });

  it("shows that the draft's data was re-read from its source", async () => {
    serve([draft({ verification: { ok: true, at: "2026-11-27T10:15:00+00:00", values_checked: 1369, problems: [],
      series: { "px:BTC:close": { source: "Hyperliquid candleSnapshot" } } } })]);
    render(<OriginalsPage />);
    await settle();
    expect(screen.getByText("Data verified")).toBeTruthy();
    expect(screen.getByTestId("verification").textContent).toContain("1,369 stored values re-read from Hyperliquid candleSnapshot at 2026-11-27 10:15 UTC; every one matched");
  });

  it("edits through the API and regenerates with or without a new chart", async () => {
    serve([draft({})]);
    render(<OriginalsPage />);
    await settle();
    fireEvent.click(screen.getByText("Edit"));
    fireEvent.change(screen.getByLabelText("Tweet text"), { target: { value: "Fixturecoin −40.0% in 30 days." } });
    fireEvent.click(screen.getByText("Save"));
    await settle();
    expect(calls.some((c) => c.startsWith("POST /api/originals/drafts/7/edit") && c.includes("Fixturecoin"))).toBe(true);
    fireEvent.click(screen.getByText("Other chart"));
    await settle();
    expect(calls.some((c) => c.includes("/regenerate") && c.includes('"chart_variant":true'))).toBe(true);
  });

  it("approved drafts offer copy text and image downloads", async () => {
    serve([draft({ status: "approved" })]);
    render(<OriginalsPage />);
    await settle();
    expect(screen.getByText("Copy text")).toBeTruthy();
    const dl = screen.getByText("Download image") as HTMLAnchorElement;
    expect(dl.getAttribute("href")).toContain("fmt=x");
    expect(dl.hasAttribute("download")).toBe(true);
    expect((screen.getByText("Portrait image") as HTMLAnchorElement).getAttribute("href")).toContain("fmt=portrait");
    expect(screen.queryByText("Approve")).toBeNull();
  });

  it("falls back to a note when the renderer isn't installed, and Generate now posts", async () => {
    serve([draft({})]);
    render(<OriginalsPage />);
    await settle();
    fireEvent.error(screen.getByRole("img"));
    expect(screen.getByText(/Graphic not available yet/)).toBeTruthy();
    fireEvent.click(screen.getByText("Generate now"));
    await settle();
    expect(calls.some((c) => c.startsWith("POST /api/originals/generate"))).toBe(true);
    expect(screen.getByText(/2 new drafts from 5 findings/)).toBeTruthy();
  });

  it("puts the editor's top picks first with their scores and the phrases every wording must keep", async () => {
    serve([draft({ id: 8, detector: "rarity" }), draft({ id: 7, detector: "weekly_streak", coverage: ["Hyperliquid", "February 2023"] })],
      [{ id: 7, rank: 1, editorial: { total: 20, shareability: 5, originality: 5, visual: 5, relevance: 5, measured: false } }]);
    render(<OriginalsPage />);
    await settle();
    expect(screen.getByTestId("top-picks").textContent).toContain("Top 1 for approval");
    expect(screen.getByText("Top 1")).toBeTruthy();
    expect(screen.getByTestId("editorial").textContent).toContain("Editor's score 20/20");
    expect(screen.getByTestId("coverage").textContent).toContain("Hyperliquid · February 2023");
    expect(screen.getByText("All other drafts")).toBeTruthy();
  });

  it("shows performance by lane, read from stored snapshots only", async () => {
    serve([draft({})]);
    render(<OriginalsPage />);
    await settle();
    expect(screen.getByTestId("performance")).toBeTruthy();
    expect(screen.getByTestId("group-news").textContent).toContain("23");
    expect(screen.getByTestId("group-news").textContent).toContain("4.0");  // 0.004 = 4.0 engagements per 1k impressions
    expect(screen.getByText("57 followers")).toBeTruthy();
    expect(screen.getByText("No Original has been posted yet.")).toBeTruthy();
    expect(calls.some((c) => c === "GET /api/originals/performance?days=30")).toBe(true);
  });

  it("an approved draft links to its X post and then shows its numbers", async () => {
    serve([draft({ status: "approved" })]);
    render(<OriginalsPage />);
    await settle();
    fireEvent.change(screen.getByLabelText("X post link"), { target: { value: "https://x.com/thepricechat/status/1234567890" } });
    fireEvent.click(screen.getByText("Link post"));
    await settle();
    expect(calls.some((c) => c.startsWith("POST /api/originals/drafts/7/posted") && c.includes("1234567890"))).toBe(true);
    cleanup();
    serve([draft({ status: "approved", posted: { x_id: "1234567890", url: "https://x.com/i/status/1234567890", at: "2026-11-27T13:00:00+00:00", how: "matched" },
      performance: { impressions: 410, likes: 9, replies: 2, reposts: 3, quotes: 0, bookmarks: 4, profile_visits: 5, url_clicks: null, engagement: 18, engagement_rate: 0.0439 } })]);
    render(<OriginalsPage />);
    await settle();
    expect(screen.getByTestId("posted").textContent).toContain("matched automatically by its text");
    expect(screen.getByTestId("draft-metrics").textContent).toContain("410 impressions");
    expect(screen.getByTestId("draft-metrics").textContent).toContain("43.9 engagements / 1k impressions");
  });

  it("shows gate problems and an empty state", async () => {
    serve([draft({ gate_problems: ["figure 9% isn't in the finding"] })]);
    render(<OriginalsPage />);
    await settle();
    expect(screen.getByText(/Figures gate: figure 9%/)).toBeTruthy();
    cleanup();
    serve([]);
    render(<OriginalsPage />);
    await settle();
    expect(screen.getByText(/No drafts to review/)).toBeTruthy();
  });
});
