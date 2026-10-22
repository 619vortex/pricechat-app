import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CardDetail, ChartResp, MarketResp, PCard, RankRow, Source, Valuation } from "./api";
import { MarketHome } from "./MarketHome";
import { QualityPage, YearsPage } from "./ListPages";
import { PokemonCardPage } from "./PokemonCardPage";
import { PokemonSources } from "./PokemonSources";
import { MarketPulse, type PulseStatus } from "./pulse";
import { marketSoundOn, play } from "./marketSound";
import { resetFx, Sparkline, SoundToggle } from "./mx";
import { cardLine, usd } from "./format";

// Lightweight Charts draws on canvas, which jsdom lacks: record what each series is given instead.
const chartLog: { kind: string; data: unknown[]; pane?: number }[] = [];
vi.mock("lightweight-charts", () => {
  const series = (kind: string, pane?: number) => {
    const entry = { kind, data: [] as unknown[], pane };
    chartLog.push(entry);
    return { setData: (d: unknown[]) => { entry.data = d; }, applyOptions: () => undefined };
  };
  return {
    AreaSeries: "Area", LineSeries: "Line", HistogramSeries: "Histogram", ColorType: { Solid: "solid" }, CrosshairMode: { Normal: 0 }, LineStyle: { Solid: 0, Dashed: 2 },
    createChart: () => ({
      applyOptions: () => undefined, addSeries: (kind: string, _o: unknown, pane?: number) => series(kind, pane),
      panes: () => [{ setStretchFactor: () => undefined }, { setStretchFactor: () => undefined }],
      timeScale: () => ({ fitContent: () => undefined }), subscribeCrosshairMove: () => undefined, remove: () => undefined,
    }),
  };
});

const card = (over: Partial<PCard> = {}): PCard => ({
  id: 1, name: "Charizard", pokemon_name: "Charizard", set_name: "Base Set", set_code: "base1", card_number: "4/102", year: 1999, language: "English",
  edition: "1st Edition", rarity: "Rare Holo", variant: "", holo_type: "Holo", era: "vintage", is_promo: false, image_url: null, image_source: null,
  release_date: null, ...over,
});
const rank = (over: Partial<RankRow> = {}): RankRow => ({
  card: card(), grading_company: "PSA", grade: "10", label: "PSA 10", market_price: null, confidence: null, confidence_reasons: [],
  change_30d: null, change_90d: null, change_reasons: { "30d": "no current price" }, sales_7d: 0, volume_7d: 0, sales_30d: 0, volume_30d: 0,
  last_sale: null, last_sale_at: null, population: null, population_source: null, population_as_of: null, market_cap: null, watchers: 0, sparkline: null, ...over,
});
const priced = (over: Partial<RankRow> = {}): RankRow => rank({
  rank: 1, market_price: 25_000_00, confidence: "MEDIUM", change_30d: -3.5, change_90d: 8.2,
  change_sources: { "30d": "sales", "90d": "sales" }, sales_30d: 3, volume_30d: 75_000_00, population: 125,
  market_cap: 3_125_000_00, sparkline: { points: [[1, 20_000_00], [2, 25_000_00]], source: "sales", from: "1970-01-01", to: "1970-01-01", span_days: 0, observations: 2 }, change_reasons: {}, ...over,
});
const market = (rows: RankRow[], over: Partial<MarketResp> = {}): MarketResp => ({
  grade: "PSA 10", total: rows.length, offset: 0, limit: 100, rows, as_of: "2026-10-08T00:00:00Z",
  hero: { total_market_cap: null, market_cap_change_30d: null, market_cap_change_90d: null, market_cap_change_30d_cards: 0,
          market_cap_change_90d_cards: 0, cards_with_cap: 0, cards_priced: 0,
          cards_tracked: rows.length, volume_30d: 0, grade_sales_30d: 0, sales_records_from: null,
          all_sales_30d: 0, population_total: null,
          cards_with_population: 0, lowest_population: null },
  sections: { trending: [], gainers: [], losers: [], recently_sold: [] },
  options: { sets: ["Base Set"], years: [1999], editions: ["1st Edition"], languages: ["English"], grades: ["PSA 10", "PSA 9"] },
  providers: { sales_connected: [], population_connected: [], sales_records: 0 }, has_market_data: false, ...over,
});

let routes: Record<string, unknown> = {};
const calls: string[] = [];
function serve(r: Record<string, unknown>) {
  routes = r;
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (raw: string, init?: RequestInit) => {
    const input = raw.replace(/[?&]pub=1$/, "");  // the public site's CDN mark (client.ts edgeMark)
    calls.push(`${init?.method ?? "GET"} ${input}`);
    // most specific route first; a route containing "?" must appear as a parameter of the request
    const keys = Object.keys(routes).sort((a, b) => b.length - a.length);
    const key = keys.find((k) => {
      const [path, param] = k.split("?");
      const ok = input === `/api${path}` || input.startsWith(`/api${path}?`);
      return ok && (!param || input.includes(param));
    });
    return key ? new Response(JSON.stringify(routes[key]), { status: 200, headers: { "Content-Type": "application/json" } })
      : new Response(JSON.stringify({ detail: "nope" }), { status: 404, headers: { "Content-Type": "application/json" } });
  }));
}
let where = "";
function Where() {
  const l = useLocation();
  where = l.pathname + l.search;
  return null;
}
async function show(ui: React.ReactElement, path = "/pokemon") {
  const r = render(<MemoryRouter initialEntries={[path]}><Where /><Routes>
    <Route path="/pokemon" element={ui} /><Route path="/pokemon/cards/:id" element={ui} /><Route path="/pokemon/sources" element={ui} /><Route path="/pokemon/years" element={ui} /><Route path="/pokemon/quality" element={ui} />
  </Routes></MemoryRouter>);
  await settle();
  return r;
}
async function settle(ms = 0) {
  for (let i = 0; i < 4; i++) await act(async () => { await new Promise((res) => setTimeout(res, ms)); });
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  chartLog.length = 0;
});

