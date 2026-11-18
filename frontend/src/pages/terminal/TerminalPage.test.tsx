import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TerminalPage, type Story } from "./TerminalPage";

/** TEST FIXTURES: made-up stories. */
const STORY: Story = { id: 5, title: "Russia launches missile strikes on Kyiv, killing 25", category: "geopolitics", topic_status: "active", lane: "breaking", trend_score: 58, peak_score: 58,
  viral: { score: 81, parts: { shock: 10, urgency: 10, social: 4 }, drivers: ["shock", "urgency"], measured: ["3 outlets published in the last hour"] },
  evidence: { score: 88, level: "confirmed", rumor: false, reasons: ["3 independent reliable outlets (reuters, ap, bbc)"], outlets: 3, official: false, mainstream: true },
  rumor: false, ahead_of_mainstream: false, fast: true, fast_why: "hard geopolitical event; evidence confirmed (88); 12 min since first report", breaking_ok: true,
  first_report: "2026-11-18T09:58:00Z", detected: "2026-11-18T10:00:00Z", last_update: "2026-11-18T10:05:00Z", age_hours: 0.2, stale: null, why: "58: fresh", incident: null,
  sources: [{ outlet: "Reuters", title: "Russia launches missile strikes on Kyiv, killing 25", url: "https://reuters.test/1", source: "news", reliable: true, official: false, at: null }],
  decision: { status: "queued", reason: "" }, post: null };
const RUMOR: Story = { ...STORY, id: 6, title: "exchange down", lane: "rumor", fast: false, fast_why: null, rumor: true, ahead_of_mainstream: true,
  evidence: { score: 14, level: "unverified", rumor: true, reasons: ["no reliable outlet yet: social or search signals only"], outlets: 0, official: false, mainstream: false },
  viral: { score: 77, parts: { social: 8 }, drivers: ["social"], measured: ["Google Trends 500K+ searches and rising"] }, decision: { status: "held", reason: "No reliable source" } };
const OVERVIEW = { now: "2026-11-18T10:10:00Z", counts: { breaking: 1, rumor: 1 }, stories: [STORY, RUMOR], total: 2,
  controls: { newsdesk_mode: "live", newsdesk_stop: false, trending_mode: "automatic", trending_live: true, trending_stop: false, x_live: true, blocked: null,
    fast_lane: { evidence_min: 55, max_age_min: 120, extra_per_day: 4, gap_min: 20 } },
  desk_events: [{ id: 1, title: "t", headline: "Oil jumps", status: "released", x_status: "published", topic: "", why: "breaking: major geopolitical event", first_seen: null, released_at: null, sources: [] }],
  published: [], incidents: [], ledger: { events: 3, detect: { median: 9, max: 30, n: 3 }, verify: null, publish: { median: 133, max: 224, n: 3 } }, ledger_rows: [], sources: [], last_scan: null, alerts: [] };
const DETAIL = { ...STORY, timeline: [{ at: "2026-11-18T09:58:00Z", seen: "2026-11-18T10:00:00Z", source: "news", outlet: "Reuters", title: STORY.title, url: "https://reuters.test/1", reliable: true, official: false }],
  posts: [], ledger: null, hooks: [{ kind: "breaking", text: "BREAKING: Russia launches missile strikes on Kyiv, killing 25\n\nVia Reuters", writer: "rules", ok: true, reason: "" },
    { kind: "bold", text: "Huge: ...", writer: "rules", ok: false, reason: "Sensitive topic (war or armed conflict): attitude (Huge)" }] };

let calls: string[] = [];
function serve() {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input} ${init?.body ?? ""}`.trim());
    const body = String(input).includes("/terminal/stories/") ? DETAIL : OVERVIEW;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 20)); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Price Chat Terminal", () => {
  it("shows lanes, both scores kept apart, the fast lane and the rumour flag", async () => {
    serve();
    render(<TerminalPage />);
    await settle();
    expect(screen.getByText("PRICE CHAT TERMINAL")).toBeTruthy();
    expect(screen.getByText("X LIVE")).toBeTruthy();
    expect(screen.getByText("FAST LANE")).toBeTruthy();
    expect(screen.getByText("unverified")).toBeTruthy();
    expect(screen.getByText("ahead of mainstream")).toBeTruthy();
    expect(screen.getAllByTitle(/Viral 81\/100/).length).toBe(1);
    expect(screen.getAllByTitle(/Evidence 88\/100/).length).toBe(1);
    expect(document.body.textContent).toMatch(/published 2.2 h/);
  });

  it("opens a story: why, timeline, gated hooks and the actions", async () => {
    serve();
    render(<TerminalPage />);
    await settle();
    fireEvent.click(screen.getByText(STORY.title, { selector: ".pt-story-title" }));
    await settle();
    expect(document.body.textContent).toMatch(/Fast lane: hard geopolitical event/);
    expect(document.body.textContent).toMatch(/Blocked: Sensitive topic/);
    fireEvent.click(screen.getByText("Use"));
    expect((screen.getByPlaceholderText("Write or pick a hook") as HTMLTextAreaElement).value).toMatch(/^BREAKING: Russia/);
    fireEvent.click(screen.getByText("Save draft"));
    await settle();
    expect(calls.some((c) => c.startsWith("POST /api/terminal/stories/5/draft") && c.includes("BREAKING"))).toBe(true);
  });

  it("filters by lane through the server", async () => {
    serve();
    render(<TerminalPage />);
    await settle();
    fireEvent.click(screen.getByTitle(/human review only/));
    await settle();
    expect(calls.some((c) => c.includes("/api/terminal?") && c.includes("lane=rumor"))).toBe(true);
  });
});
