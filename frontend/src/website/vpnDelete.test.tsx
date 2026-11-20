import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PAGE_HEADS } from "./seo";
import { VpnDeleteAccountPage, WHAT_IS_DELETED, WHAT_IS_KEPT } from "./vpnDelete";

const HOME = { sections: {}, latest: [], trending: [], whats_it_worth: [], originals: [], price_board: [], categories: [], links: { telegram: null, x: null, youtube: null }, site_url: null };
let sent: string[] = [];
function serve(signedIn: boolean, deleteStatus = 200) {
  sent = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    if (init?.method && init.method !== "GET") sent.push(`${init.method} ${input} ${init.body ?? ""}`);
    if (input.endsWith("/members/me") && init?.method === "DELETE")
      return new Response(JSON.stringify(deleteStatus === 200 ? { deleted: true } : { detail: "Type your username exactly to confirm." }), { status: deleteStatus });
    if (input.endsWith("/members/me")) return new Response(JSON.stringify(signedIn ? { member: { username: "kiwi123" } } : { detail: "Sign in" }), { status: signedIn ? 200 : 401 });
    return new Response(JSON.stringify(HOME), { status: 200 });
  }));
}
async function settle() {
  for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("the public account-deletion page", () => {
  it("names the app, both in-app paths, and what's deleted and kept, to anyone signed out", async () => {
    serve(false);
    render(<VpnDeleteAccountPage />);
    await settle();
    const text = document.body.textContent!;
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Delete your Kiwi VPN account");
    expect(text).toMatch(/Android:.*Settings › Account › Delete account/);
    expect(text).toMatch(/iPhone:.*Settings › Delete account/);
    for (const line of [...WHAT_IS_DELETED, ...WHAT_IS_KEPT]) expect(text).toContain(line);
    expect(text).toMatch(/Can't sign in any more/);
    expect(screen.getByRole("button", { name: "Sign in" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Delete my account" })).toBeNull();
    expect(PAGE_HEADS["vpn/delete-account"].title).toMatch(/Delete your Kiwi VPN account/);
  });

  it("deletes only after the username is typed exactly, with the same call the apps make", async () => {
    serve(true);
    render(<VpnDeleteAccountPage />);
    await settle();
    const button = screen.getByRole("button", { name: "Delete my account" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "kiwi12" } });
    expect(button.disabled).toBe(true);
    expect(sent).toEqual([]);
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: " kiwi123 " } });
    fireEvent.click(button);
    await settle();
    expect(sent).toEqual(['DELETE /api/members/me {"confirm_username":"kiwi123"}']);
    expect(screen.getByRole("status").textContent).toMatch(/has been deleted/);
  });

  it("says why when the server refuses", async () => {
    serve(true, 400);
    render(<VpnDeleteAccountPage />);
    await settle();
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: "kiwi123" } });
    fireEvent.click(screen.getByRole("button", { name: "Delete my account" }));
    await settle();
    expect(screen.getByRole("alert").textContent).toMatch(/Type your username/);
  });
});
