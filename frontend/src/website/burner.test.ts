// @vitest-environment node
import { describe, expect, it } from "vitest";
import { decryptPhrase, encryptPhrase, newBurner, passwordProblem, passwordStrength } from "./burner";

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
    const vault = await encryptPhrase(phrase, "kiwi-backup-1", 1000);  // few iterations: speed only (new vaults use 600,000)
    expect(vault).toMatchObject({ v: 1, kdf: "PBKDF2-SHA256", cipher: "AES-GCM", iterations: 1000 });
    expect(JSON.stringify(vault)).not.toContain("test test");  // what leaves the device is ciphertext
    expect(await decryptPhrase(vault, "kiwi-backup-1")).toBe(phrase);
    await expect(decryptPhrase(vault, "kiwi-backup-2")).rejects.toThrow();  // a wrong password never shows a wrong phrase
    const again = await encryptPhrase(phrase, "kiwi-backup-1", 1000);
    expect(again.salt).not.toBe(vault.salt);  // fresh salt and IV every time
    expect(again.iv).not.toBe(vault.iv);
  });

  it("still opens a vault made with the old 310,000 iterations: each vault carries its own count", async () => {
    const phrase = "test test test test test test test test test test test junk";
    const old = await encryptPhrase(phrase, "kiwi-backup-1", 310_000);
    expect(old.iterations).toBe(310_000);
    expect(await decryptPhrase(old, "kiwi-backup-1")).toBe(phrase);
    await expect(decryptPhrase({ ...old, iterations: 600_000 }, "kiwi-backup-1")).rejects.toThrow();  // the stored count is the one used
  });

  it("refuses short, repeated and common backup passwords", () => {
    expect(passwordProblem("kiwi-backup")).toMatch(/12 characters/);  // 11
    expect(passwordProblem("aaaaaaaaaaaaaaaa")).toMatch(/repeated/);
    for (const common of ["123456789012", "qwertyuiop12", "Password2024!", "!!iloveyou!!!", "Qwertyuiop123"]) expect(passwordProblem(common)).toMatch(/common/);
    expect(passwordProblem("kiwi-backup-12")).toBeNull();
    expect(passwordStrength("aaaaaaaaaaaa")).toBe("weak");
    expect(passwordStrength("kiwibackup12x")).toBe("fair");
    expect(passwordStrength("orbit Kiwi lantern 7")).toBe("strong");
  });
});
