import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { refreshSession } from "../../../website/members";
import { AddCardsPage } from "./AddCards";
import { PortfolioPage } from "./PortfolioPage";
import { PhoneLinkPage, PhoneScanButton } from "./PhoneHandoff";
import type { Candidate, Holding, PCardBrief } from "./api";

/** TEST FIXTURES only: a fake API answering by path. */
let routes: Record<string, unknown> = {};
const calls: { method: string; path: string; body?: unknown }[] = [];

function serve(r: Record<string, unknown>) {
  routes = r;
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const path = String(input).split("?")[0];
    calls.push({ method: init?.method ?? "GET", path, body: typeof init?.body === "string" ? JSON.parse(init.body) : init?.body });
    const body = routes[`${init?.method ?? "GET"} ${path}`] ?? routes[path];
    return body === undefined ? new Response(JSON.stringify({ detail: "nope" }), { status: 404 }) : new Response(JSON.stringify(body), { status: 200 });
  }));
}

async function show(ui: React.ReactElement, at: string) {
  refreshSession();
  const r = render(<MemoryRouter initialEntries={[at]}><Routes><Route path="/pokemon/portfolio" element={ui} /><Route path="/pokemon/portfolio/add" element={ui} />
    <Route path="*" element={<div>other page</div>} /></Routes></MemoryRouter>);
  for (let i = 0; i < 4; i++) await act(async () => { await new Promise((res) => setTimeout(res, 0)); });
  return r;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const card = (over: Partial<PCardBrief> = {}): PCardBrief => ({
  id: 1, name: "Charizard", pokemon_name: "Charizard", set_name: "Base Set", set_code: "base1", card_number: "4/102", year: 1999, language: "English",
  edition: "1st Edition", variant: "", rarity: "Rare Holo", image_url: null, era: "vintage", ...over,
});
const cand = (c: PCardBrief, suggested = false): Candidate => ({ card: c, suggested, score: 1.1, reference_prices: { "PSA 10": 400_000_00 } });
const holding = (over: Partial<Holding> = {}): Holding => ({
  id: 9, card: card(), kind: "graded", grading_company: "PSA", grade: "9", label: "PSA 9", quantity: 1, added_at: "2026-10-09T00:00:00Z", source: "scan",
  verification: "self_reported", cert_status: "unchecked", condition: null, community_review: false, is_public: true, photos: [],
  value: { each: 60_000_00, low: null, high: null, total: 60_000_00, source: "scrydex", confidence: "medium", note: "Scrydex market estimate", valued_at: null },
  ...over,
});
const signedIn = { signed_in: true, member: { id: "m", username: "ash", xp: 10, rank: { level: 1, key: "egg", name: "Egg", min_xp: 0, description: "", art: {} } } };
const status = { scanner: true, condition: false, photos: true, scans_per_day: 40 };

describe("Pokémon Portfolio", () => {
  it("asks visitors to sign in with the existing Kiwi Passport login", async () => {
    serve({ "/api/members/session": { signed_in: false, member: null }, "/api/members/portfolio/status": status });
    await show(<PortfolioPage />, "/pokemon/portfolio");
    expect(screen.getByText("Start your Pokémon Portfolio")).toBeTruthy();
    expect(screen.getByText("Sign in to continue")).toBeTruthy();
  });

  it("a portfolio that fails to load offers a retry instead of loading forever", async () => {
    serve({ "/api/members/session": signedIn, "/api/members/portfolio/status": status });
    await show(<PortfolioPage />, "/pokemon/portfolio");
    expect(screen.queryByText("Loading your portfolio…")).toBeNull();
    const retry = screen.getByRole("button", { name: "Try again" });
    const before = calls.length;
    await act(async () => { fireEvent.click(retry); await new Promise((r) => setTimeout(r, 0)); });
    expect(calls.length).toBeGreaterThan(before);
  });

  it("scans a card, never preselects a printing it isn't sure of, and saves it after confirmation", async () => {
    vi.stubGlobal("createImageBitmap", undefined); // the shrink step falls back to the original file
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:test", revokeObjectURL: () => undefined }));
    serve({
      "/api/members/session": signedIn, "/api/members/portfolio/status": status,
      "POST /api/members/portfolio/scan": {
        candidates: [cand(card()), cand(card({ id: 2, edition: "Shadowless" })), cand(card({ id: 3, edition: "Unlimited" }))],
        confidence: "uncertain", needs_choice: true, reason: "the printing (e.g. 1st Edition vs Unlimited) can't be told from the photo: pick it",
        detected: { type: "graded", language: "EN", graded: { company: "PSA", grade: "9", cert: "123" } }, scan_id: 1, cached: false,
      },
      "POST /api/members/portfolio/holdings": holding({ card: card({ id: 3, edition: "Unlimited" }) }),
      "POST /api/members/portfolio/holdings/9/photos": { id: 1 },
    });
    const { container } = await show(<AddCardsPage />, "/pokemon/portfolio/add");
    const input = container.querySelector<HTMLInputElement>('input[type=file][capture="environment"]')!;
    await act(async () => {
      fireEvent.change(input, { target: { files: [new File([new Uint8Array([255, 216, 255])], "card.jpg", { type: "image/jpeg" })] } });
      for (let i = 0; i < 4; i++) await new Promise((res) => setTimeout(res, 0));
    });
    expect(screen.getByText("Which card is it?")).toBeTruthy();
    expect(container.querySelectorAll(".pf-cands .is-suggested")).toHaveLength(0); // the expensive printing is not chosen for them
    expect(screen.getByText(/Graded slab detected: PSA 9/)).toBeTruthy();
    const unl = [...container.querySelectorAll<HTMLButtonElement>(".pf-cands button")].find((b) => b.textContent!.includes("1999 · Base Set · 4/102 · Unlimited"))!;
    await act(async () => { fireEvent.click(unl); });
    const grade = container.querySelector<HTMLSelectElement>(".pf-form select:nth-of-type(1)");
    expect(grade).toBeTruthy();
    await act(async () => {
      fireEvent.click(screen.getByText("Add to Portfolio"));
      for (let i = 0; i < 4; i++) await new Promise((res) => setTimeout(res, 0));
    });
    const add = calls.find((c) => c.method === "POST" && c.path === "/api/members/portfolio/holdings")!;
    expect(add.body).toMatchObject({ card_id: 3, kind: "graded", grading_company: "PSA", grade: "9", cert_number: "123", source: "scan" });
    expect(calls.some((c) => c.path === "/api/members/portfolio/holdings/9/photos")).toBe(true); // the scan photo kept with the card
    expect(screen.getByText("Added to your portfolio")).toBeTruthy();
    expect(screen.getByText("📷 Scan Another")).toBeTruthy();
    expect(screen.getByText("View Portfolio")).toBeTruthy();
  });

  it("opens the camera once from ?scan=1, falls back gracefully, and Cancel doesn't reopen it", async () => {
    vi.stubGlobal("navigator", { ...navigator, mediaDevices: undefined }); // a browser without a live camera API
    vi.stubGlobal("isSecureContext", true); // pricechat.live is https
    serve({ "/api/members/session": signedIn, "/api/members/portfolio/status": status });
    const { container } = await show(<AddCardsPage />, "/pokemon/portfolio/add?scan=1");
    expect(screen.getByText(/can't show a live camera/)).toBeTruthy(); // explained, with the native camera as the way forward
    expect(container.querySelector('input[type=file][accept="image/*"][capture="environment"]')).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByText("Cancel")); });
    for (let i = 0; i < 3; i++) await act(async () => { await new Promise((res) => setTimeout(res, 0)); });
    expect(screen.queryByText(/can't show a live camera/)).toBeNull(); // no loop back into the camera
    expect(screen.getByText("📷 Scan Pokémon Card")).toBeTruthy();
  });

  it("keeps the chosen grade while the optional back photo is taken", async () => {
    vi.stubGlobal("createImageBitmap", undefined);
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => "blob:test", revokeObjectURL: () => undefined }));
    vi.stubGlobal("navigator", { ...navigator, mediaDevices: undefined });
    vi.stubGlobal("isSecureContext", true);
    serve({
      "/api/members/session": signedIn, "/api/members/portfolio/status": status,
      "POST /api/members/portfolio/scan": { candidates: [cand(card())], confidence: "uncertain", needs_choice: true, reason: "",
        detected: { type: "raw", language: "EN", graded: null }, scan_id: 1, cached: false },
      "/api/members/portfolio/quote": { value: 60_000_00, low: null, high: null, source: "scrydex", confidence: "medium", note: "", as_of: null },
      "POST /api/members/portfolio/holdings": holding(), "POST /api/members/portfolio/holdings/9/photos": { id: 1 },
    });
    const { container } = await show(<AddCardsPage />, "/pokemon/portfolio/add");
    const file = () => new File([new Uint8Array([255, 216, 255])], "c.jpg", { type: "image/jpeg" });
    await act(async () => {
      fireEvent.change(container.querySelector<HTMLInputElement>('input[type=file][capture="environment"]')!, { target: { files: [file()] } });
      for (let i = 0; i < 4; i++) await new Promise((res) => setTimeout(res, 0));
    });
    await act(async () => { fireEvent.click(container.querySelector<HTMLButtonElement>(".pf-cands button")!); });
    await act(async () => { fireEvent.click(screen.getByText("Graded")); });
    await act(async () => { fireEvent.change(container.querySelectorAll<HTMLSelectElement>(".pf-form select")[1], { target: { value: "9" } }); });
    await act(async () => { fireEvent.click(container.querySelector<HTMLButtonElement>(".pf-addback")!); });
    await act(async () => {  // the phone camera returns the back photo
      fireEvent.change(container.querySelector<HTMLInputElement>('.pf-camera input[type=file]')!, { target: { files: [file()] } });
      for (let i = 0; i < 4; i++) await new Promise((res) => setTimeout(res, 0));
    });
    await act(async () => {
      fireEvent.click(screen.getByText("Add to Portfolio"));
      for (let i = 0; i < 6; i++) await new Promise((res) => setTimeout(res, 0));
    });
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/members/portfolio/holdings")!.body).toMatchObject({ kind: "graded", grading_company: "PSA", grade: "9" });
    expect(calls.filter((c) => c.path === "/api/members/portfolio/holdings/9/photos")).toHaveLength(2); // front and back
  });

  it("phone side: the QR link signs the phone in and opens the scanner", async () => {
    serve({ "POST /api/members/portfolio/handoff/claim": { username: "ash" }, "/api/members/session": signedIn, "/api/members/portfolio/status": status });
    refreshSession();
    render(<MemoryRouter initialEntries={["/pokemon/portfolio/phone/tok123tok123tok123tok123"]}><Routes>
      <Route path="/pokemon/portfolio/phone/:token" element={<PhoneLinkPage />} />
      <Route path="/pokemon/portfolio/add" element={<div>scanner page</div>} /></Routes></MemoryRouter>);
    for (let i = 0; i < 4; i++) await act(async () => { await new Promise((res) => setTimeout(res, 0)); });
    expect(calls.find((c) => c.path === "/api/members/portfolio/handoff/claim")!.body).toEqual({ token: "tok123tok123tok123tok123" });
    expect(screen.getByText("scanner page")).toBeTruthy();
    expect(sessionStorage.getItem("pc-phone-scan")).toBe("ash");
  });

  it("computer side: shows a QR code, sees the phone connect, and lists the cards it adds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const dash = (holdings: Holding[]) => ({ portfolio: {}, summary: {}, top: [], composition: {}, recent: [], holdings, history: [] });
    serve({
      "/api/members/session": signedIn,
      "POST /api/members/portfolio/handoff": { id: 5, url: "https://pricechat.live/pokemon/portfolio/phone/abc", expires_at: new Date(Date.now() + 600_000).toISOString(), minutes: 10 },
      "/api/members/portfolio/handoff/5": { status: "connected", device: "iPhone" },
      "/api/members/portfolio": dash([]),
    });
    const { container } = render(<MemoryRouter><PhoneScanButton /></MemoryRouter>);
    await act(async () => { fireEvent.click(screen.getByText("📱 Scan with your phone")); });
    for (let i = 0; i < 6; i++) await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(calls.some((c) => c.method === "POST" && c.path === "/api/members/portfolio/handoff")).toBe(true);
    routes["/api/members/portfolio"] = dash([holding({ id: 77 })]);
    await act(async () => { await vi.advanceTimersByTimeAsync(4500); });
    for (let i = 0; i < 4; i++) await act(async () => { await vi.advanceTimersByTimeAsync(50); });
    expect(screen.getByText(/Your iPhone is connected/)).toBeTruthy();
    expect(container.ownerDocument.querySelectorAll(".pf-qr-added li")).toHaveLength(1);
    vi.useRealTimers();
  });

  it("adds a raw card from search with an honest estimate", async () => {
    serve({
      "/api/members/session": signedIn, "/api/members/portfolio/status": { ...status, scanner: false },
      "/api/members/portfolio/search": { query: "zard", results: [cand(card())] },
      "/api/members/portfolio/quote": { value: 6_000_00, low: 4_000_00, high: 9_000_00, source: "scrydex_raw", confidence: "low", note: "Ungraded market prices", as_of: null },
      "POST /api/members/portfolio/holdings": holding({ kind: "raw", grading_company: null, grade: null, label: "Raw", source: "manual",
        value: { each: 6_000_00, low: 4_000_00, high: 9_000_00, total: 6_000_00, source: "scrydex_raw", confidence: "low", note: "", valued_at: null } }),
    });
    const { container } = await show(<AddCardsPage />, "/pokemon/portfolio/add");
    expect(screen.getByText(/scanner isn't available/)).toBeTruthy();
    await act(async () => {
      fireEvent.change(container.querySelector(".pf-searchbox")!, { target: { value: "zard" } });
      await new Promise((res) => setTimeout(res, 350));
    });
    await act(async () => { fireEvent.click(container.querySelector<HTMLButtonElement>(".pf-cands button")!); });
    for (let i = 0; i < 3; i++) await act(async () => { await new Promise((res) => setTimeout(res, 0)); });
    expect(container.querySelector(".pf-quote")!.textContent).toContain("$6,000"); // the estimate BEFORE adding
    expect(container.querySelector(".pf-quote")!.textContent).toContain("Range $4,000 – $9,000");
    await act(async () => {
      fireEvent.click(screen.getByText("Add to Portfolio"));
      for (let i = 0; i < 4; i++) await new Promise((res) => setTimeout(res, 0));
    });
    expect(calls.find((c) => c.method === "POST" && c.path === "/api/members/portfolio/holdings")!.body).toMatchObject({ kind: "raw", grade: null, source: "manual" });
    expect(screen.getByText(/Estimated \$6,000 \(\$4,000–\$9,000\)/)).toBeTruthy();
  });
});
