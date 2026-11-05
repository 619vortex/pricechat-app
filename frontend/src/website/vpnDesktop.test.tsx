import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VpnDesktopPage, callbackUrl, desktopSignInToResume, forgetDesktopSignIn, readRequest, rememberDesktopSignIn } from "./vpnDesktop";
import { ProfilePage } from "./passport";

const CHALLENGE = "a".repeat(43), STATE = "s".repeat(24);
const GOOD = `?challenge=${CHALLENGE}&port=49152&state=${STATE}`;
let posted: string[] = [];
function serve(signedIn: boolean) {
  posted = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    if (init?.method === "POST") posted.push(`${input} ${init.body}`);
    if (input.endsWith("/members/me")) return new Response(JSON.stringify(signedIn ? { member: { username: "kiwi123", display_name: "Kiwi Bird" } } : { detail: "Sign in" }), { status: signedIn ? 200 : 401 });
    if (input.endsWith("/members/desktop/code")) return new Response(JSON.stringify({ code: "abc+1" }), { status: 200 });
    return new Response(JSON.stringify({ sections: {}, latest: [], trending: [], whats_it_worth: [], originals: [], price_board: [], categories: [], links: { telegram: null, x: null, youtube: null }, site_url: null }), { status: 200 });
  }));
}
async function settle() {
  for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); forgetDesktopSignIn(); });
const PAGE = `/vpn/desktop?challenge=${CHALLENGE}&port=49152&state=${STATE}`;
const REQUEST = { challenge: CHALLENGE, port: 49152, state: STATE };

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
    expect(document.querySelector(".vpn")!.textContent).toMatch(/Kiwi Bird/);  // names the account being allowed, in /members/me's real shape
    expect(posted).toEqual([]);
    expect(go).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Allow" }));
    await settle();
    expect(posted[0]).toContain(CHALLENGE);
    expect(go).toHaveBeenCalledWith(`http://127.0.0.1:49152/callback?code=abc%2B1&state=${STATE}`);
    expect(document.querySelector(".vpn")!.textContent).toMatch(/go back to Kiwi VPN/);
  });

});

/** The bug: Google sent the tab to /passport?welcome=google, the app's request was gone, and the app waited forever. */
describe("coming back from signing in", () => {
  function passportAt(search: string) {
    const replace = vi.fn();
    vi.stubGlobal("location", { ...window.location, replace, search, hostname: "example.test", pathname: "/passport" });
    return replace;
  }

  it("a signed-out visit remembers the app's request, and /passport goes back to it, after any sign-in", async () => {
    for (const search of ["?welcome=google", "?welcome=x", "?welcome=telegram", "?welcome=new", "?welcome=back"]) {
      serve(false);
      const shown = render(<VpnDesktopPage search={GOOD} go={vi.fn()} />);
      await settle();
      shown.unmount();
      const replace = passportAt(search);
      serve(true);
      render(<ProfilePage />);
      expect(replace).toHaveBeenCalledWith(PAGE);
      cleanup();
      vi.unstubAllGlobals();
    }
  });

  it("the page it comes back to asks for Allow, and only then hands over the code", async () => {
    rememberDesktopSignIn(REQUEST);
    const page = desktopSignInToResume()!;
    serve(true);
    const go = vi.fn();
    render(<VpnDesktopPage search={page.slice(page.indexOf("?"))} go={go} />);
    await settle();
    expect(go).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Allow" }));
    await settle();
    expect(go).toHaveBeenCalledWith(`http://127.0.0.1:49152/callback?code=abc%2B1&state=${STATE}`);
  });

  it("works once, so a later visit to the Passport isn't sent back", () => {
    rememberDesktopSignIn(REQUEST);
    expect(desktopSignInToResume()).toBe(PAGE);
    expect(desktopSignInToResume()).toBeNull();
  });

  it("gives up with the app: after 5 minutes it's forgotten", () => {
    rememberDesktopSignIn(REQUEST, 1000);
    expect(desktopSignInToResume(1000 + 5 * 60 * 1000 + 1)).toBeNull();
    rememberDesktopSignIn(REQUEST, 1000);
    expect(desktopSignInToResume(1000 + 5 * 60 * 1000)).toBe(PAGE);
  });

  it("a person who's already signed in remembers nothing", async () => {
    serve(true);
    render(<VpnDesktopPage search={GOOD} go={vi.fn()} />);
    await settle();
    expect(desktopSignInToResume()).toBeNull();
  });

  it("a bad link is never remembered", async () => {
    serve(false);
    render(<VpnDesktopPage search="?port=1" go={vi.fn()} />);
    await settle();
    expect(desktopSignInToResume()).toBeNull();
  });

  it("anything tampered with in storage can't redirect anywhere", () => {
    for (const bad of ["not json", "{}", '{"search":"challenge=x&port=1&state=y","at":1}', `{"search":"https://evil.example/","at":${Date.now()}}`,
      `{"search":"challenge=${CHALLENGE}&port=49152@evil.example&state=${STATE}","at":${Date.now()}}`, `{"search":"challenge=${CHALLENGE}&port=49152&state=${STATE}","at":"soon"}`]) {
      sessionStorage.setItem("kiwi-vpn-desktop-signin", bad);
      expect(desktopSignInToResume(), bad).toBeNull();
    }
  });

  it("with storage switched off, it doesn't throw and the Passport still opens", () => {
    const off = () => { throw new Error("blocked"); };
    vi.stubGlobal("sessionStorage", { getItem: off, setItem: off, removeItem: off });
    expect(() => rememberDesktopSignIn(REQUEST)).not.toThrow();
    expect(desktopSignInToResume()).toBeNull();
  });
});
