// Kiwi VPN 2.0 in the window: Smart Connect, failover, the kill switch only when the service can do it, split tunneling, tools.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { Account, AccountView, Answer, Settings, Status } from "./vpn";

let calls: { cmd: string; a: unknown }[] = [];
let reply: Answer;
let view: AccountView;
let prefs: Settings;
let timings: Record<number, number | null>;
let features: string[] = [];
vi.mock("@tauri-apps/api/core", () => ({
  invoke: async (cmd: string, a?: unknown) => {
    calls.push({ cmd, a });
    if (cmd === "account_state") return view;
    if (cmd === "get_settings") return prefs;
    if (cmd === "set_settings") { prefs = (a as { new: Settings }).new; return prefs; }
    if (cmd === "should_auto_connect") return false;
    if (cmd === "has_test_config") return false;
    if (cmd === "probe_servers") return timings;
    if (cmd === "diagnostics") return { version: "2.0.0", service: "Running", tunnel: "Off", handshake_age_seconds: null, settings_file: "x", features };
    if (cmd === "speed_test") return { ping_ms: 41.6, down_bytes: 25_000_000, down_ms: 2000, up_bytes: 5_000_000, up_ms: 2000, via: "speed.cloudflare.com" };
    if (cmd === "leak_check") return { ipv4: "178.156.252.184", ipv6: null, dns: ["178.156.252.184"], tunnel_ip: "178.156.252.184", expected_dns: ["178.156.252.184"] };
    if (cmd === "my_ip") return "5.30.213.225";
    if (cmd === "vpn_status" || cmd === "vpn_disconnect" || cmd === "vpn_connect") return reply;
    return null;
  },
}));

const status = (over: Partial<Status>): Status => ({ state: "disconnected", handshake_age_seconds: null, rx_bytes: 0, tx_bytes: 0, error: null, connected_since: null, ...over });
const off = (): Answer => ({ service: true, message: null, status: status({}) });
const up = (): Answer => ({ service: true, message: null, status: status({ state: "connected", handshake_age_seconds: 5, connected_since: 1 }) });
const stale = (): Answer => ({ service: true, message: null, status: status({ state: "connected", handshake_age_seconds: 400, connected_since: 1 }) });
const SERVERS: Account["servers"] = [
  { id: 1, name: "Helsinki 1", region: "Finland", country_code: "FI", online: true, recommended: true, live_load: 0, proxy_host: "hel1.vpn.pricechat.live" },
  { id: 2, name: "Germany", region: "Falkenstein, Germany", country_code: "DE", online: true, recommended: false, live_load: 0, proxy_host: "fsn1.vpn.pricechat.live" },
  { id: 3, name: "United States (Ashburn)", region: "US East", country_code: "US", online: true, recommended: false, live_load: 0.9, proxy_host: "ash1.vpn.pricechat.live" },
];
const account: Account = { enabled: true, eligible: true, reason: null, max_devices: 3, servers: SERVERS, devices: [], plan: null, trial: null };
const settle = () => act(async () => { await vi.advanceTimersByTimeAsync(0); });
const tick = (ms = 1000) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
/** Like the real app's once-a-second check: time passes in 1 s steps. */
const pass = async (seconds: number) => { for (let i = 0; i < seconds; i++) await tick(1000); };
const connects = () => calls.filter((c) => c.cmd === "vpn_connect").map((c) => (c.a as { serverId: number }).serverId);
const go = () => document.querySelector<HTMLButtonElement>(".go")!;

