import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { memberApi, refreshSession } from "./members";
import { playSound, warmSounds } from "./sound";

/** Ethereum wallets: connect, then personal_sign a message the server wrote. Only a free off-chain signature: no
 *  transaction, no approval, never a key or seed phrase.
 *
 *  Finding the wallet: EIP-6963 (every modern extension announces itself: MetaMask, OKX, Binance Wallet, Coinbase,
 *  Rabby, Trust, Phantom…), so a member with several installed picks one, plus the older window.ethereum /
 *  window.okxwallet / window.BinanceChain. On a phone with no wallet in the browser, deep links reopen this page inside
 *  the wallet app's own browser, where the wallet is available. */

interface Eth { request(a: { method: string; params?: unknown[] }): Promise<unknown> }
interface Option { name: string; icon?: string; provider: Eth }
interface Announce { info: { name: string; icon: string; rdns: string; uuid: string }; provider: Eth }

export const NO_WALLET = "No wallet found in this browser.";

function legacy(): Option[] {
  const w = window as unknown as Record<string, unknown>;
  const out: Option[] = [];
  const eth = w.ethereum as (Eth & Record<string, unknown> & { providers?: (Eth & Record<string, unknown>)[] }) | undefined;
  const label = (p: Record<string, unknown>) => p.isOkxWallet || p.isOKExWallet ? "OKX Wallet" : p.isBinance ? "Binance Wallet"
    : p.isCoinbaseWallet ? "Coinbase Wallet" : p.isRabby ? "Rabby" : p.isTrust || p.isTrustWallet ? "Trust Wallet"
    : p.isMetaMask ? "MetaMask" : "Browser wallet";
  for (const p of eth?.providers ?? (eth ? [eth] : [])) if (typeof p.request === "function") out.push({ name: label(p), provider: p });
  const okx = w.okxwallet as Eth | undefined;
  if (okx && typeof okx.request === "function") out.push({ name: "OKX Wallet", provider: okx });
  const bnb = w.BinanceChain as Eth | undefined;
  if (bnb && typeof bnb.request === "function") out.push({ name: "Binance Wallet", provider: bnb });
  return out;
}

/** Every wallet in this browser (announced ones first), without duplicates. */
export function discover(waitMs = 350): Promise<Option[]> {
  return new Promise((resolve) => {
    const found: Option[] = [];
    const onAnnounce = (e: Event) => {
      const d = (e as CustomEvent<Announce>).detail;
      if (d?.provider && !found.some((f) => f.provider === d.provider)) found.push({ name: d.info.name, icon: d.info.icon, provider: d.provider });
    };
    window.addEventListener("eip6963:announceProvider", onAnnounce);
    window.dispatchEvent(new Event("eip6963:requestProvider"));
    window.setTimeout(() => {
      window.removeEventListener("eip6963:announceProvider", onAnnounce);
      for (const o of legacy()) if (!found.some((f) => f.provider === o.provider)) found.push(o);
      resolve(found);
    }, waitMs);
  });
}

const isPhone = () => /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);

