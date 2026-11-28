import { useCallback, useEffect, useState } from "react";
import { request } from "../../api/client";
import "./community.css";

/** Terminal › Community: members' tips (COMMUNITY_INTEL_MODE). The checks are automatic (source, independent confirmation, live data,
 *  duplicates, safety flags); every decision is the editor's. Accepting never publishes: an accepted tip joins the Terminal story about
 *  the same event and goes out only through the normal desk when you publish that story. Renders nothing while the feature is off. */

type Fig = { said: string; measured: string; what: string; ok: boolean };
export type CiItem = {
  id: number; ref: string; kind: string; kind_label: string; title: string; note: string; url: string; host: string; via: string; status: string;
  verified: boolean; verified_by: string | null; source: { class: string; label: string; outlet: string } | null; claim: string | null;
  dup: { kind?: string; ref?: string; story?: string; story_key?: string; exact?: boolean }; reason: string | null; reason_label: string | null;
  editor_note: string | null; published_at: string | null; first_spotted: boolean; credit: boolean; disclosure: string | null; created_at: string | null;
  appeal: { status: string; text: string } | null; checks: { flags?: string[]; figures?: { figures?: Fig[]; note?: string; ticker?: string };
    corroboration?: { independent?: string[]; matches?: number; disputed?: boolean } };
  tickers: string[]; forward_from: string | null; story_key: string | null;
  member?: { username: string; name: string; joined_at: string | null; community?: string };
  record?: { sent: number; accepted: number; published: number; first_spotted: number; acceptance: number | null; rejected: number };
};
type Queue = { items: CiItem[]; counts: Record<string, number>; reasons: Record<string, string>; mode: string };

const FILTERS: [string, string][] = [["review", "To review"], ["flagged", "Flagged"], ["appeals", "Appeals"], ["needs_info", "Asked"], ["checking", "Awaiting answer"],
  ["duplicate", "Duplicates"], ["decided", "Decided"]];
const ago = (iso: string | null) => {
  if (!iso) return "";
  const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  return m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
};
const tone = (c?: string) => (c === "official" || c === "outlet" ? "ok" : c === "market" ? "info" : c === "social" || c === "unknown" ? "warn" : "bad");

