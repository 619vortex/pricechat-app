import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StoryInsight, type Insight } from "./StoryInsight";

/** TEST FIXTURE (made up): a released story that went out with a picture, told twice, first seen as an X lead. */
const INSIGHT: Insight = {
  feedback: [], published: true, picture: true,
  memory: { id: 4, first: "Hackers drain $400M from Bybit exchange", told: 2, lead_at: "2026-11-19T08:00:00Z", lead_by: "@osint_watch", confirmed_at: "2026-11-19T08:38:00Z",
    sightings: [{ lane: "x-intel", ref: "topic:9", at: "2026-11-19T08:00:00Z", title: "Bybit drained", status: "lead" },
      { lane: "telegram", ref: "501", at: "2026-11-19T09:00:00Z", title: "Hackers drain $400M from Bybit exchange", status: "told" }] },
  performance: { impressions: 4200, engagements: 90, age_hours: 10, reach: 1.4, engagement: 1.2, index: 1.3, bait: false },
  chief: { status: "off" },
};

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function stub(post: (body: { target: string; verdict: string }) => unknown) {
  const fetch = vi.fn(async (_url: string, init?: RequestInit) => {
    const body = init?.method === "POST" ? post(JSON.parse(String(init.body))) : INSIGHT;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

describe("story panel › your verdict", () => {
  it("stores one-click feedback against the story and shows the story so far", async () => {
    const fetch = stub((b) => ({ target: b.target, verdicts: [b.verdict] }));
    render(<StoryInsight target="event:12" />);
    expect(await screen.findByText(/told 2×/)).toBeTruthy();
    expect(screen.getByText(/first seen on X .* by @osint_watch, confirmed 38 min later/)).toBeTruthy();
    expect(screen.getByText(/1.30× a typical post/)).toBeTruthy();
    expect(screen.getByText(/off until you approve it/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Missed story" }) as HTMLButtonElement).disabled).toBe(true);  // it was published
    fireEvent.click(screen.getByRole("button", { name: "Wrong image" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Wrong image" }).getAttribute("aria-pressed")).toBe("true"));
    const call = fetch.mock.calls.find(([, init]) => init?.method === "POST");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ target: "event:12", verdict: "wrong_image" });
  });
});
