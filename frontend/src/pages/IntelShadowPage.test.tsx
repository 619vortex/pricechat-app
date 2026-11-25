import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { locate } from "../components/nav";
import { IntelShadowPage, type IntelDashboard, type ShadowItem } from "./IntelShadowPage";

/** TEST FIXTURES: made-up stories, models and spend. */
const item = (over: Partial<ShadowItem>): ShadowItem => ({ id: 1, lane: "newsdesk", ref: "event:1", title: "Fixture Corp agrees $40 billion takeover", lane_ok: false,
  gate_ok: true, agree: false, tier: "major", state: "done", decider: "pc", model: "qwen3.8:27b", latency_ms: 4200, event_type: "merger_acquisition",
  lane_reason: "why should anyone care?", gate_reasons: ["A stated $40B takeover."], hard_veto: [], seen: 1, last_seen: new Date().toISOString(), ...over });

const MISS = item({});
const FALSE_POS = item({ id: 2, ref: "event:2", title: "Fixture minister visits fixture town", lane_ok: true, gate_ok: false, tier: "noise", decider: "cloud",
  model: "claude-haiku-5-5", latency_ms: 1800, gate_reasons: ["routine visit"] });

const FULL: IntelDashboard = {
  generated_at: new Date().toISOString(), hours: 24, since: new Date().toISOString(), mode: "shadow", banner: "Shadow mode — AI decisions are recorded only; nothing is published by AI",
  switches: { intel_enabled: true, intel_shadow_enabled: true, breaking_cloud_first: true, cloud_model: "claude-haiku-5-5", token_configured: true, pc_online: true },
  decisions: { total: 3, by_decider: { pc: 1, cloud: 1, rules: 1, pending: 0 }, by_model: { "qwen3.8:27b": 1, "claude-haiku-5-5": 1 },
    tiers: { major: 1, significant: 0, notable: 0, noise: 2, unknown: 0 }, model_tiers: { major: 1, significant: 0, notable: 0, noise: 1, unknown: 0 },
    by_lane: { newsdesk: 3 }, recent: [MISS, FALSE_POS] },
  disagreements: { counts: { total: 3, agree: 1, rules_only: 1, semantic_only: 1, pending: 0, by_source: {}, by_lane: {} }, decided: 3, agreement_rate: 0.3333,
    lane_yes_ai_no: [{ lane: "newsdesk", ref: "event:2", title: FALSE_POS.title, rules_ok: true, semantic_ok: false, tier: "noise", source: "model:claude-haiku-5-5",
      rules_reason: "", semantic_reasons: ["routine visit"], hard_veto: [], seen: 1, last_seen: null }],
    lane_no_ai_yes: [{ lane: "newsdesk", ref: "event:1", title: MISS.title, rules_ok: false, semantic_ok: true, tier: "major", source: "model:qwen3.8:27b",
      rules_reason: "why should anyone care?", semantic_reasons: [], hard_veto: ["a repeat on X"], seen: 2, last_seen: null }], prompt_version: "p1" },
  latency: { groups: [{ route: "pc", model: "qwen3.8:27b", kind: "intel.analyze", n: 4, p50_ms: 4200, p95_ms: 9100, max_ms: 9100, compute_p50_ms: 3100, compute_p95_ms: 5000 }],
    jobs: { total: 5, by_status: { done: 4, expired: 1 }, by_result: { pc: 3, cloud: 1 }, refused_over_cap: 2 } },
  workers: [{ id: 1, name: "Owner PC", enabled: true, online: true, ready: true, last_seen_at: new Date().toISOString(), models: ["qwen3.8:27b"], busy: false,
    version: "1.0", gpu: "Fixture GPU", jobs: { claimed: 5, completed: 3, failed: 1, expired_or_taken: 1, in_flight: 0 },
    availability: { online_seconds: 77760, ready_seconds: 70000, window_seconds: 86400, covered_seconds: 86400, history_since: null, pct_online: 0.9, pct_ready: 0.81 } }],
  pc_jobs: { claimed: 5, completed: 3, failed: 1, expired_or_taken: 1, in_flight: 0 },
  missed: { possible_misses: [MISS], possible_false_positives: [FALSE_POS], misses_total: 1, false_positives_total: 1 },
  spend: { today: { day: "2026-11-25", spent_usd: 1.2, cap_usd: 3, over_cap: false }, cap_usd: 3, window_usd: 1.75, window_calls: 9,
    per_model: [{ model: "claude-haiku-5-5", usd: 1.75, calls: 9, tokens_in: 9000, tokens_out: 1200 }], per_day: [{ day: "2026-11-25", usd: 1.2, calls: 6 }],
    refused_over_cap: 2 },
};

