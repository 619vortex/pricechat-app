import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import conf from "../src-tauri/tauri.conf.json";
import { App } from "./App";
import { type Account, type AccountView, type Answer, type Status, formatBytes, formatDuration, phaseOf, ratesBetween, secondsUp, serverPlace } from "./vpn";

const calls: string[] = [];
const args: Record<string, unknown> = {};
let reply: Answer;
let view: AccountView;
let ip: string | null;
let testConfig = true;
let prefs = { launch_at_startup: false, auto_connect: false, minimize_to_tray: false, notifications: true };
let autoConnect = false;
let refuse: Record<string, string> = {};
let hold: Record<string, () => void> = {};
vi.mock("@tauri-apps/api/core", () => ({
  invoke: async (cmd: string, a?: unknown) => {
    calls.push(cmd);
    args[cmd] = a;
    if (refuse[cmd]) throw refuse[cmd];
    if (cmd === "account_state") return view;
    if (cmd === "has_test_config") return testConfig;
    if (cmd === "get_settings") return prefs;
    if (cmd === "set_settings") { prefs = (a as { new: typeof prefs }).new; return prefs; }
    if (cmd === "should_auto_connect") return autoConnect;
    if (cmd === "diagnostics") return { version: "0.1.0", service: "Running", tunnel: "Connected", handshake_age_seconds: 5, settings_file: "x" };
    if (cmd === "my_ip") { if (!ip) throw "no"; return ip; }
    if (cmd === "sign_in") await new Promise<void>((done) => { hold.sign_in = done; });
    if (cmd === "vpn_status" || cmd === "vpn_disconnect" || cmd === "vpn_connect") return reply;
    return null;
  },
}));

const status = (over: Partial<Status>): Status => ({ state: "disconnected", handshake_age_seconds: null, rx_bytes: 0, tx_bytes: 0, error: null, connected_since: null, ...over });
const off = (): Answer => ({ service: true, message: null, status: status({}) });
const up = (over: Partial<Status> = {}): Answer => ({ service: true, message: null, status: status({ state: "connected", handshake_age_seconds: 5, rx_bytes: 2048, tx_bytes: 1024, ...over }) });
const SERVERS: Account["servers"] = [
  { id: 2, name: "Germany", region: "Falkenstein, Germany", country_code: "DE", online: true, recommended: false },
  { id: 1, name: "Helsinki 1", region: "Finland", country_code: "FI", online: true, recommended: true },
  { id: 3, name: "Tokyo", region: "Japan", country_code: "JP", online: false, recommended: false },
];
const account = (over: Partial<Account> = {}): Account => ({ enabled: true, eligible: true, reason: null, max_devices: 3, servers: SERVERS, devices: [], plan: null, trial: null, ...over });
const signedIn = (over: Partial<AccountView> = {}): AccountView => ({ signed_in: true, account: account(), suggested: 1, last_server: null, error: null, ...over });
const signedOut = (): AccountView => ({ signed_in: false, account: null, suggested: null, last_server: null, error: null });
const settle = () => act(async () => { await vi.advanceTimersByTimeAsync(0); });
const tick = (ms = 1000) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const heading = () => screen.getByRole("heading").textContent;
const main = () => document.querySelector<HTMLButtonElement>(".go")!;
const country = (name: RegExp) => screen.getByRole("radio", { name }) as HTMLButtonElement;

describe("what counts as connected", () => {
  it("needs the service's word and a recent handshake", () => {
    expect(phaseOf(up())).toBe("on");
    expect(phaseOf(up({ handshake_age_seconds: 180 }))).toBe("on");
    expect(phaseOf(up({ handshake_age_seconds: 181 }))).toBe("connecting");
    expect(phaseOf(up({ handshake_age_seconds: null }))).toBe("connecting");
  });
  it("is never true for anything else", () => {
    for (const state of ["disconnected", "connecting", "stopping", "failed"] as const) {
      expect(phaseOf({ service: true, message: null, status: status({ state, handshake_age_seconds: 1 }) })).not.toBe("on");
    }
    expect(phaseOf(null)).toBe("loading");
    expect(phaseOf({ service: false, status: null, message: "x" })).toBe("no-service");
    expect(phaseOf({ service: true, status: null, message: null })).toBe("no-service");
  });
});

