import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { openLogin } from "../../../website/emailLogin";
import { MemberApiError, useSession } from "../../../website/members";
import { AVAILABILITY, REQUEST_STATUS, ago, cardName, money, vaultApi, type VaultDetail, type VaultRequest } from "./api";
import { usePageHead } from "../mx";
import { VaultShell } from "./VaultStore";

export function VaultItemPage() {
  const id = Number(useParams().id);
  const [c, setC] = useState<VaultDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState(0);
  const member = useSession();

  useEffect(() => {
    setC(null);
    setError(null);
    vaultApi.listing(id).then(setC).catch((e: unknown) => setError(e instanceof MemberApiError && e.status === 404 ? "missing" : (e as Error).message));
  }, [id, member?.username]);
  usePageHead(c && c.id === id ? {
    title: `${cardName(c)}${c.set_name ? ` · ${c.set_name}` : ""}${c.price !== null ? ` · ${money(c.price, c.currency)}` : ""} · Price Chat Vault`,
    description: `${c.title}. ${c.condition ?? "Ungraded"} ${c.year ? `${c.year} ` : ""}Japanese Pokémon card in the Price Chat Vault: the seller's own photos and price, re-checked every few hours.`,
    image: c.images[0] ?? null,
  } : null);

  if (error === "missing") {
    return <VaultShell><div className="vt-empty"><h2>This card isn't in the Vault</h2><p>It may have sold or been removed by the seller.</p><Link className="vt-btn" to="/pokemon/vault">Back to the store</Link></div></VaultShell>;
  }
  if (error) return <VaultShell><p className="vt-error" role="alert">Couldn't load this card: {error}</p></VaultShell>;
  if (!c) return <VaultShell><div className="vt-item vt-item-loading" aria-busy="true"><div className="vt-skel vt-skel-art" /><div className="vt-skel vt-skel-info" /></div></VaultShell>;

  const facts: [string, string | null][] = [
    ["Pokémon", c.pokemon], ["Set", c.set_name], ["Released", c.year ? `${c.year} (approx.)` : null], ["Language", c.language],
    ["Condition", [c.condition ?? "Ungraded", c.seller_grade ? `seller's grade ${c.seller_grade}` : null].filter(Boolean).join(" · ")],
    ...Object.entries(c.specifics).filter(([k]) => !["Language", "Character", "Set"].includes(k)).map(([k, v]) => [k, v] as [string, string]),
  ];
  const out = c.availability !== "available";
  return (
    <VaultShell crumb={cardName(c)}>
      <article className="vt-item">
        <section className="vt-gallery" aria-label="Photos">
          <div className="vt-gallery-main">
            {c.images.length ? <img src={c.images[shown]} alt={`${c.title} (photo ${shown + 1} of ${c.images.length})`} referrerPolicy="no-referrer" />
              : <span className="vt-noimg">No photo</span>}
            {out && <span className="vt-flag vt-flag-out">{AVAILABILITY[c.availability]}</span>}
          </div>
          {c.images.length > 1 && (
            <div className="vt-thumbs" role="list">
              {c.images.map((u, i) => (
                <button key={u} role="listitem" className={i === shown ? "is-on" : ""} onClick={() => setShown(i)} aria-label={`Photo ${i + 1}`}>
                  <img src={u} alt="" loading="lazy" referrerPolicy="no-referrer" />
                </button>
              ))}
            </div>
          )}
          <p className="vt-photo-credit">Photos: the seller's own, hosted by {c.source.platform}.</p>
        </section>

        <section className="vt-info">
          <p className="vt-eyebrow">Japanese vintage{c.year ? ` · ${c.year}` : ""}</p>
          <h1>{cardName(c)}</h1>
          <p className="vt-fulltitle">{c.title}</p>

          <div className="vt-buybox">
            <div className="vt-buybox-price">
              <span className="vt-price vt-price-lg">{money(c.price, c.currency)}</span>
              <span className="vt-ship">{c.shipping === null ? "Shipping: see listing" : c.shipping === 0 ? "Free shipping" : `+ ${money(c.shipping, c.currency)} shipping`}</span>
            </div>
            <p className={`vt-avail is-${c.availability}`}>{AVAILABILITY[c.availability]}</p>
            <p className={`vt-checked${c.stale ? " is-stale" : ""}`}>
              {c.stale ? `⚠ Last confirmed with ${c.source.platform} ${ago(c.checked_at)}: the price or availability may have changed.`
                : `Price and availability confirmed with ${c.source.platform} ${ago(c.checked_at)}.`}
            </p>
            {!out && <RequestBox c={c} signedIn={member !== null && member !== undefined} loading={member === undefined} onDone={(r) => setC({ ...c, my_request: r })} />}
            {c.url && (
              <a className="vt-btn vt-btn-ghost vt-btn-block" href={c.url} target="_blank" rel="noopener noreferrer">
                View the original listing on {c.source.platform} ↗
              </a>
            )}
          </div>

          <section className="vt-seller" aria-label="Seller">
            <h2>Seller</h2>
            <p><b>{c.source.name}</b>{c.seller.username ? <> · {c.source.platform} seller <span className="vt-mono">{c.seller.username}</span></> : null}</p>
            {(c.seller.feedback_pct || c.seller.feedback_score !== null) && (
              <p className="vt-muted">{c.seller.feedback_pct ? `${c.seller.feedback_pct}% positive feedback` : ""}{c.seller.feedback_score !== null ? ` · ${c.seller.feedback_score.toLocaleString()} ratings` : ""}</p>
            )}
            {c.source.url && <a href={c.source.url} target="_blank" rel="noopener noreferrer">Visit the seller's store ↗</a>}
          </section>

          <section aria-label="Details">
            <h2>Card details</h2>
            <dl className="vt-facts">
              {facts.filter(([, v]) => v).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
            </dl>
          </section>
          {c.condition_detail && (
            <section aria-label="Condition">
              <h2>Condition, in the seller's words</h2>
              <p className="vt-quote">{c.condition_detail}</p>
            </section>
          )}
        </section>
      </article>
    </VaultShell>
  );
}

function RequestBox({ c, signedIn, loading, onDone }: { c: VaultDetail; signedIn: boolean; loading: boolean; onDone: (r: VaultRequest) => void }) {
  const [open, setOpen] = useState(false);
  const [contact, setContact] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (c.my_request) {
    return (
      <div className="vt-request is-sent" role="status">
        <b>Request sent · {REQUEST_STATUS[c.my_request.status]}</b>
        <span>We'll contact you at {c.my_request.contact || "your Price Chat account"}. Nothing has been charged and the card is not reserved.</span>
        <Link to="/pokemon/vault/requests">See my requests</Link>
      </div>
    );
  }
  if (!c.can_request) {
    return <p className="vt-request-off">Requests are paused for this card while we re-check it with the seller.</p>;
  }
  if (!open) {
    return (
      <>
        <button className="vt-btn vt-btn-block" disabled={loading} onClick={() => (signedIn ? setOpen(true) : openLogin())}>
          {signedIn || loading ? "Request to Buy" : "Sign in to Request to Buy"}
        </button>
        <p className="vt-fine">A request is not an order: no payment is taken and the card isn't reserved. We'll check the card with the seller and get back to you.</p>
      </>
    );
  }
  const send = async () => {
    setBusy(true);
    setErr(null);
    try {
      onDone(await vaultApi.request(c.id, contact.trim(), note.trim()));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="vt-request" onSubmit={(e) => { e.preventDefault(); void send(); }}>
      <p className="vt-request-head"><b>Request to Buy</b> · not an order, no payment</p>
      <label>How should we reach you? <small>(Telegram @, email…; optional)</small>
        <input value={contact} onChange={(e) => setContact(e.target.value)} maxLength={120} placeholder="@yourname" autoComplete="off" />
      </label>
      <label>Anything we should know? <small>(optional)</small>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} rows={3} placeholder="Questions about the condition, shipping country…" />
      </label>
      {err && <p className="vt-error" role="alert">{err}</p>}
      <div className="vt-row">
        <button type="submit" className="vt-btn" disabled={busy}>{busy ? "Sending…" : "Send request"}</button>
        <button type="button" className="vt-link" onClick={() => setOpen(false)}>Cancel</button>
      </div>
      <p className="vt-fine">Price Chat doesn't own this card yet. If we can get it for you, we'll confirm the price and next steps before anything is paid.</p>
    </form>
  );
}

export function VaultRequestsPage() {
  const member = useSession();
  const [rows, setRows] = useState<VaultRequest[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!member) return;
    vaultApi.myRequests().then((r) => setRows(r.requests)).catch((e: Error) => setErr(e.message));
  }, [member]);
  const withdraw = async (id: number) => {
    if (!window.confirm("Withdraw this request? We'll stop looking into this card for you.")) return;
    try {
      const r = await vaultApi.withdraw(id);
      setRows((xs) => xs?.map((x) => (x.id === id ? { ...x, status: r.status } : x)) ?? null);
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  return (
    <VaultShell crumb="My requests">
      <h1 className="vt-h1">My requests</h1>
      {member === null && <div className="vt-empty"><p>Sign in to see the cards you asked about.</p><button className="vt-btn" onClick={openLogin}>Sign in</button></div>}
      {err && <p className="vt-error" role="alert">{err}</p>}
      {(member === undefined || (member && rows === null)) && !err && <p className="vt-muted">Loading…</p>}
      {rows && rows.length === 0 && <div className="vt-empty"><p>You haven't requested any cards yet.</p><Link className="vt-btn" to="/pokemon/vault">Browse the Vault</Link></div>}
      {rows && rows.length > 0 && (
        <ul className="vt-reqs">
          {rows.map((r) => (
            <li key={r.id} className="vt-req">
              {r.listing?.image ? <img src={r.listing.image} alt="" referrerPolicy="no-referrer" /> : <span className="vt-noimg" />}
              <div>
                <Link to={`/pokemon/vault/${r.listing_id}`}>{r.listing?.title ?? "Card"}</Link>
                <p className="vt-muted">Asked {new Date(r.created_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })} at {money(r.price, r.currency)} · <b>{REQUEST_STATUS[r.status]}</b>
                  {r.listing && r.listing.availability !== "available" ? ` · ${AVAILABILITY[r.listing.availability]}` : ""}</p>
              </div>
              {(r.status === "new" || r.status === "contacted") && <button className="vt-link" onClick={() => void withdraw(r.id)}>Withdraw</button>}
            </li>
          ))}
        </ul>
      )}
    </VaultShell>
  );
}
