import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Overview } from "../pages/Overview";
import { Layout } from "./Layout";
import { AREAS, isTerminalPath, locate, searchJump } from "./nav";
import { PageHeader } from "./ui";

/** TEST FIXTURES only: a fake admin home API. */
const home = (counts: Record<string, number> = {}, status: Record<string, unknown> = {}) => ({
  todo: [
    { key: "signals", label: "Trading signals waiting for your OK", count: counts.signals ?? 0, to: "/trading-os" },
    { key: "vip", label: "Royal WhatsApp VIP requests", count: counts.vip ?? 0, to: "/rank-rewards?tab=royal" },
    { key: "approvals", label: "Posts and replies waiting for approval", count: counts.approvals ?? 0, to: "/approvals" },
  ],
  status: { kill_switch: false, telegram: "connected", x: "not_configured", ai: true, last_scan_at: new Date().toISOString(), failing_jobs: [], ...status },
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
          <Route path="api-keys" element={<PageHeader title="API keys page" />} />
          <Route path="terminal" element={<div data-testid="terminal-stub">Terminal</div>} />
          <Route path="terminal/newsdesk" element={<div data-testid="terminal-stub">News Desk</div>} />
          <Route path="world-monitor" element={<div data-testid="terminal-stub">World Monitor</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

/** Which section each route of AdminApp.tsx belongs to (the owner-approved table, 2026-11-22). */
function expectedSection(path: string): string {
  const p = "/" + path.replace(/\/\*$/, "").replace(/^\//, "");
  const starts = (x: string) => p === x || p.startsWith(x + "/");
  if (p === "/" || ["/approvals", "/analytics", "/affiliate"].some(starts)) return "overview";
  if (["/content", "/brand-manager", "/short-market", "/brain", "/knowledge"].some(starts)) return "content";
  if (["/publishing", "/x", "/youtube", "/website", "/trading-os"].some(starts)) return "publishing";
  if (["/terminal", "/world-monitor"].some(starts)) return "terminal";
  if (["/community", "/members", "/rank-rewards", "/workers", "/trading-bot", "/telegram", "/invite-bot", "/telegram-trading", "/pokemon"].some(starts)) return "community";
  if (["/system", "/settings", "/api-keys", "/activity", "/vpn", "/kalshi", "/realm", "/intelligence"].some(starts)) return "system";
  throw new Error(`no section expected for ${path}`);
}

describe("Admin navigation map", () => {
  it("puts every route in AdminApp.tsx in its approved section", () => {
    const src = readFileSync(resolve(__dirname, "../AdminApp.tsx"), "utf-8");
    const paths = [...src.matchAll(/<Route path="([^"]+)"/g)].map((m) => m[1]).filter((p) => p !== "*" && p !== "arena");  // arena redirects to /realm
    expect(paths.length).toBeGreaterThan(50);
    for (const raw of ["", ...paths]) {
      const concrete = "/" + raw.replace(/\/\*$/, "").replace(/:[a-z]+/g, "x");
      expect([raw, locate(concrete).area.key]).toEqual([raw, expectedSection(raw)]);
    }
    expect(locate("/arena").area.key).toBeDefined();  // still resolves (it redirects to /realm)
  });

  it("treats only Price Chat Terminal's routes as Terminal", () => {
    for (const p of ["/terminal", "/terminal/newsdesk", "/terminal/trending", "/terminal/x", "/world-monitor"]) expect(isTerminalPath(p)).toBe(true);
    for (const p of ["/", "/x", "/trading-os", "/settings", "/pokemon/cards/1"]) expect(isTerminalPath(p)).toBe(false);
    expect(AREAS.find((a) => a.key === "terminal")!.pages).toEqual([
      { to: "/terminal", label: "Terminal", about: "Breaking news, emerging stories and everything Price Chat publishes, on one page.", match: ["/terminal/", "/world-monitor"] },
    ]);
  });

  it("finds pages by name, section or description", () => {
    expect(searchJump("api")[0].to).toBe("/api-keys");
    expect(searchJump("vpn").map((r) => r.to)).toContain("/vpn");
    expect(searchJump("terminal")[0].to).toBe("/terminal");
    expect(searchJump("zzzz-nothing")).toEqual([]);
  });
});

describe("Admin shell", () => {
  it("Ctrl+K focuses the jump box; typing filters; Enter opens the page", async () => {
    serve(home());
    shell("/");
    await flush();
    const box = screen.getByRole("combobox", { name: "Jump to a page" }) as HTMLInputElement;
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(document.activeElement).toBe(box);
    fireEvent.change(box, { target: { value: "api k" } });
    expect(screen.getAllByRole("option").map((o) => o.textContent)).toContain("API keysSystem");
    const { container } = { container: document.body };
    fireEvent.keyDown(box, { key: "Enter" });
    await flush();
    expect(screen.getByText("API keys page")).toBeTruthy();  // the API keys address opened (stage 5: a panel on System, no tabs)
    expect(container.querySelector(".subnav")).toBeNull();
    expect(container.querySelector(".nav-link.active")!.textContent).toContain("System");
    expect(box.value).toBe("");
  });

  it("Escape clears the jump box", async () => {
    serve(home());
    shell("/");
    await flush();
    const box = screen.getByRole("combobox", { name: "Jump to a page" }) as HTMLInputElement;
    fireEvent.change(box, { target: { value: "vpn" } });
    expect(screen.queryAllByRole("option").length).toBeGreaterThan(0);
    fireEvent.keyDown(box, { key: "Escape" });
    expect(box.value).toBe("");
    expect(screen.queryAllByRole("option").length).toBe(0);
  });

  it("Pending counts everything waiting and lists each queue", async () => {
    serve(home({ signals: 2, vip: 1, approvals: 3 }));
    const { container } = shell("/api-keys");
    await flush();
    expect(container.querySelector(".pending-count")!.textContent).toBe("6");
    fireEvent.click(container.querySelector(".pending-btn")!);
    const links = [...container.querySelectorAll(".pending-pop a")];
    expect(links.map((a) => a.getAttribute("href"))).toEqual(["/trading-os", "/rank-rewards?tab=royal", "/approvals"]);
  });

  it("has a phone bottom bar with the six sections off Terminal", async () => {
    serve(home());
    const { container } = shell("/");
    await flush();
    const shellEl = container.querySelector(".shell")!;
    expect(shellEl.classList.contains("has-bottombar")).toBe(true);
    const links = [...container.querySelectorAll(".bottombar .bottombar-link")];
    expect(links.map((a) => a.textContent)).toEqual(["Overview", "Content", "Publish", "Terminal", "Community", "System"]);
    expect(container.querySelector(".bottombar-link.active")!.getAttribute("aria-current")).toBe("page");
  });

  for (const path of ["/terminal", "/terminal/newsdesk", "/world-monitor"]) {
    it(`keeps Price Chat Terminal's frame exactly as it was on ${path}`, async () => {
      serve(home({ signals: 1 }));
      const { container } = shell(path);
      await flush();
      const shellEl = container.querySelector(".shell")!;
      expect(shellEl.className).toBe("shell");  // no bottom-bar padding class, nothing else added
      expect(container.querySelector(".bottombar")).toBeNull();
      expect([...shellEl.children].map((c) => c.className)).toEqual(["topbar", "sidebar", "scrim", "main"]);  // same four parts as before, no extra bar
      expect(container.querySelector(".main .area-head")).toBeNull();  // Terminal has one page: no section tabs above it
      expect(screen.getByTestId("terminal-stub")).toBeTruthy();
    });
  }
});

describe("Overview", () => {
  it("lists what needs you with a chip per kind, and filters by kind", async () => {
    serve(home({ signals: 2, vip: 1, approvals: 3 }));
    const { container } = shell("/");
    await flush();
    const chips = [...container.querySelectorAll(".ov-chip")].map((c) => c.textContent);
    expect(chips).toEqual(["All 6", "Signal 2", "Reward 1", "Post 3"]);
    expect(container.querySelectorAll(".ov-row").length).toBe(3);
    fireEvent.click(screen.getByText("Post 3"));
    const rows = [...container.querySelectorAll(".ov-row")];
    expect(rows.length).toBe(1);
    expect(rows[0].querySelector("a")!.getAttribute("href")).toBe("/approvals");
    expect(screen.getByText("Needs you (6)")).toBeTruthy();
  });

  it("raises alerts from the status it already had, each with the page that fixes it", async () => {
    serve(home({}, { kill_switch: true, failing_jobs: ["News Desk"], ai: false }));
    const { container } = shell("/");
    await flush();
    const titles = [...container.querySelectorAll(".ov-alert strong")].map((t) => t.textContent);
    expect(titles).toEqual(["Emergency stop is ON", "1 background job having problems", "The AI is offline"]);
    expect(container.querySelector(".kill-indicator")!.textContent).toBe("Emergency stop is ON");  // the sidebar indicator is unchanged
  });

  it("keeps the numbers and running status it showed before", async () => {
    serve(home());
    shell("/");
    await flush();
    expect(screen.getByText("Members")).toBeTruthy();
    expect(screen.getByText("+3 this week")).toBeTruthy();
    expect(screen.getByText("1 open now")).toBeTruthy();
    for (const n of ["Signal scanner", "Telegram", "X / Twitter", "AI"]) expect(screen.getAllByText(n).length).toBeGreaterThan(0);
    expect(screen.getByText(/Recent activity and details/)).toBeTruthy();
    expect(screen.getByText("visitors, sign-ups and affiliate volume")).toBeTruthy();  // stage 5: Growth is a panel on the page (Analytics + Affiliate)
  });
});
