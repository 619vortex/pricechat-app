import { useEffect, useMemo, useRef, useState } from "react";
import { bm, type Asset, type Authenticity, type Clip, type Fit, type Move, type NewAsset, type Overlay, type OverlayType, type Timeline,
         type VideoDetail } from "./api";

/** Content Studio v4: the edit itself. A multi-track timeline (shots, graphics, captions, narration, music) over the last
 *  render, a shot / graphic inspector with real framing controls, the media library with each asset's rights record, and
 *  the visual authenticity score. Edits are saved as a timeline and rendered by the worker ("Render this edit"); the
 *  script, research and narration stay as approved. */

type Act = (name: string, fn: () => Promise<VideoDetail | void>) => void;
type Sel = { kind: "clip" | "overlay"; id: string } | null;

const MOVES: [Move, string][] = [["none", "Still"], ["push", "Slow push in"], ["pull", "Slow pull out"], ["pan_left", "Pan left"], ["pan_right", "Pan right"],
  ["pan_up", "Pan up"], ["pan_down", "Pan down"]];
const OVERLAY_LABEL: Record<OverlayType, string> = { title: "Headline", lower_third: "Lower third", price: "Price", chart: "Chart", facts: "Record card",
  source: "Source tag", text: "On-screen text" };
const MATCH_LABEL: Record<string, string> = { exact: "Exact subject", related: "Related", generic: "Generic filler", unfit: "Rejected", "": "Unchecked" };
const PART_LABEL: Record<string, string> = { relevance: "Shots match the words", authentic: "Real media", exact: "Exact subject", verified: "Rights verified",
  resolution: "Sharpness", repetition: "No repeats", readability: "Readability", consistency: "Pacing" };

const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x)) as T;
const fmt = (s: number) => `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, "0")}`;
const r3 = (n: number) => Math.round(n * 1000) / 1000;

/** Shots play back to back: the order and durations are the cut. */
function relayout(tl: Timeline): Timeline {
  let t = 0;
  for (const c of tl.video) { c.start = r3(t); t += c.dur; }
  tl.duration = r3(t + (tl.end_card ?? 1.4));
  for (const o of tl.overlays) { o.start = Math.min(o.start, Math.max(0, t - 0.5)); o.dur = Math.min(o.dur, Math.max(0.5, t - o.start)); }
  return tl;
}

