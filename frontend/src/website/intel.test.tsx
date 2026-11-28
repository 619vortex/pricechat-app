import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { type IntelData, IntelTab, type Tip } from "./intel";
import { CommunityLane } from "../pages/terminal/CommunityLane";

/** TEST FIXTURES: made-up tips, links and people. */
const tip = (over: Partial<Tip> = {}): Tip => ({
  id: 3, ref: "CI-0003", kind: "news", kind_label: "News", title: "Northwind Mining CFO resigns", note: "", url: "https://example.org/a", host: "example.org", via: "web",
  status: "rejected", stage: 3, verified: false, verified_by: null, source: { class: "unknown", label: "Unknown publisher", outlet: "example.org" }, claim: "unverified",
  dup: {}, reason_label: "Promotional or a pump", editor_note: null, published_at: null, first_spotted: false, credit: true, disclosure: "none",
  created_at: "2026-12-01T10:00:00+00:00", appeal: null, can_appeal: true, can_withdraw: false, can_answer: false, ...over,
});
const data = (over: Partial<IntelData> = {}): IntelData => ({
  tips: [tip(), tip({ id: 4, ref: "CI-0004", status: "accepted", stage: 4, verified: true, verified_by: "reported by Reuters", reason_label: null, can_appeal: false,
    published_at: "2026-12-02T10:00:00+00:00", first_spotted: true, title: "Exchange halts withdrawals" })],
  record: { sent: 2, accepted: 1, published: 1, first_spotted: 1, duplicates: 0, rejected: 1, acceptance: null, acceptance_after: 5, median_hours: 1.5, points: 5 },
  limit: { per_day: 5, sent_today: 1, level: "Regular" }, kinds: [{ key: "news", label: "News" }, { key: "research", label: "Research" }, { key: "anomaly", label: "Market anomaly" }, { key: "discovery", label: "Discovery" }],
  points: { accepted: 15, research: 25, published: 40, spam: -50 }, bot: "@CryptoPriceChatBot", ...over,
});

let calls: string[] = [];
function serve(reply: (path: string) => unknown) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input}${init?.body ? " " + String(init.body) : ""}`);
    const body = reply(String(input));
    return body === undefined ? new Response(JSON.stringify({ detail: "Not found." }), { status: 404 }) :
      new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 400)); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Passport › Intelligence", () => {
  it("shows the track record, the Telegram shortcut and every tip's progress", async () => {
    serve((p) => (p.includes("leaderboard") ? { period: "week", rows: [{ position: 1, username: "pia", name: "pia", points: 55, accepted: 1, published: 1, first_spotted: 1 }], me: null } : {}));
    render(<IntelTab data={data()} reload={async () => {}} />);
    await settle();
    expect(screen.getByText(/Paste a link or forward a post to @CryptoPriceChatBot/)).toBeTruthy();
    expect(screen.getByText("Rate after 5 decided")).toBeTruthy();
    expect(screen.getByText("⚡ First spotted")).toBeTruthy();
    expect(document.querySelector(".ci2-state.is-pub")?.textContent).toBe("Published");
    expect(screen.getByText("pia")).toBeTruthy();
  });

  it("won't send before the position question is answered, and shows the live checks", async () => {
    serve((p) => (p.includes("/check") ? { source: { class: "social", label: "Social post", outlet: "x.com" }, kind: "news", tickers: ["BTC"], flags: ["Pump wording"] }
      : p.endsWith("/members/intel") ? { tip: tip({ status: "review", ref: "CI-0009" }) } : {}));
    render(<IntelTab data={data()} reload={async () => {}} />);
    fireEvent.change(screen.getByLabelText("Link"), { target: { value: "https://x.com/a/status/1" } });
    await settle();
    expect(screen.getByText("Social post · x.com")).toBeTruthy();
    expect(screen.getByText("Pump wording")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Send to the desk" }));
    await settle();
    expect(screen.getByRole("alert").textContent).toContain("position question");
    fireEvent.click(screen.getByRole("button", { name: "No position" }));
    fireEvent.click(screen.getByRole("button", { name: "Send to the desk" }));
    await settle();
    const sent = calls.find((c) => c.startsWith("POST /api/members/intel ") || c.startsWith("POST /api/members/intel{"))!;
    expect(sent).toContain('"disclosure":"none"');
    expect(screen.getByRole("status").textContent).toContain("CI-0009");
  });

  it("a rejected tip can be appealed once, in the member's own words", async () => {
    serve(() => ({ tip: tip({ appeal: { status: "open", text: "x" }, can_appeal: false }) }));
    render(<IntelTab data={data()} reload={async () => {}} />);
    const row = screen.getByText("Northwind Mining CFO resigns").closest("li")!;
    fireEvent.click(within(row).getByRole("button", { name: "Appeal" }));
    fireEvent.change(within(row).getByLabelText(/Why should the decision change/), { target: { value: "It was a real announcement." } });
    fireEvent.click(within(row).getByRole("button", { name: "Send appeal" }));
    await settle();
    expect(calls.some((c) => c.startsWith("POST /api/members/intel/3/appeal") && c.includes("real announcement"))).toBe(true);
  });
});

describe("Terminal › Community", () => {
  it("renders nothing while the feature is off", async () => {
    serve(() => undefined);
    const { container } = render(<CommunityLane />);
    await settle();
    expect(container.innerHTML).toBe("");
  });

  it("shows the checks and needs a reason to reject; accepting says nothing was published", async () => {
    const item = { ...tip({ status: "review", reason_label: null }), checks: { flags: ["Pump wording"], corroboration: { independent: [], matches: 0 } }, tickers: [], forward_from: null,
      story_key: null, member: { username: "kim", name: "kim", joined_at: null, community: "Regular" }, record: { sent: 4, accepted: 2, published: 0, first_spotted: 0, acceptance: null, rejected: 1 } };
    serve((p) => (p.includes("/queue") ? { items: [item], counts: { review: 1 }, reasons: { promotional: "Promotional or a pump", not_enough: "Not enough to act on yet" }, mode: "preview" } : item));
    render(<CommunityLane />);
    await settle();
    expect(screen.getByText("Not independently confirmed yet")).toBeTruthy();
    expect(screen.getAllByText("Pump wording").length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Reject…" }));
    fireEvent.click(screen.getByRole("button", { name: /Promotional or a pump · −50/ }));
    await settle();
    expect(calls.some((c) => c.includes("/community-intel/3/decide") && c.includes('"reason":"promotional"'))).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Accept as lead" }));
    await settle();
    expect(screen.getByRole("status").textContent).toContain("Nothing was published");
  });
});
