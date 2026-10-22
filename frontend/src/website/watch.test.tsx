import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WatchEarn } from "./watch";
import type { WatchVideo } from "./members";

/** Stands in for the YouTube IFrame Player API. The test decides when it plays. */
class FakePlayer {
  static all: FakePlayer[] = [];
  state = -1;
  destroyed = false;
  constructor(public el: HTMLElement, public opts: { videoId: string; host: string; events: { onError: (e: { data: number }) => void } }) {
    FakePlayer.all.push(this);
  }
  private pos = 0;
  private since = 0;
  getPlayerState() {
    return this.state;
  }
  /** Like the real player: the position advances only while playing. */
  getCurrentTime() {
    if (this.state === 1) {
      this.pos += (Date.now() - this.since) / 1000;
      this.since = Date.now();
    }
    return this.pos;
  }
  set playing(on: boolean) {
    this.getCurrentTime();
    this.since = Date.now();
    this.state = on ? 1 : 2;
  }
  seek(to: number) {
    this.pos = to;
  }
  playVideo() {
    this.playing = true;
  }
  destroy() {
    this.destroyed = true;
  }
}
const player = () => FakePlayer.all[FakePlayer.all.length - 1];

const video = (over: Partial<WatchVideo>): WatchVideo => ({
  external_id: "vid1", platform: "youtube", type: "short", title: "He Put His Allowance Into Bitcoin", url: "https://www.youtube.com/shorts/vid1",
  thumbnail: "https://i.ytimg.com/vi/vid1/hqdefault.jpg", duration_seconds: 45, published_at: "2026-10-05T10:00:00+00:00",
  xp: 3, status: "available", needed_seconds: 30, latest: true, ...over,
});
const list = (videos: WatchVideo[]) => ({ threshold_seconds: 30, videos });
const four = () => list([
  video({}),
  video({ external_id: "vid2", type: "long_form", title: "Is Gold Still Worth It?", url: "https://www.youtube.com/watch?v=vid2", duration_seconds: 612, xp: 1, latest: false }),
  video({ external_id: "vid3", title: "The $5 Coffee", xp: 1, latest: false }),
  video({ external_id: "vid4", title: "Rent vs Buy", xp: 1, latest: false, status: "completed" }),
]);

let routes: Record<string, unknown> = {};
let calls: { method: string; url: string; body: unknown }[] = [];

function serve(r: Record<string, unknown>) {
  routes = r;
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ method: init?.method ?? "GET", url, body: init?.body ? JSON.parse(String(init.body)) : null });
    const body = routes[url];
    const value = typeof body === "function" ? (body as () => unknown)() : body;
    return value === undefined ? new Response(JSON.stringify({ detail: "nope" }), { status: 404 }) : new Response(JSON.stringify(value), { status: 200 });
  }));
}
const posts = (path: string) => calls.filter((c) => c.method === "POST" && c.url === `/api/members/me/watch/${path}`);

async function tick(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function mount(reload = vi.fn(async () => null), rewardFrom?: { current: Element | null }) {
  const r = render(<div className="site"><WatchEarn reload={reload} rewardFrom={rewardFrom} /></div>);
  await tick();
  return { ...r, reload };
}

/** Records every sound the page plays (the files are never fetched in tests). */
let played: string[] = [];
class FakeAudio {
  src: string;
  volume = 1;
  currentTime = 0;
  preload = "";
  constructor(src: string) {
    this.src = src;
  }
  play() {
    played.push(this.src.replace("/website/sounds/", "").replace(".wav", ""));
    return Promise.resolve();
  }
}
const motionOn = () => {
  window.matchMedia = ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} })) as never;
};
/** Watch the featured video to its goal with the server answering `answer`. */
async function watchToGoal(answer: unknown) {
  routes["/api/members/me/watch/start"] = { ok: true, needed_seconds: 30 };
  routes["/api/members/me/watch/complete"] = answer;
  fireEvent.pointerDown(document.body); // the member has interacted: sounds may play from now on
  await act(async () => screen.getByRole("button", { name: "▶ Watch" }).click());
  await tick();
  player().playVideo();
  await tick(30_500); // exactly the goal (the first reading only sets the start position): the reward moment starts
}

beforeEach(() => {
  vi.useFakeTimers();
  played = [];
  localStorage.clear();
  vi.stubGlobal("Audio", FakeAudio);
  FakePlayer.all = [];
  window.YT = { Player: FakePlayer as never };
  window.matchMedia = ((q: string) => ({ matches: q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {} })) as never;
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete window.YT;
});

