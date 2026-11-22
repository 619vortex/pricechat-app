import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Overview } from "../pages/Overview";
import { Layout } from "./Layout";
import { AREAS, locate } from "./nav";
import { PageHeader, Tabs } from "./ui";

/** TEST FIXTURES only: a fake admin home API. */
const home = (counts: Record<string, number> = {}) => ({
  todo: [
    { key: "signals", label: "Trading signals waiting for your OK", count: counts.signals ?? 0, to: "/trading-os" },
    { key: "vip", label: "Royal WhatsApp VIP requests", count: counts.vip ?? 0, to: "/rank-rewards?tab=royal" },
    { key: "approvals", label: "Posts and replies waiting for approval", count: counts.approvals ?? 0, to: "/approvals" },
  ],
  status: { kill_switch: false, telegram: "connected", x: "not_configured", ai: true, last_scan_at: new Date().toISOString(), failing_jobs: [] },
  numbers: { members: 12, new_members_7d: 3, signals_7d: 2, open_signals: 1 },
});
function serve(body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const url = String(input).split("?")[0];
    if (url === "/api/admin/home") return new Response(JSON.stringify(body), { status: 200 });
    return new Response(JSON.stringify({ detail: "nope" }), { status: 404 });
  }));
}
const flush = async () => { for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function shell(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Overview />} />
          <Route path="trading-os" element={<PageHeader title="Trading signals" subtitle="long text" />} />
          <Route path="telegram/topics/:id" element={<PageHeader title="Topic: BTC" />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("Admin layout", () => {
  it("keeps the sidebar short: six sections, every old page still reachable", () => {
    // owner-approved redesign, 2026-11-22: six sections, Price Chat Terminal its own section, left as it was
    expect(AREAS.map((a) => a.label)).toEqual(["Overview", "Content", "Publishing", "Price Chat Terminal", "Community", "System"]);
    for (const p of ["/trading-os", "/trading-bot", "/affiliate", "/members", "/rank-rewards", "/workers", "/realm", "/telegram", "/invite-bot", "/invite-bot/send",
      "/publishing", "/community", "/system", "/x", "/youtube", "/brand-manager", "/short-market", "/approvals", "/content", "/brain", "/knowledge", "/pokemon", "/website", "/analytics",
      "/settings", "/api-keys", "/activity"]) {
      expect(locate(p).page.to).toBe(p);
    }
    expect(locate("/telegram/topics/4").page.to).toBe("/telegram");
    expect(locate("/pokemon/cards/9").area.key).toBe("community");
    expect(locate("/vpn").area.key).toBe("system");
    expect(locate("/world-monitor").area.key).toBe("terminal");
    expect(locate("/terminal").area.key).toBe("terminal");
    expect(locate("/world-monitor").page.to).toBe("/terminal");  // Terminal 2.0: one page, the rest in its Settings drawer
    expect(locate("/terminal/newsdesk").page.to).toBe("/terminal");
    expect(locate("/invite-bot/send").page.label).toBe("Send invites");
  });

  it("opens a full tool with a back link to its section instead of tabs, and keeps the waiting count on the sidebar", async () => {
    // stage 3 (2026-11-22): Publishing is one page; Signals is a full tool one click from it, with "← Publishing" instead of four tabs
    serve(home({ signals: 2 }));
    const { container } = shell("/trading-os");
    await flush();
    expect(container.querySelector(".subnav")).toBeNull();
    expect(container.querySelector(".area-head")).toBeNull();
    const back = container.querySelector(".hub-back")!;
    expect(back.textContent).toBe("← Publishing");
    expect(back.getAttribute("href")).toBe("/publishing");
    expect(screen.getByText("long text")).toBeTruthy();  // with no section header above it, the tool shows its own title again
    expect(container.querySelector(".nav-link.active")!.textContent).toContain("Publishing");
    expect(container.querySelector(".nav-link.active .nav-count")!.textContent).toBe("2");
  });

  it("keeps a sub-page's own title (a topic's name)", async () => {
    serve(home());
    shell("/telegram/topics/4");
    await flush();
    expect(screen.getByRole("heading", { name: "Topic: BTC" })).toBeTruthy();
  });
});

describe("Admin home", () => {
  it("lists only what's waiting, each linking to the page that handles it", async () => {
    serve(home({ vip: 1, approvals: 3 }));
    const { container } = shell("/");
    await flush();
    const rows = [...container.querySelectorAll(".ov-row")];
    expect(rows.map((r) => r.textContent)).toEqual(["Reward1 Royal WhatsApp VIP requestsReview", "Post3 Posts and replies waiting for approvalReview"]);
    expect(rows[0].querySelector("a")!.getAttribute("href")).toBe("/rank-rewards?tab=royal");
    expect(screen.getByText("Needs you (4)")).toBeTruthy();
  });

  it("says all clear when nothing is waiting", async () => {
    serve(home());
    shell("/");
    await flush();
    expect(screen.getByText("✓ All clear. Nothing is waiting for you.")).toBeTruthy();
  });
});

describe("Tabs", () => {
  it("puts rarely used tabs under More, and shows the open one in the row", () => {
    const tabs = [{ key: "a", label: "Alpha" }, { key: "b", label: "Beta", more: true }, { key: "c", label: "Gamma", more: true }] as { key: "a" | "b" | "c"; label: string; more?: boolean }[];
    const onChange = vi.fn();
    const { container, rerender } = render(<Tabs tabs={tabs} value="a" onChange={onChange} />);
    expect([...container.querySelectorAll(".tabs > .tab")].map((t) => t.textContent)).toEqual(["Alpha"]);
    fireEvent.click(screen.getByText("Gamma"));
    expect(onChange).toHaveBeenCalledWith("c");
    rerender(<Tabs tabs={tabs} value="c" onChange={onChange} />);
    expect([...container.querySelectorAll(".tabs > .tab")].map((t) => t.textContent)).toEqual(["Alpha", "Gamma"]);
  });
});