export function EditorPanel({ v, busy, act }: { v: VideoDetail; busy: boolean; act: Act }) {
  const [draft, setDraft] = useState<Timeline | null>(v.timeline ? clone(v.timeline) : null);
  const [sel, setSel] = useState<Sel>(null);
  const [dirty, setDirty] = useState(false);
  const [time, setTime] = useState(0);
  const [assets, setAssets] = useState<Asset[]>(v.media ?? []);
  const player = useRef<HTMLVideoElement>(null);
  useEffect(() => { setDraft(v.timeline ? clone(v.timeline) : null); setDirty(false); setSel(null); }, [v.id, v.generation]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setAssets(v.media ?? []); }, [v.media]);
  const lib = useMemo(() => Object.fromEntries(assets.map((a) => [a.id, a])), [assets]);
  const auth = (v.render_info?.authenticity as Authenticity | undefined) ?? null;
  const locked = ["publishing", "sent_to_drafts", "published"].includes(v.status);

  const change = (fn: (tl: Timeline) => void) => {
    if (!draft) return;
    const next = clone(draft);
    fn(next);
    setDraft(relayout(next));
    setDirty(true);
  };
  const seek = (t: number) => { setTime(t); if (player.current) player.current.currentTime = t; };
  const addShot = (a: Asset) => change((tl) => {
    const at = sel?.kind === "clip" ? tl.video.findIndex((c) => c.id === sel.id) + 1 : tl.video.length;
    const id = `c${Date.now().toString(36)}`;
    const dur = a.kind === "video" ? Math.min(3, Math.max(1, (a.duration ?? 3) - 0.2)) : 3;
    const portrait = a.height > a.width;
    tl.video.splice(at, 0, { id, asset: a.id, start: 0, dur, in: 0, fit: portrait ? "fill" : "frame", focus: [0.5, 0.5], zoom: 1, move: a.kind === "video" ? "none" : "push", gain: 0 });
    setSel({ kind: "clip", id });
  });
  const save = () => draft && act("tl-save", () => bm.saveTimeline(v.id, draft));
  const renderEdit = () => draft && act("tl-render", async () => { await bm.saveTimeline(v.id, draft); return bm.rerender(v.id, "timeline"); });

  return (
    <div className="bmx-editor">
      <h2>Editor</h2>
      {auth && <AuthenticityCard a={auth} />}
      {!draft ? (
        <p className="bmx-fine">This video was made before the editorial renderer: re-render it with the Editorial engine (Creative › Renderer) to get an editable timeline.</p>
      ) : (
        <>
          <div className="bmx-ed-top">
            {v.has_video && (
              <video ref={player} className="bmx-ed-preview" src={bm.media(v.id, "video", v.generation)} controls playsInline
                     onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)} />
            )}
            <div className="bmx-ed-inspector">
              {sel?.kind === "clip" && draft.video.find((c) => c.id === sel.id) ? (
                <ClipInspector clip={draft.video.find((c) => c.id === sel.id)!} index={draft.video.findIndex((c) => c.id === sel.id)} count={draft.video.length}
                               asset={lib[draft.video.find((c) => c.id === sel.id)!.asset]} assets={assets} time={time}
                               onChange={(p) => change((tl) => { Object.assign(tl.video.find((c) => c.id === sel.id)!, p); })}
                               onMove={(d) => change((tl) => { const i = tl.video.findIndex((c) => c.id === sel.id); const [c] = tl.video.splice(i, 1); tl.video.splice(Math.max(0, Math.min(tl.video.length, i + d)), 0, c); })}
                               onSplit={() => change((tl) => {
                                 const i = tl.video.findIndex((c) => c.id === sel.id);
                                 const c = tl.video[i];
                                 const cut = time - c.start;
                                 if (cut < 0.5 || c.dur - cut < 0.5) return;
                                 const b: Clip = { ...clone(c), id: `c${Date.now().toString(36)}`, dur: r3(c.dur - cut), in: r3((c.in ?? 0) + cut) };
                                 c.dur = r3(cut);
                                 tl.video.splice(i + 1, 0, b);
                               })}
                               onDelete={() => { change((tl) => { if (tl.video.length > 1) tl.video = tl.video.filter((c) => c.id !== sel.id); }); setSel(null); }} />
              ) : sel?.kind === "overlay" && draft.overlays.find((o) => o.id === sel.id) ? (
                <OverlayInspector o={draft.overlays.find((o) => o.id === sel.id)!} max={draft.duration - (draft.end_card ?? 1.4)}
                                  onChange={(p) => change((tl) => { Object.assign(tl.overlays.find((o) => o.id === sel.id)!, p); })}
                                  onDelete={() => { change((tl) => { tl.overlays = tl.overlays.filter((o) => o.id !== sel.id); }); setSel(null); }} />
              ) : (
                <MixInspector tl={draft} onChange={(fn) => change(fn)} v={v} time={time}
                              onAddOverlay={(o) => { change((tl) => { tl.overlays.push(o); }); setSel({ kind: "overlay", id: o.id }); }} />
              )}
            </div>
          </div>
          <Tracks tl={draft} lib={lib} sel={sel} time={time} onSelect={setSel} onSeek={seek} />
          <div className="bmx-actions">
            <button className="btn btn-primary" disabled={busy || locked} onClick={renderEdit}>Render this edit</button>
            <button className="btn" disabled={busy || locked || !dirty} onClick={save}>Save timeline</button>
            <button className="btn btn-ghost" disabled={busy || !dirty} onClick={() => { setDraft(v.timeline ? clone(v.timeline) : null); setDirty(false); }}>Undo all changes</button>
            {dirty && <span className="bmx-fine">Unsaved edits · the player shows the last render</span>}
          </div>
          <p className="bmx-fine">Shots play back to back in the order shown. The narration and captions stay as recorded; graphics can't show a number that isn't in the research.</p>
        </>
      )}
      <MediaLibrary v={v} assets={assets} setAssets={setAssets} busy={busy} onUse={draft && !locked ? addShot : undefined}
                    used={new Set((draft?.video ?? []).map((c) => c.asset))} />
    </div>
  );
}

