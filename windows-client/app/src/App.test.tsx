import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { type Answer, type Status, formatBytes, formatDuration, phaseOf, ratesBetween } from "./vpn";

const calls: string[] = [];
let reply: Answer;
let connectReply: Answer | null = null;
let testBuild = true;
vi.mock("@tauri-apps/api/core", () => ({
  invoke: async (cmd: string) => {
    calls.push(cmd);
    if (cmd === "test_build") return testBuild;
    if (cmd === "vpn_connect_test_file") return connectReply;
    return reply;
  },
}));

const status = (over: Partial<Status>): Status => ({ state: "disconnected", handshake_age_seconds: null, rx_bytes: 0, tx_bytes: 0, error: null, ...over });
const up = (over: Partial<Status> = {}): Answer => ({ service: true, message: null, status: status({ state: "connected", handshake_age_seconds: 5, rx_bytes: 2048, tx_bytes: 1024, ...over }) });
const settle = () => act(async () => { await vi.advanceTimersByTimeAsync(0); });
const tick = () => act(async () => { await vi.advanceTimersByTimeAsync(1000); });

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
  it("formats sizes and time", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(5 * 1024 ** 3)).toBe("5.0 GB");
    expect(formatDuration(3725)).toBe("01:02:05");
  });
  it("works out speed, and refuses to invent one", () => {
    expect(ratesBetween({ at: 0, rx: 0, tx: 0 }, { at: 2000, rx: 4000, tx: 2000 })).toEqual({ rx: 2000, tx: 1000 });
    expect(ratesBetween(null, { at: 1, rx: 9, tx: 9 })).toEqual({ rx: 0, tx: 0 });
    expect(ratesBetween({ at: 0, rx: 100, tx: 100 }, { at: 1000, rx: 5, tx: 5 })).toEqual({ rx: 0, tx: 0 });
  });
});

describe("the window", () => {
  beforeEach(() => { vi.useFakeTimers(); calls.length = 0; connectReply = null; testBuild = true; reply = { service: true, message: null, status: status({}) }; });
  afterEach(() => { cleanup(); vi.useRealTimers(); });

  it("says the service is offline, and offers nothing it can't do", async () => {
    reply = { service: false, status: null, message: "The Kiwi VPN service isn't running." };
    render(<App />);
    await settle();
    expect(screen.getByRole("heading").textContent).toBe("Service offline");
    expect(screen.getByRole("alert").textContent).toContain("isn't running");
    expect((screen.getByRole("button") as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows no connected state or speeds until the handshake is reported", async () => {
    reply = { service: true, message: null, status: status({ state: "connecting" }) };
    render(<App />);
    await settle();
    expect(screen.getByRole("heading").textContent).toBe("Connecting");
    expect(document.body.textContent).not.toContain("Connected");
    expect(screen.getByRole("button").textContent).toBe("Disconnect");
    expect(screen.getByLabelText("Connection statistics").textContent).not.toMatch(/\/s/);
    reply = up();
    await tick();
    expect(screen.getByRole("heading").textContent).toBe("Connected");
    expect(screen.getByRole("status").textContent).toContain("5 s ago");
  });

  it("drops back from connected when the handshake goes stale", async () => {
    reply = up();
    render(<App />);
    await settle();
    expect(screen.getByRole("heading").textContent).toBe("Connected");
    reply = up({ handshake_age_seconds: 200 });
    await tick();
    expect(screen.getByRole("heading").textContent).toBe("Connecting");
  });

  it("reports real speed and time from the service's counters", async () => {
    reply = up({ rx_bytes: 0, tx_bytes: 0 });
    render(<App />);
    await settle();
    reply = up({ rx_bytes: 2048, tx_bytes: 1024 });
    await tick();
    const text = screen.getByLabelText("Connection statistics").textContent!;
    expect(text).toContain("2.0 KB/s");
    expect(text).toContain("1.0 KB/s");
    expect(text).toContain("00:00:01");
  });

  it("a cancelled file picker changes nothing, and a refused config shows why", async () => {
    render(<App />);
    await settle();
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    await settle();
    expect(calls).toContain("vpn_connect_test_file");
    expect(screen.getByRole("heading").textContent).toBe("Not connected");
    connectReply = { service: true, message: null, status: status({ state: "failed", error: "the config must have one interface and one peer" }) };
    reply = connectReply;
    fireEvent.click(screen.getByRole("button", { name: "Connect" }));
    await settle();
    expect(screen.getByRole("heading").textContent).toBe("Couldn't connect");
    expect(screen.getByRole("alert").textContent).toContain("one interface");
  });

  it("disconnect goes to the service", async () => {
    reply = up();
    render(<App />);
    await settle();
    reply = { service: true, message: null, status: status({}) };
    fireEvent.click(screen.getByRole("button", { name: "Disconnect" }));
    await settle();
    expect(calls).toContain("vpn_disconnect");
    expect(screen.getByRole("heading").textContent).toBe("Not connected");
  });

  it("a build without the config file can't connect until sign-in exists", async () => {
    testBuild = false;
    render(<App />);
    await settle();
    expect((screen.getByRole("button") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByRole("button").textContent).toBe("Sign in to connect");
  });
});
