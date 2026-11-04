import { readFileSync } from "node:fs";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Downloads, VpnDevicesPage, VpnPage, VpnReviewerPage, ago, bytes, pickServer, resumeDay, type VpnDevice, type VpnState } from "./vpn";
import { PLAN, STORE_LINKS, WINDOWS_BETA, visitorBrowser, visitorOs } from "./vpnConfig";

import { Flag, drawMap, place, placeLine } from "./vpnMap";

/** TEST FIXTURES only: a fake Kiwi VPN API. No real config, keys or servers. */
const HOME = { sections: {}, latest: [], trending: [], whats_it_worth: [], originals: [], price_board: [], categories: [],
               links: { telegram: null, x: null, youtube: null }, site_url: null };
const SERVERS: VpnState["servers"] = [
  { id: 1, name: "Auckland 1", region: "New Zealand", country_code: "nz", online: true, load: 0.2, adblock: false, recommended: true },
  { id: 2, name: "Frankfurt 1", region: "Germany", country_code: "DE", online: false, load: 0.9, adblock: false, recommended: false },
];
const DEVICE: VpnDevice = { id: 7, name: "My iPhone", server_id: 1, ip: "10.66.0.5", created_at: "2026-10-01T00:00:00Z",
                 last_handshake: new Date(Date.now() - 5 * 60_000).toISOString(), rx_bytes: 1024 ** 3, tx_bytes: 512 * 1024 ** 2,
                 paused_until: null };
const USAGE: VpnState["usage"] = { month: "2026-10", used_bytes: 0, cap_bytes: 0, paused_until: null };
const state = (over: Partial<VpnState> = {}): VpnState => ({
  enabled: true, eligible: true, reason: null, max_devices: 3, servers: SERVERS, devices: [], usage: USAGE, ...over,
});

