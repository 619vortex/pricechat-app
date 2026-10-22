import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { MemberApiError, useSession } from "../../../website/members";
import { openLogin } from "../../../website/emailLogin";
import { DASH, day, usd } from "../format";
import { Change, MarketShell, usePublicMarket } from "../mx";
import { portfolioApi, shrink, type Candidate, type Group, type Holding, type HoldingInput, type PCardBrief, type QuoteValue, type Status } from "./api";

export const SOURCE_LABEL: Record<string, string> = {
  sales: "Verified sales", scrydex: "Scrydex estimate", scrydex_raw: "Ungraded market prices", none: "No market data",
  comparable: "Comparable grades (estimate)", grade_equivalent: "Graded-price equivalent (estimate)",
};
/** what a portfolio entry's ownership rests on (a portfolio entry alone never proves ownership) */
export const VERIFICATION: Record<string, string> = {
  self_reported: "Self-reported holding.", vaulted: "Verified: held for you by the marketplace's vault partner.",
  marketplace: "Marketplace-owned card.", redeemed: "Redeemed from the marketplace vault and shipped to you.", sandbox: "SANDBOX: a simulated purchase, not a real card.",
};
export const GRADES: Record<"PSA" | "BGS" | "CGC", string[]> = {
  PSA: ["10", "9", "8.5", "8", "7.5", "7", "6.5", "6", "5.5", "5", "4.5", "4", "3.5", "3", "2.5", "2", "1.5", "1"],
  BGS: ["10", "9.5", "9", "8.5", "8", "7.5", "7", "6.5", "6", "5.5", "5", "4.5", "4", "3", "2", "1"],
  CGC: ["10", "9.5", "9", "8.5", "8", "7.5", "7", "6.5", "6", "5.5", "5", "4.5", "4", "3.5", "3", "2.5", "2", "1.5", "1"],
};

export function errorText(e: unknown): string {
  return e instanceof MemberApiError ? e.message : "Something went wrong. Check your connection and try again.";
}

/** The portfolio pages: inside the market frame, with their own small nav. */
export function PortfolioFrame({ crumbs, children }: { crumbs: [string, string | null][]; children: ReactNode }) {
  return (
    <MarketShell crumbs={crumbs} compact>
      <nav className="pf-tabs" aria-label="Portfolio">
        <NavLink to="/pokemon/portfolio" end>✨ My Portfolio</NavLink>
        <NavLink to="/pokemon/portfolio/add">＋ Add Cards</NavLink>
        <NavLink to="/pokemon/portfolios">Explore Collectors</NavLink>
      </nav>
      {children}
    </MarketShell>
  );
}

/** Signed out: what the portfolio is, and the way in (the existing Kiwi Passport sign-in). */
export function SignInPrompt({ title = "Start your Pokémon Portfolio" }: { title?: string }) {
  return (
    <section className="pf-signin">
      <div className="pf-holo-card" aria-hidden><span>✨</span></div>
      <h1>{title}</h1>
      <p>Scan your cards with your phone, see your collection's estimated value from real market data, and share it with the Price Chat
        community. Free with your Kiwi Passport.</p>
      <button type="button" className="pf-shiny" onClick={() => openLogin()}>Sign in to continue</button>
      <p className="pf-fine">New here? The same button creates your account. <Link to="/pokemon/portfolios">Explore public collections</Link></p>
    </section>
  );
}

/** undefined while loading, null when signed out */
export function useMember() {
  return useSession();
}

export function useStatus(): Status | null {
  const [s, setS] = useState<Status | null>(null);
  useEffect(() => {
    void portfolioApi.status().then(setS).catch(() => setS(null));
  }, []);
  return s;
}

export function GradeBadge({ h }: { h: Pick<Holding, "kind" | "label" | "grading_company"> }) {
  return <span className={`pf-badge pf-badge-${h.kind === "graded" ? (h.grading_company ?? "").toLowerCase() : "raw"}`}>{h.label}</span>;
}