const EMPTY: IntelDashboard = {
  ...FULL,
  switches: { ...FULL.switches, intel_enabled: false, intel_shadow_enabled: false, pc_online: false },
  decisions: { total: 0, by_decider: { pc: 0, cloud: 0, rules: 0, pending: 0 }, by_model: {}, tiers: {}, model_tiers: {}, by_lane: {}, recent: [] },
  disagreements: { counts: { total: 0, agree: 0, rules_only: 0, semantic_only: 0, pending: 0, by_source: {}, by_lane: {} }, decided: 0, agreement_rate: null,
    lane_yes_ai_no: [], lane_no_ai_yes: [], prompt_version: "p1" },
  latency: { groups: [], jobs: { total: 0, by_status: {}, by_result: {}, refused_over_cap: 0 } },
  workers: [], missed: { possible_misses: [], possible_false_positives: [], misses_total: 0, false_positives_total: 0 },
  spend: { ...FULL.spend, today: { day: "2026-11-25", spent_usd: 0, cap_usd: 3, over_cap: false }, window_usd: 0, window_calls: 0, per_model: [], refused_over_cap: 0 },
};

let calls: string[] = [];
function serve(body: IntelDashboard) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${input}`);
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }));
}
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 20)); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Intelligence (shadow)", () => {
  it("lives in System as a tool page", () => {
    expect(locate("/intelligence").area.key).toBe("system");
    expect(locate("/intelligence").page.label).toBe("Intelligence (shadow)");
  });

  it("shows the shadow banner, switches, decisions, disagreements, misses, latency, the PC and spend", async () => {
    serve(FULL);
    render(<IntelShadowPage />);
    await settle();
    const text = document.body.textContent ?? "";
    expect(screen.getByRole("note").textContent).toMatch(/Shadow mode — AI decisions are recorded only; nothing is published by AI/);
    expect(screen.getByText("intel_enabled: ON")).toBeTruthy();
    expect(screen.getByText("intel_shadow_enabled: ON")).toBeTruthy();
    expect(text).toMatch(/Agreement rate33%/);
    expect(text).toMatch(/Hard veto: a repeat on X/);
    expect(text).toMatch(/Possible misses.*\(1\)/);
    expect(text).toMatch(/Possible false positives.*\(1\)/);
    expect(text).toMatch(/4\.2 s/);  // p50
    expect(text).toMatch(/9\.1 s/);  // p95
    expect(text).toMatch(/90%/);  // online share of the window
    expect(text).toMatch(/\$1\.20 of \$3\.00/);
    expect(text).toMatch(/Refused over cap2/);
    expect(calls).toEqual(["GET /api/intel/dashboard?hours=24"]);
  });

  it("asks for a new window when one is picked, and only reads (GET)", async () => {
    serve(FULL);
    render(<IntelShadowPage />);
    await settle();
    fireEvent.change(screen.getByRole("combobox", { name: "Window" }), { target: { value: "336" } });
    await settle();
    expect(calls).toContain("GET /api/intel/dashboard?hours=336");
    expect(calls.every((c) => c.startsWith("GET "))).toBe(true);
  });

  it("renders the empty state when intel is off", async () => {
    serve(EMPTY);
    render(<IntelShadowPage />);
    await settle();
    const text = document.body.textContent ?? "";
    expect(screen.getByText("intel_enabled: off")).toBeTruthy();
    expect(screen.getByText("intel_shadow_enabled: off")).toBeTruthy();
    expect(text).toMatch(/nothing is published by AI/);
    expect(text).toMatch(/No PC worker has connected yet/);
    expect(text).toMatch(/No possible miss in this window/);
    expect(text).toMatch(/\$0\.00 of \$3\.00/);
  });
});
