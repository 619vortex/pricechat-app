import { useCallback, useEffect, useState } from "react";
import { request } from "../../api/client";

/** The picture behind a story (owner, 2026-11-23): the image a post carries or would carry, where it came from (the editor's library, a
 *  Commons search, Price Chat's own chart, the source article's preview), its page, author, licence and credit, and why it was chosen.
 *  A story with no usable picture is shown as held, with every reason; a held News Desk post can be looked at again, sent as words alone
 *  on the owner's say, or skipped. Reading a picture sends nothing and costs nothing (no paid AI). */

export type Picture = {
  kind: string; held: boolean; image: string; preview_image: string; source: string; page: string; title: string; author: string;
  licence: string; licence_url: string; credit: string; site: string; size: [number, number]; why: string; depiction: string;
  previews: { site: string; ok: boolean; why: string }[];
};

type Target = { kind: "topic"; id: number } | { kind: "event"; id: number; held?: boolean };

const KIND_TEXT: Record<string, string> = { photo: "Photo", template: "Price Chat graphic", chart: "Chart", data: "Data card", link_preview: "Article preview",
  held: "Held: no picture", ai: "AI illustration" };

function errText(e: unknown): string {
  return String((e as Error)?.message ?? e).replace(/^\d{3}:?\s*/, "");
}

export function PicturePanel({ target, onChanged }: { target: Target; onChanged?: () => void }) {
  const [pic, setPic] = useState<Picture | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const path = target.kind === "topic" ? `/terminal/stories/${target.id}/picture` : `/terminal/events/${target.id}/picture`;

  const load = useCallback(async (fresh = false) => {
    setProblem(null);
    try {
      const got = await request<Partial<Picture>>(`${path}${fresh ? "?fresh=true" : ""}`);
      setPic({ kind: "", held: false, image: "", preview_image: "", source: "", page: "", title: "", author: "", licence: "", licence_url: "", credit: "",
        site: "", why: "", depiction: "", ...got, size: Array.isArray(got?.size) ? got.size : [0, 0], previews: Array.isArray(got?.previews) ? got.previews : [] });
    } catch (e) {
      setProblem(errText(e));
    }
  }, [path]);

  useEffect(() => { setPic(null); void load(); }, [load]);

  async function release(action: "retry" | "text" | "skip") {
    if (target.kind !== "event") return;
    const ask = { retry: "Look for a picture again and post it if one passes?", text: "Send this post WITHOUT a picture?", skip: "Skip this post on every platform?" }[action];
    if (!window.confirm(ask)) return;
    setBusy(true); setNote(null);
    try {
      await request(`/terminal/events/${target.id}/held`, { method: "POST", body: JSON.stringify({ action }) });
      setNote(action === "skip" ? "Skipped." : action === "text" ? "Released: it goes out as words alone at the next run." : "Released: the desk looks for a picture again at the next run.");
      onChanged?.();
    } catch (e) {
      setNote(errText(e));
    } finally { setBusy(false); }
  }

  const loading = !pic && !problem;
  return (
    <section className="t2-section t2-picture" aria-label="Picture">
      <h4>Picture</h4>
      {loading && <div className="t2-pic-frame loading" aria-busy="true"><span className="t2-meta">Finding the picture…</span></div>}
      {problem && <p className="t2-problem">{problem}</p>}
      {pic && (
        <>
          {pic.image ? (
            <figure className="t2-pic-frame"><img src={pic.image} alt={pic.title || "The post's picture"} /></figure>
          ) : pic.kind === "link_preview" ? (
            <figure className="t2-pic-frame preview">
              {pic.preview_image && <img src={pic.preview_image} alt={pic.title || "The article's preview picture"} referrerPolicy="no-referrer" loading="lazy" />}
              <figcaption><span className="t2-pic-site">{pic.site}</span><span>{pic.title}</span></figcaption>
            </figure>
          ) : (
            <div className="t2-pic-frame held" role="note">
              <strong>No picture: held for your review</strong>
              <span>The desk never posts text-only on its own.</span>
            </div>
          )}
          <dl className="t2-pic-facts">
            <div><dt>Type</dt><dd><span className={`t2-pic-kind k-${pic.kind}`}>{KIND_TEXT[pic.kind] ?? pic.kind}</span></dd></div>
            <div><dt>Source</dt><dd>{pic.source}</dd></div>
            {pic.page && <div><dt>Page</dt><dd><a href={pic.page} target="_blank" rel="noreferrer">{pic.title || pic.page}</a></dd></div>}
            {pic.author && <div><dt>Author</dt><dd>{pic.author}</dd></div>}
            {pic.licence && <div><dt>Licence</dt><dd>{pic.licence_url ? <a href={pic.licence_url} target="_blank" rel="noreferrer">{pic.licence}</a> : pic.licence}</dd></div>}
            {pic.credit && <div><dt>Credit</dt><dd>{pic.credit}</dd></div>}
            {pic.size[0] > 0 && <div><dt>Size</dt><dd>{pic.size[0]} × {pic.size[1]} px</dd></div>}
            {pic.why && <div><dt>{pic.held ? "Why held" : "Why this"}</dt><dd>{pic.why}{pic.depiction ? ` (shows: ${pic.depiction})` : ""}</dd></div>}
          </dl>
          {pic.previews.length > 0 && (
            <div className="t2-pic-tried">
              <span className="t2-meta">Article previews tried</span>
              <ul>{pic.previews.map((p, i) => <li key={i} className={p.ok ? "ok" : "no"}>{p.site}: {p.ok ? "shows a picture" : p.why}</li>)}</ul>
            </div>
          )}
          <div className="t2-actions">
            {target.kind === "topic" && <button className="t2-link" onClick={() => { setPic(null); void load(true); }}>Look again</button>}
            {target.kind === "event" && target.held && (
              <>
                <button className="t2-btn" disabled={busy} onClick={() => void release("retry")}>Look again and post</button>
                <button className="t2-btn ghost" disabled={busy} onClick={() => void release("text")}>Send without picture</button>
                <button className="t2-btn ghost" disabled={busy} onClick={() => void release("skip")}>Skip</button>
              </>
            )}
          </div>
          {note && <p className="t2-fine">{note}</p>}
        </>
      )}
    </section>
  );
}
