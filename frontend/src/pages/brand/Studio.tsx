import { useEffect, useState } from "react";
import { NavLink, useNavigate, useParams } from "react-router-dom";
import { useApi } from "../../api/useApi";
import { ApiErrorBanner, Loading, formatTime } from "../../components/ui";
import { bm, num, type TikTokAccount, type Video, type VideoDetail } from "./api";
import { EventList, StatusBadge } from "./BrandManager";
import { CreativePanel } from "./Creative";
import { EditorPanel } from "./Editor";

const FILTERS: [string, string][] = [["", "All"], ["needs_review,ready,approved", "To approve / ready"], ["queued,rendering", "In progress"],
  ["publishing,sent_to_drafts,published", "On TikTok"], ["failed,cancelled", "Failed"]];
const CATEGORIES: [string, string][] = [["", "Best topic now"], ["crypto_news", "Breaking crypto news"], ["crypto_market", "Crypto market update"], ["crypto_derivatives", "Hyperliquid: moves, funding, open interest"],
  ["pokemon_market", "Pokémon card market"], ["pokemon_rare", "Rare collectible sale"], ["facts", "Top 5 facts"], ["pricechat_highlights", "Price Chat highlight"]];

/** The worker's stages in the order it runs them, weighted by roughly how long each takes (they add up to 100). */
const STEPS: [string, number, string][] = [["visuals", 35, "Finding real footage"], ["script", 20, "Writing the script"], ["voice", 10, "Recording the voice"],
  ["assemble", 30, "Editing & rendering"], ["quality", 5, "Checking & uploading"]];

/** How far a queued or rendering video is, 0-99, from the stages the worker reports. A running step counts half, except
 * the render, which reports its own percentage ("render 45%"). 100% is the finished video itself. */
export function renderProgress(v: Pick<Video, "status" | "stages">): { pct: number; label: string } | null {
  if (v.status === "queued") return { pct: 0, label: "Waiting for a worker" };
  if (v.status !== "rendering") return null;
  let pct = 0;
  let label = "";
  for (const [key, weight, name] of STEPS) {  // summed in any order: the classic engine scripts before it finds visuals
    const st = v.stages?.[key];
    if (st?.status === "done" || st?.status === "skipped") pct += weight;
    else if (st?.status === "running" || st?.status === "retrying") {
      const m = /render (\d+)%/.exec(st.note ?? "");
      pct += m ? weight * (0.1 + 0.9 * Number(m[1]) / 100) : weight / 2;  // the first tenth of the edit is laying out the timeline
      label ||= st.status === "retrying" ? `${name} (retrying)` : name;
    }
  }
  return { pct: Math.min(99, Math.round(pct)), label: label || (pct >= 95 ? "Checking & uploading" : "Starting") };
}

