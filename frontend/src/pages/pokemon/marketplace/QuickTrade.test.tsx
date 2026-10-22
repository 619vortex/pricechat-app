import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { refreshSession } from "../../../website/members";

const played: string[] = [];
vi.mock("../../../website/sound", () => ({ playSound: (n: string) => played.push(n), warmSounds: () => undefined }));

import { MarketplaceHome } from "./QuickTrade";

/** TEST FIXTURES only: a fake API answering by path. */
let routes: Record<string, unknown> = {};
const calls: { method: string; path: string; body: unknown }[] = [];
function serve(r: Record<string, unknown>) {
  routes = r;
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const path = String(input).split("?")[0];
    calls.push({ method: init?.method ?? "GET", path, body: init?.body ? JSON.parse(String(init.body)) : null });
    const body = routes[`${init?.method ?? "GET"} ${path}`] ?? routes[path];
    return body === undefined ? new Response(JSON.stringify({ detail: "nope" }), { status: 404 }) : new Response(JSON.stringify(body), { status: 200 });
  }));
}
const flush = async (n = 6) => { for (let i = 0; i < n; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
const wait = async (ms: number) => { await act(async () => { await new Promise((r) => setTimeout(r, ms)); }); await flush(); };
const member = { signed_in: true, member: { id: "m1", username: "ash", xp: 1, rank: { level: 1, key: "egg", name: "The Egg", min_xp: 0, description: "", art: { treatment: "egg", overlay: null, placeholder: null, slot: null } } } };
const shopCard = { id: 4, name: "Charizard", set: "Base Set", year: 1999, edition: "Unlimited", image: null, tier: "holo", price_cents: 3500, sell_cents: 3325, best_bid_cents: 3000 };
const posts = () => calls.filter((c) => c.method === "POST");
const base = () => ({
  "/api/members/session": member,
  "/api/members/play": { note: "test currency", balance_cents: 1_000_000, sell_pct: 95, market: [], mine: [], bids: [], activity: [] },
  "/api/members/packs/binder": { cards: [{ id: 9, name: "Pikachu", set: "Base Set", year: 1999, edition: "Unlimited", image: null, tier: "common", value: 478, count: 2 }] },
  "/api/members/play/shop": { cards: [shopCard] },
  "/api/members/play/quote/4": { market_cents: 3500, sell_cents: 3325, best_bid_cents: 3000, cheapest_listing_cents: null, owned: 0, my_bids: [] },
  "POST /api/members/play/buy-card": { ok: true, card_id: 4, price_cents: 3500, balance_cents: 996500 },
  "POST /api/members/play/sell": { ok: true, card_id: 9, price_cents: 454, balance_cents: 1000454, to: "market" },
  "POST /api/members/play/bid": { ok: true, bid_id: 1, price_cents: 3150, filled: false },
  "POST /api/members/play/sell-all": { ok: true, cards: 1, copies: 2, total_cents: 860, balance_cents: 1000860 },
});

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("one-click Marketplace (Play USDT)", () => {
  it("the market is the main view: $10,000 to start, Buy at market and Bid on every card, Inventory is the big button", async () => {
    refreshSession();
    serve(base());
    const { container } = render(<MemoryRouter><MarketplaceHome /></MemoryRouter>);
    await flush();
    await wait(900);  // the starter search, and the balance rolling up to its value
    expect(container.querySelector(".mq-bal")!.textContent).toContain("$10,000.00");
    expect(container.querySelector(".mq-note")!.textContent).toContain("not real USDT");
    expect(container.querySelector(".mq-inv")!.textContent).toContain("Inventory");
    expect(container.querySelector(".mq-bidtag")!.textContent).toBe("bid $30.00");
    await act(async () => { screen.getByRole("button", { name: "Buy" }).click(); });
    await flush();
    expect(posts()[0]).toMatchObject({ path: "/api/members/play/buy-card", body: { card_id: 4 } });
    expect(played).toContain("coin");
    expect(container.querySelector(".mq-toast")!.textContent).toBe("✓ Bought Charizard for $35.00");
    // Bid: the sheet opens with the bid pre-filled 10% under market; one press places it
    await act(async () => { screen.getByRole("button", { name: "Bid" }).click(); });
    await flush();
    expect(container.querySelector<HTMLInputElement>(".mq-amount input")!.value).toBe("31.50");
    await act(async () => { screen.getByRole("button", { name: "Place bid $31.50" }).click(); });
    await flush();
    expect(posts().pop()).toMatchObject({ path: "/api/members/play/bid", body: { card_id: 4, price_cents: 3150 } });
  });

  it("inventory: sell 5% below market in one press; Sell all asks twice, then dumps at -10%", async () => {
    refreshSession();
    serve(base());
    const { container } = render(<MemoryRouter><MarketplaceHome /></MemoryRouter>);
    await flush();
    await act(async () => { container.querySelector<HTMLButtonElement>(".mq-inv")!.click(); });
    await act(async () => { screen.getByRole("button", { name: "Sell $4.54" }).click(); });  // 95% of $4.78
    await flush();
    expect(posts()[0]).toMatchObject({ path: "/api/members/play/sell", body: { card_id: 9 } });
    expect(played).toContain("cash");
    await act(async () => { screen.getByRole("button", { name: "😱 Sell all" }).click(); });
    expect(screen.getByRole("alertdialog").textContent).toContain("Sell EVERYTHING?");
    await act(async () => { screen.getByRole("button", { name: "Yes, dump it all" }).click(); });
    expect(screen.getByRole("alertdialog").textContent).toContain("Are you SURE sure?");
    expect(posts().map((c) => c.path)).not.toContain("/api/members/play/sell-all");  // not yet
    await act(async () => { screen.getByRole("button", { name: "YES. SURE SURE. 💀" }).click(); });
    await flush();
    expect(posts().map((c) => c.path)).toContain("/api/members/play/sell-all");
    expect(container.querySelector(".mq-toast")!.textContent).toContain("Dumped 2 cards for $8.60");
  });

  it("diamond hands: backing out of Sell all sells nothing", async () => {
    refreshSession();
    serve(base());
    const { container } = render(<MemoryRouter><MarketplaceHome /></MemoryRouter>);
    await flush();
    await act(async () => { container.querySelector<HTMLButtonElement>(".mq-inv")!.click(); });
    await act(async () => { screen.getByRole("button", { name: "😱 Sell all" }).click(); });
    await act(async () => { screen.getByRole("button", { name: "No, diamond hands 💎" }).click(); });
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(posts()).toHaveLength(0);
  });

  it("asks visitors to sign in (every member starts with $10,000 Play USDT)", async () => {
    refreshSession();
    serve({ "/api/members/session": { signed_in: false, member: null } });
    const { container } = render(<MemoryRouter><MarketplaceHome /></MemoryRouter>);
    await flush();
    expect(container.querySelector(".mq-signin")!.textContent).toContain("$10,000");
    expect(container.querySelector(".mq-search")).toBeNull();
  });
});
