// Kiwi VPN 2.1 in the window: a dead server is noticed by probing through the tunnel and left within seconds, servers this PC
// already has a device for are tried first, and an idle link that answers its probes is left alone.
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { Account, AccountView, Answer, Settings, Status } from "./vpn";

let calls: { cmd: string; a: unknown }[] = [];
let reply: Answer;
let view: AccountView;
let prefs: Settings;
let probe: boolean | null;
let refuse: Record<number, string> = {};
vi.mock("@tauri-apps/api/core", () => ({
  invoke: async (cmd: string, a?: unknown) => {
    calls.push({ cmd, a });
    if (cmd === "account_state") return view;
    if (cmd === "get_settings") return prefs;
    if (cmd === "should_auto_connect") return false;
    if (cmd === "has_test_config") return false;
    if (cmd === "probe_servers") return {};
    if (cmd === "probe_tunnel") return probe;
    if (cmd === "my_ip") return "5.30.213.225";
    if (cmd === "vpn_connect") {
      const id = (a as { serverId: number }).serverId;
      if (refuse[id]) throw refuse[id];
      return reply;
    }
    if (cmd === "vpn_status" || cmd === "vpn_disconnect") return reply;
    return null;
  },
}));

const status = (over: Partial<Status>): Status => ({ state: "disconnected", handshake_age_seconds: null, rx_bytes: 0, tx_bytes: 0, error: null, connected_since: null, ...over });
const off = (): Answer => ({ service: true, message: null, status: status({}) });
const up = (rx = 1000): Answer => ({ service: true, message: null, status: status({ state: "connected", handshake_age_seconds: 5, connected_since: 1, rx_bytes: rx }) });
const SERVERS: Account["servers"] = [
  { id: 1, name: "Helsinki 1", region: "Finland", country_code: "FI", online: true, recommended: true, live_load: 0.1 },
  { id: 2, name: "Germany", region: "Falkenstein, Germany", country_code: "DE", online: true, recommended: false, live_load: 0.2 },
  { id: 3, name: "United States (Ashburn)", region: "US East", country_code: "US", online: true, recommended: false, live_load: 0.3 },
];
const account: Account = { enabled: true, eligible: true, reason: null, max_devices: 3, servers: SERVERS, devices: [], plan: null, trial: null };
const settle = () => act(async () => { await vi.advanceTimersByTimeAsync(0); });
const tick = (ms = 1000) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const pass = async (seconds: number) => { for (let i = 0; i < seconds; i++) await tick(1000); };
const connectCalls = () => calls.filter((c) => c.cmd === "vpn_connect").map((c) => c.a as { serverId: number; pauseFirst?: boolean });
const go = () => document.querySelector<HTMLButtonElement>(".go")!;

async function connectedTo(name: RegExp) {
  render(<App />);
  await settle();
  fireEvent.click(screen.getByRole("radio", { name }));
  reply = up();
  fireEvent.click(go());
  await tick();
}

beforeEach(() => {
  vi.useFakeTimers();
  calls = []; refuse = {};
  probe = false;
  reply = off();
  view = { signed_in: true, account, suggested: 1, last_server: null, error: null, known_servers: [] };
  prefs = { launch_at_startup: false, auto_connect: false, minimize_to_tray: false, notifications: false, smart_connect: false, failover: true, kill_switch: true, allow_lan: false, block_malware: false, split_exclude: [] };
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

describe("Kiwi VPN 2.1 failover", () => {
  it("a server that stops answering is left within 30 seconds, pausing the old tunnel first, with no retry of the dead one", async () => {
    await connectedTo(/Helsinki/);
    expect(connectCalls()).toEqual([{ serverId: 1 }]); // a person's own connect never pauses anything
    await pass(30); // still "connected" by the handshake's age, but nothing comes back and no probe is answered
    const moves = connectCalls().slice(1);
    expect(moves.length).toBe(1);
    expect(moves[0]).toEqual({ serverId: 2, pauseFirst: true });
    expect(calls.filter((c) => c.cmd === "probe_tunnel").length).toBeGreaterThanOrEqual(3);
    reply = up(5);
    await tick(2000);
    expect(document.body.textContent).toMatch(/moved you to/);
  });

  it("an idle link that answers its probes is never switched", async () => {
    probe = true;
    await connectedTo(/Helsinki/);
    await pass(300);
    expect(connectCalls().length).toBe(1);
    expect(calls.some((c) => c.cmd === "probe_tunnel")).toBe(true);
  });

  it("a link with traffic coming in is never probed", async () => {
    await connectedTo(/Helsinki/);
    for (let s = 0; s < 120; s++) { reply = up(1000 + s * 500); await tick(1000); }
    expect(calls.some((c) => c.cmd === "probe_tunnel")).toBe(false);
    expect(connectCalls().length).toBe(1);
  });

  it("when the app can't probe (it didn't start this tunnel), only the handshake's age decides", async () => {
    probe = null;
    await connectedTo(/Helsinki/);
    await pass(120);
    expect(connectCalls().length).toBe(1);
  });

  it("prefers a server this PC already has a device for, even over a better-ranked one", async () => {
    view = { ...view, known_servers: [1, 3] };
    await connectedTo(/Helsinki/);
    await pass(30);
    expect(connectCalls()[1]).toEqual({ serverId: 3, pauseFirst: true });
  });

  it("a server that can't be had (device limit) is skipped at once for the next", async () => {
    refuse[2] = "Too many new devices. Please wait a few minutes and try again.";
    await connectedTo(/Helsinki/);
    await pass(30);
    expect(connectCalls().slice(1).map((c) => c.serverId)).toEqual([2, 3]);
    expect(connectCalls().slice(1).every((c) => c.pauseFirst)).toBe(true);
  });

  it("never acts after the person disconnects", async () => {
    await connectedTo(/Helsinki/);
    reply = off();
    fireEvent.click(go());
    await pass(120);
    expect(connectCalls().length).toBe(1);
  });
});