/** Open this page inside a wallet app's browser (phones without a wallet in the browser). */
function deepLinks(): { name: string; href: string }[] {
  const here = location.href;
  const bare = here.replace(/^https?:\/\//, "");
  return [
    { name: "MetaMask", href: `https://metamask.app.link/dapp/${bare}` },
    { name: "OKX Wallet", href: `okx://wallet/dapp/url?dappUrl=${encodeURIComponent(here)}` },
    { name: "Trust Wallet", href: `https://link.trustwallet.com/open_url?coin_id=60&url=${encodeURIComponent(here)}` },
    { name: "Coinbase Wallet", href: `https://go.cb-w.com/dapp?cb_url=${encodeURIComponent(here)}` },
  ];
}

const INSTALL = [
  { name: "MetaMask", href: "https://metamask.io/download/" },
  { name: "OKX Wallet", href: "https://www.okx.com/web3" },
  { name: "Binance Wallet", href: "https://www.binance.com/en/web3wallet" },
];

function Picker({ options, onPick, onClose }: { options: Option[]; onPick: (o: Option) => void; onClose: () => void }) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [onClose]);
  return (
    <div className="lg-backdrop wl-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="lg-dialog wl-dialog" role="dialog" aria-modal="true" aria-labelledby="wl-title">
        <button type="button" className="lg-close" aria-label="Close" onClick={onClose}>×</button>
        <h2 className="lg-title" id="wl-title">Connect a wallet</h2>
        {options.length ? (
          <>
            <p className="lg-sub">Choose your wallet. You'll sign a free message to prove it's yours.</p>
            <ul className="wl-list">
              {options.map((o) => (
                <li key={o.name + (o.icon ?? "")}>
                  <button type="button" className="wl-opt" onClick={() => onPick(o)}>
                    {o.icon ? <img src={o.icon} alt="" width={28} height={28} /> : <span className="wl-dot" aria-hidden>◆</span>}
                    <span>{o.name}</span><span aria-hidden>→</span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        ) : isPhone() ? (
          <>
            <p className="lg-sub">Open this page in your wallet app to connect:</p>
            <ul className="wl-list">
              {deepLinks().map((d) => <li key={d.name}><a className="wl-opt" href={d.href}><span className="wl-dot" aria-hidden>◆</span><span>{d.name}</span><span aria-hidden>↗</span></a></li>)}
            </ul>
            <p className="lg-fine">Binance Wallet: open the Binance app → Web3 Wallet → Discover, and enter pricechat.live.</p>
          </>
        ) : (
          <>
            <p className="lg-sub">No wallet extension found in this browser. Install one, then reload this page:</p>
            <ul className="wl-list">
              {INSTALL.map((d) => <li key={d.name}><a className="wl-opt" href={d.href} target="_blank" rel="noopener noreferrer"><span className="wl-dot" aria-hidden>◆</span><span>{d.name}</span><span aria-hidden>↗</span></a></li>)}
            </ul>
          </>
        )}
        <p className="lg-fine">Free signature only: no transaction, no approvals. We never ask for your seed phrase or private key.</p>
      </div>
    </div>
  );
}

/** Let the member choose a wallet (straight through when there's exactly one). Rejects if they close the picker. */
export async function chooseWallet(): Promise<Eth> {
  const options = await discover();
  if (options.length === 1) return options[0].provider;
  return new Promise((resolve, reject) => {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const root = createRoot(host);
    const done = () => { root.unmount(); host.remove(); };
    root.render(<Picker options={options} onPick={(o) => { done(); resolve(o.provider); }}
                        onClose={() => { done(); reject(new Error("cancelled")); }} />);
  });
}

const toHex = (s: string) => "0x" + Array.from(new TextEncoder().encode(s), (b) => b.toString(16).padStart(2, "0")).join("");

/** Pick a wallet, get the server's message for its account, sign it. Returns what the verify endpoint needs. */
const wait = (ms: number) => new Promise((r) => window.setTimeout(r, ms));

/** The wallet's account. Some wallets (seen with exchange-wallet extensions) approve the connection but never answer
 *  eth_requestAccounts, so besides waiting for that answer we also check eth_accounts every second: as soon as the
 *  account is shared we carry on. Gives up after 2 minutes with a clear message instead of waiting forever. */
async function account(provider: Eth): Promise<string> {
  const first = (a: unknown) => (Array.isArray(a) && typeof a[0] === "string" ? (a[0] as string) : null);
  try {
    const already = first(await provider.request({ method: "eth_accounts" }));
    if (already) return already;
  } catch { /* some wallets don't answer eth_accounts before connecting */ }
  let settled = false;
  const asked = provider.request({ method: "eth_requestAccounts" }).then((a) => {
    const got = first(a);
    if (!got) throw new Error("No account was shared.");
    return got;
  });
  const polled = (async () => {
    for (let i = 0; i < 120 && !settled; i++) {
      await wait(1000);
      try {
        const got = first(await provider.request({ method: "eth_accounts" }));
        if (got) return got;
      } catch { /* keep waiting */ }
    }
    if (!settled) throw new Error("timeout");
    return "";
  })();
  try {
    return await Promise.race([asked, polled]);
  } finally {
    settled = true;
  }
}

/** Pick a wallet, get the server's message for its account, sign it. Returns what the verify endpoint needs. */
export async function signWith(noncePath: string): Promise<{ nonce: string; signature: string }> {
  const provider = await chooseWallet();
  const address = await account(provider);
  const n = await memberApi<{ message: string; nonce: string }>(noncePath, { method: "POST", body: JSON.stringify({ address }) });
  // hex-encoded UTF-8: every wallet signs the same bytes (some mangle plain-text params)
  const signature = (await Promise.race([
    provider.request({ method: "personal_sign", params: [toHex(n.message), address] }),
    wait(180_000).then(() => { throw new Error("timeout"); }),
  ])) as string;
  return { nonce: n.nonce, signature };
}

/** Sign a server-built EIP-712 message (Hyperliquid's ApproveAgent) with the member's wallet. The server builds the message
 *  for the wallet's current chain (wallets refuse typed data for another chain) and later checks who signed it. */
export async function signTypedWith<T extends { typed_data: unknown }>(start: (chainId: string) => Promise<T>): Promise<T & { signature: string; chainId: string }> {
  const provider = await chooseWallet();
  const address = await account(provider);
  const chainId = String(await provider.request({ method: "eth_chainId" })).toLowerCase();
  const t = await start(chainId);
  const signature = (await Promise.race([
    provider.request({ method: "eth_signTypedData_v4", params: [address, JSON.stringify(t.typed_data)] }),
    wait(180_000).then(() => { throw new Error("timeout"); }),
  ])) as string;
  return { ...t, signature, chainId };
}

/** A friendly message for a failed wallet step (nothing at all when the member just closed the picker). */
export function walletError(e: unknown): string | null {
  const m = e instanceof Error ? e.message : String(e ?? "");
  const code = (e as { code?: number })?.code;
  if (m === "cancelled") return null;
  if (m === "timeout") return "Your wallet didn't answer. Open the wallet, approve, and try again.";
  if (code === 4001 || /reject|denied|cancel/i.test(m)) return "You cancelled in the wallet. Try again when you're ready.";
  if (code === -32002) return "Your wallet already has a request open: check the wallet window.";
  return m || "Couldn't connect the wallet.";
}

/** "Sign in with wallet" for the sign-in popup: one Kiwi Passport per wallet (new wallets get a new one, plus 100+ XP). */
export function WalletSignIn({ compact = false }: { compact?: boolean }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function go() {
    warmSounds();
    playSound("tick");
    setBusy(true);
    setErr(null);
    try {
      const s = await signWith("/members/auth/wallet/nonce");
      const r = await memberApi<{ created: boolean }>("/members/auth/wallet/verify", { method: "POST", body: JSON.stringify(s) });
      refreshSession();
      playSound(r.created ? "levelup" : "coin");
      await new Promise((ok) => window.setTimeout(ok, 700));  // let the sound play before the Passport opens
      location.assign(`/passport?welcome=${r.created ? "new" : "back"}`);
    } catch (e) {
      setErr(walletError(e));
      setBusy(false);
    }
  }
  return (
    <div className={`lg-wallet${compact ? " is-compact" : ""}`}>
      <button type="button" className={`site-button lg-wallet-btn${busy ? " is-waiting" : ""}`} disabled={busy} onClick={go}>
        <span aria-hidden>🦊</span> {busy ? "Check your wallet…" : "Sign in with wallet"}
      </button>
      {!compact && <small className="site-muted">+100 XP the first time</small>}
      {err && <p className="site-error" role="alert">{err}</p>}
    </div>
  );
}
