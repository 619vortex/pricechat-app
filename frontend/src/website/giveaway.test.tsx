import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { refreshSession } from "./members";

const played: string[] = [];
vi.mock("./sound", () => ({ playSound: (n: string) => played.push(n), warmSounds: () => undefined }));

import { GiveawayPage } from "./giveaway";

/** TEST FIXTURES only: a fake API answering by path. */
let routes: Record<string, unknown> = {};
const calls: string[] = [];
function serve(r: Record<string, unknown>) {
  routes = r;
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input.split("?")[0]}`);
    const body = routes[`${init?.method ?? "GET"} ${input.split("?")[0]}`] ?? routes[input.split("?")[0]];
    return body === undefined ? new Response(JSON.stringify({ detail: "nope" }), { status: 404 }) : new Response(JSON.stringify(body), { status: 200 });
  }));
}
const flush = async (n = 4) => { for (let i = 0; i < n; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };

const pack = (key: string, name: string) => ({ key, name, set: name, year: 1999, tagline: "t", theme: ["#000", "#111", "#222"], mascot: "Charizard",
  cover: null, size: 102, chase: 4, owned: 3 });
const shelf = (left: number | null) => ({ card_back: "https://img.test/back.png", packs: [pack("base", "Base Set"), pack("fossil", "Fossil")], per_day: null, opens_left: null, xp_per_day: 3,
  xp_packs_left: left, level_packs: 0, rank_level: left === null ? null : 1, resets_at: "2099-01-01T00:00:00+00:00", signed_in: left !== null });
const card = (i: number, tier: string) => ({ id: i, name: `Card ${i}`, set: "Base Set", number: `${i}/102`, year: 1999, rarity: tier, edition: "Unlimited",
  variant: "", image: null, tier, value: 1000 * i, new: i === 5 });
const site = { "/api/website/home": { sections: {}, latest: [], trending: [], whats_it_worth: [], originals: [], price_board: [], categories: [], links: {}, site_url: null } };

beforeEach(() => {
  played.length = 0;
  localStorage.clear();
  refreshSession();
  window.matchMedia = ((q: string) => ({ matches: q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {} })) as never;
  window.scrollTo = () => undefined;
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Pack Giveaway", () => {
  it("always says plainly that it's a virtual, no-money test, and asks signed-out visitors to sign in", async () => {
    serve({ ...site, "/api/members/session": { signed_in: false, member: null }, "/api/members/packs": shelf(null) });
    render(<MemoryRouter><GiveawayPage /></MemoryRouter>);
    await flush();
    const warning = screen.getByRole("note").textContent!;
    expect(warning).toContain("test feature");
    expect(warning).toContain("No real money");
    expect(warning).toContain("don't win real cards or prizes");
    expect(screen.getByRole("button", { name: "Sign in to open packs" })).toBeTruthy();
    expect(screen.getAllByRole("option")).toHaveLength(2);
  });

  it("opens a pack: rip, then the cards flip one by one with sounds; a holo shines, the ultra gets the fanfare; then XP", async () => {
    serve({
      ...site, "/api/members/session": { signed_in: true, member: { id: "m1", username: "fan", xp: 10, rank: { level: 1, key: "egg", name: "The Egg", min_xp: 0, description: "", art: { treatment: "egg", overlay: null, placeholder: null, slot: null } } } }, "/api/members/packs": shelf(3),
      "/api/members/packs/binder": { cards: [], unique: 0, pulls: 0, value: 0 },
      "POST /api/members/packs/base/open": { pack: "base", cards: [card(1, "common"), card(2, "common"), card(3, "uncommon"), card(4, "holo"), card(5, "ultra")],
                                             xp: 5, bonus: false, xp_packs_left: 2, level_packs: 0, resets_at: "2099-01-01T00:00:00+00:00", value: 15000, best: 4 },
    });
    const { container } = render(<MemoryRouter><GiveawayPage /></MemoryRouter>);
    await flush();
    expect(container.querySelector(".gw-left")!.textContent).toContain("+5 Kiwi XP every pack");
    await act(async () => { screen.getByRole("button", { name: "Open Base Set" }).click(); });
    await flush();
    expect(calls).toContain("POST /api/members/packs/base/open");
    expect(played[0]).toBe("rip");
    const cards = () => [...container.querySelectorAll<HTMLButtonElement>(".gw-card")];
    expect(cards()).toHaveLength(5);
    expect(container.querySelector<HTMLElement>(".gw-stage")!.style.getPropertyValue("--back")).toContain("img.test/back.png");  // the real card back
    expect(cards()[4].className).toContain("is-hit");  // the hit card glows before it's turned
    for (let i = 0; i < 5; i++) { await act(async () => { cards()[i].click(); }); await flush(2); }
    expect(played.slice(1, 8)).toEqual(["swish", "swish", "swish", "swish", "shine", "swish", "chase"]);  // every flip swishes; holo shines; chase fanfare
    await act(async () => { await new Promise((r) => setTimeout(r, 2600)); });
    await flush();
    expect(played).toContain("xp");
    expect(container.querySelector(".gw-xp")!.textContent).toBe("+5 Kiwi XP");
    expect(container.querySelector(".gw-total")!.textContent).toContain("$150");
    expect(container.querySelector(".gw-total")!.textContent).toContain("not money, not real cards");
    expect(screen.getByRole("button", { name: /Open another/ })).toBeTruthy();
    // share my pull: shown automatically with the best card and its USD value; one tap to Telegram or X; says it's virtual
    const share = container.querySelector(".gw-sharecard")!;
    expect(share.className).toContain("tier-ultra");
    expect(share.querySelector(".gw-sharecard-name")!.textContent).toBe("Card 5");  // the most valuable card
    expect(share.querySelector(".gw-sharecard-usd")!.textContent).toBe("$50.00");
    expect(share.textContent).toContain("Whole pack $150");
    const opened: string[] = [];
    vi.stubGlobal("open", (u: string) => { opened.push(u); return null; });
    await act(async () => { screen.getByRole("button", { name: /Share on Telegram/ }).click(); });
    expect(opened[0]).toMatch(/^https:\/\/t\.me\/share\/url\?/);
    const text = decodeURIComponent(opened[0]);
    expect(text).toContain("CHASE PULL");
    expect(text).toContain("Card 5 worth $50.00");
    expect(text).toContain("Whole pack: $150");
    expect(text).toContain("virtual Base Set pack");
    expect(text).toContain("https://pricechat.live/giveaway");
    await act(async () => { screen.getByRole("button", { name: /Post/ }).click(); });
    expect(opened[1]).toMatch(/^https:\/\/x\.com\/intent\/post\?/);
    vi.unstubAllGlobals();
  });

  it("announces a rare 1st Edition pack, shows floored prices as \"at least\", and lets the member show pulls in their portfolio", async () => {
    const first = (i: number, tier: string) => ({ ...card(i, tier), edition: "1st Edition" });
    serve({
      ...site, "/api/members/session": { signed_in: true, member: { id: "m1", username: "fan", xp: 10, rank: { level: 1, key: "egg", name: "The Egg", min_xp: 0, description: "", art: { treatment: "egg", overlay: null, placeholder: null, slot: null } } } },
      "/api/members/packs": shelf(3),
      "/api/members/packs/binder": { cards: [card(9, "rare")], unique: 1, pulls: 1, value: 900, priced: 1, best: null, in_portfolio: false },
      "POST /api/members/packs/base/open": { pack: "base", print: "1st Edition", bonus: false,
                                             cards: [first(1, "common"), first(2, "common"), first(3, "uncommon"), first(4, "rare"), { ...first(5, "ultra"), value: 3514, value_floor: true }],
                                             xp: 5, xp_packs_left: 2, level_packs: 0, resets_at: "2099-01-01T00:00:00+00:00", value: 13514, best: 4 },
      "POST /api/members/packs/portfolio": { in_portfolio: true },
    });
    const { container } = render(<MemoryRouter><GiveawayPage /></MemoryRouter>);
    await flush();
    await act(async () => { screen.getByRole("button", { name: "Open Base Set" }).click(); });
    await flush(6);
    expect(played).toContain("first");  // the rare print gets its own sound...
    expect(container.querySelector(".gw-print-badge")!.textContent).toContain("1st Edition pack");  // ...and a badge over the cards
    expect(container.querySelector(".gw-stage")!.className).toContain("is-firstpack");
    const cards = () => [...container.querySelectorAll<HTMLButtonElement>(".gw-card")];
    for (let i = 0; i < 5; i++) { await act(async () => { cards()[i].click(); }); await flush(2); }
    await act(async () => { await new Promise((r) => setTimeout(r, 2600)); });
    await flush();
    expect([...container.querySelectorAll(".gw-pulled em")].pop()!.textContent).toBe("$35.14+");  // at least the Unlimited price
    expect(container.querySelector(".gw-total")!.textContent).toContain("$135+");
    const optin = container.querySelector<HTMLInputElement>(".gw-optin input")!;
    expect(optin.checked).toBe(false);  // off until the member opts in
    await act(async () => { optin.click(); });
    await flush();
    expect(calls).toContain("POST /api/members/packs/portfolio");
    expect(container.querySelector<HTMLInputElement>(".gw-optin input")!.checked).toBe(true);
    expect(container.querySelector<HTMLAnchorElement>(".gw-optin a")!.getAttribute("href")).toBe("/pokemon/portfolio");
  });

  it("Play Market: tap a binder card to sell it now or list it; buy from others with Play USDT, always labelled a test currency", async () => {
    const listing = { id: 7, card: { id: 30, name: "Machamp", set: "Base Set", year: 1999, edition: "Unlimited", image: null, tier: "holo" },
                      price_cents: 500, seller: "misty", mine: false };
    serve({
      ...site, "/api/members/session": { signed_in: true, member: { id: "m1", username: "fan", xp: 10, rank: { level: 1, key: "egg", name: "The Egg", min_xp: 0, description: "", art: { treatment: "egg", overlay: null, placeholder: null, slot: null } } } },
      "/api/members/packs": shelf(3),
      "/api/members/packs/binder": { cards: [{ ...card(2, "rare"), count: 1 }], unique: 1, pulls: 1, value: 2000, priced: 1, best: null, in_portfolio: false, auto_sell_dupes: false },
      "/api/members/play": { currency: "Play USDT", note: "Play USDT is a test currency for fun: not real USDT, no value.", balance_cents: 1234, auto_sell_dupes: false,
                             market: [listing], mine: [], activity: [] },
      "POST /api/members/play/sell": { ok: true, card_id: 2, price_cents: 2000, balance_cents: 3234 },
      "POST /api/members/play/listings/7/buy": { ok: true, card_id: 30, price_cents: 500, balance_cents: 734 },
    });
    const { container } = render(<MemoryRouter><GiveawayPage /></MemoryRouter>);
    await flush();
    expect(container.querySelector(".gw-wallet")!.textContent).toBe("💰 12.34 Play USDT");
    expect(container.querySelector(".gw-play-note")!.textContent).toContain("not real USDT");
    await act(async () => { container.querySelector<HTMLElement>(".gw-binder-grid li")!.click(); });
    expect(container.querySelector(".gw-sell")).not.toBeNull();
    expect(container.querySelector<HTMLInputElement>(".gw-sell-list input")!.value).toBe("20.00");  // listing price prefilled at the estimate
    await act(async () => { screen.getByRole("button", { name: /Sell now/ }).click(); });
    await flush();
    expect(calls).toContain("POST /api/members/play/sell");
    expect(container.querySelector(".gw-play-msg")!.textContent).toContain("Sold Card 2 for 19.00 Play USDT")  // 5% below the $20 market;
    await act(async () => { screen.getByRole("button", { name: /Buy · 5.00 Play USDT/ }).click(); });
    await flush();
    expect(calls).toContain("POST /api/members/play/listings/7/buy");
  });
});