// --- the tracks ----------------------------------------------------------------------------------------------------------
function Tracks({ tl, lib, sel, time, onSelect, onSeek }: { tl: Timeline; lib: Record<string, Asset>; sel: Sel; time: number; onSelect: (s: Sel) => void;
                                                            onSeek: (t: number) => void }) {
  const D = Math.max(1, tl.duration);
  const pct = (t: number) => `${(t / D) * 100}%`;
  const phrases = useMemo(() => {
    const out: { s: number; e: number; t: string; p: number }[] = [];
    for (const w of tl.captions.words ?? []) {
      const last = out[out.length - 1];
      if (last && last.p === w.p) { last.e = w.e; last.t += ` ${w.t}`; } else out.push({ s: w.s, e: w.e, t: w.t, p: w.p });
    }
    return out;
  }, [tl.captions.words]);
  const speech = phrases.length ? [phrases[0].s, phrases[phrases.length - 1].e] : null;
  const ruler = Array.from({ length: Math.floor(D / 5) + 1 }, (_, i) => i * 5);
  const click = (e: React.MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    onSeek(Math.max(0, Math.min(D, ((e.clientX - r.left) / r.width) * D)));
  };
  return (
    <div className="bmx-tracks" role="group" aria-label="Timeline">
      <div className="bmx-track-label" /><div className="bmx-ruler" onClick={click}>{ruler.map((t) => <span key={t} style={{ left: pct(t) }}>{fmt(t)}</span>)}</div>
      <div className="bmx-track-label">Video</div>
      <div className="bmx-lane" onClick={click}>
        {tl.video.map((c, i) => {
          const a = lib[c.asset];
          return (
            <button key={c.id} className={`bmx-clip is-${a?.match || "unknown"}${sel?.id === c.id ? " is-sel" : ""}`} style={{ left: pct(c.start), width: pct(c.dur) }}
                    title={`${i + 1}. ${a?.shows || a?.title || c.asset} · ${c.dur.toFixed(1)}s`} onClick={(e) => { e.stopPropagation(); onSelect({ kind: "clip", id: c.id }); }}>
              {a?.thumb || (a?.kind === "image" && a.url) ? <img src={a.thumb || a.url} alt="" loading="lazy" /> : null}
              <span>{i + 1}{a?.kind === "video" ? " ▶" : ""}</span>
            </button>
          );
        })}
      </div>
      <div className="bmx-track-label">Graphics</div>
      <div className="bmx-lane is-thin" onClick={click}>
        {tl.overlays.map((o) => (
          <button key={o.id} className={`bmx-ov${sel?.id === o.id ? " is-sel" : ""}`} style={{ left: pct(o.start), width: pct(o.dur) }}
                  onClick={(e) => { e.stopPropagation(); onSelect({ kind: "overlay", id: o.id }); }}>{OVERLAY_LABEL[o.type]}</button>
        ))}
      </div>
      <div className="bmx-track-label">Captions</div>
      <div className="bmx-lane is-thin" onClick={click}>
        {tl.captions.style !== "none" && phrases.map((p, i) => <span key={i} className="bmx-cap" style={{ left: pct(p.s), width: pct(Math.max(0.2, p.e - p.s)) }} title={p.t}>{p.t}</span>)}
      </div>
      <div className="bmx-track-label">Narration</div>
      <div className="bmx-lane is-thin" onClick={click}>
        {speech && tl.narration.mode !== "none" ? <span className="bmx-audio" style={{ left: pct(speech[0]), width: pct(speech[1] - speech[0]) }}>
          {tl.narration.mode === "upload" ? "Your narration" : `AI narrator${tl.narration.narrator ? ` · ${tl.narration.narrator}` : ""}`} · {Math.round(tl.narration.gain * 100)}%</span> : null}
      </div>
      <div className="bmx-track-label">Music</div>
      <div className="bmx-lane is-thin" onClick={click}>
        {tl.music.track ? <span className="bmx-audio is-music" style={{ left: 0, width: "100%" }}>{tl.music.track} · {Math.round(tl.music.gain * 100)}%</span> : null}
      </div>
      <div className="bmx-playhead" style={{ left: `calc(84px + (100% - 84px) * ${time / D})` }} />
    </div>
  );
}

