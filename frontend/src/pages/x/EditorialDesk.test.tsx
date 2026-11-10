import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EditorialDesk, type Queue, type Story } from "./EditorialDesk";

const card = { event: 7, kind: "news", headline: "NYSE PICKS AVALANCHE TO SETTLE TOKENIZED STOCKS", credit: "Source: CoinDesk", score: 58, held_score: 61, topic: "structure", picture: "graphic",
  gates: { dedup: { ok: true, detail: "a new story" }, numbers: { ok: true, detail: "no chart or measured figure in this post" } }, will_post: true, status: "will post at the next slot",
  image: "/api/x/newsdesk/editorial/preview/7.jpg", blocked: false, expires_at: null };
const queue: Queue = { next_slot: "2026-11-10T08:00:00+00:00", next_slot_dubai: "12:00", rule: "One story is posted once ... 1.5 times ...", candidates: [card], fallback: "Nothing is posted to fill it.", bar: 56, releasing: [] };
const story: Story = { subjects: ["OIL"], type: "market", direction: "down", topic: "WTI CRUDE FALLS 5.5% IN 24 HOURS", first_coverage: "2026-11-10T00:01:00Z", last_coverage: "2026-11-10T00:01:00Z",
  posts_made: 1, posts: [], merged_or_skipped: [{ event: 9, at: "2026-11-10T04:01:00Z", headline: "WTI", why: "same story as #3 4 h ago (OIL down 5.5%): 6% is not a material change" }] };

let calls: string[] = [];
function serve() {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input} ${init?.body ?? ""}`.trim());
    const body = input.includes("/history") ? { rule: queue.rule, stories: [story] } : input.includes("/block") ? {} : queue;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 20)); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Editorial desk", () => {
  it("shows the next slot's post as it will look, its gates, and the story history with why later posts were merged", async () => {
    serve();
    render(<EditorialDesk />);
    await settle();
    expect(screen.getByText("Next slot: 12:00 Dubai")).toBeTruthy();
    expect(screen.getByAltText(/Preview: NYSE PICKS AVALANCHE/).getAttribute("src")).toBe("/api/x/newsdesk/editorial/preview/7.jpg");
    expect(screen.getByText("Source: CoinDesk")).toBeTruthy();
    expect(screen.getByText("Will post")).toBeTruthy();
    expect(document.body.textContent).toMatch(/Numbers check: no chart/);
    expect(document.body.textContent).toMatch(/1\.5 times/);
    expect(document.body.textContent).toMatch(/not a material change/);
  });

  it("blocks a queued post after asking", async () => {
    serve();
    vi.stubGlobal("prompt", vi.fn(() => "not today"));
    render(<EditorialDesk />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Block" }));
    await settle();
    expect(calls.some((c) => c.startsWith("POST /api/x/newsdesk/editorial/block") && c.includes('"event_id":7') && c.includes('"block":true'))).toBe(true);
  });
});
