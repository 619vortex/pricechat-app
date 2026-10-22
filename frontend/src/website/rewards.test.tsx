import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LearnPage, RankRewards, youtubeEmbed, type RewardCard, type Rewards } from "./rewards";
import { RealmTab } from "./realm";

const art = (treatment: string) => ({ treatment, overlay: null, placeholder: null, slot: null }) as RewardCard["rank"]["art"];
const RANKS: [string, string, number, RewardCard["reward"]["kind"], string][] = [
  ["egg", "The Egg", 0, "none", "Your Kiwi journey starts here"], ["chick", "Kiwi Chick", 50, "content", "BEGINNER CONTENT"],
  ["kiwi", "Kiwi", 150, "profile", "PROFILE CUSTOMIZATION"], ["scout", "Scout Kiwi", 400, "earn", "UNLOCK EARN — Your Kiwi Trading Bot"],
  ["warrior", "Warrior Kiwi", 900, "arena", "VORTEX REALM"], ["knight", "Knight Kiwi", 1800, "trading_credit", "$10 HYPERLIQUID TRADING REWARD"],
  ["royal", "Royal Kiwi", 3500, "vip", "WHATSAPP VIP COMMUNITY"], ["golden", "Golden Kiwi", 6000, "tba", "Exclusive reward — To Be Announced"],
  ["legend", "Kiwi Legend", 10000, "tba", "Exclusive reward — To Be Announced"], ["mythic", "Mythic Kiwi", 16000, "tba", "Exclusive reward — To Be Announced"],
];

function rewards(score: number, over: Partial<Rewards> = {}): Rewards {
  const level = RANKS.filter((r) => score >= r[2]).length;
  return {
    level, score, knight: { active: false, status: "NOT_CONFIGURED", amount_usd: 10, terms: "", regions: "", expires_at: null },
    profile: { display_name: null, accent: null, accents: ["green", "gold"], next_rename_at: null },
    ranks: RANKS.map(([key, name, min, kind, title], i) => ({
      rank: { level: i + 1, key, name, min_xp: min, description: "", art: art("none") }, reached: score >= min, current: i + 1 === level,
      unlocked: score >= min && kind !== "none" && kind !== "tba", reward: { kind, title, description: kind === "none" ? "Hatch your Kiwi to unlock your first exclusive content." : "", enabled: true },
      xp_to_reach: Math.max(0, min - score), progress: score >= min ? 1 : min ? score / min : 1, next_min_xp: null,
      ...(key === "knight" || key === "royal" ? { claim: null } : {}),
    })),
    ...over,
  };
}

const HOME = { sections: {}, latest: [], trending: [], whats_it_worth: [], originals: [], price_board: [], categories: [],
               links: { telegram: null, x: null, youtube: null }, site_url: null };
