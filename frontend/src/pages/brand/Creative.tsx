import { useEffect, useState } from "react";
import { useApi } from "../../api/useApi";
import { bm, type Creative, type QcCheck, type StoryScene, type VideoDetail } from "./api";

const FIELDS: [keyof Creative, string][] = [["preset", "Preset"], ["style", "Visual style"], ["narrator", "Narrator"], ["tone", "Script tone"],
  ["intensity", "Animation"], ["music", "Music"], ["captions", "Captions"], ["engine", "Renderer"]];
// v4 editorial engine: the production style decides pacing, typography, music and graphics; the editor below does the rest
const EDITORIAL_FIELDS: [keyof Creative, string][] = [["engine", "Renderer"], ["production", "Production style"], ["narration", "Narration"], ["narrator", "AI narrator"],
  ["writer", "Hook writer"]];
const V4_NARRATORS = ["auto", "natural", "steady", "documentary"];
const ICON: Record<QcCheck["status"], string> = { pass: "✓", warn: "!", fail: "✕", skipped: "–" };

/** Content Studio v2: how this video looks and sounds, scene by scene, and the creative quality check of the last render.
 *  Every regenerate here keeps the approved script and research; only "New script" (Regenerate) writes a new one. */
export function CreativePanel({ v, busy, act }: { v: VideoDetail; busy: boolean; act: (name: string, fn: () => Promise<VideoDetail | void>) => void }) {
  const opts = useApi(bm.creativeOptions);
  const [c, setC] = useState<Creative>(v.creative ?? {});
  const [scenes, setScenes] = useState<Record<string, { screen?: string; visual?: string }>>(v.creative?.scenes ?? {});
  useEffect(() => { setC(v.creative ?? {}); setScenes(v.creative?.scenes ?? {}); }, [v.id, v.generation]); // eslint-disable-line react-hooks/exhaustive-deps
  const o = opts.data?.options;
  const engine = String(c.engine ?? opts.data?.defaults.engine ?? "editorial");
  const fields = engine === "editorial" ? EDITORIAL_FIELDS : FIELDS;
  const info = v.render_info ?? {};
  const checks = (info.checks as QcCheck[] | undefined) ?? [];
  const board = (info.storyboard as StoryScene[] | undefined) ?? [];
  const timings = (info.timings as Record<string, number> | undefined) ?? {};
  const segs = v.script?.segments ?? [];
  const canRender = segs.length > 0 && !["publishing", "sent_to_drafts", "published"].includes(v.status);
  const choice = (): Creative => {
    const out: Creative = {};
    for (const [k] of [...FIELDS, ...EDITORIAL_FIELDS]) if (c[k] !== undefined) (out as Record<string, unknown>)[k] = c[k];
    if (c.narration_url !== undefined) out.narration_url = c.narration_url || null;
    if (c.seconds !== undefined) out.seconds = c.seconds || null;
    return out;
  };
  const go = (scope: "video" | "scene" | "voice" | "captions", withScenes = false) =>
    act(`rr-${scope}`, () => bm.rerender(v.id, scope, { ...choice(), ...(withScenes ? { scenes } : {}) }));
  return (
    <div className="bmx-creative">
      <h2>Creative</h2>
      {!o ? <p className="muted">Loading options…</p> : (
        <div className="bmx-cgrid">
          {fields.map(([k, label]) => (
            <label key={k}>{label}
              <select value={String(c[k] ?? (k === "preset" ? "" : k === "engine" ? opts.data?.defaults.engine ?? "editorial" : k === "narration" ? "ai" : k === "writer" ? "old" : "auto"))}
                      onChange={(e) => setC({ ...c, [k]: e.target.value })}>
                {Object.entries(o[k] ?? {}).filter(([val]) => engine !== "editorial" || k !== "narrator" || V4_NARRATORS.includes(val))
                  .map(([val, l]) => <option key={val} value={val}>{l}</option>)}
              </select>
            </label>
          ))}
          {engine === "editorial" && c.narration === "upload" && (
            <label>Your narration (https link to MP3/WAV)
              <input value={c.narration_url ?? ""} maxLength={500} placeholder="https://…/narration.mp3" onChange={(e) => setC({ ...c, narration_url: e.target.value })} />
            </label>
          )}
          <label>Duration
            <select value={String(c.seconds ?? "")} onChange={(e) => setC({ ...c, seconds: e.target.value ? Number(e.target.value) : null })}>
              <option value="">Brand default</option>{[20, 25, 30, 35, 40, 45, 60].map((s) => <option key={s} value={s}>{s} seconds</option>)}
            </select>
          </label>
        </div>
      )}
      <div className="bmx-actions">
        <button className="btn btn-primary" disabled={busy || !canRender} onClick={() => go("video", true)}>Final render</button>
        <button className="btn" disabled={busy || !canRender} onClick={() => go("voice")}>Regenerate voice</button>
        <button className="btn" disabled={busy || !canRender} onClick={() => go("captions")}>Regenerate captions</button>
        <button className="btn btn-ghost" disabled={busy} onClick={() => act("save-creative", () => bm.saveCreative(v.id, { ...choice(), scenes }))}>Save without rendering</button>
      </div>
      <p className="bmx-fine">These keep the approved script and research; the worker reuses the voice when the words and narrator are unchanged.
        The video above is the preview: approve or send it only when you like it.</p>

      {segs.length > 0 && engine !== "editorial" && (
        <>
          <h3>Scenes</h3>
          <ol className="bmx-scenes">
            {segs.map((g, i) => {
              const ed = scenes[String(i)] ?? {};
              const shot = board.filter((b) => b.kind !== "outro")[i];
              return (
                <li key={i}>
                  <p className="bmx-scene-text">{g.beat ? <em>{g.beat}</em> : null} {g.text}{shot ? <small className="muted"> · {shot.start.toFixed(1)}–{shot.end.toFixed(1)}s · {shot.transition}</small> : null}</p>
                  <div className="bmx-scene-edit">
                    <select aria-label={`Scene ${i + 1} visual`} value={ed.visual ?? g.visual ?? g.scene}
                            onChange={(e) => setScenes({ ...scenes, [i]: { ...ed, visual: e.target.value } })}>
                      {Object.entries(o?.visual ?? {}).map(([val, l]) => <option key={val} value={val}>{l}</option>)}
                    </select>
                    <input aria-label={`Scene ${i + 1} on-screen words`} maxLength={80} placeholder="On-screen words" value={ed.screen ?? g.screen ?? ""}
                           onChange={(e) => setScenes({ ...scenes, [i]: { ...ed, screen: e.target.value } })} />
                    <button className="btn" disabled={busy || !canRender} onClick={() => go("scene", true)}>Regenerate scene</button>
                  </div>
                </li>
              );
            })}
          </ol>
          <p className="bmx-fine">Visuals only show what the research supports (no card image = no card scene). Numbers in on-screen words must come from the research.</p>
        </>
      )}

      {checks.length > 0 && (
        <>
          <h3>Creative quality check</h3>
          <ul className="bmx-checks">{checks.map((k) => <li key={k.id} className={`is-${k.status}`}><b>{ICON[k.status]}</b> {k.label} <small className="muted">· {k.detail}</small></li>)}</ul>
          <p className="bmx-fine">
            {info.renderer ? `Renderer ${String(info.renderer)}` : ""}{timings.render ? ` · render ${timings.render.toFixed(0)}s` : ""}
            {info.seconds ? ` · total ${String(info.seconds)}s` : ""}{info.cost_usd != null ? ` · cost $${Number(info.cost_usd).toFixed(2)}` : ""}
            {info.narration_match != null ? ` · narration ${(Number(info.narration_match) * 100).toFixed(0)}% matched to the script` : ""}
          </p>
        </>
      )}
    </div>
  );
}