/** Point estimate + range, with its source and confidence in the title. "Price unavailable" when there is none. */
export function ValueCell({ h, total = false }: { h: Holding; total?: boolean }) {
  const v = h.value;
  if (!v) return null;
  if (v.each === null) return <span className="pf-unavail" title={v.note ?? undefined}>Price unavailable</span>;
  const range = v.low !== null && v.high !== null && v.low !== v.high;
  return (
    <span className="pf-value" title={[v.note, v.valued_at ? `Valued ${day(v.valued_at)}` : ""].filter(Boolean).join(" · ")}>
      <b>{usd(total ? v.total : v.each)}</b>
      {range && !total && <small>{usd(v.low, true)}–{usd(v.high, true)}</small>}
      {v.confidence && <i className={`pf-conf pf-conf-${v.confidence}`}>{v.confidence}</i>}
    </span>
  );
}

export function CardArt({ card, photoId, className = "" }: { card: PCardBrief; photoId?: number; className?: string }) {
  const src = photoId ? portfolioApi.photoUrl(photoId) : card.image_url;
  return src ? <img className={`pf-art ${className}`} src={src} alt={`${card.name}, ${card.set_name}`} loading="lazy" decoding="async" />
    : <div className={`pf-art pf-noart ${className}`}>{card.name}</div>;
}

export function cardLine(c: PCardBrief): string {
  return [c.year, c.set_name, c.card_number, c.edition !== "—" ? c.edition : null, c.variant || null, c.language !== "English" ? c.language : null]
    .filter(Boolean).join(" · ");
}

export function HoldingTile({ h, onOpen, showChanges = true }: { h: Holding; onOpen: (h: Holding) => void; showChanges?: boolean }) {
  const front = h.photos.find((p) => p.side === "front");
  return (
    <button type="button" className={`pf-tile${h.kind === "graded" ? " is-graded" : ""}`} onClick={() => onOpen(h)}>
      <div className="pf-tile-art"><CardArt card={h.card} photoId={front?.id} />{h.quantity > 1 && <span className="pf-qty">×{h.quantity}</span>}</div>
      <div className="pf-tile-body">
        <strong>{h.card.name}{h.source === "giveaway" && <span className="pf-virtual" title="A virtual card from the Pack Giveaway: not owned, not real">🎁 Virtual</span>}</strong>
        <span className="pf-muted">{h.card.year} · {h.card.set_name}</span>
        <div className="pf-tile-row"><GradeBadge h={h} />{h.value && <ValueCell h={h} />}</div>
        {showChanges && h.changes && (h.changes["30d"] != null || h.changes["90d"] != null) && (
          <div className="pf-tile-row pf-small"><span>30D <Change v={h.changes["30d"]} /></span><span>90D <Change v={h.changes["90d"]} /></span></div>
        )}
      </div>
    </button>
  );
}