describe("the numbers", () => {
  it("formats sizes, time and places", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 ** 3)).toBe("5.0 GB");
    expect(formatDuration(3725)).toBe("01:02:05");
    expect(serverPlace(SERVERS[0])).toBe("Falkenstein, Germany");
    expect(serverPlace(SERVERS[1])).toBe("Helsinki, Finland");
    expect(serverPlace({ name: "Japan", region: "Japan", country_code: "JP" })).toBe("Japan");
  });
  it("works out speed, and refuses to invent one", () => {
    expect(ratesBetween({ at: 0, rx: 0, tx: 0 }, { at: 2000, rx: 4000, tx: 2000 })).toEqual({ rx: 2000, tx: 1000 });
    expect(ratesBetween(null, { at: 1, rx: 9, tx: 9 })).toEqual({ rx: 0, tx: 0 });
    expect(ratesBetween({ at: 0, rx: 100, tx: 100 }, { at: 1000, rx: 5, tx: 5 })).toEqual({ rx: 0, tx: 0 });
  });
  it("takes the timer from the service's record", () => {
    expect(secondsUp(1000, 1_060_000)).toBe(60);
    expect(secondsUp(2000, 1_000_000)).toBe(0);
    expect(secondsUp(null, 5)).toBeNull();
  });
  it("the window's security policy lets the page reach nothing on the internet", () => {
    expect(conf.app.security.csp).not.toMatch(/https:/);
    expect(conf.app.security.csp).toMatch(/connect-src ipc: http:\/\/ipc\.localhost;/);
  });
});

