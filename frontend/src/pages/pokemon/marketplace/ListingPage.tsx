import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { openLogin } from "../../../website/emailLogin";
import { day, usd } from "../format";
import { errorText, useMember } from "../portfolio/shared";
import { portfolioApi } from "../portfolio/api";
import { mkApi, usdt, type Listing } from "./api";
import { CommentsBox, RawConditionBox } from "./ListingCommunity";
import { DiffBadge, MarketplaceFrame, ModeBanner, StatusBadge, WalletPanel, newKey, useMarketStatus } from "./shared";

export function ListingPage() {
  const { id = "0" } = useParams();
  const member = useMember();
  const status = useMarketStatus();
  const nav = useNavigate();
  const [l, setL] = useState<Listing | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [photo, setPhoto] = useState(0);
  const [panel, setPanel] = useState<"buy" | "offer" | "report" | null>(null);
  const [network, setNetwork] = useState("tron");
  const [offer, setOffer] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [buyKey] = useState(newKey);
  const load = () => void mkApi.listing(Number(id)).then(setL).catch((e) => setErr(errorText(e)));
  useEffect(load, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  const act = async (fn: () => Promise<unknown>, done?: string) => {
    setBusy(true); setErr(null); setMsg(null);
    try { await fn(); if (done) setMsg(done); } catch (e) { setErr(errorText(e)); } finally { setBusy(false); }
  };
  const need = (fn: () => void) => (member ? fn() : openLogin());
  if (!l) return <MarketplaceFrame crumbs={[["Markets", "/pokemon"], ["Card Marketplace", "/pokemon/marketplace"], ["Card", null]]}>{err ? <p className="pf-error">{err}</p> : <p className="pf-muted">Loading…</p>}</MarketplaceFrame>;
  const tradable = status?.mode === "live" || (status?.mode === "simulation" && l.sandbox && status.sandbox);
  const pics = l.photos.length ? l.photos : [];
  const shown = pics[photo];
  const c = l.card;
  const f = l.fees;
  const raw = l.grading_company === "RAW";
  const gradeLabel = `${l.grading_company} ${l.grade}`;
  return (
    <MarketplaceFrame crumbs={[["Markets", "/pokemon"], ["Card Marketplace", "/pokemon/marketplace"], [`${c.name} ${gradeLabel}`, null]]}>
      <ModeBanner status={status} />
      <section className="mk-detail">
        <div className="mk-gallery">
          <div className="mk-gallery-main">
            {shown ? <a href={mkApi.photoUrl(shown.id, "full")} target="_blank" rel="noreferrer"><img src={mkApi.photoUrl(shown.id, "full")} alt={`${shown.side} photo`} /></a>
              : c.image_url ? <img src={c.image_url} alt={c.name} /> : <div className="mk-noart">{c.name}</div>}
            {!shown && <span className="mk-art-note">Catalogue image: no {raw ? "card" : "slab"} photos on this listing</span>}
          </div>
          {pics.length > 1 && (
            <div className="mk-thumbs">{pics.map((p, i) => (
              <button key={p.id} type="button" className={i === photo ? "is-on" : ""} onClick={() => setPhoto(i)} aria-label={`${p.side} photo`}>
                <img src={mkApi.photoUrl(p.id)} alt="" /><small>{p.side}{p.source === "vault" ? " (vault)" : ""}</small></button>))}</div>
          )}
        </div>
        <div className="mk-info">
          <div className="mk-card-top">
            <span className={`pf-badge pf-badge-${l.grading_company.toLowerCase()}`}>{gradeLabel}</span>
            {raw && <span className="mk-kind">Raw · AI condition</span>}
            <StatusBadge s={l.status} />
            {l.sandbox && <span className="mk-sandbox">SANDBOX · simulated</span>}
          </div>
          <h1>{c.name}</h1>
          <p className="pf-muted">{c.year} · {c.set_name}{c.card_number ? ` · #${c.card_number}` : ""} · {[c.edition !== "—" ? c.edition : "Standard", c.variant || null, c.language].filter(Boolean).join(" · ")}</p>
          <div className="mk-price-box">
            <span className="pf-eyebrow">Asking price</span>
            <strong>{usdt(l.price)}</strong>
            <span className="pf-muted">≈ {usd(l.price)} (1 USDT ≈ 1 USD, approximate)</span>
            <div className="mk-est">
              <span>Market estimate ({raw ? `raw, ${l.grade}` : gradeLabel}): <b>{l.estimate.value !== null ? usd(l.estimate.value) : "unavailable"}</b>
                {l.estimate.low !== null && l.estimate.high !== null && l.estimate.low !== l.estimate.high ? ` (${usd(l.estimate.low)}–${usd(l.estimate.high)})` : ""}</span>
              <DiffBadge pct={l.diff_pct} uncertain={l.uncertain} />
            </div>
            <p className="pf-fine">{l.estimate.source ?? "No source"} · {l.estimate.confidence ?? "—"} confidence{l.estimate.as_of ? ` · as of ${day(l.estimate.as_of)}` : ""}. {l.estimate.note}</p>
            {l.uncertain && <p className="mk-warn">⚠ This estimate is uncertain. Check recent verified sales before relying on it.</p>}
          </div>

          <div className="pf-actions">
            {l.status === "active" && !l.mine && (
              <>
                <button type="button" className="pf-shiny pf-big" disabled={!tradable} onClick={() => need(() => setPanel(panel === "buy" ? null : "buy"))}>Buy Now</button>
                <button type="button" className="pf-btn pf-big" disabled={!tradable} onClick={() => need(() => setPanel(panel === "offer" ? null : "offer"))}>Make Offer</button>
              </>
            )}
            {member && <button type="button" className="pf-btn" onClick={() => void act(async () => { const r = await mkApi.watch(l.id, !l.watching); setL({ ...l, watching: r.watching }); })}>{l.watching ? "★ Watching" : "☆ Add to Watchlist"}</button>}
            {!member && <button type="button" className="pf-btn" onClick={() => openLogin()}>☆ Add to Watchlist</button>}
            <Link className="pf-btn" to={`/pokemon/cards/${c.id}?grade=${encodeURIComponent(gradeLabel)}`}>View Market Data</Link>
            <button type="button" className="pf-btn" onClick={() => need(() => void act(() => portfolioApi.add(raw ? { card_id: c.id, kind: "raw" }
              : { card_id: c.id, kind: "graded", grading_company: l.grading_company as "PSA" | "BGS" | "CGC", grade: l.grade }),
              "Added to your portfolio as a self-reported card (this doesn't buy it)."))}>＋ Add to Portfolio</button>
          </div>
          {!tradable && l.status === "active" && !l.mine && <p className="pf-fine">Buying opens when live USDT settlement launches.</p>}

          {panel === "buy" && f && (
            <div className="mk-panel">
              <h3>Buy now · choose the payment network</h3>
              <div className="mk-networks" role="radiogroup">
                {status?.networks.map((n) => (
                  <label key={n.id} className={network === n.id ? "is-on" : ""}>
                    <input type="radio" name="net" checked={network === n.id} onChange={() => setNetwork(n.id)} />
                    <b>{n.label}</b><small>{n.fee_estimate}</small><code title="Verified USDT contract">{n.token.slice(0, 8)}…{n.token.slice(-6)}</code>
                  </label>
                ))}
              </div>
              <p className="mk-warn">Send USDT only on the network you choose. USDT on different networks is not interchangeable; funds sent on the wrong network can be lost.</p>
              <FeeTable f={f} />
              {network === "sepolia"
                ? <p className="pf-fine">🧪 Testnet: after reserving you pay from your verified wallet (👛 Wallet tab) with free test USDC. No real money moves.</p>
                : <WalletPanel network={network} />}
              <button type="button" className="pf-shiny pf-big" disabled={busy} onClick={() => void act(async () => {
                const o = await mkApi.buy(l.id, network, buyKey);
                nav(`/pokemon/marketplace/orders/${o.id}`);
              })}>{busy ? "Reserving…" : `Reserve & pay ${usdt(f.buyer_total)}`}</button>
              <p className="pf-fine">The card is reserved for you while you pay. Funds are released to the seller only after the payment, the exact item, the seller's right
                to sell and your eligibility are all verified.</p>
            </div>
          )}
          {panel === "offer" && (
            <form className="mk-panel" onSubmit={(e) => { e.preventDefault(); void act(async () => { await mkApi.offer(l.id, Number(offer)); setPanel(null); load(); }, "Offer sent. The seller has 48 hours to answer."); }}>
              <h3>Make an offer</h3>
              <label>Your offer (USDT)<input inputMode="decimal" value={offer} onChange={(e) => setOffer(e.target.value.replace(/[^0-9.]/g, ""))} placeholder={l.price ? String(Math.round(l.price / 100 * 0.9)) : ""} /></label>
              <p className="pf-fine">If the seller accepts, the card is reserved for you to pay within 30 minutes. You can cancel a pending offer any time.</p>
              <button type="submit" className="pf-shiny" disabled={busy || !offer}>Send offer</button>
            </form>
          )}
          {msg && <p className="pf-ok">{msg}</p>}
          {err && <p className="pf-error">{err}</p>}

          {l.offers && l.offers.length > 0 && (
            <div className="mk-box"><h3>{l.mine ? "Offers received" : "Your offers"}</h3>
              <ul className="mk-list">{l.offers.map((o) => (
                <li key={o.id}><b>{usdt(o.amount)}</b> <span className="pf-muted">{o.status} · expires {day(o.expires_at)}</span>
                  {l.mine && o.status === "pending" && <>
                    <button type="button" className="pf-btn pf-sm" onClick={() => void act(async () => { await mkApi.decideOffer(o.id, true); load(); }, "Accepted: the card is reserved for the buyer to pay.")}>Accept</button>
                    <button type="button" className="pf-btn pf-sm" onClick={() => void act(async () => { await mkApi.decideOffer(o.id, false); load(); })}>Reject</button></>}
                  {!l.mine && o.status === "pending" && <button type="button" className="pf-btn pf-sm" onClick={() => void act(async () => { await mkApi.cancelOffer(o.id); load(); })}>Cancel</button>}
                </li>))}</ul></div>
          )}
        </div>
      </section>

      {raw && <div className="mk-columns mk-columns-wide"><RawConditionBox l={l} signedIn={!!member} /></div>}

      <div className="mk-columns">
        <section className="mk-box">
          <h3>{raw ? "Card & condition" : "Card & certification"}</h3>
          <dl className="mk-facts">
            {raw ? <>
              <div><dt>Type</dt><dd>Raw (ungraded) card</dd></div>
              <div><dt>Condition</dt><dd>{l.grade} · AI estimate{l.condition?.low != null ? ` ≈ ${l.condition.low}–${l.condition.high}` : ""}</dd></div>
            </> : <>
              <div><dt>Grading company</dt><dd>{l.grading_company}</dd></div>
              <div><dt>Grade</dt><dd>{l.grade}</dd></div>
              <div><dt>Certification</dt><dd>{l.cert}</dd></div>
            </>}
            <div><dt>Identity verified</dt><dd>{l.verified ? "Yes, by Price Chat review" : "Not yet"}</dd></div>
            <div><dt>Custody</dt><dd>{l.custody.label}{l.custody.partner ? ` · ${l.custody.partner}` : ""}</dd></div>
            <div><dt>Insurance</dt><dd>{l.custody.insurance === "confirmed" ? l.custody.insurance_note || "Confirmed by the vault partner" : "Not confirmed"}</dd></div>
            {l.seller && <div><dt>Seller</dt><dd>{l.seller.username} · {l.seller.verified ? `verified (level ${l.seller.level})` : "unverified"}</dd></div>}
          </dl>
          <p className="pf-fine">{raw
            ? "A raw card's condition is an estimate from photos, not a professional grade. Listings are reviewed and the physical card is checked by the vault partner before it can be sold."
            : "A certification number identifies a slab; it doesn't prove authenticity on its own. Listings are reviewed and the physical slab is checked by the vault partner before it can be sold."}</p>
          {l.seller_note && <p><i>“{l.seller_note}”</i></p>}
        </section>
        <section className="mk-box">
          <h3>Fees</h3>
          {f && <FeeTable f={f} />}
          <h3>Shipping & redemption</h3>
          <p className="pf-muted">Bought cards stay in the vault in your name and can be resold without shipping. Redeem any time to have the slab shipped to you
            (insured courier; fees shown before you confirm).</p>
        </section>
        <section className="mk-box">
          <h3>Recent comparable sales ({gradeLabel})</h3>
          {l.comparables && (l.comparables.verified_sales.length + l.comparables.marketplace_sales.length) > 0 ? (
            <ul className="mk-list">
              {l.comparables.verified_sales.map((s, i) => <li key={`v${i}`}><b>{usd(s.price)}</b> <span className="pf-muted">{day(s.at)} · {s.source}</span></li>)}
              {l.comparables.marketplace_sales.map((s, i) => <li key={`m${i}`}><b>{usdt(s.price)}</b> <span className="pf-muted">{day(s.at)} · this marketplace</span></li>)}
            </ul>
          ) : <p className="pf-muted">No verified completed sales on record for this exact grade. Active listings are never counted as sales.</p>}
        </section>
        <section className="mk-box">
          <h3>Listing history</h3>
          <ul className="mk-list">{(l.history ?? []).map((h, i) => <li key={i}><span>{day(h.at)}</span> <b>{h.kind}</b> {h.price !== null && <span className="pf-muted">{usdt(h.price)}</span>}</li>)}</ul>
          {member && !l.mine && (panel === "report" ? (
            <form onSubmit={(e) => { e.preventDefault(); void act(async () => { await mkApi.report(l.id, reason); setPanel(null); }, "Reported. Our team will review it."); }}>
              <textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="What looks wrong?" rows={3} maxLength={1000} />
              <button type="submit" className="pf-btn pf-sm" disabled={reason.trim().length < 3}>Send report</button>
            </form>
          ) : <button type="button" className="pf-link" onClick={() => setPanel("report")}>Report this listing</button>)}
        </section>
      </div>
      {["active", "reserved", "sold"].includes(l.status) && <div className="mk-columns mk-columns-wide"><CommentsBox listingId={l.id} signedIn={!!member} /></div>}
    </MarketplaceFrame>
  );
}

export function FeeTable({ f }: { f: NonNullable<Listing["fees"]> }) {
  return (
    <table className="mk-fees"><tbody>
      <tr><td>Asking price</td><td>{usdt(f.price)}</td></tr>
      <tr><td>Buyer fee ({f.buyer_fee_bps / 100}%)</td><td>{usdt(f.buyer_fee)}</td></tr>
      <tr className="is-total"><td>Buyer pays</td><td>{usdt(f.buyer_total)} + network fee</td></tr>
      <tr><td>Marketplace fee from seller ({f.seller_fee_bps / 100}%)</td><td>−{usdt(f.seller_fee)}</td></tr>
      <tr><td>Seller receives</td><td>{usdt(f.seller_proceeds)}</td></tr>
    </tbody></table>
  );
}