export function CompositionBars({ title, groups, total }: { title: string; groups: Group[]; total: number | null }) {
  const max = Math.max(1, ...groups.map((g) => g.value));
  return (
    <div className="pf-comp">
      <h3>{title}</h3>
      <ul>
        {groups.slice(0, 6).map((g) => (
          <li key={g.key}>
            <span className="pf-comp-key">{g.key}</span>
            <span className="pf-comp-bar"><i style={{ width: `${(g.value / max) * 100}%` }} /></span>
            <span className="pf-comp-val">{total ? `${Math.round((g.value / total) * 100)}%` : `${g.copies}`}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Raw or graded, grade, quantity, optional details. Used after a scan and after a search. */
export function AddForm({ card, initial, onSaved, onCancel, source = "manual" }: {
  card: PCardBrief; initial?: Partial<HoldingInput>; onSaved: (h: Holding) => void; onCancel?: () => void; source?: "manual" | "scan";
}) {
  const [kind, setKind] = useState<"raw" | "graded">(initial?.kind ?? "raw");
  const [company, setCompany] = useState<"PSA" | "BGS" | "CGC">((initial?.grading_company as "PSA" | "BGS" | "CGC") ?? "PSA");
  const [grade, setGrade] = useState(initial?.grade ?? "");
  const [qty, setQty] = useState(1);
  const [cert, setCert] = useState(initial?.cert_number ?? "");
  const [more, setMore] = useState(false);
  const [price, setPrice] = useState("");
  const [date, setDate] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [quote, setQuote] = useState<QuoteValue | null | undefined>(undefined);
  useEffect(() => {
    if (kind === "graded" && !grade) { setQuote(undefined); return; }
    let alive = true;
    setQuote(undefined);
    void portfolioApi.quote(card.id, kind, kind === "graded" ? company : null, kind === "graded" ? grade : null)
      .then((q) => alive && setQuote(q)).catch(() => alive && setQuote(null));
    return () => { alive = false; };
  }, [card.id, kind, company, grade]);
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const dollars = price.trim() ? Number(price.replace(/[$,\s]/g, "")) : null;
      const h = await portfolioApi.add({
        card_id: card.id, kind, grading_company: kind === "graded" ? company : null, grade: kind === "graded" ? grade : null, quantity: qty,
        cert_number: kind === "graded" && cert.trim() ? cert.trim() : null,
        purchase_price: dollars !== null && Number.isFinite(dollars) ? Math.round(dollars * 100) : null, purchase_date: date || null,
        notes, source,
      });
      onSaved(h);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="pf-form" onSubmit={(e) => { e.preventDefault(); if (!busy && (kind === "raw" || grade)) void save(); }}>
      <div className="pf-seg" role="radiogroup" aria-label="Raw or graded">
        <button type="button" role="radio" aria-checked={kind === "raw"} className={kind === "raw" ? "is-on" : ""} onClick={() => setKind("raw")}>Raw</button>
        <button type="button" role="radio" aria-checked={kind === "graded"} className={kind === "graded" ? "is-on" : ""} onClick={() => setKind("graded")}>Graded</button>
      </div>
      {kind === "raw" ? (
        <p className="pf-fine">Raw = not professionally graded. Valued from real ungraded market prices as a conservative range; you can estimate its
          condition later (✨ AI or your own).</p>
      ) : (
        <div className="pf-row">
          <label>Company
            <select value={company} onChange={(e) => { setCompany(e.target.value as "PSA" | "BGS" | "CGC"); setGrade(""); }}>
              <option>PSA</option><option>BGS</option><option>CGC</option>
            </select>
          </label>
          <label>Official grade
            <select value={grade} onChange={(e) => setGrade(e.target.value)} required>
              <option value="">Choose</option>
              {GRADES[company].map((g) => <option key={g} value={g}>{g}</option>)}
            </select>
          </label>
        </div>
      )}
      <div className="pf-row">
        <label>Quantity
          <span className="pf-stepper">
            <button type="button" aria-label="Fewer" onClick={() => setQty((q) => Math.max(1, q - 1))}>−</button>
            <input inputMode="numeric" value={qty} onChange={(e) => setQty(Math.max(1, Math.min(999, Number(e.target.value.replace(/\D/g, "")) || 1)))} />
            <button type="button" aria-label="More" onClick={() => setQty((q) => Math.min(999, q + 1))}>＋</button>
          </span>
        </label>
        {kind === "graded" && <label>Cert number <em>(optional)</em><input value={cert} onChange={(e) => setCert(e.target.value)} maxLength={32} inputMode="numeric" /></label>}
      </div>
      <button type="button" className="pf-link" onClick={() => setMore(!more)}>{more ? "Hide" : "Add"} purchase details & notes (private)</button>
      {more && (
        <div className="pf-row">
          <label>Purchase price (USD)<input inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="e.g. 250" /></label>
          <label>Purchase date<input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
          <label className="pf-wide">Notes<input value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={500} /></label>
        </div>
      )}
      <div className="pf-quote" aria-live="polite">
        {kind === "graded" && !grade ? <span className="pf-muted">Choose the grade to see its estimate.</span>
          : quote === undefined ? <span className="pf-muted">Estimating…</span>
          : quote === null ? <span className="pf-muted">Estimate unavailable right now.</span>
          : quote.value === null ? <span className="pf-unavail">Price unavailable<small>{quote.note}</small></span> : (
            <>
              <span className="pf-quote-label">Estimated value{qty > 1 ? " each" : ""}</span>
              <strong>{usd(quote.value)}</strong>
              {quote.low !== null && quote.high !== null && quote.low !== quote.high && <span className="pf-range">Range {usd(quote.low)} – {usd(quote.high)}</span>}
              {qty > 1 && <span className="pf-muted">{qty} cards ≈ {usd(quote.value * qty)}</span>}
              <small className="pf-fine">{SOURCE_LABEL[quote.source] ?? quote.source} · {quote.confidence ?? "—"} confidence. {quote.note}</small>
            </>
          )}
      </div>
      {err && <p className="pf-error" role="alert">{err}</p>}
      <div className="pf-actions">
        {onCancel && <button type="button" className="pf-btn" onClick={onCancel}>Back</button>}
        <button type="submit" className="pf-shiny" disabled={busy || (kind === "graded" && !grade)}>{busy ? "Adding…" : "Add to Portfolio"}</button>
      </div>
    </form>
  );
}

/** One holding, opened: photos, value and its basis, condition (AI / owner / community: kept apart), edit, remove. */
export function HoldingSheet({ h: initial, owner, onClose, onChanged, status }: {
  h: Holding; owner: boolean; onClose: () => void; onChanged?: () => void; status?: Status | null;
}) {
  const [h, setH] = useState<Holding>(initial);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [side, setSide] = useState<"front" | "back" | "closeup">("front");
  const [vote, setVote] = useState<number | null>(null);
  useEffect(() => {
    void portfolioApi.holding(initial.id).then(setH).catch(() => undefined);
  }, [initial.id]);
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", esc);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", esc); document.body.style.overflow = prev; };
  }, [onClose]);
  const act = async (label: string, fn: () => Promise<unknown>, done?: string) => {
    setBusy(label);
    setErr(null);
    setMsg(null);
    try {
      await fn();
      if (done) setMsg(done);
      onChanged?.();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(null);
    }
  };
  const refresh = async () => setH(await portfolioApi.holding(h.id));
  const upload = (file: File) => act("photo", async () => { await portfolioApi.uploadPhoto(h.id, await shrink(file, 2000, 0.9), side); await refresh(); }, "Photo added.");
  const v = h.value;
  const report = h.condition_report;
  return (
    <div className="pf-sheet-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="pf-sheet" role="dialog" aria-modal="true" aria-label={h.card.name}>
        <button type="button" className="pf-x" aria-label="Close" onClick={onClose}>×</button>
        <div className="pf-sheet-top">
          <div className="pf-sheet-art"><CardArt card={h.card} photoId={h.photos.find((p) => p.side === "front")?.id} /></div>
          <div>
            <h2>{h.card.name}</h2>
            <p className="pf-muted">{cardLine(h.card)}</p>
            <p><GradeBadge h={h} /> {h.quantity > 1 && <b>×{h.quantity}</b>} {h.cert_status === "found" && <span className="pf-verified">Cert found</span>}</p>
            {v && (v.each !== null ? (
              <div className="pf-sheet-value">
                <strong>{usd(v.each)}</strong>{h.quantity > 1 && <span> each · {usd(v.total)} total</span>}
                {v.low !== null && v.high !== null && v.low !== v.high && <p className="pf-range">Range {usd(v.low)} – {usd(v.high)}</p>}
                <p className="pf-fine">{SOURCE_LABEL[v.source ?? "none"]} · {v.confidence ?? "—"} confidence{v.valued_at ? ` · ${day(v.valued_at)}` : ""}<br />{v.note}</p>
              </div>
            ) : <p className="pf-unavail">Price unavailable<br /><small>{v.note}</small>{v.valued_at && <small className="pf-fine"><br />Last checked {day(v.valued_at)}</small>}</p>)}
            <p className="pf-fine">Estimated from market data, not a guaranteed sale price. {VERIFICATION[h.verification] ?? ""}</p>
            <Link className="pf-link" to={`/pokemon/cards/${h.card.id}?grade=${encodeURIComponent(h.kind === "graded" ? `${h.grading_company} ${h.grade}` : "RAW NM")}`}>View Market Data →</Link>
          </div>
        </div>

        <div className="pf-photos">
          {h.photos.map((p) => (
            <figure key={p.id}>
              <a href={portfolioApi.photoUrl(p.id, "full")} target="_blank" rel="noreferrer"><img src={portfolioApi.photoUrl(p.id)} alt={`${p.side} photo`} loading="lazy" /></a>
              <figcaption>{p.side}{owner && <button type="button" aria-label={`Delete ${p.side} photo`} onClick={() => void act("del", async () => { await portfolioApi.deletePhoto(p.id); await refresh(); })}>×</button>}</figcaption>
            </figure>
          ))}
          {owner && status?.photos !== false && (
            <div className="pf-addphoto">
              <select value={side} onChange={(e) => setSide(e.target.value as typeof side)} aria-label="Which side">
                <option value="front">Front</option><option value="back">Back</option><option value="closeup">Close-up</option>
              </select>
              <button type="button" className="pf-btn" disabled={busy === "photo"} onClick={() => fileRef.current?.click()}>{busy === "photo" ? "Uploading…" : "📷 Add photo"}</button>
              <input ref={fileRef} type="file" accept="image/*" capture="environment" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} />
            </div>
          )}
        </div>

        {h.kind === "raw" && (
          <div className="pf-condition">
            <h3>Condition</h3>
            {h.condition ? (
              <p><b>Estimated condition: approximately {h.condition.low}{h.condition.high !== h.condition.low ? `–${h.condition.high}` : ""} equivalent</b>
                <span className="pf-tag">{h.condition.source === "ai" ? "AI estimate" : "Owner's estimate"}</span><br />
                <small>Not professionally graded or authenticated.</small></p>
            ) : <p className="pf-muted">Not assessed yet: valued as a conservative range.</p>}
            {report && (
              <details className="pf-report" open>
                <summary>✨ AI review · {report.confidence} confidence</summary>
                <ul>
                  <li><b>Centering</b> {report.centering}</li><li><b>Corners</b> {report.corners}</li>
                  <li><b>Edges</b> {report.edges}</li><li><b>Surface</b> {report.surface}</li>
                  {report.issues.length > 0 && <li><b>Visible issues</b> {report.issues.join(", ")}</li>}
                  {report.more_photos.length > 0 && <li><b>For a surer estimate</b> {report.more_photos.join("; ")}</li>}
                </ul>
                <p className="pf-fine">{report.label}.</p>
              </details>
            )}
            {owner && (
              <div className="pf-actions">
                {status?.condition !== false && (
                  <button type="button" className="pf-shiny pf-sm" disabled={busy === "ai"} onClick={() => void act("ai", async () => {
                    const r = await portfolioApi.condition(h.id);
                    if (r.status === "need_photos") throw new MemberApiError(400, r.message ?? "Add front and back photos first.");
                    if (r.holding) setH(r.holding);
                  }, "Condition estimated.")}>{busy === "ai" ? "Reviewing photos…" : "✨ Estimate Condition"}</button>
                )}
                <ConditionEditor h={h} onSave={(lo, hi) => void act("cond", async () => setH(await portfolioApi.update(h.id, { condition_low: lo, condition_high: hi })), "Saved.")} />
              </div>
            )}
            {h.community && (
              <div className="pf-community">
                <h4>Community opinion <small>{h.community.label}</small></h4>
                {h.community.distribution ? (
                  <ul className="pf-dist">{h.community.distribution.map((d) => <li key={d.estimate}><span>{d.estimate}</span><i style={{ width: `${d.share}%` }} /><b>{d.share}%</b></li>)}</ul>
                ) : <p className="pf-muted">{h.community.responses} of {h.community.min_responses} opinions needed before results show.</p>}
                {!owner && (
                  <div className="pf-vote">
                    <span>What grade could it receive?</span>
                    {[4, 5, 6, 7, 8, 9].map((n) => <button key={n} type="button" className={(vote ?? h.community?.mine) === n ? "is-on" : ""}
                      onClick={() => void act("vote", async () => { setVote(n); const c = await portfolioApi.vote(h.id, n); setH({ ...h, community: c }); }, "Thanks for your opinion.")}>{n}</button>)}
                  </div>
                )}
              </div>
            )}
            {owner && h.is_public && (
              <label className="pf-check"><input type="checkbox" checked={h.community_review}
                onChange={(e) => void act("cr", async () => setH(await portfolioApi.update(h.id, { community_review: e.target.checked })))} />
                Ask the community what grade it could get (public portfolio)</label>
            )}
          </div>
        )}

        {owner && <OwnerControls h={h} setH={setH} act={act} busy={busy} onClose={onClose} />}
        {msg && <p className="pf-ok" role="status">{msg}</p>}
        {err && <p className="pf-error" role="alert">{err}</p>}
      </section>
    </div>
  );
}

