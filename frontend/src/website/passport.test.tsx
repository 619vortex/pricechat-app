import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ProfilePage, PublicProfilePage } from "./passport";
import { refreshSession, type MissionView } from "./members";

const art = (treatment: string) => ({ treatment, overlay: null, placeholder: null, slot: null });
const egg = { level: 1, key: "egg", name: "The Egg", min_xp: 0, description: "Every Kiwi starts here.", art: art("egg") };
const chick = { level: 2, key: "chick", name: "Kiwi Chick", min_xp: 50, description: "Hatched and finding your feet.", art: art("chick") };

const mission = (over: Partial<MissionView>): MissionView => ({
  id: 1, key: "k", kind: "open_link", cadence: "once", title: "Read the latest update", description: "", platform: "website", xp: 3,
  verification: "verified_click", verification_label: "Verified as an open only",
  verification_method: "Opening it from this site.", status: "available", frequency: "Once",
  cta: { label: "Open", action: "open", url: "https://pricechat.example/update" },
  target: { url: "https://pricechat.example/update" },
  goal: 1, progress: 0, expires_at: null, completed_at: null, xp_awarded: 0, ...over,
});

const passport = (over: Record<string, unknown> = {}) => ({
  member: { id: "m1", username: "Kiwi123", display_name: "Kiwi123", username_chosen: true, joined_at: "2026-10-04T10:31:00+00:00", xp: 42,
            bonus_xp: 0, leaderboard_opt_out: false, xp_suspended: false },
  rank: { current: egg, next: chick, xp_to_next: 8, progress: 0.84 },
  linked: [{ platform: "telegram", label: "Telegram", connected: true, username: "kiwi", verified_at: "2026-10-04T10:31:00+00:00" }],
  badges: [], entitlements: [], recent: [],
  missions: [
    mission({}),
    mission({ id: 2, kind: "x_reply", title: "Reply to our latest X post", platform: "x", xp: 5, verification: "verified", verification_label: "Verified",
              status: "unavailable", cta: { label: "Connect X first", action: "link", platform: "x" }, target: { text: "Bitcoin isn't moving the way people think" } }),
    mission({ id: 3, kind: "telegram_participation", cadence: "daily", title: "Take part in the Telegram today", platform: "telegram",
              verification: "verified", verification_label: "Verified", status: "completed", xp_awarded: 3, cta: null, target: {} }),
    mission({ id: 4, kind: "weekly_active_days", cadence: "weekly", title: "3 active community days", platform: "telegram", xp: 10,
              verification: "verified", verification_label: "Verified", status: "in_progress", goal: 3, progress: 2, cta: null, target: {} }),
  ],
  next_steps: [{ mission_id: 1, title: "Watch the latest Short", xp: 3, platform: "youtube" }],
  activity: { today: { telegram: 1, x: 0, youtube: 1, website: 1, xp: 6, missions: 1, active_days: 1 },
              "7d": { telegram: 1, x: 0, youtube: 1, website: 2, xp: 42, missions: 1, active_days: 1 },
              "30d": { telegram: 1, x: 0, youtube: 1, website: 2, xp: 42, missions: 1, active_days: 1 } },
  breakdown: { total: 42, sources: [{ source: "Website", xp: 22 }, { source: "Telegram", xp: 20 }] },
  telegram: { username: "kiwi", messages_today: 7, eligible_today: 3, xp_today: 6, daily_cap: 10, active_days_7d: 1, last_verified_at: null,
              next: "+1 XP for each further real message (2 XP left today)", note: "Counted from your linked account." },
  public_profile: false,
  notifications: { unread: 0, items: [] },
  rank_up: null,
  ...over,
});

let routes: Record<string, unknown> = {};
const calls: string[] = [];

