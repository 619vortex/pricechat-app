import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { refreshSession } from "../../../website/members";
import { ago, cardName, money } from "./api";
import { VaultItemPage } from "./VaultItem";
import { VaultStore } from "./VaultStore";

/** TEST FIXTURES only: a fake Vault API with made-up listings. */
const calls: { method: string; url: string; body?: string }[] = [];
const SRC = { kind: "ebay_seller", name: "Mayumi Card Shop", username: "mayumicardshop", url: "https://www.ebay.com/str/mayumicardshop", platform: "eBay" };
const now = new Date().toISOString();

function card(id: number, over: Record<string, unknown> = {}) {
  return { id, title: `[NM] Lugia Pokemon Card Japanese Neo Genesis Holo #${id}`, pokemon: "Lugia", set_name: "Neo Genesis (Gold, Silver, to a New World...)", year: 2000,
           language: "Japanese", condition: "Ungraded", seller_grade: "NM", price: 89999, currency: "USD", shipping: 2500, availability: "available",
           image: `https://i.ebayimg.test/${id}/s-l1600.jpg`, checked_at: now, stale: false, featured: false, source: SRC,
           url: `https://www.ebay.com/itm/${id}`, can_request: true, ...over };
}
const HOME = { count: 2, facets: { pokemon: [["Lugia", 1], ["Gengar", 1]], sets: [["Neo Genesis (Gold, Silver, to a New World...)", 1]], years: [[1996, 1], [2000, 1]],
                                   price_min: 10000, price_max: 89999 },
               sources: [{ ...SRC, checked_at: now }], requests_enabled: true, max_age_hours: 6 };

function serve(routes: Record<string, unknown | ((url: URL, init?: RequestInit) => unknown)>) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(String(input), "https://pricechat.test");
    calls.push({ method: init?.method ?? "GET", url: url.pathname + url.search, body: init?.body as string | undefined });
    const hit = routes[`${init?.method ?? "GET"} ${url.pathname}`] ?? routes[url.pathname];
    const body = typeof hit === "function" ? (hit as (u: URL, i?: RequestInit) => unknown)(url, init) : hit;
    if (body === undefined) return new Response(JSON.stringify({ detail: "Not found." }), { status: 404 });
    if (body instanceof Response) return body;
    return new Response(JSON.stringify(body), { status: 200 });
  }));
}

const flush = async (n = 4) => { for (let i = 0; i < n; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Vault storefront", () => {
  it("shows real listings with the seller's price, condition, availability and attribution", async () => {
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/members/vault/home": HOME,
            "/api/members/vault/listings": (u: URL) => ({ items: u.searchParams.get("pokemon") === "Gengar"
              ? [card(2, { pokemon: "Gengar", set_name: null, year: null, title: "[NM] Gengar Pokemon Card Japanese No.094 Vintage Holo", price: 55999 })]
              : [card(1, { featured: true }), card(3, { availability: "sold_out", can_request: false })], total: 2, page: 1, per_page: 24, pages: 1 }) });
    render(<MemoryRouter initialEntries={["/pokemon/vault"]}><Routes><Route path="/pokemon/vault" element={<VaultStore />} /></Routes></MemoryRouter>);
    await flush();
    expect(screen.getByRole("heading", { level: 1, name: "Vintage Japanese Pokémon" })).toBeTruthy();
    expect(screen.getByText("Mayumi Card Shop").closest("a")?.getAttribute("href")).toBe("https://www.ebay.com/str/mayumicardshop");
    const tiles = screen.getAllByRole("link", { name: /Lugia/ });
    expect(tiles).toHaveLength(2);
    const first = within(tiles[0]);
    expect(first.getByText("$899.99")).toBeTruthy();
    expect(first.getByText("Ungraded · NM")).toBeTruthy();
    expect(first.getByText("Neo Genesis (Gold, Silver, to a New World...) · 2000")).toBeTruthy();
    expect(first.getByText("Available")).toBeTruthy();
    expect(first.getByText("Featured")).toBeTruthy();
    expect(first.getByText("View details")).toBeTruthy();
    expect(tiles[0].getAttribute("href")).toBe("/pokemon/vault/1");
    expect(within(tiles[1]).getAllByText("Sold out").length).toBeGreaterThan(0);
    expect(screen.getByText(/doesn't own, hold or\s+reserve these cards/)).toBeTruthy();

    // filter by Pokémon: the request carries the filter, the grid shows the result
    fireEvent.change(screen.getByLabelText("Pokémon"), { target: { value: "Gengar" } });
    await flush();
    expect(calls.some((c) => c.url.startsWith("/api/members/vault/listings?") && c.url.includes("pokemon=Gengar"))).toBe(true);
    expect(screen.getByRole("link", { name: /Gengar, \$559\.99/ })).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Sort"), { target: { value: "price_asc" } });
    await flush();
    expect(calls.at(-1)?.url).toContain("sort=price_asc");
  });

  it("explains an empty store instead of showing fake cards", async () => {
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/members/vault/home": { ...HOME, count: 0 },
            "/api/members/vault/listings": { items: [], total: 0, page: 1, per_page: 24, pages: 1 } });
    render(<MemoryRouter initialEntries={["/pokemon/vault"]}><Routes><Route path="/pokemon/vault" element={<VaultStore />} /></Routes></MemoryRouter>);
    await flush();
    expect(screen.getByRole("heading", { name: "The first cards are being curated" })).toBeTruthy();
    expect(screen.queryAllByRole("link", { name: /\$/ })).toHaveLength(0);
  });
});

