import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { usd } from "../format";
import { MarketShell } from "../mx";
import { mkApi, STATUS_LABEL, usdt, type Listing, type MarketStatus } from "./api";

/** Marketplace pages: inside the market frame, with their own nav. */
export function MarketplaceFrame({ crumbs, children }: { crumbs: [string, string | null][]; children: ReactNode }) {
  return (
    <MarketShell crumbs={crumbs} compact>
      <nav className="pf-tabs mk-tabs" aria-label="Marketplace">
        <NavLink to="/pokemon/marketplace" end>⚡ Marketplace</NavLink>
        <NavLink to="/pokemon/marketplace/graded">🛒 Graded & raw</NavLink>
        <NavLink to="/pokemon/marketplace/sell">＋ Sell a Card</NavLink>
        <NavLink to="/pokemon/marketplace/listings">My Listings</NavLink>
        <NavLink to="/pokemon/marketplace/orders">My Orders</NavLink>
        <NavLink to="/pokemon/marketplace/watchlist">Watchlist</NavLink>
        <NavLink to="/pokemon/marketplace/vault">Vault & Redemption</NavLink>
        <NavLink to="/pokemon/marketplace/wallet">👛 Wallet</NavLink>
        <NavLink to="/pokemon/portfolio">✨ My Portfolio</NavLink>
      </nav>
      {children}
    </MarketShell>
  );
}

export function useMarketStatus(): MarketStatus | null {
  const [s, setS] = useState<MarketStatus | null>(null);
  useEffect(() => { void mkApi.status().then(setS).catch(() => setS(null)); }, []);
  return s;
}

/** Always visible while real-money trading is off: what works and what doesn't. */
export function ModeBanner({ status }: { status: MarketStatus | null }) {
  if (!status || status.mode === "live") return null;
  return (
    <div className="mk-banner" role="note">
      <b>{status.mode === "off" ? "Marketplace closed" : "Preview · no real-money trading yet"}</b>
      <span>Browse, watch and prepare listings now. Buying and offers open when vaulting, a regulated USDT payment partner and compliance checks are in
        place.{status.sandbox ? " You have sandbox access: SANDBOX listings can be traded with simulated USDT." : ""}</span>
    </div>
  );
}

export function StatusBadge({ s }: { s: string }) {
  return <span className={`mk-status mk-st-${s}`}>{STATUS_LABEL[s] ?? s}</span>;
}

export function DiffBadge({ pct, uncertain }: { pct: number | null; uncertain?: boolean }) {
  if (pct === null) return <span className="mk-diff is-na" title="No market estimate for this exact grade">No estimate</span>;
  const below = pct < 0;
  return (
    <span className={`mk-diff ${below ? "is-below" : pct > 0 ? "is-above" : ""}`} title={uncertain ? "The market estimate is uncertain (low confidence)" : "Asking price vs market estimate"}>
      {pct === 0 ? "At estimate" : `${Math.abs(pct)}% ${below ? "below" : "above"}`}{uncertain ? " ⚠" : ""}
    </span>
  );
}

export function Art({ l, className = "" }: { l: Listing; className?: string }) {
  const front = l.photos.find((p) => p.side === "front");
  const src = front ? mkApi.photoUrl(front.id) : l.card.image_url;
  return (
    <div className={`mk-art ${className}`}>
      {src ? <img src={src} alt={`${l.card.name} ${l.grading_company} ${l.grade}`} loading="lazy" decoding="async" /> : <div className="mk-noart">{l.card.name}</div>}
      {!front && <span className="mk-art-note">Catalogue image</span>}
    </div>
  );
}

