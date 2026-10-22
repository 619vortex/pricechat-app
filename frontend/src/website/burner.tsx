import { useEffect, useRef, useState } from "react";
import { memberApi } from "./members";

/** A burner wallet's recovery phrase, encrypted in this browser with the member's password. Only this leaves the device. */
export interface Vault { v: 1; kdf: "PBKDF2-SHA256"; cipher: "AES-GCM"; iterations: number; salt: string; iv: string; ct: string }
export interface Burner { address: string; vault: Vault; created_at: string | null }

const ITERATIONS = 310_000;  // PBKDF2-SHA256: OWASP's recommendation for this hash
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
    if (pw.length < 8) return setErr("Use at least 8 characters for the password.");
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

  const copy = (t: string) => { void navigator.clipboard?.writeText(t); };
  if (step === "loading") return null;
  return (
    <li id="account-burner" className="pp-burner">
      <span className="pp-wallet-icon" aria-hidden>🔥</span>
      <span className="pp-account-name">
        <strong>Burner wallet</strong>
        {burner && <span className="site-muted"> {burner.address.slice(0, 6)}…{burner.address.slice(-4)}</span>}
      </span>
      {step === "none" && <button type="button" className="site-button pp-cta" onClick={() => { setErr(null); setStep("password"); }}>Create</button>}
      {step === "have" && burner && <button type="button" className="site-textbutton" onClick={() => copy(burner.address)}>Copy address</button>}

      {step === "offline" && <p className="pp-account-extra site-muted">Burner wallets are switching on shortly.</p>}
      {step === "none" && (
        <p className="pp-account-extra site-muted">A fresh wallet in one click, for small amounts. Its 12-word recovery phrase is encrypted on this device with a
          password you choose and kept hidden in your Passport. Price Chat can't read it, and can't recover it if you forget the password.</p>
      )}
      {step === "password" && (
        <form className="pp-account-extra pp-burner-form" onSubmit={(e) => { e.preventDefault(); void create(); }}>
          <label>Backup password<input type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} minLength={8} required /></label>
          <label>Repeat it<input type="password" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} minLength={8} required /></label>
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
                <button type="button" className="site-textbutton" onClick={() => copy(phrase)}>Copy phrase</button>
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
