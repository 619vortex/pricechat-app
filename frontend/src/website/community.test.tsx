import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { JoinPage, LeaderboardPage, LegalPage, OWNER_MARKERS } from "./community";
import { START_NOW } from "./vpn";
import { ProfilePage } from "./passport";
import { Website } from "./Website";
import { RankKiwi } from "./RankKiwi";
import { refreshSession } from "./members";

const art = (treatment: string, placeholder: string | null = null, slot: string | null = null) => ({ treatment, overlay: null, placeholder, slot });
const scout = { level: 4, key: "scout", name: "Scout Kiwi", min_xp: 400, description: "", art: art("none", "🎒", "back") };
const home = {
  sections: { latest: true, trending: true, whats_it_worth: true, price_board: true, originals: true, community: true, story: true },
  latest: [], trending: [], whats_it_worth: [], originals: [], price_board: [], categories: [],
  links: { telegram: "https://t.me/thepricechat", x: "https://x.com/pricechat", youtube: "https://www.youtube.com/@pricechat" }, site_url: null,
};
const passport = {
  member: { id: "m1", username: "KiwiKing", display_name: "KiwiKing", username_chosen: true, joined_at: "2026-09-01T10:00:00+00:00", xp: 612,
            bonus_xp: 0, leaderboard_opt_out: false, xp_suspended: false },
  rank: { current: scout, next: { ...scout, level: 5, key: "warrior", name: "Warrior Kiwi", min_xp: 900 }, xp_to_next: 288, progress: 0.424 },
  linked: [
    { platform: "telegram", label: "Telegram", connected: true, username: "kiwiking", verified_at: "2026-09-02T10:00:00+00:00" },
    { platform: "x", label: "X", connected: false, username: null, verified_at: null },
    { platform: "google", label: "Google / YouTube", connected: false, username: null, verified_at: null },
  ],
  badges: [{ key: "early_kiwi", name: "Early Kiwi", description: "", awarded_at: "2026-09-01T10:00:00+00:00", awarded_by: "automatic" }],
  entitlements: [],
  recent: [
    { id: 2, event_type: "identity.telegram_linked", xp: 20, category: "reputation", source: "telegram", reason: "Connected Telegram",
      occurred_at: "2026-09-02T10:00:00+00:00", reversed: false, is_reversal: false },
    { id: 3, event_type: "manual.award", xp: -10, category: "reputation", source: "admin", reason: "Reversed: duplicate",
      occurred_at: "2026-09-03T10:00:00+00:00", reversed: false, is_reversal: true },
  ],
};

let routes: Record<string, unknown> = {};
const calls: string[] = [];