export function ListingCard({ l }: { l: Listing }) {
  return (
    <Link to={`/pokemon/marketplace/l/${l.id}`} className={`mk-card${l.sandbox ? " is-sandbox" : ""}`}>
      <Art l={l} />
      <div className="mk-card-body">
        <div className="mk-card-top">
          <span className={`pf-badge pf-badge-${l.grading_company.toLowerCase()}`}>{l.grading_company} {l.grade}</span>
          {l.sandbox ? <span className="mk-sandbox">SANDBOX</span> : l.custody.vaulted ? <span className="mk-vaulted">🔒 Vaulted</span> : null}
        </div>
        <strong className="mk-card-name">{l.card.name}</strong>
        <span className="mk-card-meta">{l.card.year} · {l.card.set_name}{l.card.card_number ? ` #${l.card.card_number}` : ""}</span>
        <span className="mk-card-meta">{[l.card.edition !== "—" ? l.card.edition : null, l.card.variant || null, l.card.language].filter(Boolean).join(" · ")} · {l.cert ? `Cert ${l.cert}` : "Raw · AI condition"}</span>
        <div className="mk-card-price">
          <b>{usdt(l.price)}</b>
          <small>Est. {l.estimate.value !== null ? usd(l.estimate.value) : "—"}</small>
        </div>
        <div className="mk-card-foot">
          <DiffBadge pct={l.diff_pct} uncertain={l.uncertain} />
          {l.status !== "active" && <StatusBadge s={l.status} />}
          {l.seller && <span className="mk-seller">{l.seller.verified ? "✔ " : ""}{l.seller.username}</span>}
        </div>
      </div>
    </Link>
  );
}

export function ListingGrid({ items, empty }: { items: Listing[]; empty: ReactNode }) {
  if (!items.length) return <div className="mk-empty">{empty}</div>;
  return <div className="mk-grid">{items.map((l) => <ListingCard key={l.id} l={l} />)}</div>;
}

// --- wallet (only needed to pay; browsing never asks for one) -------------------------------------------------------------

type Eip1193 = { request: (a: { method: string; params?: unknown[] }) => Promise<unknown> };
type SolanaWallet = { connect: () => Promise<{ publicKey: { toString: () => string } }>; isPhantom?: boolean };
type TronLink = { request?: (a: { method: string }) => Promise<unknown>; tronWeb?: { defaultAddress?: { base58?: string } } };

const EVM_CHAINS: Record<string, string> = { "0x1": "Ethereum mainnet", "0xaa36a7": "Sepolia testnet" };

/** Connects an injected wallet (MetaMask / Phantom / TronLink) to SHOW the address and network. Nothing is signed or
 * sent from here while settlement is simulated; never asks for a seed phrase or private key. */
export function WalletPanel({ network }: { network: string }) {
  const [addr, setAddr] = useState<string | null>(null);
  const [chain, setChain] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const w = window as unknown as { ethereum?: Eip1193; phantom?: { solana?: SolanaWallet }; solana?: SolanaWallet; tronLink?: TronLink; tronWeb?: TronLink["tronWeb"] };
  const connect = useCallback(async () => {
    setErr(null);
    try {
      if (network === "ethereum") {
        if (!w.ethereum) throw new Error("No Ethereum wallet found (install MetaMask or use WalletConnect in your wallet's browser).");
        const accounts = (await w.ethereum.request({ method: "eth_requestAccounts" })) as string[];
        const id = (await w.ethereum.request({ method: "eth_chainId" })) as string;
        setAddr(accounts[0] ?? null);
        setChain(EVM_CHAINS[id] ?? `Chain ${id}`);
      } else if (network === "solana") {
        const sol = w.phantom?.solana ?? w.solana;
        if (!sol) throw new Error("No Solana wallet found (install Phantom or Solflare).");
        const r = await sol.connect();
        setAddr(r.publicKey.toString());
        setChain("Solana");
      } else {
        if (!w.tronLink) throw new Error("No Tron wallet found (install TronLink).");
        await w.tronLink.request?.({ method: "tron_requestAccounts" });
        setAddr(w.tronWeb?.defaultAddress?.base58 ?? w.tronLink.tronWeb?.defaultAddress?.base58 ?? null);
        setChain("Tron");
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't connect the wallet.");
    }
  }, [network, w]);
  return (
    <div className="mk-wallet">
      {addr ? (
        <p>👛 <b>{addr.slice(0, 6)}…{addr.slice(-4)}</b> · {chain}{network === "ethereum" && chain !== "Ethereum mainnet" ? " ⚠ switch to Ethereum mainnet for real payments" : ""}</p>
      ) : <button type="button" className="pf-btn pf-sm" onClick={() => void connect()}>Connect wallet</button>}
      <p className="pf-fine">Balance checks and signing open with live settlement. Price Chat never asks for your seed phrase or private keys.</p>
      {err && <p className="pf-error">{err}</p>}
    </div>
  );
}

export function newKey(): string {
  return (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`).replace(/[^a-zA-Z0-9-]/g, "").slice(0, 60);
}