describe("the window", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    calls.length = 0; refuse = {}; hold = {}; testConfig = true; autoConnect = false;
    prefs = { launch_at_startup: false, auto_connect: false, minimize_to_tray: false, notifications: true };
    reply = off(); view = signedIn(); ip = "5.30.213.225";
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); });

  it("says the service is offline first, whatever else is true, and offers nothing it can't do", async () => {
    reply = { service: false, status: null, message: "Something other than the Kiwi VPN service is answering. Nothing was sent to it." };
    view = signedOut();
    render(<App />);
    await settle();
    expect(heading()).toBe("Service offline");
    expect(screen.getByRole("alert").textContent).toContain("Something other than");
    expect(main().disabled).toBe(true);
    expect(main().textContent).toBe("Connect");
  });

  it("signed out: offers sign-in, waits for the browser, can cancel, then shows countries", async () => {
    view = signedOut();
    render(<App />);
    await settle();
    expect(main().textContent).toBe("Sign in with Price Chat");
    expect(screen.queryByRole("radiogroup")).toBeNull();
    fireEvent.click(main());
    await settle();
    expect(calls).toContain("sign_in");
    expect(document.body.textContent).toContain("Finish signing in in your browser");
    expect(main().textContent).toBe("Cancel");
    fireEvent.click(main());
    await settle();
    expect(calls).toContain("cancel_sign_in");
    view = signedIn();
    await act(async () => { hold.sign_in(); await vi.advanceTimersByTimeAsync(0); });
    expect(main().textContent).toBe("Connect");
    expect(screen.getByRole("radiogroup")).toBeTruthy();
  });

  it("shows why signing in failed", async () => {
    view = signedOut();
    refuse.sign_in = "Signing in took too long. Please try again.";
    render(<App />);
    await settle();
    fireEvent.click(main());
    await settle();
    expect(screen.getByRole("alert").textContent).toContain("took too long");
    expect(main().textContent).toBe("Sign in with Price Chat");
  });

  it("shows countries with flags, the suggested one chosen and offline ones unavailable, and connects by naming only the server", async () => {
    render(<App />);
    await settle();
    expect(country(/Helsinki/).getAttribute("aria-checked")).toBe("true");
    expect(country(/Falkenstein/).querySelector<HTMLImageElement>(".flag img")!.getAttribute("src")).toBe("/flags/de.svg");
    expect(country(/Tokyo/).disabled).toBe(true);
    fireEvent.click(country(/Falkenstein/));
    expect(calls).not.toContain("vpn_connect");
    expect(country(/Falkenstein/).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(main());
    await settle();
    expect(args.vpn_connect).toEqual({ serverId: 2 });
  });

  it("never asks for a config file on the main screen: it's only under Settings, Advanced", async () => {
    render(<App />);
    await settle();
    fireEvent.click(main());
    await settle();
    expect(calls).toContain("vpn_connect");
    expect(calls).not.toContain("vpn_connect_test_file");
    expect(document.body.textContent).not.toMatch(/config file|\.conf|WireGuard/i);
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(document.querySelector<HTMLDetailsElement>("details.advanced")!.open).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Use a config file" }));
    await settle();
    expect(calls).toContain("vpn_connect_test_file");
  });

  it("a build without the config file option doesn't offer it", async () => {
    testConfig = false;
    render(<App />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    expect(screen.queryByRole("button", { name: "Use a config file" })).toBeNull();
  });

  it("switching country while connected reconnects straight away", async () => {
    reply = up({ connected_since: 1 });
    view = signedIn({ last_server: 1 });
    render(<App />);
    await settle();
    fireEvent.click(country(/Falkenstein/));
    await settle();
    expect(args.vpn_connect).toEqual({ serverId: 2 });
  });

  it("explains a refusal in plain words, with the way to My Devices", async () => {
    refuse.vpn_connect = "You've used all your devices. Remove one under My Devices on pricechat.live, then try again.";
    render(<App />);
    await settle();
    fireEvent.click(main());
    await settle();
    expect(screen.getByRole("alert").textContent).toContain("used all your devices");
    expect(heading()).toBe("Not connected");
    fireEvent.click(screen.getByRole("button", { name: "Open My Devices" }));
    expect(args.open_site).toEqual({ page: "devices" });
  });

  it("offers the free trial when that's the next step", async () => {
    view = signedIn({ account: account({ eligible: false, reason: "Start your free 30-day trial", trial: { available: true, days: 30 } }) });
    render(<App />);
    await settle();
    expect(main().textContent).toBe("Start free trial: 30 days");
    expect(screen.queryByRole("radiogroup")).toBeNull();
    fireEvent.click(main());
    await settle();
    expect(calls).toContain("start_trial");
  });

  it("the service switched off: says so, and shows no trial, plan, countries or plans link", async () => {
    // even if something still reports a running trial, it isn't shown beside the unavailable message
    view = signedIn({ account: account({ enabled: false, eligible: false, reason: "Kiwi VPN is unavailable right now. Please try again later.", plan: { kind: "trial", days_left: 30 }, trial: { available: false, days: 30 } }) });
    render(<App />);
    await settle();
    expect(document.body.textContent).toContain("Kiwi VPN is unavailable right now");
    expect(document.body.textContent).not.toMatch(/Free trial|days left|See plans/);
    expect(screen.queryByRole("radiogroup")).toBeNull();
    expect(main().textContent).toBe("Connect");
    expect(main().disabled).toBe(true);
  });

  it("a running trial with servers online: shows the trial and countries, and Connect works", async () => {
    view = signedIn({ account: account({ plan: { kind: "trial", days_left: 30 } }) });
    render(<App />);
    await settle();
    expect(document.querySelector(".plan")!.textContent).toBe("Free trial · 30 days left");
    expect(country(/Falkenstein/)).toBeTruthy();
    expect(country(/Helsinki/)).toBeTruthy();
    expect(main().disabled).toBe(false);
    expect(document.body.textContent).not.toMatch(/unavailable/);
    fireEvent.click(main());
    await settle();
    expect(args.vpn_connect).toEqual({ serverId: 1 });
  });

  it("access but no server online: says why Connect is off", async () => {
    view = signedIn({ suggested: null, account: account({ plan: { kind: "trial", days_left: 30 }, servers: [{ ...SERVERS[0], online: false }] }) });
    render(<App />);
    await settle();
    expect(main().disabled).toBe(true);
    expect(document.body.textContent).toContain("No servers are available right now");
  });

  it("says why an account can't connect, and shows the plans", async () => {
    view = signedIn({ account: account({ eligible: false, reason: "Your time has run out." }) });
    render(<App />);
    await settle();
    expect(main().disabled).toBe(true);
    expect(screen.getByRole("status").textContent).toContain("Your time has run out");
    fireEvent.click(screen.getByRole("button", { name: "See plans" }));
    expect(args.open_site).toEqual({ page: "plans" });
  });

  it("signs out from Settings, through the app's Rust side", async () => {
    render(<App />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    view = signedOut();
    fireEvent.click(screen.getByRole("button", { name: "Sign out of Kiwi VPN" }));
    await settle();
    expect(calls).toContain("sign_out");
    expect(main().textContent).toBe("Sign in with Price Chat");
  });

  it("shows no connected state or speeds until the handshake is reported", async () => {
    reply = { service: true, message: null, status: status({ state: "connecting" }) };
    render(<App />);
    await settle();
    expect(heading()).toBe("Connecting");
    expect(document.body.textContent).not.toContain("Connected");
    expect(main().textContent).toBe("Disconnect");
    expect(screen.getByLabelText("Connection statistics").textContent).not.toMatch(/\/s/);
    reply = up();
    await tick();
    expect(heading()).toBe("Connected");
    expect(screen.getByRole("status").textContent).toContain("5 s ago");
  });

  it("drops back from connected when the handshake goes stale", async () => {
    reply = up();
    render(<App />);
    await settle();
    expect(heading()).toBe("Connected");
    reply = up({ handshake_age_seconds: 200 });
    await tick();
    expect(heading()).toBe("Connecting");
  });

  it("reports real speed from the service's counters", async () => {
    reply = up({ rx_bytes: 0, tx_bytes: 0 });
    render(<App />);
    await settle();
    reply = up({ rx_bytes: 2048, tx_bytes: 1024 });
    await tick();
    const text = screen.getByLabelText("Connection statistics").textContent!;
    expect(text).toContain("2.0 KB/s");
    expect(text).toContain("1.0 KB/s");
  });

  it("shows the location from the server list and the address from Price Chat", async () => {
    render(<App />);
    await tick();
    expect(document.querySelector(".ip")!.textContent).toBe("5.30.213.225");
    expect(document.querySelector(".place")!.textContent).toBe("–");
    ip = "2.31.133.78";
    view = signedIn({ last_server: 2 });
    reply = up({ connected_since: 1 });
    fireEvent.click(main());
    await tick();
    await tick(4000);
    expect(document.querySelector(".ip")!.textContent).toBe("2.31.133.78");
    expect(document.querySelector(".place")!.textContent).toBe("Falkenstein, Germany");
    expect(screen.queryByText(/same as before connecting/)).toBeNull();
  });

  it("warns when the address is the same after connecting", async () => {
    render(<App />);
    await tick();
    reply = up({ connected_since: 1 });
    await tick();
    await tick(4000);
    expect(screen.getByText(/same as before connecting/)).toBeTruthy();
  });

  it("says when it couldn't check the address", async () => {
    ip = null;
    render(<App />);
    await settle();
    expect(document.querySelector(".ip")!.textContent).toBe("Checking");
    await tick(20_000);
    expect(document.querySelector(".ip")!.textContent).toBe("Couldn't check");
  });

  it("the timer picks up from the service when a window opens on a running tunnel", async () => {
    vi.setSystemTime(new Date(1_000_000_000));
    reply = up({ connected_since: 1_000_000 - 3725 });
    render(<App />);
    await settle();
    expect(screen.getByLabelText("Connection statistics").textContent).toContain("01:02:05");
  });

  it("settings save, connect-at-startup needs launch-at-startup, and the kill switch says it isn't available", async () => {
    render(<App />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    const connectAtStart = () => screen.getByRole("switch", { name: /Connect when Windows starts/ }) as HTMLInputElement;
    expect(connectAtStart().disabled).toBe(true);
    fireEvent.click(screen.getByRole("switch", { name: /Launch when Windows starts/ }));
    await settle();
    expect(prefs.launch_at_startup).toBe(true);
    expect(connectAtStart().disabled).toBe(false);
    fireEvent.click(connectAtStart());
    await settle();
    expect(prefs.auto_connect).toBe(true);
    fireEvent.click(screen.getByRole("switch", { name: /Launch when Windows starts/ }));
    await settle();
    expect(prefs).toMatchObject({ launch_at_startup: false, auto_connect: false });
    expect(document.body.textContent).toMatch(/Kill switch.*Not available yet/);
    expect(screen.queryByRole("switch", { name: /Kill switch/ })).toBeNull();
  });

  it("auto-connects at most once, only when Rust says so, and never retries after a failure", async () => {
    autoConnect = true;
    refuse.vpn_connect = "That server is offline right now.";
    render(<App />);
    await settle();
    await tick(5000);
    expect(calls.filter((c) => c === "should_auto_connect")).toHaveLength(1);
    expect(calls.filter((c) => c === "vpn_connect")).toHaveLength(1);
    await tick(70_000);
    expect(calls.filter((c) => c === "vpn_connect")).toHaveLength(1);
    cleanup();
    calls.length = 0;
    autoConnect = false;
    refuse = {};
    render(<App />);
    await settle();
    await tick(2000);
    expect(calls).not.toContain("vpn_connect");
  });

  it("notifies on connecting and on an unasked drop, but not on the person's own disconnect", async () => {
    render(<App />);
    await settle();
    reply = up();
    await tick();
    expect(args.notify).toEqual({ kind: "connected", place: "Helsinki, Finland" });
    calls.length = 0;
    reply = off();
    await tick();
    expect(args.notify).toEqual({ kind: "lost", place: null });
    calls.length = 0;
    reply = up();
    await tick();
    calls.length = 0;
    reply = off();
    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    await settle();
    await tick();
    expect(calls).not.toContain("notify");
  });

  it("shows the account's plan, and diagnostics with nothing secret", async () => {
    view = signedIn({ account: account({ plan: { kind: "trial", days_left: 12 } }) });
    render(<App />);
    await settle();
    expect(document.querySelector(".plan")!.textContent).toBe("Free trial · 12 days left");
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    const advanced = document.querySelector<HTMLDetailsElement>("details.advanced")!;
    advanced.open = true;
    fireEvent(advanced, new Event("toggle"));
    await settle();
    expect(calls).toContain("diagnostics");
    expect(document.querySelector(".diag")!.textContent).toMatch(/Version0\.1\.0/);
    expect(document.querySelector(".diag")!.textContent).not.toMatch(/key|token|PrivateKey/i);
  });

  it("disconnect goes to the service", async () => {
    reply = up();
    render(<App />);
    await settle();
    reply = off();
    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    await settle();
    expect(calls).toContain("vpn_disconnect");
    expect(heading()).toBe("Not connected");
  });
});
