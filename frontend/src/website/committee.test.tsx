import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("lightweight-charts", () => {
  const series = { setData: vi.fn(), createPriceLine: vi.fn(() => ({})), removePriceLine: vi.fn(), priceScale: () => ({ applyOptions: vi.fn() }) };
  const chart = { addSeries: vi.fn(() => series), subscribeClick: vi.fn(), remove: vi.fn(), timeScale: () => ({ fitContent: vi.fn() }) };
  return { createChart: vi.fn(() => chart), CandlestickSeries: {}, ColorType: { Solid: "solid" }, LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 } };
});

import fixture from "./committee.fixture.json";
import { CommitteeSection, POLL_MAX_MS, POLL_MS, RunDetail, safeUrl, useRunPoll, type Run, type RunSummary } from "./committee";
import { refreshSession } from "./members";

const RUN = fixture as unknown as Run;  // a real rules-backend run: DOGE 4h Hyperliquid perp, verdict WAIT
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const SUMMARY: RunSummary = { id: RUN.id, status: "done", asset: RUN.asset, timeframe: RUN.timeframe, created_at: "2026-10-23T03:36:17Z",
  price_at: 0.079475, verdict: "WAIT", rating: "Underweight", exec_status: "NOT_APPLICABLE", outcomes: {} };

let calls: string[] = [];
function stubFetch(runFor: (n: number) => Run) {
  let n = 0;
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    calls.push(url);
    if (url.startsWith("/api/committee/runs/")) return json(runFor(n++));
    if (url.startsWith("/api/committee/runs")) return json({ runs: [SUMMARY] });
    if (url.startsWith("/api/committee/status")) return json({ enabled: true, backend: "rules", backends: {}, providers: { hyperliquid: { ok: true }, news: { ok: false } },
      budget: { calls_today: 3, calls_limit: 300, usd_today: 0, usd_limit: 2 } });
    if (url.startsWith("/api/committee/track")) return json({ by_verdict: { WAIT: { "1h": { n: 2, hits: null, hit_rate: null, avg_ret_pct: -0.4 } },
      LONG: { "1h": { n: 3, hits: 2, hit_rate: 0.667, avg_ret_pct: 0.5 } } }, horizons: ["1h", "4h", "24h", "7d"], scored_runs: 5, recent: [SUMMARY],
      note: "WAIT is tracked as no position: its row shows the move that followed." });
    if (url.startsWith("/api/members/session")) return json({ signed_in: false, member: null });
    return json({ detail: "nope" }, 404);
  }));
}

const runCalls = () => calls.filter((u) => u.startsWith(`/api/committee/runs/${RUN.id}`)).length;

