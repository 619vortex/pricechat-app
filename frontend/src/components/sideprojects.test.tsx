import { act, cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GummyBakedPage } from "../pages/sideprojects/GummyBakedPage";
import { Layout } from "./Layout";
import { AREAS, SIDE_PROJECTS, gummyBaked, searchJump } from "./nav";
import { PageHeader } from "./ui";

/** Owner, 2026-11-23: GUMMY & BAKED stays a separate side project; the admin sidebar only gives access to it, at the very bottom, under
 *  "Side Projects". TEST FIXTURES only: a fake admin home API. */
const fetchMock = vi.fn(async (input: string) => {
  if (String(input).split("?")[0] === "/api/admin/home") return new Response(JSON.stringify({ todo: [], status: { kill_switch: false } }), { status: 200 });
  return new Response("{}", { status: 404 });
});
const flush = async () => { for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); fetchMock.mockClear(); });

function shell(path: string) {
  vi.stubGlobal("fetch", fetchMock);
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<PageHeader title="Home" />} />
          <Route path="side-projects/gummy-baked" element={<GummyBakedPage />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("Side Projects", () => {
  it("lists GUMMY & BAKED Audit under Side Projects at the bottom of the sidebar, apart from the six sections", async () => {
    const { container } = shell("/");
    await flush();
    const side = screen.getByRole("navigation", { name: "Side projects" });
    expect(within(side).getByText("Side Projects")).toBeTruthy();
    expect(within(side).getByRole("link", { name: "GUMMY & BAKED Audit" }).getAttribute("href")).toBe("/side-projects/gummy-baked");
    // after the six sections, in the sidebar's foot (the very bottom)
    const sidebar = container.querySelector<HTMLElement>(".sidebar")!;
    const sections = within(sidebar).getByRole("navigation", { name: "Admin sections" });
    expect(sections.compareDocumentPosition(side) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(side.closest(".sidebar-foot")).not.toBeNull();
    // the six sections and the phone bar are unchanged
    expect(AREAS.map((a) => a.label)).toEqual(["Overview", "Content", "Publishing", "Price Chat Terminal", "Community", "System"]);
    expect(within(sections).queryByText("GUMMY & BAKED Audit")).toBeNull();
    expect(SIDE_PROJECTS.map((p) => p.label)).toEqual(["GUMMY & BAKED Audit"]);
  });

  it("opens a page that only links to the audit: nothing of it is fetched from Price Chat", async () => {
    shell("/side-projects/gummy-baked");
    await flush();
    expect(screen.getByRole("heading", { name: "GUMMY & BAKED Audit" })).toBeTruthy();
    const site = screen.getByRole("link", { name: /Open the audit site/ });
    expect(site.getAttribute("href")).toBe(gummyBaked().siteUrl);
    expect(site.getAttribute("target")).toBe("_blank");
    expect(site.getAttribute("rel")).toContain("noopener");
    expect(screen.getByRole("link", { name: /Open audit moderation/ }).getAttribute("href")).toBe("http://127.0.0.1:8001/moderation.html");
    // the only call is the admin shell's own home poll: no GUMMY data passes through Price Chat
    expect(fetchMock.mock.calls.map((c) => String(c[0]).split("?")[0])).toEqual(expect.arrayContaining(["/api/admin/home"]));
    expect(fetchMock.mock.calls.every((c) => String(c[0]).startsWith("/api/admin/home"))).toBe(true);
    // no section is highlighted: the page belongs to none of them
    const sections = screen.getByRole("navigation", { name: "Admin sections" });
    expect(within(sections).queryAllByRole("link").filter((a) => a.getAttribute("aria-current") === "page")).toHaveLength(0);
    expect(screen.getByRole("link", { name: "GUMMY & BAKED Audit" }).className).toContain("active");
  });

  it("can be found with Jump to", () => {
    expect(searchJump("gummy")[0]).toMatchObject({ to: "/side-projects/gummy-baked", section: "Side Projects" });
  });

  it("uses the local dev server until the audit is online", () => {
    expect(gummyBaked()).toEqual({ siteUrl: "http://127.0.0.1:8000/", adminUrl: "http://127.0.0.1:8001/moderation.html", online: false });
  });
});