function ConditionEditor({ h, onSave }: { h: Holding; onSave: (lo: number | null, hi: number | null) => void }) {
  const [open, setOpen] = useState(false);
  const [lo, setLo] = useState(h.condition?.low ?? 5);
  const [hi, setHi] = useState(h.condition?.high ?? 7);
  if (!open) return <button type="button" className="pf-btn pf-sm" onClick={() => setOpen(true)}>Set my own estimate</button>;
  return (
    <span className="pf-condedit">
      ~<select value={lo} onChange={(e) => setLo(Number(e.target.value))}>{[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => <option key={n}>{n}</option>)}</select>
      to <select value={hi} onChange={(e) => setHi(Number(e.target.value))}>{[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => <option key={n}>{n}</option>)}</select> equivalent
      <button type="button" className="pf-btn pf-sm" onClick={() => { onSave(Math.min(lo, hi), Math.max(lo, hi)); setOpen(false); }}>Save</button>
      {h.condition && <button type="button" className="pf-link" onClick={() => { onSave(null, null); setOpen(false); }}>Clear</button>}
    </span>
  );
}

function OwnerControls({ h, setH, act, busy, onClose }: {
  h: Holding; setH: (h: Holding) => void; act: (label: string, fn: () => Promise<unknown>, done?: string) => Promise<void>; busy: string | null; onClose: () => void;
}) {
  const isPublic = usePublicMarket();  // the simulated marketplace is retired from the public site
  const [qty, setQty] = useState(h.quantity);
  const [confirm, setConfirm] = useState(false);
  const [editId, setEditId] = useState(false);
  return (
    <div className="pf-owner">
      {editId ? <IdentityEditor h={h} onCancel={() => setEditId(false)} onSave={(ch) => void act("id", async () => { setH(await portfolioApi.update(h.id, ch)); setEditId(false); }, "Identification updated. Photos and history kept.")} busy={busy === "id"} />
        : <button type="button" className="pf-btn" onClick={() => setEditId(true)}>✏️ Edit Identification</button>}
      <div className="pf-row">
        <label>Quantity
          <span className="pf-stepper">
            <button type="button" onClick={() => setQty(Math.max(1, qty - 1))}>−</button>
            <input inputMode="numeric" value={qty} onChange={(e) => setQty(Math.max(1, Math.min(999, Number(e.target.value.replace(/\D/g, "")) || 1)))} />
            <button type="button" onClick={() => setQty(Math.min(999, qty + 1))}>＋</button>
          </span>
        </label>
        {qty !== h.quantity && <button type="button" className="pf-btn" onClick={() => void act("q", async () => setH(await portfolioApi.update(h.id, { quantity: qty })), "Saved.")}>Save quantity</button>}
        <label className="pf-check"><input type="checkbox" checked={h.is_public} onChange={(e) => void act("pub", async () => setH(await portfolioApi.update(h.id, { is_public: e.target.checked })))} /> Show in my public portfolio</label>
      </div>
      {h.purchase_price != null && <p className="pf-muted">Bought for {usd(h.purchase_price)}{h.purchase_date ? ` on ${day(h.purchase_date)}` : ""} (private)</p>}
      <div className="pf-actions">
        {h.kind === "graded" && !isPublic && <Link className="pf-shiny pf-sm" to={`/pokemon/marketplace/sell?holding=${h.id}`}>🛒 Sell This Card</Link>}
        <button type="button" className="pf-btn" disabled={!!busy} onClick={() => void act("dup", () => portfolioApi.duplicate(h.id), "Added another copy as a separate entry.")}>Duplicate</button>
        {!confirm ? <button type="button" className="pf-btn pf-danger" onClick={() => setConfirm(true)}>Remove</button>
          : <button type="button" className="pf-btn pf-danger" onClick={() => void act("rm", async () => { await portfolioApi.remove(h.id); onClose(); })}>Confirm remove</button>}
      </div>
    </div>
  );
}

