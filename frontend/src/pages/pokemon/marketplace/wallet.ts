import { memberApi } from "../../../website/members";

/** Browser wallets (EVM): OKX Wallet, Binance Web3 Wallet, MetaMask, Trust… discovered with EIP-6963 (each installed wallet
 * announces itself), with the legacy `window.ethereum` as a fallback. Only public addresses and signatures leave the
 * browser: the page never asks for, sees or stores a seed phrase or private key. */

export interface Eip1193 { request: (a: { method: string; params?: unknown[] }) => Promise<unknown> }
export interface WalletInfo { uuid: string; name: string; icon: string; rdns: string }
export interface Discovered { info: WalletInfo; provider: Eip1193 }
export interface LinkedWallet { id: number; chain: string; address: string; short: string; wallet: string; verified_at: string; payout: boolean }

/** The wallets the page suggests when none is installed, in this order. */
export const SUGGESTED = [
  { name: "OKX Wallet", rdns: "com.okex.wallet", url: "https://www.okx.com/web3", deeplink: (u: string) => `okx://wallet/dapp/url?dappUrl=${encodeURIComponent(u)}` },
  { name: "Binance Wallet", rdns: "com.binance.wallet", url: "https://www.binance.com/en/web3wallet", deeplink: null },
  { name: "MetaMask", rdns: "io.metamask", url: "https://metamask.io/download/", deeplink: (u: string) => `https://metamask.app.link/dapp/${u.replace(/^https?:\/\//, "")}` },
];

/** Every wallet that announces itself within `ms` (EIP-6963), plus window.ethereum if nothing announced. */
export function discover(ms = 400): Promise<Discovered[]> {
  return new Promise((resolve) => {
    const found = new Map<string, Discovered>();
    const on = (e: Event) => {
      const d = (e as CustomEvent<Discovered>).detail;
      if (d?.info?.uuid && d.provider) found.set(d.info.uuid, d);
    };
    window.addEventListener("eip6963:announceProvider", on);
    window.dispatchEvent(new Event("eip6963:requestProvider"));
    window.setTimeout(() => {
      window.removeEventListener("eip6963:announceProvider", on);
      const list = [...found.values()];
      const legacy = (window as unknown as { ethereum?: Eip1193 & { isOkxWallet?: boolean; isBinance?: boolean; isMetaMask?: boolean } }).ethereum;
      if (!list.length && legacy) {
        const name = legacy.isOkxWallet ? "OKX Wallet" : legacy.isBinance ? "Binance Wallet" : legacy.isMetaMask ? "MetaMask" : "Browser wallet";
        list.push({ info: { uuid: "legacy", name, icon: "", rdns: "legacy" }, provider: legacy });
      }
      const rank = (d: Discovered) => { const i = SUGGESTED.findIndex((s) => s.rdns === d.info.rdns); return i < 0 ? 99 : i; };
      resolve(list.sort((a, b) => rank(a) - rank(b)));
    }, ms);
  });
}

function toHex(text: string): string {
  return "0x" + Array.from(new TextEncoder().encode(text), (b) => b.toString(16).padStart(2, "0")).join("");
}

export const walletApi = {
  mine: () => memberApi<{ wallets: LinkedWallet[] }>("/members/market/wallets"),
  setPayout: (id: number) => memberApi<{ wallets: LinkedWallet[] }>(`/members/market/wallets/${id}/payout`, { method: "POST" }),
  remove: (id: number) => memberApi<{ wallets: LinkedWallet[] }>(`/members/market/wallets/${id}`, { method: "DELETE" }),
};

/** Connect the wallet, have it sign Price Chat's one-time message, and let the server check the signature. */
export async function connectAndProve(w: Discovered): Promise<LinkedWallet[]> {
  const accounts = (await w.provider.request({ method: "eth_requestAccounts" })) as string[];
  const address = accounts?.[0];
  if (!address) throw new Error("The wallet didn't share an address.");
  const c = await memberApi<{ address: string; message: string }>("/members/market/wallets/challenge", { method: "POST", body: JSON.stringify({ address }) });
  const signature = (await w.provider.request({ method: "personal_sign", params: [toHex(c.message), address] })) as string;
  const r = await memberApi<{ wallets: LinkedWallet[] }>("/members/market/wallets/verify", {
    method: "POST", body: JSON.stringify({ address, signature, wallet: w.info.name.slice(0, 40) }),
  });
  return r.wallets;
}

/** A wallet's own "user rejected" error, in plain words. */
export function walletError(e: unknown): string {
  const code = (e as { code?: number })?.code;
  if (code === 4001) return "You cancelled in the wallet. Nothing was linked.";
  if (code === -32002) return "Your wallet already has a request open: check the wallet window.";
  return e instanceof Error ? e.message : "The wallet couldn't complete that. Try again.";
}

const SEPOLIA = {
  chainId: "0xaa36a7", chainName: "Sepolia (testnet)", nativeCurrency: { name: "Sepolia Ether", symbol: "ETH", decimals: 18 },
  rpcUrls: ["https://ethereum-sepolia-rpc.publicnode.com"], blockExplorerUrls: ["https://sepolia.etherscan.io"],
};

/** ERC-20 transfer(to, amount) call data. */
export function transferData(to: string, amountUnits: string): string {
  const addr = to.toLowerCase().replace(/^0x/, "").padStart(64, "0");
  const amt = BigInt(amountUnits).toString(16).padStart(64, "0");
  return `0xa9059cbb${addr}${amt}`;
}

/** TESTNET: have the wallet send exactly `amountUnits` of the test token to `payTo` on Sepolia. Returns the tx hash. */
export async function payOnTestnet(w: Discovered, o: { token: string; pay_to: string; amount_units: string }): Promise<string> {
  const accounts = (await w.provider.request({ method: "eth_requestAccounts" })) as string[];
  const from = accounts?.[0];
  if (!from) throw new Error("The wallet didn't share an address.");
  try {
    await w.provider.request({ method: "wallet_switchEthereumChain", params: [{ chainId: SEPOLIA.chainId }] });
  } catch (e) {
    if ((e as { code?: number })?.code !== 4902) throw e;
    await w.provider.request({ method: "wallet_addEthereumChain", params: [SEPOLIA] });  // the wallet doesn't know Sepolia yet
  }
  return (await w.provider.request({ method: "eth_sendTransaction", params: [{ from, to: o.token, data: transferData(o.pay_to, o.amount_units) }] })) as string;
}