function ProgressBar({ v, compact }: { v: Pick<Video, "status" | "stages">; compact?: boolean }) {
  const p = renderProgress(v);
  if (!p) return null;
  return (
    <div className={`bmx-progress${compact ? " is-compact" : ""}`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={p.pct} aria-label="Video progress">
      <div className="bmx-progress-top"><span>{p.label}</span><b>{p.pct}%</b></div>
      <div className="bmx-progress-track"><div className="bmx-progress-fill" style={{ width: `${p.pct}%` }} /></div>
    </div>
  );
}

export function StudioTab() {
  const { id } = useParams();
  const [filter, setFilter] = useState("");
  const [poll, setPoll] = useState(15000);
  const { data, failure, reload } = useApi(() => bm.videos(filter || undefined), poll);
  const working = !!data?.items.some((v) => renderProgress(v));
  useEffect(() => setPoll(working ? 4000 : 15000), [working]);  // the bars move while something renders
  const [cat, setCat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const navigate = useNavigate();
  useEffect(() => { void reload(); }, [filter, reload]);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const v = await bm.create(cat);
      navigate(`/brand-manager/studio/${v.id}`);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  if (id) return <VideoPanel id={Number(id)} onChanged={() => void reload()} />;
  return (
    <>
      <div className="bmx-toolbar">
        <div className="bmx-chips">{FILTERS.map(([k, l]) => <button key={k} className={filter === k ? "is-on" : ""} onClick={() => setFilter(k)}>{l}</button>)}</div>
        <div className="bmx-create">
          <select value={cat} onChange={(e) => setCat(e.target.value)} aria-label="Category">{CATEGORIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
          <button className="btn btn-primary" disabled={busy} onClick={() => void create()}>{busy ? "Finding a topic…" : "＋ Make a video now"}</button>
        </div>
      </div>
      <ApiErrorBanner error={error ?? failure} />
      {!data ? <Loading /> : data.items.length === 0 ? <p className="muted">No videos yet. Press START AUTO on the Overview, or make one now.</p> : (
        <div className="bmx-grid">{data.items.map((v) => <Tile key={v.id} v={v} />)}</div>
      )}
    </>
  );
}

function Tile({ v }: { v: Video }) {
  return (
    <NavLink to={`/brand-manager/studio/${v.id}`} className="bmx-vtile">
      <div className="bmx-vthumb">{v.has_thumb ? <img src={bm.media(v.id, "thumb", v.generation)} alt="" loading="lazy" /> : <span>{v.status === "rendering" ? "Rendering…" : "No video yet"}</span>}</div>
      <div className="bmx-vbody">
        <StatusBadge s={v.status} />
        <ProgressBar v={v} compact />
        <h3>{v.title || v.topic}</h3>
        <p className="muted">{v.template_label}{v.duration ? ` · ${v.duration.toFixed(0)}s` : ""}</p>
        <p className="muted">{v.slot_at ? `Slot ${new Date(v.slot_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}` : "Unscheduled"}</p>
        {v.metrics?.views != null && <p><b>{num(v.metrics.views)}</b> views · {num(v.metrics.likes)} likes</p>}
      </div>
    </NavLink>
  );
}

function VideoPanel({ id, onChanged }: { id: number; onChanged: () => void }) {
  const [poll, setPoll] = useState(8000);
  const { data: v, failure, setData, reload } = useApi(() => bm.video(id), poll);
  const working = !!v && !!renderProgress(v);
  useEffect(() => setPoll(working ? 3000 : 8000), [working]);
  const acc = useApi(bm.tiktok);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [edit, setEdit] = useState<{ title: string; description: string; hashtags: string } | null>(null);
  const navigate = useNavigate();
  if (!v) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const act = async (name: string, fn: () => Promise<VideoDetail | void>) => {
    setBusy(name);
    setError(null);
    try {
      const r = await fn();
      if (r) setData({ ...r, events: r.events ?? v.events });
      onChanged();
      void reload();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };
  const s = v.script || {};
  const onTikTok = ["publishing", "sent_to_drafts", "published"].includes(v.status);
  const direct = (v.publish_mode ?? "drafts") === "direct";
  return (
    <>
      <p><NavLink to="/brand-manager/studio">← Content Studio</NavLink></p>
      <ApiErrorBanner error={error} />
      <div className="bmx-detail">
        <section className="bmx-player">
          {v.has_video ? <video src={bm.media(v.id, "video", v.generation)} controls playsInline poster={v.has_thumb ? bm.media(v.id, "thumb", v.generation) : undefined} />
            : <div className="bmx-noplayer">{v.status === "failed" ? "Rendering failed" : renderProgress(v) ? <ProgressBar v={v} /> : "The video appears here when a worker finishes it."}</div>}
          {v.qc && <p className="bmx-fine">Quality check: {v.qc.problems.length ? v.qc.problems.join("; ") : `passed · ${v.qc.width}×${v.qc.height} · ${v.qc.duration.toFixed(1)}s · ${(v.qc.size_bytes / 1e6).toFixed(1)} MB`}</p>}
          {v.render_info && <p className="bmx-fine">Made by {String(v.render_info.worker)} · script {String(v.render_info.llm ?? "")} · voice {String(v.render_info.tts ?? "")} · {String(v.render_info.seconds ?? "?")}s</p>}
        </section>
        <section className="bmx-info">
          <StatusBadge s={v.status} />
          <h1>{s.title || v.topic}</h1>
          <p className="muted">{v.category_label} · {v.template_label} · {v.slot_at ? `slot ${formatTime(v.slot_at)}` : "unscheduled"}{v.private ? " · private post" : ""}</p>
          {v.error && <div className="bmx-alert"><b>Problem</b><p>{v.error}</p></div>}
          {v.metrics && <p className="bmx-numbers"><span><b>{num(v.metrics.views)}</b> views</span><span><b>{num(v.metrics.likes)}</b> likes</span>
            <span><b>{num(v.metrics.comments)}</b> comments</span><span><b>{num(v.metrics.shares)}</b> shares</span>
            {v.metrics.share_url && <a href={v.metrics.share_url} target="_blank" rel="noreferrer">Open on TikTok ↗</a>}</p>}

          {onTikTok && v.publish_status !== "PUBLISH_COMPLETE" && (
            <div className="bmx-actions">
              <span className="muted">TikTok status: <b>{v.publish_status ?? "sent"}</b></span>
              <button className="btn" disabled={!!busy} onClick={() => void act("status", () => bm.publishStatus(v.id))}>{busy === "status" ? "Asking TikTok…" : "Check status on TikTok"}</button>
            </div>
          )}
          {!onTikTok && ["needs_review", "ready", "approved"].includes(v.status) && v.has_video && (
            <div className="bmx-dest" role="radiogroup" aria-label="Where this video goes">
              <span className="muted">Send to</span>
              <button role="radio" aria-checked={!direct} className={!direct ? "is-on" : ""} disabled={!!busy || (direct && acc.data?.can_upload_drafts === false)}
                      onClick={() => { if (direct) void act("dest", () => bm.setDestination(v.id, "drafts")); }}>TikTok drafts (inbox)</button>
              <button role="radio" aria-checked={direct} className={direct ? "is-on" : ""} disabled={!!busy || (!direct && acc.data?.can_direct_post === false)}
                      onClick={() => { if (!direct) void act("dest", () => bm.setDestination(v.id, "direct")); }}>Post directly</button>
            </div>
          )}
          {!onTikTok && (
            <div className="bmx-actions">
              {v.status === "needs_review" && !direct && <button className="btn btn-primary" disabled={!!busy} onClick={() => void act("approve", () => bm.approve(v.id))}>Approve</button>}
              {(v.status === "ready" || v.status === "approved") && !direct && (
                <button className="btn btn-primary" disabled={!!busy} onClick={() => void act("send", () => bm.publishNow(v.id))}>Send to TikTok drafts now</button>
              )}
              {(v.status === "ready" || v.status === "approved") && !direct && acc.data?.username && (
                <span className="bmx-fine">Uploads this video to @{acc.data.username}'s TikTok inbox. You finish and post it in the TikTok app.</span>
              )}
              {v.status === "approved" && direct && <button className="btn btn-primary" disabled={!!busy} onClick={() => void act("send", () => bm.publishNow(v.id))}>Post now</button>}
              <button className="btn" disabled={!!busy || !s.title} onClick={() => setEdit({ title: s.title ?? "", description: s.description ?? "", hashtags: (s.hashtags ?? []).join(" ") })}>Edit caption</button>
              <button className="btn" disabled={!!busy} onClick={() => void act("regen", () => bm.regenerate(v.id))}>New script</button>
              <button className="btn btn-ghost" disabled={!!busy} onClick={() => { if (window.confirm("Delete this video?")) void act("del", async () => { await bm.remove(v.id); navigate("/brand-manager/studio"); }); }}>Delete</button>
            </div>
          )}
          {edit && (
            <div className="bmx-form">
              <label>Title<input value={edit.title} maxLength={150} onChange={(e) => setEdit({ ...edit, title: e.target.value })} /></label>
              <label>Description<textarea rows={3} value={edit.description} maxLength={2000} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></label>
              <label>Hashtags<input value={edit.hashtags} onChange={(e) => setEdit({ ...edit, hashtags: e.target.value })} /></label>
              <div className="bmx-actions">
                <button className="btn btn-primary" onClick={() => void act("save", async () => { const r = await bm.edit(v.id, { title: edit.title, description: edit.description, hashtags: edit.hashtags.split(/\s+/).filter(Boolean) }); setEdit(null); return r; })}>Save</button>
                <button className="btn btn-ghost" onClick={() => setEdit(null)}>Cancel</button>
              </div>
            </div>
          )}
          {direct && ["needs_review", "ready", "approved"].includes(v.status) && v.has_video && (
            <PostScreen v={v} busy={!!busy} onApprove={(b, now) => void act(now ? "send" : "approve", async () => {
              const r = await bm.approve(v.id, b);
              return now ? bm.publishNow(v.id) : r;
            })} />
          )}

          <CreativePanel v={v} busy={!!busy} act={(n, fn) => void act(n, fn)} />
          <EditorPanel v={v} busy={!!busy} act={(n, fn) => void act(n, fn)} />
          {s.hook && <p className="bmx-hook"><span className="muted">Hook</span> “{s.hook}”</p>}
          <h2>Caption</h2>
          <p>{s.description || "—"}</p>
          <p className="bmx-tags">{(s.hashtags ?? []).map((t) => <span key={t} className="bmx-tag">{t}</span>)}</p>
          {s.title && <CopyCaption text={`${s.title}\n${s.description ?? ""}\n${(s.hashtags ?? []).join(" ")}`.trim()} />}
          <h2>Script</h2>
          {s.segments?.length ? <ol className="bmx-script">{s.segments.map((g, i) => <li key={i}><em>{g.scene}</em> {g.text}{g.screen && <small className="muted"> · on screen: {g.screen}</small>}</li>)}</ol> : <p className="muted">Not written yet.</p>}
          {s.engine && <p className="bmx-fine">Written by {s.engine}; fact-checked twice: every number comes from the research below, and claims the research doesn't make are sent back.</p>}
          <h2>Research & sources</h2>
          <ul className="bmx-facts">{(v.brief.facts ?? []).map((f, i) => <li key={i}>{f.text} <span className="muted">({f.source})</span></li>)}</ul>
          <ul className="bmx-facts">{v.sources.map((x, i) => <li key={i}>{x.url ? <a href={x.url} target="_blank" rel="noreferrer">{x.name} ↗</a> : x.name}</li>)}</ul>
          <h2>Pipeline</h2>
          <ul className="bmx-stagelist">{Object.entries(v.stages).map(([k, st]) => <li key={k} className={`is-${st.status}`}><b>{k}</b> {st.status}{st.note ? ` · ${st.note}` : ""}</li>)}</ul>
          <h2>History</h2>
          <EventList events={v.events} />
        </section>
      </div>
    </>
  );
}

/** The TikTok caption (title, description, hashtags) on the clipboard: drafts are finished in the TikTok app. */
function CopyCaption({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      window.setTimeout(() => setDone(false), 2000);
    } catch {
      setDone(false);
    }
  };
  return <button className="btn btn-ghost" onClick={() => void copy()}>{done ? "Copied ✓" : "Copy caption for TikTok"}</button>;
}

/** TikTok's required posting screen for direct posts (Content Sharing Guidelines): creator shown, privacy chosen by the
 * owner with no default, interactions off by default and greyed when the creator disabled them, commercial content
 * disclosure, the Music Usage Confirmation, a preview, and an editable title. */
function PostScreen({ v, busy, onApprove }: { v: VideoDetail; busy: boolean; onApprove: (b: Record<string, unknown>, now: boolean) => void }) {
  const [acc, setAcc] = useState<TikTokAccount | null>(null);
  const [infoError, setInfoError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const load = async () => {  // TikTok: query creator info every time the posting screen renders
    setLoading(true);
    setInfoError(null);
    try { setAcc(await bm.creatorInfo()); } catch (e) { setInfoError(e); } finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, [v.id]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (loading && !acc) return <div className="bmx-post"><h2>Post to TikTok</h2><p className="muted">Reading this TikTok account's posting options…</p></div>;
  if (!acc?.creator_info) return (
    <div className="bmx-post">
      <h2>Post to TikTok</h2>
      <ApiErrorBanner error={infoError} />
      <p className="bmx-note">TikTok didn't return this account's posting options, so posting is off. {infoError ? "" : "Connect TikTok with the video.publish permission first."}</p>
      <button className="btn" disabled={loading} onClick={() => void load()}>Try again</button>
    </div>
  );
  return <PostForm v={v} acc={acc} busy={busy || loading} onApprove={onApprove} />;
}

function PostForm({ v, acc, busy, onApprove }: { v: VideoDetail; acc: TikTokAccount; busy: boolean; onApprove: (b: Record<string, unknown>, now: boolean) => void }) {
  const info = acc.creator_info ?? {};
  const [privacy, setPrivacy] = useState("");
  const [comment, setComment] = useState(false);
  const [duet, setDuet] = useState(false);
  const [stitch, setStitch] = useState(false);
  const [disclose, setDisclose] = useState(false);
  const [yours, setYours] = useState(false);
  const [branded, setBranded] = useState(false);
  const [title, setTitle] = useState(`${v.script.title ?? ""} ${(v.script.hashtags ?? []).join(" ")}`.trim());
  const options = info.privacy_level_options ?? [];
  const label: Record<string, string> = { PUBLIC_TO_EVERYONE: "Everyone", MUTUAL_FOLLOW_FRIENDS: "Friends", FOLLOWER_OF_CREATOR: "Followers", SELF_ONLY: "Only me" };
  const maxSec = info.max_video_post_duration_sec;
  const tooLong = !!maxSec && (v.duration ?? 0) > maxSec;
  const invalid = tooLong || !privacy || (disclose && !yours && !branded) || (branded && privacy === "SELF_ONLY");
  const choices = () => ({ privacy_level: privacy, allow_comment: comment, allow_duet: duet, allow_stitch: stitch, disclose, your_brand: yours,
                           branded_content: branded, title, consent: true });
  return (
    <div className="bmx-post">
      <h2>Post to TikTok</h2>
      <p className="bmx-creator">{info.creator_avatar_url && <img src={info.creator_avatar_url} alt="" referrerPolicy="no-referrer" />}
        <span>Posting to <b>{info.creator_nickname}</b> (@{info.creator_username})</span></p>
      {tooLong && <div className="bmx-alert" role="alert"><b>Too long for this account</b><p>This video is {v.duration?.toFixed(0)} s; this TikTok account can post up to {maxSec} s.</p></div>}
      <label>Title<textarea rows={2} value={title} maxLength={2200} onChange={(e) => setTitle(e.target.value)} /></label>
      <label>Who can see this video
        <select value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
          <option value="" disabled>Choose…</option>
          {options.map((o) => <option key={o} value={o} disabled={(o !== "SELF_ONLY" && !acc.can_direct_post) || (branded && o === "SELF_ONLY")}
            title={branded && o === "SELF_ONLY" ? "Branded content visibility cannot be set to private." : undefined}>{label[o] ?? o}</option>)}
        </select>
      </label>
      <p className="bmx-fine">Until TikTok audits Price Chat's app, only “Only me” is accepted for direct posts.</p>
      <fieldset>
        <legend>Allow users to</legend>
        <label className={info.comment_disabled ? "is-off" : ""}><input type="checkbox" disabled={info.comment_disabled} checked={comment} onChange={(e) => setComment(e.target.checked)} /> Comment</label>
        <label className={info.duet_disabled ? "is-off" : ""}><input type="checkbox" disabled={info.duet_disabled} checked={duet} onChange={(e) => setDuet(e.target.checked)} /> Duet</label>
        <label className={info.stitch_disabled ? "is-off" : ""}><input type="checkbox" disabled={info.stitch_disabled} checked={stitch} onChange={(e) => setStitch(e.target.checked)} /> Stitch</label>
      </fieldset>
      <label className="bmx-switch"><input type="checkbox" checked={disclose} onChange={(e) => { setDisclose(e.target.checked); if (!e.target.checked) { setYours(false); setBranded(false); } }} /> Disclose video content</label>
      {disclose && (
        <div className="bmx-disclose">
          <label><input type="checkbox" checked={yours} onChange={(e) => setYours(e.target.checked)} /> Your brand <small>You are promoting yourself or your own business.</small></label>
          <label><input type="checkbox" checked={branded} onChange={(e) => { setBranded(e.target.checked); if (e.target.checked && privacy === "SELF_ONLY") setPrivacy(""); }} /> Branded content <small>You are promoting another brand or a third party.</small></label>
          {(yours || branded) && <p className="bmx-fine">Your video will be labeled “{branded ? "Paid partnership" : "Promotional content"}”.</p>}
          {!yours && !branded && <p className="bmx-fine">Choose at least one to post with disclosure on.</p>}
        </div>
      )}
      <p className="bmx-fine">
        By posting, you agree to TikTok's {branded && <><a href="https://www.tiktok.com/legal/page/global/bc-policy/en" target="_blank" rel="noreferrer">Branded Content Policy</a> and </>}
        <a href="https://www.tiktok.com/legal/page/global/music-usage-confirmation/en" target="_blank" rel="noreferrer">Music Usage Confirmation</a>.
        After posting, it may take a few minutes for the video to appear on your profile.
      </p>
      <div className="bmx-actions">
        <button className="btn btn-primary" disabled={busy || invalid} title={invalid ? "Choose who can see the video (and a disclosure option if disclosure is on)" : undefined}
                onClick={() => onApprove(choices(), true)}>
          Post to TikTok now
        </button>
        <button className="btn" disabled={busy || invalid} onClick={() => onApprove(choices(), false)}>Approve & post at its slot</button>
      </div>
    </div>
  );
}
