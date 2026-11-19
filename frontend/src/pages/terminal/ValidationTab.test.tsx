import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ValidationTab, type ValidationData } from "./ValidationTab";

/** TEST FIXTURES: one real refused headline (Telegram 198) and one made-up flagged picture. */
const DATA: ValidationData = {
  candidates: { count: 12, target: 30, published: 2, flagged: 1 }, images: { count: 1, target: 10, flagged: 1 }, extraordinary_live: false, vision: "off",
  rows: [{ id: 812, title: "Saudi-led coalition intercepts Houthi missiles targeting Riyadh and south", decision: "REJECT", interest: 20, evidence: "two outlets", outlets: 2,
    virality: 61, why: "why would our audience care? a routine military update: intercepts, with nothing hit and no casualties stated",
    extraordinary: "not a discovery, a revelation or an unexpected development", extraordinary_would_publish: false, flag: "", seen_at: new Date().toISOString() }],
  image_rows: [{ platform: "telegram", ref: "301", headline: "Gold falls 3%", link: "https://t.me/x/301", library: "microsoft", credit: "Photo: X", problems: ["off-subject: the microsoft photo for a story about gold"],
    verdict: "FLAG", flag: "bad image", at: new Date().toISOString() }],
  report: null,
};

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Settings › Validation", () => {
  it("shows progress, every candidate's decision and reason, and flagged pictures", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(DATA), { status: 200, headers: { "Content-Type": "application/json" } })));
    render(<ValidationTab />);
    expect(await screen.findByText("12 / 30")).toBeTruthy();
    expect(screen.getByText("Shadow (judged, never published)")).toBeTruthy();
    expect(screen.getByText("Off (no paid calls)")).toBeTruthy();
    expect(screen.getByText(/routine military update/)).toBeTruthy();
    expect(screen.getByText(/off-subject: the microsoft photo/)).toBeTruthy();
    expect(screen.getByText(/report is made automatically when 30/)).toBeTruthy();
  });
});
