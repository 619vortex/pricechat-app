import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ViralEngine, type Viral } from "./ViralEngine";

const VIRAL: Viral = {
  at: "2026-11-11T08:00:00Z", explore_pct: 20, margin: 12,
  hooks: { shock: { name: "Shocking fact", directive: "" }, money: { name: "Market consequence", directive: "" }, controversy: { name: "Controversy", directive: "" },
    winners_losers: { name: "Winners vs losers", directive: "" }, contradiction: { name: "Contradiction", directive: "" } },
  stories: [{ event: 9, title: "Tanker struck near Hormuz, Iran denies involvement", status: "released", score: 66, hook: "money", exploring: false,
    reactions: [{ name: "Brent crude", pct: 4.0 }],
    angles: [
      { hook: "money", name: "Market consequence", headline: "Tanker struck near Hormuz", context: "", stakes: "Since the first report: Brent crude +4.0%.", evidence: "measured",
        support: 0.9, score: 81, parts: { concrete: 20, money: 10 }, problems: [] },
      { hook: "shock", name: "Shocking fact", headline: "Tanker struck near Hormuz", context: "", stakes: "", evidence: "biggest attack since 2019", support: 0.7, score: 70,
        parts: { concrete: 12 }, problems: [] },
      { hook: "contradiction", name: "Contradiction", headline: "x", context: "", stakes: "", evidence: "even as", support: 0.6, score: 0, parts: {},
        problems: ["figures no source gave: 9"] }] }],
  engagement: { days: 21, measured_posts: 12, average_rate: 0.025, learning: true, telegram: "Telegram's Bot API gives no view counts.",
    hook_points: { money: 3.1 }, features: { hook: { money: { posts: 6, rate: 0.04, vs_average: 1.6 } } },
    experiments: [{ arm: "shock", control: "money", arm_posts: 6, control_posts: 6, lift: -0.6, verdict: "control better" }] },
  data: { bar: 55, mgm_bar: 75, mgm_today: "Bitcoin's biggest one-day drop since April 2025: −9.0%", recent: [],
    candidates: [{ kind: "anomaly", headline: "Bitcoin's biggest one-day drop since April 2025: −9.0%", interest: 91, why: { rarity: 60, since: 25 } }] },
};
let state = { data_posts: true, experiments: true };
const calls: string[] = [];

function serve() {
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input}`);
    if (init?.method === "PUT") state = { ...state, ...JSON.parse(String(init.body)) };
    const body = input.includes("/viral") ? VIRAL : state;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); state = { data_posts: true, experiments: true }; calls.length = 0; });

describe("Viral Engine", () => {
  it("shows the five hooks for a story, the winner, and why a refused one lost", async () => {
    serve();
    render(<ViralEngine />);
    expect(await screen.findByText("Tanker struck near Hormuz, Iran denies involvement")).toBeTruthy();
    expect(screen.getByText("Measured since the first report: Brent crude +4%")).toBeTruthy();
    expect(screen.getByText("refused")).toBeTruthy();
    expect(screen.getByText("figures no source gave: 9")).toBeTruthy();
  });

  it("shows what works and the experiments", async () => {
    serve();
    render(<ViralEngine />);
    fireEvent.click(await screen.findByText("What works"));
    expect(await screen.findByText("control better")).toBeTruthy();
    expect(screen.getByText("Shocking fact instead of Market consequence")).toBeTruthy();
    expect(screen.getByText(/Audience points per hook: Market consequence \+3.1/)).toBeTruthy();
  });

  it("shows today's MARKETS GONE MAD pick and switches data posts off", async () => {
    serve();
    render(<ViralEngine />);
    fireEvent.click(await screen.findByText("Data posts"));
    expect(await screen.findByText(/MARKETS GONE MAD today:/)).toBeTruthy();
    fireEvent.click(await screen.findByText("Turn data posts off"));
    await waitFor(() => expect(screen.getByText("Turn data posts on")).toBeTruthy());
    expect(calls).toContain("PUT /api/x/newsdesk/network");
  });
});
