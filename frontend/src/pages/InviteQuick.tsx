import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { request } from "../api/client";
import { relativeTime } from "../components/ui";

/** One-click invitations: one person at a time. SEND → NEXT → SEND → NEXT.
 *  - "auto": they asked for an invite, so the bot sends it from the selected account.
 *  - "personal": an active member who didn't ask; SEND opens their Telegram chat with the message typed in and YOU press send. */

interface Recipient { user_id: number; username: string | null; group: string | null; last_active_at: string; mode: "auto" | "personal"; why: string; open_url?: string }
interface View { account: { id: number; name: string; telegram: string | null }; recipient: Recipient | null; message: string; sent_today: number; daily_limit: number; paused_until: string | null }
interface Acc { id: number; name: string; status: string }

const post = <T,>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } });

export function InviteQuickPage() {
  const [accounts, setAccounts] = useState<Acc[]>([]);
  const [acc, setAcc] = useState<number | null>(() => Number(localStorage.getItem("ib-quick-acc")) || null);
  const [v, setV] = useState<View | null>(null);
  const [done, setDone] = useState<null | "sent" | "opened">(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void request<{ accounts: Acc[] }>("/invites").then((o) => {
      const ok = o.accounts.filter((a) => a.status === "connected" || a.status === "paused");
      setAccounts(ok);
      const want = new URLSearchParams(location.search).get("account")?.toLowerCase();  // ?account=LOTEX (bookmarkable)
      const byUrl = want ? ok.find((a) => a.name.toLowerCase() === want || String(a.id) === want) : undefined;
      if (byUrl) setAcc(byUrl.id);
      else if (!ok.some((a) => a.id === acc) && ok[0]) setAcc(ok[0].id);
    }).catch((e) => setErr(String(e)));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const load = useCallback(async () => {
    if (!acc) return;
    setErr(null); setDone(null);
    try { setV(await request<View>(`/invites/one?account_id=${acc}`)); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
  }, [acc]);
  useEffect(() => { if (acc) { localStorage.setItem("ib-quick-acc", String(acc)); void load(); } }, [acc, load]);

  const r = v?.recipient ?? null;
  const send = useCallback(async () => {
    if (!r || !acc || busy || done) return;
    setBusy(true); setErr(null);
    if (r.mode === "personal" && r.open_url) {
      window.open(r.open_url, "_blank", "noopener");  // opened in the click itself (no popup blocker)
      try { await navigator.clipboard.writeText(v!.message); } catch { /* the link already pre-fills the text */ }
    }
    try {
      const res = await post<{ status: "sent" | "opened" }>("/invites/one/send", { account_id: acc, user_id: r.user_id });
      setDone(res.status);
      setV((x) => (x ? { ...x, sent_today: x.sent_today + 1 } : x));
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }, [r, acc, busy, done, v]);
  const skip = useCallback(async () => {
    if (!r || !acc || busy) return;
    setBusy(true);
    try { await post("/invites/one/skip", { account_id: acc, user_id: r.user_id }); await load(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  }, [r, acc, busy, load]);
  const next = useCallback(() => { if (done) void load(); }, [done, load]);

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement)?.tagName === "SELECT") return;
      const key = e.key.toLowerCase();
      if (key === "s") void send(); else if (key === "k") void skip(); else if (key === "n" || (key === "enter" && done)) next();
    };
    document.addEventListener("keydown", k);
    return () => document.removeEventListener("keydown", k);
  }, [send, skip, next, done]);

  return (
    <div className="iq">
      <header className="iq-top">
        <h1>Send invites</h1>
        <select className="input" value={acc ?? ""} onChange={(e) => setAcc(Number(e.target.value))} aria-label="Account">
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <span className="iq-count" title="Invitations from this account today">{v ? `${v.sent_today} / ${v.daily_limit} today` : "—"}</span>
        <Link to="/invite-bot" className="muted small">Settings ›</Link>
      </header>

      {err && <div className="banner banner-error">{err}</div>}
      {v?.paused_until && <div className="banner banner-warn">Paused until {new Date(v.paused_until).toLocaleString()}: Telegram asked an account to slow down.</div>}

      {!v ? <p className="muted">Loading…</p> : !r ? (
        <div className="iq-card iq-empty"><h2>Nobody left to review</h2><p className="muted">New people appear as they chat in your watched groups (checked every 3 minutes).</p>
          <button className="btn" onClick={() => void load()}>Refresh</button></div>
      ) : (
        <div className="iq-card">
          <div className="iq-who">
            <a href={`https://t.me/${r.username}`} target="_blank" rel="noreferrer" className="iq-user">@{r.username}</a>
            <span className={`badge tone-${r.mode === "auto" ? "good" : "info"}`}>{r.mode === "auto" ? "Bot sends (they asked)" : "You send in Telegram"}</span>
          </div>
          <dl className="iq-facts">
            <div><dt>Group</dt><dd>{r.group ?? "—"}</dd></div>
            <div><dt>Last active</dt><dd>{relativeTime(r.last_active_at)}</dd></div>
            <div><dt>From</dt><dd>{v.account.name}{v.account.telegram ? ` (${v.account.telegram})` : ""}</dd></div>
          </dl>
          <p className="iq-msg">{v.message}</p>
          {r.mode === "personal" && !done && <p className="muted small">Send Message opens your chat with @{r.username} in Telegram (signed in as {v.account.name}) with this text typed in. Press send there.</p>}
          {done === "sent" && <p className="iq-ok">✓ Sent by {v.account.name}</p>}
          {done === "opened" && <p className="iq-ok">✓ Opened in Telegram with the message ready (also copied). Press send there, then Next.</p>}
          <div className="iq-buttons">
            <button className="btn btn-primary iq-big" disabled={busy || !!done} onClick={() => void send()}>{busy ? "…" : "SEND MESSAGE"} <kbd>S</kbd></button>
            <button className="btn iq-big" disabled={busy} onClick={() => void skip()}>SKIP <kbd>K</kbd></button>
            <button className="btn iq-big" disabled={busy || !done} onClick={next}>NEXT <kbd>N</kbd></button>
          </div>
        </div>
      )}
    </div>
  );
}
