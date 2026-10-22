import { useEffect, useRef, useState } from "react";
import { memberApi } from "./members";

/** A burner wallet's recovery phrase, encrypted in this browser with the member's password. Only this leaves the device. */
export interface Vault { v: 1; kdf: "PBKDF2-SHA256"; cipher: "AES-GCM"; iterations: number; salt: string; iv: string; ct: string }
export interface Burner { address: string; vault: Vault; created_at: string | null }

// PBKDF2-SHA256, OWASP's current figure. New vaults only: each vault stores its own count (older ones: 310,000) and decrypts with it.
const ITERATIONS = 600_000;
export const MIN_PASSWORD = 12;
// The most-used passwords and their bases: a stolen vault is guessed offline, and these go first. Also checked with digits and
// symbols stripped from the ends, so "Password2024!" counts as "password".
const COMMON = new Set(("password passw0rd qwerty qwertyuiop asdfghjkl zxcvbnm azerty letmein iloveyou admin administrator welcome " +
  "monkey dragon football baseball basketball soccer superman batman sunshine princess shadow master michael jordan trustno1 " +
  "starwars whatever freedom computer internet abc123 abcdef abcdefgh 123456 1234567890 123456789012 1q2w3e4r 1qaz2wsx " +
  "qazwsx changeme secret default bitcoin ethereum crypto metamask wallet pricechat passport burner").split(" "));

/** Why a backup password isn't good enough, or null when it is. */
export function passwordProblem(pw: string): string | null {
  if (pw.length < MIN_PASSWORD) return `Use at least ${MIN_PASSWORD} characters for the password.`;
  if (/^(.)\1*$/su.test(pw)) return "Not one character repeated: pick something harder to guess.";
  const low = pw.toLowerCase();
  if (COMMON.has(low) || COMMON.has(low.replace(/^[^a-z]+|[^a-z]+$/g, ""))) return "That's one of the most common passwords. Pick something less guessable.";
  return null;
}

/** A rough hint shown while typing: length and character variety. */
export function passwordStrength(pw: string): "weak" | "fair" | "strong" {
  if (passwordProblem(pw)) return "weak";
  const kinds = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((r) => r.test(pw)).length;
  return pw.length >= 16 || (pw.length >= 14 && kinds >= 3) ? "strong" : "fair";
}
const b64 = (buf: ArrayBuffer | Uint8Array) => btoa(String.fromCharCode(...new Uint8Array(buf instanceof Uint8Array ? buf : new Uint8Array(buf))));
const unb64 = (s: string) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

async function keyFrom(password: string, salt: Uint8Array, iterations: number): Promise<CryptoKey> {
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey({ name: "PBKDF2", salt: salt as BufferSource, iterations, hash: "SHA-256" }, base, { name: "AES-GCM", length: 256 }, false,
                                 ["encrypt", "decrypt"]);
}

export async function encryptPhrase(phrase: string, password: string, iterations = ITERATIONS): Promise<Vault> {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await keyFrom(password, salt, iterations), new TextEncoder().encode(phrase));
  return { v: 1, kdf: "PBKDF2-SHA256", cipher: "AES-GCM", iterations, salt: b64(salt), iv: b64(iv), ct: b64(ct) };
}

/** Throws on a wrong password (AES-GCM checks the tag), so a typo can never show a wrong phrase. */
export async function decryptPhrase(vault: Vault, password: string): Promise<string> {
  const key = await keyFrom(password, unb64(vault.salt), vault.iterations);
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(vault.iv) }, key, unb64(vault.ct));
  return new TextDecoder().decode(plain);
}

/** A fresh Ethereum wallet (12-word BIP-39 phrase, m/44'/60'/0'/0/0), made here; the library loads only when needed. */
export async function newBurner(): Promise<{ address: string; phrase: string }> {
  const { Wallet } = await import("ethers");
  const w = Wallet.createRandom();
  if (!w.mnemonic) throw new Error("No recovery phrase was generated. Try again.");
  return { address: w.address, phrase: w.mnemonic.phrase };
}

type Step = "loading" | "offline" | "none" | "password" | "have";

/** Kiwi Passport › Burner wallet: create one in a click, its recovery phrase saved encrypted to the Passport and revealed
 *  only with the member's password. Price Chat can't read it, and can't recover it if the password is lost. */
