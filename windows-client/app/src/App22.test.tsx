// Kiwi VPN 2.2 in the window: "new version available" with an Install button (the service found and checks the update; the page
// only asks), the daily check, the strict kill switch and the DNS over TLS option, and the list of cases where the normal kill
// switch lets traffic through.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App, UPDATE_EVERY_MS, UPDATE_FIRST_MS } from "./App";
import { RELEASE_CASES } from "./tools";
import { updateWarning } from "./update";
import type { Account, AccountView, Answer, Settings, Status, UpdateInfo } from "./vpn";

let calls: { cmd: string; a: unknown }[] = [];
let reply: Answer;
let view: AccountView;
let prefs: Settings;
let features: string[] = [];
vi.mock("@tauri-apps/api/core", () => ({
  invoke: async (cmd: string, a?: unknown) => {
    calls.push({ cmd, a });
    if (cmd === "account_state") return view;
    if (cmd === "get_settings") return prefs;
    if (cmd === "set_settings") { prefs = (a as { new: Settings }).new; return prefs; }
    if (cmd === "should_auto_connect") return false;
    if (cmd === "has_test_config") return false;
    if (cmd === "probe_servers") return {};
    if (cmd === "diagnostics") return { version: "2.2.0", service: "Running", tunnel: "Off", handshake_age_seconds: null, settings_file: "x", features };
    if (cmd === "my_ip") return "5.30.213.225";
    if (cmd === "vpn_status" || cmd === "vpn_disconnect" || cmd === "vpn_connect" || cmd === "update_check" || cmd === "update_install") return reply;
    return null;
  },
}));

const status = (over: Partial<Status>): Status => ({ state: "disconnected", handshake_age_seconds: null, rx_bytes: 0, tx_bytes: 0, error: null, connected_since: null, ...over });
const answer = (over: Partial<Status>): Answer => ({ service: true, message: null, status: status(over) });
const available = (over: Partial<UpdateInfo> = {}): UpdateInfo => ({ state: "available", version: "2.2.1", notes: "Fixes reconnect after sleep.", security: false, error: null, ...over });
const SERVERS: Account["servers"] = [{ id: 1, name: "Helsinki 1", region: "Finland", country_code: "FI", online: true, recommended: true, live_load: 0 }];
const account: Account = { enabled: true, eligible: true, reason: null, max_devices: 3, servers: SERVERS, devices: [], plan: null, trial: null };
const settle = () => act(async () => { await vi.advanceTimersByTimeAsync(0); });
const tick = (ms = 1000) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const count = (cmd: string) => calls.filter((c) => c.cmd === cmd).length;
const text = () => document.body.textContent ?? "";