function serve(r: Record<string, unknown>) {
  routes = r;
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input}`);
    const path = input.split("?")[0];
    const body = routes[path];
    return body === undefined ? new Response(JSON.stringify({ detail: "nope" }), { status: 404 }) : new Response(JSON.stringify(body), { status: 200 });
  }));
}

async function show(ui: React.ReactElement) {
  const r = render(ui);
  await act(async () => {
    await new Promise((res) => setTimeout(res, 0));
  });
  await act(async () => {
    await new Promise((res) => setTimeout(res, 0));
  });
  return r;
}

beforeEach(() => {
  localStorage.clear();
  refreshSession();
  window.matchMedia = ((q: string) => ({ matches: q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {} })) as never;
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("header account link", () => {
  it("shows LOGIN when signed out", async () => {
    serve({ "/api/website/home": home, "/api/members/session": { signed_in: false, member: null } });
    const { container } = await show(<Website />);
    const login = container.querySelector<HTMLAnchorElement>(".site-login")!;
    expect(login.textContent).toBe("Login");
    expect(login.getAttribute("href")).toBe("/website/join");
    expect(container.querySelector(".site-joincta .site-button")!.textContent).toBe("Join Price Chat");
    // a first-time visitor reads what joining gets them, right by the button
    expect(container.querySelector(".site-joincta-note")!.textContent).toContain("unlock rewards as your Kiwi ranks up");
    expect(container.querySelector(".site-joincta-note")!.textContent).not.toMatch(/every rank/i);  // audit UX-7: 4 of 10 ranks have no reward yet
  });

  it("shows the member's Kiwi, username and rank when signed in", async () => {
    serve({ "/api/website/home": home, "/api/members/session": { signed_in: true, member: { id: "m1", username: "KiwiKing", xp: 612, rank: scout } } });
    const { container } = await show(<Website />);
    const chip = container.querySelector<HTMLAnchorElement>(".site-account-member")!;
    expect(chip.getAttribute("href")).toBe("/passport");
    expect(chip.textContent).toContain("KiwiKing");
    expect(chip.textContent).toContain("Scout Kiwi");
    expect(chip.querySelector("img")!.getAttribute("src")).toBe("/brand/kiwi.webp");
    expect(container.querySelector(".site-login")).toBeNull();
    expect(container.querySelector(".site-joincta-note")).toBeNull();  // members aren't sold what they have
  });

  it("puts the community call to action after the story, just before the footer", async () => {
    serve({ "/api/website/home": home, "/api/members/session": { signed_in: false, member: null } });
    const { container } = await show(<Website />);
    const ids = [...container.querySelectorAll("main > section")].map((s) => s.id).filter(Boolean);
    expect(ids.indexOf("community")).toBe(ids.length - 1);
    expect(ids.indexOf("story")).toBeLessThan(ids.indexOf("community"));
    const footer = container.querySelector("footer")!;
    for (const label of ["About", "Contact", "Disclaimer", "Terms", "Privacy", "YouTube", "X", "Telegram"])
      expect(within(footer).getAllByText(label).length).toBeGreaterThan(0);
  });

  it("tells the story forward: the approved copy, nothing accusatory, and the Kiwi building beside the headline", async () => {
    serve({ "/api/website/home": home, "/api/members/session": { signed_in: false, member: null } });
    const { container } = await show(<Website />);
    const story = container.querySelector("#story")!.textContent!;
    for (const line of [
      "We spent years helping build and support the Crypto Banter community.",
      "It was an important chapter for us. We met incredible people, built relationships around the world and learned what makes an online community genuinely special.",
      "Over time, our paths and ideas about what we wanted to build next began to change.",
      "Rather than looking backwards, we decided to take everything we had learned — the experience, relationships, creativity and community spirit — and build something of our own.",
      "Something broader than one market.",
      "A place for people who are curious about crypto, stocks, collectibles, property, cars and everything else that has a price.",
    ]) expect(story).toContain(line);
    for (const gone of ["taken for granted", "Commitments should mean something", "should have been there", "built on trust", "promises", "walk away"])
      expect(story).not.toContain(gone);
    // the "We chose to build something new." scene and its closing copy were removed from the site
    for (const gone of ["We chose to build", "something new.", "Price Chat is the next chapter.", "The community comes first."])
      expect(story).not.toContain(gone);
    expect(container.querySelector(".site-build, .pd-stage, .kb-build")).toBeNull();
    expect(container.querySelector(".site-horizon .kf")).not.toBeNull(); // the failed-flight Kiwi above stays
  });
});

describe("join page", () => {
  it("puts one-tap Telegram, X and Google first, showing only the ones that are set up", async () => {
    serve({
      "/api/members/session": { signed_in: false, member: null },
      "/api/website/home": home,
      "/api/members/auth/providers": {
        google: { available: true, note: "", callback: "" }, x: { available: false, note: "not set up", callback: "" },
        telegram: { available: true, note: "", bot: "pricechat_bot", mode: "deeplink" },
      },
      "/api/members/auth/telegram/start": { request: 7, link: "https://t.me/pricechat_bot?start=pc_abc", expires_at: "" },
    });
    const { container } = await show(<JoinPage />);
    const buttons = [...container.querySelectorAll(".lg-social .site-provider")].map((b) => b.textContent!.trim());
    expect(buttons).toEqual(["Continue with Telegram", "Continue with Google"]);  // X isn't set up: not shown at all
    expect(screen.getByText(/never posts or messages for you/)).toBeTruthy();
    // a first-time visitor sees what they get before signing up
    expect(screen.getByRole("list", { name: "What you get" }).textContent).toContain("unlock rewards along the way");
    expect(screen.getByRole("list", { name: "What you get" }).textContent).not.toMatch(/every rank/i);  // audit UX-7
    expect(container.querySelector("input[type=email]")).toBeNull();  // email sign-in not set up: no dead end, no empty form
    expect(container.querySelector(".lg-legacy-box")).toBeNull();
  });

  it("also signs up with an email code when that's set up: email, 6 digits (pasted), then a username", async () => {
    serve({
      "/api/members/session": { signed_in: false, member: null },
      "/api/website/home": home,
      "/api/members/auth/providers": {
        google: { available: true, note: "", callback: "" }, x: { available: true, note: "", callback: "" },
        telegram: { available: true, note: "", bot: "pricechat_bot", mode: "deeplink" }, email: { available: true, code_minutes: 10, note: "" },
      },
      "/api/members/auth/telegram/start": { request: 7, link: "https://t.me/pricechat_bot?start=pc_abc", expires_at: "" },
      "/api/members/auth/email/start": { sent: true, resend_after: 60, expires_in: 600 },
      "/api/members/auth/email/verify": { signed_in: true, created: true, needs_username: true, member: { username: "Kiwi123456" } },
      "/api/members/username/check": { ok: true, problem: null },
      "/api/members/me": { member: { username: "KiwiFan" } },
    });
    const assign = vi.fn();
    vi.stubGlobal("location", { ...location, assign, search: "", replace: vi.fn() });
    const { container } = await show(<JoinPage />);
    expect([...container.querySelectorAll(".lg-social .site-provider")].length).toBe(3);
    expect(screen.getByText("or with your email")).toBeTruthy();
    const email = container.querySelector<HTMLInputElement>("input[type=email]")!;
    await act(async () => { fireEvent.change(email, { target: { value: "fan@example.com" } }); });
    await act(async () => { fireEvent.click(screen.getByText("Continue with Email")); });
    expect(calls).toContain("POST /api/members/auth/email/start");
    expect(screen.getByText(/We sent a 6-digit code to fan@example.com/)).toBeTruthy();
    expect(screen.getByText("Resend code in 60s")).toBeTruthy(); // cooldown
    const first = container.querySelector<HTMLInputElement>(".lg-digit")!;
    await act(async () => {
      fireEvent.paste(first, { clipboardData: { getData: () => "123 456" } });
      await new Promise((res) => setTimeout(res, 0));
    });
    expect(calls).toContain("POST /api/members/auth/email/verify");
    const name = await screen.findByLabelText("Username");
    await act(async () => {
      fireEvent.change(name, { target: { value: "KiwiFan" } });
      await new Promise((res) => setTimeout(res, 400));
    });
    expect(screen.getByText("Available")).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByText("Start earning XP")); });
    expect(calls).toContain("PATCH /api/members/me");
    expect(assign).toHaveBeenCalledWith("/passport?welcome=new");
  });

  it("Telegram opens in the tap itself (link prepared beforehand) and explains Start, then the bot's reply", async () => {
    serve({
      "/api/members/session": { signed_in: false, member: null },
      "/api/website/home": home,
      "/api/members/auth/providers": { google: { available: false, note: "", callback: "" }, x: { available: false, note: "", callback: "" },
                                       telegram: { available: true, note: "", bot: "pricechat_bot", mode: "deeplink" } },
      "/api/members/auth/telegram/start": { request: 7, link: "https://t.me/pricechat_bot?start=pc_abc", expires_at: "" },
      "/api/members/auth/telegram/status/7": { status: "pending" },
    });
    const open = vi.fn();
    vi.stubGlobal("open", open);
    await show(<JoinPage />);
    expect(calls).toContain("POST /api/members/auth/telegram/start");  // prepared before the tap
    await act(async () => screen.getByText("Continue with Telegram").click());
    expect(open).toHaveBeenCalledWith("https://t.me/pricechat_bot?start=pc_abc", "_blank", "noopener");  // synchronously: not blocked on phones
    const steps = screen.getByRole("status").textContent!;
    expect(steps).toContain("Tap Start");
    expect(steps).toContain("Yes, sign me in");  // the bot asks first (audit SEC-4)
    expect(steps).toContain("Done!");
    expect(sessionStorage.getItem("pc-tg-signin")).toContain("pc_abc");  // survives coming back from the Telegram app / a reload
    sessionStorage.clear();
  });
});

describe("Kiwi Passport", () => {
  it("shows rank, XP progress, linked accounts and every XP award with its reason", async () => {
    serve({
      "/api/members/session": { signed_in: true, member: { id: "m1", username: "KiwiKing", xp: 612, rank: scout } },
      "/api/members/me": passport, "/api/website/home": home, "/api/members/rules": { rules: [] }, "/api/members/ranks": { ranks: [scout] },
    });
    const { container } = await show(<ProfilePage />);
    expect(container.querySelector(".pp-user")!.textContent).toBe("KiwiKing");
    expect(container.querySelector(".pp-to-next")!.textContent).toBe("288 XP to Warrior Kiwi");
    expect(container.querySelector<HTMLElement>(".pp-bar-fill")!.style.getPropertyValue("--p")).toBe("0.424");  // a transform, not a width
    expect(container.querySelector(".pp-bar-track")!.getAttribute("aria-valuenow")).toBe("42");
    const linked = container.querySelector(".pp-accounts")!.textContent!;
    expect(linked).toContain("@kiwiking");
    expect(linked).toContain("Connected ✓");
    expect([...container.querySelectorAll(".pp-accounts .pp-connect button")].map((b) => b.textContent)).toEqual(["Connect", "Connect"]); // X, Google
    const xp = [...container.querySelectorAll(".site-xp-list li")].map((li) => li.textContent);
    expect(xp[0]).toContain("+20 XP");
    expect(xp[0]).toContain("Connected Telegram");
    expect(xp[1]).toContain("-10 XP");
    expect(container.querySelector(".pp-kiwi img")!.getAttribute("src")).toBe("/brand/kiwi.webp"); // the canonical Kiwi, never redrawn
    expect(screen.getByText("Delete my Price Chat account")).toBeTruthy();
  });
});

describe("invite links", () => {
  it("remember the invite and say plainly what earns the welcome bonus", async () => {
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/website/home": home,
            "/api/members/auth/providers": { google: { available: true }, x: { available: true }, telegram: { available: true, bot: "b", mode: "deeplink" } },
            "/api/members/referrals/visit": { valid: true, welcome_xp: 10, signed_in: false } });
    const { container } = await show(
      <MemoryRouter initialEntries={["/website/join/kiwi941074"]}>
        <Routes><Route path="/website/join/:code" element={<JoinPage />} /></Routes>
      </MemoryRouter>,
    );
    expect(calls).toContain("POST /api/members/referrals/visit");
    expect(container.querySelector(".lb-invited")!.textContent).toBe(
      "🥝 A friend invited you. Create your Kiwi Passport and do one real thing in Price Chat to get a +10 XP welcome bonus.");
  });
});

describe("leaderboard", () => {
  const row = (position: number, username: string, value: number) => ({ position, username, value, rank_score: value, xp: value, legacy_points: 0, rank: scout });

  it("shows usernames, ranks and XP, medals for the top 3, and switches period and board", async () => {
    serve({
      "/api/members/session": { signed_in: false, member: null }, "/api/website/home": home,
      "/api/members/leaderboard": { period: "all", metric: "score", note: "", me: null,
                                    rows: [row(1, "KiwiKing", 561), row(2, "Second", 400), row(3, "Third", 300), row(4, "Fourth", 200)] },
    });
    const { container } = await show(<LeaderboardPage />);
    const rows = [...container.querySelectorAll(".lb-list li")];
    expect(rows[0].textContent).toContain("KiwiKing");
    expect(rows[0].textContent).toContain("Scout Kiwi");
    expect(rows[0].textContent).toContain("561 XP");
    expect(rows.slice(0, 3).map((r) => r.querySelector(".lb-medal")!.textContent)).toEqual(["🥇", "🥈", "🥉"]);
    expect(rows[3].querySelector(".site-board-pos")!.textContent).toBe("#4");
    // the podium: 2nd · 1st · 3rd, each with their rank Kiwi, the winner on the tallest step
    const pods = [...container.querySelectorAll(".lb-podium .lb-pod")];
    expect(pods.map((p) => p.querySelector(".lb-pod-name")!.textContent)).toEqual(["Second", "KiwiKing", "Third"]);
    expect(pods[1].className).toContain("lb-pod-1");
    expect(pods[1].querySelector(".lb-pod-val")!.textContent).toBe("561 XP");
    expect(pods.every((p) => p.querySelector(".rk-kiwi"))).toBe(true);
    expect(calls).toContain("GET /api/members/leaderboard?period=all&metric=score&limit=100");
    await act(async () => screen.getByRole("tab", { name: "This week" }).click());
    await act(async () => screen.getByRole("tab", { name: "Referrals" }).click());
    await act(async () => {
      await new Promise((res) => setTimeout(res, 0));
    });
    expect(calls).toContain("GET /api/members/leaderboard?period=week&metric=referrals&limit=100");
  });

  it("always shows the member's own place, from the server, even outside the top 100", async () => {
    serve({
      "/api/members/session": { signed_in: true, member: { id: "m", username: "me_kiwi", xp: 88, rank: scout } }, "/api/website/home": home,
      "/api/members/leaderboard": { period: "all", metric: "score", note: "", rows: [row(1, "KiwiKing", 561)],
                                    me: { username: "me_kiwi", rank: scout, position: 347, value: 88, hidden: false } },
    });
    const { container } = await show(<LeaderboardPage />);
    const mine = container.querySelector(".lb-me-row li")!;
    expect(mine.className).toContain("is-me");
    expect(mine.textContent).toContain("#347");
    expect(mine.textContent).toContain("me_kiwi You");
    expect(mine.textContent).toContain("88 XP");
  });

  it("shows your referral numbers on the referrals board", async () => {
    serve({
      "/api/members/session": { signed_in: true, member: { id: "m", username: "me_kiwi", xp: 88, rank: scout } }, "/api/website/home": home,
      "/api/members/leaderboard": { period: "all", metric: "referrals", note: "", rows: [row(1, "me_kiwi", 5)],
                                    me: { username: "me_kiwi", rank: scout, position: 1, value: 5, hidden: false } },
      "/api/members/me/referrals": { code: "kiwi123456", link: "http://127.0.0.1:5173/website/join/kiwi123456", joined: 7, qualified: 5,
                                     xp_earned: 125, reward_xp: 25, welcome_xp: 10, enabled: true, qualifying: ["Connected Telegram"] },
    });
    const { container } = await show(<LeaderboardPage />);
    await act(async () => screen.getByRole("tab", { name: "Referrals" }).click());
    await act(async () => {
      await new Promise((res) => setTimeout(res, 0));
    });
    expect(container.querySelector(".lb-mine")!.textContent).toContain("Joined7Qualified5XP earned125");
    expect(container.querySelector(".lb-list li")!.className).toContain("is-me"); // in the list: highlighted, not repeated
    expect(container.querySelector(".lb-list li")!.textContent).toContain("5 referrals");
    expect(container.querySelector(".lb-me-row")).toBeNull();
  });

  it("says when the board couldn't load (never 'nobody here yet') and tries again on request", async () => {
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/website/home": home });  // leaderboard: 404
    const { container } = await show(<LeaderboardPage />);
    expect(container.textContent).toContain("Couldn't load the leaderboard");
    expect(container.textContent).not.toContain("Nobody here yet");
    routes["/api/members/leaderboard"] = { period: "all", metric: "score", note: "", me: null, rows: [row(1, "KiwiKing", 561)] };
    await act(async () => screen.getByRole("button", { name: "Try again" }).click());
    await act(async () => {
      await new Promise((res) => setTimeout(res, 0));
    });
    expect(container.querySelector(".lb-list li")!.textContent).toContain("KiwiKing");
  });
});

describe("legal pages", () => {
  it("are marked as drafts and the privacy page explains how connected accounts are used", async () => {
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/website/home": home });
    const { container } = await show(<LegalPage kind="privacy" />);
    expect(screen.getByRole("note").textContent).toContain("Draft");
    expect(container.textContent).toContain("Connected accounts may be used to verify eligible Price Chat community activity");
    // honest about X: its token is kept so Reply / Repost work, and only a member's own press posts anything
    expect(container.textContent).toContain("we only post on X when you press Reply or Repost yourself");
    expect(container.textContent).not.toContain("we don't post, like or message anything");
    cleanup();
    for (const kind of ["terms", "disclaimer", "contact"] as const) {
      await show(<LegalPage kind={kind} />);
      expect(screen.getByRole("note").textContent).toContain("Draft");
      cleanup();
    }
  });

  it("states Kiwi VPN's price, manual renewal and refund rules, in force with the owner's facts still marked", async () => {
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/website/home": home });
    const terms = (await show(<LegalPage kind="vpnterms" />)).container.textContent!;
    expect(terms).toMatch(/\$9\.99 \(US dollars\) for 30 days/);
    expect(terms).toMatch(/Nothing renews or charges by itself/);
    expect(terms).toMatch(/added after any paid time or free trial/);
    expect(terms).toMatch(/Last updated \d+ \w+ 20\d\d/);
    expect(screen.queryByRole("note")).toBeNull();  // in force; a missing owner fact is left out, never shown (owner 2026-11-17)
    expect(terms).not.toMatch(/Stripe|credit card|Draft/i);
    cleanup();
    const refunds = (await show(<LegalPage kind="vpnrefunds" />)).container.textContent!;
    expect(refunds).toMatch(/can't reverse them/);
    expect(refunds).toMatch(/Check now/);
    expect(refunds).toMatch(/KV-/);
    expect(refunds).toMatch(/rights the law gives you/);
    expect(screen.queryByRole("note")).toBeNull();
  });

  it("Kiwi VPN terms name the provider, age, liability, law and the right to cancel, with every unknown fact a visible owner marker", async () => {
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/website/home": home });
    const { container } = await show(<LegalPage kind="vpnterms" />);
    const terms = container.textContent!;
    for (const h of ["Who provides Kiwi VPN", "Who can use it", "Your right to cancel (EU and UK)", "Ending", "Our liability", "Law and disputes"])
      expect(screen.getByRole("heading", { name: h })).toBeTruthy();
    expect(terms).toMatch(/18 or over, or the age of majority where you live/);
    expect(terms).toMatch(/legal where you are/);
    expect(terms).toMatch(/doesn't allow us to limit/);
    expect(terms).toMatch(/mandatory consumer laws of the country where you live/);
    expect(terms).toMatch(/within 14 days of paying/);
    // owner 2026-11-17: no placeholder is ever published; an owner fact not filled in is left out of the text
    expect(container.querySelector("mark.site-owner-marker")).toBeNull();
    expect(terms).not.toMatch(/\[OWNER|company number|VAT number|governed by the law of/);
    expect(terms).toMatch(/Kiwi VPN is a Price Chat service provided by the Price Chat team/);
    expect(terms).toMatch(/message us on Telegram or X \(see Contact\)/i);
    expect(terms).not.toMatch(/Avigad|Dubai|@pricechat\.live/);  // no business fact filled in by guesswork
    // site paths are links
    expect(container.querySelector('a[href="/vpn/refunds"]')).toBeTruthy();
    expect(container.querySelector('a[href="/vpn/acceptable-use"]')).toBeTruthy();
  });

  it("the refund policy explains the 14-day right to cancel, quotes the checkout box word for word, and has its anchor", async () => {
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/website/home": home });
    const { container } = await show(<LegalPage kind="vpnrefunds" />);
    const section = container.querySelector("section#right-to-cancel")!;
    expect(section).toBeTruthy();
    expect(section.textContent).toContain(START_NOW);  // the exact words the customer ticks (vpn.tsx)
    expect(section.textContent).toMatch(/14 days from the day you pay/);
    expect(section.textContent).toMatch(/the time you haven't used yet/);
    expect(section.textContent).toMatch(/we pay the network fee/);
    expect(section.textContent).toMatch(/KV-000123/);
    expect(section.querySelector("mark.site-owner-marker")).toBeNull();
    expect(section.textContent).toMatch(/To cancel, message us on Telegram or X \(see Contact\) within the 14 days/);
  });

  it("Kiwi VPN privacy names who resolves DNS and what the servers and hosts hold, with no absolute no-logs claim", async () => {
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/website/home": home });
    const privacy = (await show(<LegalPage kind="vpnprivacy" />)).container.textContent!;
    expect(screen.getByRole("heading", { name: "Your DNS lookups" })).toBeTruthy();
    expect(privacy).toMatch(/Cloudflare \(1\.1\.1\.1\) for WireGuard devices/);
    expect(privacy).toMatch(/hosting provider's \(Hetzner's\) resolvers for the browser extension/);
    expect(privacy).toMatch(/aren't encrypted/);
    expect(privacy).toMatch(/server's own resolver or passed to a public resolver/);
    expect(privacy).toMatch(/haven't yet verified that on every server, so we don't promise it/);  // owner 2026-11-15: no zero-DNS-log claim until verified
    expect(privacy).not.toMatch(/keeps no log/);
    expect(privacy).toMatch(/IP address it last connected from/);  // WireGuard's in-memory endpoint (audit I1)
    expect(privacy).toMatch(/last handshake/);
    expect(privacy).toMatch(/Railway's and Vercel's\) request logs record them, including your IP address/);  // audit L5
    expect(privacy).toMatch(/private \(incognito\) windows/);  // audit L17
    expect(privacy).not.toMatch(/We don't log|don't record the sites|your DNS lookups or the content|zero[- ]logs?|no[- ]logs/i);
  });

  it("Kiwi VPN privacy names the controller, legal bases, processors, retention from the code and how to use your rights", async () => {
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/website/home": home });
    const { container } = await show(<LegalPage kind="vpnprivacy" />);
    const privacy = container.textContent!;
    for (const h of ["Who is responsible for your data", "Why we use it", "Who else is involved", "How long we keep it", "Your rights", "Children"])
      expect(screen.getByRole("heading", { name: h })).toBeTruthy();
    expect(privacy).toMatch(/needed for our contract with you/);
    expect(privacy).toMatch(/legitimate interest/);
    expect(privacy).toMatch(/legal obligations under tax and accounting law/);
    for (const who of ["Hetzner", "Railway", "Vercel", "NOWPayments", "Resend", "Cloudflare", "Telegram"]) expect(privacy).toContain(who);
    // retention, as the code does it
    expect(privacy).toMatch(/removed after 30 days/);  // service.KEEP_LAPSED
    expect(privacy).toMatch(/settled and at least 7 days old/);  // billing.SCRUB_AFTER
    expect(privacy).toMatch(/check records: 30 days/);  // trial_guard.SIGNAL_DAYS
    expect(privacy).toMatch(/counted in memory only, for 24 hours/);
    expect(privacy).toMatch(/nearest 5 minutes/);  // service.ACTIVITY_STEP
    expect(privacy).toMatch(/never the address itself/);
    expect(privacy).toMatch(/ticked the box at checkout/);
    expect(privacy).toMatch(/copy of your data.*corrected or deleted.*portable/);
    expect(privacy).toMatch(/within one month/);
    expect(privacy).toMatch(/data protection authority/);
    expect(container.querySelector("mark.site-owner-marker")).toBeNull();
    expect(privacy).not.toMatch(/\[OWNER|For those transfers we rely on/);
    expect(privacy).toMatch(/for as long as tax and accounting law requires\./);
    expect(privacy).toMatch(/The Price Chat team decides how the personal data/);
  });

  it("Kiwi VPN acceptable use is in force, covering abuse, P2P, suspension, reporting and visibility", async () => {
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/website/home": home });
    const aup = (await show(<LegalPage kind="vpnaup" />)).container.textContent!;
    expect(screen.queryByRole("note")).toBeNull();  // no draft banner, and no owner facts missing
    expect(aup).not.toMatch(/Draft|isn't in force/);
    expect(aup).toMatch(/part of the Kiwi VPN terms/);
    expect(aup).toMatch(/Last updated/);
    expect(document.title).toBe("Kiwi VPN Acceptable Use · Price Chat");
    for (const h of ["Not allowed", "File sharing (P2P)", "What we can and can't see", "Abuse reports and suspension", "Reporting abuse"])
      expect(screen.getByRole("heading", { name: h })).toBeTruthy();
    expect(aup).toMatch(/Anything illegal/);
    expect(aup).toMatch(/Spam/);
    expect(aup).toMatch(/port scans/);
    expect(aup).toMatch(/isn't supported for now/);
    expect(aup).toMatch(/abuse report that we can verify/);
    expect(aup).toMatch(/still setting up a dedicated abuse address/);  // owner 2026-11-15: no address that doesn't exist
    expect(aup).toMatch(/Telegram or X \(see Contact\)/);
    expect(aup).not.toMatch(/@[\w-]+\.\w+|\[abuse contact/);  // no email address, no placeholder
    expect(aup).toMatch(/usually can't tell which account/);
    cleanup();
    const terms = (await show(<LegalPage kind="vpnterms" />)).container.textContent!;
    expect(terms).toContain("/vpn/acceptable-use");
    cleanup();
    await show(<LegalPage kind="terms" />);
    expect(screen.getByRole("note").textContent).toBe("Draft: this page has not had final legal review yet.");  // the community pages keep theirs
  });

  it("lists every owner fact still missing and where it shows, so nothing ships with a gap unnoticed", () => {
    const markers = OWNER_MARKERS.map((m) => m.marker);
    expect(markers).toEqual([
      "[OWNER: legal entity or trader name]", "[OWNER: postal address]", "[OWNER: business email for Kiwi VPN]",
      "[OWNER: company registration number, or remove if there is none]", "[OWNER: VAT number, or remove if not VAT-registered]",
      "[OWNER: governing-law country]", "[OWNER: courts with jurisdiction]", "[OWNER: whether the price includes VAT or other sales tax]",
      "[OWNER: how long tax law requires payment records to be kept]", "[OWNER: safeguard relied on for transfers outside the EU/UK, e.g. Standard Contractual Clauses]",
    ]);
    for (const m of OWNER_MARKERS) expect(m.pages.length).toBeGreaterThan(0);  // each one is on a page
    expect(OWNER_MARKERS.find((m) => m.marker.includes("business email"))!.pages).toEqual(["/vpn/privacy", "/vpn/terms", "/vpn/refunds"]);
  });
});

describe("rank art", () => {
  it("always renders the canonical Kiwi, dressed per rank around it (backdrop + gear), never redrawn", () => {
    const { container, rerender } = render(<RankKiwi art={art("none", "⚔️", "hand") as never} rankKey="warrior" size={120} />);
    expect(container.querySelector(".rk-kiwi")!.getAttribute("src")).toBe("/brand/kiwi.webp");
    expect(container.querySelectorAll(".rk img")).toHaveLength(1);  // one Kiwi, the canonical one
    expect(container.querySelector(".rkd-warrior .rkd-gear-back .rkd-sword")).not.toBeNull();  // sword on the back
    expect(container.querySelector(".rkd-gear-front .rkd-band")).not.toBeNull();  // headband
    expect(container.querySelector(".rk-slot")).toBeNull();  // no more emoji placeholders
    rerender(<RankKiwi art={art("none", "👑", "head") as never} rankKey="royal" size={40} />);
    expect(container.querySelector(".rk.is-small .rkd-crown")).not.toBeNull();  // gear stays readable at list size
    expect(container.querySelector(".rkd-rays")).toBeNull();  // effects don't
    rerender(<RankKiwi art={{ treatment: "none", overlay: "/brand/ranks/warrior.png", placeholder: "⚔️", slot: "hand" }} />);
    expect(container.querySelector(".rk-overlay")!.getAttribute("src")).toBe("/brand/ranks/warrior.png");
    rerender(<RankKiwi art={art("gold") as never} />);
    expect(container.querySelector(".rk-gold .rk-kiwi")!.getAttribute("src")).toBe("/brand/kiwi.webp");
  });
});

describe("legal pages publish no placeholders (owner, 2026-11-17)", () => {
  afterEach(cleanup);
  it.each(["privacy", "vpnprivacy", "vpnterms", "vpnrefunds", "vpnaup", "terms", "disclaimer", "contact"] as const)("%s", async (kind) => {
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/website/home": home });
    const text = (await show(<LegalPage kind={kind} />)).container.textContent!;
    expect(text).not.toMatch(/\[OWNER|OWNER:|Being finalised/);
    expect(text).not.toMatch(/ ,|\(\s*\)|\s\./);  // no holes left where a fact was taken out
  });
});