describe("formatting", () => {
  it("never shows a number it doesn't have", () => {
    expect(usd(null)).toBe("—");
    expect(usd(3_125_000_00, true)).toBe("$3.13M");
    expect(usd(25_000_00)).toBe("$25,000");
    expect(cardLine(card())).toBe("4/102 — 1st Edition Holo");
  });
});

describe("market rankings (CoinGecko-style)", () => {
  it("lists the catalog with dashes and an honest banner when there is no market data", async () => {
    serve({ "/pokemon/market": market([rank()]) });
    const { container } = await show(<MarketHome />);
    expect(screen.getByRole("heading", { level: 1, name: "Top 100 Vintage Pokémon Cards by Market Cap" })).toBeTruthy(); // the default view
    expect(container.querySelector(".mx-banner")!.textContent).toContain("No market data yet");
    const head = [...container.querySelectorAll(".mx-table thead th")].map((th) => th.textContent!.replace(/[▲▼↕]/g, ""));
    expect(head).toEqual(["", "#", "Card", "Grade", "Price", "30D", "90D", "Volume (30D)", "Market Cap", "Population", "Last 90 Days"]);
    const r = container.querySelector(".mx-table tbody tr")!;
    expect(r.textContent).toContain("Charizard");
    expect(r.textContent).toContain("1st Ed");
    expect(r.textContent).not.toMatch(/\$\d/); // no price anywhere
    expect(r.querySelector(".mx-spark")).toBeNull(); // no invented sparkline
    expect(r.querySelector(".mx-noimg")).not.toBeNull();
  });

  it("shows rank, price, green gains, red losses, volume, cap, population and a sparkline", async () => {
    serve({ "/pokemon/market": market([priced()], { has_market_data: true }) });
    const { container } = await show(<MarketHome />);
    const r = container.querySelector(".mx-table tbody tr")!;
    const cells = [...r.querySelectorAll("td")].map((td) => td.textContent);
    expect(cells[1]).toBe("1");
    expect(cells[4]).toBe("$25,000");
    expect(r.querySelector(".mx-up")!.textContent).toContain("8.2%");  // 90D (the 30D move is a loss)
    expect(r.querySelector(".mx-down")!.textContent).toContain("3.5%");
    expect(cells[7]).toBe("$75K3 sales");
    expect(cells[8]).toBe("$3.13M");
    expect(cells[9]).toBe("125");
    expect(r.querySelector(".mx-spark path")).not.toBeNull();
  });

  it("sorts on the server with shareable URLs, and pages 100 at a time", async () => {
    serve({ "/pokemon/market": market([priced()], { total: 250 }) });
    await show(<MarketHome />);
    await act(async () => screen.getByRole("button", { name: /^Price/ }).click());
    await settle();
    expect(where).toBe("/pokemon?sort=price");
    expect(calls.some((c) => c.includes("sort=price") && c.includes("dir=desc"))).toBe(true);
    await act(async () => screen.getByRole("button", { name: /^Price/ }).click());
    await settle();
    expect(where).toBe("/pokemon?sort=price&dir=asc");
    await act(async () => screen.getByRole("button", { name: "2" }).click());
    await settle();
    expect(calls.some((c) => c.includes("offset=100") && c.includes("limit=100"))).toBe(true);
    expect(where).toContain("page=2");
  });

  it("changes the grade of the whole table, or of one row", async () => {
    serve({ "/pokemon/market?ids=1": market([priced({ label: "PSA 9", grade: "9", market_price: 4_000_00 })]), "/pokemon/market": market([priced()]) });
    const { container } = await show(<MarketHome />);
    fireEvent.change(screen.getByLabelText("Grade for Charizard"), { target: { value: "PSA 9" } });
    await settle();
    expect(calls.some((c) => c.includes("ids=1") && c.includes("grade=PSA+9"))).toBe(true);
    expect(container.querySelector(".mx-table tbody tr")!.textContent).toContain("$4,000");
    fireEvent.change(screen.getByLabelText("Grade for the whole table"), { target: { value: "BGS 9.5" } });
    await settle();
    expect(where).toBe("/pokemon?grade=BGS+9.5");
  });

  it("labels a provider (Scrydex) price as an estimate, never as a sale", async () => {
    serve({ "/pokemon/market": market([priced({ price_source: "scrydex", provider: { provider: "scrydex", name: "Scrydex", market: 400_000_00, low: 380_000_00, mid: 400_000_00, high: 420_000_00, currency: "USD", as_of: "2026-10-08",
      fetched_at: "2026-10-08T11:00:00Z", variant: "firstEditionShadowlessHolofoil", provider_card_id: "base1-4", trends: { "24h": 0, "7d": 2.5, "30d": -1.2, "90d": null } }, market_price: 400_000_00 })], { has_market_data: true }) });
    const { container } = await show(<MarketHome />);
    const cell = container.querySelector(".mx-table tbody tr")!.querySelectorAll("td")[4];
    expect(cell.textContent).toBe("$400,000est.");
    expect(container.querySelector(".mx-sourcebar")!.textContent).toContain("est. = Scrydex valuation · PC = PriceCharting value (no history)");
    expect(cell.getAttribute("title")).toContain("not a verified sale");
  });

  it("shows a PriceCharting fallback as PC, and flags sources that disagree", async () => {
    const ref = { provider: "pricecharting", name: "PriceCharting", price: 24_808_28, as_of: "2026-10-08", product_id: "715695", diff_pct: 148.2, disagree: true };
    serve({ "/pokemon/market": market([
      priced({ price_source: "scrydex", market_price: 61_575_76, reference: ref }),
      priced({ card: card({ id: 26, name: "Machamp" }), price_source: "pricecharting", market_price: 5_000_00, change_90d: null, change_30d: null,
               reference: { ...ref, price: 5_000_00, diff_pct: null, disagree: false }, rank: undefined }),
    ], { has_market_data: true }) });
    const { container } = await show(<MarketHome />);
    const [a, b] = [...container.querySelectorAll(".mx-table tbody tr")].map((tr) => tr.querySelectorAll("td")[4]);
    expect(a.textContent).toBe("⚠$61,576est.");
    expect(a.getAttribute("title")).toContain("PriceCharting: $24,808 (shown price +148.2% vs PriceCharting) · sources disagree by more than 25%");
    expect(b.textContent).toBe("$5,000PC");
    expect(b.getAttribute("title")).toContain("PriceCharting value as of 2026-10-08 (reference, not a verified sale)");
  });

  it("category chips filter through the URL and the highlights can be hidden", async () => {
    serve({ "/pokemon/market": market([priced()], { has_market_data: true }) });
    const { container } = await show(<MarketHome />);
    expect(container.querySelector(".mx-highlights")).not.toBeNull();
    await act(async () => screen.getByRole("button", { name: /Base Set/ }).click());
    await settle();
    expect(where).toBe("/pokemon?set=Base+Set");
    expect(calls.some((c) => c.includes("set=Base+Set"))).toBe(true);
    expect(screen.getByRole("button", { name: /Base Set/ }).getAttribute("aria-pressed")).toBe("true");
    await act(async () => within(screen.getByRole("navigation", { name: "Categories" })).getByRole("button", { name: /^All/ }).click());
    await settle();
    expect(where).toBe("/pokemon");
    await act(async () => screen.getByRole("switch", { name: "Show highlights" }).click());
    expect(container.querySelector(".mx-highlights")).toBeNull();
  });

  it("switches between the English and Japanese markets and shows yen with the dated rate", async () => {
    resetFx();
    const jp = priced({ card: card({ id: 19, name: "Charizard", language: "Japanese", set_name: "Japanese Base Set (Expansion Pack)" }), market_price: 25_000_00 });
    serve({ "/pokemon/market": market([jp], { has_market_data: true }),
            "/pokemon/fx": { usd_jpy: { rate: 149.25, rate_date: "2026-10-07", fetched_at: "2026-10-08T10:02:00+00:00", source: "European Central Bank reference rate (via Frankfurter)", stale: false } } });
    const { container } = await show(<MarketHome />);
    expect(container.querySelector(".mx-jpy")).toBeNull(); // USD only by default
    expect(screen.queryByRole("group", { name: "Market" })).toBeNull(); // one toggle, no three-way selector
    const jpToggle = () => screen.getByRole("button", { name: /Japanese/, pressed: undefined });
    expect(jpToggle().getAttribute("aria-pressed")).toBe("false"); // default: all cards
    await act(async () => jpToggle().click());
    await settle();
    expect(where).toBe("/pokemon?language=Japanese");
    expect(jpToggle().getAttribute("aria-pressed")).toBe("true");
    expect(jpToggle().className).toContain("is-on");
    expect(calls.some((c) => c.includes("/pokemon/market") && c.includes("language=Japanese"))).toBe(true);
    expect(container.querySelector(".mx-jpy")!.textContent).toBe("¥3,731,250"); // $25,000 x 149.25, shown by default on the Japanese market
    expect(container.querySelector(".mx-fxbar")!.textContent).toContain("$1 = ¥149.25 · ECB rate of 7 Oct 2026 · fetched 8 Oct 2026, 10:02 UTC");
    expect(container.textContent).toContain("English prices and populations are never applied to Japanese cards");
    await act(async () => within(screen.getByRole("group", { name: "Currency" })).getByRole("button", { name: "USD" }).click());
    await settle();
    expect(where).toBe("/pokemon?language=Japanese&cur=usd");
    expect(container.querySelector(".mx-jpy")).toBeNull();
    await act(async () => jpToggle().click()); // off again: back to all cards
    await settle();
    expect(where).toBe("/pokemon?cur=usd");
    expect(jpToggle().getAttribute("aria-pressed")).toBe("false");
    resetFx();
  });

  it("suggests cards as you type in the search bar", async () => {
    serve({ "/pokemon/market": market([rank()]), "/pokemon/cards": { query: "x", grade: "PSA 10", results: [{ card: card(), grade: "PSA 10", market_price: null,
                                                                                                        market_cap: null, change_30d: null }] } });
    await show(<MarketHome />);
    fireEvent.change(screen.getByLabelText("Search Pokémon cards"), { target: { value: "Charizard 1st edition PSA 10" } });
    await settle(260);
    expect(calls.some((c) => c.startsWith("GET /api/pokemon/cards?q=Charizard+1st+edition+PSA+10"))).toBe(true);
    expect(screen.getByRole("listbox").textContent).toContain("1999 Base Set · 4/102 · 1st Edition");
  });
});

