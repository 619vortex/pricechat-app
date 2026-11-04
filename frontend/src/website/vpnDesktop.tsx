import { useCallback, useEffect, useState } from "react";
import { SitePage } from "./community";
import { openLogin } from "./emailLogin";
import { MemberApiError, memberApi } from "./members";
import "./vpn.css";

/**
 * Signing in to the Kiwi VPN desktop app. The app opens this page with its challenge, a loopback port and a state. The person presses Allow, and a one-time
 * code goes to 127.0.0.1 only. The code is useless without the secret the app kept, so another site sending someone here gains nothing. See routes/desktop.py.
 */

export interface DesktopRequest { challenge: string; port: number; state: string }

/** null unless every part is exactly what the app sends: nothing from the address bar reaches a redirect otherwise. */
export function readRequest(search: string): DesktopRequest | null {
  const q = new URLSearchParams(search);
  const challenge = q.get("challenge") ?? "", state = q.get("state") ?? "", raw = q.get("port") ?? "";
  const port = /^\d{4,5}$/.test(raw) ? Number(raw) : 0;
  if (!/^[A-Za-z0-9_-]{43}$/.test(challenge) || !/^[A-Za-z0-9_-]{16,64}$/.test(state) || port < 1024 || port > 65535) return null;
  return { challenge, port, state };
}

export function callbackUrl(r: DesktopRequest, code: string): string {
  return `http://127.0.0.1:${r.port}/callback?code=${encodeURIComponent(code)}&state=${encodeURIComponent(r.state)}`;
}

type Who = { username?: string; display_name?: string } | null;

export function VpnDesktopPage({ search = location.search, go = (url: string) => location.assign(url) }: { search?: string; go?: (url: string) => void }) {
  const [request] = useState(() => readRequest(search));
  const [who, setWho] = useState<Who | "loading">("loading");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const check = useCallback(async () => {
    try { setWho(await memberApi<NonNullable<Who>>("/members/me")); }
    catch (e) { setWho(null); if (!(e instanceof MemberApiError && e.status === 401)) setError("Couldn't reach Price Chat. Please try again."); }
  }, []);
  useEffect(() => {
    void check();
    const id = window.setInterval(() => void check(), 4000);  // notices the sign-in finishing in the popup
    return () => window.clearInterval(id);
  }, [check]);
  async function allow() {
    if (!request) return;
    setBusy(true); setError(null);
    try {
      const { code } = await memberApi<{ code: string }>("/members/desktop/code", { method: "POST", body: JSON.stringify({ challenge: request.challenge }) });
      setDone(true);
      go(callbackUrl(request, code));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't sign in. Please try again.");
    } finally { setBusy(false); }
  }
  const name = who && who !== "loading" ? who.display_name ?? who.username : null;
  return (
    <SitePage title="Sign in to Kiwi VPN for Windows" description="Connect the Kiwi VPN app to your Price Chat account." path="/vpn/desktop" noindex>
      <section className="vpn">
        <section className="vpn-card vpn-start" aria-labelledby="vpn-desktop">
          <h2 id="vpn-desktop">Kiwi VPN for Windows</h2>
          {!request ? <p className="site-error" role="alert">This link isn't valid. Open Kiwi VPN on your PC and press Sign in.</p>
            : done ? <p className="site-muted" role="status">You're signed in. You can close this tab and go back to Kiwi VPN.</p>
            : who === "loading" ? <p className="site-muted" role="status">Checking your account…</p>
            : who === null ? (
              <>
                <p className="site-muted">Sign in with your Price Chat account to use Kiwi VPN on this PC.</p>
                <button className="site-button" onClick={openLogin}>Sign in</button>
              </>
            ) : (
              <>
                <p className="site-muted">Allow the Kiwi VPN app on <b>this PC</b> to use {name ? <b>{name}</b> : "your account"}? Only continue if you just pressed Sign in in the app.</p>
                <button className="site-button" disabled={busy} onClick={() => void allow()}>{busy ? "Signing in…" : "Allow"}</button>
              </>
            )}
          {error && <p className="site-error" role="alert">{error}</p>}
        </section>
      </section>
    </SitePage>
  );
}