// --- inspectors ----------------------------------------------------------------------------------------------------------
/** A 9:16 thumbnail of how the shot is framed: the same crop maths as the renderer (no motion). */
function FramePreview({ clip, asset, frameTop = 330, frameH = 900 }: { clip: Clip; asset?: Asset; frameTop?: number; frameH?: number }) {
  const W = 108, H = 192, k = W / 1080;
  if (!asset) return <div className="bmx-frame" style={{ width: W, height: H }} />;
  const src = asset.thumb || asset.url;
  const boxW = W, boxH = clip.fit === "fill" ? H : frameH * k;
  const aw = Math.max(1, asset.width), ah = Math.max(1, asset.height);
  const s = (clip.fit === "fill" ? Math.max(boxW / aw, boxH / ah) : Math.min(boxW / aw, boxH / ah)) * Math.max(1, clip.zoom);
  const w = aw * s, h = ah * s;
  const x = w <= boxW ? (boxW - w) / 2 : -(w - boxW) * clip.focus[0];
  const y = h <= boxH ? (boxH - h) / 2 : -(h - boxH) * clip.focus[1];
  return (
    <div className="bmx-frame" style={{ width: W, height: H }}>
      <div style={{ position: "absolute", left: 0, top: clip.fit === "fill" ? 0 : frameTop * k, width: boxW, height: boxH, overflow: "hidden" }}>
        {asset.kind === "video" && !asset.thumb ? <video src={src} muted style={{ position: "absolute", left: x, top: y, width: w, height: h }} />
          : <img src={src} alt="" style={{ position: "absolute", left: x, top: y, width: w, height: h, maxWidth: "none" }} />}
      </div>
    </div>
  );
}

