import { useCallback, useEffect, useRef, useState, type ClipboardEvent, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { MemberApiError, memberApi, refreshSession } from "./members";
import { SocialSignIn } from "./socialButtons";
import { WalletSignIn } from "./wallet";

/** Passwordless sign-in: email -> 6-digit code -> (new members only) username. The same form signs up and signs in.
 * The server never says whether an address has an account; codes are single use, expire, and only work in this browser. */

type Step = "email" | "code" | "username";
interface StartResp { sent: boolean; resend_after: number; expires_in: number }
interface VerifyResp { signed_in: boolean; created: boolean; needs_username: boolean; member: { username: string } }

const OPEN_EVENT = "pc-open-login";

/** Open the sign-in popup from anywhere (header Login, "Join" buttons, a mission that needs an account). */
export function openLogin(): void {
  window.dispatchEvent(new Event(OPEN_EVENT));
}

function useCountdown(): [number, (s: number) => void] {
  const [left, setLeft] = useState(0);
  useEffect(() => {
    if (left <= 0) return;
    const t = window.setTimeout(() => setLeft((v) => v - 1), 1000);
    return () => window.clearTimeout(t);
  }, [left]);
  return [left, setLeft];
}

function message(e: unknown): string {
  return e instanceof MemberApiError ? e.message : "Something went wrong. Please check your connection and try again.";
}

/** Six boxes that behave like one field: type, backspace, arrow keys, and paste (or SMS / mail autofill) of the whole code. */
function CodeBoxes({ value, onChange, disabled, onComplete }: { value: string; onChange: (v: string) => void; disabled?: boolean; onComplete: (v: string) => void }) {
  const refs = useRef<(HTMLInputElement | null)[]>([]);
  useEffect(() => { refs.current[0]?.focus(); }, []);
  const set = (next: string) => {
    const clean = next.replace(/\D/g, "").slice(0, 6);
    onChange(clean);
    refs.current[Math.min(clean.length, 5)]?.focus();
    if (clean.length === 6) onComplete(clean);
  };
  const onKey = (i: number) => (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Backspace" && !value[i] && i > 0) {
      e.preventDefault();
      onChange(value.slice(0, i - 1));
      refs.current[i - 1]?.focus();
    } else if (e.key === "ArrowLeft" && i > 0) refs.current[i - 1]?.focus();
    else if (e.key === "ArrowRight" && i < 5) refs.current[i + 1]?.focus();
  };
  const onPaste = (e: ClipboardEvent<HTMLInputElement>) => {
    const digits = e.clipboardData.getData("text").replace(/\D/g, "");
    if (digits) {
      e.preventDefault();
      set(digits);
    }
  };
  return (
    <div className="lg-code" role="group" aria-label="6-digit code">
      {Array.from({ length: 6 }, (_, i) => (
        <input key={i} ref={(el) => { refs.current[i] = el; }} className="lg-digit" inputMode="numeric" pattern="[0-9]*" maxLength={i === 0 ? 6 : 1}
               autoComplete={i === 0 ? "one-time-code" : "off"} aria-label={`Digit ${i + 1}`} value={value[i] ?? ""} disabled={disabled}
               onPaste={onPaste} onKeyDown={onKey(i)}
               onChange={(e) => {
                 const typed = e.target.value.replace(/\D/g, "");
                 if (typed.length > 1) set(value.slice(0, i) + typed); // autofill / fast typing into one box
                 else set(value.slice(0, i) + typed + value.slice(i + 1));
               }} />
      ))}
    </div>
  );
}

