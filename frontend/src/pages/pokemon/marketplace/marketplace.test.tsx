import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { refreshSession } from "../../../website/members";
import type { PCardBrief } from "../portfolio/api";
import { usdt, type Listing } from "./api";
import { GradedMarketplace } from "./Home";
import { ListingPage } from "./ListingPage";

/** TEST FIXTURES only: a fake API answering by path. */
let routes: Record<string, unknown> = {};
const calls: { method: string; path: string; headers?: HeadersInit }[] = [];

function serve(r: Record<string, unknown>) {
  routes = r;
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const path = String(input).split("?")[0];
    calls.push({ method: init?.method ?? "GET", path, headers: init?.headers });
    const body = routes[`${init?.method ?? "GET"} ${path}`] ?? routes[path];
    return body === undefined ? new Response(JSON.stringify({ detail: "nope" }), { status: 404 }) : new Response(JSON.stringify(body), { status: 200 });
  }));
}

async function show(at: string) {
  refreshSession();
  const r = render(<MemoryRouter initialEntries={[at]}><Routes>
    <Route path="/pokemon/marketplace/graded" element={<GradedMarketplace />} /><Route path="/pokemon/marketplace/l/:id" element={<ListingPage />} />
    <Route path="*" element={<div>other page</div>} /></Routes></MemoryRouter>);
  for (let i = 0; i < 5; i++) await act(async () => { await new Promise((res) => setTimeout(res, 0)); });
  return r;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const card: PCardBrief = { id: 1, name: "Charizard", pokemon_name: "Charizard", set_name: "Base Set", set_code: "base1", card_number: "4/102", year: 1999,
  language: "English", edition: "1st Edition", variant: "", rarity: "Rare Holo", image_url: null, era: "vintage" };
const listing = (over: Partial<Listing> = {}): Listing => ({
  id: 7, status: "active", sandbox: false, card, grading_company: "PSA", grade: "9", cert: "••••5678", price: 900_00, currency: "USDT", listed_at: "2026-10-09T00:00:00Z",
  sold_at: null, estimate: { value: 1_000_00, low: null, high: null, source: "scrydex", confidence: "high", note: "", as_of: "2026-10-09" }, diff_pct: -10,
  uncertain: false, custody: { status: "vaulted", label: "Vaulted", vaulted: true, partner: "acme", insurance: "none", insurance_note: "" }, verified: true,
  photos: [], mine: false, ...over,
});
const status = (sandbox = false) => ({ mode: "simulation", sandbox, signed_in: false, photos: true, notice: "",
  networks: [{ id: "tron", label: "Tron (TRC-20)", token: "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t", decimals: 6, confirmations: 20, fee_estimate: "≈ 1 USDT", wallets: [] }],
  fees: { price: 10_000, buyer_fee: 0, buyer_fee_bps: 0, buyer_total: 10_000, seller_fee: 500, seller_fee_bps: 500, seller_proceeds: 9_500, network_fees: {}, currency: "USDT" } });
const empty = { mode: "simulation", sandbox: false, featured: [], newest: [], cheapest: [], valuable: [], sold: [], trending: [], vaulted_inventory: 0,
  stats: { sales: 0, volume: 0, sales_30d: 0, volume_30d: 0, active_listings: 0, currency: "USDT" } };

describe("Card Marketplace", () => {
  it("formats USDT exactly", () => {
    expect(usdt(123456)).toBe("1,234.56 USDT");
    expect(usdt(null)).toBe("—");
  });

  it("an empty marketplace says so honestly (no invented inventory or volume) and shows the preview banner", async () => {
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/members/market/status": status(), "/api/members/market/home": empty });
    await show("/pokemon/marketplace/graded");
    expect(screen.getByText(/No cards are listed yet/)).toBeTruthy();
    expect(screen.getByText(/no real-money trading yet/i)).toBeTruthy();
    expect(screen.getByText(/No completed marketplace sales yet/)).toBeTruthy();
  });

  it("a listing shows USDT price, USD estimate, the discount and a masked cert; buying is off for real listings in simulation", async () => {
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/members/market/status": status(), "/api/members/market/listings/7": listing({
      fees: status().fees, history: [], comparables: { verified_sales: [], marketplace_sales: [] } }) });
    await show("/pokemon/marketplace/l/7");
    expect(screen.getByText("900.00 USDT")).toBeTruthy();
    expect(screen.getByText("10% below")).toBeTruthy();
    expect(screen.getAllByText("••••5678").length).toBeGreaterThan(0);
    expect((screen.getByRole("button", { name: "Buy Now" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/never counted as sales/)).toBeTruthy();
  });

  it("sandbox buyers pick a network explicitly and the purchase carries an idempotency key", async () => {
    serve({ "/api/members/session": { signed_in: true, member: { username: "ash" } }, "/api/members/market/status": status(true),
            "/api/members/market/listings/7": listing({ sandbox: true, fees: status().fees, history: [], comparables: { verified_sales: [], marketplace_sales: [] } }),
            "POST /api/members/market/listings/7/buy": { id: 3 } });
    await show("/pokemon/marketplace/l/7");
    fireEvent.click(screen.getByRole("button", { name: "Buy Now" }));
    expect(screen.getByText(/not interchangeable/)).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: /Reserve & pay/ })); });
    const buy = calls.find((c) => c.path === "/api/members/market/listings/7/buy");
    expect(buy && (buy.headers as Record<string, string>)["Idempotency-Key"]).toBeTruthy();
  });

  it("a raw listing shows the AI condition review, members' grade and comments; a member can grade it", async () => {
    serve({ "/api/members/session": { signed_in: true, member: { username: "ash" } }, "/api/members/market/status": status(),
            "/api/members/market/listings/8": listing({ id: 8, grading_company: "RAW", grade: "LP", cert: null, kind: "raw", fees: status().fees, history: [],
              comparables: { verified_sales: [], marketplace_sales: [] },
              condition: { label: "LP", low: 6.5, high: 7.5, ai: { low: 6.5, high: 7.5, confidence: "medium", corners: "light wear top-left", issues: ["whitening"] },
                           community: { count: 2, average: 6.5, mine: null } } }),
            "/api/members/market/listings/8/comments": { comments: [{ id: 1, parent_id: null, at: "2026-10-10T00:00:00Z", username: "misty", seller: false,
                                                                     body: "Any photos of the corners?", deleted: false, mine: false }] },
            "POST /api/members/market/listings/8/condition-vote": { count: 3, average: 6.7, mine: { estimate: 7, note: "" } } });
    await show("/pokemon/marketplace/l/8");
    expect(screen.getAllByText("RAW LP").length).toBeGreaterThan(0);
    expect(screen.getByText(/light wear top-left/)).toBeTruthy();
    expect(screen.getByText("6.5")).toBeTruthy();
    expect(screen.getByText("Any photos of the corners?")).toBeTruthy();
    expect(screen.queryByText(/Certification/)).toBeNull();
    await act(async () => { fireEvent.click(screen.getByRole("radio", { name: "7" })); });
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/members/market/listings/8/condition-vote")).toBe(true);
    expect(screen.getByText("6.7")).toBeTruthy();
  });
});
