import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NetworkSwitches, PostStyleSwitch } from "./NetworkSwitches";

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

  it("shows Wire, Clean and Classic as three buttons, Clean the default, and switches only the style", async () => {
    const { findByRole, getByRole, getByText } = within(render(<PostStyleSwitch />).container);
    const group = await findByRole("group", { name: "Post style" });
    const names = within(group).getAllByRole("button").map((b) => b.textContent);
    expect(names).toEqual(["Wire", "Clean (default)", "Classic"]);
    expect(getByRole("button", { name: "Clean (default)" }).getAttribute("aria-pressed")).toBe("true");
    expect(getByText(/^Default: picture first/)).toBeTruthy();
    fireEvent.click(getByRole("button", { name: "Clean (default)" }));
    expect(calls.some((c) => c.init?.method === "PUT")).toBe(false);
    fireEvent.click(getByRole("button", { name: "Wire" }));
    await waitFor(() => expect(getByRole("button", { name: "Wire" }).getAttribute("aria-pressed")).toBe("true"));
    expect(getByText(/^Opt-in: short factual headline/)).toBeTruthy();
    expect(JSON.parse(String(calls.find((c) => c.init?.method === "PUT")?.init?.body))).toEqual({ post_style: "wire" });
    fireEvent.click(getByRole("button", { name: "Classic" }));
    await waitFor(() => expect(getByRole("button", { name: "Classic" }).getAttribute("aria-pressed")).toBe("true"));
    fireEvent.click(getByRole("button", { name: "Clean (default)" }));
    await waitFor(() => expect(getByRole("button", { name: "Clean (default)" }).getAttribute("aria-pressed")).toBe("true"));
    const puts = calls.filter((c) => c.init?.method === "PUT").map((c) => JSON.parse(String(c.init?.body)));
    expect(puts).toEqual([{ post_style: "wire" }, { post_style: "classic" }, { post_style: "clean" }]);
  });

  it("shows clean for a style it doesn't know, or when none is saved", async () => {
    state = { ...state, post_style: "something-else" };
    const { findByRole } = within(render(<PostStyleSwitch />).container);
    expect((await findByRole("button", { name: "Clean (default)" })).getAttribute("aria-pressed")).toBe("true");
  });
});