beforeEach(() => {
  vi.useFakeTimers();
  calls = []; features = ["kill_switch", "dns_guard", "strict_kill_switch", "dot_guard", "update"];
  reply = answer({});
  view = { signed_in: true, account, suggested: 1, last_server: null, error: null, known_servers: [] };
  prefs = { launch_at_startup: false, auto_connect: false, minimize_to_tray: false, notifications: false, smart_connect: false, failover: true, kill_switch: true, allow_lan: true, block_malware: false, split_exclude: [] };
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("Kiwi VPN 2.2 updates", () => {
  it("shows the new version with an Install button, and Install asks the service with nothing else", async () => {
    reply = answer({ update: available() });
    render(<App />);
    await tick();
    expect(text()).toMatch(/New version available: Kiwi VPN 2\.2\.1/);
    expect(text()).toMatch(/Fixes reconnect after sleep/);
    fireEvent.click(screen.getByRole("button", { name: "Install" }));
    await settle();
    const installs = calls.filter((c) => c.cmd === "update_install");
    expect(installs.length).toBe(1);
    expect(installs[0].a).toBeUndefined(); // no URL, path or version from the page
  });

  it("while connected, Install first says what happens to the connection, and Later does nothing", async () => {
    reply = answer({ state: "connected", handshake_age_seconds: 3, connected_since: 1, kill_switch: true, update: available() });
    render(<App />);
    await tick();
    fireEvent.click(screen.getByRole("button", { name: "Install" }));
    await settle();
    expect(count("update_install")).toBe(0);
    expect(text()).toMatch(/disconnected for about 30 seconds/);
    expect(text()).toMatch(/kill switch is off during the update too/);
    fireEvent.click(screen.getByRole("button", { name: "Later" }));
    await settle();
    expect(count("update_install")).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Install" }));
    fireEvent.click(screen.getByRole("button", { name: "Install now" }));
    await settle();
    expect(count("update_install")).toBe(1);
  });

  it("the warning matches the protection in force", () => {
    expect(updateWarning(false, false)).toMatch(/normal connection meanwhile/);
    expect(updateWarning(true, false)).toMatch(/kill switch is off during the update too/);
    expect(updateWarning(true, true)).toMatch(/strict kill switch keeps blocking/);
    expect(updateWarning(true, true)).not.toMatch(/normal connection/);
  });

  it("a required or security update says so", async () => {
    reply = answer({ update: available({ state: "required", security: true }) });
    render(<App />);
    await tick();
    expect(text()).toMatch(/Update required: Kiwi VPN 2\.2\.1/);
    expect(text()).toMatch(/fixes a security problem/);
  });

  it("downloading, installing and failed are shown; up to date and unknown show nothing", async () => {
    reply = answer({ update: available({ state: "downloading" }) });
    render(<App />);
    await tick();
    expect(text()).toMatch(/Downloading Kiwi VPN 2\.2\.1/);
    reply = answer({ update: available({ state: "installing" }) });
    await tick();
    expect(text()).toMatch(/Installing Kiwi VPN 2\.2\.1/);
    reply = answer({ update: available({ state: "failed", error: "The update couldn't be installed (wrong-signer). Kiwi VPN will try again later." }) });
    await tick();
    expect(text()).toMatch(/wrong-signer/);
    for (const state of ["none", "unknown"] as const) {
      reply = answer({ update: { state, error: "Automatic updates aren't set up in this build yet." } });
      await tick();
      expect(screen.queryByRole("status", { name: "Update" })).toBeNull();
      expect(text()).not.toMatch(/New version|Update required|aren't set up/);
    }
    expect(screen.queryByRole("button", { name: "Install" })).toBeNull();
  });

  it("an older service with no update information shows nothing", async () => {
    render(<App />);
    await tick();
    expect(text()).not.toMatch(/New version/);
  });

  it("asks the service to check soon after opening, then once a day", async () => {
    render(<App />);
    await settle();
    expect(count("update_check")).toBe(0);
    await tick(UPDATE_FIRST_MS);
    expect(count("update_check")).toBe(1);
    await tick(UPDATE_EVERY_MS);
    expect(count("update_check")).toBe(2);
  });
});

describe("Kiwi VPN 2.2 kill switch", () => {
  const openSettings = async () => {
    render(<App />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    await settle();
  };

  it("offers the strict kill switch, off by default, only with the kill switch on", async () => {
    await openSettings();
    const strict = screen.getByRole("switch", { name: "Strict kill switch" }) as HTMLInputElement;
    expect(strict.checked).toBe(false);
    expect(strict.disabled).toBe(false);
    fireEvent.click(strict);
    await settle();
    expect(prefs.strict_kill_switch).toBe(true);
    cleanup();
    prefs = { ...prefs, kill_switch: false, strict_kill_switch: false };
    await openSettings();
    expect((screen.getByRole("switch", { name: "Strict kill switch" }) as HTMLInputElement).disabled).toBe(true);
  });

  it("offers blocking DNS over TLS, off by default", async () => {
    await openSettings();
    const dot = screen.getByRole("switch", { name: "Block DNS over TLS outside the VPN" }) as HTMLInputElement;
    expect(dot.checked).toBe(false);
    fireEvent.click(dot);
    await settle();
    expect(prefs.block_dot).toBe(true);
  });

  it("neither is offered by a service that can't enforce it", async () => {
    features = ["kill_switch", "dns_guard"];
    await openSettings();
    expect(screen.queryByRole("switch", { name: "Strict kill switch" })).toBeNull();
    expect(screen.queryByRole("switch", { name: "Block DNS over TLS outside the VPN" })).toBeNull();
    expect(screen.getByRole("switch", { name: "Kill switch" })).toBeTruthy();
  });

  it("lists every case where the normal kill switch lets traffic through", async () => {
    await openSettings();
    expect(text()).toMatch(/The normal kill switch lets your traffic out unprotected/);
    for (const c of RELEASE_CASES) expect(text()).toContain(c);
    expect(RELEASE_CASES.length).toBe(6);
    expect(RELEASE_CASES.join(" ")).toMatch(/crash/);
    expect(RELEASE_CASES.join(" ")).toMatch(/Windows starts/);
    expect(RELEASE_CASES.join(" ")).toMatch(/6 times/);
    expect(RELEASE_CASES.join(" ")).toMatch(/10 minutes/);
    expect(RELEASE_CASES.join(" ")).toMatch(/2 minutes/);
    expect(RELEASE_CASES.join(" ")).toMatch(/updated/);
    cleanup();
    prefs = { ...prefs, strict_kill_switch: true };
    await openSettings();
    expect(text()).toMatch(/strict kill switch keeps blocking in all of these cases/);
  });

  it("a strict block with no tunnel says so and offers Disconnect", async () => {
    reply = answer({ state: "failed", kill_switch: true, strict: true, error: "The VPN couldn't reconnect. The strict kill switch is still blocking all internet traffic: connect again, or press Disconnect to get your normal connection back." });
    render(<App />);
    await tick();
    expect(text()).toMatch(/strict kill switch is blocking all internet traffic until you connect or press Disconnect/);
    fireEvent.click(screen.getByRole("button", { name: "Disconnect and unblock" }));
    await settle();
    expect(count("vpn_disconnect")).toBe(1);
  });

  it("while connected with the strict kill switch, the banner says it holds even if Kiwi stops", async () => {
    reply = answer({ state: "connected", handshake_age_seconds: 3, connected_since: 1, kill_switch: true, strict: true });
    render(<App />);
    await tick();
    expect(text()).toMatch(/Strict kill switch on/);
  });
});
