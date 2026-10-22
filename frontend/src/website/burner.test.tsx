import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BurnerRow, CLIPBOARD_CLEAR_MS, clearClipboard, encryptPhrase } from "./burner";

/** TEST FIXTURES only: a fake Passport API with a made-up burner address and a throwaway phrase. */
const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });
const ADDRESS = "0x1234567890abcdef1234567890abcdef12345678";
const PHRASE = "test only words never a real wallet one two three four five six seven";
function clipboard(read: () => Promise<string>) {
  const write = vi.fn(async () => undefined);
  Object.defineProperty(navigator, "clipboard", { value: { writeText: write, readText: vi.fn(read) }, configurable: true });
  return write;
}

describe("Burner wallet", () => {
  it("Copy address says it worked", async () => {
    const vault = { v: 1, kdf: "PBKDF2-SHA256", cipher: "AES-GCM", iterations: 1, salt: "", iv: "", ct: "" };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ burner: { address: ADDRESS, vault, created_at: null } }))));
    const write = clipboard(async () => "");
    render(<ul><BurnerRow /></ul>);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Copy address" }));
    await flush();
    expect(write).toHaveBeenCalledWith(ADDRESS);
    expect(screen.getByRole("button", { name: "Copied ✓" })).toBeTruthy();
  });

  it("a copied recovery phrase is wiped from the clipboard after 60 s, and the page says so", async () => {
    const vault = await encryptPhrase(PHRASE, "correct horse battery", 1000);
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ burner: { address: ADDRESS, vault, created_at: null } }))));
    const write = clipboard(async () => PHRASE);
    render(<ul><BurnerRow /></ul>);
    await flush();
    fireEvent.change(screen.getByLabelText("Backup password"), { target: { value: "correct horse battery" } });
    fireEvent.click(screen.getByRole("button", { name: "Reveal recovery phrase" }));
    await flush();
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "Copy phrase" }));
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(write).toHaveBeenLastCalledWith(PHRASE);
    expect(screen.getByRole("status").textContent).toMatch(/clipboard will be cleared in 60s/);
    await act(async () => { await vi.advanceTimersByTimeAsync(CLIPBOARD_CLEAR_MS); });
    expect(write).toHaveBeenLastCalledWith("");
  });

  it("clearing only touches the clipboard while it still holds the phrase, and clears anyway when it can't be read", async () => {
    let write = clipboard(async () => "something the member copied since");
    await clearClipboard(PHRASE);
    expect(write).not.toHaveBeenCalled();
    write = clipboard(async () => PHRASE);
    await clearClipboard(PHRASE);
    expect(write).toHaveBeenCalledWith("");
    write = clipboard(async () => { throw new DOMException("denied", "NotAllowedError"); });
    await clearClipboard(PHRASE);
    expect(write).toHaveBeenCalledWith("");
  });
});
