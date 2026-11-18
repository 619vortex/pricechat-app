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
  it("keeps the sidebar short: a few areas, every old page still reachable", () => {
    expect(AREAS.length).toBeLessThanOrEqual(11);  // World Monitor got its own area (owner, 2026-11-12)
    for (const p of ["/trading-os", "/trading-bot", "/affiliate", "/members", "/rank-rewards", "/workers", "/realm", "/telegram", "/invite-bot", "/invite-bot/send",
      "/x", "/youtube", "/brand-manager", "/short-market", "/approvals", "/content", "/brain", "/knowledge", "/pokemon", "/website", "/analytics",
      "/settings", "/api-keys", "/activity"]) {
      expect(locate(p).page.to).toBe(p);
    }
    expect(locate("/telegram/topics/4").page.to).toBe("/telegram");
    expect(locate("/pokemon/cards/9").area.key).toBe("pokemon");
    expect(locate("/vpn").area.key).toBe("vpn");
    expect(locate("/world-monitor").area.key).toBe("terminal");
    expect(locate("/terminal").area.key).toBe("terminal");
    expect(locate("/world-monitor").page.to).toBe("/terminal");  // Terminal 2.0: one page, the rest in its Settings drawer
    expect(locate("/terminal/newsdesk").page.to).toBe("/terminal");
    expect(locate("/invite-bot/send").page.label).toBe("Send invites");
  });

  it("shows the area's pages as tabs with one line about the page, and the waiting count on the sidebar", async () => {
    serve(home({ signals: 2 }));
    const { container } = shell("/trading-os");
    await flush();
    expect([...container.querySelectorAll(".subnav-link")].map((a) => a.textContent)).toEqual(["Signals", "Bot access", "Affiliate", "Kalshi bot"]);
    expect(container.querySelector(".subnav-link.active")!.textContent).toBe("Signals");
    expect(container.querySelector(".area-about")!.textContent).toContain("signals waiting for your OK");
    expect(screen.queryByText("long text")).toBeNull();  // the page's own title / subtitle aren't repeated under the area header
    expect(container.querySelector(".nav-link.active")!.textContent).toContain("Trading");
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
    const rows = [...container.querySelectorAll(".home-todo a")];
    expect(rows.map((r) => r.textContent)).toEqual(["1Royal WhatsApp VIP requestsOpen →", "3Posts and replies waiting for approvalOpen →"]);
    expect(rows[0].getAttribute("href")).toBe("/rank-rewards?tab=royal");
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
