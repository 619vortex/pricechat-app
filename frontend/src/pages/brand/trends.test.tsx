import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BrandManagerPage } from "./BrandManager";

/** TEST FIXTURES only: a fake trends API with made-up headlines. */
const calls: { method: string; url: string; body?: string }[] = [];
const now = new Date().toISOString();
const ex = (title: string, source: string) => ({ title, source, url: `https://example.test/${encodeURIComponent(title)}`, at: now });
const trend = (over: Record<string, unknown>) => ({ term: "genius act", label: "GENIUS Act", kind: "headline", status: "new", docs: 5,
  outlets: ["CNBC", "CoinDesk", "Financial Times"], baseline: 0, momentum: 6, strength: 9, series: [0, 0, 0, 0, 0, 0, 0, 5],
  examples: [ex("GENIUS Act rule lands", "CNBC"), ex("Banks weigh GENIUS Act", "Financial Times")], day: "2026-10-21", videos: [], ...over });
const data = {
  trends: [trend({}),
    trend({ term: "pudgy penguins", label: "Pudgy Penguins (PENGU)", kind: "search", status: "searched", docs: 0, outlets: ["CoinGecko searches"], baseline: null,
            examples: [{ title: "#2 most-searched coin on CoinGecko in the last 24 hours", source: "CoinGecko", url: "https://example.test/pengu", at: null, rank: 2, symbol: "PENGU" }] }),
    trend({ term: "tariffs", label: "Tariffs", status: "steady", docs: 4, baseline: 4, series: [4, 4, 4, 4, 4, 4, 4, 4],
            videos: [{ id: 3, topic: "Tariff week", status: "published", views: 2100 }] })],
  scanned_at: now, outlets: ["CNBC", "CoinDesk", "CoinGecko searches"], category_on: true,
  learned: { by_trend: [{ key: "rode a trend", videos: 4, avg_views: 2600 }, { key: "no trend", videos: 5, avg_views: 1300 }], multiplier: 1.4,
             words: [{ label: "Stablecoin", videos: 2, avg_views: 3100 }] },
};

function serve(routes: Record<string, unknown>) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input).split("?")[0];
    calls.push({ method: init?.method ?? "GET", url, body: init?.body as string | undefined });
    const body = routes[`${init?.method ?? "GET"} ${url}`] ?? routes[url];
    return body === undefined ? new Response(JSON.stringify({ detail: "nope" }), { status: 404 }) : new Response(JSON.stringify(body), { status: 200 });
  }));
}
const flush = async () => { for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Trends", () => {
  it("lists what's trending with its evidence, hides steady words, and makes a video from a trend", async () => {
    serve({ "/api/brand-manager/trends": data, "POST /api/brand-manager/trends/video": { id: 42 } });
    render(<MemoryRouter initialEntries={["/brand-manager/trends"]}><Routes><Route path="/brand-manager/*" element={<BrandManagerPage />} />
      <Route path="/brand-manager/studio/:id" element={<p>studio</p>} /></Routes></MemoryRouter>);
    await flush();
    expect(screen.getByText("GENIUS Act")).toBeTruthy();
    expect(screen.getByText("New")).toBeTruthy();
    expect(screen.getByText("#2 on CoinGecko")).toBeTruthy();
    expect(screen.queryByText("Tariffs")).toBeNull();  // steady: only on "show all"
    expect(screen.getByText(/the boost is ×1.4/)).toBeTruthy();
    expect(screen.getByText(/Stablecoin \(3,100 views\)/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "GENIUS Act" }));
    expect(screen.getByRole("link", { name: "GENIUS Act rule lands" }).getAttribute("href")).toContain("example.test");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Make a video" })); });
    await flush();
    expect(JSON.parse(calls.find((c) => c.url.endsWith("/trends/video"))?.body ?? "{}")).toEqual({ term: "genius act" });
  });

  it("shows steady words and the videos that used them on request", async () => {
    serve({ "/api/brand-manager/trends": data });
    render(<MemoryRouter initialEntries={["/brand-manager/trends"]}><Routes><Route path="/brand-manager/*" element={<BrandManagerPage />} /></Routes></MemoryRouter>);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Show steady and cooling too" }));
    expect(screen.getByText("Tariffs")).toBeTruthy();
    expect(screen.getByText("1 · 2,100 avg views")).toBeTruthy();
  });
});