const stats = (over: Partial<Valuation["stats"]> = {}): Valuation["stats"] => ({
  market_price: null, confidence: null, confidence_reasons: [], last_sale: null, last_sale_at: null, sales_count: 0, sales_7d: 0, sales_30d: 0, sales_90d: 0,
  volume_7d: 0, volume_30d: 0, median_30d: null, median_90d: null, average_30d: null, high_30d: null, low_30d: null, window_days: 90, ...over,
});
const val = (label: string, over: Partial<Valuation> = {}): Valuation => ({
  grading_company: label.split(" ")[0], grade: label.split(" ")[1], label, graded: !label.startsWith("RAW"), market_price: null, confidence: null,
  change_30d: null, change_90d: null, population: null, population_source: null, population_as_of: null, market_cap: null,
  sales_7d: 0, volume_7d: 0, sales_30d: 0, volume_30d: 0, stats: stats(), ...over,
});
const ts = (iso: string) => Date.parse(iso) / 1000;
const change = (value: number | null, reason: string | null = null) => ({ value, reason, base: null, base_day: null });
const chart = (over: Partial<ChartResp> = {}): ChartResp => ({
  card_id: 1, as_of: "2026-10-09T00:00:00Z", grade: "PSA 10", graded: true, range: "90d", rank: null,
  summary: { market_price: null, confidence: null, confidence_reasons: [], sales_count: 0, window_days: 90,
             changes: { "30d": change(null, "no current price"), "90d": change(null, "no current price") },
             population: null, population_source: null, population_as_of: null, market_cap: null, last_verified_sale: null, sales_30d: 0, volume_30d: 0, median_30d: null,
             highest_sale: null, lowest_sale: null, highest_estimate: null, lowest_estimate: null },
  line: [], cap_line: [], sales: [], volume: [], population_history: [], ...over,
});

