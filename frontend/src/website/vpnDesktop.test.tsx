import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VpnDesktopPage, callbackUrl, readRequest } from "./vpnDesktop";

const CHALLENGE = "a".repeat(43), STATE = "s".repeat(24);
const GOOD = `?challenge=${CHALLENGE}&port=49152&state=${STATE}`;
let posted: string[] = [];
function serve(signedIn: boolean) {
  posted = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    if (init?.method === "POST") posted.push(`${input} ${init.body}`);
    if (input.endsWith("/members/me")) return new Response(JSON.stringify(signedIn ? { username: "kiwi" } : { detail: "Sign in" }), { status: signedIn ? 200 : 401 });
    if (input.endsWith("/members/desktop/code")) return new Response(JSON.stringify({ code: "abc+1" }), { status: 200 });
    return new Response(JSON.stringify({ sections: {}, latest: [], trending: [], whats_it_worth: [], originals: [], price_board: [], categories: [], links: { telegram: null, x: null, youtube: null }, site_url: null }), { status: 200 });
  }));
}
async function settle() {
  for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("signing in to the desktop app", () => {
  it("accepts only what the app sends", () => {
    expect(readRequest(GOOD)).toEqual({ challenge: CHALLENGE, port: 49152, state: STATE });
    for (const bad of ["", `?challenge=x&port=49152&state=${STATE}`, `?challenge=${CHALLENGE}&port=80&state=${STATE}`, `?challenge=${CHALLENGE}&port=99999&state=${STATE}`,
      `?challenge=${CHALLENGE}&port=49152@evil.example&state=${STATE}`, `?challenge=${CHALLENGE}&port=49152&state=a/b`]) expect(readRequest(bad)).toBeNull();
  });

  it("only ever sends the code to this PC", () => {
    expect(callbackUrl({ challenge: CHALLENGE, port: 49152, state: STATE }, "a b")).toBe(`http://127.0.0.1:49152/callback?code=a%20b&state=${STATE}`);
  });

  it("refuses a bad link, with no button to press", async () => {
    serve(true);
    render(<VpnDesktopPage search="?port=1" go={vi.fn()} />);
    await settle();
    expect(screen.getAllByRole("alert")[0].textContent).toMatch(/isn't valid/);
    expect(screen.queryByRole("button", { name: "Allow" })).toBeNull();
  });

  it("asks a signed-out person to sign in first", async () => {
    serve(false);
    render(<VpnDesktopPage search={GOOD} go={vi.fn()} />);
    await settle();
    expect(document.querySelector(".vpn button")!.textContent).toBe("Sign in");
    expect(screen.queryByRole("button", { name: "Allow" })).toBeNull();
  });

  it("gives no code until the person presses Allow, then hands it to the app", async () => {
    serve(true);
    const go = vi.fn();
    render(<VpnDesktopPage search={GOOD} go={go} />);
    await settle();
    expect(document.querySelector(".vpn")!.textContent).toMatch(/kiwi/);
    expect(posted).toEqual([]);
    expect(go).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Allow" }));
    await settle();
    expect(posted[0]).toContain(CHALLENGE);
    expect(go).toHaveBeenCalledWith(`http://127.0.0.1:49152/callback?code=abc%2B1&state=${STATE}`);
    expect(document.querySelector(".vpn")!.textContent).toMatch(/go back to Kiwi VPN/);
  });
});
