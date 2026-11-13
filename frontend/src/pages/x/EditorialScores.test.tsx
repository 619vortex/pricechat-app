import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EditorialScores, type Performance, type Scorecards } from "./EditorialScores";

let minimum = 45;
const CARDS = (): Scorecards => ({
  minimum, dimensions: ["surprise", "impact", "controversy", "relevance", "discussion"], beats: { scandal: "Scandal", minor_corporate: "Minor corporate announcement" },
  stories: [
    { event: 7, title: "DOJ seizes $1.2B from Binance hack", headline: "DOJ seizes $1.2B from Binance hack", status: "candidate", why: "", virality: 70, evidence: "official",
      editorial: { total: 62, dims: { surprise: 11, impact: 19, controversy: 10, relevance: 14, discussion: 8 }, beat: "scandal", beat_name: "Scandal", beat_points: 5, reject: "" },
      below_minimum: false, chosen_hook: "DOJ seizes $1.2B stolen in the Binance hack", hook_why: "rank 1",
      hooks: [{ text: "DOJ seizes $1.2B stolen in the Binance hack", strategy: "model", rank: 1, total: 80, engagement: 70, accuracy: 100, problems: [], ok: true },
        { text: "Binance CEO arrested as DOJ seizes $1.2B", strategy: "model", rank: 2, total: 0, engagement: 75, accuracy: 75, problems: ["'arrested': an accusation or legal action the sources don't state"], ok: false }],
      question: "", question_why: "no question: discussion 8 under 10", visual: { first: "photo", why: "a licensed news photo" },
      accuracy: { ok: false, problems: ["X: $BNB: the sources name Binance, not its BNB token"] } },
    { event: 8, title: "Zeta launches partnership", headline: "Zeta launches partnership", status: "candidate", why: "editorial: a minor corporate announcement", virality: 58,
      evidence: "two outlets", editorial: { total: 10, beat: "minor_corporate", beat_name: "Minor corporate announcement", reject: "a minor corporate announcement" },
      below_minimum: true, hooks: [], chosen_hook: "", hook_why: "", question: "", question_why: "", visual: {}, accuracy: { ok: true, problems: [] } }],
});
const PERF: Performance = {
  days: 21, measured: 3, learning: false, learn_min: 8, beat_points: {}, strategy_points: {},
  platforms: { telegram: { posts: 0, rated: 0, median_rate: null, median_views: null, views: 0, shares: 0, replies: 0, reactions: 0 },
    x: { posts: 3, rated: 3, median_rate: 0.02, median_views: 120, views: 400, shares: 1, replies: 0, reactions: 6 } },
  tables: { beat: { scandal: { posts: 2, index: 1.4 } }, hook_strategy: {}, format: {}, question: {} },
  connections: { telegram: { connected: false, why: "not connected: Telegram's Bot API gives no view counts" }, x: { connected: true, why: "from X's API" } },
};
const calls: string[] = [];

function serve() {
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input}`);
    if (init?.method === "PUT") minimum = JSON.parse(String(init.body)).editorial_min;
    const body = input.includes("/performance") ? PERF : input.includes("/quality") ? { editorial_min: minimum } : CARDS();
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); minimum = 45; calls.length = 0; });

describe("Editorial scores", () => {
  it("shows the scorecard, the ranked hooks with fact-check problems, and the accuracy gate", async () => {
    serve();
    render(<EditorialScores />);
    expect(await screen.findByText("DOJ seizes $1.2B from Binance hack")).toBeTruthy();
    expect(screen.getByText("Editorial 62")).toBeTruthy();
    expect(screen.getByText(/Surprise 11 · Impact 19 · Controversy 10/)).toBeTruthy();
    expect(screen.getByText("'arrested': an accusation or legal action the sources don't state")).toBeTruthy();
    expect(screen.getByText(/Accuracy gate: X: \$BNB/)).toBeTruthy();
    expect(screen.getByText("Rejected: a minor corporate announcement")).toBeTruthy();
  });

  it("shows performance with Telegram clearly not connected", async () => {
    serve();
    render(<EditorialScores />);
    fireEvent.click(await screen.findByText("Performance"));
    expect(await screen.findByText(/Telegram's Bot API gives no view counts/)).toBeTruthy();
    expect(screen.getByText("not connected")).toBeTruthy();
    expect(screen.getByText(/Learning starts at 8 measured posts/)).toBeTruthy();
  });

  it("sets the editorial bar", async () => {
    serve();
    render(<EditorialScores />);
    await screen.findByText("Editorial 62");
    fireEvent.change(screen.getByLabelText("New editorial bar"), { target: { value: "55" } });
    fireEvent.click(screen.getByText("Set bar"));
    await waitFor(() => expect(calls).toContain("PUT /api/x/newsdesk/editorial/quality"));
    await waitFor(() => expect(screen.getByText("55")).toBeTruthy());
  });
});
