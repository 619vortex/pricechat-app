import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { refreshSession } from "../../../website/members";
import { WalletPage } from "./WalletPage";

/** TEST FIXTURES only: a fake API and a fake browser wallet that announces itself (EIP-6963). No real wallet or key. */
const calls: { method: string; path: string; body?: string }[] = [];
const ADDR = "0x2c7536E3605D9C16a7a3D7b1898e529396a65c23";

function serve(routes: Record<string, unknown>) {
  calls.length = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const path = String(input).split("?")[0];
    calls.push({ method: init?.method ?? "GET", path, body: init?.body as string | undefined });
    const body = routes[`${init?.method ?? "GET"} ${path}`] ?? routes[path];
    return body === undefined ? new Response(JSON.stringify({ detail: "nope" }), { status: 404 }) : new Response(JSON.stringify(body), { status: 200 });
  }));
}

function fakeWallet(name: string, rdns: string) {
  const request = vi.fn(async ({ method }: { method: string }) => (method === "eth_requestAccounts" ? [ADDR] : method === "personal_sign" ? "0x" + "ab".repeat(65) : null));
  const announce = () => window.dispatchEvent(new CustomEvent("eip6963:announceProvider", { detail: { info: { uuid: rdns, name, icon: "", rdns }, provider: { request } } }));
  window.addEventListener("eip6963:requestProvider", announce);
  return { request, off: () => window.removeEventListener("eip6963:requestProvider", announce) };
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Wallet", () => {
  it("finds installed wallets, has one sign Price Chat's message, and shows the verified payout wallet", async () => {
    const okx = fakeWallet("OKX Wallet", "com.okex.wallet");
    const mm = fakeWallet("MetaMask", "io.metamask");
    const linked = { id: 1, chain: "evm", address: ADDR, short: "0x2c75…5c23", wallet: "OKX Wallet", verified_at: "2026-10-11T00:00:00Z", payout: true };
    serve({ "/api/members/session": { signed_in: true, member: { username: "ash" } }, "/api/members/market/status": { mode: "simulation", networks: [], fees: {} },
            "/api/members/market/wallets": { wallets: [] },
            "POST /api/members/market/wallets/challenge": { address: ADDR, message: "Price Chat: confirm this wallet is yours." },
            "POST /api/members/market/wallets/verify": { wallets: [linked] } });
    refreshSession();
    render(<MemoryRouter><WalletPage /></MemoryRouter>);
    for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 150)); });
    const buttons = screen.getAllByRole("button", { name: /Connect / }).map((b) => b.textContent?.replace("👛", ""));
    expect(buttons).toEqual(["Connect OKX Wallet", "Connect MetaMask"]);  // OKX first, as suggested
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Connect OKX Wallet" })); });
    for (let i = 0; i < 3; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(okx.request).toHaveBeenCalledWith({ method: "personal_sign", params: [expect.stringMatching(/^0x/), ADDR] });
    expect(mm.request).not.toHaveBeenCalled();
    const verify = calls.find((c) => c.path === "/api/members/market/wallets/verify");
    expect(JSON.parse(verify?.body ?? "{}")).toMatchObject({ address: ADDR, wallet: "OKX Wallet" });
    expect(screen.getByText("💰 Payouts")).toBeTruthy();
    expect(screen.getByText(/Never share your seed phrase/)).toBeTruthy();
    okx.off(); mm.off();
  });
});

describe("Testnet payment", () => {
  it("builds the exact ERC-20 transfer the wallet signs", async () => {
    const { transferData } = await import("./wallet");
    expect(transferData("0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed", "250004321")).toBe(
      "0xa9059cbb0000000000000000000000005aaeb6053f3e94c9b9a09f33669435e7ef1beaed000000000000000000000000000000000000000000000000000000000ee6c361");
  });
});
