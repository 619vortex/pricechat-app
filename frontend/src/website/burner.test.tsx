import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BurnerRow } from "./burner";

/** TEST FIXTURES only: a fake Passport API with a made-up burner address and vault. */
const flush = async () => { for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); }); };
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Burner wallet", () => {
  it("Copy address says it worked", async () => {
    const vault = { v: 1, kdf: "PBKDF2-SHA256", cipher: "AES-GCM", iterations: 1, salt: "", iv: "", ct: "" };
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ burner: { address: "0x1234567890abcdef1234567890abcdef12345678", vault, created_at: null } }))));
    const write = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText: write }, configurable: true });
    render(<ul><BurnerRow /></ul>);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Copy address" }));
    await flush();
    expect(write).toHaveBeenCalledWith("0x1234567890abcdef1234567890abcdef12345678");
    expect(screen.getByRole("button", { name: "Copied ✓" })).toBeTruthy();
  });
});
