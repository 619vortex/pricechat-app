import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { VideoDetail } from "./api";
import { EditorPanel } from "./Editor";

/** TEST FIXTURES only: a rendered v4 video with its timeline and media library; the API calls are recorded, not sent. */
const asset = (id: string, kind: "video" | "image", match: "exact" | "related", over: Record<string, unknown> = {}) => ({
  id, kind, provider: "commons", url: `https://upload.wikimedia.org/${id}.${kind === "video" ? "webm" : "jpg"}`, page: `https://commons.wikimedia.org/wiki/File:${id}`,
  title: id, creator: "A. Photographer", rights_holder: "A. Photographer", license: "CC BY 4.0", license_url: "https://creativecommons.org/licenses/by/4.0/",
  attribution: "Video: A. Photographer · CC BY 4.0", width: 1920, height: 1080, duration: kind === "video" ? 12 : null, orientation: "landscape",
  shows: `${id} shows the real thing`, match, score: 0.9, note: "", authentic: true, status: "ok", ...over });
const video = {
  id: 7, topic: "Real story", status: "needs_review", generation: 2, has_video: true, has_thumb: true, script: { segments: [] }, brief: { numbers: { price_usd: 217.09 } },
  sources: [{ name: "Nasdaq" }], render_info: { authenticity: { score: 86, grade: "A", flags: ["1 identical shot(s) reused"], judge: "llm",
    parts: { relevance: 0.9, authentic: 1 }, weights: { relevance: 30, authentic: 18 }, metrics: { authentic: 1, exact: 0.7, video_share: 0.6, shots: 2, avg_shot: 3, distinct_assets: 2 } } },
  media: [asset("gpu", "video", "exact"), asset("hq", "image", "related"), asset("junk", "image", "related", { status: "failed", note: "watermark" })],
  timeline: { version: 1, style: "real_news", duration: 7.4, end_card: 1.4, overlays: [{ id: "o1", type: "price", start: 1, dur: 2, data: { label: "Close", value: "$217.09", note: "" } }],
              video: [{ id: "c1", asset: "gpu", start: 0, dur: 3, in: 1, fit: "frame", focus: [0.5, 0.5], zoom: 1, move: "none", gain: 0.1 },
                      { id: "c2", asset: "hq", start: 3, dur: 3, fit: "fill", focus: [0.5, 0.5], zoom: 1, move: "push", gain: 0 }],
              captions: { style: "clean", words: [{ t: "Nvidia", s: 0.2, e: 0.6, p: 0 }] }, narration: { mode: "ai", gain: 1 }, music: { track: null, gain: 0 } },
} as unknown as VideoDetail;

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Content Studio v4 editor", () => {
  it("edits a shot on the timeline and renders the edit", async () => {
    const calls: { name: string; fn: () => Promise<unknown> }[] = [];
    const sent: { method: string; url: string; body?: string }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
      sent.push({ method: init?.method ?? "GET", url: String(input), body: init?.body as string | undefined });
      return new Response(JSON.stringify(video), { status: 200 });
    }));
    render(<EditorPanel v={video} busy={false} act={(name, fn) => { calls.push({ name, fn }); }} />);
    expect(screen.getByText("86")).toBeTruthy();
    expect(screen.getByText("1 identical shot(s) reused")).toBeTruthy();
    // the library lists the usable assets with their licence; rejected ones only under "Everything checked"
    expect(screen.getAllByText("CC BY 4.0").length).toBe(2);
    fireEvent.click(screen.getByRole("button", { name: "Everything checked" }));
    expect(screen.getByText("watermark")).toBeTruthy();
    // select the second shot, frame it differently and move it first
    fireEvent.click(screen.getByTitle(/^2\. hq shows/));
    expect(screen.getByText("Shot 2 of 2")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Framing"), { target: { value: "frame" } });
    fireEvent.change(screen.getByLabelText("Duration (s)"), { target: { value: "2.5" } });
    fireEvent.click(screen.getByRole("button", { name: "← Earlier" }));
    expect(screen.getByText(/Unsaved edits/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Render this edit" }));
    expect(calls[0].name).toBe("tl-render");
    await act(async () => { await calls[0].fn(); });
    const put = sent.find((s) => s.method === "PUT")!;
    expect(put.url).toContain("/brand-manager/videos/7/timeline");
    const tl = JSON.parse(put.body!).timeline;
    expect(tl.video.map((c: { asset: string }) => c.asset)).toEqual(["hq", "gpu"]);
    expect(tl.video[0]).toMatchObject({ fit: "frame", dur: 2.5, start: 0 });
    expect(tl.video[1].start).toBe(2.5);
    expect(JSON.parse(sent.find((s) => s.url.endsWith("/rerender"))!.body!)).toEqual({ scope: "timeline" });
  });
});