export function BurnerRow() {
  const [step, setStep] = useState<Step>("loading");
  const [burner, setBurner] = useState<Burner | null>(null);
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [phrase, setPhrase] = useState<string | null>(null);  // only while revealed
  const [shown, setShown] = useState(false);
  const [removing, setRemoving] = useState(false);
  const hideTimer = useRef<number | null>(null);

  useEffect(() => {
    memberApi<{ burner: Burner | null }>("/members/me/burner")
      .then((r) => { setBurner(r.burner); setStep(r.burner ? "have" : "none"); })
      .catch((e: unknown) => setStep(String((e as Error)?.message ?? "").includes("404") || String(e).includes("Not Found") ? "offline" : "none"));
    return () => { if (hideTimer.current) window.clearTimeout(hideTimer.current); };
  }, []);

  function hide() {
    setPhrase(null);
    setShown(false);
    if (hideTimer.current) window.clearTimeout(hideTimer.current);
  }

  async function create() {
    setErr(null);
    const problem = passwordProblem(pw);
    if (problem) return setErr(problem);
    if (pw !== pw2) return setErr("The two passwords don't match.");
    if (!ack) return setErr("Tick the box first: we can't recover the phrase without this password.");
    setBusy(true);
    try {
      const w = await newBurner();
      const vault = await encryptPhrase(w.phrase, pw);
      const r = await memberApi<{ burner: Burner }>("/members/me/burner", { method: "POST", body: JSON.stringify({ address: w.address, vault }) });
      setBurner(r.burner);
      setPw(""); setPw2(""); setAck(false);
      setStep("have");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't create the wallet. Try again.");
    } finally {
      setBusy(false);
    }
  }

  async function reveal() {
    if (!burner) return;
    setErr(null);
    setBusy(true);
    try {
      setPhrase(await decryptPhrase(burner.vault, pw));
      setPw("");
      if (hideTimer.current) window.clearTimeout(hideTimer.current);
      hideTimer.current = window.setTimeout(hide, 60_000);  // never left on screen
    } catch {
      setErr("Wrong password.");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!burner) return;
    setBusy(true);
    try {
      await memberApi("/members/me/burner", { method: "DELETE", body: JSON.stringify({ address: burner.address }) });
      hide(); setBurner(null); setRemoving(false); setStep("none");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Couldn't remove it.");
    } finally {
      setBusy(false);
    }
  }

  const [copied, setCopied] = useState<"address" | "phrase" | null>(null);
  const copy = (t: string, what: "address" | "phrase") => {
    void navigator.clipboard?.writeText(t).then(() => { setCopied(what); window.setTimeout(() => setCopied(null), 2000); })
      .catch(() => setErr("Couldn't copy: select the text and copy it yourself."));
  };
  if (step === "loading") return null;
  return (
    <li id="account-burner" className="pp-burner">
      <span className="pp-wallet-icon" aria-hidden>🔥</span>
      <span className="pp-account-name">
        <strong>Burner wallet</strong>
        {burner && <span className="site-muted"> {burner.address.slice(0, 6)}…{burner.address.slice(-4)}</span>}
      </span>
      {step === "none" && <button type="button" className="site-button pp-cta" onClick={() => { setErr(null); setStep("password"); }}>Create</button>}
      {step === "have" && burner && <button type="button" className="site-textbutton" onClick={() => copy(burner.address, "address")}>{copied === "address" ? "Copied ✓" : "Copy address"}</button>}

      {step === "offline" && <p className="pp-account-extra site-muted">Burner wallets are switching on shortly.</p>}
      {step === "none" && (
        <p className="pp-account-extra site-muted">A fresh wallet in one click, for small amounts. Its 12-word recovery phrase is encrypted on this device with a
          password you choose and kept hidden in your Passport. Price Chat can't read it, and can't recover it if you forget the password.</p>
      )}
      {step === "password" && (
        <form className="pp-account-extra pp-burner-form" onSubmit={(e) => { e.preventDefault(); void create(); }}>
          <label>Backup password<input type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} minLength={MIN_PASSWORD} required /></label>
          {pw && <p className="site-muted" aria-live="polite">Strength: {{ weak: "too weak", fair: "fair", strong: "strong" }[passwordStrength(pw)]}
            {passwordProblem(pw) ? ` (${passwordProblem(pw)})` : passwordStrength(pw) === "fair" ? " (a few unrelated words, 16+ characters, is stronger)" : ""}</p>}
          <label>Repeat it<input type="password" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} minLength={MIN_PASSWORD} required /></label>
          <label className="pp-burner-ack"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
            I understand Price Chat can't recover my recovery phrase if I forget this password.</label>
          <div className="pp-burner-actions">
            <button type="submit" className={`site-button pp-cta${busy ? " is-waiting" : ""}`} disabled={busy}>{busy ? "Creating…" : "Create wallet"}</button>
            <button type="button" className="site-textbutton" onClick={() => { setStep("none"); setErr(null); }}>Cancel</button>
          </div>
        </form>
      )}
      {step === "have" && burner && (
        <div className="pp-account-extra pp-burner-form">
          <p className="site-muted">Address: <code className="pp-burner-addr">{burner.address}</code></p>
          {phrase ? (
            <>
              <button type="button" className={`pp-burner-phrase${shown ? " is-shown" : ""}`} onClick={() => setShown(!shown)}
                      aria-label={shown ? "Recovery phrase (tap to hide)" : "Recovery phrase hidden (tap to show)"}>
                {phrase.split(" ").map((w, i) => <span key={i}><small>{i + 1}</small> {w}</span>)}
              </button>
              <p className="site-muted">Write these 12 words down on paper, in order. Anyone with them controls the wallet. Never share them,
                not even with Price Chat.</p>
              <div className="pp-burner-actions">
                <button type="button" className="site-textbutton" onClick={() => copy(phrase, "phrase")}>{copied === "phrase" ? "Copied ✓" : "Copy phrase"}</button>
                <button type="button" className="site-textbutton" onClick={hide}>Hide</button>
              </div>
            </>
          ) : (
            <form className="pp-burner-actions" onSubmit={(e) => { e.preventDefault(); void reveal(); }}>
              <input type="password" placeholder="Backup password" aria-label="Backup password" autoComplete="current-password" value={pw}
                     onChange={(e) => setPw(e.target.value)} required />
              <button type="submit" className={`site-button${busy ? " is-waiting" : ""}`} disabled={busy}>{busy ? "Unlocking…" : "Reveal recovery phrase"}</button>
            </form>
          )}
          {!removing ? <button type="button" className="site-textbutton pp-burner-remove" onClick={() => setRemoving(true)}>Remove from Passport…</button> : (
            <div className="pp-burner-actions">
              <span className="pp-connect-error">Back up the phrase first: removing it from your Passport can't be undone.</span>
              <button type="button" className="site-button" disabled={busy} onClick={() => void remove()}>Yes, remove</button>
              <button type="button" className="site-textbutton" onClick={() => setRemoving(false)}>Keep it</button>
            </div>
          )}
        </div>
      )}
      {err && <p className="pp-account-extra pp-connect-error" role="alert">{err}</p>}
    </li>
  );
}
