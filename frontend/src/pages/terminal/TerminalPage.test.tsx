import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { featuredOf, feedOf, TerminalPage, type T2Overview, type T2Story } from "./TerminalPage";

/** TEST FIXTURES: made-up stories. */
const now = new Date().toISOString();
const STORY: T2Story = { id: 5, title: "Russia launches missile strikes on Kyiv, killing 25", category: "geopolitics", topic_status: "active", lane: "breaking", trend_score: 58, peak_score: 58,
  viral: { score: 81, parts: { shock: 10 }, drivers: ["shock"], measured: [] },
  evidence: { score: 88, level: "confirmed", rumor: false, reasons: ["3 independent reliable outlets"], outlets: 3, official: false, mainstream: true },
  rumor: false, ahead_of_mainstream: false, fast: true, fast_why: "hard geopolitical event; evidence confirmed (88)", breaking_ok: true,
  first_report: now, detected: now, last_update: now, age_hours: 0.2, stale: null, why: "58: fresh", incident: null,
  sources: [{ outlet: "Reuters", title: "Russia launches missile strikes on Kyiv, killing 25", url: "https://reuters.test/1", source: "news", reliable: true, official: false, at: null },
    { outlet: "CENTCOM on X", title: "Strikes on Kyiv", url: "https://x.com/centcom/status/1", source: "x_accounts", reliable: true, official: false, at: null }],
  decision: { status: "queued", reason: "" }, post: null };
const RUMOR: T2Story = { ...STORY, id: 6, title: "Bybaz exchange reportedly halts withdrawals", category: "crypto", lane: "rumor", fast: false, fast_why: null, rumor: true,
  evidence: { score: 14, level: "unverified", rumor: true, reasons: ["no reliable outlet yet"], outlets: 0, official: false, mainstream: false },
  viral: { score: 95, parts: {}, drivers: [], measured: [] }, decision: { status: "held", reason: "No reliable source" }, sources: [STORY.sources[0]] };
const OLD: T2Story = { ...STORY, id: 7, title: "Fed minutes show officials split", category: "markets", lane: "watch", fast: false, first_report: "2026-11-18T01:00:00Z",
  viral: { score: 30, parts: {}, drivers: [], measured: [] }, evidence: { ...STORY.evidence!, level: "single source", score: 40 } };
const OVERVIEW: T2Overview = { now, counts: { breaking: 1, rumor: 1, watch: 1, emerging: 2 }, stories: [STORY, RUMOR, OLD], total: 3, last_scan: now,
  desk_only: [{ id: 41, title: "Oil jumps 4% after the strike", headline: "Oil jumps 4%", status: "released", why: "major market move", first_seen: now, released_at: now, lane: "published",
    sources: [{ name: "Bloomberg", url: "https://bloomberg.test/1" }], desk: { id: 41, status: "released", headline: "Oil jumps 4%", why: "major market move", x_status: "published", tg_status: "delivered", released_at: now } }],
  automation: { status: "running", on: true, stopped: false, paused_at: null, attention: [], trending_auto: true },
  today: { x: 3, live: 3, telegram: 2 }, controls: { newsdesk_stop: false, trending_stop: false, x_live: true, newsdesk_mode: "live", trending_mode: "automatic" } };
const DETAIL = { ...STORY, timeline: [{ at: now, source: "news", outlet: "Reuters", title: STORY.title, url: "https://reuters.test/1", reliable: true, official: false }],
  posts: [], ledger: null, hooks: [{ kind: "breaking", text: "BREAKING: Russia launches missile strikes on Kyiv, killing 25\n\nVia Reuters", writer: "rules", ok: true, reason: "" },
    { kind: "viral", text: "Huge: ...", writer: "rules", ok: false, reason: "Sensitive topic" },
    { kind: "curiosity", text: "All eyes on Kyiv: Russia launches missile strikes, killing 25\n\nVia Reuters", writer: "rules", ok: true, reason: "" }] };

let calls: string[] = [];
let overview: T2Overview = OVERVIEW;
function serve(fail?: (url: string) => string | null) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    calls.push(`${init?.method ?? "GET"} ${url} ${init?.body ?? ""}`.trim());
    const problem = fail?.(url);
    if (problem) return new Response(JSON.stringify({ detail: problem }), { status: 409, headers: { "Content-Type": "application/json" } });
    const body = url.includes("/terminal/stories/") ? DETAIL : url.includes("/terminal/automation") ? { ...OVERVIEW.automation, on: false, status: "paused" } : overview;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 20)); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); overview = OVERVIEW; });

