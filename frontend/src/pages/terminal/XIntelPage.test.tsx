import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { XIntelPage, type XOverview } from "./XIntelPage";

/** TEST FIXTURES: made-up X narrative and watchlist. */
const OVERVIEW: XOverview = {
  access: { configured: true, app_only: true, user_context: true, status: "connected", account: "thepricechat", tested: true, last_error: null,
    capabilities: { search_recent: { status: "available", detail: "search works" }, own_posts: { status: "available", detail: "5 recent posts readable" } },
    usage_today: { cost: 0.02 }, usage_month: { cost: 1.25, posts: 240 }, budget: { daily_usd: 5, monthly_usd: 30 }, plan: "pay-per-use (credits)",
    prices: { post_read: 0.005, user_read: 0.01, list_read: 0.005, owned_read: 0.001, trends_request: 0.01 }, pricing_source: "docs.x.com pricing", search_ok: true },
  enabled: false, daily_usd: 1, max_posts: 50, spent_today: 0, spent_month: 0, last_read: null,
  estimate: { accounts: 2, queries: 1, posts_per_day: 30, usd_per_day: 0.17, usd_per_month: 5.1, assumed_posts_per_account_day: 15, note: "estimate" },
  groups: { breaking: "Breaking news and global events", onchain: "On-chain investigators and exchange alerts" },
  tiers: { A: "Official", B: "Reporter", C: "Leads only" },
  watchlist: [{ handle: "Reuters", group: "breaking", tier: "A", note: "", posts_24h: 12 }, { handle: "zachxbt", group: "onchain", tier: "B", note: "", posts_24h: 2 }],
  emerging: [{ term: "$BYZ", kind: "cashtag", accounts: 4, ratio: 8 }],
  narratives: [{ id: 9, title: "Bybaz exchange drained of $120 million", category: "crypto", topic_status: "active", lane: "rumor", trend_score: 50, peak_score: 50,
    viral: { score: 78, parts: {}, drivers: ["social"], measured: [] }, evidence: { score: 30, level: "unverified", rumor: true, reasons: [], outlets: 0, official: false, mainstream: false },
    rumor: true, ahead_of_mainstream: true, fast: false, fast_why: null, breaking_ok: false, first_report: null, detected: null, last_update: null, age_hours: 0.3, stale: null,
    why: null, incident: null, sources: [], decision: { status: "held", reason: "No reliable source" }, post: null,
    claim: { status: "unsupported", kinds: ["financial scandal"], disputes: [], original: { outlet: "@zachxbt", title: "t", url: "https://x.com/zachxbt/status/10", at: null } },
    x: { posts: 4, independent: 2, independent_accounts: ["zachxbt", "lookonchain"], copies: 2, coordinated: false, tiers: { B: 1, C: 1 },
      velocity: { per_min: 12.5, between_reads: 0, basis: "X public counts" },
      links: [{ handle: "zachxbt", tier: "B", url: "https://x.com/zachxbt/status/10", text: "Bybaz exchange drained", at: null, engagement: 900 }] } }],
  posts_24h: 4, health: null, paused: null, protect: null,
  pilot: { started: "2026-11-18T08:10:00Z", spend_usd: 0.42, posts_read: 40, unique_posts: 38, duplicates_returned: 0, stories: 5, x_led: 3, before_other_sources: 2,
    lead_min: { median: 22, max: 41, n: 2 }, verified: 2, useful_discoveries: 1, verified_published: 1, false_positives: 1, cost_per_useful: 0.42,
    budget_check: "$28.53 left this month covers 13 days at $1.00 plus the posting reserve", days: { "2026-11-18": { usd: 0.42, new: 38 } },
    recent: [{ title: "Bybaz drained", x_first: "2026-11-18T09:00:00Z", other_first: "2026-11-18T09:22:00Z", x_led: true, lead_min: 22, accounts: ["@zachxbt"],
      verified_at: "2026-11-18T09:25:00Z", rumor: false, claim: "confirmed", published: true }] },
};

let calls: string[] = [];
function serve() {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input} ${init?.body ?? ""}`.trim());
    return new Response(JSON.stringify(OVERVIEW), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 20)); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("X Intelligence", () => {
  it("shows the access audit, the paid switch off, narratives with independence and original X links", async () => {
    serve();
    render(<XIntelPage />);
    await settle();
    expect(screen.getByText("Reading X: OFF (paid)")).toBeTruthy();
    expect(document.body.textContent).toMatch(/pay-per-use \(credits\)/);
    expect(document.body.textContent).toMatch(/\$0\.17\/day, \$5\.10\/month/);
    expect(screen.getByText("2 independent")).toBeTruthy();
    expect(screen.getByText("2 copies")).toBeTruthy();
    expect(screen.getByText("unsupported")).toBeTruthy();
    expect((screen.getByText(/@zachxbt \[B\]/) as HTMLAnchorElement).href).toBe("https://x.com/zachxbt/status/10");
    expect(screen.getByText("$BYZ · 4")).toBeTruthy();
    expect(document.body.textContent).toMatch(/Cost per useful discovery\$0\.42/);
    expect(document.body.textContent).toMatch(/median lead 22 min/);
  });

  it("turns paid reads on only after the price is confirmed", async () => {
    serve();
    const confirm = vi.fn((_message?: string) => false);
    vi.stubGlobal("confirm", confirm);
    render(<XIntelPage />);
    await settle();
    fireEvent.click(screen.getByText("Turn on (paid)…"));
    expect(confirm.mock.calls[0][0]).toMatch(/\$0\.005 per post/);
    expect(calls.some((c) => c.startsWith("PUT"))).toBe(false);
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByText("Turn on (paid)…"));
    await settle();
    expect(calls.some((c) => c.startsWith("PUT /api/terminal/x/settings") && c.includes('"confirm_paid":true'))).toBe(true);
  });

  it("edits the watchlist: change a tier, remove, add, save", async () => {
    serve();
    render(<XIntelPage />);
    await settle();
    fireEvent.change(screen.getByLabelText("Tier of zachxbt"), { target: { value: "A" } });
    fireEvent.click(screen.getByLabelText("Read zachxbt"));
    fireEvent.click(screen.getByLabelText("Remove Reuters"));
    fireEvent.change(screen.getByLabelText("New handle"), { target: { value: "@PeckShieldAlert" } });
    fireEvent.click(screen.getByText("Add"));
    fireEvent.click(screen.getByText("Save watchlist"));
    await settle();
    const put = calls.find((c) => c.startsWith("PUT /api/terminal/x/watchlist")) ?? "";
    expect(put).toContain('"handle":"zachxbt","group":"onchain","tier":"A","note":"","on":false');
    expect(put).toContain('"handle":"PeckShieldAlert"');
    expect(put).not.toContain("Reuters");
  });
});