/** Re-identify a saved holding (wrong printing, set, grade…): the same holding, photos and history; only what it IS changes. */
function IdentityEditor({ h, onSave, onCancel, busy }: {
  h: Holding; busy: boolean; onCancel: () => void;
  onSave: (ch: { card_id?: number; kind?: "raw" | "graded"; grading_company?: "PSA" | "BGS" | "CGC" | null; grade?: string | null }) => void;
}) {
  const [q, setQ] = useState(`${h.card.name} ${h.card.set_name}`);
  const [list, setList] = useState<Candidate[] | null>(null);
  const [card, setCard] = useState<PCardBrief>(h.card);
  const [kind, setKind] = useState(h.kind);
  const [company, setCompany] = useState<"PSA" | "BGS" | "CGC">(h.grading_company ?? "PSA");
  const [grade, setGrade] = useState(h.grade ?? "9");
  const [err, setErr] = useState<string | null>(null);
  const search = async () => {
    setErr(null);
    try { setList((await portfolioApi.search(q)).results); } catch (e) { setErr(errorText(e)); }
  };
  useEffect(() => { void search(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const changed = card.id !== h.card.id || kind !== h.kind || (kind === "graded" && (company !== h.grading_company || grade !== h.grade));
  return (
    <div className="pf-identity">
      <h3>Edit Identification</h3>
      <p className="pf-fine">Pick the exact printing (check the slab label or card for “1st Edition”, “Reverse Foil”, set symbol and number). Photos and history are kept.</p>
      <form className="pf-row" onSubmit={(e) => { e.preventDefault(); void search(); }}>
        <input value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search cards" placeholder="Name, set, number" />
        <button type="submit" className="pf-btn">Search</button>
      </form>
      {err && <p className="pf-error">{err}</p>}
      {list && (
        <ul className="pf-idlist">
          {list.slice(0, 24).map((c) => (
            <li key={c.card.id}>
              <button type="button" className={c.card.id === card.id ? "is-on" : ""} onClick={() => setCard(c.card)}>
                {c.card.image_url ? <img src={c.card.image_url} alt="" loading="lazy" /> : <span className="pf-qr-noimg" />}
                <span><b>{c.card.name}</b><small>{cardLine(c.card)}</small></span>
                {c.card.id === h.card.id && <i className="pf-tag">current</i>}
              </button>
            </li>
          ))}
          {list.length === 0 && <li className="pf-muted">No cards found. Try the set name or card number.</li>}
        </ul>
      )}
      <div className="pf-row">
        <label>Type <select value={kind} onChange={(e) => setKind(e.target.value as "raw" | "graded")}><option value="raw">Raw</option><option value="graded">Graded</option></select></label>
        {kind === "graded" && <>
          <label>Company <select value={company} onChange={(e) => { const c = e.target.value as "PSA" | "BGS" | "CGC"; setCompany(c); if (!GRADES[c].includes(grade)) setGrade(GRADES[c][0]); }}>
            {(["PSA", "BGS", "CGC"] as const).map((c) => <option key={c}>{c}</option>)}</select></label>
          <label>Grade <select value={grade} onChange={(e) => setGrade(e.target.value)}>{GRADES[company].map((g) => <option key={g}>{g}</option>)}</select></label>
        </>}
      </div>
      <p className="pf-muted">New identification: <b>{card.name}</b> · {cardLine(card)} · {kind === "graded" ? `${company} ${grade}` : "Raw"}</p>
      <div className="pf-actions">
        <button type="button" className="pf-shiny" disabled={!changed || busy} onClick={() => onSave({
          ...(card.id !== h.card.id ? { card_id: card.id } : {}), kind, ...(kind === "graded" ? { grading_company: company, grade } : {}),
        })}>{busy ? "Saving & pricing…" : "Save identification"}</button>
        <button type="button" className="pf-btn" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export function Money({ cents, compact = false }: { cents: number | null | undefined; compact?: boolean }) {
  return <>{cents === null || cents === undefined ? DASH : usd(cents, compact)}</>;
}
