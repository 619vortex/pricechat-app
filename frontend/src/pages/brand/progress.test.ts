import { describe, expect, it } from "vitest";
import { renderProgress } from "./Studio";

const at = "2026-10-20T12:00:00Z";
const s = (status: string, note = "") => ({ status, at, note }) as never;

describe("Content Studio progress bar", () => {
  it("shows 0% while queued and nothing once the video is finished", () => {
    expect(renderProgress({ status: "queued", stages: {} })).toEqual({ pct: 0, label: "Waiting for a worker" });
    expect(renderProgress({ status: "needs_review", stages: {} })).toBeNull();
    expect(renderProgress({ status: "failed", stages: {} })).toBeNull();
  });

  it("adds finished stages, half a running one, and the render's own percentage", () => {
    expect(renderProgress({ status: "rendering", stages: { visuals: s("running"), script: s("waiting") } })).toEqual({ pct: 18, label: "Finding real footage" });
    expect(renderProgress({ status: "rendering", stages: { visuals: s("done"), script: s("retrying", "a number isn't in the research") } }))
      .toEqual({ pct: 45, label: "Writing the script (retrying)" });
    const rendering = (note: string) => renderProgress({ status: "rendering", stages: { visuals: s("done"), script: s("done"), voice: s("done"), assemble: s("running", note) } });
    expect(rendering("12 shots · 4 graphics")?.pct).toBe(80);
    expect(rendering("render 0%")?.pct).toBe(68);
    expect(rendering("render 50%")?.pct).toBe(82);
    expect(rendering("render 100%")).toEqual({ pct: 95, label: "Editing & rendering" });
  });

  it("never reaches 100% before the video exists, and doesn't depend on stage order", () => {
    const all = { visuals: s("done"), script: s("done"), voice: s("done"), assemble: s("done"), subtitles: s("waiting") };
    expect(renderProgress({ status: "rendering", stages: all })).toEqual({ pct: 95, label: "Checking & uploading" });
    expect(renderProgress({ status: "rendering", stages: { script: s("done"), voice: s("done"), visuals: s("running") } })?.pct).toBe(48);
  });
});
