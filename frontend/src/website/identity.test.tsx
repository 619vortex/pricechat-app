import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { type Identity, type PBadge, PassportCard, PassportInvite, ProfileTab, type PublicV2 } from "./identity";

/** TEST FIXTURES: a made-up member, badges and activity. */
const badge = (key: string, over: Partial<PBadge> = {}): PBadge => ({
  key, name: key.replace(/_/g, " "), description: "", family: "community", family_name: "Community", color: "#3fa9f5", tier: "bronze", title: null,
  earned: true, awarded_at: "2026-11-01T00:00:00+00:00", serial: null, featured_slot: null, rarity: { holders: 3, members: 72, share: 0.04, label: "Epic" }, ...over,
});
const founding = badge("founding_kiwi", { name: "Founding Kiwi No. 014", family: "founders", family_name: "Founders", tier: "mythic", title: "Founding Kiwi",
  serial: 14, limit: { cap: 100, until: "2027-01-01T00:00:00+00:00", claimed: 72 } });
const regular = badge("community_regular", { name: "Community Regular", tier: "silver", title: "Telegram Regular" });
const locked = badge("hot_hand", { name: "Hot Hand", family: "predictor", family_name: "Predictor", tier: "mythic", earned: false, awarded_at: null,
  rarity: { holders: 0, members: 72, share: 0, label: "Unclaimed" } });
const rank = { level: 6, key: "knight", name: "Knight Kiwi", min_xp: 1800, description: "", art: { treatment: "", overlay: null, placeholder: true, slot: "" } } as unknown as PublicV2["rank"];
const pub = (over: Partial<PublicV2> = {}): PublicV2 => ({
  v: 2, username: "kiwiwhale", joined_at: "2026-10-04T00:00:00+00:00", rank, display_name: null, accent: "green", title: null, bio: null, featured: [],
  badge_count: 2, sections: { community: true, predictions: true, streak: true, platforms: true, badges: true }, card: "/api/members/public/kiwiwhale/card.png",
  collection: [founding, regular], community: { score: 412, level: "Regular" }, streak: { current: 12, best: 18 }, platforms: ["telegram", "x"],
  invite: "/join/kiwi123456", ...over,
});
const ident = (over: Partial<Identity> = {}): Identity => ({
  public_profile: false, invited: false, title_badge: null, bio: "", accent: "green", accents: ["green", "gold", "ice", "ember", "violet"], accent_unlocked: true,
  featured: [], sections: { community: true, predictions: true, streak: true, platforms: true, badges: true }, ref_link: false,
  titles: [{ badge: "founding_kiwi", title: "Founding Kiwi No. 014" }, { badge: "community_regular", title: "Telegram Regular" }],
  collection: [founding, regular, locked], community: { score: 412, level: { name: "Regular", floor: 200, next_at: 500, next_name: "Pillar" }, window_days: 90,
    parts: [{ key: "active_days", label: "Days taking part in the Telegram", count: 30, points: 300 }, { key: "discussion", label: "Real conversation", count: 112, points: 112 }], penalty: 0 },
  preview: pub(), share_url: "/u/kiwiwhale", invite: null, rules: { bio_max: 80, featured_slots: 3, score_window_days: 90 }, ...over,
});

