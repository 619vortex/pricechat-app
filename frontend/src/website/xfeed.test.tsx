import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { XFeedSection } from "./xfeed";
import type { FeedItem, XFeed } from "./members";

const item = (over: Partial<FeedItem>): FeedItem => ({
  id: "2107392473525817636", kind: "reply", text: "Bro went to a Solana conference to make sure Cardano still exists 😭",
  url: "https://x.com/pricechat/status/2107392473525817636", posted_at: new Date(Date.now() - 3600_000).toISOString(),
  parent: { text: "Great to be here at the Solana summit", name: "Charles Hoskinson", username: "IOHK_Charles" },
  replying_to: "IOHK_Charles", likes: 3, replies: 1, reposts: 2, xp: 1, author: { name: "Price Chat", username: "pricechat" }, media: [], done: [], ...over,
});
const feed = (items: FeedItem[], over: Partial<XFeed> = {}): XFeed => ({
  items, remaining: items.length, read_seconds: 6, today: { earned: 3, cap: 10 },
  needs_x: false, x: { connected: true, username: "kiwifan", can_act: false, needs_reconnect: false }, ...over,
});

let routes: Record<string, unknown> = {};
let calls: { method: string; url: string; body: unknown }[] = [];
function serve(r: Record<string, unknown>) {
  routes = r;
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method ?? "GET", url, body: init?.body ? JSON.parse(String(init.body)) : null });
    const b = routes[url];
    const v = typeof b === "function" ? (b as () => unknown)() : b;
    return v === undefined ? new Response(JSON.stringify({ detail: "nope" }), { status: 404 }) : new Response(JSON.stringify(v), { status: 200 });
  }));
}
const posts = (path: string) => calls.filter((c) => c.method === "POST" && c.url === `/api/members/${path}`);
async function tick(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}
async function mount(reload = vi.fn(async () => null), rewardFrom?: { current: Element | null }) {
  const r = render(<div className="site"><XFeedSection reload={reload} rewardFrom={rewardFrom} /></div>);
  await tick();
  return { ...r, reload };
}

let played: string[] = [];
class FakeAudio {
  constructor(public src: string) {}
  volume = 1;
  currentTime = 0;
  preload = "";
  play() {
    played.push(this.src.replace("/website/sounds/", "").replace(".wav", ""));
    return Promise.resolve();
  }
}

