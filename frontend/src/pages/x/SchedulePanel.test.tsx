import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { SchedulePanel, type Schedule } from "./NewsDesk";

afterEach(() => cleanup());

const schedule: Schedule = {
  schedule: "slots", slot_hours_dubai: [0, 4, 8, 12, 16, 20], next_slot: "2026-11-09T08:00:00+00:00", next_slot_dubai: "12:00",
  slots: [
    { at: "2026-11-09T00:00:00+00:00", dubai: "04:00", state: "missed", event: null, kind: null, headline: null, x: null, telegram: null, telegram_message_id: null },
    { at: "2026-11-09T04:00:00+00:00", dubai: "08:00", state: "filled", event: 7, kind: "recap", headline: "📊 MARKET CHECK · 08:00 Dubai", x: "dry_run", telegram: "delivered", telegram_message_id: 148 },
  ],
  last_telegram: { event: 7, at: "2026-11-09T04:01:00+00:00", message_id: 148 }, last_x: { event: 7, at: "2026-11-09T04:01:00+00:00", status: "dry_run" }, failures: [],
};

describe("News Desk schedule", () => {
  it("shows the next slot, what filled each slot, missed slots, the last Telegram post and feed problems", () => {
    render(<SchedulePanel schedule={schedule} feeds={{ at: "2026-11-09T04:01:00+00:00", feeds: {
      "https://a.test": { name: "CoinDesk", tier: 2, items: 25, new: 3, newest: "2026-11-09T03:21:00+00:00", problem: null },
      "https://b.test": { name: "MarketWatch", tier: 1, items: 30, new: 0, newest: "2025-07-03T12:36:00+00:00", problem: "stale: newest item 03 Jul 2025" } } }} />);
    expect(screen.getByText("Schedule: next post 12:00 Dubai")).toBeTruthy();
    expect(screen.getByText("missed")).toBeTruthy();
    expect(screen.getByText("Market check")).toBeTruthy();
    expect(screen.getByText("#148").getAttribute("href")).toBe("https://t.me/Pricechatupdates/148");
    expect(screen.getByText(/1 with problems of 2/)).toBeTruthy();
    expect(screen.getByText("stale: newest item 03 Jul 2025")).toBeTruthy();
  });

  it("warns when a slot's post wasn't delivered", () => {
    render(<SchedulePanel schedule={{ ...schedule, failures: [{ event: 7, dubai: "08:00", x: "failed", telegram: "delivered" }] }} />);
    expect(screen.getByText(/Not delivered: 08:00 \(Telegram delivered, X failed\)/)).toBeTruthy();
  });
});
