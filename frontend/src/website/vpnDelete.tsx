import { useCallback, useEffect, useState } from "react";
import { SitePage } from "./community";
import { openLogin } from "./emailLogin";
import { MemberApiError, memberApi } from "./members";
import { pageHead } from "./seo";
import "./vpn.css";

/**
 * The public account-deletion page that Google Play's form links to (and that the apps open): it names the app, says how to
 * delete in each app and here, and lists what's deleted and kept. The lists are the apps' own (android-client
 * AccountDeletion.kt, ios-client AccountDeletion.swift), from members.service.delete_member and the database's cascades.
 * Deleting here is the same DELETE /api/members/me the apps call, with the same typed-username check.
 */

export const WHAT_IS_DELETED = [
  "Your Price Chat account (Kiwi Passport): username, display name, profile and sign-in methods (email, Google, X, Telegram)",
  "Your XP, rank history, badges, missions and referral code",
  "Your Kiwi VPN account: plan, free trial and any time left on it, which ends straight away",
  "Every Kiwi VPN device on this account, on every phone and PC. They stop working; servers drop them at their next check",
  "Browser extension logins and support reports you sent",
  "Every signed-in session, in the apps and in browsers",
];

export const WHAT_IS_KEPT = [
  "One-way codes showing a free trial or a one-time reward was used, so a new account with the same sign-in can't claim it again. They hold no name or address",
  "Records of any payments, with the link to your account removed, for as long as tax and accounting law requires",
  "A log line that an account was deleted, with no name",
];

type Me = { member?: { username?: string; display_name?: string } };

export function VpnDeleteAccountPage() {
  const [me, setMe] = useState<Me | null | "loading">("loading");  // null: signed out
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const check = useCallback(async () => {
    try { setMe(await memberApi<Me>("/members/me")); }
    catch (e) { setMe(null); if (!(e instanceof MemberApiError && e.status === 401)) setError("Couldn't reach Price Chat. Please try again."); }
  }, []);
  useEffect(() => { void check(); }, [check]);
  useEffect(() => {  // notices the sign-in finishing in the popup
    if (me !== null || done) return;
    const id = window.setInterval(() => void check(), 4000);
    return () => window.clearInterval(id);
  }, [me, done, check]);
  const username = me && me !== "loading" ? me.member?.username ?? "" : "";
  async function remove(e: React.FormEvent) {
    e.preventDefault();
    if (!username || typed.trim() !== username) { setError(`Type your username exactly as shown: ${username}`); return; }
    setBusy(true); setError(null);
    try {
      await memberApi("/members/me", { method: "DELETE", body: JSON.stringify({ confirm_username: typed.trim() }) });
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Couldn't delete the account. Please try again.");
    } finally { setBusy(false); }
  }
  return (
    <SitePage {...pageHead("vpn/delete-account")}>
      <article className="site-legal site-reveal">
        <h1 className="site-display site-h1">Delete your Kiwi VPN account</h1>
        <p className="site-body">
          Kiwi VPN (Android, iPhone, Windows and the browser extension), by Price Chat, uses your Price Chat account, also called your Kiwi Passport.
          Deleting that account deletes your Kiwi VPN account with it. It can't be undone.
        </p>

        <section id="in-the-app" aria-labelledby="del-app">
          <h2 id="del-app" className="site-legal-h">In the app</h2>
          <ul className="site-body">
            <li><b>Android:</b> Settings › Account › Delete account. Type your username to confirm.</li>
            <li><b>iPhone:</b> Settings › Delete account. Type your username to confirm.</li>
          </ul>
          <p className="site-muted">The app then disconnects and erases its saved session, VPN keys and settings. Uninstalling the app on its own doesn't delete your account.</p>
        </section>

        <section id="on-the-web" className="vpn-card vpn-start" aria-labelledby="del-web">
          <h2 id="del-web">Delete it here</h2>
          {done ? (
            <p className="site-muted" role="status">Your account has been deleted. Kiwi VPN on your devices stops working at the servers' next check. You can close this page.</p>
          ) : me === "loading" ? (
            <p className="site-muted" role="status">Checking your account…</p>
          ) : me === null ? (
            <>
              <p className="site-muted">Sign in with the account you use for Kiwi VPN. This page updates once you're signed in.</p>
              <button className="site-button" onClick={openLogin}>Sign in</button>
            </>
          ) : (
            <form className="vpn-form" onSubmit={(e) => void remove(e)}>
              <p className="site-muted">Signed in as <b>{username || "…"}</b>. To confirm, type your username.</p>
              <label className="site-field"><span>Username</span>
                <input autoComplete="off" value={typed} onChange={(e) => setTyped(e.target.value)} /></label>
              <button className="site-button" type="submit" disabled={busy || !username || typed.trim() !== username}>{busy ? "Deleting…" : "Delete my account"}</button>
            </form>
          )}
          {error && <p className="site-error" role="alert">{error}</p>}
        </section>

        <section id="whats-deleted" aria-labelledby="del-what">
          <h2 id="del-what" className="site-legal-h">What's deleted</h2>
          <ul className="site-body">{WHAT_IS_DELETED.map((t) => <li key={t}>{t}</li>)}</ul>
          <h2 className="site-legal-h">What's kept</h2>
          <ul className="site-body">{WHAT_IS_KEPT.map((t) => <li key={t}>{t}</li>)}</ul>
          <p className="site-muted">Deletion happens straight away. Nothing else is kept afterwards, apart from our hosting providers' ordinary request logs, kept for as long as those providers keep their logs (see the <a className="site-link" href="/vpn/privacy">Kiwi VPN privacy page</a>).</p>
        </section>

        <section id="cant-sign-in" aria-labelledby="del-help">
          <h2 id="del-help" className="site-legal-h">Can't sign in any more?</h2>
          <p className="site-body">
            Ask us to delete it through the <a className="site-link" href="/contact">contact page</a>, ideally from the Telegram or X account linked to it.
            We'll check that the account is yours before deleting anything, and answer within one month. Never send us a password or a VPN key.
          </p>
        </section>
      </article>
    </SitePage>
  );
}
