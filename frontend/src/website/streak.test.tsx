import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const played: string[] = [];
vi.mock("./sound", () => ({ playSound: (n: string) => played.push(n) }));

import { StreakCard, type StreakView } from "./streak";

const days = (doneLast: number, today = true) => Array.from({ length: 14 }, (_, i) => ({
  day: `2026-10-${String(i + 1).padStart(2, "0")}`, done: i >= 14 - doneLast && (today || i < 13), xp: i >= 14 - doneLast ? 5 : 0, today: i === 13,
}));
const view = (over: Partial<StreakView>): StreakView => ({
  current: 0, best: 0, done_today: false, at_risk: false, next_day: 1, next_reward: 5, next_is_milestone: false, next_milestone: 3,
  next_milestone_bonus: 15, today_xp: 0, days: days(0), milestones: { 3: 15, 7: 40, 14: 75, 30: 150, 60: 250, 100: 400 }, ...over,
});

beforeEach(() => { played.length = 0; localStorage.clear(); vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("daily streak", () => {
  it("keeps it to one slim row: the flame, the count, the last 7 days and today, no explanations", () => {
    const { container, rerender } = render(<StreakCard s={view({})} />);
    expect(container.querySelector(".stk-digits")!.textContent).toBe("0");
    rerender(<StreakCard s={view({ current: 4, best: 4, at_risk: true, next_reward: 13, days: days(4, false), next_milestone: 7, next_milestone_bonus: 40 })} />);
    expect(container.querySelector(".stk-digits")!.textContent).toBe("4");
    expect(container.querySelectorAll(".stk-days li").length).toBe(7);
    expect(container.querySelectorAll(".stk-days li.is-done").length).toBe(3);  // today not done yet
    expect(container.querySelector(".stk-state")!.textContent).toBe("+13 XP today");
    expect(container.querySelector("section, h2")).toBeNull();  // no section / heading of its own
    expect(container.textContent).not.toMatch(/bonus|Best|How streaks work|quick task/);
    expect(played).toEqual([]);  // nothing to celebrate yet
  });

  it("celebrates a new day: counts up with ticks, then the fanfare on a milestone, and shows the XP", () => {
    localStorage.setItem("pc-streak-seen", JSON.stringify({ n: 2, day: "2026-10-13" }));
    const { container } = render(<StreakCard s={view({ current: 3, best: 3, done_today: true, today_xp: 24, next_reward: 11, days: days(3), next_milestone: 7 })} />);
    expect(container.querySelector(".stk-digits")!.textContent).toBe("2");  // starts from what this browser last showed
    act(() => { vi.advanceTimersByTime(1000); });
    expect(container.querySelector(".stk-digits")!.textContent).toBe("3");
    expect(played).toEqual(["levelup"]);  // day 3 is a milestone
    expect(screen.getByRole("status").textContent).toBe("+24 XP");
    expect(container.querySelectorAll(".stk-days li.is-done").length).toBe(3);
    expect(container.querySelector(".stk-state")!.textContent).toBe("Today ✓");
    expect(JSON.parse(localStorage.getItem("pc-streak-seen")!).n).toBe(3);
  });

  it("an ordinary new day plays the streak chime, a longer jump ticks along the way", () => {
    localStorage.setItem("pc-streak-seen", JSON.stringify({ n: 1, day: "x" }));
    render(<StreakCard s={view({ current: 5, best: 5, done_today: true, today_xp: 13, days: days(5), next_milestone: 7 })} />);
    act(() => { vi.advanceTimersByTime(3000); });
    expect(played.filter((p) => p === "tick").length).toBe(3);
    expect(played[played.length - 1]).toBe("streak");
  });

  it("a page reload on a day already celebrated doesn't replay anything", () => {
    localStorage.setItem("pc-streak-seen", JSON.stringify({ n: 5, day: "2026-10-14" }));
    render(<StreakCard s={view({ current: 5, best: 5, done_today: true, today_xp: 13, days: days(5) })} />);
    act(() => { vi.advanceTimersByTime(3000); });
    expect(played).toEqual([]);
  });
});
