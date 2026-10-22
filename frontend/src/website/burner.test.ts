// @vitest-environment node
import { describe, expect, it } from "vitest";
import { decryptPhrase, encryptPhrase, newBurner } from "./burner";

/** The burner wallet's browser-side crypto. TEST DATA only: a made-up phrase and passwords. */
describe("burner wallet", () => {
  it("makes a real 12-word wallet with a checksummed address", async () => {
    const w = await newBurner();
    expect(w.phrase.split(" ")).toHaveLength(12);
    expect(w.address).toMatch(/^0x[0-9a-fA-F]{40}$/);
    const { Wallet } = await import("ethers");
    expect(Wallet.fromPhrase(w.phrase).address).toBe(w.address);  // the phrase really restores this wallet
  });

  it("encrypts with the member's password; only that password opens it", async () => {
    const phrase = "test test test test test test test test test test test junk";
    const vault = await encryptPhrase(phrase, "kiwi-backup-1", 1000);  // few iterations: speed only (the app uses 310,000)
    expect(vault).toMatchObject({ v: 1, kdf: "PBKDF2-SHA256", cipher: "AES-GCM", iterations: 1000 });
    expect(JSON.stringify(vault)).not.toContain("test test");  // what leaves the device is ciphertext
    expect(await decryptPhrase(vault, "kiwi-backup-1")).toBe(phrase);
    await expect(decryptPhrase(vault, "kiwi-backup-2")).rejects.toThrow();  // a wrong password never shows a wrong phrase
    const again = await encryptPhrase(phrase, "kiwi-backup-1", 1000);
    expect(again.salt).not.toBe(vault.salt);  // fresh salt and IV every time
    expect(again.iv).not.toBe(vault.iv);
  });
});