function ClipInspector({ clip, index, count, asset, assets, time, onChange, onMove, onSplit, onDelete }: {
  clip: Clip; index: number; count: number; asset?: Asset; assets: Asset[]; time: number; onChange: (p: Partial<Clip>) => void; onMove: (d: number) => void;
  onSplit: () => void; onDelete: () => void }) {
  const maxIn = asset?.kind === "video" && asset.duration ? Math.max(0, asset.duration - clip.dur) : 0;
  const usable = assets.filter((a) => a.status === "ok");
  const inside = time > clip.start + 0.5 && time < clip.start + clip.dur - 0.5;
  return (
    <div className="bmx-insp">
      <h3>Shot {index + 1} of {count}</h3>
      <div className="bmx-insp-row">
        <FramePreview clip={clip} asset={asset} />
        <div className="bmx-insp-meta">
          <p><b>{asset?.shows || asset?.title || clip.asset}</b></p>
          <p className="bmx-fine">{asset ? `${MATCH_LABEL[asset.match] ?? asset.match} · ${asset.kind} · ${asset.width}×${asset.height}${asset.duration ? ` · ${asset.duration.toFixed(1)}s` : ""}` : "missing asset"}</p>
          {asset && <p className="bmx-fine">{asset.license}{asset.attribution ? ` · ${asset.attribution}` : ""}</p>}
        </div>
      </div>
      <label>Replace with
        <select value={clip.asset} onChange={(e) => onChange({ asset: e.target.value, in: 0 })}>
          {usable.map((a) => <option key={a.id} value={a.id}>{(a.kind === "video" ? "▶ " : "") + (a.shows || a.title).slice(0, 60)} ({MATCH_LABEL[a.match] ?? a.match})</option>)}
        </select>
      </label>
      <div className="bmx-cgrid">
        <label>Duration (s)<input type="number" min={0.4} max={30} step={0.1} value={clip.dur} onChange={(e) => onChange({ dur: Math.max(0.4, Number(e.target.value) || 0.4) })} /></label>
        {asset?.kind === "video" && <label>Start in clip (s)<input type="number" min={0} max={maxIn} step={0.1} value={clip.in ?? 0} onChange={(e) => onChange({ in: Math.min(maxIn, Math.max(0, Number(e.target.value) || 0)) })} /></label>}
        <label>Framing<select value={clip.fit} onChange={(e) => onChange({ fit: e.target.value as Fit })}><option value="fill">Full screen (crop)</option><option value="frame">Framed (whole picture)</option></select></label>
        <label>Camera move<select value={clip.move} onChange={(e) => onChange({ move: e.target.value as Move })}>{MOVES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        <label>Zoom {clip.zoom.toFixed(2)}×<input type="range" min={1} max={3} step={0.05} value={clip.zoom} onChange={(e) => onChange({ zoom: Number(e.target.value) })} /></label>
        <label>Focus left/right<input type="range" min={0} max={1} step={0.01} value={clip.focus[0]} onChange={(e) => onChange({ focus: [Number(e.target.value), clip.focus[1]] })} /></label>
        <label>Focus up/down<input type="range" min={0} max={1} step={0.01} value={clip.focus[1]} onChange={(e) => onChange({ focus: [clip.focus[0], Number(e.target.value)] })} /></label>
        {asset?.kind === "video" && <label>Clip sound {Math.round(clip.gain * 100)}%<input type="range" min={0} max={1} step={0.05} value={clip.gain} disabled={asset.audio === false} onChange={(e) => onChange({ gain: Number(e.target.value) })} /></label>}
      </div>
      <div className="bmx-actions">
        <button className="btn" disabled={index === 0} onClick={() => onMove(-1)}>← Earlier</button>
        <button className="btn" disabled={index === count - 1} onClick={() => onMove(1)}>Later →</button>
        <button className="btn" disabled={!inside} title="Split this shot at the playhead" onClick={onSplit}>Split at {fmt(time)}</button>
        <button className="btn btn-ghost" disabled={count <= 1} onClick={onDelete}>Remove shot</button>
      </div>
    </div>
  );
}

const TEXT_FIELDS: Record<OverlayType, [string, string][]> = {
  title: [["kicker", "Kicker"], ["text", "Headline"]], lower_third: [["name", "Name"], ["detail", "Detail"]], price: [["label", "Label"], ["value", "Value"], ["note", "Note"]],
  chart: [["title", "Title"]], facts: [["title", "Title"], ["footer", "Footer"]], source: [["text", "Text"]], text: [["text", "Text"]],
};

function OverlayInspector({ o, max, onChange, onDelete }: { o: Overlay; max: number; onChange: (p: Partial<Overlay>) => void; onDelete: () => void }) {
  const rows = (o.data.rows as { k: string; v: string }[] | undefined) ?? [];
  return (
    <div className="bmx-insp">
      <h3>{OVERLAY_LABEL[o.type]}</h3>
      <div className="bmx-cgrid">
        <label>Starts at (s)<input type="number" min={0} max={max} step={0.1} value={o.start} onChange={(e) => onChange({ start: Math.max(0, Number(e.target.value) || 0) })} /></label>
        <label>On screen for (s)<input type="number" min={0.5} max={30} step={0.1} value={o.dur} onChange={(e) => onChange({ dur: Math.max(0.5, Number(e.target.value) || 0.5) })} /></label>
        {TEXT_FIELDS[o.type].map(([k, label]) => (
          <label key={k}>{label}<input value={String(o.data[k] ?? "")} maxLength={k === "text" ? 160 : 80} onChange={(e) => onChange({ data: { ...o.data, [k]: e.target.value } })} /></label>
        ))}
      </div>
      {o.type === "facts" && rows.map((r, i) => (
        <div key={i} className="bmx-cgrid">
          <label>Row {i + 1}<input value={r.k} maxLength={30} onChange={(e) => onChange({ data: { ...o.data, rows: rows.map((x, j) => (j === i ? { ...x, k: e.target.value } : x)) } })} /></label>
          <label>Value<input value={r.v} maxLength={30} onChange={(e) => onChange({ data: { ...o.data, rows: rows.map((x, j) => (j === i ? { ...x, v: e.target.value } : x)) } })} /></label>
        </div>
      ))}
      {o.type === "chart" && <p className="bmx-fine">The chart always draws the research's own data; only its title and timing can change.</p>}
      <div className="bmx-actions"><button className="btn btn-ghost" onClick={onDelete}>Remove graphic</button></div>
    </div>
  );
}

function MixInspector({ tl, v, time, onChange, onAddOverlay }: { tl: Timeline; v: VideoDetail; time: number; onChange: (fn: (tl: Timeline) => void) => void;
                                                                 onAddOverlay: (o: Overlay) => void }) {
  const nums = Object.entries(v.brief.numbers ?? {}).filter(([k]) => k !== "year");
  const base = (type: OverlayType, data: Record<string, unknown>): Overlay => ({ id: `o${Date.now().toString(36)}`, type, start: r3(time), dur: 3, data });
  const hasChart = Boolean((v.brief as { chart?: { points?: unknown[] } }).chart?.points?.length);
  return (
    <div className="bmx-insp">
      <h3>Sound & captions</h3>
      <div className="bmx-cgrid">
        <label>Captions<select value={tl.captions.style} onChange={(e) => onChange((t) => { t.captions.style = e.target.value as Timeline["captions"]["style"]; })}>
          <option value="clean">Clean</option><option value="boxed">Boxed (news)</option><option value="none">None</option></select></label>
        <label>Narration {Math.round(tl.narration.gain * 100)}%<input type="range" min={0} max={1.5} step={0.05} value={tl.narration.gain} onChange={(e) => onChange((t) => { t.narration.gain = Number(e.target.value); })} /></label>
        <label>Music<select value={tl.music.track ?? ""} onChange={(e) => onChange((t) => { t.music.track = e.target.value || null; if (e.target.value && !t.music.gain) t.music.gain = 0.12; })}>
          <option value="">None</option><option value="documentary">Calm documentary pad</option><option value="market">Minimal market pulse</option>
          <option value="playful">Playful plucks</option><option value="viral">Driving beat</option></select></label>
        <label>Music volume {Math.round(tl.music.gain * 100)}%<input type="range" min={0} max={0.6} step={0.01} value={tl.music.gain} onChange={(e) => onChange((t) => { t.music.gain = Number(e.target.value); })} /></label>
      </div>
      <h3>Add a graphic at {fmt(time)}</h3>
      <div className="bmx-actions">
        <button className="btn" onClick={() => onAddOverlay(base("lower_third", { name: "", detail: "" }))}>Lower third</button>
        <button className="btn" onClick={() => onAddOverlay(base("text", { text: "" }))}>On-screen text</button>
        <button className="btn" onClick={() => onAddOverlay(base("source", { text: `Source: ${v.sources.map((s) => s.name).join(", ")}`.slice(0, 80) }))}>Source tag</button>
        {nums.length > 0 && <button className="btn" onClick={() => onAddOverlay(base("price", { label: nums[0][0].replace(/_/g, " "), value: String(nums[0][1]), note: "" }))}>Number</button>}
        {hasChart && <button className="btn" onClick={() => onAddOverlay({ ...base("chart", { title: "" }), dur: 5 })}>Chart</button>}
      </div>
      <p className="bmx-fine">Select a shot or a graphic on the timeline to edit it. Numbers must match the research exactly.</p>
    </div>
  );
}

// --- the media library -----------------------------------------------------------------------------------------------------
function MediaLibrary({ v, assets, setAssets, busy, onUse, used }: { v: VideoDetail; assets: Asset[]; setAssets: (a: Asset[]) => void; busy: boolean;
                                                                       onUse?: (a: Asset) => void; used: Set<string> }) {
  const [show, setShow] = useState<"usable" | "all">("usable");
  const [adding, setAdding] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const list = assets.filter((a) => show === "all" || a.status === "ok");
  const toggle = async (a: Asset) => {
    setErr(null);
    try { setAssets((await bm.setAsset(v.id, a.id, { status: a.status === "excluded" ? "ok" : "excluded" })).assets); }
    catch (e) { setErr(e instanceof Error ? e.message : "Couldn't change it."); }
  };
  return (
    <div className="bmx-library">
      <div className="bmx-lib-head">
        <h3>Media library <small className="muted">· {assets.filter((a) => a.status === "ok").length} usable of {assets.length} looked at</small></h3>
        <div className="bmx-chips">
          <button className={show === "usable" ? "is-on" : ""} onClick={() => setShow("usable")}>Usable</button>
          <button className={show === "all" ? "is-on" : ""} onClick={() => setShow("all")}>Everything checked</button>
          <button onClick={() => setAdding(!adding)}>＋ Add media</button>
        </div>
      </div>
      {err && <p className="bmx-alert">{err}</p>}
      {adding && <AddAsset v={v} busy={busy} onDone={(a) => { setAssets(a); setAdding(false); }} />}
      {assets.length === 0 ? <p className="bmx-fine">No media yet: the worker researches real footage and photos for the story before it writes the script.</p> : (
        <div className="bmx-lib-grid">
          {list.map((a) => (
            <article key={a.id} className={`bmx-asset is-${a.status}`}>
              <div className="bmx-asset-thumb">
                {a.thumb || a.kind === "image" ? <img src={a.thumb || a.url} alt={a.shows || a.title} loading="lazy" /> : <span>▶ video</span>}
                <em className={`bmx-match is-${a.match || "none"}`}>{MATCH_LABEL[a.match] ?? a.match}</em>
                {used.has(a.id) && <em className="bmx-used">In the edit</em>}
              </div>
              <p className="bmx-asset-title">{a.kind === "video" ? "▶ " : ""}{a.shows || a.title}</p>
              <p className="bmx-fine">{a.width && a.height ? `${a.width}×${a.height}` : "size ?"}{a.duration ? ` · ${a.duration.toFixed(1)}s` : ""}{a.orientation ? ` · ${a.orientation}` : ""} · {a.provider}</p>
              <p className="bmx-fine"><b>{a.license || "no licence"}</b>{a.creator ? ` · ${a.creator}` : ""}{a.permitted?.editorial_only ? " · editorial use only" : ""}</p>
              {a.attribution && <p className="bmx-fine">Credit shown: {a.attribution}</p>}
              {a.note && <p className="bmx-fine">{a.note}</p>}
              <div className="bmx-asset-actions">
                {a.page && <a href={a.page} target="_blank" rel="noreferrer">Source ↗</a>}
                {a.license_url && <a href={a.license_url} target="_blank" rel="noreferrer">Licence ↗</a>}
                {onUse && a.status === "ok" && <button className="btn btn-sm" onClick={() => onUse(a)}>Add to timeline</button>}
                {(a.status === "ok" || a.status === "excluded") && <button className="btn btn-sm btn-ghost" disabled={busy} onClick={() => void toggle(a)}>{a.status === "excluded" ? "Include" : "Exclude"}</button>}
              </div>
            </article>
          ))}
        </div>
      )}
      <p className="bmx-fine">Only media with a licence we could read is used (CC0, CC BY, CC BY-SA, public domain, licensed stock, or media you add and confirm you hold the rights to). Credits the licence asks for are shown on screen.</p>
    </div>
  );
}

function AddAsset({ v, busy, onDone }: { v: VideoDetail; busy: boolean; onDone: (a: Asset[]) => void }) {
  const [a, setA] = useState<NewAsset>({ url: "", license: "", attest: false, match: "exact" });
  const [err, setErr] = useState<string | null>(null);
  const set = (p: Partial<NewAsset>) => setA({ ...a, ...p });
  const submit = async () => {
    setErr(null);
    try { onDone((await bm.addAsset(v.id, a)).assets); } catch (e) { setErr(e instanceof Error ? e.message : "Couldn't add it."); }
  };
  return (
    <div className="bmx-form">
      <div className="bmx-cgrid">
        <label>Direct link to the file (https)<input value={a.url} onChange={(e) => set({ url: e.target.value })} placeholder="https://…/clip.mp4" /></label>
        <label>Type<select value={a.kind ?? ""} onChange={(e) => set({ kind: (e.target.value || undefined) as NewAsset["kind"] })}><option value="">Detect</option><option value="video">Video</option><option value="image">Photo</option></select></label>
        <label>Licence<input value={a.license} onChange={(e) => set({ license: e.target.value })} placeholder="CC BY 4.0 · Licensed · Owned · Editorial use" /></label>
        <label>Creator / rights holder<input value={a.creator ?? ""} onChange={(e) => set({ creator: e.target.value })} /></label>
        <label>Source page<input value={a.page ?? ""} onChange={(e) => set({ page: e.target.value })} placeholder="where the licence is stated" /></label>
        <label>Credit to show<input value={a.attribution ?? ""} onChange={(e) => set({ attribution: e.target.value })} placeholder="Video: Name / Source · CC BY 4.0" /></label>
        <label>What it shows<input value={a.shows ?? ""} onChange={(e) => set({ shows: e.target.value })} /></label>
        <label>Relevance<select value={a.match} onChange={(e) => set({ match: e.target.value as NewAsset["match"] })}><option value="exact">The exact subject</option><option value="related">Related</option><option value="generic">Generic</option></select></label>
      </div>
      <label className="bmx-switch"><input type="checkbox" checked={a.attest} onChange={(e) => set({ attest: e.target.checked })} /> I hold the rights to use this media in Price Chat's videos.</label>
      {err && <p className="bmx-alert">{err}</p>}
      <div className="bmx-actions"><button className="btn btn-primary" disabled={busy || !a.url || !a.license || !a.attest} onClick={() => void submit()}>Add to library</button></div>
    </div>
  );
}

function AuthenticityCard({ a }: { a: Authenticity }) {
  const m = a.metrics;
  return (
    <div className={`bmx-auth is-${a.grade}`}>
      <div className="bmx-auth-score"><b>{a.score}</b><span>/100</span><em>{a.grade}</em></div>
      <div className="bmx-auth-body">
        <p><b>Visual authenticity</b> <small className="muted">· judged by {a.judge}</small></p>
        <p className="bmx-fine">{Math.round((m.authentic ?? 0) * 100)}% real media · {Math.round((m.exact ?? 0) * 100)}% the exact subject · {Math.round((m.video_share ?? 0) * 100)}% video ·
          {" "}{m.shots} shots, avg {m.avg_shot}s · {m.distinct_assets} different assets</p>
        <div className="bmx-auth-parts">
          {Object.entries(a.parts).map(([k, val]) => (
            <span key={k} title={`${PART_LABEL[k] ?? k}: weight ${a.weights[k]}`}><i style={{ width: `${Math.round(val * 100)}%` }} />{PART_LABEL[k] ?? k}</span>
          ))}
        </div>
        {a.flags.length > 0 && <ul className="bmx-auth-flags">{a.flags.map((f) => <li key={f}>{f}</li>)}</ul>}
      </div>
    </div>
  );
}
