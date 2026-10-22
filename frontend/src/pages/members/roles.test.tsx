import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MembersPage } from "../Members";

/** TEST FIXTURES only: a fake members admin API. */
const row = (roles: string[]) => ({ id: "p1", username: "kiwi_bob", display_name: null, xp: 120, bonus_xp: 0, rank_level: 2, rank: "Kiwi Chick", linked: [],
  rank_score: 120, legacy_points: 0, last_active_at: null, joined_at: new Date().toISOString(), status: "active", xp_suspended: false, open_flags: 0,
  leaderboard_opt_out: false, roles });
const calls: string[] = [];
function serve() {
  let roles: string[] = [];
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input).split("?")[0];
    calls.push(`${init?.method ?? "GET"} ${url} ${init?.body ?? ""}`);
    if (url === "/api/members-admin/members") return new Response(JSON.stringify({ total: 1, members: [row(roles)] }));
    if (url === "/api/members-admin/members/p1/roles") {
      const b = JSON.parse(String(init?.body));
      roles = b.grant ? [...roles, b.role].sort() : roles.filter((r) => r !== b.role);
      return new Response(JSON.stringify(row(roles)));
    }
    return new Response(JSON.stringify({ detail: "nope" }), { status: 404 });
  }));
}
const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

describe("Members list: team roles", () => {
  it("makes a member a team member or a manager in one click, and asks before removing", async () => {
    serve();
    window.history.replaceState(null, "", "/?tab=members");
    render(<MemoryRouter><MembersPage /></MemoryRouter>);
    await act(async () => { await new Promise((r) => setTimeout(r, 300)); });
    await flush();
    fireEvent.click(screen.getByText("+ Manager"));
    await flush();
    expect(calls.some((c) => c.includes("/roles") && c.includes('"MANAGER"') && c.includes('"grant":true'))).toBe(true);
    expect(screen.getByText("✓ Manager")).toBeTruthy();
    fireEvent.click(screen.getByText("+ Team member"));
    await flush();
    expect(screen.getByText("✓ Team member")).toBeTruthy();
    vi.stubGlobal("confirm", vi.fn(() => false));
    fireEvent.click(screen.getByText("✓ Manager"));
    await flush();
    expect(screen.getByText("✓ Manager")).toBeTruthy();  // cancelled: nothing changes
    vi.stubGlobal("confirm", vi.fn(() => true));
    fireEvent.click(screen.getByText("✓ Manager"));
    await flush();
    expect(screen.getByText("+ Manager")).toBeTruthy();
  });
});