describe("Watch & Earn", () => {
  it("shows the latest video with its reward and the next three as thumbnails, without loading any player", async () => {
    serve({ "/api/members/me/watch": four() });
    const { container } = await mount();
    expect(screen.getByRole("heading", { name: "Watch & Earn" })).not.toBeNull();
    const feature = container.querySelector(".we-feature")!;
    expect(feature.querySelector(".we-kicker")!.textContent).toBe("Watch our latest Short+3 XP");
    expect(feature.querySelector(".we-title")!.textContent).toBe("He Put His Allowance Into Bitcoin");
    expect(feature.querySelector(".we-meta")!.textContent).toBe("0:45 · 5 Oct");
    expect(feature.querySelector(".we-goal")!.textContent).toBe("Watch 30 seconds here · +3 XP");
    expect(feature.querySelector(".we-poster")!.className).toContain("is-short"); // vertical for Shorts
    expect([...container.querySelectorAll(".we-item-title")].map((e) => e.textContent))
      .toEqual(["He Put His Allowance Into Bitcoin", "Is Gold Still Worth It?", "The $5 Coffee", "Rent vs Buy"]);
    expect([...container.querySelectorAll(".we-item-meta")].map((e) => e.textContent)).toEqual(["+3 XP5 Oct", "+1 XP5 Oct", "+1 XP5 Oct", "✓ Earned5 Oct"]);
    expect(container.querySelector(".we-item.is-active .we-item-now")!.textContent).toBe("Selected"); // the one in the player, in green
    expect(container.querySelector(".we-item.is-earned .we-item-title")!.textContent).toBe("Rent vs Buy");
    expect(container.querySelector("iframe")).toBeNull(); // thumbnails only until someone presses Watch
    expect(FakePlayer.all).toHaveLength(0);
    expect(feature.querySelector(".we-yt")!.getAttribute("href")).toBe("https://www.youtube.com/shorts/vid1"); // secondary, never automatic
  });

  it("plays inside Price Chat, counts only real playing time, and pays when the server says so", async () => {
    let paid = false;
    serve({
      "/api/members/me/watch": () => (paid ? list([video({ status: "completed" })]) : list([video({})])),
      "/api/members/me/watch/start": { ok: true, needed_seconds: 30 },
      "/api/members/me/watch/complete": () => {
        paid = true;
        return { ok: true, code: "paid", xp: 3, message: "Counted." };
      },
    });
    const open = vi.fn();
    vi.stubGlobal("open", open);
    const { container, reload } = await mount();
    await act(async () => screen.getByRole("button", { name: "▶ Watch" }).click());
    await tick();
    expect(posts("start")[0].body).toEqual({ external_id: "vid1" });
    expect(player().opts.videoId).toBe("vid1");
    expect(player().opts.host).toBe("https://www.youtube-nocookie.com");
    expect(container.querySelector(".we-player")!.className).toContain("is-short");

    await tick(5000); // loaded, but not playing yet: nothing counts
    expect(container.querySelector(".we-count")!.textContent).toBe("0 / 30 sec");
    player().playVideo();
    await tick(18_000);
    expect(container.querySelector(".we-count")!.textContent).toBe("18 / 30 sec");
    player().playing = false; // paused
    await tick(20_000);
    expect(container.querySelector(".we-count")!.textContent).toBe("18 / 30 sec");
    expect(posts("complete")).toHaveLength(0);
    player().seek(200); // skipping ahead doesn't count
    player().playing = true;
    await tick(12_500);
    expect(posts("complete")).toHaveLength(1);
    expect((posts("complete")[0].body as { played_seconds: number }).played_seconds).toBeGreaterThanOrEqual(30);
    await tick();
    await tick(1000); // the reward moment (reduced motion here: no flight) settles
    expect(container.querySelector(".we-done")!.textContent).toBe("✓ Completed");
    expect(container.querySelector(".we-kicker .is-earned")!.textContent).toBe("✓ Earned");
    expect(container.querySelector(".we-player")).not.toBeNull(); // the video keeps playing
    expect(reload).toHaveBeenCalled(); // the Passport above animates the XP in and adds the Recent XP row
    expect(open).not.toHaveBeenCalled(); // never sent to YouTube
    await tick(10_000);
    expect(posts("complete")).toHaveLength(1); // once
  });

  it("keeps asking while the server says it's too soon, and never shows XP it didn't award", async () => {
    let n = 0;
    serve({
      "/api/members/me/watch": list([video({})]),
      "/api/members/me/watch/start": { ok: true, needed_seconds: 30 },
      "/api/members/me/watch/complete": () => (n++ === 0 ? { ok: false, code: "too_soon", xp: 0, message: "Keep watching" }
                                                           : { ok: true, code: "refused", xp: 0, message: "Played here. No XP this time: today's video XP is used up." }),
    });
    const { container } = await mount();
    await act(async () => screen.getByRole("button", { name: "▶ Watch" }).click());
    await tick();
    player().playVideo();
    await tick(30_500);
    expect(posts("complete")).toHaveLength(1);
    expect(container.querySelector(".we-done")).toBeNull();
    await tick(2500);
    expect(posts("complete")).toHaveLength(2);
    expect(container.querySelector(".we-done")!.textContent).toBe("✓ Completed");
    expect(container.querySelector(".we-note")!.textContent).toContain("today's video XP is used up");
  });

  it("swaps a recent video into the player in place, one player at a time", async () => {
    serve({ "/api/members/me/watch": four(), "/api/members/me/watch/start": { ok: true, needed_seconds: 30 } });
    const { container } = await mount();
    await act(async () => screen.getByRole("button", { name: "▶ Watch" }).click());
    await tick();
    const first = player();
    await act(async () => (container.querySelectorAll(".we-item")[1] as HTMLButtonElement).click());
    await tick();
    expect(first.destroyed).toBe(true);
    expect(player().opts.videoId).toBe("vid2");
    expect(container.querySelector(".we-title")!.textContent).toBe("Is Gold Still Worth It?");
    expect(container.querySelector(".we-player")!.className).not.toContain("is-short"); // 16:9 for normal videos
    expect(container.querySelector(".we-kicker")!.textContent).toBe("Price Chat video+1 XP");
    expect(container.querySelector(".we-item.is-active .we-item-title")!.textContent).toBe("Is Gold Still Worth It?");
    expect(container.querySelector(".we-item.is-active .we-item-now")!.textContent).toBe("▶ Playing");
    expect(posts("start").map((c) => c.body)).toEqual([{ external_id: "vid1" }, { external_id: "vid2" }]);
    await act(async () => screen.getByRole("button", { name: "Next video" }).click());
    await tick();
    expect(player().opts.videoId).toBe("vid3");
    expect(container.querySelectorAll(".we-player")).toHaveLength(1);
  });

  it("is honest when the player can't count or the video can't be embedded", async () => {
    serve({ "/api/members/me/watch": four(), "/api/members/me/watch/start": { ok: true, needed_seconds: 30 } });
    const { container } = await mount();
    await act(async () => screen.getByRole("button", { name: "▶ Watch" }).click());
    await tick();
    act(() => player().opts.events.onError({ data: 150 })); // the owner doesn't allow playback elsewhere
    await tick();
    expect(container.querySelector(".we-note")!.textContent).toBe("This video can't play inside Price Chat. You can still watch it on YouTube.");
    expect(container.querySelector("a.pp-go")!.textContent).toBe("Open on YouTube"); // now the main way
    expect(container.querySelector(".pp-mcard-reward")).toBeNull();

    cleanup();
    delete window.YT;
    serve({ "/api/members/me/watch": four(), "/api/members/me/watch/start": { ok: true, needed_seconds: 30 } });
    const again = await mount();
    await act(async () => screen.getByRole("button", { name: "▶ Watch" }).click());
    await tick(9000);
    expect(again.container.querySelector(".we-player iframe")!.getAttribute("src"))
      .toBe("https://www.youtube-nocookie.com/embed/vid1?autoplay=1&playsinline=1&rel=0");
    expect(again.container.querySelector(".we-note")!.textContent).toBe("This browser blocked the player's progress, so this play can't earn XP.");
    expect(posts("complete")).toHaveLength(0);
  });

  it("shows nothing when there are no videos", async () => {
    serve({ "/api/members/me/watch": list([]) });
    const { container } = await mount();
    expect(container.querySelector(".we")).toBeNull();
  });
});

