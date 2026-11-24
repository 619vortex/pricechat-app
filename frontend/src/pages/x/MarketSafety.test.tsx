import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Corrections, MarketPause } from "./MarketSafety";

const calls: { url: string; init?: RequestInit }[] = [];

vi.mock("../../api/client", () => ({
  request: vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (url === "/x/newsdesk/corrections") return [];
    if (url.endsWith("/corrections/preview")) {
      return { platform: "telegram", current_text: "JUST IN: BTCUSD_PERP longs liquidated: $10.1B on Binance in an hour.", new_text: JSON.parse(String(init?.body)).text,
        chars: 40, limit: 1024, problems: [], warnings: [], emergency_stop: false, confirm_token: "tok-1", expires_at: "2026-11-21T12:10:00Z" };
    }
    if (url.endsWith("/corrections")) return { at: "2026-11-21T12:01:00Z", event: 1045, action: "tg_edit", outcome: "done", error: null, new_text: "x", old_text: "y" };
    return {};
  }),
}));

afterEach(() => { calls.length = 0; vi.restoreAllMocks(); });

describe("Market-data posts", () => {
  it("shows why market posts are paused and resumes them only after a confirmation", async () => {
    const save = vi.fn(async () => undefined);
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(<MarketPause paused reason="automatic: 3 implausible or contradicted figures within 60 minutes" held={[
      { kind: "implausible_liquidations", subject: "BTC", why: "$120.0M in an hour is 60 times BTC's typical recorded hour", at: "2026-11-21T12:00:00Z" }]} busy={false} save={save} />);
    expect(screen.getByText("Paused: nothing market-related is posted")).toBeTruthy();
    expect(screen.getByText(/automatic: 3 implausible/)).toBeTruthy();
    fireEvent.click(screen.getByText("Resume market-data posts"));
    expect(save).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Resume market-data posts"));
    expect(save).toHaveBeenCalledWith({ market_pause: false });
    expect(confirm).toHaveBeenCalledTimes(2);
  });
});

describe("Liquidation pause", () => {
  it("says price news goes on and resumes liquidation posts only after a confirmation", async () => {
    const save = vi.fn(async () => undefined);
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(<MarketPause paused={false} reason={null} held={[]} busy={false} save={save} liqPaused liqReason="automatic: 3 implausible liquidation figures within 60 minutes" />);
    expect(screen.getByText("Liquidation posts paused")).toBeTruthy();
    expect(screen.getByText(/Price news checked against two markets goes on/)).toBeTruthy();
    expect(screen.getByText("On")).toBeTruthy();  // market-data posts themselves stay on
    fireEvent.click(screen.getByText("Resume liquidation posts"));
    expect(save).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Resume liquidation posts"));
    expect(save).toHaveBeenCalledWith({ liq_pause: false });
    expect(confirm).toHaveBeenCalledTimes(2);
  });
});

describe("Corrections", () => {
  it("previews first, sends nothing until confirmed, then sends with the token", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<Corrections />);
    fireEvent.change(screen.getByLabelText(/Event #/), { target: { value: "1045" } });
    fireEvent.change(screen.getByPlaceholderText(/The correction/), { target: { value: "<s>JUST IN: $10.1B</s> CORRECTION: about $17-18 million." } });
    fireEvent.click(screen.getByText("Preview"));
    await waitFor(() => expect(screen.getByText("Confirm and send")).toBeTruthy());
    expect(calls.some((c) => c.url === "/x/newsdesk/events/1045/corrections")).toBe(false);  // the preview sent nothing
    fireEvent.click(screen.getByText("Confirm and send"));
    await waitFor(() => expect(screen.getByText(/Done: done/)).toBeTruthy());
    const sent = calls.find((c) => c.url === "/x/newsdesk/events/1045/corrections");
    expect(JSON.parse(String(sent?.init?.body))).toEqual({ action: "tg_edit", text: "<s>JUST IN: $10.1B</s> CORRECTION: about $17-18 million.", confirm_token: "tok-1" });
    expect(confirm).toHaveBeenCalledTimes(1);
  });
});
