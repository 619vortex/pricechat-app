import { act, cleanup, render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PublicMarket } from "./market";

const home = {
  sections: { latest: true, trending: true, whats_it_worth: true, price_board: true, originals: true, community: true, story: true },
  latest: [], trending: [], whats_it_worth: [], originals: [], price_board: [], categories: [],
  links: { telegram: "https://t.me/thepricechat", x: "https://x.com/pricechat", youtube: "https://www.youtube.com/@pricechat" }, site_url: "https://pricechat.live",
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
});

async function open(path: string) {
  vi.stubGlobal("fetch", vi.fn(async (input: string) => String(input).startsWith("/api/website/home")
    ? new Response(JSON.stringify(home), { status: 200, headers: { "Content-Type": "application/json" } })
    : new Response(JSON.stringify({ detail: "nope" }), { status: 404, headers: { "Content-Type": "application/json" } })));
  render(<MemoryRouter initialEntries={[path]}><Routes><Route path="/pokemon/*" element={<PublicMarket />} /></Routes></MemoryRouter>);
  for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
const canonical = () => document.head.querySelectorAll<HTMLLinkElement>('link[rel="canonical"]');

describe("public Pokémon Market head", () => {
  it("each page has its own title and a canonical URL of its own path (not the market home)", async () => {
    await open("/pokemon/sets?grade=PSA%209");
    expect(document.title).toBe("Pokémon Card Sets by Market Cap · Price Chat");
    expect(canonical()).toHaveLength(1);
    expect(canonical()[0].href).toBe("https://pricechat.live/pokemon/sets");
    cleanup();
    expect(canonical()).toHaveLength(0); // nothing is left behind for the dashboard
    await open("/pokemon");
    expect(document.title).toBe("Pokémon Market · Price Chat");
    expect(canonical()[0].href).toBe("https://pricechat.live/pokemon");
  });
});
