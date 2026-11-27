import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { locate } from "../../components/nav";
import { OriginalsPage, type OriginalDraft, type OriginalsList } from "./OriginalsPage";

/** TEST FIXTURES: made-up findings and figures. */
const draft = (over: Partial<OriginalDraft>): OriginalDraft => ({
  id: 7, finding_key: "rarity:BTC:30d:2026-11-24", format: "extremes", detector: "rarity", status: "pending",
  text: "Fixturecoin −40.0% in 30 days.\nIn Hyperliquid data since March 2023, only 1.1% of all 30-day stretches were this bad.", chars: 120,
  original_text: "same", writer: "template", score: 91.2, as_of: "2026-11-24", headline: "Fixturecoin −40.0% in 30 days", window: "30d",
  calculation: "30d return = 60 / 100 − 1 = −40.0%", sources: [{ name: "hyperliquid", label: "Hyperliquid", attribution: "Data: Hyperliquid", terms_url: "", commercial_ok: true }],
  attribution: ["Data: Hyperliquid"], rarity: 0.95, novelty: 1, notes: [], chart: { kind: "histogram_marker", title: "Every 30-day move" }, version: 1,
  created_at: new Date().toISOString(), decided_at: null, decided_by: "", reject_reason: "", writer_pending: false, gate_problems: [], ...over,
});

let calls: string[] = [];
function serve(drafts: OriginalDraft[]) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input}${init?.body ? " " + String(init.body) : ""}`);
    const body: OriginalsList = { drafts, counts: { pending: drafts.filter((d) => d.status === "pending").length, approved: 0, rejected: 0, regenerating: 0 },
      publishing: "manual", switches: { growth_enabled: false, growth_collect_enabled: false, intel_enabled: false } };
    if (String(input).includes("/generate")) return new Response(JSON.stringify({ drafted: 2, found: 5, errors: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
    return new Response(JSON.stringify(String(input).includes("/drafts/7/") ? drafts[0] : body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 20)); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Originals", () => {
  it("lives in Publishing next to X", () => {
    expect(locate("/x/originals").area.key).toBe("publishing");
    expect(locate("/x/originals").page.label).toBe("Originals");
  });

  it("shows the graphic, text, count, sources and calculation, and approve only calls approve", async () => {
    serve([draft({})]);
    render(<OriginalsPage />);
    await settle();
    expect(screen.getByTestId("tweet-text").textContent).toContain("−40.0% in 30 days");
    expect(screen.getByText("120/280")).toBeTruthy();
    const img = screen.getByRole("img") as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("/api/originals/drafts/7/image.png?fmt=x&v=1");
    expect(screen.getByText(/Hyperliquid/, { selector: "div" })).toBeTruthy();
    expect(screen.getByText(/60 \/ 100 − 1/)).toBeTruthy();
    expect(screen.getByText(/Nothing here is posted automatically/)).toBeTruthy();
    fireEvent.click(screen.getByText("Approve"));
    await settle();
    expect(calls.some((c) => c.startsWith("POST /api/originals/drafts/7/approve"))).toBe(true);
    expect(calls.every((c) => !/twitter|x\.com|telegram/i.test(c))).toBe(true);
  });

  it("shows that the draft's data was re-read from its source", async () => {
    serve([draft({ verification: { ok: true, at: "2026-11-27T10:15:00+00:00", values_checked: 1369, problems: [],
      series: { "px:BTC:close": { source: "Hyperliquid candleSnapshot" } } } })]);
    render(<OriginalsPage />);
    await settle();
    expect(screen.getByText("Data verified")).toBeTruthy();
    expect(screen.getByTestId("verification").textContent).toContain("1,369 stored values re-read from Hyperliquid candleSnapshot at 2026-11-27 10:15 UTC; every one matched");
  });

  it("edits through the API and regenerates with or without a new chart", async () => {
    serve([draft({})]);
    render(<OriginalsPage />);
    await settle();
    fireEvent.click(screen.getByText("Edit"));
    fireEvent.change(screen.getByLabelText("Tweet text"), { target: { value: "Fixturecoin −40.0% in 30 days." } });
    fireEvent.click(screen.getByText("Save"));
    await settle();
    expect(calls.some((c) => c.startsWith("POST /api/originals/drafts/7/edit") && c.includes("Fixturecoin"))).toBe(true);
    fireEvent.click(screen.getByText("Other chart"));
    await settle();
    expect(calls.some((c) => c.includes("/regenerate") && c.includes('"chart_variant":true'))).toBe(true);
  });

  it("approved drafts offer copy text and image downloads", async () => {
    serve([draft({ status: "approved" })]);
    render(<OriginalsPage />);
    await settle();
    expect(screen.getByText("Copy text")).toBeTruthy();
    const dl = screen.getByText("Download image") as HTMLAnchorElement;
    expect(dl.getAttribute("href")).toContain("fmt=x");
    expect(dl.hasAttribute("download")).toBe(true);
    expect((screen.getByText("Portrait image") as HTMLAnchorElement).getAttribute("href")).toContain("fmt=portrait");
    expect(screen.queryByText("Approve")).toBeNull();
  });

  it("falls back to a note when the renderer isn't installed, and Generate now posts", async () => {
    serve([draft({})]);
    render(<OriginalsPage />);
    await settle();
    fireEvent.error(screen.getByRole("img"));
    expect(screen.getByText(/Graphic not available yet/)).toBeTruthy();
    fireEvent.click(screen.getByText("Generate now"));
    await settle();
    expect(calls.some((c) => c.startsWith("POST /api/originals/generate"))).toBe(true);
    expect(screen.getByText(/2 new drafts from 5 findings/)).toBeTruthy();
  });

  it("shows gate problems and an empty state", async () => {
    serve([draft({ gate_problems: ["figure 9% isn't in the finding"] })]);
    render(<OriginalsPage />);
    await settle();
    expect(screen.getByText(/Figures gate: figure 9%/)).toBeTruthy();
    cleanup();
    serve([]);
    render(<OriginalsPage />);
    await settle();
    expect(screen.getByText(/No drafts to review/)).toBeTruthy();
  });
});