describe("Terminal 2.0", () => {
  it("picks the verified breaking story, never a more viral rumour, as the featured story", () => {
    expect(featuredOf([RUMOR, OLD, STORY])?.id).toBe(5);
    expect(featuredOf([{ ...STORY, lane: "published" }])).toBeNull();
  });

  it("merges News Desk events into one feed, important fresh stories first", () => {
    const items = feedOf(OVERVIEW);
    expect(items.map((i) => i.key)).toContain("d41");
    expect(items[0].key).toBe("t5");
    expect(items.find((i) => i.key === "t6")?.verdict).toBe("unverified");
    expect(items.find((i) => i.key === "t5")?.x).toBe(true);
  });

  it("shows one simple page: status, three counters, the featured story and the feed", async () => {
    serve();
    const { container } = render(<TerminalPage />);
    await settle();
    expect(screen.getByText("Running")).toBeTruthy();
    expect(screen.getByRole("switch", { name: "Automation" }).getAttribute("aria-checked")).toBe("true");
    const counters = [...container.querySelectorAll(".t2-counter")].map((b) => b.textContent);
    expect(counters).toEqual(["1Breaking", "3Emerging", "5Published today"]);
    const featured = container.querySelector(".t2-featured")!;
    expect(featured.textContent).toContain(STORY.title);
    expect(featured.textContent).toContain("Verified");
    expect(container.querySelector(".t2-alert")).toBeNull();  // nothing needs attention: no banner
    expect(screen.getByText("Oil jumps 4%")).toBeTruthy();
    const rumour = screen.getByText(RUMOR.title).closest(".t2-card")!;
    expect(rumour.textContent).toContain("Unverified");
    expect(container.textContent).not.toMatch(/trend score|fast lane|dry_run/i);  // no developer words on the main screen
  });

  it("never offers to publish an unverified featured story", async () => {
    overview = { ...OVERVIEW, stories: [RUMOR] };
    serve();
    render(<TerminalPage />);
    await settle();
    expect(screen.getByText(/Unconfirmed claim/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Publish" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("opens a story panel with hooks, scores, timeline and X and Telegram publishing through the checked endpoints", async () => {
    serve();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<TerminalPage />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    await settle();
    const panel = screen.getByRole("dialog", { name: "Story" });
    expect(within(panel).getByText("Headlines")).toBeTruthy();
    expect(within(panel).getByText("Blocked: Sensitive topic")).toBeTruthy();
    expect(within(panel).getByTitle(/Evidence 88\/100/)).toBeTruthy();
    expect(within(panel).getByTitle(/Viral 81\/100/)).toBeTruthy();
    fireEvent.click(within(panel).getByText(/All eyes on Kyiv/));
    expect((within(panel).getByLabelText("Post text") as HTMLTextAreaElement).value).toMatch(/^All eyes on Kyiv/);
    fireEvent.click(within(panel).getByRole("button", { name: "Publish to X" }));
    await settle();
    expect(calls.some((c) => c.startsWith("POST /api/terminal/stories/5/publish") && c.includes("All eyes on Kyiv"))).toBe(true);
    fireEvent.click(within(panel).getByRole("button", { name: "Publish to Telegram" }));
    await settle();
    expect(calls.some((c) => c.startsWith("POST /api/terminal/stories/5/telegram"))).toBe(true);
  });

  it("shows the server's refusal instead of pretending it published", async () => {
    serve((url) => (url.includes("/telegram") ? "Not sent: this story is already on the Telegram channel." : null));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<TerminalPage />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Publish to Telegram" }));
    await settle();
    expect(screen.getByText(/already on the Telegram channel/)).toBeTruthy();
  });

  it("quick publish shows the exact words and sends to the chosen platforms", async () => {
    serve();
    render(<TerminalPage />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Publish" }));
    await settle();
    const modal = screen.getByRole("dialog", { name: "Publish" });
    expect(modal.textContent).toContain("BREAKING: Russia launches missile strikes on Kyiv");
    fireEvent.click(within(modal).getByLabelText(/Telegram/));
    fireEvent.click(within(modal).getByRole("button", { name: "Publish now" }));
    await settle();
    expect(calls.filter((c) => c.startsWith("POST")).map((c) => c.split(" ")[1])).toEqual(["/api/terminal/stories/5/publish", "/api/terminal/stories/5/telegram"]);
  });

  it("the automation switch and the emergency stop ask first", async () => {
    serve();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<TerminalPage />);
    await settle();
    fireEvent.click(screen.getByRole("switch", { name: "Automation" }));
    fireEvent.click(screen.getByRole("button", { name: "Emergency stop" }));
    await settle();
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(calls.some((c) => c.startsWith("POST"))).toBe(false);
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("switch", { name: "Automation" }));
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Emergency stop" }));
    await settle();
    const posts = calls.filter((c) => c.startsWith("POST"));
    expect(posts[0]).toContain('/api/terminal/automation {"on":false}');
    expect(posts[1]).toContain('/api/terminal/stop {"which":"newsdesk","on":true}');
  });

  it("shows attention only when something needs it, and a stop can be lifted", async () => {
    overview = { ...OVERVIEW, controls: { ...OVERVIEW.controls, newsdesk_stop: true },
      automation: { ...OVERVIEW.automation, status: "paused", stopped: true, attention: ["News Desk emergency stop is on"] } };
    serve();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<TerminalPage />);
    await settle();
    expect(screen.getByText("Emergency stop is on")).toBeTruthy();
    expect(screen.getByText("Paused")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Lift stop" }));
    await settle();
    expect(calls.some((c) => c.includes('/api/terminal/stop {"which":"newsdesk","on":false}'))).toBe(true);
  });

  it("keeps every older control in the Settings drawer", async () => {
    serve();
    render(<TerminalPage />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    const drawer = screen.getByRole("dialog", { name: "Settings" });
    expect(within(drawer).getAllByRole("tab").map((t) => t.textContent)).toEqual(["General", "News Desk", "Trending", "X Intelligence", "World Monitor", "Validation", "Diagnostics"]);
    expect(within(drawer).getByText("Live")).toBeTruthy();
    expect(within(drawer).getByRole("button", { name: "Stop all Telegram and X posting" })).toBeTruthy();
  });

  it("filters the feed with one tap", async () => {
    serve();
    const { container } = render(<TerminalPage />);
    await settle();
    fireEvent.click(screen.getByRole("tab", { name: "Rumors" }));
    expect([...container.querySelectorAll(".t2-card-title")].map((x) => x.textContent)).toEqual([RUMOR.title]);
    fireEvent.change(screen.getByLabelText("Search stories"), { target: { value: "nothing matches" } });
    expect(screen.getByText("Nothing here right now.")).toBeTruthy();
  });
});
