import { useEffect, useState } from "react";
import { day } from "../format";
import { SignInPrompt, errorText, useMember } from "../portfolio/shared";
import { MarketplaceFrame, ModeBanner, useMarketStatus } from "./shared";
import { SUGGESTED, connectAndProve, discover, walletApi, walletError, type Discovered, type LinkedWallet } from "./wallet";

/** 👛 Wallet: connect OKX / Binance / MetaMask, prove it's yours with a free signature, choose where seller payouts go. */
export function WalletPage() {
  const member = useMember();
  const status = useMarketStatus();
  const crumbs: [string, string | null][] = [["Markets", "/pokemon"], ["Card Marketplace", "/pokemon/marketplace"], ["Wallet", null]];
  return (
    <MarketplaceFrame crumbs={crumbs}>
      <ModeBanner status={status} />
      {member === undefined ? <p className="pf-muted">Loading…</p> : member === null ? <SignInPrompt title="Sign in to connect a wallet" /> : <Wallets />}
    </MarketplaceFrame>
  );
}

function Wallets() {
  const [linked, setLinked] = useState<LinkedWallet[] | null>(null);
  const [found, setFound] = useState<Discovered[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  useEffect(() => {
    void walletApi.mine().then((r) => setLinked(r.wallets)).catch((e) => setErr(errorText(e)));
    void discover().then(setFound);
  }, []);
  const connect = (w: Discovered) => {
    setBusy(w.info.uuid); setErr(null); setOk(null);
    void connectAndProve(w).then((list) => { setLinked(list); setOk(`${w.info.name} connected and verified.`); })
      .catch((e) => setErr(e?.status ? errorText(e) : walletError(e))).finally(() => setBusy(null));
  };
  const act = (key: string, fn: () => Promise<{ wallets: LinkedWallet[] }>) => {
    setBusy(key); setErr(null); setOk(null);
    void fn().then((r) => setLinked(r.wallets)).catch((e) => setErr(errorText(e))).finally(() => setBusy(null));
  };
  const here = typeof window !== "undefined" ? window.location.href : "";
  return (
    <section className="mk-section mk-wallet">
      <div className="mk-head"><h1>👛 Wallet</h1></div>
      <p className="pf-muted">Connect the wallet you'll use on the marketplace. You prove it's yours by signing a short message: it's free, sends no transaction and
        gives nobody access to your funds. We only keep the public address. <b>Never share your seed phrase or private key with anyone, including us.</b></p>

      <div className="mk-box">
        <h3>Your wallets</h3>
        {linked === null ? <p className="pf-muted">Loading…</p> : linked.length === 0 ? <p className="pf-muted">No wallet connected yet.</p> : (
          <ul className="mk-wallets">{linked.map((w) => (
            <li key={w.id}>
              <span className="mk-wallet-dot" aria-hidden>✓</span>
              <div><b>{w.wallet || "Wallet"}</b> <code title={w.address}>{w.short}</code>
                <small>Verified {day(w.verified_at)} · works on Ethereum and other EVM networks</small></div>
              {w.payout ? <span className="mk-payout">💰 Payouts</span>
                : <button type="button" className="pf-btn pf-sm" disabled={!!busy} onClick={() => act(`p${w.id}`, () => walletApi.setPayout(w.id))}>Use for payouts</button>}
              <button type="button" className="pf-link" disabled={!!busy} onClick={() => act(`r${w.id}`, () => walletApi.remove(w.id))}>Remove</button>
            </li>))}</ul>
        )}
        <p className="pf-fine">Seller proceeds are paid to the wallet marked 💰 Payouts. Check the address in your wallet app before you rely on it.</p>
      </div>

      <div className="mk-box">
        <h3>Connect a wallet</h3>
        {found === null ? <p className="pf-muted">Looking for wallets in this browser…</p> : found.length > 0 ? (
          <div className="mk-connect">{found.map((w) => (
            <button key={w.info.uuid} type="button" className="mk-connect-btn" disabled={!!busy} onClick={() => connect(w)}>
              {w.info.icon ? <img src={w.info.icon} alt="" /> : <span aria-hidden>👛</span>}
              <span>{busy === w.info.uuid ? "Check your wallet…" : `Connect ${w.info.name}`}</span>
            </button>))}</div>
        ) : (
          <div>
            <p className="pf-muted">No wallet found in this browser. Install one, or on your phone open this page inside the wallet app's browser:</p>
            <div className="mk-connect">{SUGGESTED.map((s) => (
              <a key={s.rdns} className="mk-connect-btn" href={s.deeplink && /Android|iPhone|iPad/i.test(navigator.userAgent) ? s.deeplink(here) : s.url}
                 target="_blank" rel="noreferrer"><span aria-hidden>👛</span><span>{s.name}</span></a>))}</div>
            <p className="pf-fine">Binance app: open Binance → Web3 Wallet → Discover, and paste this page's address.</p>
          </div>
        )}
        {ok && <p className="pf-ok">{ok}</p>}
        {err && <p className="pf-error" role="alert">{err}</p>}
      </div>
    </section>
  );
}
