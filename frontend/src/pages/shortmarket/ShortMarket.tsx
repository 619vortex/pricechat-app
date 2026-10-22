import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Loading, relativeTime } from "../../components/ui";
import { AUTO, loadChoices, saveChoices, sm, STATUS, type Choices, type Idea, type Overview, type Short, type ShortDetail } from "./api";
import "./short.css";

/** SHORT MARKET: finds a real money story, writes it, voices it and animates it in 3D on the owner's own machine.
 * No sign-in to any network and nothing is posted: the finished video is downloaded and uploaded by hand. */
export function ShortMarketPage() {
  return (
    <div className="smx">
      <Routes>
        <Route index element={<Home />} />
        <Route path=":id" element={<Detail />} />
      </Routes>
    </div>
  );
}

/** Choices the options still offer. Left alone, the look changes with every video and the length follows the story. */
function valid(c: Choices, o: Overview["options"]): Choices {
  return {
    style: c.style === AUTO || o.styles.some((s) => s.key === c.style) ? c.style : AUTO,
    voice: o.voices.some((v) => v.key === c.voice) ? c.voice : o.defaults.voice,
    seconds: c.seconds === 0 || o.lengths.includes(c.seconds) ? c.seconds : 0,
    quality: o.qualities.some((q) => q.key === c.quality) ? c.quality : o.defaults.quality,
  };
}

const FIRST_IDEAS = 9;  // the best stories first; the rest behind one click

