import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PushNow } from "./TradingOS";

/** TEST FIXTURES only: a fake Trading OS API. Nothing reaches Telegram. */
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const flush = async () => { for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };

describe("Push a signal now", () => {
  it("asks first, then posts the chosen market and says which signal went out", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
      calls.push(`${init?.method} ${input} ${init?.body}`);
      return new Response(JSON.stringify({ id: 9, number: 7, coin: "ETH", side: "LONG" }));
    }));
    const done = vi.fn();
    render(<PushNow onDone={done} onError={() => undefined} />);
    fireEvent.click(screen.getByText("ETH"));
    vi.stubGlobal("confirm", vi.fn(() => false));
    fireEvent.click(screen.getByText("Push to Telegram"));
    await flush();
    expect(calls).toHaveLength(0);  // cancelled: nothing posted
    vi.stubGlobal("confirm", vi.fn(() => true));
    fireEvent.click(screen.getByText("Push to Telegram"));
    await flush();
    expect(calls[0]).toContain("POST /api/tradingos/admin/manual-signal");
    expect(calls[0]).toContain('"coin":"ETH"');
    expect(done).toHaveBeenCalledWith("#007 ETH LONG posted to Telegram.");
  });
});
