import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TelegramText, WorldMonitor, xLength, type AppSnap, type Scan } from "./WorldMonitor";

const SCAN: Scan = {
  at: "2026-11-11T08:00:00Z", hours: 12, counts: { verified: 1, held: 1, rejected: 1 }, world_monitor_found: 2, world_monitor_confirmed: 1, next_slot_dubai: "12:00",
  note: "Market claims show as unchecked here.", feeds_checked_at: "2026-11-11T07:58:00Z",
  feeds: [{ url: "https://rss.dw.com/x", name: "DW", items: 30, world_monitor: true, problem: null }],
  stories: [
    { story: 1, title: "Missile strike hits Aramco terminal", link: "https://www.dw.com/en/a-1", source: "DW", world_monitor: true, published_at: "2026-11-11T07:00:00Z",
      category: "politics", state: "verified", why: "", evidence: "two outlets", confirmed_by: ["The National"], world_monitor_confirms: ["The National"], important: true,
      roundup: false, event: { id: 9, status: "candidate", score: 61, telegram: "pending", x: "pending", why: "" } },
    { story: 2, title: "Hot Inflation, AI Selloff & Anthropic IPO", link: "https://www.bloomberg.com/news/newsletters/a-b-c", source: "Bloomberg", world_monitor: false,
      published_at: "2026-11-11T06:00:00Z", category: null, state: "rejected", why: "a round-up of several stories: one post is one story", evidence: "",
      confirmed_by: [], world_monitor_confirms: [], important: false, roundup: true, event: null },
  ],
};
const NEXT = { at: "", next_slot_dubai: "12:00", x_links: true, simultaneous: "X posts the moment Telegram delivers.", posts: [{
  event: 9, when: "12:00 Dubai slot", will_post: true, status: "will post", score: 61, image: "/api/x/newsdesk/editorial/preview/9.jpg",
  package: { ok: true, final: false, x_text: "Missile hits Aramco terminal\n\nDW: https://www.dw.com/en/a-1", links: [{ name: "DW", url: "https://www.dw.com/en/a-1", kind: "article" }],
    telegram_html: '<b>Missile hits Aramco terminal</b>\n\n🔗 Source: <a href="https://www.dw.com/en/a-1">DW</a> <a href="javascript:alert(1)">bad</a><script>x</script>',
    copy: { writer: "local" } } }] };

const APP: AppSnap = { connected: true, seen: true, age_seconds: 60, note: "", app_version: "2.3.8", received_at: "2026-11-12T05:20:00Z",
  feeds: { earthquakes: { ok: true, count: 2, note: "", ms: 400 }, "acled-conflict": { ok: false, count: 0, note: "ACLED not configured", ms: 100 } },
  signals: [
    { id: "q", layer: "earthquake", severity: "critical", title: "M7.2 earthquake, Kermadec Islands (tsunami flag)", detail: "USGS", at: "2026-11-12T04:00:00Z",
      url: "https://earthquake.usgs.gov/a", lat: -29, lon: -177 },
    { id: "c", layer: "instability", severity: "high", title: "Ukraine: instability 59/100", detail: "", at: null, url: "", lat: null, lon: null },
  ],
  summary: { "macro-signals": { verdict: "CASH", fear_greed: 28, fear_greed_label: "Fear" } } };

function serve(scanNow = { new_stories: 3 }) {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input}`);
    const body = input.includes("scan-now") ? scanNow : input.includes("/monitor/upcoming") ? NEXT : input.includes("/monitor/app") ? APP : SCAN;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
  return calls;
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("World Monitor", () => {
  it("shows what was scanned, verified and rejected, and why", async () => {
    serve();
    render(<WorldMonitor />);
    expect(await screen.findByText("Missile strike hits Aramco terminal")).toBeTruthy();
    expect(screen.getByText("Verified (two outlets)")).toBeTruthy();
    expect(screen.getByText("a round-up of several stories: one post is one story")).toBeTruthy();
    expect(screen.getByText("The National (WM)")).toBeTruthy();
    fireEvent.click(screen.getByText("Verified only"));
    expect(screen.queryByText("Hot Inflation, AI Selloff & Anthropic IPO")).toBeNull();
  });

  it("shows exactly what Telegram and X will post, with live links only", async () => {
    serve();
    render(<WorldMonitor />);
    fireEvent.click(await screen.findByText("Up next"));
    expect(await screen.findByText("Posts: 12:00 Dubai slot")).toBeTruthy();
    const dw = screen.getAllByRole("link", { name: "DW" })[0];
    expect(dw.getAttribute("href")).toBe("https://www.dw.com/en/a-1");
    expect(screen.queryByRole("link", { name: "bad" })).toBeNull();  // only https links survive
    expect(document.querySelector("script")).toBeNull();
    expect(screen.getByText(/X · 57\/280/)).toBeTruthy();
  });

  it("scans the feeds on request and says scanning never posts", async () => {
    const calls = serve();
    render(<WorldMonitor />);
    fireEvent.click(await screen.findByText("Scan now"));
    await waitFor(() => expect(screen.getByText(/3 new stories found/)).toBeTruthy());
    expect(calls.some((c) => c === "POST /api/x/newsdesk/editorial/monitor/scan-now")).toBe(true);
  });

  it("counts links as 23 characters, like X", () => {
    expect(xLength("Hi https://example.com/a/very/long/path/that/goes/on")).toBe(3 + 23);
    render(<TelegramText html={"<b>Bold</b>\nline"} />);
    expect(screen.getByText("Bold").tagName).toBe("B");
  });
  it("shows the desktop app's signals, its health and the keyless feeds", async () => {
    serve();
    render(<WorldMonitor />);
    fireEvent.click(await screen.findByText(/Desktop app/));
    expect(await screen.findByText("M7.2 earthquake, Kermadec Islands (tsunami flag)")).toBeTruthy();
    expect(screen.getByText("Connected")).toBeTruthy();
    expect(screen.getByText("28 Fear")).toBeTruthy();
    expect(screen.getByText(/acled-conflict \(ACLED not configured\)/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Country instability" }));
    await waitFor(() => expect(screen.queryByText(/Kermadec/)).toBeNull());
    expect(screen.getByText("Ukraine: instability 59/100")).toBeTruthy();
  });
});