const summary = { scope: "vintage (1996-2003) cards, PSA 10", candidates: 6982, eligible: 67, shown: 67, excluded: 6915, limit: 100, min_history: 8,
  max_age_days: 10, excluded_by_reason: { history: 6875, population: 3900, moves: 433 }, total_market_cap: 80_000_000_00,
  labels: { price: "Reliable PSA 10 price", population: "PSA 10 population", history: "Price history", identity: "Exact card identity",
            freshness: "Fresh data", moves: "No unverified moves", spikes: "No price spikes", consistency: "Agrees with sales" } };

describe("Top 100 and data quality", () => {
  it("opens on the Top 100, says how many qualify without padding, switches to all cards, and shows confidence per row", async () => {
    const q = { eligible: true, score: 100, confidence: "high" as const, history_points: 72, reasons: [] };
    serve({ "/pokemon/market": market([priced({ quality: q })], { has_market_data: true, top100: summary, view: "top100" }) });
    const { container } = await show(<MarketHome />);
    expect(calls.some((c) => c.includes("/pokemon/market") && c.includes("view=top100"))).toBe(true);
    expect(container.querySelector(".mx-lede")!.textContent).toContain("67 of 6,982 vintage PSA 10 records meet every data-quality check (fewer than 100: the list is not padded");
    expect(screen.getByRole("link", { name: "6,915 excluded, see why" }).getAttribute("href")).toBe("/pokemon/quality");
    const pill = container.querySelector(".mx-table .mx-confpill")!;
    expect(pill.className).toContain("mx-confpill-high");
    expect(pill.getAttribute("title")).toBe("Top 100 eligible · score 100/100 · 72 price observations");
    await act(async () => within(screen.getByRole("group", { name: "Ranking" })).getByRole("button", { name: "All cards" }).click());
    await settle();
    expect(where).toBe("/pokemon?view=all");
    expect(calls.some((c) => c.includes("/pokemon/market") && c.includes("view=all"))).toBe(true);
  });

  it("Data Quality lists what is excluded and why, by reason, with entries / exits", async () => {
    const bad = { eligible: false, score: 55, confidence: "low" as const, history_points: 1, failed: ["history"],
                  reasons: ["1 price observation in the last year (needs 8)"], checks: [] };
    serve({ "/pokemon/top100/quality": { summary, missing: { price: 1533, population: 3900, history: 6875 }, total: 1, offset: 0, limit: 50,
                                         rows: [{ ...priced(), quality: bad }],
                                         events: [{ at: "2026-10-08T12:00:00Z", kind: "left", rank: 99, reasons: ["unverified +80% 7d move"], card: card() }] },
            "/pokemon/quality": { grade: "PSA 10", rows: [], summary: { cards: 0, both_sources: 0, disagree: 0, stale: 0, single_source: 0, stale_after_days: 3 } } });
    const { container } = await show(<QualityPage />, "/pokemon/quality");
    await settle();
    expect(container.querySelector(".mx-elig .mx-statgrid")!.textContent).toContain("67qualify for the Top 100 (shown: 67, not padded)");
    expect(container.querySelector(".mx-elig .mx-why-list")!.textContent).toBe("1 price observation in the last year (needs 8)");
    expect(container.querySelector(".mx-events")!.textContent).toContain("▼ Left");
    await act(async () => screen.getByRole("button", { name: /Price history 6,875/ }).click());
    await settle();
    expect(calls.some((c) => c.includes("/pokemon/top100/quality") && c.includes("reason=history"))).toBe(true);
  });
});

describe("market cap by year", () => {
  it("lists years newest first with a bar per year, links to the year, and filters to Japanese", async () => {
    const year = (y: number, cap: number | null, over = {}) => ({ year: y, era: y <= 2003 ? "vintage" : "modern", cards: 10, cards_priced: 8, cards_with_cap: cap ? 5 : 0,
      market_cap: cap, market_cap_change_30d: 2.5, market_cap_change_90d: null, share: cap ? (cap / 400_000_00) * 100 : null, population: 100, sets: 2,
      languages: ["English", "Japanese"], top: [priced()], ...over });
    serve({ "/pokemon/years": { grade: "PSA 10", language: null, total_market_cap: 400_000_00,
                                years: [{ ...year(2023, 100_000_00), rank: 2 }, { ...year(1999, 300_000_00), rank: 1 }, year(1996, null, { languages: ["Japanese"] })] } });
    const { container } = await show(<YearsPage />, "/pokemon/years");
    const rows = [...container.querySelectorAll(".mx-years tbody tr")];
    expect(rows.map((r) => r.querySelector(".mx-year-link")!.textContent)).toEqual(["2023", "1999", "1996"]);
    expect(rows[1].querySelector(".mx-yearbar")!.textContent).toBe("$300K");
    expect((rows[1].querySelector(".mx-yearbar-fill") as HTMLElement).style.width).toBe("100%");
    expect(rows[0].querySelectorAll("td")[3].textContent).toBe("25.0%");
    expect(rows[2].querySelector(".mx-yearbar")!.textContent).toBe("—");
    expect(rows[0].querySelector(".mx-year-link")!.getAttribute("href")).toBe("/pokemon?year=2023");
    expect(container.textContent).toContain("is $400K");
    await act(async () => screen.getByRole("button", { name: /Japanese/ }).click());
    await settle();
    expect(where).toBe("/pokemon/years?language=Japanese");
    expect(calls.some((c) => c.includes("/pokemon/years?grade=PSA+10&language=Japanese"))).toBe(true);
  });
});

