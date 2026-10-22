import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { usd } from "../format";
import { shrink } from "../portfolio/api";
import { GRADES, SignInPrompt, cardLine, errorText, useMember } from "../portfolio/shared";
import { mkApi, usdt, type Listing, type Sellable, type SellerStatus } from "./api";
import { FeeTable } from "./ListingPage";
import { MarketplaceFrame, ModeBanner, useMarketStatus } from "./shared";

const STEPS = ["Card", "Details", "Photos", "Evidence & price", "Review"];
const RAW_NAMES: Record<string, string> = { NM: "Near Mint", LP: "Lightly Played", MP: "Moderately Played", HP: "Heavily Played", DMG: "Damaged" };

/** Sell a card: verified sellers pick a graded slab (company + grade + cert) or a raw card (condition from its AI scan),
 * add photos, ownership evidence and a price, review, and submit for verification. A portfolio entry alone never creates a
 * live listing. */
export function SellPage() {
  const member = useMember();
  const status = useMarketStatus();
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const [seller, setSeller] = useState<SellerStatus | null>(null);
  const [step, setStep] = useState(0);
  const [list, setList] = useState<Sellable[] | null>(null);
  const [h, setH] = useState<Sellable | null>(null);
  const [company, setCompany] = useState<"PSA" | "BGS" | "CGC">("PSA");
  const [grade, setGrade] = useState("");
  const [cert, setCert] = useState("");
  const [draft, setDraft] = useState<Listing | null>(null);
  const [shots, setShots] = useState<Record<string, string>>({});
  const [evidence, setEvidence] = useState("");
  const [price, setPrice] = useState("");
  const [note, setNote] = useState("");
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!member) return;
    void mkApi.seller().then(setSeller).catch((e) => setErr(errorText(e)));
    void mkApi.sellable().then((r) => {
      setList(r.holdings);
      const want = Number(sp.get("holding"));
      const pre = r.holdings.find((x) => x.holding_id === want && !x.listed && ready(x));
      if (pre) { pick(pre); setStep(1); }
    }).catch((e) => setErr(errorText(e)));
  }, [member]); // eslint-disable-line react-hooks/exhaustive-deps
  const pick = (x: Sellable) => {
    setH(x);
    if (x.kind !== "raw") { setCompany((x.grading_company as "PSA") ?? "PSA"); setGrade(x.grade ?? ""); setCert(x.cert ?? ""); }
  };
  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label); setErr(null);
    try { await fn(); } catch (e) { setErr(errorText(e)); } finally { setBusy(null); }
  };
  const crumbs: [string, string | null][] = [["Markets", "/pokemon"], ["Card Marketplace", "/pokemon/marketplace"], ["Sell a card", null]];
  if (member === undefined) return <MarketplaceFrame crumbs={crumbs}><p className="pf-muted">Loading…</p></MarketplaceFrame>;
  if (member === null) return <MarketplaceFrame crumbs={crumbs}><SignInPrompt title="Sign in to sell a card" /></MarketplaceFrame>;
  const cents = price ? Math.round(Number(price) * 100) : null;
  const fee = status?.fees.seller_fee_bps ?? 500;
  const isRaw = h?.kind === "raw";
  const sides = isRaw ? (["front", "back"] as const) : (["front", "back", "label"] as const);
  return (
    <MarketplaceFrame crumbs={crumbs}>
      <ModeBanner status={status} />
      <section className="mk-sell">
        <h1>Sell a card</h1>
        {err && <p className="pf-error" role="alert">{err}</p>}
        {seller && !seller.can_list ? <SellerGate seller={seller} onChange={setSeller} /> : (<>
        <ol className="mk-steps">{STEPS.map((s, i) => <li key={s} className={i === step ? "is-on" : i < step ? "is-done" : ""}>{s}</li>)}</ol>

        {step === 0 && (
          <div>
            <p className="pf-muted">Choose a graded slab or a raw card from your portfolio. Raw cards are described by an AI condition estimate of their photos, and
              members can add their own grade. Every listing is reviewed, and the card must be received and checked by a vault partner before it can be sold.</p>
            {list === null ? <p className="pf-muted">Loading your cards…</p> : list.length === 0 ? (
              <p>No cards in your portfolio yet. <Link to="/pokemon/portfolio/add">Add one</Link>, then come back.</p>
            ) : (
              <ul className="mk-pick">{list.map((x) => (
                <li key={x.holding_id}>
                  <button type="button" disabled={x.listed || !ready(x)} onClick={() => { pick(x); setStep(1); }}>
                    {x.card.image_url ? <img src={x.card.image_url} alt="" loading="lazy" /> : <span className="pf-qr-noimg" />}
                    <span><b>{x.card.name}</b><small>{cardLine(x.card)}</small></span>
                    {x.kind === "raw"
                      ? <span className="pf-badge pf-badge-raw">{x.grade ? `RAW ${x.grade}` : "RAW"}</span>
                      : <span className={`pf-badge pf-badge-${x.grading_company.toLowerCase()}`}>{x.grading_company} {x.grade}</span>}
                    <small>{x.listed ? "Already listed" : !ready(x) ? "Scan its condition first" : x.value != null ? `Est. ${usd(x.value)}` : ""}</small>
                  </button>
                </li>))}</ul>
            )}
            {list?.some((x) => !ready(x)) && (
              <p className="pf-fine">Raw cards need an AI condition scan before they can be listed: open the card in your <Link to="/pokemon/portfolio">portfolio</Link>,
                add sharp front and back photos, and tap ✨ Estimate Condition.</p>
            )}
          </div>
        )}

        {step === 1 && h && isRaw && (
          <form onSubmit={(e) => { e.preventDefault(); void run("draft", async () => {
            const d = await mkApi.create({ holding_id: h.holding_id, grading_company: "RAW", price: null, ownership_evidence: "", note: "" });
            setDraft(d); setStep(2);
          }); }}>
            <div className="mk-confirm"><b>{h.card.name}</b><span className="pf-muted">{cardLine(h.card)}</span>
              <span className="pf-fine">Wrong printing? Fix it first with <Link to="/pokemon/portfolio">Edit Identification</Link> in your portfolio.</span></div>
            <div className="mk-rawcond">
              <span className="pf-badge pf-badge-raw">RAW {h.grade}</span>
              <div><b>{RAW_NAMES[h.grade ?? ""] ?? h.grade}</b> · AI estimate ≈ {h.condition?.low}{h.condition?.high !== h.condition?.low ? `–${h.condition?.high}` : ""} of 10
                <small>From the AI review of your photos. Not a professional grade: buyers see the full review and can add their own grade.</small></div>
            </div>
            <div className="pf-actions"><button type="button" className="pf-btn" onClick={() => setStep(0)}>Back</button>
              <button type="submit" className="pf-shiny" disabled={busy === "draft"}>{busy ? "Saving…" : "Save draft & continue"}</button></div>
          </form>
        )}

        {step === 1 && h && !isRaw && (
          <form onSubmit={(e) => { e.preventDefault(); void run("draft", async () => {
            const d = await mkApi.create({ holding_id: h.holding_id, grading_company: company, grade, cert_number: cert, price: null, ownership_evidence: "", note: "" });
            setDraft(d); setStep(2);
          }); }}>
            <div className="mk-confirm"><b>{h.card.name}</b><span className="pf-muted">{cardLine(h.card)}</span>
              <span className="pf-fine">Wrong printing? Fix it first with <Link to="/pokemon/portfolio">Edit Identification</Link> in your portfolio.</span></div>
            <div className="pf-row">
              <label>Grading company<select value={company} onChange={(e) => { setCompany(e.target.value as "PSA"); setGrade(""); }}><option>PSA</option><option>BGS</option><option>CGC</option></select></label>
              <label>Grade<select value={grade} onChange={(e) => setGrade(e.target.value)} required><option value="">Choose</option>{GRADES[company].map((g) => <option key={g}>{g}</option>)}</select></label>
              <label>Certification number<input value={cert} onChange={(e) => setCert(e.target.value)} required minLength={6} maxLength={32} inputMode="numeric" placeholder="As printed on the label" /></label>
            </div>
            <div className="pf-actions"><button type="button" className="pf-btn" onClick={() => setStep(0)}>Back</button>
              <button type="submit" className="pf-shiny" disabled={busy === "draft" || !grade || cert.trim().length < 6}>{busy ? "Saving…" : "Save draft & continue"}</button></div>
          </form>
        )}

        {step === 2 && draft && (
          <div>
            <p className="pf-muted">{isRaw
              ? "Clear, glare-free photos of the actual card: the whole front and the whole back, edges and corners visible."
              : "Clear, glare-free photos of the actual slab: the front, the back, and a close-up of the label (cert number readable)."}</p>
            <div className="mk-shots">
              {sides.map((side) => (
                <label key={side} className="mk-shot">
                  {shots[side] ? <img src={shots[side]} alt={`${side} photo`} /> : <span>＋ {side === "label" ? "Slab label" : side[0].toUpperCase() + side.slice(1)}</span>}
                  <input type="file" accept="image/*" capture="environment" hidden onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) void run(side, async () => { await mkApi.photo(draft.id, side, await shrink(file, 2000, 0.9)); setShots((s) => ({ ...s, [side]: URL.createObjectURL(file) })); });
                    e.target.value = "";
                  }} />
                  {busy === side && <small>Uploading…</small>}
                </label>
              ))}
            </div>
            <div className="pf-actions"><button type="button" className="pf-shiny" disabled={sides.some((s) => !shots[s])} onClick={() => setStep(3)}>Continue</button></div>
            <p className="pf-fine">Your draft is saved: you can finish later from My Listings.</p>
          </div>
        )}

        {step === 3 && draft && (
          <form onSubmit={(e) => { e.preventDefault(); void run("save", async () => {
            setDraft(await mkApi.update(draft.id, { price: Number(price), ownership_evidence: evidence, note })); setStep(4);
          }); }}>
            <label className="pf-wide">Ownership evidence (private: only you and our reviewers see it)
              <textarea value={evidence} onChange={(e) => setEvidence(e.target.value)} rows={4} minLength={20} maxLength={4000} required
                        placeholder="Where and when you got it, and what you can provide (receipt, invoice, grading submission form…)." /></label>
            <label>Asking price (USDT)<input inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value.replace(/[^0-9.]/g, ""))} required /></label>
            {draft.estimate.value !== null && <p className="pf-muted">Market estimate for {isRaw ? `raw ${draft.grade}` : `${draft.grading_company} ${draft.grade}`}: <b>{usd(draft.estimate.value)}</b> ({draft.estimate.confidence} confidence).</p>}
            <label className="pf-wide">Note to buyers (optional, public)<input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} /></label>
            <div className="pf-actions"><button type="button" className="pf-btn" onClick={() => setStep(2)}>Back</button>
              <button type="submit" className="pf-shiny" disabled={!!busy || !price || evidence.trim().length < 20}>Review</button></div>
          </form>
        )}

        {step === 4 && draft && cents && (
          <div>
            <FeeTable f={{ price: cents, buyer_fee: 0, buyer_fee_bps: status?.fees.buyer_fee_bps ?? 0, buyer_total: cents, seller_fee: Math.floor(cents * fee / 10000),
                           seller_fee_bps: fee, seller_proceeds: cents - Math.floor(cents * fee / 10000), network_fees: {}, currency: "USDT" }} />
            <ul className="mk-obligations">
              {isRaw
                ? <li>Your listing is reviewed (identity, photos, evidence). The condition shown is the AI estimate: the vault checks the card on intake.</li>
                : <li>Your listing is reviewed (identity, certification, photos, evidence). A certification number alone doesn't prove authenticity.</li>}
              <li>Once approved you send the card to the approved vault partner (instructions follow). It goes live only after the vault confirms it.</li>
              <li>While listed, the card can't be sold elsewhere. If a buyer pays, the sale is binding; you're paid {usdt(cents - Math.floor(cents * fee / 10000))} after settlement.</li>
              <li>High-value cards (≥ 1,000 USDT) need enhanced seller verification.</li>
            </ul>
            <label className="pf-check"><input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} /> I own this card, it's genuine to my knowledge, and I accept these seller obligations.</label>
            <div className="pf-actions"><button type="button" className="pf-btn" onClick={() => setStep(3)}>Back</button>
              <button type="button" className="pf-shiny pf-big" disabled={!agree || !!busy} onClick={() => void run("submit", async () => {
                await mkApi.submit(draft.id); nav("/pokemon/marketplace/listings");
              })}>{busy ? "Submitting…" : "Submit for verification"}</button></div>
          </div>
        )}
        </>)}
      </section>
    </MarketplaceFrame>
  );
}

