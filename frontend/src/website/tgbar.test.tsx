import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const played: string[] = [];
vi.mock("./sound", () => ({ playSound: (n: string) => played.push(n) }));
vi.mock("./community", () => ({ TelegramButton: ({ label }: { label: string }) => <button type="button">{label}</button> }));

import type { TelegramActivity } from "./members";
import { TelegramBar } from "./tgbar";

const t = (over: Partial<TelegramActivity>): TelegramActivity => ({
  username: "kiwi", messages_today: 4, eligible_today: 3, xp_today: 12, daily_cap: 50, active_days_7d: 2, last_verified_at: null, next: "", note: "",
  messages_total: 120, xp_total: 300, ...over,
});

beforeEach(() => { played.length = 0; localStorage.clear(); vi.useFakeTimers(); });
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("Telegram chat bar", () => {
  it("shows the message counter and today's chat XP against the cap", () => {
    const { container } = render(<TelegramBar t={t({})} />);
    expect(container.querySelector(".tgb-num")!.textContent).toBe("120");
    expect(container.textContent).toContain("4 today · 12 / 50 XP");
    expect(played).toEqual([]);  // first visit: nothing to convert
  });

  it("converts the messages sent since the last visit into XP: coins while counting, then the XP pops", () => {
    localStorage.setItem("pc-tg-seen", JSON.stringify({ m: 115, xp: 290 }));
    const { container } = render(<TelegramBar t={t({})} />);
    expect(container.querySelector(".tgb-num")!.textContent).toBe("115");
    act(() => { vi.advanceTimersByTime(3000); });
    expect(container.querySelector(".tgb-num")!.textContent).toBe("120");
    expect(played.filter((p) => p === "coin").length).toBe(5);
    expect(played[played.length - 1]).toBe("xp");
    expect(screen.getByRole("status").textContent).toBe("+10 XP");  // the XP the server already credited, never invented
    expect(JSON.parse(localStorage.getItem("pc-tg-seen")!).m).toBe(120);
  });

  it("without a linked Telegram, offers to connect it", () => {
    render(<TelegramBar t={null} />);
    expect(screen.getByRole("button", { name: "Connect" })).toBeTruthy();
  });
});
