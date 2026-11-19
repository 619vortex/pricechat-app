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
  chief: { enabled: false, status: "off", shortlisted: 14, queued_off: 14, reviewed: 0, spent_today: 0, daily_cap: 1, model: "claude-opus-5-5",
    cost: { candidates_per_day: 30, mean_input_tokens: 1100, output_tokens_assumed: 700, by_model: { "claude-opus-5-5": { per_review: 0.0184, per_day: 0.552, per_month: 16.56 } } },
    agreement: {}, labelled: { n: 0, gate_right: 0, editor_right: 0 }, invalid_reasons: 0, blocked_unverified: 0, disagreements: [] },
  feedback: { counts: { great: 1, boring: 0, wrong_image: 0, missed: 1 }, labels: { great: "Great", boring: "Boring", wrong_image: "Wrong image", missed: "Missed story" }, rows: [] },
  memory: { error: "OperationalError" },
  performance: { measured: 0, best: [], by_topic: [], by_hook: [], telegram: "not measurable", note: "No X post with metrics yet." },
  photo_gaps: [{ subject: "EQUINOR", candidates: 2, published: 1 }],
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
    expect(screen.getByText("Off until you approve it (no paid calls)")).toBeTruthy();
    expect(screen.getByText(/Opus 5.5: \$0.02 a story · \$0.55 a day · \$16.56 a month/)).toBeTruthy();
    expect(screen.getByText("1 Great · 0 Boring · 0 Wrong image · 1 Missed story")).toBeTruthy();
    expect(screen.queryByText("Story memory and X leads")).toBeNull();  // a part that failed is left out, the rest still shows
    expect(screen.getByText(/EQUINOR: 2 candidates, 1 published/)).toBeTruthy();
  });
});
