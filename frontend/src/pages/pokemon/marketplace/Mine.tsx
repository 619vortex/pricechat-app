import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { day } from "../format";
import { SignInPrompt, errorText, useMember } from "../portfolio/shared";
import { mkApi, usdt, type Listing, type Offer, type Order, type VaultItem } from "./api";
import { ListingGrid, MarketplaceFrame, ModeBanner, StatusBadge, WalletPanel, useMarketStatus } from "./shared";
import { discover, payOnTestnet, walletApi, walletError, type Discovered, type LinkedWallet } from "./wallet";

function Gate({ title, crumb, children }: { title: string; crumb: string; children: (refresh: () => void) => React.ReactNode }) {
  const member = useMember();
  const [n, setN] = useState(0);
  const crumbs: [string, string | null][] = [["Markets", "/pokemon"], ["Card Marketplace", "/pokemon/marketplace"], [crumb, null]];
  return (
    <MarketplaceFrame crumbs={crumbs}>
      {member === undefined ? <p className="pf-muted">Loading…</p> : member === null ? <SignInPrompt title={title} /> : <div key={n}>{children(() => setN((x) => x + 1))}</div>}
    </MarketplaceFrame>
  );
}

function useLoad<T>(fn: () => Promise<T>): [T | null, string | null] {
  const [d, setD] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { void fn().then(setD).catch((e) => setErr(errorText(e))); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return [d, err];
}

export function MyListingsPage() {
  return <Gate title="Sign in to see your listings" crumb="My Listings">{(refresh) => <MyListings refresh={refresh} />}</Gate>;
}

const NEXT: Record<string, string> = {
  draft: "Finish the draft: photos, evidence and price, then submit.", awaiting_verification: "We're reviewing the card, photos and your evidence.",
  awaiting_custody: "Approved. Send the slab to the vault partner; it goes live once the vault confirms it.", verified: "In the vault and verified: publish it.",
  active: "Live on the marketplace.", reserved: "A buyer reserved it and is paying.", sold: "Sold.", cancelled: "Cancelled.", rejected: "Not approved.",
};

function MyListings({ refresh }: { refresh: () => void }) {
  const [d, err] = useLoad(() => mkApi.myListings());
  const [msg, setMsg] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => { try { await fn(); refresh(); } catch (e) { setMsg(errorText(e)); } };
  return (
    <section className="mk-section">
      <div className="mk-head"><h1>My Listings</h1><Link className="pf-shiny" to="/pokemon/marketplace/sell">＋ Sell a card</Link></div>
      {err && <p className="pf-error">{err}</p>}{msg && <p className="pf-error">{msg}</p>}
      {d && (d.listings.length === 0 ? <p className="pf-muted">No listings yet.</p> : (
        <ul className="mk-rows">{d.listings.map((l) => (
          <li key={l.id}>
            <Link to={`/pokemon/marketplace/l/${l.id}`}>{l.card.image_url ? <img src={l.card.image_url} alt="" /> : null}</Link>
            <div><b>{l.card.name}</b> <span className="pf-muted">{l.grading_company} {l.grade}{l.cert ? ` · cert ${l.cert}` : " · raw"}</span><br />
              <StatusBadge s={l.status} /> {l.sandbox && <span className="mk-sandbox">SANDBOX</span>} <small className="pf-muted">{NEXT[l.status]}</small>
              {l.review_note && <p className="pf-fine">Reviewer: {l.review_note}</p>}
              {l.flags && l.flags.length > 0 && <p className="pf-fine">⚑ {l.flags.join(" · ")}</p>}</div>
            <div className="mk-row-side"><b>{usdt(l.price)}</b>
              {l.status === "verified" && <button type="button" className="pf-shiny pf-sm" onClick={() => void act(() => mkApi.publish(l.id))}>Publish</button>}
              {l.status === "active" && <button type="button" className="pf-btn pf-sm" onClick={() => {
                const v = window.prompt("New asking price (USDT)", l.price ? String(l.price / 100) : "");
                if (v) void act(() => mkApi.update(l.id, { price: Number(v) }));
              }}>Change price</button>}
              {["draft", "awaiting_verification", "awaiting_custody", "verified", "active"].includes(l.status) &&
                <button type="button" className="pf-btn pf-sm pf-danger" onClick={() => void act(() => mkApi.cancel(l.id))}>Cancel</button>}
            </div>
          </li>))}</ul>
      ))}
    </section>
  );
}

export function WatchlistPage() {
  return <Gate title="Sign in to see your watchlist" crumb="Watchlist">{() => <Watchlist />}</Gate>;
}

function Watchlist() {
  const [d, err] = useLoad(() => mkApi.watchlist());
  return (
    <section className="mk-section"><h1>Watchlist</h1>{err && <p className="pf-error">{err}</p>}
      {d && <ListingGrid items={d.listings} empty={<p>Nothing watched yet. Tap ☆ on any listing.</p>} />}</section>
  );
}

export function MyOrdersPage() {
  return <Gate title="Sign in to see your orders" crumb="My Orders">{(refresh) => <MyOrders refresh={refresh} />}</Gate>;
}

function MyOrders({ refresh }: { refresh: () => void }) {
  const [d, err] = useLoad(() => mkApi.myOrders());
  const [msg, setMsg] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => { try { await fn(); refresh(); } catch (e) { setMsg(errorText(e)); } };
  const offerRow = (o: Offer, mine: boolean) => (
    <li key={o.id}><Link to={`/pokemon/marketplace/l/${o.listing_id}`}>Listing #{o.listing_id}</Link> <b>{usdt(o.amount)}</b>
      {o.listing_price ? <span className="pf-muted"> (ask {usdt(o.listing_price)})</span> : null} <StatusBadge s={o.status} />
      <small className="pf-muted"> expires {day(o.expires_at)}</small>
      {mine && o.status === "pending" && <button type="button" className="pf-btn pf-sm" onClick={() => void act(() => mkApi.cancelOffer(o.id))}>Cancel</button>}
      {!mine && o.status === "pending" && <>
        <button type="button" className="pf-shiny pf-sm" onClick={() => void act(() => mkApi.decideOffer(o.id, true))}>Accept</button>
        <button type="button" className="pf-btn pf-sm" onClick={() => void act(() => mkApi.decideOffer(o.id, false))}>Reject</button></>}
      {o.order_id && <Link className="pf-link" to={`/pokemon/marketplace/orders/${o.order_id}`}> Order →</Link>}
    </li>
  );
  return (
    <section className="mk-section">
      <h1>My Orders</h1>{err && <p className="pf-error">{err}</p>}{msg && <p className="pf-error">{msg}</p>}
      {d && <>
        {d.orders.length === 0 ? <p className="pf-muted">No orders yet.</p> : (
          <ul className="mk-rows">{d.orders.map((o) => (
            <li key={o.id}>
              {o.card?.image_url ? <img src={o.card.image_url} alt="" /> : <span />}
              <div><b>{o.card?.name}</b> <span className="pf-muted">{o.grading_company} {o.grade} · {o.role === "buyer" ? "bought" : "sold"} · {o.network_label}</span><br />
                <StatusBadge s={o.status} /> {o.sandbox && <span className="mk-sandbox">SANDBOX</span>} <small className="pf-muted">#{o.ref}</small></div>
              <div className="mk-row-side"><b>{usdt(o.role === "buyer" ? o.total : o.proceeds)}</b><Link className="pf-btn pf-sm" to={`/pokemon/marketplace/orders/${o.id}`}>View</Link></div>
            </li>))}</ul>
        )}
        <h2>Offers you made</h2>{d.offers_made.length ? <ul className="mk-list">{d.offers_made.map((o) => offerRow(o, true))}</ul> : <p className="pf-muted">None.</p>}
        <h2>Offers on your cards</h2>{d.offers_received.length ? <ul className="mk-list">{d.offers_received.map((o) => offerRow(o, false))}</ul> : <p className="pf-muted">None.</p>}
      </>}
    </section>
  );
}

/** TESTNET: pay from a verified wallet with one tap; the server then checks the transaction on-chain and keeps re-checking
 * while it waits for confirmations. */
function TestnetPay({ o, onOrder }: { o: Order; onOrder: (o: Order) => void }) {
  const [found, setFound] = useState<Discovered[] | null>(null);
  const [linked, setLinked] = useState<LinkedWallet[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tx, setTx] = useState("");
  useEffect(() => { void discover().then(setFound); void walletApi.mine().then((r) => setLinked(r.wallets)).catch(() => setLinked([])); }, []);
  const last = o.payments[o.payments.length - 1];
  const waiting = last?.status === "pending";
  useEffect(() => {  // the server re-checks a payment waiting for confirmations each time we ask
    if (!waiting || !last) return;
    const t = window.setInterval(() => void mkApi.submitTx(o.id, last.tx).then(onOrder).catch(() => undefined), 10_000);
    return () => window.clearInterval(t);
  }, [waiting, last, o.id, onOrder]);
  const send = (hash: string) => mkApi.submitTx(o.id, hash).then(onOrder);
  const pay = (w: Discovered) => {
    setBusy(w.info.uuid); setErr(null);
    void payOnTestnet(w, { token: o.token, pay_to: o.pay_to ?? "", amount_units: o.amount_units }).then((hash) => { setTx(hash); return send(hash); })
      .catch((e) => setErr(e?.status ? errorText(e) : walletError(e))).finally(() => setBusy(null));
  };
  return (
    <div className="mk-testnet">
      <p><b>🧪 Testnet:</b> real wallet, real blockchain, <b>test tokens with no value</b>. Get free test USDC at <a href="https://faucet.circle.com" target="_blank" rel="noreferrer">faucet.circle.com</a> (choose
        Ethereum Sepolia) and a little Sepolia ETH for gas from a Sepolia faucet.</p>
      {linked && linked.length > 0 && <p className="pf-fine">Pay from one of your verified wallets: {linked.map((w) => w.short).join(", ")}. A payment from any other wallet is rejected.</p>}
      {waiting ? <p className="pf-ok">⏳ Payment seen: {last.confirmations}/{o.confirmations_needed} confirmations. This page updates by itself.</p> : (
        <div className="mk-connect">{(found ?? []).map((w) => (
          <button key={w.info.uuid} type="button" className="mk-connect-btn" disabled={!!busy} onClick={() => pay(w)}>
            {w.info.icon ? <img src={w.info.icon} alt="" /> : <span aria-hidden>👛</span>}
            <span>{busy === w.info.uuid ? "Confirm in your wallet…" : `Pay with ${w.info.name}`}</span></button>))}
          {found !== null && found.length === 0 && <p className="pf-muted">No wallet found in this browser: open this page in your wallet app's browser.</p>}
        </div>
      )}
      <details><summary className="pf-fine">Already sent it? Paste the transaction hash</summary>
        <form className="pf-actions" onSubmit={(e) => { e.preventDefault(); setErr(null); void send(tx.trim()).catch((x) => setErr(errorText(x))); }}>
          <input value={tx} onChange={(e) => setTx(e.target.value)} placeholder="0x…" maxLength={66} aria-label="Transaction hash" />
          <button type="submit" className="pf-btn pf-sm" disabled={!/^0x[0-9a-fA-F]{64}$/.test(tx.trim())}>Check it</button>
        </form></details>
      {err && <p className="pf-error" role="alert">{err}</p>}
    </div>
  );
}

export function OrderPage() {
  const { id = "0" } = useParams();
  return <Gate title="Sign in to see this order" crumb="Order">{() => <OrderView id={Number(id)} />}</Gate>;
}

const STAGES = ["awaiting_payment", "paid", "completed"];

function OrderView({ id }: { id: number }) {
  const status = useMarketStatus();
  const [o, setO] = useState<Order | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dispute, setDispute] = useState("");
  const load = useCallback(() => void mkApi.order(id).then(setO).catch((e) => setErr(errorText(e))), [id]);
  useEffect(() => {
    load();
    const t = window.setInterval(load, 8000);
    return () => window.clearInterval(t);
  }, [load]);
  const act = async (fn: () => Promise<Order | unknown>) => {
    setBusy(true); setErr(null);
    try { const r = await fn(); if (r && typeof r === "object" && "ref" in r) setO(r as Order); else load(); } catch (e) { setErr(errorText(e)); } finally { setBusy(false); }
  };
  if (!o) return err ? <p className="pf-error">{err}</p> : <p className="pf-muted">Loading…</p>;
  const left = Math.max(0, Math.round((Date.parse(o.expires_at) - Date.now()) / 60000));
  const amount = (Number(o.amount_units) / 10 ** o.decimals).toFixed(o.testnet ? o.decimals : 2);  // testnet: the order's exact tail matters
  const unit = o.testnet ? "test USDC" : "USDT";
  return (
    <section className="mk-section mk-order">
      <ModeBanner status={status} />
      <h1>Order #{o.ref} {o.sandbox && <span className="mk-sandbox">SANDBOX · simulated</span>}</h1>
      <p className="pf-muted">{o.card?.name} · {o.grading_company} {o.grade} · you are the {o.role}</p>
      <ol className="mk-steps">{STAGES.map((s) => <li key={s} className={o.status === s ? "is-on" : STAGES.indexOf(o.status) > STAGES.indexOf(s) ? "is-done" : ""}>{s.replace("_", " ")}</li>)}</ol>
      <p><StatusBadge s={o.status} /> {o.note && <span className="pf-muted">{o.note}</span>}</p>
      {err && <p className="pf-error">{err}</p>}
      {o.role === "buyer" && o.status === "awaiting_payment" && (
        <div className="mk-panel">
          <h3>Pay {amount} {unit} on {o.network_label}</h3>
          <dl className="mk-facts">
            <div><dt>Network</dt><dd>{o.network_label}</dd></div>
            <div><dt>{o.testnet ? "Token (test USDC on Sepolia · no value)" : "Token (verified USDT contract)"}</dt><dd><code>{o.token}</code></dd></div>
            <div><dt>Send exactly</dt><dd><b>{amount} {unit}</b></dd></div>
            <div><dt>To {o.testnet ? "the test receiving wallet" : "this order's address"}</dt><dd><code>{o.pay_to}</code></dd></div>
            <div><dt>Time left</dt><dd>{left} min</dd></div>
            <div><dt>Confirmations required</dt><dd>{o.confirmations_needed}</dd></div>
          </dl>
          {o.testnet ? <TestnetPay o={o} onOrder={setO} /> : <WalletPanel network={o.network} />}
          {o.testnet ? null : o.sandbox ? (
            <div className="mk-sim">
              <p><b>Simulation:</b> no blockchain is used. Send a synthetic transfer and watch the same independent checks a real payment gets.</p>
              <div className="pf-actions">
                <button type="button" className="pf-shiny" disabled={busy} onClick={() => void act(() => mkApi.simulatePay(o.id, "exact"))}>Simulate exact payment</button>
                {["underpaid", "wrong_network", "wrong_token", "unconfirmed"].map((x) => (
                  <button key={x} type="button" className="pf-btn pf-sm" disabled={busy} onClick={() => void act(() => mkApi.simulatePay(o.id, x))}>{x.replace("_", " ")}</button>))}
              </div>
            </div>
          ) : <p className="pf-fine">Live payments open with settlement. The server confirms every transaction itself; pasting a transaction hash is never enough.</p>}
          <button type="button" className="pf-link" onClick={() => void act(() => mkApi.cancelOrder(o.id))}>Cancel order</button>
        </div>
      )}
      {o.payments.length > 0 && (
        <div className="mk-box"><h3>Payment checks</h3><ul className="mk-list">{o.payments.map((p) => (
          <li key={p.tx}><code>{p.tx.slice(0, 14)}…</code> <StatusBadge s={p.status === "confirmed" ? "completed" : p.status} /> {p.problem && <span className="pf-muted">{p.problem}</span>}
            {p.explorer && <a href={p.explorer} target="_blank" rel="noreferrer"> Explorer ↗</a>}{p.simulated && <small className="pf-muted"> simulated</small>}</li>))}</ul></div>
      )}
      <table className="mk-fees"><tbody>
        <tr><td>Price</td><td>{usdt(o.price)}</td></tr><tr><td>Buyer fee</td><td>{usdt(o.buyer_fee)}</td></tr>
        <tr className="is-total"><td>Buyer pays</td><td>{usdt(o.total)}</td></tr><tr><td>Marketplace fee</td><td>−{usdt(o.seller_fee)}</td></tr>
        <tr><td>Seller receives</td><td>{usdt(o.proceeds)}</td></tr></tbody></table>
      {o.status === "completed" && o.role === "buyer" && <p className="pf-ok">The card is yours: it's in your <Link to="/pokemon/marketplace/vault">vault</Link> and your portfolio.</p>}
      {["paid", "completed", "refund_pending"].includes(o.status) && (
        <details className="mk-box"><summary>Problem with this order?</summary>
          <textarea rows={3} value={dispute} onChange={(e) => setDispute(e.target.value)} placeholder="Describe the problem (min. 20 characters)" />
          <button type="button" className="pf-btn pf-sm" disabled={dispute.trim().length < 20} onClick={() => void act(async () => { await mkApi.dispute(o.id, dispute); setDispute(""); })}>Open a dispute</button>
        </details>
      )}
    </section>
  );
}

export function VaultPage() {
  return <Gate title="Sign in to see your vault" crumb="Vault & Redemption">{(refresh) => <Vault refresh={refresh} />}</Gate>;
}

function Vault({ refresh }: { refresh: () => void }) {
  const [d, err] = useLoad(() => mkApi.vault());
  const [open, setOpen] = useState<VaultItem | null>(null);
  return (
    <section className="mk-section">
      <h1>Vault & Redemption</h1>
      <p className="pf-muted">Cards you own on the marketplace ledger. Vaulted cards can be resold without shipping, or redeemed: the slab is shipped to you and the card
        leaves the marketplace for good.</p>
      {err && <p className="pf-error">{err}</p>}
      {d && (d.items.length === 0 ? <p className="pf-muted">No marketplace cards yet. Cards you buy, or list and vault, appear here.</p> : (
        <ul className="mk-rows">{d.items.map((i) => (
          <li key={i.item_id}>
            {i.card.image_url ? <img src={i.card.image_url} alt="" /> : <span />}
            <div><b>{i.card.name}</b> <span className="pf-muted">{i.grading_company} {i.grade} · cert {i.cert}</span> {i.sandbox && <span className="mk-sandbox">SANDBOX</span>}<br />
              <small>{i.custody_label}{i.vault_partner ? ` · ${i.vault_partner}` : ""} · insurance {i.insurance === "confirmed" ? "confirmed" : "not confirmed"}</small>
              {i.redemption && <p className="pf-fine">Redemption: <StatusBadge s={i.redemption.status} />{i.redemption.tracking ? ` · ${i.redemption.carrier} ${i.redemption.tracking}` : ""}</p>}</div>
            <div className="mk-row-side">
              {i.can_redeem && <button type="button" className="pf-btn pf-sm" onClick={() => setOpen(i)}>Redeem physical card</button>}
              {i.redemption?.status === "requested" && <button type="button" className="pf-btn pf-sm" onClick={() => void mkApi.cancelRedemption(i.redemption!.id).then(refresh)}>Cancel redemption</button>}
            </div>
          </li>))}</ul>
      ))}
      {open && <RedeemForm item={open} onDone={() => { setOpen(null); refresh(); }} onCancel={() => setOpen(null)} />}
    </section>
  );
}

function RedeemForm({ item, onDone, onCancel }: { item: VaultItem; onDone: () => void; onCancel: () => void }) {
  const [a, setA] = useState<Record<string, string>>({ name: "", line1: "", line2: "", city: "", region: "", postcode: "", country: "", phone: "" });
  const [quote, setQuote] = useState<Record<string, number> | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  useEffect(() => {
    if (a.country.length !== 2) { setQuote(null); return; }
    void mkApi.redemptionQuote(item.item_id, a.country).then((r) => setQuote(r.fees)).catch(() => setQuote(null));
  }, [a.country, item.item_id]);
  const f = (k: string, label: string, req = true) => <label>{label}<input value={a[k]} required={req} onChange={(e) => setA({ ...a, [k]: k === "country" ? e.target.value.toUpperCase().slice(0, 2) : e.target.value })} /></label>;
  return (
    <form className="mk-panel" onSubmit={(e) => { e.preventDefault(); void mkApi.redeem(item.item_id, a).then(onDone).catch((x) => setErr(errorText(x))); }}>
      <h3>Redeem {item.card.name} {item.grading_company} {item.grade}</h3>
      <div className="pf-row">{f("name", "Full name")}{f("line1", "Address")}{f("line2", "Address line 2", false)}{f("city", "City")}{f("region", "Region / state", false)}
        {f("postcode", "Postcode")}{f("country", "Country (2 letters, e.g. AE)")}{f("phone", "Phone (for the courier)", false)}</div>
      {quote && <table className="mk-fees"><tbody>
        <tr><td>Insured shipping</td><td>{usdt(quote.shipping)}</td></tr><tr><td>Handling</td><td>{usdt(quote.handling)}</td></tr>
        <tr><td>Transit insurance</td><td>{usdt(quote.insurance)}</td></tr><tr className="is-total"><td>Total</td><td>{usdt(quote.total)}</td></tr></tbody></table>}
      <p className="pf-fine">Your address is encrypted and shared only with the vault/shipping partner. Once requested the card is locked: it can't be listed or sold.
        The marketplace claim is retired only when the card is delivered.</p>
      <label className="pf-check"><input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} /> I understand the card leaves the marketplace once delivered.</label>
      {err && <p className="pf-error">{err}</p>}
      <div className="pf-actions"><button type="button" className="pf-btn" onClick={onCancel}>Back</button><button type="submit" className="pf-shiny" disabled={!ok}>Confirm redemption</button></div>
    </form>
  );
}

export type { Listing };