describe("Watch & Earn reward moment", () => {
  it("plays the reward once, only on the server's paid answer: sound, rising +XP, then hands off to the Passport", async () => {
    motionOn();
    let paid = false;
    serve({ "/api/members/me/watch": () => list([video({ status: paid ? "completed" : "available" })]) });
    const rewardFrom = { current: null as Element | null };
    let flewFrom: string | null = null;
    const reload = vi.fn(async () => {
      paid = true;
      flewFrom = rewardFrom.current?.textContent ?? null; // what the Passport's flying chip starts from
      return null;
    });
    const { container } = await mount(reload, rewardFrom);
    await watchToGoal({ ok: true, code: "paid", xp: 3, message: "Counted." });
    expect(played).toEqual(["tick", "xp"]); // one tiny tick at halfway, one ding at the goal: nothing while watching
    const reward = container.querySelector(".we-reward")!;
    expect(reward.textContent).toBe("+3 XP");
    expect(reward.className).toContain("is-rise");
    expect(container.querySelector(".we-feature")!.className).toContain("is-earning");
    expect(container.querySelector(".we-progress")!.className).toContain("is-full");
    expect(container.querySelector(".we-count")!.textContent).toBe("30 / 30 sec");
    await tick(450);
    expect(flewFrom).toBe("+3 XP");
    expect(container.querySelector(".we-reward")!.className).toContain("is-fly");
    await tick(800);
    expect(container.querySelector(".we-reward")).toBeNull();
    expect(container.querySelector(".we-done")!.textContent).toBe("✓ Completed");
    expect(container.querySelector(".we-kicker .is-earned")!.textContent).toBe("✓ Earned");
    expect(rewardFrom.current).toBeNull(); // used once
    await tick(20_000);
    expect(played).toEqual(["tick", "xp"]);
    expect(posts("complete")).toHaveLength(1);
  });

  it("never replays for a video already earned: no reward, no sound, no second claim", async () => {
    serve({ "/api/members/me/watch": list([video({ status: "completed" })]) });
    const { container } = await mount();
    expect(container.querySelector(".we-kicker .is-earned")!.textContent).toBe("✓ Earned");
    expect(container.querySelector(".we-done")!.textContent).toBe("✓ Completed");
    await watchToGoal({ ok: true, code: "paid", xp: 3, message: "Counted." });
    expect(posts("complete")).toHaveLength(0);
    expect(container.querySelector(".we-reward")).toBeNull();
    expect(played).toEqual([]);
    expect(container.querySelector(".we-player")).not.toBeNull(); // it can still be watched
  });

  it("no XP from the server means no reward moment", async () => {
    serve({ "/api/members/me/watch": list([video({})]) });
    const { container } = await mount();
    await watchToGoal({ ok: true, code: "refused", xp: 0, message: "Played here. No XP this time: today's XP limit is reached." });
    await tick(2000);
    expect(container.querySelector(".we-reward")).toBeNull();
    expect(played).toEqual(["tick"]);
    expect(container.querySelector(".we-kicker .is-earned")).toBeNull();
    expect(container.querySelector(".we-note")!.textContent).toContain("today's XP limit is reached");
  });

  it("has a small sound switch, remembered in this browser", async () => {
    serve({ "/api/members/me/watch": list([video({})]) });
    const first = await mount();
    const toggle = screen.getByRole("button", { name: /Sound on/ });
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    await act(async () => toggle.click());
    expect(screen.getByRole("button", { name: /Sound off/ }).getAttribute("aria-pressed")).toBe("false");
    expect(localStorage.getItem("pc-sound")).toBe("off");
    await watchToGoal({ ok: true, code: "paid", xp: 3, message: "Counted." });
    await tick(2000);
    expect(played).toEqual([]); // silent, but the reward is still shown
    expect(first.container.querySelector(".we-kicker .is-earned")!.textContent).toBe("✓ Earned");
    cleanup();
    await mount();
    expect(screen.getByRole("button", { name: /Sound off/ })).not.toBeNull();
  });

  it("with reduced motion: no flight, just +XP then ✓ Earned", async () => {
    serve({ "/api/members/me/watch": list([video({})]) }); // matchMedia says "reduce" (beforeEach)
    const rewardFrom = { current: null as Element | null };
    let from: Element | null | undefined;
    const reload = vi.fn(async () => {
      from = rewardFrom.current;
      return null;
    });
    const { container } = await mount(reload, rewardFrom);
    await watchToGoal({ ok: true, code: "paid", xp: 3, message: "Counted." });
    expect(container.querySelector(".we-reward")!.textContent).toBe("+3 XP");
    await tick(100);
    expect(from).toBeNull(); // nothing to fly from: the Passport just updates its numbers
    await tick(1000);
    expect(container.querySelector(".we-kicker .is-earned")!.textContent).toBe("✓ Earned");
  });
});
