import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NetworkSwitches } from "./NetworkSwitches";

const calls: { url: string; init?: RequestInit }[] = [];
let state: Record<string, unknown> = { x_links: true, x_cost_per_post_usd: 0.2, emergency: false, x_paused: "", telegram_paused: "", post_style: "clean" };

vi.mock("../../api/client", () => ({
  request: vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    if (init?.method === "PUT") {
      const body = JSON.parse(String(init.body));
      state = { ...state, ...body, x_cost_per_post_usd: (body.x_links ?? state.x_links) ? 0.2 : 0.015 };
    }
    return state;
  }),
}));

afterEach(() => { calls.length = 0; state = { x_links: true, x_cost_per_post_usd: 0.2, emergency: false, x_paused: "", telegram_paused: "", post_style: "clean" }; });

describe("Price Chat News switches", () => {
  it("pauses both platforms only after a confirmation, and shows the X link cost", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    render(<NetworkSwitches />);
    expect(await screen.findByText("Posting automatically")).toBeTruthy();
    expect(screen.getByText(/\$0\.200 per X post/)).toBeTruthy();
    fireEvent.click(screen.getByText("Emergency pause"));
    expect(calls.some((c) => c.init?.method === "PUT")).toBe(false);
    fireEvent.click(screen.getByText("Emergency pause"));
    await waitFor(() => expect(screen.getByText("Paused: nothing is posted")).toBeTruthy());
    expect(JSON.parse(String(calls.find((c) => c.init?.method === "PUT")?.init?.body))).toEqual({ emergency: true });
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it("turns source links on X off", async () => {
    render(<NetworkSwitches />);
    fireEvent.click(await screen.findByText("Turn links off"));
    await waitFor(() => expect(screen.getByText(/\$0\.015 per X post/)).toBeTruthy());
  });

  it("starts on clean (the default) and switches between wire, clean and classic", async () => {
    const { findByText, getByText, queryByText } = within(render(<NetworkSwitches />).container);
    expect(await findByText("Clean")).toBeTruthy();
    expect(getByText(/^Default: picture first/)).toBeTruthy();
    expect(queryByText("Use clean")).toBeNull();
    fireEvent.click(getByText("Use wire"));
    await waitFor(() => expect(getByText("Wire")).toBeTruthy());
    expect(getByText(/^Opt-in: short factual headline/)).toBeTruthy();
    expect(JSON.parse(String(calls.find((c) => c.init?.method === "PUT")?.init?.body))).toEqual({ post_style: "wire" });
    fireEvent.click(getByText("Use classic"));
    await waitFor(() => expect(getByText("Classic")).toBeTruthy());
    fireEvent.click(getByText("Use clean"));
    await waitFor(() => expect(getByText("Clean")).toBeTruthy());
    expect(JSON.parse(String(calls.filter((c) => c.init?.method === "PUT").at(-1)?.init?.body))).toEqual({ post_style: "clean" });
  });

  it("shows clean for a style it doesn't know", async () => {
    state = { ...state, post_style: "something-else" };
    const { findByText } = within(render(<NetworkSwitches />).container);
    expect(await findByText("Clean")).toBeTruthy();
  });
});