function ready(x: Sellable): boolean {
  return x.kind !== "raw" || !!x.condition?.scanned;
}

/** Only verified sellers list: ask once, then wait for the review. */
function SellerGate({ seller, onChange }: { seller: SellerStatus; onChange: (s: SellerStatus) => void }) {
  const [country, setCountry] = useState(seller.country ?? "");
  const [about, setAbout] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (seller.status === "pending") {
    return (
      <div className="mk-gate">
        <h2>⏳ Seller verification in review</h2>
        <p className="pf-muted">Thanks! We're checking your request. You'll be able to list cards as soon as it's approved.</p>
      </div>
    );
  }
  if (seller.status === "suspended") {
    return <div className="mk-gate"><h2>Seller account suspended</h2><p className="pf-muted">Contact support if you think this is a mistake.</p></div>;
  }
  return (
    <form className="mk-gate" onSubmit={(e) => { e.preventDefault(); setBusy(true); setErr(null);
      void mkApi.requestVerification(country, about).then(onChange).catch((x) => setErr(errorText(x))).finally(() => setBusy(false)); }}>
      <h2>✅ Become a verified seller</h2>
      <p className="pf-muted">Only verified sellers can list cards, so buyers know who they're dealing with. Tell us a little about yourself: we review every request.</p>
      {err && <p className="pf-error" role="alert">{err}</p>}
      <label>Country (2 letters)<input value={country} onChange={(e) => setCountry(e.target.value.toUpperCase().slice(0, 2))} required minLength={2} maxLength={2} placeholder="AE" /></label>
      <label className="pf-wide">About your collection
        <textarea value={about} onChange={(e) => setAbout(e.target.value)} rows={4} minLength={20} maxLength={1000} required
                  placeholder="How long you've collected, where your cards come from, what you plan to sell…" /></label>
      <div className="pf-actions"><button type="submit" className="pf-shiny" disabled={busy || country.length !== 2 || about.trim().length < 20}>{busy ? "Sending…" : "Request verification"}</button></div>
    </form>
  );
}
