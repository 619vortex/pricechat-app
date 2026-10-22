import { useEffect, useState } from "react";
import { openLogin } from "../../../website/emailLogin";
import { day } from "../format";
import { errorText } from "../portfolio/shared";
import { mkApi, type Comment, type Listing, type RawCondition } from "./api";

const RAW_NAMES: Record<string, string> = { NM: "Near Mint", LP: "Lightly Played", MP: "Moderately Played", HP: "Heavily Played", DMG: "Damaged" };

/** A raw card's condition: the AI review of the seller's photos, and members' own 1-10 grades (never the seller's). */
export function RawConditionBox({ l, signedIn }: { l: Listing; signedIn: boolean }) {
  const [community, setCommunity] = useState<RawCondition["community"]>(l.condition?.community ?? null);
  const [pick, setPick] = useState<number>(l.condition?.community?.mine?.estimate ?? 0);
  const [note, setNote] = useState(l.condition?.community?.mine?.note ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const cond = l.condition;
  if (!cond) return null;
  const ai = cond.ai;
  const range = cond.low !== null && cond.high !== null ? (cond.low === cond.high ? `${cond.low}` : `${cond.low}–${cond.high}`) : "—";
  const save = (n: number) => {
    if (!signedIn) { openLogin(); return; }
    setPick(n); setBusy(true); setErr(null);
    void mkApi.vote(l.id, n, note).then(setCommunity).catch((e) => setErr(errorText(e))).finally(() => setBusy(false));
  };
  return (
    <section className="mk-box mk-rawbox">
      <h3>Condition (raw card)</h3>
      <div className="mk-rawhead">
        <span className="pf-badge pf-badge-raw">RAW {cond.label}</span>
        <div><b>{RAW_NAMES[cond.label] ?? cond.label}</b><small>AI estimate ≈ {range} of 10 · not a professional grade</small></div>
      </div>
      {ai && (
        <details className="pf-report" open>
          <summary>✨ AI review of the seller's photos · {ai.confidence} confidence</summary>
          <ul>
            {ai.centering && <li><b>Centering</b> {ai.centering}</li>}
            {ai.corners && <li><b>Corners</b> {ai.corners}</li>}
            {ai.edges && <li><b>Edges</b> {ai.edges}</li>}
            {ai.surface && <li><b>Surface</b> {ai.surface}</li>}
            {ai.issues && ai.issues.length > 0 && <li><b>Visible issues</b> {ai.issues.join(", ")}</li>}
          </ul>
          <p className="pf-fine">{ai.label ?? "AI estimate: not a professional grade, not authentication"}{ai.at ? ` · ${day(ai.at)}` : ""}.</p>
        </details>
      )}
      <div className="mk-community">
        <div className="mk-community-score">
          <strong>{community?.average ?? "—"}</strong>
          <small>{community?.count ? `members' grade · ${community.count} vote${community.count === 1 ? "" : "s"}` : "no member grades yet"}</small>
        </div>
        {!l.mine && (
          <div className="mk-vote">
            <span className="pf-fine">Your grade, from the photos:</span>
            <div className="mk-vote-row" role="radiogroup" aria-label="Your condition grade">
              {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
                <button key={n} type="button" role="radio" aria-checked={pick === n} className={pick === n ? "is-on" : ""} disabled={busy} onClick={() => save(n)}>{n}</button>
              ))}
            </div>
            {pick > 0 && <input className="mk-vote-note" value={note} maxLength={280} placeholder="Why? (optional: e.g. back edges whitening)"
                                onChange={(e) => setNote(e.target.value)} onBlur={() => pick && note !== (community?.mine?.note ?? "") && save(pick)} />}
          </div>
        )}
      </div>
      {err && <p className="pf-error">{err}</p>}
    </section>
  );
}

/** Questions and remarks on a listing (one reply level). */
export function CommentsBox({ listingId, signedIn }: { listingId: number; signedIn: boolean }) {
  const [list, setList] = useState<Comment[] | null>(null);
  const [body, setBody] = useState("");
  const [replyTo, setReplyTo] = useState<Comment | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { void mkApi.comments(listingId).then((r) => setList(r.comments)).catch(() => setList([])); }, [listingId]);
  const send = () => {
    setBusy(true); setErr(null);
    void mkApi.addComment(listingId, body, replyTo?.id ?? null).then((r) => { setList(r.comments); setBody(""); setReplyTo(null); })
      .catch((e) => setErr(errorText(e))).finally(() => setBusy(false));
  };
  const top = (list ?? []).filter((c) => c.parent_id === null);
  const replies = (id: number) => (list ?? []).filter((c) => c.parent_id === id);
  const row = (c: Comment) => (
    <div className={`mk-comment${c.parent_id ? " is-reply" : ""}`} key={c.id}>
      <div className="mk-comment-head"><b>{c.username}</b>{c.seller && <span className="mk-seller-tag">Seller</span>}<small>{day(c.at)}</small></div>
      <p>{c.deleted ? <i className="pf-muted">Comment removed</i> : c.body}</p>
      <div className="mk-comment-actions">
        {signedIn && !c.parent_id && !c.deleted && <button type="button" className="pf-link" onClick={() => setReplyTo(c)}>Reply</button>}
        {c.mine && !c.deleted && <button type="button" className="pf-link" onClick={() => void mkApi.deleteComment(c.id).then(() => mkApi.comments(listingId)).then((r) => setList(r.comments))}>Delete</button>}
      </div>
    </div>
  );
  return (
    <section className="mk-box mk-comments">
      <h3>Questions & comments{list && list.length > 0 ? ` (${list.filter((c) => !c.deleted).length})` : ""}</h3>
      {list === null ? <p className="pf-muted">Loading…</p> : top.length === 0 ? <p className="pf-muted">No comments yet. Ask the seller about the card.</p>
        : top.map((c) => <div key={c.id}>{row(c)}{replies(c.id).map(row)}</div>)}
      {signedIn ? (
        <form className="mk-comment-form" onSubmit={(e) => { e.preventDefault(); send(); }}>
          {replyTo && <p className="pf-fine">Replying to {replyTo.username} · <button type="button" className="pf-link" onClick={() => setReplyTo(null)}>cancel</button></p>}
          <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={2} maxLength={1000} placeholder="Ask about the card or share what you see in the photos…" />
          <button type="submit" className="pf-btn pf-sm" disabled={busy || body.trim().length < 2}>{busy ? "Posting…" : "Post"}</button>
        </form>
      ) : <button type="button" className="pf-link" onClick={() => openLogin()}>Sign in to comment</button>}
      {err && <p className="pf-error">{err}</p>}
    </section>
  );
}