let calls: string[] = [];
function serve(reply: (path: string, body: unknown) => unknown) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input}${init?.body ? " " + String(init.body) : ""}`);
    return new Response(JSON.stringify(reply(String(input), init?.body ? JSON.parse(String(init.body)) : null)), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 10)); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Kiwi Passport 2.0", () => {
  it("the public card shows only the sections the member chose, and never a handle", () => {
    render(<PassportCard p={pub({ title: "Founding Kiwi No. 014", featured: [founding], sections: { community: true, predictions: true, streak: false, platforms: true, badges: false } })} />);
    const card = screen.getByRole("article");
    expect(within(card).getByText("Founding Kiwi No. 014", { selector: ".pc2-title" })).toBeTruthy();
    expect(card.textContent).toContain("No. 014 of 100");  // a numbered badge shows its number, not a rarity word
    expect(card.textContent).toContain("412");
    expect(card.textContent).not.toContain("Day streak");
    expect(card.querySelector(".pc2-case")).toBeNull();
    expect(within(card).getByText("Telegram")).toBeTruthy();
    expect(within(card).getByRole("link", { name: /kiwiwhale's invite/ }).getAttribute("href")).toBe("/join/kiwi123456");
  });

  it("the invitation asks once, with a live preview, and either answer is final", async () => {
    serve(() => ident({ invited: true, public_profile: true }));
    const done = vi.fn();
    const { container } = render(<PassportInvite data={ident()} onAnswered={done} />);
    expect(container.querySelector(".pc2-invite-card article")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Make my Passport public" }));
    await settle();
    expect(calls).toContain('POST /api/members/passport2/invite {"action":"accept"}');
    expect(done.mock.calls[0][0].public_profile).toBe(true);
    cleanup();
    const { container: again } = render(<PassportInvite data={ident({ invited: true })} onAnswered={done} />);
    expect(again.innerHTML).toBe("");  // never shown again
  });

  it("the editor previews choices live before saving, caps featured badges and saves in one request", async () => {
    serve((_path, body) => ident({ title_badge: "founding_kiwi", bio: (body as { bio: string }).bio, featured: ["founding_kiwi"] }));
    render(<ProfileTab data={ident()} reload={async () => {}} />);
    const stage = () => document.querySelector(".pc2-stage article") as HTMLElement;
    fireEvent.change(screen.getByLabelText("Title under your name"), { target: { value: "founding_kiwi" } });
    fireEvent.change(screen.getByLabelText("One line about you"), { target: { value: "Calls the close every day." } });
    expect(within(stage()).getByText("Founding Kiwi No. 014", { selector: ".pc2-title" })).toBeTruthy();  // before any save
    expect(stage().textContent).toContain("Calls the close every day.");
    fireEvent.click(within(screen.getByRole("group", { name: /Featured badges/ })).getByRole("button", { name: /Founding Kiwi No. 014/ }));
    expect(stage().querySelector(".pc2-feat")?.textContent).toContain("No. 014 of 100");
    fireEvent.click(within(document.querySelector(".pc2-collection") as HTMLElement).getByRole("button", { name: /Hot Hand/ }));
    expect(screen.getByRole("status").textContent).toContain("Not earned yet");
    fireEvent.click(screen.getByRole("checkbox", { name: /Daily streak/ }));
    expect(stage().textContent).not.toContain("Day streak");
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await settle();
    const patch = calls.find((c) => c.startsWith("PATCH /api/members/passport2"))!;
    expect(JSON.parse(patch.slice(patch.indexOf("{")))).toMatchObject({ title_badge: "founding_kiwi", bio: "Calls the close every day.", featured: ["founding_kiwi"],
      sections: { streak: false } });
    expect(screen.getByText("Saved ✓")).toBeTruthy();
  });

  it("explains the community score and that packs and money never count", () => {
    render(<ProfileTab data={ident()} reload={async () => {}} />);
    const cs = document.querySelector(".pc2-community") as HTMLElement;
    expect(cs.textContent).toContain("412");
    expect(cs.textContent).toContain("88 to Pillar");
    expect(cs.textContent).toMatch(/Packs, purchases, deposits, trading and sign-ins never count/);
    expect(cs.textContent).toContain("doesn't change your XP, rank or rewards");
  });

  it("locks the frame colours below Kiwi rank", () => {
    render(<ProfileTab data={ident({ accent_unlocked: false })} reload={async () => {}} />);
    expect((screen.getByRole("button", { name: "Gold" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Frames unlock at Kiwi rank/)).toBeTruthy();
  });
});
