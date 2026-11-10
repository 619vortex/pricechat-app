import { useState } from "react";
import { openSite, sendSupport, type SupportKind } from "./vpn";

/** Price Chat takes at most this many characters (backend app/vpn/support.py MAX_MESSAGE; the Rust side checks it too). */
export const MAX_MESSAGE = 4000;

/** Tools › Support (2.1): a bug report or feedback to Price Chat. The diagnostics are put together and stripped by the app's Rust side; the page only says whether to include them. */
export function Support({ signedIn }: { signedIn: boolean }) {
  const [kind, setKind] = useState<SupportKind>("bug");
  const [message, setMessage] = useState("");
  const [include, setInclude] = useState(true);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const pick = (k: SupportKind) => { setKind(k); setInclude(k === "bug"); setResult(null); };
  const send = async () => {
    setBusy(true); setResult(null);
    try {
      await sendSupport(kind, message.trim(), include);
      setMessage("");
      setResult({ ok: true, text: "Sent, thank you." });
    } catch (e) { setResult({ ok: false, text: String(e) }); }
    finally { setBusy(false); }
  };
  if (!signedIn) {
    return (
      <section className="support" aria-label="Support">
        <h2>Support</h2>
        <p className="hint">Sign in with Price Chat to send a bug report or feedback from here. You can also reach us on the website.</p>
        <button type="button" className="row" onClick={() => void openSite("contact")}>Contact Price Chat</button>
      </section>
    );
  }
  const length = message.trim().length;
  return (
    <section className="support" aria-label="Support">
      <h2>Support</h2>
      <p className="hint">Tell us what went wrong, or what you'd like. It goes to the Price Chat team with your account name.</p>
      <div className="kinds" role="radiogroup" aria-label="Kind of report">
        {(["bug", "feedback"] as const).map((k) => (
          <button key={k} type="button" role="radio" aria-checked={kind === k} className={kind === k ? "is-picked" : ""} disabled={busy} onClick={() => pick(k)}>
            {k === "bug" ? "Bug" : "Feedback"}
          </button>
        ))}
      </div>
      <textarea aria-label="Message" rows={5} maxLength={MAX_MESSAGE} value={message} disabled={busy}
                placeholder={kind === "bug" ? "What happened, and what did you expect?" : "What should we know?"}
                onChange={(e) => { setMessage(e.target.value); setResult(null); }} />
      <label className="check">
        <input type="checkbox" checked={include} disabled={busy} onChange={(e) => setInclude(e.target.checked)} />
        <span>Include diagnostics (no keys, no addresses of your home network)<small>App version, connection state, which options are on and the app's recent log. Keys and every IP address are removed first.</small></span>
      </label>
      <button type="button" className="row" disabled={busy || length === 0 || length > MAX_MESSAGE} onClick={() => void send()}>{busy ? "Sending…" : "Send"}</button>
      {result && <p className={result.ok ? "result" : "problem"} role={result.ok ? "status" : "alert"}>{result.text}</p>}
    </section>
  );
}