let calls: string[] = [];
function serve(routes: Record<string, [number, unknown]>) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input}`);
    const hit = routes[input.split("?")[0]] ?? [404, { detail: "nope" }];
    return new Response(JSON.stringify(hit[1]), { status: hit[0] });
  }));
}
async function settle() {
  for (let i = 0; i < 3; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Rank rewards on the Passport", () => {
  const tap = (container: HTMLElement, key: string) => fireEvent.click(container.querySelector(`#reward-${key} button`)!);
  const pop = (container: HTMLElement) => container.querySelector<HTMLElement>(".rw-pop");

  it("is one compact row of the 10 Kiwis with the progress right under it, and no reward cards", () => {
    const { container } = render(<RankRewards data={rewards(200)} reload={async () => {}} />);
    expect(container.querySelectorAll(".rw-timeline li")).toHaveLength(10);
    expect(container.querySelectorAll(".rw-timeline .rk")).toHaveLength(10);  // the official Kiwi artwork, never emoji stand-ins
    expect(container.querySelector(".rw-card, .rw-cards")).toBeNull();
    expect(pop(container)).toBeNull();  // nothing open until a Kiwi is tapped
    expect(container.querySelector(".rw-timeline li.is-current")!.textContent).toContain("Kiwi");
    expect(container.querySelector("#reward-scout")!.className).toContain("is-locked");
    expect(container.querySelector("#reward-chick")!.className).toContain("is-done");
    expect(container.querySelector(".rw-timeline + .rw-next")!.textContent).toBe("200 / 400 XP · 200 XP to Scout Kiwi");
  });

  it("opens a Kiwi's reward in a popover: name, XP, reward, status and its action", () => {
    const { container } = render(<RankRewards data={rewards(500)} reload={async () => {}} />);
    tap(container, "scout");
    const p = pop(container)!;
    expect(screen.getByRole("dialog", { name: "Scout Kiwi reward" })).toBe(p);
    expect(p.textContent).toContain("Scout Kiwi · 400 XP");
    expect(p.textContent).toContain("UNLOCK EARN — Your Kiwi Trading Bot");
    expect(p.querySelector(".rw-pill")!.textContent).toBe("✓ Unlocked");
    expect(p.querySelector<HTMLAnchorElement>("a.rw-btn")!.getAttribute("href")).toBe("/earn");
    expect(p.querySelector(".rk")).toBeTruthy();
    tap(container, "warrior");  // another Kiwi switches the popover
    expect(pop(container)!.textContent).toContain("VORTEX REALM");
    expect(pop(container)!.querySelector(".rw-pill")!.textContent).toBe("🔒 Locked");
    expect(pop(container)!.textContent).toContain("400 XP to go");
    expect(pop(container)!.querySelector(".rw-btn")).toBeNull();  // no action for a locked reward
    tap(container, "warrior");  // the same Kiwi again closes it
    expect(pop(container)).toBeNull();
  });

  it("closes on Escape (focus back on the Kiwi) and on a click outside", () => {
    const { container } = render(<div><p id="outside">x</p><RankRewards data={rewards(200)} reload={async () => {}} /></div>);
    tap(container, "kiwi");
    expect(pop(container)).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(pop(container)).toBeNull();
    expect(document.activeElement).toBe(container.querySelector("#reward-kiwi button"));
    tap(container, "kiwi");
    fireEvent.pointerDown(container.querySelector("#outside")!);
    expect(pop(container)).toBeNull();
    tap(container, "kiwi");
    fireEvent.pointerDown(pop(container)!);  // inside: stays open
    expect(pop(container)).toBeTruthy();
  });

  it("opens the reward a link points to (/passport#reward-warrior)", () => {
    history.replaceState(null, "", "#reward-warrior");
    const { container } = render(<RankRewards data={rewards(1000)} reload={async () => {}} />);
    expect(pop(container)!.textContent).toContain("VORTEX REALM");
    expect(pop(container)!.querySelector<HTMLAnchorElement>("a.rw-btn")!.getAttribute("href")).toBe("/passport#realm");
    history.replaceState(null, "", location.pathname);
  });

  it("marks a claimable reward on its Kiwi, and keeps the Knight reward pending until it's activated", async () => {
    const { container, rerender } = render(<RankRewards data={rewards(2000)} reload={async () => {}} />);
    expect(container.querySelector("#reward-knight .rw-dot")).toBeNull();
    tap(container, "knight");
    expect(pop(container)!.textContent).toContain("Pending activation");
    expect(pop(container)!.querySelector("button.rw-btn")).toBeNull();
    const active = rewards(2000, { knight: { active: true, status: "ACTIVE", amount_usd: 10, terms: "Once per member.", regions: "Not in the US.", expires_at: null } });
    rerender(<RankRewards data={active} reload={async () => {}} />);
    expect(container.querySelector("#reward-knight .rw-dot")).toBeTruthy();
    expect(pop(container)!.textContent).toContain("Claim now");
    expect(pop(container)!.textContent).toContain("Not in the US.");
    serve({ "/api/members/rewards/knight/claim": [200, { claim: { id: "c1", status: "PENDING" } }] });
    fireEvent.click(pop(container)!.querySelector("button.rw-btn")!);
    await settle();
    expect(calls).toContain("POST /api/members/rewards/knight/claim");
  });

  it("shows a server refusal from a claim inside the popover", async () => {
    serve({ "/api/members/rewards/royal/request": [403, { detail: "Reach Royal Kiwi (3,500 XP) to unlock this." }] });
    const { container } = render(<RankRewards data={rewards(3600)} reload={async () => {}} />);
    tap(container, "royal");
    fireEvent.click(pop(container)!.querySelector("button.rw-btn")!);
    await settle();
    expect(pop(container)!.querySelector("[role=alert]")!.textContent).toContain("Reach Royal Kiwi");
  });

  it("customizes the Passport from the Kiwi rank's popover", async () => {
    serve({ "/api/members/rewards/profile": [200, { display_name: "Kiwi Bob" }] });
    const { container } = render(<RankRewards data={rewards(200)} reload={async () => {}} />);
    tap(container, "kiwi");
    fireEvent.click(screen.getByText("Customize your Passport"));
    fireEvent.change(container.querySelector("#rw-dname")!, { target: { value: "Kiwi Bob" } });
    fireEvent.submit(container.querySelector(".rw-custom form")!);
    await settle();
    expect(calls).toContain("PATCH /api/members/rewards/profile");
    expect(screen.getByText("Display name saved ✓")).toBeTruthy();
  });

  it("shows To Be Announced for the top three", () => {
    const { container } = render(<RankRewards data={rewards(200)} reload={async () => {}} />);
    for (const k of ["golden", "legend", "mythic"]) {
      tap(container, k);
      expect(pop(container)!.textContent).toContain("Exclusive reward — To Be Announced");
      expect(pop(container)!.querySelector(".rw-pill")!.textContent).toBe("Coming later");
    }
  });
});

