import { act, cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContentHub } from "../pages/hubs/ContentHub";
import { PublishingHub } from "../pages/hubs/PublishingHub";
import { XPage } from "../pages/XPage";
import { Layout } from "./Layout";

/** TEST FIXTURES only: fake API answers keyed by path (everything else is a 404, which the embedded pages show as an error). */
type Routes_ = Record<string, unknown>;
const HOME = {
  todo: [
    { key: "drafts", label: "Content drafts to review", count: 2, to: "/content" },
    { key: "memories", label: "Community memories to review", count: 1, to: "/content" },
    { key: "signals", label: "Trading signals waiting for your OK", count: 0, to: "/trading-os" },
  ],
  status: { kill_switch: false, telegram: "connected", x: "not_configured", ai: true, last_scan_at: new Date().toISOString(), failing_jobs: [] },
  numbers: { members: 1, new_members_7d: 0, signals_7d: 0, open_signals: 0 },
};
const BM = {
  automation: { running: true, status: "running", reason: "", started_at: null, last_tick_at: null, emergency_stop: false, upload_consent: true, blocking: [], warnings: [] },
  stats: { created_today: 3, published_today: 2, sent_to_drafts_today: 0, upcoming: 4, total_views: null, followers: null, followers_gained: null, best_video: null, measured_videos: 0 },
};
const SM = { worker: { registered: 1, online: true, name: "pc", last_seen_at: null, video_ai: null, voices: [], writer: null }, counts: { queued: 1, rendering: 0, ready: 5, failed: 0 } };

function serve(extra: Routes_ = {}) {
  const table: Routes_ = { "/api/admin/home": HOME, "/api/brand-manager/overview": BM, "/api/short-market/overview": SM, ...extra };
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const url = String(input).split("?")[0];
    calls.push(url);
    if (url in table) return new Response(JSON.stringify(table[url]), { status: 200 });
    return new Response(JSON.stringify({ detail: "not in this test" }), { status: 404 });
  }));
  return calls;
}
const flush = async () => { for (let i = 0; i < 8; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function shell(path: string, routes: ReactNode) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Layout />}>{routes}</Route>
      </Routes>
    </MemoryRouter>,
  );
}

const contentRoutes = (
  <>
    <Route path="content" element={<ContentHub />} />
    <Route path="brain" element={<ContentHub />} />
    <Route path="knowledge" element={<ContentHub />} />
    <Route path="brand-manager/*" element={<div data-testid="bm-tool">Brand Manager tool</div>} />
  </>
);

describe("Content: one page instead of five tabs (stage 2)", () => {
  it("has no section tabs, and shows live cards from the existing APIs", async () => {
    serve();
    const { container } = shell("/content", contentRoutes);
    await flush();
    expect(container.querySelector(".area-head")).toBeNull();
    expect(container.querySelector(".subnav")).toBeNull();
    expect(screen.getByRole("heading", { level: 1, name: "Content" })).toBeTruthy();
    const bm = screen.getByLabelText("Brand Manager");
    expect(bm.textContent).toContain("Made today3");
    expect(bm.textContent).toContain("Published today2");
    expect(screen.getByLabelText("Short Market").textContent).toContain("Ready5");
    const review = screen.getByLabelText("Waiting for review");
    expect(review.textContent).toContain("Content drafts to review");
    expect(review.textContent).toContain("Community memories to review");
    // Ideas & drafts opens by default; AI voice and Knowledge are folded and not loaded
    expect((container.querySelector("#panel-ideas") as HTMLDetailsElement).open).toBe(true);
    expect((container.querySelector("#panel-ai") as HTMLDetailsElement).open).toBe(false);
    expect(container.querySelector("#panel-ai .hub-panel-body")).toBeNull();
    expect(container.querySelector(".nav-link.active")!.textContent).toContain("Content");
  });

  it("opens the AI voice panel for the old /brain address, and Knowledge for /knowledge", async () => {
    serve();
    const { container, unmount } = shell("/brain", contentRoutes);
    await flush();
    expect((container.querySelector("#panel-ai") as HTMLDetailsElement).open).toBe(true);
    expect(container.querySelector(".nav-link.active")!.textContent).toContain("Content");
    unmount();
    const k = shell("/knowledge", contentRoutes);
    await flush();
    expect((k.container.querySelector("#panel-knowledge") as HTMLDetailsElement).open).toBe(true);
  });

  it("shows Brand Manager's emergency stop on its card when it is on", async () => {
    serve({ "/api/brand-manager/overview": { ...BM, automation: { ...BM.automation, emergency_stop: true, running: false, status: "stopped" } } });
    shell("/content", contentRoutes);
    await flush();
    expect(screen.getByLabelText("Brand Manager").textContent).toContain("emergency stop on");
  });

  it("gives full tools a back link instead of tabs", async () => {
    serve();
    const { container } = shell("/brand-manager/studio", contentRoutes);
    await flush();
    expect(screen.getByTestId("bm-tool")).toBeTruthy();
    const back = container.querySelector(".hub-back")!;
    expect(back.textContent).toBe("← Content");
    expect(back.getAttribute("href")).toBe("/content");
    expect(container.querySelector(".area-head")).toBeNull();
  });
});