beforeEach(() => { refreshSession(); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("committee run detail", () => {
  it("renders the Phase-6 layout from a real run: leans, theses, risk committee, verdict, both scenarios and why no trade", () => {
    stubFetch(() => RUN);
    render(<RunDetail run={RUN} err={null} gaveUp={false} />);
    const verdict = screen.getByLabelText("Verdict");
    expect(within(verdict).getByRole("heading", { name: /DOGE/ })).toBeTruthy();
    expect(within(verdict).getByText("WAIT")).toBeTruthy();
    expect(within(verdict).getByText("Rating Underweight")).toBeTruthy();
    expect(within(verdict).getByText(/Research only — nothing was traded/)).toBeTruthy();
    for (const a of ["Technical", "Fundamental", "News", "Sentiment"]) expect(screen.getByLabelText(`${a} analyst`)).toBeTruthy();
    expect(within(screen.getByLabelText("Technical analyst")).getByText("Bearish")).toBeTruthy();
    expect(screen.getByLabelText("Bull thesis").textContent).toContain("The bull case is thin");
    expect(screen.getByLabelText("Bear thesis").textContent).toContain("technical");
    expect(screen.getByRole("heading", { name: "Risk committee" })).toBeTruthy();
    // WAIT: both potential setups with their levels and an expandable derivation
    const long = screen.getByLabelText("Potential LONG");
    const short = screen.getByLabelText("Potential SHORT");
    expect(long.textContent).toContain("0.080107");
    expect(short.textContent).toContain("0.079318");
    expect(within(long).getByText("How derived")).toBeTruthy();
    expect(long.textContent).toContain(RUN.verdict!.scenarios.long!.trigger_rule);
    const why = screen.getByLabelText("No trade approved");
    expect(why.textContent).toContain("No trade approved because");
    expect(why.textContent).toContain("no trade proposed");
    expect(screen.getByRole("heading", { name: "What changes our view" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Download HTML report" }).getAttribute("href")).toBe(`/api/committee/runs/${RUN.id}/report.html`);
  });

  it("evidence refs expand to their sources with timestamps and only http(s) links", () => {
    stubFetch(() => RUN);
    render(<RunDetail run={RUN} err={null} gaveUp={false} />);
    const tech = screen.getByLabelText("Technical analyst");
    fireEvent.click(within(tech).getByText("Evidence (4)"));
    const chip = within(tech).getAllByRole("button", { name: /Sources T1/ })[0];
    expect(chip.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(chip);
    expect(chip.getAttribute("aria-expanded")).toBe("true");
    const link = within(tech).getByRole("link", { name: /DOGE perpetual 1h candles/ });
    expect(link.getAttribute("href")).toBe("https://api.hyperliquid.xyz/info");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(safeUrl("javascript:alert(1)")).toBeNull();
    expect(safeUrl("data:text/html,x")).toBeNull();
  });

  it("a running run shows progress, and a stale one says it stopped checking", () => {
    const running = { ...RUN, status: "running" } as Run;
    const { rerender } = render(<RunDetail run={running} err={null} gaveUp={false} />);
    expect(screen.getByText(/The committee is meeting/)).toBeTruthy();
    rerender(<RunDetail run={running} err={null} gaveUp />);
    expect(screen.getByText(/Stopped checking/)).toBeTruthy();
  });
});

function Probe({ id }: { id: string }) {
  const { run, gaveUp } = useRunPoll(id);
  return <p>{run?.status ?? "none"}{gaveUp ? " gave-up" : ""}</p>;
}

describe("polling", () => {
  it("polls every 3 s and stops once the run is done", async () => {
    vi.useFakeTimers();
    stubFetch((n) => (n < 2 ? ({ ...RUN, status: "running" } as Run) : RUN));
    render(<Probe id={RUN.id} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(runCalls()).toBe(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS); });
    expect(runCalls()).toBe(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS); });
    expect(runCalls()).toBe(3);
    expect(screen.getByText("done")).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS * 10); });
    expect(runCalls()).toBe(3);
  });

  it("gives up after 5 minutes and pauses while the tab is hidden", async () => {
    vi.useFakeTimers();
    stubFetch(() => ({ ...RUN, status: "running" } as Run));
    render(<Probe id={RUN.id} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS * 2); });
    const before = runCalls();
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS * 5); });
    expect(runCalls()).toBe(before);  // hidden: nothing fetched
    Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
    await act(async () => { document.dispatchEvent(new Event("visibilitychange")); await vi.advanceTimersByTimeAsync(0); });
    expect(runCalls()).toBe(before + 1);
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MAX_MS); });
    const atEnd = runCalls();
    expect(screen.getByText(/gave-up/)).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS * 20); });
    expect(runCalls()).toBe(atEnd);
  });
});

describe("committee section", () => {
  it("is labelled as research, lists runs and the track record with sample sizes, and asks signed-out visitors to sign in", async () => {
    stubFetch(() => RUN);
    const runs = { data: { runs: [SUMMARY] }, error: null, loading: false, reload: vi.fn(async () => undefined) };
    render(<CommitteeSection runs={runs} shown={RUN.id} onPick={vi.fn()} />);
    expect(screen.getByText(/Research — not your trades or P&L/)).toBeTruthy();
    expect(await screen.findByText("Sign in to ask")).toBeTruthy();
    expect(await screen.findByLabelText("Verdict")).toBeTruthy();
    expect(await screen.findByText(/2\/3 right/)).toBeTruthy();
    expect(screen.getByText(/no position · move/)).toBeTruthy();
    expect(screen.getByText(/missing news/)).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /DOGE/ }).length).toBe(2);  // latest runs + historical recommendations
  });
});