describe("Beginner content", () => {
  it("shows Coming soon until something is published", async () => {
    serve({ "/api/members/rewards/beginner": [200, { items: [], coming_soon: true }], "/api/website/home": [200, HOME] });
    render(<LearnPage />);
    await settle();
    expect(screen.getByText("Coming soon")).toBeTruthy();
  });

  it("explains the lock below Kiwi Chick", async () => {
    serve({ "/api/members/rewards/beginner": [403, { detail: "Reach Kiwi Chick (50 XP) to unlock this." }], "/api/website/home": [200, HOME] });
    render(<LearnPage />);
    await settle();
    expect(screen.getByText("Reach Kiwi Chick (50 XP) to unlock this.")).toBeTruthy();
  });

  it("embeds YouTube privately and leaves other links as links", () => {
    expect(youtubeEmbed("https://www.youtube.com/watch?v=abcDEF12345")).toBe("https://www.youtube-nocookie.com/embed/abcDEF12345");
    expect(youtubeEmbed("https://youtu.be/abcDEF12345")).toBe("https://www.youtube-nocookie.com/embed/abcDEF12345");
    expect(youtubeEmbed("https://vimeo.com/123")).toBeNull();
  });
});

describe("Vortex Realm tab", () => {
  const status = (unlocked: boolean) => ({
    unlocked, available: true, needs: { name: "Warrior Kiwi", min_xp: 900 },
    rank: { current: { level: 4, key: "scout", name: "Scout Kiwi", min_xp: 400, description: "", art: art("none") }, next: null, score: 500, xp_to_next: 400, progress: 0.2 },
  });

  it("is locked below Warrior Kiwi, with the progress towards it", async () => {
    serve({ "/api/members/realm": [200, status(false)] });
    const { container } = render(<RealmTab />);
    await settle();
    expect(container.textContent).toContain("It unlocks at Warrior Kiwi (900 XP)");
    expect(container.textContent).toContain("500 / 900 XP");
    expect(container.querySelector("iframe")).toBeNull();
    expect(screen.queryByText("▶ Play")).toBeNull();
  });

  it("only loads the game once the player presses Play", async () => {
    serve({ "/api/members/realm": [200, status(true)] });
    const { container } = render(<RealmTab />);
    await settle();
    expect(container.querySelector("iframe")).toBeNull();  // nothing downloaded yet
    fireEvent.click(screen.getByText("▶ Play"));
    expect(container.querySelector("iframe")!.getAttribute("src")).toBe("/api/members/realm/play/index.html");
    expect(screen.getByText("⛶ Full screen")).toBeTruthy();
  });

  it("asks a signed-out visitor to sign in", async () => {
    serve({ "/api/members/realm": [401, { detail: "Please sign in." }] });
    render(<RealmTab />);
    await settle();
    expect(screen.getByText("Sign in to your Kiwi Passport to play.")).toBeTruthy();
  });
});
