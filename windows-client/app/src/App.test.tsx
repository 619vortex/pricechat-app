import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import conf from "../src-tauri/tauri.conf.json";
import { LOOKUPS, placeLine, readLookup } from "./exit";
import { type Answer, type Status, formatBytes, formatDuration, phaseOf, ratesBetween, secondsUp } from "./vpn";

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

const status = (over: Partial<Status>): Status => ({ state: "disconnected", handshake_age_seconds: null, rx_bytes: 0, tx_bytes: 0, error: null, connected_since: null, ...over });
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

describe("the outside view", () => {
  it("reads a lookup, and refuses anything that isn't one", () => {
    expect(readLookup({ ip: "2.31.133.78", city: "Falkenstein", country: "DE" })).toEqual({ ip: "2.31.133.78", city: "Falkenstein", country: "DE" });
    expect(readLookup({ ip: "2a00:f2a::1", country: "ae" })).toEqual({ ip: "2a00:f2a::1", city: null, country: "AE" });
    expect(readLookup({ ip: "<img src=x>", country: "DE" })).toBeNull();
    expect(readLookup({ ip: "1.2.3.4", country: "<script>", city: "x".repeat(65) })).toEqual({ ip: "1.2.3.4", country: null, city: null });
    for (const bad of [null, "1.2.3.4", {}, { ip: 7 }, { ip: "abcd" }]) expect(readLookup(bad)).toBeNull();
  });
  it("words the place from what it knows", () => {
    expect(placeLine({ ip: "x", city: "Falkenstein", country: "DE" })).toBe("Falkenstein, Germany");
    expect(placeLine({ ip: "x", city: null, country: "FI" })).toBe("Finland");
    expect(placeLine({ ip: "x", city: null, country: null })).toBeNull();
  });
  it("the window's security policy allows every lookup, and nothing else out there", () => {
    const csp = conf.app.security.csp;
    const allowed = csp.split(";").find((d) => d.trim().startsWith("connect-src"))!.trim().split(/\s+/).filter((s) => s.startsWith("https:"));
    expect(allowed.sort()).toEqual(LOOKUPS.map((u) => new URL(u).origin).sort());
  });
});

describe("the timer", () => {
  it("comes from the service's record, not from when this window opened", () => {
    expect(secondsUp(1000, 1_060_000)).toBe(60);
    expect(secondsUp(2000, 1_000_000)).toBe(0);
    expect(secondsUp(null, 5)).toBeNull();
    expect(secondsUp(undefined, 5)).toBeNull();
  });
});

describe("the window", () => {
  let lookupReply: unknown;
  const fetched: string[] = [];
  const stats = () => screen.getByLabelText("Connection statistics").textContent!;
  beforeEach(() => {
    vi.useFakeTimers();
    calls.length = 0; fetched.length = 0; connectReply = null; testBuild = true;
    reply = { service: true, message: null, status: status({}) };
    lookupReply = { ip: "5.30.213.225", city: "Dubai", country: "AE" };
    vi.stubGlobal("fetch", async (url: string) => { fetched.push(url); return { ok: lookupReply !== null, json: async () => lookupReply }; });
  });
  afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

  it("never asks an outside service while disconnected, so it can't see the person's own address", async () => {
    render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetched).toEqual([]);
    expect(document.querySelector(".ip")!.textContent).toBe("–");
    expect(document.querySelector(".place")!.textContent).toBe("–");
    reply = { service: true, message: null, status: status({ state: "connecting" }) };
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetched).toEqual([]);
  });

  it("shows the server's place and address once connected", async () => {
    render(<App />);
    await settle();
    lookupReply = { ip: "2.31.133.78", city: "Falkenstein", country: "DE" };
    reply = up({ connected_since: 1 });
    await tick();
    expect(document.querySelector(".ip")!.textContent).toBe("Checking");
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(document.querySelector(".ip")!.textContent).toBe("2.31.133.78");
    expect(document.querySelector(".place")!.textContent).toBe("Falkenstein, Germany");
    expect(document.querySelector<HTMLImageElement>(".flag img")!.getAttribute("src")).toBe("/flags/de.svg");
  });

  it("says when it couldn't check, rather than showing nothing or a guess", async () => {
    lookupReply = null;
    reply = up({ connected_since: 1 });
    render(<App />);
    await settle();
    expect(document.querySelector(".ip")!.textContent).toBe("Checking");
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(document.querySelector(".ip")!.textContent).toBe("Couldn't check");
  });

  it("an answer that arrives after the connection changed is thrown away", async () => {
    let release: (v: unknown) => void = () => {};
    vi.stubGlobal("fetch", (url: string) => { fetched.push(url); return new Promise((done) => { release = done; }); });
    reply = up({ connected_since: 1 });
    render(<App />);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    reply = { service: true, message: null, status: status({ state: "connecting" }) };
    await tick();
    await act(async () => { release({ ok: true, json: async () => ({ ip: "9.9.9.9", country: "DE" }) }); await vi.advanceTimersByTimeAsync(0); });
    expect(document.body.textContent).not.toContain("9.9.9.9");
  });

  it("the timer picks up from the service when a window opens on a running tunnel", async () => {
    vi.setSystemTime(new Date(1_000_000_000));
    reply = up({ connected_since: 1_000_000 - 3725 });
    render(<App />);
    await settle();
    expect(stats()).toContain("01:02:05");
  });

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
