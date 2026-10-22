import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useCountUp, useFirstTime } from "./motion";
import { ProfilePage } from "./passport";
import { LeaderboardPage } from "./community";
import { Website } from "./Website";
import { RankKiwi } from "./RankKiwi";
import { RANK_ASSETS } from "./rankAssets";
import { refreshSession } from "./members";

let reduce = false;
let routes: Record<string, unknown | (() => unknown)> = {};

function serve(r: typeof routes) {
  routes = r;
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const v = routes[input.split("?")[0]];
    const body = typeof v === "function" ? (v as () => unknown)() : v;
    return body === undefined ? new Response("{}", { status: 404 }) : new Response(JSON.stringify(body), { status: 200 });
  }));
}

async function flush(n = 3) {
  for (let i = 0; i < n; i++)
    await act(async () => {
      await new Promise((res) => setTimeout(res, 0));
    });
}

class IO {
  static instances: IO[] = [];
  constructor(public cb: (e: { isIntersecting: boolean; target: Element }[]) => void) {
    IO.instances.push(this);
  }
  els: Element[] = [];
  observe(el: Element) {
    this.els.push(el);
  }
  unobserve() {}
  disconnect() {}
  fire() {
    this.cb(this.els.map((target) => ({ isIntersecting: true, target })));
  }
}

beforeEach(() => {
  reduce = false;
  localStorage.clear();
  sessionStorage.clear();
  refreshSession();
  IO.instances = [];
  vi.stubGlobal("IntersectionObserver", IO);
  window.matchMedia = ((q: string) => ({ matches: q.includes("reduce") ? reduce : false, media: q, addEventListener() {}, removeEventListener() {} })) as never;
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const egg = { level: 1, key: "egg", name: "The Egg", min_xp: 0, description: "", art: { treatment: "egg", overlay: null, placeholder: null, slot: null } };
const chick = { ...egg, level: 2, key: "chick", name: "Kiwi Chick", min_xp: 50, art: { ...egg.art, treatment: "chick" } };
const home = { sections: {}, latest: [], trending: [], whats_it_worth: [], originals: [], price_board: [], categories: [],
               links: { telegram: null, x: null, youtube: null }, site_url: null };

function passport(xp: number, missionStatus: string) {
  const missions = [{ id: 7, key: "k", kind: "open_link", cadence: "once", title: "Read the latest update", description: "", platform: "website", xp: 3,
                      verification: "verified_click", verification_label: "Verified as an open only", verification_method: "", status: missionStatus,
                      frequency: "Once", cta: missionStatus === "completed" ? null : { label: "Open", action: "open", url: "https://pricechat.example/u" },
                      target: {}, goal: 1, progress: 0, expires_at: null, completed_at: null, xp_awarded: missionStatus === "completed" ? 3 : 0 }];
  return {
    missions, today: missionStatus === "completed" ? [] : missions, more_missions: [],
    member: { id: "m", username: "Kiwi1", display_name: "Kiwi1", username_chosen: true, joined_at: "2026-10-04T10:00:00+00:00", xp,
              bonus_xp: 0, leaderboard_opt_out: false, xp_suspended: false },
    rank: { current: egg, next: chick, xp_to_next: 50 - xp, progress: xp / 50, score: xp },
    linked: [], badges: [], entitlements: [], recent: [], notifications: { unread: 0, items: [] }, rank_up: null,
  };
}

describe("motion system", () => {
  it("counts numbers only when they change, and instantly with reduced motion", async () => {
    const { result, rerender } = renderHook(({ v }) => useCountUp(v, 50), { initialProps: { v: 10 } });
    expect(result.current).toBe(10); // first paint: no animation
    rerender({ v: 20 });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 120));
    });
    expect(result.current).toBe(20);
    reduce = true;
    rerender({ v: 30 });
    expect(result.current).toBe(30);
  });

  it("plays the passport entrance once per visit, never with reduced motion", () => {
    expect(renderHook(() => useFirstTime("k1")).result.current).toBe(true);
    expect(renderHook(() => useFirstTime("k1")).result.current).toBe(false);
    reduce = true;
    expect(renderHook(() => useFirstTime("k2")).result.current).toBe(false);
  });

  it("turns scroll reveals on only when motion is allowed", async () => {
    serve({ "/api/website/home": home, "/api/members/session": { signed_in: false, member: null } });
    const { container, unmount } = render(<Website />);
    await flush();
    expect(container.querySelector(".site")!.classList.contains("site-reveal-on")).toBe(true);
    unmount();
    reduce = true;
    const second = render(<Website />);
    await flush();
    expect(second.container.querySelector(".site")!.classList.contains("site-reveal-on")).toBe(false);
    expect(second.container.querySelector("#story")!.className).toContain("is-playing"); // reduced motion: shown at once
  });

  it("starts the Story timeline when its heading comes into view", async () => {
    serve({ "/api/website/home": home, "/api/members/session": { signed_in: false, member: null } });
    const { container } = render(<Website />);
    await flush();
    const story = container.querySelector("#story")!;
    expect(story.className).not.toContain("is-playing");
    expect(story.querySelector(".site-horizon-line")!.className).toContain("price-line");
    await act(async () => IO.instances.forEach((io) => io.fire()));
    expect(story.className).toContain("is-playing");
  });
});

