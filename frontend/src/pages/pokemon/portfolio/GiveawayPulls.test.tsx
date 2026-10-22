import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GiveawayPulls } from "./GiveawayPulls";

/** TEST FIXTURES only: a fake binder answering by path. */
let binder: Record<string, unknown> = {};
const posts: unknown[] = [];
function serve(b: Record<string, unknown>) {
  binder = b;
  posts.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body));
      posts.push(body);
      binder = { ...binder, in_portfolio: body.show };
      return new Response(JSON.stringify({ in_portfolio: body.show }), { status: 200 });
    }
    return new Response(JSON.stringify(binder), { status: String(input).includes("/members/packs/binder") ? 200 : 404 });
  }));
}
const pull = (id: number, over: Record<string, unknown> = {}) => ({ id, name: `Mon ${id}`, set: "Jungle", number: `${id}/64`, year: 1999, edition: "Unlimited",
  image: `https://img.test/${id}.png`, tier: "rare", value: 1000, count: 1, ...over });
const flush = async () => { for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Giveaway pulls in the portfolio", () => {
  it("offers to add the pulls to the portfolio as virtual cards, then lets the member remove them again", async () => {
    serve({ cards: [pull(1), pull(10, { count: 2 })], unique: 2, pulls: 3, value: 3000, in_portfolio: false });
    const changed = vi.fn();
    const { container } = render(<MemoryRouter><GiveawayPulls onChanged={changed} /></MemoryRouter>);
    await flush();
    expect(container.textContent).toContain("Add your 3 Pack Giveaway cards to this portfolio?");
    expect(container.textContent).toContain("never on public leaderboards");
    await act(async () => screen.getByRole("button", { name: "Add to my portfolio" }).click());
    await flush();
    expect(posts).toEqual([{ show: true }]);
    expect(changed).toHaveBeenCalled();  // the dashboard reloads: the cards are now in its value and chart
    expect(container.textContent).toContain("Your 3 Pack Giveaway cards are in this portfolio");
    await act(async () => screen.getByRole("button", { name: "Remove them" }).click());
    await flush();
    expect(posts).toEqual([{ show: true }, { show: false }]);
  });

  it("shows nothing at all without any pulls", async () => {
    serve({ cards: [], unique: 0, pulls: 0, value: 0, in_portfolio: true });
    const { container } = render(<MemoryRouter><GiveawayPulls /></MemoryRouter>);
    await flush();
    expect(container.innerHTML).toBe("");
  });
});