beforeEach(() => {
  vi.useFakeTimers();
  played = [];
  localStorage.clear();
  vi.stubGlobal("Audio", FakeAudio);
  window.matchMedia = ((q: string) => ({ matches: q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {} })) as never;
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

const CAN: Partial<XFeed> = { x: { connected: true, username: "kiwifan", can_act: true, needs_reconnect: false } };

describe("Read & Earn", () => {
  it("before X is connected: only a Connect X card, no feed, nothing read", async () => {
    serve({ "/api/members/me/x-feed": feed([], { needs_x: true, x: { connected: false, username: null, can_act: false, needs_reconnect: false } }) });
    const { container } = await mount();
    expect(screen.getByRole("heading", { name: "Read & Earn" })).not.toBeNull();
    expect(container.querySelector(".xf-connect-card")!.textContent).toContain("Connect X to start Read & Earn");
    expect(screen.getByRole("button", { name: "Connect X" })).toBeTruthy();  // X's own OAuth page, never a password field
    expect(container.querySelector("input[type=password], iframe")).toBeNull();
    expect(container.querySelector(".xf-card")).toBeNull();
    await tick(8000);
    expect(posts("me/x-feed/shown")).toHaveLength(0);
  });

  it("only promises in-Passport replies and reposts while X write is on (audit PP-14)", async () => {
    const notConnected = feed([], { needs_x: true, x: { connected: false, username: null, can_act: false, needs_reconnect: false } });
    serve({ "/api/members/me/x-feed": notConnected, "/api/members/auth/providers": { x: { available: true, write: false } } });
    const off = await mount();
    expect(off.container.querySelector(".xf-connect-card")!.textContent).not.toContain("Reply and repost");
    cleanup();
    serve({ "/api/members/me/x-feed": notConnected, "/api/members/auth/providers": { x: { available: true, write: true } } });
    const on = await mount();
    await tick();
    expect(on.container.querySelector(".xf-connect-card")!.textContent).toContain("Reply and repost without leaving your Passport");
  });

  it("keeps everything in ONE card: account, today's XP, the post with its context and media, actions and Up next", async () => {
    serve({ "/api/members/me/x-feed": feed([
      item({ media: [{ type: "photo", url: "https://pbs.twimg.com/media/a.jpg", width: 1200, height: 675, alt: "A chart" }] }),
      item({ id: "2", kind: "post", parent: null, replying_to: null, text: "Pokémon cards are outperforming your portfolio. 😭" }),
    ], { remaining: 2, today: { earned: 3, cap: 10, read: 3, per_post: 1 } }) });
    const { container } = await mount();
    const box = container.querySelector(".xf-box")!;
    expect(box.querySelectorAll(".xf-card")).toHaveLength(1); // one post at a time
    expect(box.querySelector(".xf-acct")!.textContent).toContain("@kiwifan");
    expect(box.querySelector(".xf-today-meta")!.textContent).toBe("3 / 10 XP today3 read2 posts left");
    expect(box.querySelector(".xf-parent .xf-who")!.textContent).toBe("Charles Hoskinson @IOHK_Charles");
    expect(box.querySelector(".xf-main .xf-who")!.textContent).toContain("Price Chat @pricechat");
    expect(box.querySelector(".xf-text")!.textContent).toBe("Bro went to a Solana conference to make sure Cardano still exists 😭");
    expect(box.querySelector<HTMLImageElement>(".xf-media img")!.getAttribute("src")).toBe("https://pbs.twimg.com/media/a.jpg");
    expect(box.querySelector(".xf-metrics")!.textContent).toContain("Read the replies on X");
    expect(box.querySelector(".xf-read-label")!.textContent).toBe("Reading · +1 XP");
    expect(box.querySelector(".xf-next")).toBeTruthy(); // Up next lives INSIDE the card
    expect([...box.querySelectorAll(".xf-next-text")].map((e) => e.textContent)).toEqual(["Pokémon cards are outperforming your portfolio. 😭"]);
    expect(box.querySelector(".xf-foot")!.textContent).toContain("support only");
  });

  it("jumps to a post from Up next without leaving the page", async () => {
    serve({ "/api/members/me/x-feed": feed([item({}), item({ id: "2", kind: "post", parent: null, replying_to: null, text: "Second post about markets today" })]),
            "/api/members/me/x-feed/shown": { ok: true } });
    const { container } = await mount();
    await act(async () => (container.querySelector(".xf-next-row") as HTMLButtonElement).click());
    await tick(400);
    expect(container.querySelector(".xf-text")!.textContent).toBe("Second post about markets today");
    expect(container.querySelector(".xf-next")).toBeNull();
  });

  it("pays for reading only when the server says so, with the Watch & Earn reward moment, then lets the member move on", async () => {
    let paid = false;
    serve({
      "/api/members/me/x-feed": () => feed(paid ? [] : [item({})], paid ? { today: { earned: 4, cap: 10 } } : {}),
      "/api/members/me/x-feed/shown": { ok: true, read_seconds: 6 },
      "/api/members/me/x-feed/read": () => (paid = true, { ok: true, code: "paid", xp: 1, message: "Counted." }),
    });
    fireEvent.pointerDown(document.body); // the member has interacted: sounds may play
    const { container, reload } = await mount();
    await tick(500);
    expect(posts("me/x-feed/shown")[0].body).toEqual({ post_id: "2107392473525817636" });
    await tick(3000);
    expect(posts("me/x-feed/read")).toHaveLength(0); // not yet read long enough
    await tick(3000);
    expect(posts("me/x-feed/read")).toHaveLength(1);
    await tick(2000);
    expect(played).toEqual(["xp"]);
    expect(reload).toHaveBeenCalled(); // the Passport counts it in (and shows a rank-up first, if any)
    expect(container.querySelector(".xf-read-label")!.textContent).toBe("✓ Read +1 XP");
    expect(container.querySelector(".xf-today-meta")!.textContent).toContain("4 / 10 XP today");
    expect(container.querySelector(".xf-nextbtn")!.className).toContain("is-primary");
    await act(async () => screen.getByRole("button", { name: "Next post" }).click());
    await tick(400);
    expect(container.querySelector(".xf-empty-title")!.textContent).toBe("You're caught up 🥝");
  });

  it("without the posting permission: Like, Reply and Repost open X's own screens, never XP", async () => {
    serve({ "/api/members/me/x-feed": feed([item({})]), "/api/members/me/x-feed/shown": { ok: true }, "/api/members/me/x-feed/like-opened": { ok: true } });
    fireEvent.pointerDown(document.body);
    const { container } = await mount();
    const btns = () => [...container.querySelectorAll<HTMLAnchorElement>(".xf-x-row .xf-x-btn")];
    expect(btns().map((b) => b.textContent!.trim())).toEqual(["♥ Like ↗", "💬 Reply ↗", "🔁 Repost ↗"]);
    expect(btns()[0].getAttribute("href")).toBe("https://x.com/intent/like?tweet_id=2107392473525817636");
    expect(btns()[1].getAttribute("href")).toBe("https://x.com/intent/post?in_reply_to=2107392473525817636");
    expect(btns()[2].getAttribute("href")).toBe("https://x.com/intent/retweet?tweet_id=2107392473525817636");
    await act(async () => { btns()[0].click(); });
    await tick();
    expect(btns()[0].textContent).toBe("✓ Opened on X");  // honest: we can't know they liked it
    expect(posts("me/x-feed/like-opened")[0].body).toEqual({ post_id: "2107392473525817636" });
    expect(container.querySelector(".xf-x .xf-support")!.textContent).toContain("Allow them right here");
    expect(posts("me/x/repost")).toHaveLength(0);  // nothing is sent for the member
    expect(container.querySelector(".xf-x-row")!.textContent).not.toContain("XP");
  });

  it("Like opens X's like screen as a small window over the page (same browser), and says so until it's closed", async () => {
    serve({ "/api/members/me/x-feed": feed([item({})]), "/api/members/me/x-feed/shown": { ok: true }, "/api/members/me/x-feed/like-opened": { ok: true } });
    const popup = { closed: false };
    const open = vi.fn(() => popup as unknown as Window);
    vi.stubGlobal("open", open);
    fireEvent.pointerDown(document.body);
    const { container } = await mount();
    const like = container.querySelector<HTMLAnchorElement>(".xf-x-row .xf-x-btn")!;
    await act(async () => { like.click(); });
    await tick();
    expect(open).toHaveBeenCalledWith("https://x.com/intent/like?tweet_id=2107392473525817636", "pricechat-x", expect.stringContaining("popup=yes"));
    expect(container.querySelector(".xf-x-row")!.textContent).toContain("Tap Like in the X window, then close it");
    popup.closed = true;
    await tick(700);
    expect(container.querySelector(".xf-x-row .xf-x-btn")!.textContent).toBe("✓ Opened on X");
    expect(posts("me/x-feed/like-opened")).toHaveLength(1);
  });

  it("with the posting permission: Reply opens a box right here, Repost in one press, as the member, never XP", async () => {
    serve({
      "/api/members/me/x-feed": feed([item({})], CAN),
      "/api/members/me/x-feed/shown": { ok: true },
      "/api/members/me/x/reply": { ok: true, code: "sent", message: "Replied", url: "https://x.com/kiwifan/status/99", reconnect: false },
      "/api/members/me/x/repost": { ok: true, code: "sent", message: "Reposted", url: "https://x.com/pricechat/status/2107392473525817636", reconnect: false },
    });
    fireEvent.pointerDown(document.body);
    const { container } = await mount();
    await act(async () => screen.getByRole("button", { name: /Reply/ }).click());
    const box = screen.getByRole("textbox", { name: "Your reply" });
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Post reply" }).disabled).toBe(true);  // the member writes it
    fireEvent.change(box, { target: { value: "  Cardano still exists 😂  " } });
    await act(async () => screen.getByRole("button", { name: "Post reply" }).click());
    await tick();
    expect(posts("me/x/reply")[0].body).toEqual({ post_id: "2107392473525817636", text: "Cardano still exists 😂" });
    expect(container.querySelector<HTMLAnchorElement>(".xf-x-btn.is-done")!.getAttribute("href")).toBe("https://x.com/kiwifan/status/99");
    await act(async () => screen.getByRole("button", { name: /Repost/ }).click());
    await tick();
    expect(posts("me/x/repost")[0].body).toEqual({ post_id: "2107392473525817636" });
    expect([...container.querySelectorAll(".xf-x-btn.is-done")].map((b) => b.textContent!.replace("view", "").trim())).toEqual(["✓ Replied", "✓ Reposted"]);
    expect(container.querySelector(".xf-x-row")!.textContent).not.toContain("XP");  // support only: never a reward
  });

  it("shows what the member already did, from the server's record", async () => {
    serve({ "/api/members/me/x-feed": feed([item({ done: ["repost", "like"] })], CAN), "/api/members/me/x-feed/shown": { ok: true } });
    const { container } = await mount();
    expect([...container.querySelectorAll(".xf-x-btn.is-done")].map((b) => b.textContent)).toEqual(["✓ Opened on X", "✓ Reposted"]);
  });

  it("shows X's answer when it refuses, with a way to reconnect", async () => {
    serve({
      "/api/members/me/x-feed": feed([item({})], CAN),
      "/api/members/me/x-feed/shown": { ok: true },
      "/api/members/me/x/repost": { ok: false, code: "reconnect", message: "Reconnect X to let Price Chat send this for you.", url: null, reconnect: true },
    });
    await mount();
    await act(async () => screen.getByRole("button", { name: /Repost/ }).click());
    await tick();
    expect(screen.getByRole("alert").textContent).toContain("Reconnect X to let Price Chat send this for you.");
    expect(screen.getByRole("alert").textContent).toContain("Reconnect X");
  });

  it("says plainly when today's reading XP is used up, and never shows XP it can't pay", async () => {
    serve({ "/api/members/me/x-feed": feed([item({ xp: 0 })], { today: { earned: 10, cap: 10 } }) });
    const { container } = await mount();
    expect(container.querySelector(".xf-read-label")!.textContent).toBe("Reading · today's reading XP is used up");
  });
});