/** The whole flow. purpose "link": add a verified email to the signed-in member (Kiwi Passport). */
export function EmailLogin({ purpose = "login", onDone, autoFocus = true }: { purpose?: "login" | "link"; onDone?: (r: { created: boolean }) => void; autoFocus?: boolean }) {
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [username, setUsername] = useState("");
  const [nameNote, setNameNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [cooldown, setCooldown] = useCountdown();
  const created = useRef(false);

  const send = useCallback(async () => {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const r = await memberApi<StartResp>("/members/auth/email/start", { method: "POST", body: JSON.stringify({ email, purpose }) });
      setStep("code");
      setCode("");
      setCooldown(r.resend_after);
      setInfo(`We sent a 6-digit code to ${email.trim()}. It expires in ${Math.round(r.expires_in / 60)} minutes.`);
    } catch (e) {
      if (e instanceof MemberApiError && e.status === 429) {
        const m = /(\d+) seconds/.exec(e.message);
        if (m) setCooldown(Number(m[1]));
      }
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }, [email, purpose, setCooldown]);

  const verify = useCallback(async (c: string) => {
    setBusy(true);
    setError(null);
    try {
      const r = await memberApi<VerifyResp>("/members/auth/email/verify", { method: "POST", body: JSON.stringify({ email, code: c }) });
      created.current = r.created;
      refreshSession();
      if (purpose === "login" && r.needs_username) {
        setStep("username");
        setInfo(r.created ? "You're in. One last thing: choose the username the community will see." : "Welcome back. Choose your username to finish.");
      } else onDone?.({ created: r.created });
    } catch (e) {
      setError(message(e));
      setCode("");
    } finally {
      setBusy(false);
    }
  }, [email, purpose, onDone]);

  // live username check (debounced)
  useEffect(() => {
    if (step !== "username" || !username.trim()) {
      setNameNote(null);
      return;
    }
    const t = window.setTimeout(() => {
      void memberApi<{ ok: boolean; problem: string | null }>("/members/username/check", { method: "POST", body: JSON.stringify({ username: username.trim() }) })
        .then((r) => setNameNote(r.ok ? { ok: true, text: "Available" } : { ok: false, text: r.problem ?? "Not available" }))
        .catch(() => setNameNote(null));
    }, 350);
    return () => window.clearTimeout(t);
  }, [username, step]);

  const saveName = async () => {
    setBusy(true);
    setError(null);
    try {
      await memberApi("/members/me", { method: "PATCH", body: JSON.stringify({ username: username.trim() }) });
      refreshSession();
      onDone?.({ created: created.current });
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="lg-flow" aria-live="polite">
      {step === "email" && (
        <form className="lg-form" onSubmit={(e) => { e.preventDefault(); if (!busy && cooldown <= 0) void send(); }}>
          <label className="lg-label" htmlFor={`lg-email-${purpose}`}>Email address</label>
          <input id={`lg-email-${purpose}`} className="lg-input" type="email" inputMode="email" autoComplete="email" required autoFocus={autoFocus}
                 placeholder="you@example.com" value={email} onChange={(e) => setEmail(e.target.value)} disabled={busy} />
          <button className="site-button lg-submit" type="submit" disabled={busy || !email.trim() || cooldown > 0}>
            {busy ? "Sending…" : cooldown > 0 ? `Wait ${cooldown}s` : purpose === "link" ? "Send me a code" : "Continue with Email"}
          </button>
        </form>
      )}

      {step === "code" && (
        <form className="lg-form" onSubmit={(e) => { e.preventDefault(); if (code.length === 6 && !busy) void verify(code); }}>
          {info && <p className="lg-info">{info}</p>}
          <CodeBoxes value={code} onChange={setCode} disabled={busy} onComplete={(c) => void verify(c)} />
          <button className="site-button lg-submit" type="submit" disabled={busy || code.length !== 6}>{busy ? "Checking…" : "Verify code"}</button>
          <div className="lg-row">
            <button type="button" className="lg-text" disabled={busy || cooldown > 0} onClick={() => void send()}>
              {cooldown > 0 ? `Resend code in ${cooldown}s` : "Resend code"}
            </button>
            <button type="button" className="lg-text" onClick={() => { setStep("email"); setError(null); setInfo(null); }}>Use a different email</button>
          </div>
          <p className="lg-fine">Can't find it? Check spam or promotions. The code only works in this browser.</p>
        </form>
      )}

      {step === "username" && (
        <form className="lg-form" onSubmit={(e) => { e.preventDefault(); if (!busy && nameNote?.ok) void saveName(); }}>
          {info && <p className="lg-info">{info}</p>}
          <label className="lg-label" htmlFor="lg-username">Username</label>
          <input id="lg-username" className="lg-input" autoComplete="username" autoFocus maxLength={20} placeholder="e.g. KiwiCollector"
                 value={username} onChange={(e) => setUsername(e.target.value.replace(/\s/g, ""))} disabled={busy} />
          <p className={`lg-hint${nameNote ? (nameNote.ok ? " is-ok" : " is-bad") : ""}`}>{nameNote?.text ?? "3-20 letters, numbers or underscores. Public on the leaderboard."}</p>
          <button className="site-button lg-submit" type="submit" disabled={busy || !nameNote?.ok}>{busy ? "Saving…" : "Start earning XP"}</button>
        </form>
      )}

      {error && <p className="lg-error" role="alert">{error}</p>}
    </div>
  );
}

interface EmailAvailability { email?: { available: boolean } }

/** The sign-in popup, mounted once in the site header. */
export function LoginHost() {
  const [open, setOpen] = useState(false);
  const [available, setAvailable] = useState<boolean | null>(null);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const show = () => setOpen(true);
    window.addEventListener(OPEN_EVENT, show);
    return () => window.removeEventListener(OPEN_EVENT, show);
  }, []);
  useEffect(() => {
    if (!open) return;
    void memberApi<EmailAvailability>("/members/auth/providers").then((p) => setAvailable(!!p.email?.available)).catch(() => setAvailable(false));
    const esc = (e: globalThis.KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("keydown", esc);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // keyboard and screen-reader users land in the dialog, and go back to what opened it (e.g. Login) when it closes
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    box.current?.focus();
    return () => {
      document.removeEventListener("keydown", esc);
      document.body.style.overflow = prev;
      opener?.focus?.();
    };
  }, [open]);
  if (!open) return null;
  // a portal: the header may be a containing block (backdrop filter), the popup must cover the whole page
  return createPortal(
    <div className="lg-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(false); }}>
      <div className="lg-dialog" role="dialog" aria-modal="true" aria-labelledby="lg-title" ref={box} tabIndex={-1}>
        <button type="button" className="lg-close" aria-label="Close" onClick={() => setOpen(false)}>×</button>
        <LoginPanel available={available} onDone={({ created }) => { setOpen(false); location.assign(`/passport?welcome=${created ? "new" : "back"}`); }} />
      </div>
    </div>,
    document.body,
  );
}

/** Heading, one-tap Telegram / X / Google, then the email code (when email sign-in is set up). One Kiwi Passport either
 * way: new visitors get a Passport, returning members land in theirs (XP, rank and missions unchanged). */
export function LoginPanel({ available, onDone }: {
  available: boolean | null; onDone: (r: { created: boolean }) => void; legacyHref?: string | null;
}) {
  const telegramDone = () => {
    refreshSession();
    location.assign("/passport?welcome=telegram");
  };
  return (
    <div className="lg-panel">
      <img className="lg-kiwi" src="/brand/kiwi.webp" alt="" width={56} height={56} />
      <h2 className="lg-title" id="lg-title">Welcome to Price Chat</h2>
      <p className="lg-sub">Free to join. Earn XP for watching, reading and chatting, and rank up your Kiwi to unlock rewards.</p>
      <SocialSignIn onTelegramDone={telegramDone} />
      <WalletSignIn />
      {available && <><p className="lg-or"><span>or with your email</span></p><EmailLogin onDone={onDone} autoFocus={false} /></>}
      <p className="lg-fine">No password. Signing in only proves the account is yours: Price Chat never posts or messages for you unless you press a button to.{" "}
        <a className="site-link" href="/website/privacy">Privacy</a></p>
    </div>
  );
}
