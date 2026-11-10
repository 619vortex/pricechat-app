import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TelegramTradingCard, type TgStatus } from "./TelegramTradingCard";

/** TEST FIXTURES only: a fake owner API answering by method + path. Nothing reaches Telegram or an exchange. */
const calls: { key: string; body: unknown }[] = [];
function serve(routes: Record<string, unknown>) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${input.split("?")[0]}`;
    calls.push({ key, body: init?.body ? JSON.parse(String(init.body)) : null });
    let out = routes[key];
    if (typeof out === "function") out = (out as () => unknown)();
    if (out instanceof Response) return out;
    return out === undefined ? new Response(JSON.stringify({ detail: "nope" }), { status: 404 }) : new Response(JSON.stringify(out));
  }));
}
const flush = async () => { for (let i = 0; i < 5; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const base: TgStatus = {
  switches: { live: false, kill: false, killed_at: null, gate_signal_enter: true, global_paused: false, mainnet_execution: false, live_phrase: "ENABLE TELEGRAM MAINNET" },
  referral: { codes: ["BANTER", "KLGXFMNI"], url: "https://app.hyperliquid.xyz/join/BANTER" },
  window_hours: 24, activity: { manual_preview: 3, tg_mainnet_preview: 2 }, failures: { entry_not_filled: 1 }, open_positions: { PAPER: 1 }, pending_previews: 0,
  recent: [{ at: "2026-11-10T08:00:00Z", actor: "m:7", action: "tg_mainnet_preview", details: { coin: "BTC" } }],
};
const PATH = "/api/tradingos/admin/telegram-trading";

describe("Telegram trading (owner)", () => {
  it("shows mainnet OFF, the referral codes, activity and failures", async () => {
    serve({ [`GET ${PATH}`]: base });
    render(<TelegramTradingCard />);
    await flush();
    const t = document.body.textContent!;
    expect(t).toMatch(/Mainnet from TelegramOFF/);
    expect(t).toMatch(/BANTER, KLGXFMNI/);
    expect(t).toMatch(/Mainnet previews: 2/);
    expect(t).toMatch(/Not filled: 1/);
  });

  it("the emergency stop asks first, then saves kill: true", async () => {
    serve({ [`GET ${PATH}`]: base, [`PUT ${PATH}`]: { ...base, switches: { ...base.switches, kill: true } } });
    render(<TelegramTradingCard />);
    await flush();
    vi.stubGlobal("confirm", vi.fn(() => false));
    fireEvent.click(screen.getByRole("button", { name: "Emergency stop" }));
    await flush();
    expect(calls.some((c) => c.key.startsWith("PUT"))).toBe(false);
    vi.stubGlobal("confirm", vi.fn(() => true));
    fireEvent.click(screen.getByRole("button", { name: "Emergency stop" }));
    await flush();
    expect(calls.find((c) => c.key === `PUT ${PATH}`)?.body).toEqual({ kill: true });
  });

  it("mainnet stays off until the exact phrase is typed, and never while the stop is on", async () => {
    serve({ [`GET ${PATH}`]: base, [`PUT ${PATH}`]: base });
    render(<TelegramTradingCard />);
    await flush();
    const on = screen.getByRole("button", { name: "Switch mainnet on" }) as HTMLButtonElement;
    expect(on.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Confirmation phrase"), { target: { value: "enable telegram mainnet" } });
    expect(on.disabled).toBe(false);
    vi.stubGlobal("confirm", vi.fn(() => true));
    fireEvent.click(on);
    await flush();
    expect(calls.find((c) => c.key === `PUT ${PATH}`)?.body).toEqual({ live: true, phrase: "enable telegram mainnet" });
    cleanup();
    serve({ [`GET ${PATH}`]: { ...base, switches: { ...base.switches, kill: true } } });
    render(<TelegramTradingCard />);
    await flush();
    fireEvent.change(screen.getByLabelText("Confirmation phrase"), { target: { value: "ENABLE TELEGRAM MAINNET" } });
    expect((screen.getByRole("button", { name: "Switch mainnet on" }) as HTMLButtonElement).disabled).toBe(true);
    expect(document.body.textContent).toMatch(/Emergency stop is on/);
  });
});
