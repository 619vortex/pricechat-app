import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ShortMarketPage } from "./ShortMarket";

/** TEST FIXTURES only: a fake Short Market API. No real worker, no real video; nothing is posted anywhere. */
const calls: { method: string; url: string; body?: string }[] = [];
const now = new Date().toISOString();
const options = {
  categories: [{ key: "markets", label: "Market moves" }, { key: "history", label: "Today in money history" }],
  styles: [{ key: "cartoon3d", label: "3D Cartoon", accent: "#ffd23f" }, { key: "clay", label: "Claymation", accent: "#ff8a5c" }],
  voices: [{ key: "am_michael", label: "Michael · warm, American" }, { key: "bf_emma", label: "Emma · clear, British" }],
  lengths: [10, 20, 30, 40, 50], qualities: [{ key: "standard", label: "Standard", detail: "576p scenes", minutes: 14 }, { key: "best", label: "Best", detail: "720p scenes", minutes: 26 }],
  defaults: { style: "cartoon3d", voice: "am_michael", seconds: 30, quality: "standard" },
};
const pipeline = [{ key: "idea", label: "Find the story", where: "backend" }, { key: "script", label: "Write the story", where: "worker" },
  { key: "visuals", label: "Animate 3D scenes", where: "worker" }];
const overview = (worker: Record<string, unknown> = {}) => ({
  worker: { registered: 1, online: true, name: "This PC", last_seen_at: now, video_ai: { model: "Wan2.2" }, voices: ["system", "kokoro"], writer: "gemma3:27b", ...worker },
  counts: { queued: 0, rendering: 0, ready: 1, failed: 0 }, options, pipeline, max_open: 6 });
const idea = (over: Record<string, unknown> = {}) => ({ id: 11, key: "k", category: "markets", category_label: "Market moves", topic: "Crypto market mood today: Fear (31/100)",
  headline: "The crypto market's mood today is Fear: 31 out of 100", score: 1.4, found_at: now, used: false, short_id: null,
  facts: [{ text: "The whole crypto market is worth $3.41 trillion.", source: "CoinGecko" }], sources: [{ name: "CoinGecko", url: "https://www.coingecko.com" }], ...over });
const short = (over: Record<string, unknown> = {}) => ({ id: 5, topic: "Crypto market mood today: Fear (31/100)", category: "markets", category_label: "Market moves",
  style: "cartoon3d", style_label: "3D Cartoon", voice: "am_michael", seconds: 30, quality: "standard", status: "ready", stages: {}, title: "Crypto's mood just flipped to fear",
  hashtags: ["#crypto"], duration: 31.2, size_bytes: 14_200_000, has_video: true, has_thumb: true, error: null, generation: 1, attempts: 1, created_at: now,
  downloaded_at: null, ...over });
const detail = (over: Record<string, unknown> = {}) => ({ ...short(over), caption: "Crypto's mood just flipped to fear\n\nThe index reads 31. Not financial advice.\n\n#crypto",
  script: { title: "Crypto's mood just flipped to fear", engine: "ollama:gemma3:27b",
            segments: [{ text: "The crypto market just lost its nerve.", screen: "Mood: fear", visual: "a nervous robot watches a gauge swing left" }] },
  brief: { facts: [{ text: "The index reads 31 out of 100 today: Fear.", source: "alternative.me" }] }, sources: [{ name: "CoinGecko", url: "https://www.coingecko.com" }],
  render_info: { worker: "This PC", seconds: 840, scenes: 7, ai_scenes: 7, tts: "kokoro:am_michael", llm: "ollama:gemma3:27b", renderer: "wan2.2-ti2v-5b 576x1024 + ffmpeg" } });

