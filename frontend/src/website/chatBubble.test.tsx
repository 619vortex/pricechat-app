import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatBubble, bubbleHiddenOn } from "./chatBubble";

function serve(body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })));
}

async function show() {
  const r = render(<ChatBubble />);
  await act(async () => { await new Promise((res) => setTimeout(res, 0)); });
  return r;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("community chat bubble", () => {
  it("is absent when the website chat is off (no public group)", async () => {
    serve({ enabled: false });
    await show();
    expect(document.querySelector(".cb-fab")).toBeNull();
  });

  it("shows unread messages, opens the live feed and links to the Telegram group", async () => {
    const now = Date.now();
    serve({ enabled: true, title: "Price Chat 📈 🚀", username: "thepricechat", link: "https://t.me/thepricechat", members: 119,
            messages: [
              { id: 1, name: "Alice", text: "gm everyone", at: new Date(now - 120_000).toISOString(), reply_to: null, forwarded: false },
              { id: 2, name: "Dana", text: "btc looking strong", at: new Date(now - 30_000).toISOString(), reply_to: 1, forwarded: false },
            ] });
    localStorage.setItem("pc-chat-seen", String(now - 600_000));  // a returning visitor who last looked 10 minutes ago
    await show();
    const fab = document.querySelector<HTMLButtonElement>(".cb-fab")!;
    expect(fab.textContent).toContain("2"); // unread badge
    await act(async () => { fireEvent.click(fab); });
    expect(screen.getByText("gm everyone")).toBeTruthy();
    expect(screen.getByText(/↪ Alice: gm everyone/)).toBeTruthy(); // the reply shows what it answers
    expect(screen.getByText(/Live · 119 members/)).toBeTruthy();
    const join = screen.getByText("Open chat in Telegram").closest("a")!;
    expect(join.getAttribute("href")).toBe("https://t.me/thepricechat");
    expect(join.getAttribute("rel")).toContain("noopener");
    expect(document.querySelector("input, textarea")).toBeNull(); // read-only: nothing is sent from the website
  });

  it("shows no unread badge on a first visit, only the live dot; later messages count (audit UX-11)", async () => {
    const now = Date.now();
    const msgs = Array.from({ length: 12 }, (_, i) => ({ id: i + 1, name: "Kiwi", text: `msg ${i}`, at: new Date(now - (12 - i) * 20_000).toISOString(), reply_to: null, forwarded: false }));
    serve({ enabled: true, link: "https://t.me/thepricechat", messages: msgs });
    await show();
    const fab = document.querySelector<HTMLButtonElement>(".cb-fab")!;
    expect(fab.querySelector(".cb-badge")).toBeNull();  // used to be a "9+" for everyone
    expect(fab.querySelector(".cb-dot")).not.toBeNull();
    expect(fab.getAttribute("aria-label")).toBe("Open community chat");
    expect(Number(localStorage.getItem("pc-chat-seen"))).toBe(Date.parse(msgs[11].at));
    cleanup();
    serve({ enabled: true, link: "https://t.me/thepricechat", messages: [...msgs, { id: 99, name: "New", text: "hi", at: new Date(now).toISOString(), reply_to: null, forwarded: false }] });
    await show();
    expect(document.querySelector(".cb-badge")!.textContent).toBe("1");  // one real new message since the first look
  });

  it("stays off the sign-in and Passport pages, where it covered the buttons", () => {
    expect(["/join", "/website/join", "/passport", "/passport/x"].every(bubbleHiddenOn)).toBe(true);
    expect(["/", "/vpn", "/joint", "/pokemon"].some(bubbleHiddenOn)).toBe(false);
  });
});