describe("card page (CoinGecko coin layout)", () => {
  const detail = (over: Partial<CardDetail> = {}): CardDetail => ({
    card: card(), headline: null, active_listings: [], reference_prices: [], total_market_cap: null, watched: false, other_grades: [], population: [],
    population_totals: {}, recent_sales: [], valuations: ["RAW NM", "PSA 8", "PSA 9", "PSA 10"].map((label) => val(label)), ...over,
  });
  const meta = { source: null, meta: null, reason: "Not linked to TCGdex yet." };

  it("shows the data-confidence checks, the market-cap formula, and eBay listings as asking prices", async () => {
    const quality = { eligible: false, score: 80, confidence: "medium", history_points: 3, failed: ["history"], reasons: ["3 price observations in the last year (needs 8)"],
      checks: [{ code: "price", label: "Reliable PSA 10 price", ok: true, detail: "Scrydex PSA 10 price" },
               { code: "history", label: "Price history", ok: false, detail: "3 price observations in the last year (needs 8)" }] };
    serve({ "/pokemon/cards/1/chart": chart(), "/pokemon/cards/1/meta": meta, "/pokemon/cards/1": detail(),
            "/pokemon/cards/1/quality": { card_id: 1, grade: "PSA 10", era: "vintage", quality, top100_rank: null, in_scope: true, min_history: 8, eligible_count: 67,
                                          market_cap: { price: 450_539_00, price_source: "scrydex", price_as_of: "2026-10-08", population: 123, population_source: "scrydex",
                                                        population_as_of: "2026-10-08T08:00:00Z", market_cap: 450_539_00 * 123 } },
            "/pokemon/cards/1/ebay": { connected: true, query: "1999 Pokemon Base Set Charizard 4/102 1st Edition PSA 10 -proxy", grade: "PSA 10", fetched: true, message: null,
                                       counts: { seen: 12, matched: 1, review: 2, rejected: 9 },
                                       sold: { access: false, message: "SOLD DATA UNAVAILABLE FROM CURRENT EBAY API ACCESS (Marketplace Insights is not granted to these keys)" },
                                       listings: [{ id: 5, listing_id: "v1|1|0", title: "1999 Pokemon Base Set 1st Edition Charizard #4 PSA 10", price: 499_999_00, currency: "USD",
                                                    condition: "Graded", seller: "slabking", listing_type: "fixed_price", url: "https://www.ebay.com/itm/1",
                                                    image_url: "https://i.ebayimg.test/1.jpg", observed_at: "2026-10-08T12:00:00Z", grade: "PSA 10" }] } });
    const { container } = await show(<PokemonCardPage />, "/pokemon/cards/1");
    await settle(); // confidence and eBay load after the card itself
    expect(container.querySelector(".mx-checks")!.textContent).toContain("✗ Price history 3 price observations in the last year (needs 8)");
    expect(container.querySelector(".mx-audit")!.textContent).toBe(
      "Market cap = $450,539 PSA 10 price (Scrydex estimate, 8 Oct 2026) × 123 PSA 10 population (Scrydex, 8 Oct 2026) = $55,416,297");
    const li = container.querySelector(".mx-ebay li")!;
    expect(li.textContent).toContain("$499,999");
    expect(li.querySelector(".mx-ebay-go")!.getAttribute("href")).toBe("https://www.ebay.com/itm/1");
    expect(screen.getByText(/eBay listings · PSA 10 \(asking prices, not sales\)/)).toBeTruthy();
    expect(container.textContent).toContain("SOLD DATA UNAVAILABLE FROM CURRENT EBAY API ACCESS");
    expect(container.textContent).toContain("12 results, 1 matched exactly, 2 for review, 9 other cards");
  });

  it("shows identity, rank, honest empty states and no invented chart", async () => {
    serve({ "/pokemon/cards/1/chart": chart(), "/pokemon/cards/1/meta": meta, "/pokemon/cards/1": detail() });
    const { container } = await show(<PokemonCardPage />, "/pokemon/cards/1");
    expect(container.querySelector(".mx-coin-name")!.textContent).toBe("Charizard PSA 10");
    expect(container.querySelector(".mx-rankpill")!.textContent).toBe("Unranked");
    expect(container.querySelector(".mx-coin-price strong")!.textContent).toBe("—");
    expect(container.querySelector(".mx-chart-empty")!.textContent).toContain("No PSA 10 price history in this range");
    expect(chartLog).toEqual([]); // nothing drawn without data
    expect(container.querySelector(".mx-crumbs")!.textContent).toBe("Markets› Base Set› Charizard PSA 10");
    expect(container.querySelector(".mx-coin-noimg")).not.toBeNull();
  });

  it("charts the estimate, the real sales as separate markers and daily volume; ranges and tabs are in the URL", async () => {
    const sale = { t: "2026-10-05T12:00:00Z", v: 300_000_00, id: 7, source: "goldin", verified: true, excluded: false, url: "https://goldin.test/lot/7", sale_type: "auction" };
    const base = chart();
    const c = chart({ rank: 3, line: [{ t: "2026-09-01T00:00:00Z", v: 280_000_00, kind: "carried" }, { t: "2026-10-05T00:00:00Z", v: 300_000_00, kind: "sale_day" }],
                      sales: [sale], volume: [{ t: "2026-10-05T00:00:00Z", count: 1, usd: 300_000_00 }],
                      summary: { ...base.summary, market_price: 300_000_00, confidence: "MEDIUM", changes: { ...base.summary.changes, "30d": change(4.2) } } });
    serve({ "/pokemon/cards/1/chart": c, "/pokemon/cards/1/meta": meta, "/pokemon/cards/1": detail(), "/pokemon/watchlist/1": { watched: true } });
    const { container } = await show(<PokemonCardPage />, "/pokemon/cards/1");
    expect(container.querySelector(".mx-rankpill")!.textContent).toBe("Rank #3");
    expect(container.querySelector(".mx-coin-price")!.textContent).toBe("$300,000▲4.2% up30D");
    const area = chartLog.find((l) => l.kind === "Area")!;
    expect(area.data).toEqual([{ time: ts("2026-09-01T00:00:00Z"), value: 280000, color: "rgba(57,255,136,0.45)" }, { time: ts("2026-10-05T00:00:00Z"), value: 300000 }]);
    const dots = chartLog.filter((l) => l.kind === "Line");
    expect(dots).toHaveLength(1);
    expect(dots[0].data).toEqual([{ time: ts("2026-10-05T12:00:00Z"), value: 300000 }]); // the sale at its own time and price
    expect(chartLog.find((l) => l.kind === "Histogram")).toMatchObject({ pane: 1, data: [{ time: ts("2026-10-05T00:00:00Z"), value: 300000 }] });

    await act(async () => screen.getByRole("button", { name: "30D" }).click());
    await settle();
    expect(where).toBe("/pokemon/cards/1?range=30d");
    expect(calls).toContain("GET /api/pokemon/cards/1/chart?grade=PSA+10&range=30d");
    await act(async () => screen.getByRole("tab", { name: "Sales" }).click());
    await settle();
    expect(where).toContain("tab=sales");
    const link = screen.getByRole("link", { name: "Goldin ↗" }) as HTMLAnchorElement;
    expect(link.href).toBe("https://goldin.test/lot/7");
    await act(async () => screen.getByText("☆ Add to watchlist").click());
    expect(calls).toContain("PUT /api/pokemon/watchlist/1");
  });

  it("with no verified sales, shows the Scrydex price with attribution and charts Scrydex's own history", async () => {
    const base = chart();
    const c = chart({ provider_line: [{ t: "2026-09-01T00:00:00Z", v: 390_000_00, kind: "history" }, { t: "2026-10-08T00:00:00Z", v: 400_000_00, kind: "current" }],
                      summary: { ...base.summary, market_price: 400_000_00, price_source: "scrydex", provider: { provider: "scrydex", name: "Scrydex", market: 400_000_00, low: 380_000_00, mid: 400_000_00, high: 420_000_00, currency: "USD", as_of: "2026-10-08",
      fetched_at: "2026-10-08T11:00:00Z", variant: "firstEditionShadowlessHolofoil", provider_card_id: "base1-4", trends: { "24h": 0, "7d": 2.5, "30d": -1.2, "90d": null } } } });
    serve({ "/pokemon/cards/1/chart": c, "/pokemon/cards/1/meta": meta, "/pokemon/cards/1": detail() });
    const { container } = await show(<PokemonCardPage />, "/pokemon/cards/1");
    expect(container.querySelector(".mx-coin-price strong")!.textContent).toBe("$400,000");
    expect(container.textContent).toContain("Scrydex PSA 10 market price (provider estimate, not a sale) · as of 8 Oct 2026 · range $380,000–$420,000");
    expect(container.textContent).toContain("Price sourceScrydex (provider estimate)");
    expect(chartLog.find((l) => l.kind === "Area")!.data).toEqual([{ time: ts("2026-09-01T00:00:00Z"), value: 390000 }, { time: ts("2026-10-08T00:00:00Z"), value: 400000 }]);
    expect(chartLog.filter((l) => l.kind === "Line")).toHaveLength(0); // no sale markers: these are not sales
    expect(container.querySelector(".mx-legend")!.textContent).toContain("Scrydex market price (PSA 10, provider estimate)");
    expect(container.querySelector(".mx-coverage")!.textContent).toBe(
      "2 points · 1 Sept 2026 → 8 Oct 2026 · source: Scrydex daily market valuations (provider estimates) · all prices in USD · Scrydex data fetched 8 Oct 2026 · 90D selected, but history on record covers only 38 days");
    expect(screen.getByText("Latest verified PSA 10 sales")).toBeTruthy();
    expect(container.textContent).toContain("No verified PSA 10 sales on record in this range. Prices shown above are estimates, not sales");
  });

  it("market cap mode draws only the cap line and never sale markers", async () => {
    const c = chart({ line: [{ t: "2026-10-05T00:00:00Z", v: 300_000_00, kind: "sale_day" }], cap_line: [{ t: "2026-10-05T00:00:00Z", v: 300_000_00 * 121, population: 121 }],
                      sales: [{ t: "2026-10-05T12:00:00Z", v: 300_000_00, id: 7, source: "goldin", verified: true, excluded: false, url: null, sale_type: null }] });
    serve({ "/pokemon/cards/1/chart": c, "/pokemon/cards/1/meta": meta, "/pokemon/cards/1": detail() });
    await show(<PokemonCardPage />, "/pokemon/cards/1");
    chartLog.length = 0;
    await act(async () => screen.getByRole("button", { name: "Market Cap" }).click());
    await settle();
    // one cap point and no sale markers: stated in words instead of a flat one-point chart
    expect(chartLog).toEqual([]);
    expect(document.querySelector(".mx-chart-empty")!.textContent).toContain("Market cap $36,300,000");
  });

  it("a cap line with two points is drawn as an area, without sale markers", async () => {
    const c = chart({ line: [{ t: "2026-10-05T00:00:00Z", v: 300_000_00, kind: "sale_day" }],
                      cap_line: [{ t: "2026-10-04T00:00:00Z", v: 290_000_00 * 121, population: 121 }, { t: "2026-10-05T00:00:00Z", v: 300_000_00 * 121, population: 121 }],
                      sales: [{ t: "2026-10-05T12:00:00Z", v: 300_000_00, id: 7, source: "goldin", verified: true, excluded: false, url: null, sale_type: null }] });
    serve({ "/pokemon/cards/1/chart": c, "/pokemon/cards/1/meta": meta, "/pokemon/cards/1": detail() });
    await show(<PokemonCardPage />, "/pokemon/cards/1");
    chartLog.length = 0;
    await act(async () => screen.getByRole("button", { name: "Market Cap" }).click());
    await settle();
    expect(chartLog.map((l) => l.kind)).toEqual(["Area"]);
    expect(chartLog[0].data).toEqual([{ time: ts("2026-10-04T00:00:00Z"), value: 35_090_000 }, { time: ts("2026-10-05T00:00:00Z"), value: 36_300_000 }]);
  });
});