export function CommunityLane() {
  const [filter, setFilter] = useState("review");
  const [q, setQ] = useState<Queue | null | false>(null);
  const [sel, setSel] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const load = useCallback(async () => {
    try {
      const r = await request<Queue>(`/community-intel/queue?filter=${filter}`);
      setQ(r && Array.isArray(r.items) && r.counts ? r : false);  // anything else: the feature is off or the reply isn't the queue
    } catch { setQ(false); }
  }, [filter]);
  useEffect(() => { void load(); const id = window.setInterval(() => void load(), 60_000); return () => window.clearInterval(id); }, [load]);
  if (q === false || q === null) return null;
  const items = q.items;
  const cur = items.find((i) => i.id === sel) ?? items[0] ?? null;
  const waiting = (q.counts.review ?? 0) + (q.counts.appeals ?? 0);

  async function act(path: string, body: unknown, ok: string) {
    setBusy(true); setMsg(null);
    try { await request(path, { method: "POST", body: JSON.stringify(body) }); setMsg({ ok: true, text: ok }); await load(); }
    catch (e) { setMsg({ ok: false, text: String((e as Error)?.message ?? e).replace(/^\d{3}:?\s*/, "") }); }
    finally { setBusy(false); }
  }

  return (
    <section className="ci" aria-labelledby="ci-h">
      <div className="ci-head">
        <div><h2 id="ci-h">Community <span className="ci-count">{waiting}</span></h2>
          <p className="t2-meta">Members' tips. The checks are automatic; every decision is yours. Accepting publishes nothing.</p></div>
        <div className="t2-chips" role="tablist" aria-label="Community filter">
          {FILTERS.map(([k, l]) => {
            const n = k === "appeals" ? q.counts.appeals : k === "decided" ? (q.counts.accepted ?? 0) + (q.counts.merged ?? 0) + (q.counts.rejected ?? 0) : k === "flagged" ? undefined : q.counts[k];
            return <button key={k} role="tab" aria-selected={filter === k} className={`t2-chip ${filter === k ? "on" : ""}`} onClick={() => { setFilter(k); setSel(null); }}>
              {l}{n ? <b> {n}</b> : null}</button>;
          })}
        </div>
      </div>
      {items.length === 0 ? <p className="t2-empty">Nothing here right now.</p> : (
        <div className="ci-body">
          <ul className="ci-list" role="listbox" aria-label="Tips">
            {items.map((i) => (
              <li key={i.id}>
                <button type="button" role="option" aria-selected={cur?.id === i.id} className="ci-item" onClick={() => { setSel(i.id); setMsg(null); }}>
                  <span className="ci-k">{i.kind_label} · {i.ref} · {ago(i.created_at)}</span>
                  <b>{i.title || i.host || "Forwarded post"}</b>
                  <span className="ci-chips">
                    <span className={`ci-chip ${tone(i.source?.class)}`}>{i.source?.label ?? "No link"}</span>
                    <span className={`ci-chip ${i.verified ? "ok" : "warn"}`}>{i.verified ? "Confirmed" : "Unconfirmed"}</span>
                    {i.dup.kind && i.dup.kind !== "new" && <span className="ci-chip info">{i.dup.kind === "tip" ? `Like ${i.dup.ref}` : i.dup.kind === "covered" ? "Already covered" : "In Terminal"}</span>}
                    {(i.checks.flags ?? []).length > 0 && <span className="ci-chip bad">{i.checks.flags!.length} flag{i.checks.flags!.length > 1 ? "s" : ""}</span>}
                    {i.disclosure === "holds" && <span className="ci-chip warn">Holds a position</span>}
                    {i.appeal?.status === "open" && <span className="ci-chip warn">Appeal</span>}
                    <span className="ci-chip">{i.member?.name ?? "member"}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {cur && <Detail i={cur} reasons={q.reasons} busy={busy} msg={msg} act={act} />}
        </div>
      )}
    </section>
  );
}

function Detail({ i, reasons, busy, msg, act }: { i: CiItem; reasons: Record<string, string>; busy: boolean; msg: { ok: boolean; text: string } | null;
  act: (path: string, body: unknown, ok: string) => Promise<void> }) {
  const [note, setNote] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [dataChecked, setDataChecked] = useState(false);
  const [newInfo, setNewInfo] = useState(false);
  const [fact, setFact] = useState(false);
  useEffect(() => { setNote(""); setRejecting(false); setDataChecked(false); setNewInfo(false); setFact(false); }, [i.id]);
  const open = ["review", "needs_info", "checking"].includes(i.status) || (i.status === "duplicate");
  const cor = i.checks.corroboration ?? {};
  const figs = i.checks.figures?.figures ?? [];
  const r = i.record;
  const decide = (action: string, extra: Record<string, unknown> = {}, ok = "Done.") =>
    act(`/community-intel/${i.id}/decide`, { action, note: note || undefined, ...extra }, ok);
  return (
    <article className="ci-detail" aria-label={`${i.ref} details`}>
      <header className="ci-d-head">
        <span className="ci-k">{i.kind_label} · {i.ref} · via {i.via === "telegram" ? "Telegram" : "Passport"}{i.forward_from ? ` · forwarded from ${i.forward_from}` : ""}</span>
        <h3>{i.title || "Forwarded post"}</h3>
        {i.member && <p className="t2-meta">From <b>{i.member.name}</b> · community {i.member.community ?? "—"}
          {r ? ` · ${r.accepted} of ${r.sent} accepted${r.acceptance != null ? ` (${Math.round(r.acceptance * 100)}%)` : ""} · ${r.published} published · ${r.first_spotted} first spotted` : ""}</p>}
      </header>
      {i.note && <p className="ci-quote">{i.note}</p>}
      {i.url && <p className="ci-url"><a href={i.url} target="_blank" rel="noopener noreferrer nofollow">{i.url}</a></p>}
      <div className="ci-grid">
        <div className="ci-panel"><h4>Source and confirmation</h4>
          <Line tone={tone(i.source?.class)} title={`${i.source?.label ?? "No link"}${i.source?.outlet ? ` · ${i.source.outlet}` : ""}`} sub="From the link's address; the link itself is never fetched" />
          <Line tone={i.verified ? "ok" : cor.disputed ? "bad" : "warn"} title={i.verified ? `Confirmed: ${i.verified_by}` : cor.disputed ? "Disputed by a source" : "Not independently confirmed yet"}
            sub={(cor.independent ?? []).length ? `Also reported by ${cor.independent!.join(", ")}` : `${cor.matches ?? 0} related items in the Terminal`} />
          <Line tone={i.disclosure === "holds" ? "warn" : i.disclosure ? "ok" : "bad"} title={i.disclosure === "holds" ? "The member holds a position" : i.disclosure ? "No position held" : "Conflict question not answered yet"}
            sub="Shown to the editor only" />
        </div>
        <div className="ci-panel"><h4>Duplicates, figures and flags</h4>
          <Line tone={!i.dup.kind || i.dup.kind === "new" ? "ok" : "info"} title={!i.dup.kind || i.dup.kind === "new" ? "New: nothing like it yet" : i.dup.kind === "tip" ? `${i.dup.exact ? "Same link as" : "Similar to"} ${i.dup.ref}` : i.dup.kind === "covered" ? "Already covered before this tip" : "Matches a Terminal story"}
            sub={i.dup.story ?? ""} />
          {figs.map((f, n) => <Line key={n} tone={f.ok ? "ok" : "bad"} title={`${f.what} ${f.said}`} sub={`Measured ${f.measured}${f.ok ? "" : ": doesn't match"}`} />)}
          {i.checks.figures?.note && <Line tone="info" title={i.checks.figures.note} sub="" />}
          {(i.checks.flags ?? []).map((f) => <Line key={f} tone="bad" title={f} sub="Flagged at intake" />)}
        </div>
      </div>
      {i.appeal && (
        <div className={`ci-appeal ${i.appeal.status}`}>
          <b>Appeal {i.appeal.status === "open" ? "waiting" : i.appeal.status}</b><p>{i.appeal.text}</p>
          {i.appeal.status === "open" && <div className="t2-actions">
            <button className="t2-btn primary sm" disabled={busy} onClick={() => void act(`/community-intel/${i.id}/appeal`, { uphold: true, note }, "Appeal upheld: back in the queue, any penalty removed.")}>Uphold</button>
            <button className="t2-btn sm" disabled={busy} onClick={() => void act(`/community-intel/${i.id}/appeal`, { uphold: false, note }, "Appeal not upheld.")}>Deny</button></div>}
        </div>
      )}
      {i.status !== "review" && i.status !== "checking" && i.status !== "needs_info" && i.status !== "duplicate" && (
        <p className="ci-state">Decided: <b>{i.status}</b>{i.reason_label ? ` · ${i.reason_label}` : ""}{i.published_at ? " · story published" : ""}{i.first_spotted ? " · first spotted" : ""}</p>
      )}
      <div className="ci-decide">
        <label className="ci-note" htmlFor={`ci-note-${i.id}`}>Note to the member (optional; required to ask a question)</label>
        <textarea id={`ci-note-${i.id}`} value={note} maxLength={400} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Which exchange was it, and when?" />
        {(i.kind === "research" || i.kind === "discovery") && <label className="ci-tick"><input type="checkbox" checked={dataChecked} onChange={(e) => setDataChecked(e.target.checked)} /> I checked the data or tool myself (counts as independent verification)</label>}
        {i.dup.kind && i.dup.kind !== "new" && <label className="ci-tick"><input type="checkbox" checked={newInfo} onChange={(e) => setNewInfo(e.target.checked)} /> It adds something the story didn't have</label>}
        <label className="ci-tick"><input type="checkbox" checked={fact} onChange={(e) => setFact(e.target.checked)} /> It shows a circulating claim was false (Fact Checker)</label>
        {open ? (
          <div className="t2-actions">
            <button className="t2-btn primary" disabled={busy || !i.disclosure} onClick={() => void decide("accept", { data_checked: dataChecked, fact_check: fact }, "Accepted. Nothing was published.")}>Accept as lead</button>
            {i.dup.kind && i.dup.kind !== "new" && <button className="t2-btn" disabled={busy || !i.disclosure} onClick={() => void decide("merge", { new_info: newInfo }, "Merged into the story.")}>Merge into story</button>}
            <button className="t2-btn" disabled={busy || !note.trim()} onClick={() => void decide("ask", {}, "Question sent to the member.")}>Ask for more</button>
            <button className="t2-btn danger" disabled={busy} onClick={() => setRejecting(!rejecting)}>Reject…</button>
          </div>
        ) : <div className="t2-actions"><button className="t2-btn sm" disabled={busy} onClick={() => void decide("reopen", {}, "Reopened.")}>Reopen</button></div>}
        {rejecting && open && (
          <div className="ci-reasons" role="group" aria-label="Reason">
            {Object.entries(reasons).map(([k, l]) => (
              <button key={k} className={`t2-chip ${["promotional", "misleading"].includes(k) ? "ci-spam" : ""}`} disabled={busy}
                      onClick={() => void decide("reject", { reason: k }, `Rejected: ${l}.`)}>{l}{["promotional", "misleading"].includes(k) ? " · −50" : ""}</button>
            ))}
          </div>
        )}
        {msg && <p className={msg.ok ? "ci-ok" : "ci-bad"} role="status">{msg.text}</p>}
      </div>
    </article>
  );
}

function Line({ tone, title, sub }: { tone: string; title: string; sub: string }) {
  return <div className="ci-line"><span className={`ci-ic ${tone}`} aria-hidden="true">{tone === "ok" ? "✓" : tone === "bad" ? "!" : tone === "warn" ? "?" : "i"}</span>
    <div><b>{title}</b>{sub && <span>{sub}</span>}</div></div>;
}