function serve(r: Record<string, unknown>) {
  routes = r;
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input}`);
    const body = routes[input.split("?")[0]];
    return body === undefined ? new Response(JSON.stringify({ detail: "nope" }), { status: 404 }) : new Response(JSON.stringify(body), { status: 200 });
  }));
}

async function show(ui: React.ReactElement) {
  const r = render(ui);
  for (let i = 0; i < 3; i++)
    await act(async () => {
      await new Promise((res) => setTimeout(res, 0));
    });
  return r;
}

const base = (p: unknown) => ({
  "/api/members/session": { signed_in: true, member: { id: "m1", username: "Kiwi123", xp: 42, rank: egg } },
  "/api/members/me": p, "/api/members/rules": { rules: [] }, "/api/members/ranks": { ranks: [egg, chick] },
  "/api/website/home": { sections: {}, latest: [], trending: [], whats_it_worth: [], originals: [], price_board: [], categories: [],
                         links: { telegram: null, x: null, youtube: null }, site_url: null },
  "/api/members/feed": {
    x_posts: [{ id: "p1", type: "post", text: "Bitcoin isn't moving the way people think", posted_at: "2026-10-04T09:00:00+00:00",
                metrics: { likes: 14, replies: 13 }, actions: { open: "https://x.com/pricechat/status/p1", like: "https://x.com/intent/like?tweet_id=p1",
                reply: "https://x.com/intent/post?in_reply_to=p1", repost: "https://x.com/intent/retweet?tweet_id=p1" } }],
    x_conversations: [{ id: "r1", type: "reply", text: "Influencers love to promote leverage", posted_at: "2026-10-04T10:00:00+00:00", metrics: {},
                        replied_to: "creator", actions: { open: "https://x.com/pricechat/status/r1", like: "", reply: "", repost: "" } }],
    videos: [], telegram: { url: "https://t.me/thepricechat" },
  },
});

beforeEach(() => {
  localStorage.clear();
  refreshSession();
  window.matchMedia = ((q: string) => ({ matches: q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {} })) as never;
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const xReply = mission({ id: 5, kind: "x_reply", title: "Reply to our latest post", platform: "x", xp: 0, earns_xp: false,
                         verification: "verified", verification_label: "Verified", cta: { label: "Reply", action: "x_reply", post_id: "1840000000000000001" },
                         target: { text: "Bitcoin isn't moving the way people think", posted_at: "2026-10-04T09:00:00+00:00", metrics: { likes: 14, replies: 13 } } });
const xRepost = mission({ id: 6, kind: "x_repost", title: "Repost our latest post", platform: "x", xp: 0, earns_xp: false,
                          verification: "verified", verification_label: "Verified", cta: { label: "Repost", action: "x_repost", post_id: "1840000000000000001" },
                          target: { text: "Bitcoin isn't moving the way people think" } });

const v3 = (over: Record<string, unknown> = {}) => {
  const p = passport(over);
  const missions = (p.missions as MissionView[]).concat([structuredClone(xReply), structuredClone(xRepost)]);
  return { ...p, missions, today: [missions[0], missions[4], missions[5]], more_missions: [missions[1]],
           x: { connected: true, username: "kiwifan", can_act: true, needs_reconnect: false }, ...over };
};

let bodies: Record<string, unknown> = {};
function serveV3(r: Record<string, unknown>) {
  serve(r);
  bodies = {};
  const inner = globalThis.fetch as unknown as (i: string, init?: RequestInit) => Promise<Response>;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    if (init?.body) bodies[input] = JSON.parse(String(init.body));
    return inner(input, init);
  }));
}

describe("Kiwi Passport V3", () => {
  it("shows who you are, your rank, your XP and how far the next rank is, then the calmer sections", async () => {
    serveV3(base(v3()));
    const { container } = await show(<ProfilePage />);
    expect(container.querySelector(".pp-a")!.textContent).toContain("Kiwi123");
    expect(container.querySelector(".pp-rank-name")!.textContent).toBe("The Egg");
    expect(container.querySelector(".pp-xp-big")!.textContent).toBe("42 XP");
    expect(container.querySelector(".pp-to-next")!.textContent).toBe("8 XP to Kiwi Chick");
    expect(container.querySelector(".pp-bar-of")!.textContent).toBe("42 / 50 XP this rank");  // labelled: progress within the rank
    expect(container.querySelector(".pp-activity")).toBeNull();
    expect(calls).not.toContain("GET /api/members/feed");
    const order = [...container.querySelectorAll(".pp-flow > section")].map((s) => s.getAttribute("aria-labelledby") ?? s.getAttribute("aria-label"));
    expect(order).toEqual(["Kiwi Passport", "ranks-title"]);
    // connected accounts and XP history aren't sections of their own: they're inside the Settings panel
    expect(container.querySelector(".pp-settings-wrap #linked-title")).toBeTruthy();
    expect(container.querySelector(".pp-settings-wrap #xp-title")).toBeTruthy();
  });

  it("keeps the name in the card: the pencil opens the editor, and Cancel closes it", async () => {
    serveV3(base(v3()));
    const { container } = await show(<ProfilePage />);
    expect(container.querySelector(".pp-user")!.textContent).toBe("Kiwi123");
    expect(container.querySelector("#pp-name-input")).toBeNull();  // no separate name box once a name is chosen
    await act(async () => screen.getByRole("button", { name: "Edit your username" }).click());
    expect(container.querySelector<HTMLInputElement>("#pp-name-input")!.value).toBe("Kiwi123");
    await act(async () => screen.getByRole("button", { name: "Cancel" }).click());
    expect(container.querySelector("#pp-name-input")).toBeNull();
  });

  it("with nothing left in the queue, nudges today's streak and offers the easy XP, a pack included", async () => {
    const streak = { current: 2, best: 2, done_today: false, at_risk: true, next_day: 3, next_reward: 9, next_is_milestone: true, next_milestone: 3,
                     next_milestone_bonus: 15, today_xp: 0, days: [{ day: "2026-10-04", done: false, xp: 0, today: true }], milestones: { 3: 15 } };
    serveV3(base(v3({ missions: [], today: [], more_missions: [], streak })));
    const { container } = await show(<ProfilePage />);
    const stk = container.querySelector(".pp-play .pp-hero .stk")!;  // the streak lives in the main square, as one row
    expect(stk.textContent).toContain("+9 XP today");
    expect(container.querySelectorAll(".stk")).toHaveLength(1);  // and has no section of its own
    expect(container.querySelector(".pp-mcard-done")!.textContent).not.toMatch(/caught up/i);  // never next to more things to do
    expect(container.querySelector(".pp-easy-title")!.textContent).toBe("Keep earning XP");
    expect(container.querySelector<HTMLAnchorElement>(".pp-quick-card[href='/giveaway']")).toBeTruthy();
  });

  it("shows ONE mission at a time with one big button, and an honest today count", async () => {
    const p = v3();
    (p.missions as MissionView[]).find((m) => m.id === 3)!.completed_at = new Date().toISOString(); // finished today
    serveV3(base(p));
    const { container } = await show(<ProfilePage />);
    const cards = container.querySelectorAll(".pp-mcard");
    expect(cards.length).toBe(1);
    expect(cards[0].querySelector(".pp-mcard-title")!.textContent).toBe("Read the latest update");
    expect(cards[0].querySelector(".pp-mcard-reward")!.textContent).toBe("+3 XP");
    expect(cards[0].querySelectorAll(".pp-mcard-action button").length).toBe(1);
    expect(container.querySelector(".pp-count")!.textContent).toBe("1 / 2 today"); // 1 done today + 1 still open (community/X tasks live elsewhere)
    expect(container.querySelectorAll(".pp-count .pp-dots .is-on").length).toBe(1);
    expect(container.textContent).not.toContain("Verified as an open only"); // no technical verification wording
  });

  it("never puts a connect step (or a task that needs one) in the main card: easy tasks only, no loop", async () => {
    const linkX = mission({ id: 8, kind: "link_x", title: "Connect X", platform: "x", xp: 20, cta: { label: "Connect", action: "link", platform: "x" }, target: {} });
    const linkTg = mission({ id: 10, kind: "link_telegram", title: "Connect Telegram", platform: "telegram", xp: 20,
                            cta: { label: "Connect", action: "link", platform: "telegram" }, target: {} });
    const needsX = mission({ id: 11, kind: "x_reply", title: "Reply to our post", platform: "x", xp: 0,
                             cta: { label: "Connect X first", action: "link", platform: "x" }, target: {} });
    const p = v3();
    p.today = [linkX, linkTg, needsX, ...(p.today as MissionView[])];
    serveV3(base(p));
    const { container } = await show(<ProfilePage />);
    expect(container.querySelector(".pp-mcard-title")!.textContent).toBe("Read the latest update");
    expect(container.querySelector(".pp-play")!.textContent).not.toContain("Connect X");
    expect(container.querySelector(".pp-play")!.textContent).not.toContain("Connect Telegram");
  });

  it("keeps community and X tasks out of the main card (they can't be confirmed on the spot, so they looped)", async () => {
    const extra = [
      mission({ id: 30, kind: "telegram_join_channel", title: "Join Price Chat Updates", platform: "telegram", xp: 10,
                cta: { label: "Join channel", action: "telegram_join", url: "https://t.me/x" }, target: {} }),
      mission({ id: 31, kind: "telegram_discussion", title: "Join the discussion", platform: "telegram", xp: 5,
                cta: { label: "Open", action: "open", url: "https://t.me/x" }, target: {} }),
      xReply, xRepost,
    ];
    const p = v3();
    p.today = [...extra, ...(p.today as MissionView[])];
    serveV3(base(p));
    const { container } = await show(<ProfilePage />);
    expect(container.querySelector(".pp-mcard-title")!.textContent).toBe("Read the latest update");
    for (const t of ["Join Price Chat Updates", "Join the discussion", "Reply to our latest post", "Repost our latest post", "Take part in the Telegram today"]) {
      expect(container.querySelector(".pp-play")!.textContent).not.toContain(t);
    }
  });

  it("never makes connecting Google / YouTube a mission: it's optional, under Connected accounts", async () => {
    const linkGoogle = mission({ id: 9, kind: "link_google", title: "Connect Google / YouTube", platform: "youtube", xp: 20,
                                 cta: { label: "Connect", action: "link", platform: "google" }, target: {} });
    const p = v3();
    p.today = [linkGoogle, ...(p.today as MissionView[])];
    serveV3(base(p));
    const { container } = await show(<ProfilePage />);
    expect(container.querySelector(".pp-mcard-title")!.textContent).toBe("Read the latest update");
    expect(container.querySelector(".pp-play")!.textContent).not.toContain("Google");
    expect(container.querySelector(".pp-count")!.textContent).toBe("0 / 1 today"); // not counted as something left to do
  });

  it("leaves the latest Short to Watch & Earn: it's never the mission card, so it can't pay on a bare click", async () => {
    const p = v3();
    const latest = mission({ id: 40, kind: "open_short", title: "Watch the latest Short", platform: "youtube",
                             cta: { label: "Watch", action: "open", url: "https://www.youtube.com/shorts/vid1" }, target: { external_id: "vid1" } });
    p.today = [latest, ...(p.today as MissionView[])];
    serveV3(base(p));
    const { container } = await show(<ProfilePage />);
    expect(container.querySelector("#mission-40")).toBeNull();
    expect(container.querySelector(".pp-mcard-title")!.textContent).toBe("Read the latest update");
  });

  it("opens the link, and never shows XP the server didn't award", async () => {
    serveV3({ ...base(v3()), "/api/members/missions/1/open": { status: "in_progress", xp: 0 } });
    const open = vi.fn();
    vi.stubGlobal("open", open);
    const { container } = await show(<ProfilePage />);
    await act(async () => screen.getByRole("button", { name: /^Open$/ }).click());
    await act(async () => new Promise((r) => setTimeout(r, 0)));
    expect(open).toHaveBeenCalledWith("https://pricechat.example/update", "_blank", "noopener");
    expect(container.querySelector("#mission-1 .pp-go-done")!.textContent).toBe(" Opened");
    expect(container.querySelector(".pp-xp-big")!.textContent).toBe("42 XP");
  });

  it("says when one mission is enough to level up", async () => {
    serveV3(base(v3({ rank: { current: egg, next: chick, xp_to_next: 3, progress: 0.9 } })));
    const { container } = await show(<ProfilePage />);
    expect(container.querySelector(".pp-mcard-lvl")!.textContent).toBe("⚡ This levels you up");
    expect(container.querySelector(".pp-bar-track")!.className).toContain("is-near");
  });

  it("celebrates XP that arrived while you were away, once", async () => {
    const recent = [{ id: 17, event_type: "website.login", xp: 2, category: "website", source: "website", reason: "Daily visit",
                      occurred_at: new Date().toISOString(), reversed: false, is_reversal: false }];
    serveV3(base(v3({ recent })));
    const first = await show(<ProfilePage />);
    expect(first.container.querySelector(".pp-toast")!.textContent).toBe("+2 XP · Daily visit");
    cleanup();
    const again = await show(<ProfilePage />);
    expect(again.container.querySelector(".pp-toast")).toBeNull(); // seen
  });

  it("keeps connected accounts simple, with Telegram details on demand", async () => {
    serveV3(base(v3({ linked: [
      { platform: "telegram", label: "Telegram", connected: true, username: "kiwi", verified_at: "2026-10-04T10:31:00+00:00" },
      { platform: "x", label: "X", connected: true, username: "kiwifan", verified_at: "2026-10-04T10:31:00+00:00" },
      { platform: "google", label: "Google", connected: false, username: null, verified_at: null },
    ] })));
    const { container } = await show(<ProfilePage />);
    const rows = [...container.querySelectorAll(".pp-accounts > li:not(#account-email)")];
    expect(rows[0].textContent).toContain("Telegram @kiwi");
    expect(rows[0].textContent).toContain("Connected ✓");
    expect(rows[0].textContent).toContain("Today: +6 XP");
    expect(rows[0].querySelector("details")!.textContent).toContain("Messages counted today3 of 7");
    expect(rows[0].querySelector("details")!.textContent).toContain("Daily cap10 XP");
    expect(rows[2].textContent).toContain("YouTube Optional"); // optional, never "incomplete"
    expect(rows[2].className).toBe("is-optional");
    expect(rows[2].querySelector(".pp-connect button")!.textContent).toBe("Connect");
  });

  it("asks to reconnect X when its permission is missing", async () => {
    serveV3(base(v3({ linked: [{ platform: "x", label: "X", connected: true, username: "kiwifan", verified_at: "2026-10-04T10:31:00+00:00" }],
                      x: { connected: true, username: "kiwifan", can_act: false, needs_reconnect: true } })));
    const { container } = await show(<ProfilePage />);
    const row = container.querySelector(".pp-accounts > li:not(#account-email)")!;
    expect(row.querySelector(".pp-connect button")!.textContent).toBe("Reconnect");
    expect(row.textContent).toContain("X needs permission to reply and repost for you.");
  });

  it("never fails silently: Connect X says why it can't, with Try again", async () => {
    const google = { available: true, note: "", callback: "http://127.0.0.1:5173/api/members/auth/google/callback" };
    serveV3({ ...base(v3({ linked: [{ platform: "x", label: "X", connected: false, username: null, verified_at: null }] })),
              "/api/members/auth/providers": { google, x: { available: false, callback: "", note: "Sign in with X isn't set up yet." },
                                               telegram: { available: true, note: "", bot: "bot", mode: "deeplink" } } });
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign, search: "", hostname: "example.test", pathname: "/website/profile" });
    const { container } = await show(<ProfilePage />);
    const row = container.querySelector("#account-x")!;
    await act(async () => (row.querySelector(".pp-connect button") as HTMLButtonElement).click());
    await act(async () => new Promise((r) => setTimeout(r, 0)));
    expect(row.querySelector(".pp-connect-error")!.textContent).toBe(
      "Couldn't connect X. The Price Chat team hasn't finished setting up X sign-in yet, so it can't be connected right now. Try again");
    expect(assign).not.toHaveBeenCalled();
    routes["/api/members/auth/providers"] = { google, x: { available: true, callback: "", note: "" }, telegram: { available: true, note: "", bot: "bot", mode: "deeplink" } };
    await act(async () => (row.querySelector(".pp-connect-error button") as HTMLButtonElement).click());
    await act(async () => new Promise((r) => setTimeout(r, 0)));
    expect(assign).toHaveBeenCalledWith("/api/members/auth/x/start?purpose=link"); // only ever Price Chat's start endpoint
  });

  it("never stays on Opening…: Back from the provider, or a navigation that never starts, restores Connect", async () => {
    const providers = { google: { available: true, note: "", callback: "" }, x: { available: true, callback: "", note: "" },
                        telegram: { available: true, note: "", bot: "bot", mode: "deeplink" } };
    serveV3({ ...base(v3({ linked: [{ platform: "google", label: "Google", connected: false, username: null, verified_at: null }] })),
              "/api/members/auth/providers": providers });
    vi.stubGlobal("location", { ...window.location, assign: vi.fn(), search: "", hostname: "example.test", pathname: "/website/profile" });
    const { container } = await show(<ProfilePage />);
    const row = container.querySelector("#account-google")!;
    const button = () => row.querySelector(".pp-connect > button") as HTMLButtonElement;
    await act(async () => button().click());
    await act(async () => new Promise((r) => setTimeout(r, 0)));
    expect(button().textContent).toBe("Opening Google…");
    expect(button().disabled).toBe(true);
    // the member pressed Back on Google's page: the browser restores this page from its back/forward cache
    await act(async () => window.dispatchEvent(Object.assign(new Event("pageshow"), { persisted: true })));
    expect(button().disabled).toBe(false);
    expect(button().textContent).toBe("Connect");
    expect(row.querySelector(".pp-connect-error")!.textContent).toContain("Google didn't finish connecting, so nothing was linked.");

    vi.useFakeTimers();
    try {
      await act(async () => (row.querySelector(".pp-connect-error button") as HTMLButtonElement).click());
      await act(async () => vi.advanceTimersByTimeAsync(0));
      expect(button().textContent).toBe("Opening Google…");
      await act(async () => vi.advanceTimersByTimeAsync(15_000));
      expect(button().disabled).toBe(false);
      expect(row.querySelector(".pp-connect-error")!.textContent).toContain("Google didn't open. Please try again.");
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a failed connection on that account's row when the provider sends the member back", async () => {
    serveV3(base(v3({ linked: [{ platform: "google", label: "Google", connected: false, username: null, verified_at: null }] })));
    window.history.pushState({}, "", "/website/profile?error=Google%20didn%27t%20complete%20the%20sign-in.&code=callback_failed&platform=google");
    const { container } = await show(<ProfilePage />);
    expect(container.querySelector("#account-google .pp-connect-error")!.textContent).toBe(
      "Couldn't connect YouTube. Google didn't complete the sign-in.");
    expect(container.querySelector("p.site-error")).toBeNull(); // shown where the member clicked, not lost at the top
    window.history.pushState({}, "", "/");
  });

  it("has small links to the leaderboard (with your place) and invites, and copies the invite link", async () => {
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    serveV3({ ...base(v3({ standing: { position: 12, board: "all" } })),
              "/api/members/me/referrals": { code: "kiwi941074", link: "http://127.0.0.1:5173/website/join/kiwi941074", joined: 7, qualified: 5,
                                             xp_earned: 125, reward_xp: 25, welcome_xp: 10, enabled: true,
                                             qualifying: ["Connected Telegram", "Connected X"] } });
    const { container } = await show(<ProfilePage />);
    const links = container.querySelector(".pp-links")!;
    expect(container.querySelectorAll(".pp-links")).toHaveLength(1);
    expect(links.closest(".pp-hero")).toBeTruthy();  // with the Kiwi profile and the XP bar, not under the missions
    expect(links.closest(".pp-stage")).toBeNull();
    expect(links.querySelector("a")!.getAttribute("href")).toBe("/website/leaderboard");
    expect(links.querySelector("a")!.textContent).toBe("🏆 Leaderboard #12");
    expect(container.querySelector(".pp-mcard")!.textContent).not.toContain("referral"); // no stats on the main screen
    await act(async () => screen.getByText("👥 Invite friends").click());
    await act(async () => new Promise((r) => setTimeout(r, 0)));
    const dialog = screen.getByRole("dialog");
    expect((dialog.querySelector("input") as HTMLInputElement).value).toBe("http://127.0.0.1:5173/website/join/kiwi941074");
    expect(dialog.textContent).toContain("Get +25 XP for every friend who joins and does one real thing");
    expect(dialog.textContent).toContain("do one of: connected telegram, connected x");
    await act(async () => screen.getByText("Copy invite link").click());
    expect(writeText).toHaveBeenCalledWith("http://127.0.0.1:5173/website/join/kiwi941074");
    expect(dialog.querySelector(".pp-go")!.textContent).toBe(" Copied 🥝");
  });

  it("says when you moved up the leaderboard, from the server's numbers", async () => {
    const before = v3({ standing: { position: 5, board: "all" } });
    const after = structuredClone(before);
    Object.assign((after.missions as MissionView[])[0], { status: "completed", xp_awarded: 3, completed_at: new Date().toISOString() });
    after.member = { ...after.member, xp: 45 };
    (after as Record<string, unknown>).standing = { position: 3, board: "all" };
    serveV3({ ...base(before), "/api/members/missions/1/open": { status: "completed", xp: 3 } });
    vi.stubGlobal("open", vi.fn());
    const { container } = await show(<ProfilePage />);
    expect(container.querySelector(".pp-moved")).toBeNull();
    routes["/api/members/me"] = after;
    await act(async () => screen.getByRole("button", { name: /^Open$/ }).click());
    await act(async () => new Promise((r) => setTimeout(r, 0)));
    expect(container.querySelector(".pp-moved")!.textContent).toBe("↑ You moved to #3");
  });

  it("levels up once: LEVEL UP!, the new rank, then Next mission", async () => {
    serveV3({ ...base(v3({ rank_up: { id: 9, title: "Kiwi Chick unlocked", level: 2 } })), "/api/members/me/notifications/read": { ok: true } });
    await show(<ProfilePage />);
    const dialog = screen.getByRole("dialog");
    expect(dialog.querySelector(".lu-kicker")!.textContent).toBe("Level up!");
    expect(dialog.querySelector("#rankup-title")!.textContent).toBe("The Egg");
    expect(dialog.querySelector(".lu-badge")!.textContent).toBe("Rank 1");
    await act(async () => screen.getByText("Next mission →").click());
    expect(calls).toContain("POST /api/members/me/notifications/read");
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("community reputation", () => {
  it("keeps legacy Telegram reputation behind Details", async () => {
    const rep = { price_chat_xp: 342, price_chat_weight: 1, price_chat_points: 342, combot_counts: true,
                  combot: { xp: 2817, messages: 4000, rep: 3, rank: null, active_days: null, source_timestamp: "2026-10-01T00:00:00+00:00",
                            imported_at: "2026-10-04T10:00:00+00:00", source_type: "csv" },
                  combot_weight: 0.25, legacy_cap: 500, legacy_raw: 704, legacy_points: 500, capped: true, rank_score: 842,
                  formula: "342 Price Chat XP x 1 + min(2817 Combot XP x 0.25, 500) legacy = 842" };
    serveV3(base(v3({ reputation: rep, rank: { current: egg, next: chick, xp_to_next: 8, progress: 0.84, score: 842 } })));
    const { container } = await show(<ProfilePage />);
    expect(container.querySelector(".pp-xp-big")!.textContent).toBe("842 XP");
    const box = container.querySelector(".pp-score-details")!;
    expect(box.querySelector("summary")!.textContent).toBe("Includes Telegram legacy reputation · Details");
    expect(box.textContent).toContain("Price Chat XP342");
    expect(box.textContent).toContain("Telegram legacy2,817");
    expect(box.textContent).toContain("Rank contribution+500");
    expect(box.textContent).not.toContain("x 0.25"); // no weighting math for members
  });

  it("confirms a newly linked X account", async () => {
    const p = v3({ linked: [{ platform: "x", label: "X", connected: true, username: "kiwifan", verified_at: "2026-10-04T10:31:00+00:00" }] });
    serveV3(base(p));
    window.history.pushState({}, "", "/website/profile?linked=x");
    const { container } = await show(<ProfilePage />);
    expect(container.querySelector(".pp-connected")!.textContent).toBe("X connected @kiwifan Verified");
    window.history.pushState({}, "", "/");
  });
});

describe("public profile", () => {
  it("shows only username, rank, badges and join date", async () => {
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/website/home": base(passport())["/api/website/home"],
            "/api/members/public/Kiwi123": { username: "Kiwi123", joined_at: "2026-10-04T10:31:00+00:00", rank: egg,
                                             badges: [{ key: "early_kiwi", name: "Early Kiwi", description: "Founding community member." }] } });
    const { container } = await show(
      <MemoryRouter initialEntries={["/website/u/Kiwi123"]}>
        <Routes><Route path="/website/u/:username" element={<PublicProfilePage />} /></Routes>
      </MemoryRouter>,
    );
    expect(container.querySelector(".site-passport-name")!.textContent).toBe("Kiwi123");
    expect(container.textContent).toContain("Early Kiwi");
    expect(container.textContent).not.toMatch(/telegram|XP/i);
    expect(screen.getByRole("button", { name: "Share profile" })).toBeTruthy();
    expect(container.querySelector<HTMLAnchorElement>(".pub-cta a")!.getAttribute("href")).toBe("/join");  // a visitor: start your own Kiwi
    expect(container.querySelector(".pub-card.rkd-tint-egg")).not.toBeNull();
  });

  it("says a private profile is private", async () => {
    serve({ "/api/members/session": { signed_in: false, member: null } });
    const { container } = await show(
      <MemoryRouter initialEntries={["/website/u/someone"]}>
        <Routes><Route path="/website/u/:username" element={<PublicProfilePage />} /></Routes>
      </MemoryRouter>,
    );
    expect(container.textContent).toContain("private, or doesn't exist");
  });
});

describe("Kiwi Passport: Telegram chat bar, honest daily status, compact VIP", () => {
  const crew = { enabled: true, link: "https://t.me/cryptopricechatbot?start=crew_kiwi123456",
    share_url: "https://t.me/share/url?url=https%3A%2F%2Ft.me%2Fcryptopricechatbot%3Fstart%3Dcrew_kiwi123456&text=Come%20join%20me",
    community: "https://t.me/thepricechat", reward_xp: 100, bonus_xp: 500, bonus_at: 5, hold_hours: 24, verified: 3, pending: 1, joined_waiting: 1,
    rejected: 0, xp_earned: 300, bonus_paid: false,
    milestones: [{ name: "First Friend", at: 1, done: true }, { name: "Kiwi Recruiter", at: 3, done: true }, { name: "Community Builder", at: 5, done: false },
                 { name: "Kiwi Ambassador", at: 10, done: false }] };

  it("has no separate crew card: the Telegram chat bar sits inside the mission square", async () => {
    serveV3({ ...base(v3()), "/api/members/me/crew": crew });
    const { container } = await show(<ProfilePage />);
    expect(container.querySelector(".crew")).toBeNull();
    expect(container.querySelector(".pp-play .pp-stage .tgb")).toBeTruthy();
    expect(calls.some((c) => c.startsWith("POST"))).toBe(false);  // opening the Passport never pays or sends anything
  });

  it("shows Telegram chat as a Daily Missions row next to the others, never as a separate box", async () => {
    const p = v3({ telegram: { username: "kiwi", messages_today: 4, eligible_today: 3, xp_today: 12, daily_cap: 50, active_days_7d: 2,
                              last_verified_at: null, next: "", note: "", messages_total: 120, xp_total: 300 } });
    const now = new Date().toISOString();
    for (const m of p.missions as MissionView[]) { m.status = "completed"; m.completed_at = now; }
    serveV3(base(p));
    const { container } = await show(<ProfilePage />);
    const rows = [...container.querySelectorAll(".pp-quick > li")].map((li) => li.textContent ?? "");
    expect(rows.map((t) => t.match(/Watch a short|Read a post|Open a pack|Telegram chat/)?.[0])).toEqual(
      ["Watch a short", "Read a post", "Open a pack", "Telegram chat"]);
    const tg = container.querySelector(".pp-quick .tgb.is-row")!;
    expect(tg.textContent).toContain("4 today · 12 / 50 XP");  // counter, daily progress and chat XP kept
    expect(container.querySelectorAll(".tgb")).toHaveLength(1);  // no standalone Telegram box any more
  });

  it("says 'Daily missions complete' only when they are, then 'Keep earning XP' with the real rewards", async () => {
    const p = v3();
    const now = new Date().toISOString();
    for (const m of p.missions as MissionView[]) { m.status = "completed"; m.completed_at = now; }
    const rules = [{ event_type: "website.x_post_read", label: "Read", description: "", xp: 1, daily_cap: 10, max_per_member: null, enabled: true,
                     availability: "available", availability_note: "", verification_source: "website" },
                   { event_type: "pack.open", label: "Pack", description: "", xp: 5, daily_cap: 100, max_per_member: null, enabled: true,
                     availability: "available", availability_note: "", verification_source: "packs" }];
    serveV3({ ...base(p), "/api/members/rules": { rules } });
    const { container } = await show(<ProfilePage />);
    const done = container.querySelector(".pp-mcard-done")!;
    expect(done.textContent).toContain("Daily missions complete ✓");
    {
      expect(done.textContent).not.toMatch(/caught up/i);
      const cards = [...container.querySelectorAll(".pp-quick-card")].map((c) => c.textContent);
      expect(cards.find((t) => t!.includes("Read a post"))).toContain("+1 XP");
      expect(cards.find((t) => t!.includes("Open a pack"))).toContain("+5 XP");
    }
  });

  it("shows the VIP Trading Bot as one compact line, not a banner above the Passport", async () => {
    serveV3({ ...base(v3()), "/api/website/home": { sections: {}, latest: [], trending: [], whats_it_worth: [], originals: [], price_board: [], categories: [],
              links: { telegram: null, x: null, youtube: null }, site_url: null, trading_bot: true },
              "/api/members/trading-bot": { rules: { min_rank_level: 3 }, rank: { ok: false } } });
    const { container } = await show(<ProfilePage />);
    const vip = container.querySelector<HTMLAnchorElement>(".pp-vip")!;
    {
      expect(vip.textContent).toMatch(/Unlocks at Kiwi Rank 3.*View requirements/);
      expect(container.querySelector(".pp-vipbot")).toBeNull();
      expect(vip.compareDocumentPosition(container.querySelector(".pp-a")!) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();  // below the Kiwi card
    }
  });
});