let calls: string[] = [];
type Route = [number, unknown] | ((init?: RequestInit) => [number, unknown]);
function serve(routes: Record<string, Route>) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${input.split("?")[0]}`;
    calls.push(`${key} ${init?.body ?? ""}`.trim());
    const r = routes[key] ?? routes[input.split("?")[0]];
    const [status, body] = r ? (typeof r === "function" ? r(init) : r) : [404, { detail: "nope" }];
    return new Response(status === 204 ? null : JSON.stringify(body), { status });
  }));
}
async function settle() {
  for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const BILL = { price_usd: 9.99, period_days: 30, min_usd: 5, max_usd: 500, usdt: true, card: true,
               networks: [{ key: "ethereum", name: "USDT on Ethereum" }, { key: "tron", name: "USDT on Tron", min_usd: 10.96 }, { key: "solana", name: "USDT on Solana" }] };

describe("Free while payments are off", () => {
  afterEach(() => { try { sessionStorage.clear(); } catch { /* none */ } });
  const money = /\$|USDT|card|pay/i;
  /** Everything but the Royal Kiwi card, which always says its benefit needs no payment. */
  const outsideRoyal = (container: HTMLElement) => {
    const copy = container.querySelector(".vpn")!.cloneNode(true) as HTMLElement;
    copy.querySelector(".vpn-royal")?.remove();
    copy.querySelectorAll(".vpn-card h2").forEach((h) => h.append(" "));
    return copy.textContent!.replace(/Your Plan|Available Countries/g, "").replace(/\bvpn-platform\b/g, "");
  };

  it("shows a signed-out visitor one way in, with nothing about money", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [401, { detail: "Sign in" }],
            "GET /api/members/vpn/public": [200, { enabled: true, payments: false, billing: null }] });
    const { container } = render(<VpnPage />);
    await settle();
    expect(screen.getByText("Free while we test")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Sign in to start" }).getAttribute("href")).toBe("/join");
    expect(screen.queryByRole("heading", { name: "Anonymous, with USDT" })).toBeNull();
    expect(outsideRoyal(container)).not.toMatch(money);
  });

  it("gives a signed-in member devices and no payment section", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ devices: [DEVICE], billing: null })] });
    const { container } = render(<VpnPage />);
    await settle();
    const page = outsideRoyal(container);
    expect(page).toMatch(/1 connected/);
    expect(page).toMatch(/My Devices/);
    expect(page).not.toMatch(/Add a device/);
    expect(screen.queryByRole("heading", { name: "Your time" })).toBeNull();
    expect(page).not.toMatch(money);
  });

  it("offers the extension only when a server can take it", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ billing: null })] });
    let view = render(<VpnPage />);
    await settle();
    expect(screen.queryByRole("link", { name: "Download the ZIP" })).toBeNull();
    view.unmount();
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ billing: null, servers: [{ ...SERVERS[0], browser: true }] })] });
    view = render(<VpnPage />);
    await settle();
    expect(screen.getByRole("link", { name: "Download the ZIP" }).getAttribute("href")).toBe("/downloads/kiwi-vpn-extension.zip");
  });

  const EDGE = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36 Edg/156.0.0.0";
  const CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36";
  const PHONE = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Mobile Safari/537.36";

  it("signs a reviewer in with an email and password, and says what to do next", async () => {
    serve({ "/api/website/home": [200, HOME], "POST /api/members/auth/reviewer": [200, { signed_in: true }] });
    const { container } = render(<VpnReviewerPage />);
    fireEvent.change(container.querySelector('input[type="email"]')!, { target: { value: "r@example.com" } });
    fireEvent.change(container.querySelector('input[type="password"]')!, { target: { value: "secret" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await settle();
    expect(calls.find((c) => c.startsWith("POST /api/members/auth/reviewer"))).toContain('"email":"r@example.com"');
    expect(screen.getByRole("status").textContent).toMatch(/press the switch/);
  });

  it("shows a reviewer why the sign-in failed", async () => {
    serve({ "/api/website/home": [200, HOME], "POST /api/members/auth/reviewer": [401, { detail: "That email or password isn't right, or this account has ended." }] });
    const { container } = render(<VpnReviewerPage />);
    fireEvent.change(container.querySelector('input[type="email"]')!, { target: { value: "r@example.com" } });
    fireEvent.change(container.querySelector('input[type="password"]')!, { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await settle();
    expect(screen.getByRole("alert").textContent).toMatch(/isn't right/);
  });

  it("tells the browsers apart", () => {
    expect(visitorBrowser(EDGE)).toBe("edge");
    expect(visitorBrowser(CHROME)).toBe("chrome");
    expect(visitorBrowser(PHONE)).toBe("phone");
    expect(visitorBrowser("Mozilla/5.0 (Windows NT 10.0; rv:130.0) Gecko/20100101 Firefox/130.0")).toBe("other");
    expect(visitorBrowser(`${CHROME} OPR/110.0.0.0`)).toBe("other");
  });

  const card = (c: HTMLElement, name: string) => [...c.querySelectorAll(".vpn-app")].find((x) => x.querySelector("h3")!.textContent!.startsWith(name))!;

  it("shows the four platforms, each with an icon, a description and a status", () => {
    const { container } = render(<Downloads agent={CHROME} />);
    expect([...container.querySelectorAll(".vpn-app h3")].map((h) => h.childNodes[0].textContent)).toEqual(["Windows desktop", "Browser extension", "Android", "iOS / macOS"]);
    for (const c of container.querySelectorAll(".vpn-app")) {
      expect(c.querySelector(".vpn-app-icon")).toBeTruthy();
      expect(c.querySelector("p")!.textContent!.length).toBeGreaterThan(10);
      expect(c.querySelector(".vpn-status")).toBeTruthy();
    }
  });

  it("claims nothing that isn't released: no download anywhere, and each status says so", () => {
    const { container } = render(<Downloads agent={CHROME} testers />);
    expect([...container.querySelectorAll(".vpn-app .vpn-status")].map((x) => x.textContent)).toEqual(["In development", "Coming soon", "Coming soon", "Coming soon"]);
    expect(screen.queryByText("Available")).toBeNull();
    expect([...container.querySelectorAll(".vpn-app a")].every((a) => a.getAttribute("href") === "/vpn/devices#manual")).toBe(true);
    expect([...container.querySelectorAll("a")].filter((a) => /chromewebstore|microsoftedge|\.exe|\.msi|apps\.apple|play\.google/.test(a.href))).toHaveLength(0);
    expect(screen.getByText("Manual install, for testers (Chrome and Edge)")).toBeTruthy();
  });

  it("marks the extension Available only with a real store address, and drops the manual install", () => {
    Object.assign(STORE_LINKS, { chrome: "https://chromewebstore.google.com/detail/test", edge: "https://microsoftedge.microsoft.com/addons/detail/test" });
    try {
      const { container } = render(<Downloads agent={EDGE} testers />);
      expect(screen.getByRole("link", { name: "Get for Edge" }).getAttribute("href")).toBe(STORE_LINKS.edge);
      expect(screen.getByRole("link", { name: "Add to Chrome" }).getAttribute("href")).toBe(STORE_LINKS.chrome);
      expect(card(container, "Browser extension").querySelector(".vpn-status")!.textContent).toBe("Available");
      expect(card(container, "Android").querySelector(".vpn-status")!.textContent).toBe("Coming soon");
      expect(screen.queryByText(/Manual install/)).toBeNull();
    } finally {
      Object.assign(STORE_LINKS, { chrome: "", edge: "" });
    }
  });

  it("offers the Windows beta only once a real installer is set, labelled Beta, promising no sign-in", () => {
    let view = render(<Downloads agent={CHROME} />);
    expect(screen.queryByRole("link", { name: /Download for Windows/ })).toBeNull();
    view.unmount();
    Object.assign(WINDOWS_BETA, { href: "/downloads/KiwiVPN-Setup.exe", version: "0.1.0" });
    try {
      view = render(<Downloads agent={CHROME} />);
      const windows = card(view.container, "Windows");
      expect(screen.getByRole("link", { name: /Download for Windows/ }).getAttribute("href")).toBe("/downloads/KiwiVPN-Setup.exe");
      expect(windows.querySelector(".vpn-status")!.textContent).toBe("Beta");
      expect(windows.textContent).toMatch(/temporary/);
      expect(windows.textContent).not.toMatch(/sign in|one-click/i);
      expect(card(view.container, "Browser extension").querySelector(".vpn-status")!.textContent).toBe("Coming soon");
      expect(card(view.container, "Android").querySelector(".vpn-status")!.textContent).toBe("Coming soon");
    } finally {
      Object.assign(WINDOWS_BETA, { href: "", version: "" });
    }
  });

  it("keeps WireGuard out of the cards, one link away", () => {
    const { container } = render(<Downloads agent={CHROME} />);
    for (const c of container.querySelectorAll(".vpn-app")) expect(c.textContent).not.toMatch(/WireGuard/);
    expect(screen.getByRole("link", { name: "Manual setup for Windows" }).getAttribute("href")).toBe("/vpn/devices#manual");
    expect(screen.getByRole("link", { name: "Set it up manually" }).getAttribute("href")).toBe("/vpn/devices#manual");
    expect(container.querySelector("#vpn-manual")).toBeNull();
  });

  it("marks the visitor's own device", () => {
    expect(visitorOs(CHROME)).toBe("Windows");
    expect(visitorOs(PHONE)).toBe("Android");
    expect(visitorOs("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605")).toBe("macOS");
    expect(visitorOs("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605", 5)).toBe("iOS");
    expect(visitorOs("Mozilla/5.0 (X11; Linux x86_64) Firefox/130")).toBe("Linux");
    let view = render(<Downloads agent={CHROME} />);
    expect(card(view.container, "Windows").textContent).toMatch(/Your device/);
    expect(card(view.container, "Browser").textContent).toMatch(/Your browser/);
    view.unmount();
    view = render(<Downloads agent={PHONE} />);
    expect(card(view.container, "Android").textContent).toMatch(/Your device/);
    expect(screen.getByText(/Browser extensions need a computer/)).toBeTruthy();
  });

  it("stays coming soon while closed, however it's priced", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [401, { detail: "Sign in" }],
            "GET /api/members/vpn/public": [200, { enabled: false, payments: false, billing: null }] });
    const { container } = render(<VpnPage />);
    await settle();
    expect(container.querySelector(".vpn-hero")!.textContent).toMatch(/Opening soon/);
    expect(screen.queryByText("Free while we test")).toBeNull();
    expect(screen.queryByRole("link", { name: "Sign in to start" })).toBeNull();
  });
});

describe("The dashboard: plans, trial, Royal Kiwi and real figures", () => {
  afterEach(() => { try { sessionStorage.clear(); } catch { /* none */ } });
  const TRIAL_LABEL = "Start Free Trial — 30 Days";
  const STATS = { active_users: 2, countries: 1, online_servers: 1, uptime: null,
                  servers: [{ id: 1, name: "Helsinki 1", region: "Finland", country_code: "FI", online: true, recommended: true, browser: true }] };

  it("shows only the server's figures and leaves uptime out when nothing measures it", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [401, { detail: "Sign in" }], "GET /api/members/vpn/public": [200, { enabled: false, payments: false, trial: false, billing: null }],
            "GET /api/members/vpn/stats": [200, STATS] });
    const { container } = render(<VpnPage />);
    await settle();
    const net = container.querySelector(".vpn-network")!.textContent!;
    expect(net).toMatch(/1 country · 1 server online · 2 active in the last 10 minutes/);
    expect(container.textContent).not.toMatch(/Uptime|\d ?ms\b|Mbps|Gbps/);
    expect(container.querySelector(".vpn-servers")!.textContent).toMatch(/Helsinki.*Recommended.*Finland/);
    expect(container.textContent).not.toMatch(/Amsterdam|Frankfurt|London|New York/);
  });

  it("doesn't dress up an idle network: no active count at zero", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [401, { detail: "Sign in" }], "GET /api/members/vpn/public": [200, { enabled: false, payments: false, billing: null }],
            "GET /api/members/vpn/stats": [200, { ...STATS, active_users: 0 }] });
    const { container } = render(<VpnPage />);
    await settle();
    expect(container.querySelector(".vpn-network")!.textContent).not.toMatch(/active in the last/);
  });

  it("says plainly when nothing can be read, with no invented numbers", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [401, { detail: "Sign in" }], "GET /api/members/vpn/public": [200, { enabled: false, payments: false, billing: null }],
            "GET /api/members/vpn/stats": [500, { detail: "down" }] });
    const { container } = render(<VpnPage />);
    await settle();
    expect(container.textContent).toMatch(/Server status is unavailable/);
    expect(container.querySelector(".vpn-network")!.textContent).not.toMatch(/\d (country|countries|server)/);
  });

  it("shows no start button while the trial is off", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ eligible: false, reason: "Add time.", plan: { kind: "not_activated", until: null, days_left: null }, trial: { available: false, days: 30 } })] });
    render(<VpnPage />);
    await settle();
    expect(screen.queryByRole("button", { name: TRIAL_LABEL })).toBeNull();
    expect(screen.getByText("Not Activated")).toBeTruthy();
    expect(screen.getByText("Purchasing isn't open yet.")).toBeTruthy();
  });

  it("starts a trial through the server and then shows the days left, with no second offer", async () => {
    const before = state({ eligible: false, reason: "Add time.", plan: { kind: "not_activated", until: null, days_left: null }, trial: { available: true, days: 30 } });
    const after = state({ plan: { kind: "trial", until: "2026-12-04T00:00:00+00:00", days_left: 30 }, trial: { available: false, days: 30 } });
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, before], "POST /api/members/vpn/trial": [200, after] });
    render(<VpnPage />);
    await settle();
    fireEvent.click(screen.getAllByRole("button", { name: TRIAL_LABEL })[0]);
    await settle();
    expect(calls.some((c) => c === "POST /api/members/vpn/trial")).toBe(true);
    expect(screen.queryByRole("button", { name: TRIAL_LABEL })).toBeNull();
    const my = document.querySelector<HTMLElement>(".vpn-my")!;
    expect(my.querySelector(".vpn-big")!.textContent).toBe("Free Trial");
    expect(my.textContent).toMatch(/30 days left/);
  });

  it("shows the server's refusal if a trial can't start", async () => {
    const before = state({ eligible: false, reason: "Add time.", plan: { kind: "not_activated", until: null, days_left: null }, trial: { available: true, days: 30 } });
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, before], "POST /api/members/vpn/trial": [409, { detail: "You've already used your free trial." }] });
    render(<VpnPage />);
    await settle();
    fireEvent.click(screen.getAllByRole("button", { name: TRIAL_LABEL })[0]);
    await settle();
    expect(screen.getByRole("alert").textContent).toMatch(/already used/);
  });

  it("offers a Royal Kiwi no trial, and shows access active only when the server says so", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ royal: true, plan: { kind: "royal", until: null, days_left: null }, trial: { available: false, days: 30 } })] });
    let view = render(<VpnPage />);
    await settle();
    expect(screen.getAllByText("Royal Kiwi Access Active").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: TRIAL_LABEL })).toBeNull();
    expect(screen.getByRole("link", { name: "View My Kiwi Passport" }).getAttribute("href")).toBe("/passport");
    view.unmount();
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ royal: false, plan: { kind: "free", until: null, days_left: null } })] });
    view = render(<VpnPage />);
    await settle();
    expect(screen.queryByText("Royal Kiwi Access Active")).toBeNull();
    expect(document.querySelector(".vpn-hero")!.textContent).not.toMatch(/Forever/);
  });

  it("adds no navigation of its own", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state()] });
    const { container } = render(<VpnPage />);
    await settle();
    expect(container.querySelector(".vpn nav")).toBeNull();
  });
});

describe("Paying and anonymous keys", () => {
  afterEach(() => { try { sessionStorage.clear(); } catch { /* none */ } });
  const SIGNED_OUT = { "/api/website/home": [200, HOME] as Route, "GET /api/members/vpn": [401, { detail: "Sign in" }] as Route,
                       "GET /api/members/vpn/public": [200, { enabled: true, billing: BILL }] as Route };

  it("offers a signed-out visitor both ways in, and warns that a lost key can't be recovered", async () => {
    serve(SIGNED_OUT);
    render(<VpnPage />);
    await settle();
    expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/join");
    expect(screen.getByRole("heading", { name: "Anonymous, with USDT" })).toBeTruthy();
    expect(document.body.textContent).toMatch(/Lose it and your time is gone: nobody can recover it/);
  });

  it("shows a new key once and won't continue until it's saved; then pays with the key, never a cookie login", async () => {
    let sent: RequestInit | undefined;
    serve({ ...SIGNED_OUT, "POST /api/members/vpn/anon": [201, { key: "KIWI-TEST" }],
            "GET /api/members/vpn/anon": [200, state({ eligible: false, reason: "Kiwi VPN is $9.99 for 30 days. Add time to start.", anonymous: true, billing: BILL, paid_until: null })],
            "POST /api/members/vpn/anon/usdt": (init) => { sent = init; return [200, { network_name: "USDT on Solana", address: "TAddr1", amount: 5, amount_usd: 5, expires_at: null, days: 15 }]; } });
    render(<VpnPage />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Get a key" }));
    await settle();
    expect(screen.getByText("KIWI-TEST")).toBeTruthy();
    const go = screen.getByRole("button", { name: "Continue" }) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(go);
    await settle();
    expect(document.body.textContent).toMatch(/Using your anonymous key/);
    expect(screen.queryByRole("button", { name: /Subscribe by card/ })).toBeNull();  // cards need a Passport
    const amount = screen.getByLabelText(/Amount/);
    fireEvent.change(amount, { target: { value: "5" } });
    expect(document.body.textContent).toMatch(/Buys about 15\.0 days/);
    fireEvent.click(screen.getByRole("button", { name: "Pay with USDT" }));
    await settle();
    expect((sent!.headers as Record<string, string>)["X-Kiwi-Key"]).toBe("KIWI-TEST");
    expect(JSON.parse(String(sent!.body))).toEqual({ amount_usd: 5, network: "solana" });
    expect(screen.getByText("TAddr1")).toBeTruthy();
    expect(document.body.textContent).toMatch(/Send exactly 5 USDT on USDT on Solana/);
  });

  it("an anonymous key deletes its device on the key route, with the key", async () => {
    sessionStorage.setItem("kiwi-vpn-key", "KIWI-TEST");
    let sent: RequestInit | undefined;
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn/anon": [200, state({ anonymous: true, billing: BILL, devices: [DEVICE] })],
            "DELETE /api/members/vpn/anon/devices/7": (init) => { sent = init; return [204, null]; } });
    render(<VpnDevicesPage />);
    await settle();
    fireEvent.click(screen.getAllByRole("button", { name: /Delete/ })[0]);
    await settle();
    const again = screen.queryAllByRole("button", { name: /Delete|Confirm|Yes/ });
    if (!sent && again.length) { fireEvent.click(again[again.length - 1]); await settle(); }
    expect((sent!.headers as Record<string, string>)["X-Kiwi-Key"]).toBe("KIWI-TEST");
  });

  it("labels the owner's test mode, and payments in it as real", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ test_mode: true, billing: BILL })] });
    render(<VpnPage />);
    await settle();
    expect(document.body.textContent).toMatch(/Test mode.*closed to everyone else.*Payments here are real/);
  });

  it("refuses an amount outside the limits before asking the server", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ billing: BILL, anonymous: false })] });
    render(<VpnPage />);
    await settle();
    fireEvent.change(screen.getByLabelText(/Amount/), { target: { value: "4" } });
    expect((screen.getByRole("button", { name: "Pay with USDT" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: /Subscribe by card/ })).toBeNull();  // never automatic billing
    fireEvent.change(screen.getByLabelText(/Amount/), { target: { value: "9.99" } });
    fireEvent.change(screen.getByLabelText("Pay with"), { target: { value: "tron" } });
    expect((screen.getByRole("button", { name: "Pay with USDT" }) as HTMLButtonElement).disabled).toBe(true);  // below Tron's own minimum
    expect(document.body.textContent).toMatch(/\$10\.96 to \$500 on this network/);
  });

  it("says payments aren't open when no provider is configured, and never offers a dead button", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ eligible: false, billing: { ...BILL, usdt: false, card: false, networks: [] } })] });
    render(<VpnPage />);
    await settle();
    expect(document.body.textContent).toMatch(/Payments open soon/);
    expect(screen.queryByRole("button", { name: /Pay with USDT|Subscribe by card/ })).toBeNull();
  });
});

describe("Kiwi VPN page", () => {
  it("shows the price, and no rank talk, when locked", async () => {
    serve({ "/api/website/home": [200, HOME],
            "GET /api/members/vpn": [200, state({ eligible: false, reason: "Kiwi VPN is $9.99 for 30 days. Add time to start.", devices: [] })] });
    const { container } = render(<VpnPage />);
    await settle();
    expect(screen.getByText(/Kiwi VPN is \$9\.99 for 30 days\. Add time to start\./)).toBeTruthy();
    expect(container.textContent).not.toMatch(/Kiwi Rank|Rank up/);
    expect(container.querySelector(".vpn-form")).toBeNull();  // no device form while locked
  });

  it("asks a signed-out visitor to sign in", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [401, { detail: "Sign in first." }] });
    render(<VpnPage />);
    await settle();
    expect(screen.getByText("Sign in first.")).toBeTruthy();
    expect(screen.getByText("Sign in").getAttribute("href")).toBe("/join");
  });

  it("lists servers with flag, online dot and load", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ devices: [DEVICE] })] });
    const { container } = render(<VpnPage />);
    await settle();
    const rows = container.querySelectorAll(".vpn-servers li");
    expect(rows).toHaveLength(2);
    expect(rows[0].querySelector(".vpn-flag img")!.getAttribute("src")).toBe("/brand/flags/nz.svg");
    expect(rows[0].querySelector(".vpn-dot")!.getAttribute("aria-label")).toBe("Online");
    expect(rows[1].className).toContain("is-off");
    expect(rows[1].querySelector("[role=meter]")!.getAttribute("aria-valuenow")).toBe("90");
  });

  it("lists devices with last connected and data used, and won't offer an offline server", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ devices: [DEVICE] })] });
    const { container } = render(<VpnDevicesPage />);
    await settle();
    const dev = container.querySelector(".vpn-device")!.textContent!;
    expect(dev).toContain("My iPhone");
    expect(dev).toContain("Auckland, New Zealand");
    expect(dev).toContain("5 min ago");
    expect(dev).toContain("1.5 GB this month");
    expect(screen.getByRole("option", { name: /Frankfurt, Germany/ }).hasAttribute("disabled")).toBe(true);
  });

  it("creates a device and shows its config once: QR code, .conf download, copy and the save-it-now warning", async () => {
    const CONFIG = "[Interface]\nPrivateKey = TEST-ONLY\nAddress = 10.66.0.6/32\n\n[Peer]\nPublicKey = TEST\nEndpoint = 203.0.113.10:51820\n";
    const created = { ...DEVICE, id: 8, name: "Laptop", last_handshake: null, rx_bytes: 0, tx_bytes: 0 };
    let devices = [DEVICE];
    serve({
      "/api/website/home": [200, HOME],
      "GET /api/members/vpn": () => [200, state({ devices })],
      "POST /api/members/vpn/devices": () => { devices = [DEVICE, created]; return [201, { device: created, config: CONFIG, filename: "kiwi-vpn-auckland-1-laptop.conf" }]; },
    });
    const blobs: Blob[] = [];
    const createObjectURL = vi.fn((b: Blob) => { blobs.push(b); return "blob:test"; });
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() }));
    const clicked: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) { clicked.push(this.download); });
    const writeText = vi.fn(async () => undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });

    const { container } = render(<VpnDevicesPage />);
    await settle();
    fireEvent.change(screen.getByPlaceholderText("e.g. My iPhone"), { target: { value: "  Laptop " } });
    fireEvent.click(screen.getByRole("button", { name: "Add device" }));
    await settle();
    expect(calls).toContain('POST /api/members/vpn/devices {"name":"Laptop","server_id":1}');

    expect(screen.getByText(/This is shown once — save it now/)).toBeTruthy();
    const img = await screen.findByAltText("QR code for Laptop");
    expect(img.getAttribute("src")).toMatch(/^data:image\/svg\+xml/);
    fireEvent.click(screen.getByRole("button", { name: "Download .conf" }));
    expect(clicked).toEqual(["kiwi-vpn-auckland-1-laptop.conf"]);
    const text = await new Promise<string>((ok) => { const r = new FileReader(); r.onload = () => ok(String(r.result)); r.readAsText(blobs[0]); });
    expect(text).toBe(CONFIG);
    fireEvent.click(screen.getByRole("button", { name: "Copy config" }));
    await settle();
    expect(writeText).toHaveBeenCalledWith(CONFIG);
    // setup steps link to the official WireGuard apps
    expect(container.querySelector('.vpn-steps a[href="https://www.wireguard.com/install/"]')).toBeTruthy();
    expect(container.querySelectorAll(".vpn-devices li")).toHaveLength(2);

    // done → the config is gone for good
    vi.stubGlobal("confirm", vi.fn(() => true));
    fireEvent.click(screen.getByRole("button", { name: /I saved it/ }));
    expect(screen.queryByAltText("QR code for Laptop")).toBeNull();
    expect(container.textContent).not.toContain("TEST-ONLY");
  });

  it.each([
    [409, "You've reached your device limit."],
    [503, "Frankfurt is offline."],
    [403, "Reach Scout Kiwi first."],
  ])("shows the API's message on %i", async (status, detail) => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state()], "POST /api/members/vpn/devices": [status, { detail }] });
    render(<VpnDevicesPage />);
    await settle();
    fireEvent.change(screen.getByPlaceholderText("e.g. My iPhone"), { target: { value: "Phone" } });
    fireEvent.click(screen.getByRole("button", { name: "Add device" }));
    await settle();
    expect(screen.getByRole("alert").textContent).toBe(detail);
    expect(screen.queryByText(/shown once/)).toBeNull();
  });

  it("explains a 503 even without a message", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state()], "POST /api/members/vpn/devices": [503, {}] });
    render(<VpnDevicesPage />);
    await settle();
    fireEvent.change(screen.getByPlaceholderText("e.g. My iPhone"), { target: { value: "Phone" } });
    fireEvent.click(screen.getByRole("button", { name: "Add device" }));
    await settle();
    expect(screen.getByRole("alert").textContent).toMatch(/server is offline/);
  });

  it("deletes a device only after confirming", async () => {
    let devices = [DEVICE];
    serve({
      "/api/website/home": [200, HOME],
      "GET /api/members/vpn": () => [200, state({ devices })],
      "DELETE /api/members/vpn/devices/7": () => { devices = []; return [204, null]; },
    });
    const { container } = render(<VpnDevicesPage />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Delete My iPhone" }));
    expect(calls.some((c) => c.startsWith("DELETE"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete My iPhone" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete for good" }));
    await settle();
    expect(calls).toContain("DELETE /api/members/vpn/devices/7");
    expect(container.querySelector(".vpn-device")).toBeNull();
    expect(screen.getByText("No devices yet.")).toBeTruthy();
  });

  it("tags the recommended server and shows ad-blocking servers", async () => {
    const servers: VpnState["servers"] = [
      { ...SERVERS[0], load: 0.1, recommended: false },
      { ...SERVERS[1], online: true, load: 0.3, adblock: true, recommended: true },
    ];
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ servers })] });
    const { container } = render(<VpnPage />);
    await settle();
    const rows = container.querySelectorAll(".vpn-servers li");
    expect(rows[0].querySelector(".vpn-tag")).toBeNull();
    expect(rows[1].querySelector(".vpn-tag")!.textContent).toBe("Recommended");
    expect(rows[1].textContent).toContain("Blocks ads & trackers");
    expect(rows[0].textContent).not.toContain("Blocks ads");
  });

  it("preselects the recommended server for a new device", async () => {
    const servers: VpnState["servers"] = [
      { ...SERVERS[0], load: 0.1, recommended: false },
      { ...SERVERS[1], online: true, load: 0.3, adblock: true, recommended: true },
    ];
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ servers })], "POST /api/members/vpn/devices": [503, { detail: "busy" }] });
    const { container } = render(<VpnDevicesPage />);
    await settle();
    expect((container.querySelector(".vpn-form select") as HTMLSelectElement).value).toBe("2");
    expect(screen.getByRole("option", { name: /Frankfurt.*Recommended/ })).toBeTruthy();
    fireEvent.change(screen.getByPlaceholderText("e.g. My iPhone"), { target: { value: "Phone" } });
    fireEvent.click(screen.getByRole("button", { name: "Add device" }));
    await settle();
    expect(calls).toContain('POST /api/members/vpn/devices {"name":"Phone","server_id":2}');
  });

  it("shows this month's data against the cap", async () => {
    serve({ "/api/website/home": [200, HOME],
            "GET /api/members/vpn": [200, state({ devices: [DEVICE], usage: { ...USAGE, used_bytes: 15 * 1024 ** 3, cap_bytes: 50 * 1024 ** 3 } })] });
    const { container } = render(<VpnPage />);
    await settle();
    expect(container.querySelector(".vpn-usage")!.textContent).toContain("15.0 GB of 50.0 GB this month");
    expect(screen.getByRole("meter", { name: "Data used this month" }).getAttribute("aria-valuenow")).toBe("30");
    expect(container.querySelector(".vpn-paused")).toBeNull();
    expect(container.querySelector(".vpn-form")).toBeNull();  // the main page never has the device form
  });

  it("says unlimited when there's no cap", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ usage: { ...USAGE, used_bytes: 1536 * 1024 ** 2 } })] });
    const { container } = render(<VpnPage />);
    await settle();
    expect(container.querySelector(".vpn-facts")!.textContent).toMatch(/Data this month1\.5 GBThis month · no limit/);
    expect(screen.queryByRole("meter", { name: "Data used this month" })).toBeNull();
  });

  it("shows a paused banner (and no form) once the monthly cap is used up", async () => {
    const until = "2026-11-01T00:00:00+00:00";
    serve({ "/api/website/home": [200, HOME],
            "GET /api/members/vpn": [200, state({ devices: [{ ...DEVICE, paused_until: until }],
                                                  usage: { ...USAGE, used_bytes: 51 * 1024 ** 3, cap_bytes: 50 * 1024 ** 3, paused_until: until } })] });
    const { container } = render(<VpnPage />);
    await settle();
    const banner = container.querySelector(".vpn-paused")!.textContent!;
    expect(banner).toContain("Paused for the rest of the month");
    expect(banner).toContain("50.0 GB");
    expect(banner).toContain("1 Nov");
    expect(screen.getByRole("meter", { name: "Data used this month" }).getAttribute("aria-valuenow")).toBe("100");
    cleanup();
    const devices = render(<VpnDevicesPage />);
    await settle();
    expect(devices.container.querySelector(".vpn-paused")).toBeTruthy();
    expect(devices.container.querySelector(".vpn-form")).toBeNull();
    expect(devices.container.querySelector(".vpn-device")!.textContent).toContain("Paused until 1 Nov");
  });

  it("hides the form at the device limit", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ max_devices: 1, devices: [DEVICE] })] });
    const { container } = render(<VpnDevicesPage />);
    await settle();
    expect(container.querySelector(".vpn-form")).toBeNull();
    expect(screen.getByText(/all 1 of your devices/)).toBeTruthy();
  });
});

describe("The redesigned page", () => {
  afterEach(() => { try { sessionStorage.clear(); } catch { /* none */ } });
  const member = (over: Partial<VpnState> = {}) => ({ "/api/website/home": [200, HOME] as Route, "GET /api/members/vpn": [200, state(over)] as Route });

  it("runs in the agreed order", async () => {
    serve(member({ billing: BILL, plan: { kind: "trial", until: "2026-12-04T00:00:00+00:00", days_left: 12 }, trial: { available: false, days: 30 } }));
    const { container } = render(<VpnPage />);
    await settle();
    const order = [".vpn-hero", "#vpn-plans", "#vpn-downloads", ".vpn-my", "#vpn-network", "#royal", "#vpn-faq"].map((s) => container.querySelector(s)!);
    order.forEach((el, i) => { if (i) expect(order[i - 1].compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy(); });
  });

  it.each([
    ["signed out and closed", { "/api/website/home": [200, HOME] as Route, "GET /api/members/vpn": [401, { detail: "x" }] as Route, "GET /api/members/vpn/public": [200, { enabled: false, billing: null }] as Route }],
    ["signed out and paying", { "/api/website/home": [200, HOME] as Route, "GET /api/members/vpn": [401, { detail: "x" }] as Route, "GET /api/members/vpn/public": [200, { enabled: true, trial: true, billing: BILL }] as Route }],
    ["on a trial", member({ devices: [DEVICE], plan: { kind: "trial", until: "2026-12-04T00:00:00+00:00", days_left: 12 } })],
    ["expired", member({ eligible: false, billing: BILL, plan: { kind: "expired", until: null, days_left: null } })],
  ])("has no dead same-page link when %s", async (_n, routes) => {
    serve(routes);
    const { container } = render(<VpnPage />);
    await settle();
    const links = [...container.querySelectorAll<HTMLAnchorElement>('.vpn a[href^="#"]')];
    expect(links.length).toBeGreaterThan(0);
    for (const a of links) expect(container.querySelector(a.getAttribute("href")!), a.textContent!).toBeTruthy();
  });

  it("has no Connect button, and never says this browser is connected", async () => {
    serve(member({ devices: [DEVICE] }));
    const { container } = render(<VpnPage />);
    await settle();
    expect(screen.queryByRole("button", { name: /^connect/i })).toBeNull();
    expect(container.querySelector(".vpn-my")!.textContent).toMatch(/1 connected in last 10 min/);  // a real handshake on a real device
    expect(container.textContent).not.toMatch(/You are (now )?(connected|protected)/i);
  });

  it("makes no claim the service can't back up", async () => {
    serve(member({ billing: BILL }));
    const { container } = render(<VpnPage />);
    await settle();
    expect(container.textContent).not.toMatch(/military|100%|zero[- ]logs?|no[- ]logs|unhackable|fully anonymous|completely anonymous/i);
  });

  it("offers trial and buying only where the server allows, and says so when it doesn't", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [401, { detail: "x" }], "GET /api/members/vpn/public": [200, { enabled: false, trial: false, billing: null }] });
    const { container } = render(<VpnPage />);
    await settle();
    const plans = container.querySelector("#vpn-plans")!;
    expect(plans.textContent).toMatch(/\$9\.99.*per 30 days.*No automatic billing.*Purchasing isn't open yet/);
    expect(plans.querySelectorAll("button")).toHaveLength(0);
    expect(container.querySelector(".vpn-hero")!.textContent).toMatch(/Opening soon/);
    expect(screen.queryByRole("link", { name: /Start Free Trial/ })).toBeNull();
  });

  it("sends a visitor to sign in for the trial while it's open", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [401, { detail: "x" }], "GET /api/members/vpn/public": [200, { enabled: true, trial: true, billing: null }] });
    render(<VpnPage />);
    await settle();
    expect(screen.getByRole("link", { name: "Start Free Trial — 30 Days" }).getAttribute("href")).toBe("/join");
  });

  it("opens the time section from the plan's button", async () => {
    serve(member({ eligible: true, billing: BILL, plan: { kind: "paid", until: "2026-12-04T00:00:00+00:00", days_left: 12 } }));
    const { container } = render(<VpnPage />);
    await settle();
    const time = container.querySelector<HTMLDetailsElement>("#vpn-time")!;
    expect(time.open).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Add Time" }));
    expect(time.open).toBe(true);
  });

  it("has no device form, config or server choice on the main page, and links to My Devices", async () => {
    serve(member({ devices: [DEVICE] }));
    const { container } = render(<VpnPage />);
    await settle();
    expect(container.querySelector(".vpn-form")).toBeNull();
    expect(container.querySelector("#vpn-manual")).toBeNull();
    expect(screen.queryByRole("button", { name: /for a new device/ })).toBeNull();
    expect(screen.getByRole("link", { name: "My Devices" }).getAttribute("href")).toBe("/vpn/devices");
  });

  it("offers no manual setup while access is closed, because that page has nothing to give yet", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [401, { detail: "x" }], "GET /api/members/vpn/public": [200, { enabled: false, trial: false, billing: null }] });
    const { container } = render(<VpnPage />);
    await settle();
    expect(container.querySelector('a[href^="/vpn/devices"]')).toBeNull();
    expect(container.querySelector("#vpn-faq")!.textContent).not.toMatch(/manual setup/i);
    expect(container.querySelector(".vpn-app .vpn-status")!.textContent).toBe("In development");
  });

  it("replaces Explore Apps with Get Kiwi VPN, going to the apps", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [401, { detail: "x" }], "GET /api/members/vpn/public": [200, { enabled: false, trial: false, billing: null }] });
    const { container } = render(<VpnPage />);
    await settle();
    expect(screen.queryByText("Explore Apps")).toBeNull();
    const cta = container.querySelector(".vpn-hero .site-button")!;
    expect(cta.textContent).toBe("Get Kiwi VPN");
    expect(cta.getAttribute("href")).toBe("#vpn-downloads");
    expect(cta.className).toContain("is-go");
  });

  it("shows pricing to signed-out visitors even while payments are off, and what happens after the trial", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [401, { detail: "x" }], "GET /api/members/vpn/public": [200, { enabled: true, trial: true, payments: false, billing: null }] });
    const { container } = render(<VpnPage />);
    await settle();
    const plans = container.querySelector("#vpn-plans")!.textContent!;
    expect(plans).toMatch(/30\s*days/);
    expect(plans).toMatch(/\$9\.99\s*per 30 days/);
    expect(plans).toMatch(/When your trial ends nothing is charged\. Your devices switch off and are kept for 30 days/);
    expect(plans).toMatch(/Purchasing isn't open yet/);
    expect(plans).not.toMatch(/annual|year|% off/i);
  });

  it("uses the server's price when it sends one", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [401, { detail: "x" }], "GET /api/members/vpn/public": [200, { enabled: true, billing: { ...BILL, price_usd: 12.5, period_days: 28 } }] });
    const { container } = render(<VpnPage />);
    await settle();
    expect(container.querySelector("#vpn-plans")!.textContent).toMatch(/\$12\.5\s*per 28 days/);
  });

  it("keeps the fallback prices in step with the backend's", () => {
    const billingPy = readFileSync("../backend/app/vpn/billing.py", "utf8");
    const servicePy = readFileSync("../backend/app/vpn/service.py", "utf8");
    expect(billingPy).toMatch(new RegExp(`^PRICE_USD = ${PLAN.price_usd}$`, "m"));
    expect(billingPy).toMatch(new RegExp(`^PERIOD_DAYS = ${PLAN.period_days}$`, "m"));
    expect(servicePy).toMatch(new RegExp(`^TRIAL_DAYS = ${PLAN.trial_days}$`, "m"));
    expect(servicePy).toMatch(new RegExp(`^KEEP_LAPSED = timedelta\\(days=${PLAN.keep_days}\\)$`, "m"));
  });

  it("keeps manual setup on My Devices, folded, with the form and WireGuard links", async () => {
    serve(member());
    const { container } = render(<VpnDevicesPage />);
    await settle();
    const manual = container.querySelector<HTMLDetailsElement>("details#vpn-manual")!;
    expect(manual.open).toBe(false);
    for (const os of ["Windows", "macOS", "Linux", "Android", "iOS"]) expect(manual.querySelector(`a[aria-label="WireGuard Setup for ${os}"]`)).toBeTruthy();
    expect(manual.querySelector(".vpn-form")).toBeTruthy();
    expect(container.textContent).toMatch(/set themselves up when you sign in/);
  });

  it("asks a signed-out visitor on My Devices to sign in, still shows manual setup, and accepts a key", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [401, { detail: "x" }], "GET /api/members/vpn/public": [200, { enabled: true, billing: BILL }],
            "GET /api/members/vpn/anon": [200, state({ anonymous: true, billing: BILL })] });
    const { container } = render(<VpnDevicesPage />);
    await settle();
    expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/join");
    expect(container.querySelector("#vpn-manual")).toBeTruthy();
    expect(container.querySelector("#vpn-manual .vpn-form")).toBeNull();
    fireEvent.change(screen.getByPlaceholderText("KIWI-XXXX-…"), { target: { value: "KIWI-TEST" } });
    fireEvent.click(screen.getByRole("button", { name: "Use my key" }));
    await settle();
    expect(screen.getByRole("heading", { name: "Your devices" })).toBeTruthy();
  });

  it("explains why an account that can't add a device can't, with the way forward", async () => {
    serve(member({ eligible: false, reason: "Your time has run out.", billing: BILL }));
    const { container } = render(<VpnDevicesPage />);
    await settle();
    expect(container.querySelector("#vpn-manual .vpn-form")).toBeNull();
    expect(container.querySelector("#vpn-manual")!.textContent).toMatch(/Your time has run out/);
    expect(screen.getByRole("link", { name: "See pricing" }).getAttribute("href")).toBe("/vpn#vpn-plans");
  });

  it("makes no over-claim about privacy or the private key on the page", async () => {
    serve(member({ billing: BILL }));
    const { container } = render(<VpnPage />);
    await settle();
    expect(container.textContent).not.toMatch(/never keep|never stored|never record|no traffic logs/i);
    expect(container.querySelector(".vpn-hero")!.textContent).toMatch(/We don't store your device's private key/);
  });

  it("shows each kind of Royal status only from the server's answer", async () => {
    serve(member({ royal: false, plan: { kind: "trial", until: "2026-12-04T00:00:00+00:00", days_left: 12 } }));
    let view = render(<VpnPage />);
    await settle();
    expect(view.container.querySelector("#royal")!.textContent).toMatch(/Your rank doesn't include this yet/);
    view.unmount();
    serve(member({ royal: false, anonymous: true }));
    view = render(<VpnPage />);
    await settle();
    expect(view.container.querySelector("#royal")!.textContent).toMatch(/Ranks belong to a Kiwi Passport/);
  });

  it("keeps the test-mode note to testers", async () => {
    serve(member({ test_mode: false }));
    const { container } = render(<VpnPage />);
    await settle();
    expect(container.querySelector(".vpn-testmode")).toBeNull();
  });

  it("falls back to a country's letters when it has no flag picture", () => {
    const { container } = render(<Flag cc="co" />);
    expect(container.querySelector("img")!.getAttribute("src")).toBe("/brand/flags/co.svg");
    fireEvent.error(container.querySelector("img")!);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toBe("CO");
  });

  it("reads a location from whatever Admin typed", () => {
    expect(place({ name: "Helsinki 1", region: "Finland", country_code: "FI" })).toEqual({ city: "Helsinki", country: "Finland" });
    expect(place({ name: "Germany", region: "Falkenstein, Germany", country_code: "DE" })).toEqual({ city: "Falkenstein", country: "Germany" });
    expect(place({ name: "Japan", region: "", country_code: "JP" })).toEqual({ city: "", country: "Japan" });
    expect(placeLine({ name: "Helsinki 1", region: "Finland", country_code: "FI" })).toBe("Helsinki, Finland");
    expect(placeLine({ name: "Japan", region: "Japan", country_code: "JP" })).toBe("Japan");
  });

  it("takes a new country from the server list with no change to the page", () => {
    // a tiny fake world: France at the left, Japan at the right, with the sea between
    const world = { step: 0.5, top: 60, columns: 720, rows: Array.from({ length: 200 }, () => `..300FR40..280JP40`) };
    const server = (id: number, cc: string, online = true) => ({ id, name: `s${id}`, region: "", country_code: cc, online });
    const one = drawMap(world, [server(1, "FR")]);
    expect(one.pins).toHaveLength(1);
    expect(one.lit).not.toBe("");
    const both = drawMap(world, [server(1, "FR"), server(2, "JP")]);
    expect(both.pins).toHaveLength(2);
    both.pins.forEach((p) => { expect(p.x).toBeGreaterThan(0); expect(p.x).toBeLessThan(1000); });
    // offline: a pin, but its country isn't lit
    const down = drawMap(world, [server(1, "FR", false)]);
    expect(down.lit).toBe("");
    expect(down.off).not.toBe("");
    expect(down.pins[0].online).toBe(false);
    // a country the map doesn't know gets no pin rather than a wrong one
    expect(drawMap(world, [server(3, "ZZ")]).pins).toHaveLength(0);
  });
});

describe("helpers", () => {
  it("formats bytes and times", () => {
    expect(bytes(0)).toBe("0 MB");
    expect(bytes(1536 * 1024)).toBe("1.5 MB");
    expect(ago(null)).toBe("Never connected");
    expect(ago("2026-10-20T00:00:00Z", Date.parse("2026-10-22T00:00:00Z"))).toBe("2 days ago");
    expect(resumeDay("2026-11-01T00:00:00+00:00")).toBe("1 Nov");
  });

  it("picks the recommended server, else the least busy online one", () => {
    const s = (id: number, online: boolean, load: number, recommended = false) =>
      ({ id, name: `s${id}`, region: "", country_code: "", online, load, adblock: false, recommended });
    expect(pickServer([s(1, true, 0.1), s(2, true, 0.5, true)])).toBe(2);
    expect(pickServer([s(1, true, 0.5), s(2, false, 0), s(3, true, 0.2)])).toBe(3);
    expect(pickServer([s(4, true, 0.2), s(3, true, 0.2)])).toBe(3);  // a tie: the lowest id
    expect(pickServer([s(5, false, 0)])).toBe(5);
    expect(pickServer([])).toBe(0);
  });
});
