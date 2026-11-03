import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VpnPage, ago, bytes, flag, pickServer, resumeDay, type VpnDevice, type VpnState } from "./vpn";

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
               networks: [{ key: "ethereum", name: "USDT on Ethereum" }, { key: "tron", name: "USDT on Tron" }, { key: "solana", name: "USDT on Solana" }] };

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
            "GET /api/members/vpn/anon": [200, state({ eligible: false, reason: "Kiwi VPN is $9.99 a month. Add time to start.", anonymous: true, billing: BILL, paid_until: null })],
            "POST /api/members/vpn/anon/usdt": (init) => { sent = init; return [200, { network_name: "USDT on Tron", address: "TAddr1", amount: 5, amount_usd: 5, expires_at: null, days: 15 }]; } });
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
    expect(JSON.parse(String(sent!.body))).toEqual({ amount_usd: 5, network: "tron" });
    expect(screen.getByText("TAddr1")).toBeTruthy();
    expect(document.body.textContent).toMatch(/Send exactly 5 USDT on USDT on Tron|Send exactly 5 USDT/);
  });

  it("an anonymous key deletes its device on the key route, with the key", async () => {
    sessionStorage.setItem("kiwi-vpn-key", "KIWI-TEST");
    let sent: RequestInit | undefined;
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn/anon": [200, state({ anonymous: true, billing: BILL, devices: [DEVICE] })],
            "DELETE /api/members/vpn/anon/devices/7": (init) => { sent = init; return [204, null]; } });
    render(<VpnPage />);
    await settle();
    fireEvent.click(screen.getAllByRole("button", { name: /Delete/ })[0]);
    await settle();
    const again = screen.queryAllByRole("button", { name: /Delete|Confirm|Yes/ });
    if (!sent && again.length) { fireEvent.click(again[again.length - 1]); await settle(); }
    expect((sent!.headers as Record<string, string>)["X-Kiwi-Key"]).toBe("KIWI-TEST");
  });

  it("refuses an amount outside the limits before asking the server", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ billing: BILL, anonymous: false })] });
    render(<VpnPage />);
    await settle();
    fireEvent.change(screen.getByLabelText(/Amount/), { target: { value: "4" } });
    expect((screen.getByRole("button", { name: "Pay with USDT" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("button", { name: /Subscribe by card: \$9\.99 a month/ })).toBeTruthy();  // a Passport can use a card
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
            "GET /api/members/vpn": [200, state({ eligible: false, reason: "Kiwi VPN is $9.99 a month. Add time to start.", devices: [] })] });
    const { container } = render(<VpnPage />);
    await settle();
    expect(screen.getByText(/Kiwi VPN is \$9\.99 a month\. Add time to start\./)).toBeTruthy();
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

  it("lists servers with flag, online dot and load, and devices with last connected and data used", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ devices: [DEVICE] })] });
    const { container } = render(<VpnPage />);
    await settle();
    const rows = container.querySelectorAll(".vpn-servers li");
    expect(rows).toHaveLength(2);
    expect(rows[0].querySelector(".vpn-flag")!.textContent).toBe("🇳🇿");
    expect(rows[0].querySelector(".vpn-dot")!.getAttribute("aria-label")).toBe("Online");
    expect(rows[1].className).toContain("is-off");
    expect(rows[1].querySelector("[role=meter]")!.getAttribute("aria-valuenow")).toBe("90");
    const dev = container.querySelector(".vpn-device")!.textContent!;
    expect(dev).toContain("My iPhone");
    expect(dev).toContain("Auckland 1");
    expect(dev).toContain("5 min ago");
    expect(dev).toContain("1.5 GB this month");
    // the offline server can't be picked for a new device
    expect(screen.getByRole("option", { name: /Frankfurt 1/ }).hasAttribute("disabled")).toBe(true);
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

    const { container } = render(<VpnPage />);
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
    render(<VpnPage />);
    await settle();
    fireEvent.change(screen.getByPlaceholderText("e.g. My iPhone"), { target: { value: "Phone" } });
    fireEvent.click(screen.getByRole("button", { name: "Add device" }));
    await settle();
    expect(screen.getByRole("alert").textContent).toBe(detail);
    expect(screen.queryByText(/shown once/)).toBeNull();
  });

  it("explains a 503 even without a message", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state()], "POST /api/members/vpn/devices": [503, {}] });
    render(<VpnPage />);
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
    const { container } = render(<VpnPage />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Delete My iPhone" }));
    expect(calls.some((c) => c.startsWith("DELETE"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete My iPhone" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete for good" }));
    await settle();
    expect(calls).toContain("DELETE /api/members/vpn/devices/7");
    expect(container.querySelector(".vpn-device")).toBeNull();
    expect(screen.getByText(/No devices yet/)).toBeTruthy();
  });

  it("preselects the recommended server and tags it; shows ad-blocking servers", async () => {
    const servers: VpnState["servers"] = [
      { ...SERVERS[0], load: 0.1, recommended: false },
      { ...SERVERS[1], online: true, load: 0.3, adblock: true, recommended: true },
    ];
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ servers })],
            "POST /api/members/vpn/devices": [503, { detail: "busy" }] });
    const { container } = render(<VpnPage />);
    await settle();
    const rows = container.querySelectorAll(".vpn-servers li");
    expect(rows[0].querySelector(".vpn-tag")).toBeNull();
    expect(rows[1].querySelector(".vpn-tag")!.textContent).toBe("Recommended");
    expect(rows[1].textContent).toContain("🛡️ Blocks ads & trackers");
    expect(rows[0].textContent).not.toContain("Blocks ads");
    expect((container.querySelector(".vpn-form select") as HTMLSelectElement).value).toBe("2");
    expect(screen.getByRole("option", { name: /Frankfurt 1.*Recommended/ })).toBeTruthy();
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
    expect(container.querySelector(".vpn-form")).toBeTruthy();
  });

  it("says unlimited when there's no cap", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ usage: { ...USAGE, used_bytes: 1536 * 1024 ** 2 } })] });
    const { container } = render(<VpnPage />);
    await settle();
    expect(container.querySelector(".vpn-usage")!.textContent).toBe("1.5 GB this month · no limit");
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
    expect(container.querySelector(".vpn-form")).toBeNull();
    expect(container.querySelector(".vpn-device")!.textContent).toContain("Paused until 1 Nov");
    expect(screen.getByRole("meter", { name: "Data used this month" }).getAttribute("aria-valuenow")).toBe("100");
  });

  it("hides the form at the device limit", async () => {
    serve({ "/api/website/home": [200, HOME], "GET /api/members/vpn": [200, state({ max_devices: 1, devices: [DEVICE] })] });
    const { container } = render(<VpnPage />);
    await settle();
    expect(container.querySelector(".vpn-form")).toBeNull();
    expect(screen.getByText(/all 1 of your devices/)).toBeTruthy();
  });
});

describe("helpers", () => {
  it("formats flags, bytes and times", () => {
    expect(flag("NZ")).toBe("🇳🇿");
    expect(flag("x")).toBe("🌐");
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