/** The whole page is one button. Everything else (look, voice, length, choosing the story) is optional and folded away. */
function Home() {
  const ov = useApi(sm.overview, 10000);
  const [choices, setChoices] = useState<Choices | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [more, setMore] = useState(false);
  const [shortsPoll, setShortsPoll] = useState(20000);
  const shorts = useApi(sm.shorts, shortsPoll);
  const navigate = useNavigate();

  const options = ov.data?.options;
  useEffect(() => { if (options && !choices) setChoices(valid(loadChoices({ ...options.defaults, style: AUTO, seconds: 0 }), options)); }, [options, choices]);
  const open = useMemo(() => (shorts.data?.items ?? []).filter((s) => s.status === "queued" || s.status === "rendering"), [shorts.data]);
  useEffect(() => { setShortsPoll(open.length ? 4000 : 20000); }, [open.length]);

  if (!ov.data || !choices || !options) return ov.failure ? <ApiErrorBanner error={ov.failure} /> : <Loading />;
  const set = (patch: Partial<Choices>) => { const next = { ...choices, ...patch }; setChoices(next); saveChoices(next); };
  const full = open.length >= ov.data.max_open;
  const quality = options.qualities.find((q) => q.key === choices.quality);
  const minutes = quality ? Math.max(1, Math.round((quality.minutes * (choices.seconds || 30)) / 30)) : null;

  const make = async (pick: { idea_id?: number; category?: string }, name: string) => {
    setBusy(name);
    setError(null);
    try {
      await sm.make(choices, pick);
      await Promise.all([shorts.reload(), ov.reload()]);
      document.getElementById("smx-making")?.scrollIntoView?.({ behavior: "smooth", block: "nearest" });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };
  const w = ov.data.worker;
  const done = (shorts.data?.items ?? []).filter((s) => s.status === "ready" || s.status === "failed");

  return (
    <>
      <section className="smx-hero">
        <div className="smx-hero-text">
          <p className="smx-eyebrow">Short Market</p>
          <h1>Money stories,<br />filmed by your AI.</h1>
          <p className="smx-sub">One click: it finds a real story, writes it, voices it and animates it in 3D. You download the video and post it on TikTok.</p>
          <div className="smx-pills">
            <span className={`smx-pill ${w.online ? "is-on" : "is-off"}`}><i />{w.online ? `${w.name} is online` : w.registered ? `${w.name ?? "Worker"} is offline` : "No worker yet"}</span>
            {w.online && <span className={`smx-pill ${w.video_ai ? "is-on" : "is-warn"}`}><i />{w.video_ai ? "3D video model ready" : "No video model: still scenes"}</span>}
            {w.online && <span className={`smx-pill ${w.voices.includes("kokoro") ? "is-on" : "is-warn"}`}><i />{w.voices.includes("kokoro") ? "Natural voice ready" : "Basic voice only"}</span>}
          </div>
        </div>
        <div className="smx-maker">
          <button className="smx-big" disabled={!!busy || full} onClick={() => void make({}, "best")}>
            {busy === "best" ? "Finding a story…" : "Make a video"}
          </button>
          <p className="smx-fine">{full ? `${ov.data.max_open} videos are already being made.` : "A new 10 to 50 second story every time you press it."}
            {minutes && w.video_ai && !full ? ` Ready in about ${minutes} minutes.` : ""}</p>
        </div>
      </section>

      <ApiErrorBanner error={error} />
      {!w.online && (
        <div className="smx-note" role="status">
          <b>{w.registered ? "Your AI worker is offline." : "No AI worker is connected."}</b>{" "}
          {w.registered ? `Videos you ask for wait here safely until it is back${w.last_seen_at ? ` (last seen ${relativeTime(w.last_seen_at)})` : ""}.`
            : "Videos are made on your own machine by the Price Chat worker (Brand Manager › Settings › AI › Add worker). Until one connects, videos you ask for wait in the queue."}
        </div>
      )}

      {open.length > 0 && (
        <section className="smx-card" id="smx-making">
          <h2>Now making</h2>
          {open.map((s) => <Making key={s.id} s={s} stages={ov.data!.pipeline} />)}
        </section>
      )}

      <section className="smx-section">
        <header className="smx-head"><div><h2>Your videos</h2><p className="smx-fine">Download, then upload to TikTok yourself. Nothing here posts anywhere.</p></div></header>
        {shorts.failure ? <ApiErrorBanner error={shorts.failure} /> : !shorts.data ? <Loading />
          : done.length === 0 ? <p className="muted">{open.length ? "Your first video is on its way." : "No videos yet. Press Make a video."}</p> : (
            <div className="smx-grid">{done.map((s) => <Tile key={s.id} s={s} onOpen={() => navigate(`/short-market/${s.id}`)} />)}</div>
          )}
      </section>

      <details className="smx-more-box" open={more} onToggle={(e) => setMore((e.currentTarget as HTMLDetailsElement).open)}>
        <summary>Options: look, voice, length, or pick the story yourself</summary>
        {more && (
          <div className="smx-options">
            <div className="smx-styles" role="radiogroup" aria-label="3D look">
              {[{ key: AUTO, label: "Surprise me", accent: "#ffc93c" }, ...options.styles].map((s) => (
                <button key={s.key} role="radio" aria-checked={choices.style === s.key} className={choices.style === s.key ? "is-on" : ""}
                        style={{ ["--sw" as string]: s.accent }} onClick={() => set({ style: s.key })}><i />{s.label}</button>
              ))}
            </div>
            <div className="smx-selects">
              <label>Voice<select value={choices.voice} onChange={(e) => set({ voice: e.target.value })}>{options.voices.map((v) => <option key={v.key} value={v.key}>{v.label}</option>)}</select></label>
              <label>Length<select value={choices.seconds} onChange={(e) => set({ seconds: Number(e.target.value) })}>
                <option value={0}>Automatic · 10 to 50 seconds</option>{options.lengths.map((n) => <option key={n} value={n}>{n} seconds</option>)}</select></label>
              <label>Picture<select value={choices.quality} onChange={(e) => set({ quality: e.target.value })}>{options.qualities.map((q) => <option key={q.key} value={q.key}>{q.label} · {q.detail}</option>)}</select></label>
            </div>
            <Ideas options={options} busy={!!busy || full} busyName={busy} onMake={(id) => void make({ idea_id: id }, `idea-${id}`)} onError={setError} />
          </div>
        )}
      </details>
    </>
  );
}

/** Story ideas, read from the sources only when the owner opens the options. */
function Ideas({ options, busy, busyName, onMake, onError }: { options: Overview["options"]; busy: boolean; busyName: string | null;
                                                               onMake: (id: number) => void; onError: (e: unknown) => void }) {
  const [category, setCategory] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [looking, setLooking] = useState(false);
  const ideas = useApi(() => sm.ideas(category || undefined));
  const mounted = useRef(false);
  useEffect(() => { if (mounted.current) void ideas.reload(); mounted.current = true; }, [category]); // eslint-disable-line react-hooks/exhaustive-deps
  const refresh = async () => {
    setLooking(true);
    try {
      const r = await sm.refreshIdeas();
      ideas.setData(category ? { ...r, items: r.items.filter((i) => i.category === category) } : r);
    } catch (e) {
      onError(e);
    } finally {
      setLooking(false);
    }
  };
  return (
    <section className="smx-section">
      <header className="smx-head">
        <div>
          <h2>Story ideas</h2>
          <p className="smx-fine">Found in news feeds, market data and history. Every fact has its source{ideas.data?.refreshed_at ? ` · checked ${relativeTime(ideas.data.refreshed_at)}` : ""}.</p>
        </div>
        <button className="btn" disabled={looking} onClick={() => void refresh()}>{looking ? "Looking…" : "Look again"}</button>
      </header>
      <div className="smx-chips" role="tablist" aria-label="Topic">
        {[{ key: "", label: "Everything" }, ...options.categories].map((c) => (
          <button key={c.key} role="tab" aria-selected={category === c.key} className={category === c.key ? "is-on" : ""} onClick={() => setCategory(c.key)}>{c.label}</button>
        ))}
      </div>
      {ideas.failure ? <ApiErrorBanner error={ideas.failure} /> : !ideas.data ? <p className="muted">Reading the sources…</p>
        : ideas.data.items.length === 0 ? <p className="muted">No story with reliable sources in this topic right now. Try another topic, or look again later.</p> : (
          <div className="smx-ideas">
            {(showAll ? ideas.data.items : ideas.data.items.slice(0, FIRST_IDEAS)).map((i) => <IdeaCard key={i.id} idea={i} busy={busy} making={busyName === `idea-${i.id}`} onMake={() => onMake(i.id)} />)}
          </div>
        )}
      {!showAll && (ideas.data?.items.length ?? 0) > FIRST_IDEAS && (
        <button className="btn smx-more" onClick={() => setShowAll(true)}>Show all {ideas.data!.items.length} stories</button>
      )}
    </section>
  );
}

function Making({ s, stages, bare = false }: { s: Short; stages: Overview["pipeline"]; bare?: boolean }) {
  const current = stages.find((st) => ["running", "retrying"].includes(s.stages[st.key]?.status ?? ""));
  const note = current ? s.stages[current.key]?.note : "";
  return (
    <div className="smx-making">
      {!bare && <p className="smx-making-title"><NavLink to={`/short-market/${s.id}`}>{s.title || s.topic}</NavLink><span className="muted">{s.style_label} · {s.seconds}s</span></p>}
      <ol>
        {stages.map((st) => {
          const status = s.stages[st.key]?.status ?? "waiting";
          return <li key={st.key} className={`is-${status}`} title={s.stages[st.key]?.note || undefined}><i />{st.label}</li>;
        })}
      </ol>
      <p className="smx-fine">{s.status === "queued" ? (s.attempts > 0 ? `Trying again${s.error ? `: ${s.error}` : ""}` : "Waiting for the worker to pick it up.")
        : current ? `${current.label}${note ? ` · ${note}` : ""}` : "Working…"}</p>
    </div>
  );
}

function IdeaCard({ idea, busy, making, onMake }: { idea: Idea; busy: boolean; making: boolean; onMake: () => void }) {
  return (
    <article className={`smx-idea is-${idea.category}${idea.used ? " is-used" : ""}`}>
      <p className="smx-tag">{idea.category_label}</p>
      <h3>{idea.headline}</h3>
      <ul>{idea.facts.slice(0, 3).map((f, k) => <li key={k}>{f.text}{f.source && <span className="muted"> ({f.source})</span>}</li>)}</ul>
      <footer>
        <span className="smx-sources">{idea.sources.slice(0, 2).map((x, k) => x.url ? <a key={k} href={x.url} target="_blank" rel="noreferrer">{x.name} ↗</a> : <span key={k}>{x.name}</span>)}</span>
        {idea.short_id ? <NavLink className="btn btn-sm" to={`/short-market/${idea.short_id}`}>Made ✓</NavLink>
          : <button className="btn btn-sm smx-make" disabled={busy} onClick={onMake}>{making ? "Starting…" : idea.used ? "Make anyway" : "Make this short"}</button>}
      </footer>
    </article>
  );
}

function size(bytes: number | null): string {
  return bytes ? `${(bytes / 1e6).toFixed(1)} MB` : "";
}

function Tile({ s, onOpen }: { s: Short; onOpen: () => void }) {
  const [label, tone] = STATUS[s.status];
  return (
    <article className="smx-tile">
      <button className="smx-thumb" onClick={onOpen} aria-label={`Open ${s.title || s.topic}`}>
        {s.has_thumb ? <img src={sm.media(s, "thumb")} alt="" loading="lazy" /> : <span>{s.status === "failed" ? "Couldn't be made" : "No picture"}</span>}
        {s.duration && <em>{s.duration.toFixed(0)}s</em>}
      </button>
      <div className="smx-tile-body">
        <span className={`smx-badge is-${tone}`}>{s.status === "ready" && s.downloaded_at ? "Downloaded" : label}</span>
        <h3><NavLink to={`/short-market/${s.id}`}>{s.title || s.topic}</NavLink></h3>
        <p className="muted">{s.style_label} · {relativeTime(s.created_at)}</p>
        {s.has_video && s.status === "ready" && <a className="btn btn-primary btn-sm" href={sm.download(s)} download>Download{s.size_bytes ? ` · ${size(s.size_bytes)}` : ""}</a>}
      </div>
    </article>
  );
}

function Detail() {
  const id = Number(useParams().id);
  const [poll, setPoll] = useState(4000);
  const { data: s, failure, setData } = useApi(() => sm.short(id), poll);
  const ov = useApi(sm.overview);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [copied, setCopied] = useState(false);
  const [again, setAgain] = useState<Partial<Choices> | null>(null);
  const navigate = useNavigate();
  const openNow = s ? s.status === "queued" || s.status === "rendering" : true;
  useEffect(() => { setPoll(openNow ? 4000 : 30000); }, [openNow]);
  if (!s) return failure ? <><p><NavLink to="/short-market">← Short Market</NavLink></p><ApiErrorBanner error={failure} /></> : <Loading />;

  const act = async (name: string, fn: () => Promise<ShortDetail | void>) => {
    setBusy(name);
    setError(null);
    try {
      const r = await fn();
      if (r) setData(r);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(s.caption);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch (e) {
      setError(e);
    }
  };
  const [label, tone] = STATUS[s.status];
  const sc = s.script ?? {};
  const info = s.render_info;
  const options = ov.data?.options;
  return (
    <>
      <p><NavLink to="/short-market">← Short Market</NavLink></p>
      <ApiErrorBanner error={error} />
      <div className="smx-detail">
        <section className="smx-player">
          {s.has_video && s.status === "ready" ? <video key={s.generation} src={sm.media(s, "video")} controls playsInline poster={s.has_thumb ? sm.media(s, "thumb") : undefined} />
            : <div className="smx-noplayer">{s.status === "failed" ? "This short couldn't be made." : "The video appears here when it's finished."}</div>}
          {info && <p className="smx-fine">Made by {info.worker} in {info.seconds ? `${Math.round(info.seconds / 60)} min` : "?"} · {info.ai_scenes ?? 0} of {info.scenes ?? "?"} scenes animated
            {info.renderer ? ` (${info.renderer})` : ""} · voice {info.tts} · story {info.llm}</p>}
        </section>
        <section className="smx-info">
          <span className={`smx-badge is-${tone}`}>{label}</span>
          <h1>{sc.title || s.topic}</h1>
          <p className="muted">{s.category_label} · {s.style_label} · {s.duration ? `${s.duration.toFixed(0)}s` : `${s.seconds}s asked`}{s.size_bytes ? ` · ${size(s.size_bytes)}` : ""}</p>
          {s.error && s.status !== "ready" && <div className="smx-alert" role="alert"><b>{s.status === "failed" ? "Problem" : "Last try"}</b><p>{s.error}</p></div>}
          {openNow && ov.data && <Making s={s} stages={ov.data.pipeline} bare />}

          <div className="smx-actions">
            {s.has_video && s.status === "ready" && <a className="btn btn-primary" href={sm.download(s)} download>Download MP4</a>}
            {s.caption && <button className="btn" onClick={() => void copy()}>{copied ? "Copied ✓" : "Copy caption"}</button>}
            <button className="btn" disabled={!!busy} onClick={() => setAgain(again ? null : {})}>Make it again…</button>
            <button className="btn btn-ghost" disabled={!!busy} onClick={() => { if (window.confirm("Delete this short and its video?")) void act("del", async () => { await sm.remove(s.id); navigate("/short-market"); }); }}>Delete</button>
          </div>
          {again && options && (
            <div className="smx-form">
              <p className="smx-fine">Same story, new take: a new script, new voice recording and new scenes. The current video is replaced when the new one is ready.</p>
              <div className="smx-selects">
                <label>Look<select value={again.style ?? s.style} onChange={(e) => setAgain({ ...again, style: e.target.value })}>{options.styles.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}</select></label>
                <label>Voice<select value={again.voice ?? s.voice} onChange={(e) => setAgain({ ...again, voice: e.target.value })}>{options.voices.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}</select></label>
                <label>Length<select value={again.seconds ?? s.seconds} onChange={(e) => setAgain({ ...again, seconds: Number(e.target.value) })}>{options.lengths.map((n) => <option key={n} value={n}>{n} seconds</option>)}</select></label>
                <label>Picture<select value={again.quality ?? s.quality} onChange={(e) => setAgain({ ...again, quality: e.target.value })}>{options.qualities.map((o) => <option key={o.key} value={o.key}>{o.label} · {o.detail}</option>)}</select></label>
              </div>
              <div className="smx-actions">
                <button className="btn btn-primary" disabled={!!busy} onClick={() => void act("again", async () => { const r = await sm.remake(s.id, again); setAgain(null); return r; })}>{busy === "again" ? "Starting…" : "Make it again"}</button>
                <button className="btn btn-ghost" onClick={() => setAgain(null)}>Cancel</button>
              </div>
            </div>
          )}

          {s.status === "ready" && (
            <>
              <h2>Post it</h2>
              <ol className="smx-steps">
                <li>Download the MP4 and open TikTok’s upload screen.</li>
                <li>Paste the caption below (the Copy caption button puts it on your clipboard).</li>
                <li>Switch on TikTok’s <b>AI-generated content</b> label: the pictures and the voice are made by AI.</li>
              </ol>
            </>
          )}
          {s.caption && (<><h2>Caption</h2><pre className="smx-caption">{s.caption}</pre></>)}
          <h2>Story</h2>
          {sc.segments?.length ? (
            <ol className="smx-script">
              {sc.segments.map((g, k) => <li key={k}><b>{g.screen}</b><span>{g.text}</span><small>{g.visual}</small></li>)}
            </ol>
          ) : <p className="muted">Not written yet.</p>}
          {sc.engine && <p className="smx-fine">Written by {sc.engine}. Checked before filming: every number comes from the facts below.</p>}
          <h2>Facts and sources</h2>
          <ul className="smx-facts">{(s.brief.facts ?? []).map((f, k) => <li key={k}>{f.text} <span className="muted">({f.source})</span></li>)}</ul>
          <ul className="smx-facts">{s.sources.map((x, k) => <li key={k}>{x.url ? <a href={x.url} target="_blank" rel="noreferrer">{x.name} ↗</a> : x.name}</li>)}</ul>
        </section>
      </div>
    </>
  );
}