describe("XP feedback", () => {
  it("shows +XP on the mission that earned it, keeps the finished card in place and draws the check", async () => {
    let call = 0;
    serve({
      "/api/members/session": { signed_in: true, member: { id: "m", username: "Kiwi1", xp: 42, rank: egg } },
      "/api/members/me": () => (call++ === 0 ? passport(42, "available") : passport(45, "completed")),
      "/api/members/missions/7/open": { status: "completed", xp: 3 },
      "/api/members/rules": { rules: [] }, "/api/members/ranks": { ranks: [egg, chick] }, "/api/website/home": home,
    });
    vi.stubGlobal("open", vi.fn());
    const { container } = render(<ProfilePage />);
    await flush();
    await act(async () => screen.getByRole("button", { name: /^Open$/ }).click());
    await flush();
    const card = container.querySelector("#mission-7")!;
    expect(card.className).toContain("is-success");
    expect(container.querySelectorAll(".pp-mcard").length).toBe(1); // stays in place this visit (the server no longer lists it)
    expect(card.querySelector(".pp-go-done")!.textContent).toContain("+3 XP");
    expect(card.querySelector("svg.pp-go-check")).not.toBeNull();
  });
});

describe("leaderboard movement", () => {
  it("shows gentle up/down movement since the last look, nothing the first time", async () => {
    const row = (u: string, pos: number) => ({ position: pos, username: u, value: 100 - pos, rank_score: 100 - pos, xp: 1, legacy_points: 0, rank: egg });
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/website/home": home,
            "/api/members/leaderboard": { rows: [row("a", 1), row("b", 2)], note: "" } });
    const first = render(<LeaderboardPage />);
    await flush();
    expect(first.container.querySelector(".lb-move")).toBeNull();
    first.unmount();
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/website/home": home,
            "/api/members/leaderboard": { rows: [row("b", 1), row("a", 2)], note: "" } });
    const second = render(<LeaderboardPage />);
    await flush();
    const moves = [...second.container.querySelectorAll(".lb-move")].map((e) => e.textContent);
    expect(moves).toEqual(["↑ 1", "↓ 1"]);
  });
});

describe("rank artwork slots", () => {
  it("uses the official rank artwork as soon as its slot is filled", () => {
    const { container, rerender } = render(<RankKiwi art={egg.art as never} rankKey="warrior" />);
    expect(container.querySelector(".rk-kiwi")!.getAttribute("src")).toBe("/brand/kiwi.webp"); // stand-in until then
    RANK_ASSETS.warrior = "/brand/ranks/warrior.webp";
    rerender(<RankKiwi art={egg.art as never} rankKey="warrior" />);
    expect(container.querySelector(".rk-asset")!.getAttribute("src")).toBe("/brand/ranks/warrior.webp");
    expect(container.querySelector(".rk-kiwi")).toBeNull();
    RANK_ASSETS.warrior = null;
  });
});
