import { afterEach, describe, expect, it, vi } from "vitest";
import { discover, signWith, walletError } from "./wallet";

/** TEST FIXTURES only: fake wallet providers. */
const fake = (name: string) => ({ name, request: async () => [] });

afterEach(() => { delete (window as unknown as Record<string, unknown>).ethereum; delete (window as unknown as Record<string, unknown>).okxwallet; });

describe("wallet discovery", () => {
  it("lists every wallet that announces itself (EIP-6963) plus older injected ones, once each", async () => {
    const mm = fake("mm"), okx = fake("okx");
    const answer = () => {
      for (const [p, n] of [[mm, "MetaMask"], [okx, "OKX Wallet"]] as const)
        window.dispatchEvent(new CustomEvent("eip6963:announceProvider", { detail: { info: { name: n, icon: "data:,", rdns: n, uuid: n }, provider: p } }));
    };
    window.addEventListener("eip6963:requestProvider", answer);
    (window as unknown as Record<string, unknown>).ethereum = { ...fake("legacy"), isBinance: true };
    (window as unknown as Record<string, unknown>).okxwallet = okx;  // the same OKX provider: not listed twice
    const found = await discover(20);
    window.removeEventListener("eip6963:requestProvider", answer);
    expect(found.map((f) => f.name)).toEqual(["MetaMask", "OKX Wallet", "Binance Wallet"]);
  });

  it("explains wallet errors in plain words and stays quiet when the picker is closed", () => {
    expect(walletError(new Error("cancelled"))).toBeNull();
    expect(walletError({ code: 4001, message: "User rejected the request." })).toMatch(/cancelled in the wallet/);
    expect(walletError({ code: -32002, message: "pending" })).toMatch(/already has a request open/);
  });
});

describe("a wallet that approves but never answers", () => {
  it("still carries on once the account shows up in eth_accounts (no endless 'waiting for your wallet')", async () => {
    let approved = false;
    const calls: string[] = [];
    (window as unknown as Record<string, unknown>).ethereum = {
      isMetaMask: true,
      request: async ({ method }: { method: string }) => {
        calls.push(method);
        if (method === "eth_requestAccounts") { approved = true; return new Promise(() => undefined); }  // approves, never answers
        if (method === "eth_accounts") return approved ? ["0xabc0000000000000000000000000000000000001"] : [];
        if (method === "personal_sign") return "0xsig";
        return null;
      },
    };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ message: "hello", nonce: "n1" }), { status: 200 })));
    const out = await signWith("/members/me/wallet/nonce");
    expect(out).toEqual({ nonce: "n1", signature: "0xsig" });
    expect(calls).toContain("personal_sign");
    vi.unstubAllGlobals();
  }, 10_000);
});
