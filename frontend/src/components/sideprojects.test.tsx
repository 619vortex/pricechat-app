import { act, cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GummyBakedPage, type SideProject } from "../pages/sideprojects/GummyBakedPage";
import { AdminGate } from "./AdminGate";
import { Layout } from "./Layout";
import { AREAS, SIDE_PROJECTS, searchJump } from "./nav";
import { PageHeader } from "./ui";

/** Owner, 2026-11-23: GUMMY & BAKED stays a separate side project; the admin sidebar only gives access to it, at the very bottom, under
 *  "Side Projects", for admins only; no localhost address; "Not available yet" until it is deployed. TEST FIXTURES only: made-up
 *  admin session and side-project answers. */
const GUMMY = (over: Partial<SideProject> = {}): SideProject => ({ key: "gummy-baked", label: "GUMMY & BAKED Audit", about: "The audit.", available: false,
  site_url: null, admin_url: null, ...over });

let calls: string[] = [];
function serve(session: { required: boolean; authenticated: boolean }, project: SideProject = GUMMY()) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const url = String(input).split("?")[0];
    calls.push(url);
    if (url === "/api/admin/session") return new Response(JSON.stringify({ ...session, username: "owner" }), { status: 200 });
    if (url === "/api/admin/home") return new Response(JSON.stringify({ todo: [], status: { kill_switch: false } }), { status: 200 });
    if (url === "/api/admin/side-projects") return new Response(JSON.stringify({ projects: [project] }), { status: 200 });
    return new Response("{}", { status: 404 });
  }));
}
const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function shell(path: string, gate = true) {
  const routes = (
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<PageHeader title="Home" />} />
          <Route path="side-projects/gummy-baked" element={<GummyBakedPage />} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
  return render(gate ? <AdminGate>{routes}</AdminGate> : routes);
}

describe("Side Projects", () => {
  it("lists GUMMY & BAKED Audit under Side Projects at the very bottom of the sidebar, apart from the six sections", async () => {
    serve({ required: true, authenticated: true });
    const { container } = shell("/");
    await flush();
    const side = screen.getByRole("navigation", { name: "Side projects" });
    expect(within(side).getByText("Side Projects")).toBeTruthy();
    expect(within(side).getByRole("link", { name: "GUMMY & BAKED Audit" }).getAttribute("href")).toBe("/side-projects/gummy-baked");
    const sidebar = container.querySelector<HTMLElement>(".sidebar")!;
    const sections = within(sidebar).getByRole("navigation", { name: "Admin sections" });
    expect(sections.compareDocumentPosition(side) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(side.closest(".sidebar-foot")).not.toBeNull();
    expect(AREAS.map((a) => a.label)).toEqual(["Overview", "Content", "Publishing", "Price Chat Terminal", "Community", "System"]);
    expect(within(sections).queryByText("GUMMY & BAKED Audit")).toBeNull();
    expect(SIDE_PROJECTS.map((p) => p.label)).toEqual(["GUMMY & BAKED Audit"]);
  });

  it("is hidden from anyone not signed in as an admin", async () => {
    serve({ required: true, authenticated: false });
    shell("/");
    await flush();
    expect(screen.queryByText("GUMMY & BAKED Audit")).toBeNull();  // the login screen, nothing else
    cleanup();
    shell("/", false);  // the shell without an admin session at all
    await flush();
    expect(screen.queryByRole("navigation", { name: "Side projects" })).toBeNull();
  });

  it("says Not available yet until the audit is deployed, with no link and no local address", async () => {
    serve({ required: true, authenticated: true });
    shell("/side-projects/gummy-baked");
    await flush();
    expect(screen.getByRole("heading", { name: "GUMMY & BAKED Audit" })).toBeTruthy();
    expect(screen.getByText("Not available yet.")).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Open the audit/ })).toBeNull();
    expect(document.body.innerHTML).not.toMatch(/127\.0\.0\.1|localhost/);
    // no section is highlighted: the page belongs to none of them
    const sections = screen.getByRole("navigation", { name: "Admin sections" });
    expect(within(sections).queryAllByRole("link").filter((a) => a.getAttribute("aria-current") === "page")).toHaveLength(0);
    expect(screen.getByRole("link", { name: "GUMMY & BAKED Audit" }).className).toContain("active");
  });

  it("opens the deployed audit in a new tab once the server gives its address", async () => {
    serve({ required: true, authenticated: true }, GUMMY({ available: true, site_url: "https://audit.example.org/", admin_url: "https://mod.example.org/moderation.html" }));
    shell("/side-projects/gummy-baked");
    await flush();
    const site = screen.getByRole("link", { name: /Open the audit site/ });
    expect(site.getAttribute("href")).toBe("https://audit.example.org/");
    expect(site.getAttribute("target")).toBe("_blank");
    expect(site.getAttribute("rel")).toContain("noopener");
    expect(screen.getByRole("link", { name: /Open audit moderation/ }).getAttribute("href")).toBe("https://mod.example.org/moderation.html");
    expect(screen.queryByText("Not available yet.")).toBeNull();
    // only Price Chat's own admin APIs are called: the audit's data never passes through Price Chat
    expect(calls.every((u) => u.startsWith("/api/admin/"))).toBe(true);
  });

  it("can be found with Jump to", () => {
    expect(searchJump("gummy")[0]).toMatchObject({ to: "/side-projects/gummy-baked", section: "Side Projects" });
  });
});