describe("Market Pulse sidebar", () => {
  const status = (over: Partial<PulseStatus> = {}): PulseStatus => ({
    enabled: false, trusted_accounts: [], last_refresh_at: "2026-10-08T12:00:00Z", cooldown_seconds: 0, error: null, connected: true, posts_stored: 1,
    last_result: { posts_returned: 10, users_returned: 6, estimated_cost_usd: 0.11, kept: 4 },
    budget: { today_usd: 0.11, month_usd: 3, daily_usd: 5, monthly_usd: 20, left_usd: 4.89 }, ...over,
  });
  const post = { id: "101", username: "VintageSlabs", name: "Vintage Slabs", avatar: "https://pbs.test/a.jpg", verified: false, trusted: true,
    text: "Base Set 1st Edition Charizard PSA 10 sold at Goldin", created_at: "2026-10-08T11:00:00Z", url: "https://x.com/VintageSlabs/status/101",
    media: "https://pbs.test/card.jpg", metrics: { likes: 1200, reposts: 30, replies: 4, quotes: 1 }, categories: ["auctions", "vintage"] };

  it("shows posts that open the original X post, switches tabs, and refreshes with its cost", async () => {
    serve({ "/pokemon/pulse/refresh": { ok: true, kept: 2, estimated_cost_usd: 0.085, status: status({ cooldown_seconds: 300 }) }, "/pokemon/pulse": { posts: [post], status: status() } });
    const { container } = await show(<MarketPulse />);
    expect(screen.getByRole("heading", { name: "Pokémon Market Pulse" })).toBeTruthy();
    const link = container.querySelector(".mx-post") as HTMLAnchorElement;
    expect(link.href).toBe("https://x.com/VintageSlabs/status/101");
    expect(link.target).toBe("_blank");
    expect(link.textContent).toContain("@VintageSlabs");
    expect(link.textContent).toContain("♥ 1.2K");
    expect(container.querySelector(".mx-post-media")!.getAttribute("src")).toBe("https://pbs.test/card.jpg");
    await act(async () => screen.getByRole("tab", { name: "Popular" }).click());
    await settle();
    expect(calls).toContain("GET /api/pokemon/pulse?tab=popular&limit=30");
    await act(async () => screen.getByRole("button", { name: "Refresh Market Pulse" }).click());
    await settle();
    expect(calls).toContain("POST /api/pokemon/pulse/refresh");
    expect(container.querySelector(".mx-pulse-msg")!.textContent).toBe("2 new posts · est. cost $0.085");
  });

  it("keeps the refresh button disabled during the cooldown", async () => {
    serve({ "/pokemon/pulse": { posts: [post], status: status({ cooldown_seconds: 120 }) } });
    await show(<MarketPulse />);
    const btn = screen.getByRole("button", { name: "Refresh Market Pulse" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toMatch(/^[12]:[0-5]\d$/);
  });

  it("links a post to the cards it mentions", async () => {
    const withCards = { ...post, cards: [{ id: 1, name: "Charizard", set_name: "Base Set", edition: "1st Edition", image_url: null }] };
    serve({ "/pokemon/pulse": { posts: [withCards], status: status() } });
    const { container } = await show(<MarketPulse />);
    const chip = container.querySelector(".mx-post-card")!;
    expect(chip.textContent).toBe("Charizard");
    expect(chip.getAttribute("title")).toBe("Charizard · Base Set · 1st Edition");
  });

  it("says clearly when X access is unavailable", async () => {
    serve({ "/pokemon/pulse": { posts: [], status: status({ error: "X budget reached: $20.03 of the $20.00 monthly budget used", posts_stored: 0 }) } });
    await show(<MarketPulse />);
    expect(screen.getByRole("alert").textContent).toContain("X budget reached");
  });
});

describe("sound and motion", () => {
  it("the sound toggle is remembered, and sound never throws or plays without audio support", async () => {
    window.localStorage.removeItem("pc-market-sound");
    serve({});
    await show(<SoundToggle />);
    const btn = screen.getByRole("button", { name: "Sound on: turn off" });
    expect(btn.textContent).toBe("🔊");
    await act(async () => btn.click());
    expect(marketSoundOn()).toBe(false);
    expect(screen.getByRole("button", { name: "Sound off: turn on" }).textContent).toBe("🔇");
    expect(() => play("up")).not.toThrow(); // jsdom: no Web Audio, no user activation: silently nothing
    window.localStorage.removeItem("pc-market-sound");
  });

  it("starring a card pops the star", async () => {
    serve({ "/pokemon/market": market([priced()]), "/pokemon/watchlist/1": { watched: true }, "/pokemon/watchlist": { cards: [] } });
    const { container } = await show(<MarketHome />);
    await act(async () => screen.getByRole("button", { name: "Add Charizard to watchlist" }).click());
    await settle();
    const star = container.querySelector(".mx-table .mx-star")!;
    expect(star.className).toContain("is-on");
    expect(star.className).toContain("is-popping");
  });
});

describe("sparkline", () => {
  it("draws straight segments through the real observations only, coloured by their own change", () => {
    const pts: [number, number][] = [[0, 100], [1, 100], [2, 300], [3, 300], [4, 120], [5, 400], [6, 380], [7, 390]];
    const { container } = render(<Sparkline points={pts} height={40} />);
    const d = container.querySelector(".mx-spark-line")!.getAttribute("d")!;
    expect(d).not.toContain("C"); // no curves
    expect((d.match(/[ML]/g) ?? []).length).toBe(pts.length); // one vertex per observation: nothing added
    const ys = [...d.matchAll(/[ML]-?\d+(?:\.\d+)?,(-?\d+(?:\.\d+)?)/g)].map((m) => Number(m[1]));
    expect(Math.min(...ys)).toBeCloseTo(3); // the highest observation touches the top padding
    expect(Math.max(...ys)).toBeCloseTo(37); // the lowest the bottom
    expect(container.querySelector(".mx-spark")!.getAttribute("class")).toContain("mx-spark-up"); // 390 > 100
    expect(container.querySelector(".mx-spark-line")!.getAttribute("stroke-width")).toBe("1.3");
    expect(container.querySelector(".mx-spark-dot")).toBeNull(); // no oversized end dot
    expect(container.querySelector(".mx-spark-obs")).toBeNull(); // detailed enough: no markers
  });

  it("marks each observation when there are only a few, and goes red when the window ends lower", () => {
    const { container } = render(<Sparkline points={[[0, 500], [3, 520], [6, 410]]} />);
    expect(container.querySelector(".mx-spark")!.getAttribute("class")).toContain("mx-spark-down");
    expect(container.querySelector(".mx-spark-obs")!.getAttribute("d")!.match(/M/g)!.length).toBe(3);
    expect(container.querySelector(".mx-spark")!.getAttribute("aria-label")).toBe("Down 18.0% across 3 price observations");
    const one = render(<Sparkline points={[[0, 500]]} />);
    expect(one.container.querySelector(".mx-spark")).toBeNull(); // one observation: no chart, never a flat line
  });
});

describe("data sources", () => {
  it("shows connected / not connected and never a credential value", async () => {
    const src = (over: Partial<Source>): Source => ({ key: "psa", name: "PSA", connected: false, reason: "Not connected: add PSA_API_TOKEN.", serves: ["cert"],
      access: "x", licence: "y", credentials: ["PSA_API_TOKEN"], missing: ["PSA_API_TOKEN"], credentials_present: false, rate_limit: "100/day", notes: [],
      last_request_at: null, requests_today: 0, last_sync_at: null, last_success_at: null, records_imported: 0, last_error: null, ...over });
    serve({ "/pokemon/review": { items: [] }, "/pokemon/sources": { sources: [src({}), src({ key: "tcgplayer", name: "TCGplayer", credentials: [], missing: [], serves: [],
                                                           reason: "Not available: TCGplayer isn't accepting new API applications." }),
                                            src({ key: "pokemontcg", name: "Pokémon TCG API", connected: true, reason: "", credentials: [], missing: [] })] } });
    const { container } = await show(<PokemonSources />, "/pokemon/sources");
    const cards = [...container.querySelectorAll(".pm-source-title")].map((e) => e.textContent);
    expect(cards).toEqual(["PSA NOT CONNECTED", "TCGplayer NOT CONNECTED", "Pokémon TCG API CONNECTED"]);
    expect(container.textContent).toContain("TCGplayer isn't accepting new API applications");
    expect((container.querySelector(".pm-cred input[type=password]") as HTMLInputElement).value).toBe("");
    expect(screen.getByText("Connect PSA first")).toBeTruthy();
    expect(container.querySelector(".pm-source-facts")!.textContent).toContain("Credentials presentNO");
    expect(container.querySelector(".pm-source-facts")!.textContent).toContain("Rate limit100/day");
  });

  it("says plainly when eBay sold data is unavailable", async () => {
    serve({ "/pokemon/review": { items: [] }, "/pokemon/sources": { sources: [{ key: "ebay", name: "eBay", connected: true, reason: "", serves: ["get_listing_data"],
      access: "", licence: "", credentials: ["EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET"], missing: [], credentials_present: true, rate_limit: "5,000/day",
      notes: ["SOLD DATA UNAVAILABLE FROM CURRENT EBAY API ACCESS (Marketplace Insights is not granted to these keys)"], last_request_at: null, requests_today: 0,
      last_sync_at: null, last_success_at: null, records_imported: 12, last_error: null }] } });
    const { container } = await show(<PokemonSources />, "/pokemon/sources");
    expect(container.querySelector(".pm-source-note.is-warn")!.textContent).toContain("SOLD DATA UNAVAILABLE FROM CURRENT EBAY API ACCESS");
    expect(screen.getByText("Sync now")).toBeTruthy();
  });

  it("previews a CSV, asks for the exact card on review rows, and imports only confirmed rows", async () => {
    const rows = [
      { row: 1, input: {}, status: "ready", errors: [], warnings: [], card_id: 1, card: "1999 Base Set Charizard 4/102 1st Edition", confidence: 1, candidates: [],
        reasons: [], grade: "PSA 10", price: 300_000_00, currency: "USD", price_usd: 300_000_00, sale_date: "2026-10-04T00:00:00+00:00", source: "goldin" },
      { row: 2, input: {}, status: "review", errors: [], warnings: [], card_id: 3, card: "1999 Base Set Charizard 4/102 Unlimited", confidence: 0.6,
        candidates: [{ card_id: 1, label: "1999 Base Set Charizard 4/102 1st Edition", confidence: 0.6 }, { card_id: 3, label: "1999 Base Set Charizard 4/102 Unlimited", confidence: 0.6 }],
        reasons: ["edition not stated (1st Edition, Shadowless, Unlimited exist)"], grade: "PSA 9", price: 4000_00, currency: "USD", price_usd: 4000_00,
        sale_date: "2026-10-02T00:00:00+00:00", source: "ebay" },
      { row: 3, input: {}, status: "error", errors: ["PSA grades run 1 to 10 in half steps: '11'"], warnings: [], card_id: 1, card: null, confidence: 1,
        candidates: [], reasons: [] },
    ];
    serve({ "/pokemon/review": { items: [] }, "/pokemon/sources": { sources: [] },
            "/pokemon/import/csv/preview": { rows, counts: { ready: 1, review: 1, duplicate: 0, error: 1 }, total: 3 },
            "/pokemon/import/csv/commit": { imported: 2, skipped: 0, failed: 1, results: [] } });
    let committed: unknown = null;
    const inner = globalThis.fetch as unknown as (i: string, init?: RequestInit) => Promise<Response>;
    vi.stubGlobal("fetch", vi.fn(async (i: string, init?: RequestInit) => {
      if (i.endsWith("/csv/commit")) committed = JSON.parse(String(init?.body));
      return inner(i, init);
    }));
    const { container } = await show(<PokemonSources />, "/pokemon/sources");
    fireEvent.change(screen.getByLabelText("CSV text"), { target: { value: "card,set\nCharizard,Base Set" } });
    await act(async () => screen.getByText("Preview").click());
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(container.querySelector(".pm-csv-counts")!.textContent).toBe("1 ready 1 review needed 0 duplicates 1 errors");
    const boxes = [...container.querySelectorAll(".pm-csv-table input[type=checkbox]")] as HTMLInputElement[];
    expect(boxes.map((b) => [b.checked, b.disabled])).toEqual([[true, false], [false, true], [false, true]]); // review needs a card; errors never import
    fireEvent.change(screen.getByLabelText("Card for row 2"), { target: { value: "3" } });
    await act(async () => screen.getByText("Import 2 selected rows").click());
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(committed).toEqual({ csv: "card,set\nCharizard,Base Set", accept: [{ row: 1, card_id: null }, { row: 2, card_id: 3 }], verified: true });
    expect(screen.getByRole("status").textContent).toBe("2 imported · 0 skipped · 1 failed");
  });
});