const XOP = { x_status: "connected", username: "thepricechat", user_context: true, write_capability: {}, social_actions_active: true, dry_run: true, mode: "supervised", automatic: "locked" };
const HOME_SIGNALS = { ...HOME, todo: [...HOME.todo.filter((t) => t.key !== "signals"), { key: "signals", label: "Trading signals waiting for your OK", count: 2, to: "/trading-os" }], numbers: { ...HOME.numbers, open_signals: 3, signals_7d: 9 } };
const publishingRoutes = (
  <>
    <Route path="publishing" element={<PublishingHub />} />
    <Route path="youtube" element={<PublishingHub />} />
    <Route path="website" element={<PublishingHub />} />
    <Route path="x" element={<XPage />} />
  </>
);

describe("Publishing: one page instead of four tabs (stage 3)", () => {
  it("shows X's mode and signals waiting, with no section tabs", async () => {
    serve({ "/api/admin/home": HOME_SIGNALS, "/api/x/operator": XOP });
    const { container } = shell("/publishing", publishingRoutes);
    await flush();
    expect(container.querySelector(".area-head")).toBeNull();
    const x = screen.getByLabelText("X / Twitter");
    expect(x.textContent).toContain("connected");
    expect(x.textContent).toContain("DRY RUN");  // the live / dry-run state is visible on the page, never folded away
    const sig = screen.getByLabelText("Signals");
    expect(sig.textContent).toContain("Open now3");
    expect(sig.textContent).toContain("Trading signals waiting for your OK");
    expect(screen.getByLabelText("News Desk (Telegram + X)").querySelector("a")!.getAttribute("href")).toBe("/terminal");
    expect(container.querySelector(".nav-link.active")!.textContent).toContain("Publishing");
  });

  it("opens YouTube and Website from their old addresses", async () => {
    serve();
    const a = shell("/youtube", publishingRoutes);
    await flush();
    expect((a.container.querySelector("#panel-youtube") as HTMLDetailsElement).open).toBe(true);
    expect((a.container.querySelector("#panel-website") as HTMLDetailsElement).open).toBe(false);
    a.unmount();
    const b = shell("/website", publishingRoutes);
    await flush();
    expect((b.container.querySelector("#panel-website") as HTMLDetailsElement).open).toBe(true);
  });

  it("gives X five real tabs and folds setup below them; old ?tab= links open the fold", async () => {
    serve();
    const { container } = shell("/x?tab=connection", publishingRoutes);
    await flush();
    const tabs = [...container.querySelectorAll('[role="tab"]')].map((t) => t.textContent);
    expect(tabs).toEqual(["Overview", "Post ideas", "Mentions", "Our posts", "Analytics"]);
    expect(screen.queryByText(/^More/)).toBeNull();
    expect((container.querySelector("#x-connection") as HTMLDetailsElement).open).toBe(true);
    for (const id of ["x-growth", "x-accounts", "x-topics"]) expect((container.querySelector(`#${id}`) as HTMLDetailsElement).open).toBe(false);
    expect(container.querySelector(".hub-back")!.textContent).toContain("Publishing");
  });
});