beforeEach(() => {
  vi.useFakeTimers();
  calls = []; features = [];
  reply = off();
  view = { signed_in: true, account, suggested: 1, last_server: null, error: null };
  prefs = { launch_at_startup: false, auto_connect: false, minimize_to_tray: false, notifications: false, smart_connect: true, failover: true, kill_switch: false, allow_lan: true, block_malware: false, split_exclude: [] };
  timings = { 1: 90, 2: 40, 3: 30 };
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("Kiwi VPN 2.0", () => {
  it("Smart Connect is the default choice and connects to the fastest server that isn't busy", async () => {
    render(<App />);
    await settle();
    expect((screen.getByRole("radio", { name: /Fastest server/ }) as HTMLButtonElement).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(go());
    await settle();
    expect(calls.findIndex((c) => c.cmd === "probe_servers")).toBeLessThan(calls.findIndex((c) => c.cmd === "vpn_connect")); // timed before connecting
    expect(connects()).toEqual([2]); // Ashburn is faster but 90% busy: 30 + 270 > 40
  });

  it("choosing a country by hand still connects there, without timing", async () => {
    render(<App />);
    await settle();
    fireEvent.click(screen.getByRole("radio", { name: /Helsinki/ }));
    fireEvent.click(go());
    await settle();
    expect(connects()).toEqual([1]);
    expect(calls.some((c) => c.cmd === "probe_servers")).toBe(false);
  });

  it("failover: a dead server is retried once, then the next best takes over, and the person is told", async () => {
    render(<App />);
    await settle();
    fireEvent.click(screen.getByRole("radio", { name: /Germany/ }));
    reply = up();
    fireEvent.click(go());
    await tick();
    expect(connects()).toEqual([2]);
    reply = stale(); // the server stopped answering
    await pass(47);
    expect(connects()).toEqual([2, 2]); // the same server again first
    await pass(47);
    expect(connects().length).toBe(3);
    expect(connects()[2]).not.toBe(2);
    reply = up();
    await tick(2000);
    expect(document.body.textContent).toMatch(/stopped answering, so Kiwi VPN moved you to/);
  });

  it("never fails over after the person disconnects", async () => {
    render(<App />);
    await settle();
    reply = up();
    fireEvent.click(go());
    await tick();
    reply = off();
    fireEvent.click(go()); // Disconnect
    await pass(200);
    expect(connects().length).toBe(1);
  });

  it("offers the kill switch only when the service says it can do it", async () => {
    render(<App />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    await settle();
    expect(screen.queryByRole("switch", { name: "Kill switch" })).toBeNull();
    expect(document.body.textContent).toMatch(/Kill switch.*Not available yet/);
    cleanup();
    features = ["kill_switch"];
    render(<App />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    await settle();
    fireEvent.click(screen.getByRole("switch", { name: "Kill switch" }));
    await settle();
    expect(prefs.kill_switch).toBe(true);
  });

  it("split tunneling saves only valid ranges and refuses ones that would leak", async () => {
    render(<App />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    await settle();
    const box = screen.getByLabelText("Addresses that skip the VPN");
    fireEvent.change(box, { target: { value: "0.0.0.0/0" } });
    expect(screen.getByRole("alert").textContent).toMatch(/too wide/);
    fireEvent.change(box, { target: { value: "192.168.1.7/24\n10.0.0.5" } });
    fireEvent.click(screen.getByRole("button", { name: /^Save/ }));
    await settle();
    expect(prefs.split_exclude).toEqual(["192.168.1.0/24", "10.0.0.5/32"]);
  });

  it("Tools: speed test and a passing leak check, in plain words", async () => {
    render(<App />);
    await settle();
    reply = up();
    fireEvent.click(go());
    await tick();
    fireEvent.click(screen.getByRole("button", { name: "Tools" }));
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Run speed test" }));
    await settle();
    expect(document.body.textContent).toContain("100.0 Mbps down · 20.0 Mbps up · 42 ms");
    fireEvent.click(screen.getByRole("button", { name: "Check for leaks" }));
    await settle();
    expect(document.querySelector(".leaks.is-ok")).toBeTruthy();
    expect(document.body.textContent).toContain("IPv6: none visible");
  });
});

describe("split tunneling limits", () => {
  it("refuses a list the service couldn't take", async () => {
    render(<App />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    await settle();
    fireEvent.change(screen.getByLabelText("Addresses that skip the VPN"), { target: { value: "20.1.2.3\n60.1.2.3\n100.1.2.3\n140.1.2.3" } });
    expect(screen.getByRole("alert").textContent).toMatch(/pieces; Kiwi VPN allows 64/);
    expect((screen.getByRole("button", { name: /^Save/ }) as HTMLButtonElement).disabled).toBe(true);
  });
});
