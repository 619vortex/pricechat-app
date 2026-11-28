import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PredictionsTab, type PredOverview, type PredQuestion } from "./predictions";

/** TEST FIXTURES: made-up calls, prices and players. */
const q = (over: Partial<PredQuestion> = {}): PredQuestion => ({
  id: 5, kind: "daily", type: "direction", asset: "BTC", title: "Will Bitcoin close today higher?",
  options: [{ key: "up", label: "Higher" }, { key: "down", label: "Lower" }], ref_price: 61840, settle_day: "2026-11-28",
  opens_at: "2026-11-28T13:30:00+00:00", locks_at: new Date(Date.now() + 3_600_000).toISOString(), settles_at: "2026-11-29T00:00:00+00:00",
  status: "open", outcome: null, settle_price: null, void_reason: "", calls: 212, split: null, my_call: null, xp: { play: 2, right: 8 },
  source: "Hyperliquid BTC perpetual, daily close (00:00 UTC)", ...over,
});
const data = (over: Partial<PredOverview> = {}): PredOverview => ({
  open: [q()], recent: [q({ id: 4, status: "settled", outcome: "up", settle_price: 61840, split: { up: 0.58, down: 0.42 } })], signed_in: true,
  rules: { xp: "Predictor XP never counts towards the Kiwi rank or any reward with money value." },
  stats: { calls: 41, decided: 40, right: 26, accuracy: 0.65, accuracy_after: 10, streak: 4, best_streak: 9, xp: 312, pending_xp: 0,
    level: { level: 3, name: "Caller", xp: 312, floor: 150, next_at: 400, next_name: "Sharp Caller" }, week_position: 18, week_players: 642, linked: true },
  history: [{ question_id: 4, kind: "daily", title: "x", settle_day: "2026-11-27", call: "Higher", outcome: "Higher", result: "right", status: "settled", settle_price: 61840, via: "telegram" }],
  ...over,
});

let calls: string[] = [];
function serve() {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input}${init?.body ? " " + String(init.body) : ""}`);
    if (String(input).includes("/vote")) return new Response(JSON.stringify({ question: q({ my_call: "up", calls: 213 }) }), { status: 200, headers: { "Content-Type": "application/json" } });
    return new Response(JSON.stringify({ period: "week", min_calls: 3, players: 1, rows: [{ position: 1, name: "kiwiwhale", points: 86, right: 7, decided: 7, calls: 7, accuracy: 1 }], me: null }),
      { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 20)); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Passport › Predictions", () => {
  it("shows today's call with the split hidden, the record and the Predictor level", async () => {
    serve();
    render(<PredictionsTab data={data()} reload={async () => {}} />);
    await settle();
    expect(screen.getByTestId("call-daily").textContent).toContain("Will Bitcoin close today higher?");
    expect(screen.getByTestId("call-daily").textContent).toContain("split shown at the lock");
    expect(screen.getByTestId("call-daily").textContent).not.toContain("58%");
    expect(screen.getByText("Caller")).toBeTruthy();
    expect(screen.getByText("65%")).toBeTruthy();
    expect(screen.getByText("#18")).toBeTruthy();
    expect(screen.getByText("kiwiwhale")).toBeTruthy();
    expect(screen.getByText(/Higher 58%/)).toBeTruthy();  // a locked / settled call shows its split
  });

  it("one tap makes the call through the shared API and marks it", async () => {
    serve();
    render(<PredictionsTab data={data()} reload={async () => {}} />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: /Higher/ }));
    await settle();
    expect(calls.some((c) => c.startsWith("POST /api/members/predictions/5/vote") && c.includes('"option":"up"'))).toBe(true);
    expect(screen.getByRole("button", { name: /Higher ✓/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("says when nothing is open and shows pending XP", async () => {
    serve();
    render(<PredictionsTab data={data({ open: [], stats: { ...data().stats!, pending_xp: 46 } })} reload={async () => {}} />);
    await settle();
    expect(screen.getByText(/daily call opens at 13:30 UTC/)).toBeTruthy();
    expect(screen.getByText(/46 XP pending/)).toBeTruthy();
  });

  it("a visitor without a Passport is sent to the Telegram group to play, with no vote buttons", async () => {
    serve();
    render(<PredictionsTab data={data({ signed_in: false, stats: undefined, history: undefined })} reload={async () => {}} />);
    await settle();
    const card = screen.getByTestId("call-daily");
    expect(card.querySelector("button")).toBeNull();
    expect(screen.getByRole("link", { name: "Play in Telegram" }).getAttribute("href")).toBe("https://t.me/cryptopricechat");
    expect(card.textContent).toContain("No sign-up needed");
  });
});
