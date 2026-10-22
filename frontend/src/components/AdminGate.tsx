import { createContext, useCallback, useContext, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { ADMIN_SIGNED_OUT } from "../api/client";

/**
 * The owner login in front of the internal dashboard (/admin). The server is what actually protects the admin APIs
 * (every one answers 401 without a session); this screen is just the way in. When the server doesn't ask for a login
 * (local development without ADMIN_PASSWORD_HASH), the dashboard opens as before.
 */
type Session = { required: boolean; authenticated: boolean; username: string | null };

const AdminContext = createContext<{ session: Session | null; signOut: () => Promise<void> }>({ session: null, signOut: async () => {} });
export const useAdmin = () => useContext(AdminContext);

export function AdminGate({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/admin/session", { credentials: "same-origin" });
      if (!res.ok) throw new Error(String(res.status));
      setSession(await res.json());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    void load();
    const out = () => setSession((s) => (s ? { ...s, authenticated: false, username: null } : s));
    window.addEventListener(ADMIN_SIGNED_OUT, out);
    return () => window.removeEventListener(ADMIN_SIGNED_OUT, out);
  }, [load]);

  const signOut = useCallback(async () => {
    try {
      await fetch("/api/admin/logout", { method: "POST", credentials: "same-origin" });
    } finally {
      setSession((s) => (s ? { ...s, authenticated: false, username: null } : s));
    }
  }, []);

  if (failed) return <Centered><p className="muted">Can't reach the Price Chat backend.</p><button className="btn" onClick={() => void load()}>Try again</button></Centered>;
  if (!session) return <Centered><p className="muted">Loading…</p></Centered>;
  if (session.required && !session.authenticated) return <Login onDone={load} />;
  return <AdminContext.Provider value={{ session, signOut }}>{children}</AdminContext.Provider>;
}

function Centered({ children }: { children: ReactNode }) {
  return <div className="admin-login"><div className="card admin-login-card">{children}</div></div>;
}

function Login({ onDone }: { onDone: () => Promise<void> }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch("/api/admin/login", {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (res.ok) {
        setPassword("");
        await onDone();
        return;
      }
      const body = await res.json().catch(() => ({}));
      setError(typeof body.detail === "string" ? body.detail : "Sign-in failed.");
    } catch {
      setError("Can't reach the Price Chat backend.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Centered>
      <h1 className="admin-login-title">Price Chat admin</h1>
      <p className="muted small">The internal dashboard. Owner sign-in only.</p>
      <form className="form" onSubmit={(e) => void submit(e)}>
        <label className="small">Username
          <input className="input" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
        </label>
        <label className="small">Password
          <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <p className="admin-login-error" role="alert">{error}</p>}
        <button className="btn btn-primary" type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </Centered>
  );
}