function serve(routes: Record<string, unknown | ((body: string | undefined) => unknown)>) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = String(input);
    calls.push({ method: init?.method ?? "GET", url, body: init?.body as string | undefined });
    const path = url.split("?")[0];
    const hit = routes[`${init?.method ?? "GET"} ${url}`] ?? routes[`${init?.method ?? "GET"} ${path}`] ?? routes[url] ?? routes[path];
    const body = typeof hit === "function" ? (hit as (b?: string) => unknown)(init?.body as string | undefined) : hit;
    return body === undefined ? new Response(JSON.stringify({ detail: "nope" }), { status: 404 }) : new Response(JSON.stringify(body), { status: 200 });
  }));
}
const flush = async () => { for (let i = 0; i < 5; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
const page = (at = "/short-market") => render(<MemoryRouter initialEntries={[at]}><Routes><Route path="/short-market/*" element={<ShortMarketPage />} /></Routes></MemoryRouter>);
afterEach(() => { cleanup(); vi.unstubAllGlobals(); window.localStorage.clear(); });

describe("Short Market", () => {
  const openOptions = async () => {
    const box = document.querySelector("details.smx-more-box") as HTMLDetailsElement;
    await act(async () => { box.open = true; fireEvent(box, new Event("toggle")); });
    await flush();
  };

  it("is one button: a click makes a video with an automatic look and length, and finished videos have a download", async () => {
    let items: unknown[] = [short()];
    serve({ "/api/short-market/overview": overview(), "/api/short-market/shorts": () => ({ items, total: items.length }),
            "POST /api/short-market/shorts": () => { items = [short({ id: 6, status: "rendering", has_video: false, has_thumb: false, title: null,
              stages: { idea: { status: "done", at: now, note: "" }, script: { status: "done", at: now, note: "" }, visuals: { status: "running", at: now, note: "scene 3 of 7 · about 6 min left" } } }), short()]; return detail(); } });
    page();
    await flush();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Money stories,filmed by your AI.");
    expect(screen.getByText("This PC is online")).toBeTruthy();
    expect(screen.getByText("3D video model ready")).toBeTruthy();
    expect(screen.getByText(/A new 10 to 50 second story every time you press it\. Ready in about 14 minutes\./)).toBeTruthy();
    expect(screen.getAllByRole("button").filter((b) => !b.className.includes("smx-thumb")).map((b) => b.textContent)).toEqual(["Make a video"]);  // one button
    expect(calls.some((c) => c.url.includes("/ideas"))).toBe(false);  // the sources are only read when the options are opened
    const dl = screen.getByRole("link", { name: "Download · 14.2 MB" });
    expect(dl.getAttribute("href")).toBe("/api/short-market/shorts/5/media/video?download=1&g=1");
    expect(dl.hasAttribute("download")).toBe(true);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Make a video" })); });
    await flush();
    expect(JSON.parse(calls.find((c) => c.method === "POST")?.body ?? "{}")).toEqual({ style: "auto", voice: "am_michael", seconds: null, quality: "standard", idea_id: null, category: null });
    expect(screen.getByRole("heading", { name: "Now making" })).toBeTruthy();
    expect(screen.getByText("Animate 3D scenes · scene 3 of 7 · about 6 min left")).toBeTruthy();
  });

  it("the folded options choose the look, voice, length and the story, and are remembered", async () => {
    serve({ "/api/short-market/overview": overview(), "/api/short-market/ideas": { items: [idea(), idea({ id: 12, headline: "Already told", used: true, short_id: 5 })], refreshed_at: now },
            "/api/short-market/shorts": { items: [], total: 0 }, "POST /api/short-market/shorts": detail() });
    page();
    await flush();
    await openOptions();
    expect(calls.filter((c) => c.url.includes("/ideas")).length).toBe(1);
    const card = screen.getByText("The crypto market's mood today is Fear: 31 out of 100").closest("article")!;
    expect(within(card).getByText(/\$3\.41 trillion/)).toBeTruthy();
    expect(within(card).getByRole("link", { name: "CoinGecko ↗" }).getAttribute("href")).toBe("https://www.coingecko.com");
    expect(within(screen.getByText("Already told").closest("article")!).getByRole("link", { name: "Made ✓" }).getAttribute("href")).toBe("/short-market/5");
    expect((screen.getByRole("radio", { name: "Surprise me" }) as HTMLElement).getAttribute("aria-checked")).toBe("true");
    fireEvent.click(screen.getByRole("radio", { name: "Claymation" }));
    fireEvent.change(screen.getByLabelText("Voice"), { target: { value: "bf_emma" } });
    fireEvent.change(screen.getByLabelText("Length"), { target: { value: "40" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Make this short" })); });
    await flush();
    expect(JSON.parse(calls.find((c) => c.method === "POST")?.body ?? "{}")).toEqual({ style: "clay", voice: "bf_emma", seconds: 40, quality: "standard", idea_id: 11, category: null });
    expect(JSON.parse(window.localStorage.getItem("pc-short-market-choices") ?? "{}")).toMatchObject({ style: "clay", seconds: 40 });
    fireEvent.click(screen.getByRole("tab", { name: "Today in money history" }));
    await flush();
    expect(calls.some((c) => c.url === "/api/short-market/ideas?category=history")).toBe(true);
  });

  it("says plainly when no worker is connected or it has no video model", async () => {
    serve({ "/api/short-market/overview": overview({ online: false, video_ai: null, voices: [] }), "/api/short-market/ideas": { items: [], refreshed_at: null },
            "/api/short-market/shorts": { items: [], total: 0 } });
    page();
    await flush();
    expect(screen.getByText("This PC is offline")).toBeTruthy();
    expect(screen.getByText("Your AI worker is offline.")).toBeTruthy();
    cleanup();
    serve({ "/api/short-market/overview": overview({ video_ai: null, voices: ["system"] }), "/api/short-market/ideas": { items: [], refreshed_at: null },
            "/api/short-market/shorts": { items: [], total: 0 } });
    page();
    await flush();
    expect(screen.getByText("No video model: still scenes")).toBeTruthy();
    expect(screen.getByText("Basic voice only")).toBeTruthy();
  });

  it("a finished short: download, caption to copy, the story with its sources, and making it again in another look", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    let current = detail();
    serve({ "/api/short-market/overview": overview(), "/api/short-market/shorts/5": () => current,
            "POST /api/short-market/shorts/5/remake": () => (current = detail({ status: "queued", generation: 2, style: "clay", style_label: "Claymation", has_video: false })) });
    page("/short-market/5");
    await flush();
    expect(screen.getByRole("heading", { level: 1, name: "Crypto's mood just flipped to fear" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Download MP4" }).getAttribute("href")).toBe("/api/short-market/shorts/5/media/video?download=1&g=1");
    expect(screen.getByText(/7 of 7 scenes animated/)).toBeTruthy();
    expect(screen.getByText(/Made by This PC in 14 min/)).toBeTruthy();
    expect(screen.getByText(/AI-generated content/)).toBeTruthy();
    expect(screen.getByText("a nervous robot watches a gauge swing left")).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Copy caption" })); });
    expect(writeText).toHaveBeenCalledWith("Crypto's mood just flipped to fear\n\nThe index reads 31. Not financial advice.\n\n#crypto");
    fireEvent.click(screen.getByRole("button", { name: "Make it again…" }));
    fireEvent.change(screen.getByLabelText("Look"), { target: { value: "clay" } });
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Make it again" })); });
    await flush();
    expect(JSON.parse(calls.find((c) => c.url.endsWith("/remake"))?.body ?? "{}")).toEqual({ style: "clay" });
    expect(screen.getByText("Waiting for the worker")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Download MP4" })).toBeNull();  // the old take is gone from the page until the new one is ready
  });
});