describe("Vault card page", () => {
  const detail = (over: Record<string, unknown> = {}) => ({ ...card(1), images: ["https://i.ebayimg.test/1/s-l1600.jpg", "https://i.ebayimg.test/1b/s-l1600.jpg"],
    condition_detail: "Card Condition: Near mint or better", seller: { username: "mayumicardshop", feedback_pct: "100.0", feedback_score: 120000 },
    specifics: { "Card Number": "9", Rarity: "Holo Rare" }, first_seen_at: now, ended_at: null, max_age_hours: 6, my_request: null, ...over });

  it("sends a Request to Buy that is clearly not an order", async () => {
    serve({ "/api/members/session": { signed_in: true, member: { username: "ash" } }, "/api/members/vault/listings/1": detail(),
            "POST /api/members/vault/listings/1/request": { id: 7, listing_id: 1, status: "new", contact: "@ash_tg", note: "", price: 89999, currency: "USD", created_at: now } });
    refreshSession();
    render(<MemoryRouter initialEntries={["/pokemon/vault/1"]}><Routes><Route path="/pokemon/vault/:id" element={<VaultItemPage />} /></Routes></MemoryRouter>);
    await flush(6);
    expect(screen.getByRole("heading", { level: 1, name: "Lugia" })).toBeTruthy();
    expect(screen.getByText("$899.99")).toBeTruthy();
    expect(screen.getByText("+ $25.00 shipping")).toBeTruthy();
    expect(screen.getByText("Card Condition: Near mint or better")).toBeTruthy();
    expect(screen.getByText(/100\.0% positive feedback/)).toBeTruthy();
    expect(screen.getByRole("link", { name: /View the original listing on eBay/ }).getAttribute("href")).toBe("https://www.ebay.com/itm/1");
    expect(screen.getByText(/no payment is taken and the card isn't reserved/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Request to Buy" })); });
    fireEvent.change(screen.getByPlaceholderText("@yourname"), { target: { value: "@ash_tg" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Send request" })); });
    await flush();
    const sent = calls.find((c) => c.method === "POST");
    expect(sent?.url).toBe("/api/members/vault/listings/1/request");
    expect(JSON.parse(sent?.body ?? "{}")).toEqual({ contact: "@ash_tg", note: "" });
    expect(screen.getByText(/Request sent/)).toBeTruthy();
    expect(screen.getByText(/Nothing has been charged and the card is not reserved/)).toBeTruthy();
  });

  it("pauses requests on a sold-out or unconfirmed listing", async () => {
    serve({ "/api/members/session": { signed_in: true, member: { username: "ash" } }, "/api/members/vault/listings/1": detail({ stale: true, can_request: false,
            checked_at: new Date(Date.now() - 7 * 3600_000).toISOString() }) });
    refreshSession();
    const { unmount } = render(<MemoryRouter initialEntries={["/pokemon/vault/1"]}><Routes><Route path="/pokemon/vault/:id" element={<VaultItemPage />} /></Routes></MemoryRouter>);
    await flush(6);
    expect(screen.getByText(/may have changed/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Request to Buy" })).toBeNull();
    unmount();
    serve({ "/api/members/session": { signed_in: true, member: { username: "ash" } }, "/api/members/vault/listings/1": detail({ availability: "sold_out", can_request: false }) });
    render(<MemoryRouter initialEntries={["/pokemon/vault/1"]}><Routes><Route path="/pokemon/vault/:id" element={<VaultItemPage />} /></Routes></MemoryRouter>);
    await flush(6);
    expect(screen.getAllByText("Sold out").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /Request to Buy/ })).toBeNull();
  });
});

describe("Vault helpers", () => {
  it("formats the seller's own currency and names cards", () => {
    expect(money(89999, "USD")).toBe("$899.99");
    expect(money(1500000, "JPY")).toBe("¥15,000");
    expect(money(null, "USD")).toBe("—");
    expect(cardName({ pokemon: null, title: "[NM] Gengar Pokemon Card Japanese No.094" })).toBe("Gengar");
    expect(ago(new Date(Date.now() - 3 * 3600_000).toISOString())).toBe("3 h ago");
  });
});
