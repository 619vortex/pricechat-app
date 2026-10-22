import { useEffect, useState, type FormEvent } from "react";
import QRCode from "qrcode";
import { api } from "../../api/client";
import { useApi } from "../../api/useApi";
import type { TelegramAccountStatus, TelegramConnection, TelegramDialog, TelegramSource } from "../../api/types";
import { ApiErrorBanner, Badge, Card, Empty, Loading, formatTime, relativeTime, type Tone } from "../../components/ui";

const STATUS: Record<string, [string, Tone]> = {
  connected: ["Connected", "good"],
  disconnected: ["Disconnected", "warn"],
  needs_login: ["Needs login", "warn"],
  code_sent: ["Waiting for code", "info"],
  password_needed: ["Waiting for 2FA password", "info"],
  error: ["Error", "bad"],
  not_configured: ["Not set up", "neutral"],
};

export function StatusBadge({ status }: { status: string }) {
  const [text, tone] = STATUS[status] ?? [status, "neutral" as Tone];
  return <Badge tone={tone}>{text}</Badge>;
}

/** TELEGRAM: connection type, status, identity, session, sources, today's messages, processing. */
export function TelegramStatusCard({ data }: { data: TelegramConnection }) {
  const who = [
    data.bot.configured && data.bot.username ? `bot @${data.bot.username}` : null,
    data.account.session === "active" ? `${data.account.display_name ?? "account"}${data.account.username ? ` (@${data.account.username})` : ""}` : null,
  ].filter(Boolean);
  const lastOk = [data.bot.last_success_at, data.account.last_success_at].filter(Boolean).sort().pop() ?? null;
  return (
    <Card title="Telegram">
      <dl className="kv">
        <dt>Connection type</dt>
        <dd>{data.mode.length ? data.mode.map((m) => (m === "bot" ? "Bot" : "Account")).join(" + ") : "none"}</dd>
        <dt>Status</dt>
        <dd>
          <StatusBadge status={data.status} />
        </dd>
        <dt>Connected identity</dt>
        <dd>{who.length ? who.join(" · ") : <span className="muted">-</span>}</dd>
        <dt>Last successful Telegram request</dt>
        <dd>{relativeTime(lastOk)}</dd>
        <dt>Session</dt>
        <dd>{data.account.configured ? (data.account.session === "active" ? "active (stored encrypted on this computer)" : "none") : <span className="muted">no account session</span>}</dd>
        <dt>Authorised sources</dt>
        <dd>
          {data.sources.enabled} enabled of {data.sources.total} ({data.sources.bot} via bot, {data.sources.account} via account)
        </dd>
        <dt>Messages ingested today</dt>
        <dd>{data.messages_today}</dd>
        <dt>Last message received</dt>
        <dd>{relativeTime(data.last_message_at)}</dd>
        <dt>Last processing run</dt>
        <dd>{relativeTime(data.last_processing_at)}</dd>
        {(data.bot.last_error || data.account.last_error) && (
          <>
            <dt>Last error</dt>
            <dd className="tone-bad">{data.account.last_error ?? data.bot.last_error}</dd>
          </>
        )}
      </dl>
    </Card>
  );
}

/** Telegram account mode: official client API login, group discovery, explicit authorisation. */
export function AccountSetup({ onChanged }: { onChanged: () => void }) {
  const { data, failure, setData, reload } = useApi(api.tgAccount);
  const [apiId, setApiId] = useState("");
  const [apiHash, setApiHash] = useState("");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [dialogs, setDialogs] = useState<TelegramDialog[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [qr, setQr] = useState<string | null>(null);  // QR login: the image to scan, while waiting

  async function startQr() {
    setBusy(true);
    try {
      const r = await api.tgQrStart();
      setData(r.account);
      setQr(r.url ? await QRCode.toDataURL(r.url, { width: 280, margin: 1 }) : null);
      setError(null);
      setNotice(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }
  // while the QR is shown: ask every 2 s whether it was scanned (and refresh the QR when Telegram rotates the token)
  useEffect(() => {
    if (!qr) return;
    let stop = false;
    const id = window.setInterval(async () => {
      try {
        const r = await api.tgQrPoll();
        if (stop) return;
        setData(r.account);
        if (r.status === "connected") { setQr(null); setNotice("Connected ✓ Now press “Show my groups and channels”."); onChanged(); }
        else if (r.status === "password_needed") setQr(null);
        else if (r.url) setQr(await QRCode.toDataURL(r.url, { width: 280, margin: 1 }));
      } catch (e) {
        setQr(null);
        setError(e);
      }
    }, 2000);
    return () => { stop = true; window.clearInterval(id); };
  }, [qr !== null]);  // eslint-disable-line react-hooks/exhaustive-deps

  async function run(fn: () => Promise<TelegramAccountStatus | unknown>, msg?: string) {
    setBusy(true);
    try {
      const r = await fn();
      if (r && typeof r === "object" && "status" in (r as object) && "session" in (r as object)) setData(r as TelegramAccountStatus);
      else await reload();
      setError(null);
      setNotice(msg ?? null);
      onChanged();
    } catch (e) {
      setError(e);
      setNotice(null);
    } finally {
      setBusy(false);
    }
  }
  async function refreshDialogs() {
    setBusy(true);
    try {
      setDialogs(await api.tgDialogs());
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  }

  if (!data) return failure ? <ApiErrorBanner error={failure} /> : <Loading />;
  const s = data.status;
  return (
    <Card title="Telegram account (official client API)">
      <p className="muted small">
        Price Chat logs in as a Telegram account that is already a member of the Price Chat groups, using Telegram's official client API (the same one
        Telegram's own apps use). It only reads the groups and channels you authorise below; private chats are never listed or read, contacts are never
        read, and nothing is ever sent.
      </p>
      {notice && <div className="banner banner-good">{notice}</div>}
      <ApiErrorBanner error={error} />

      {!data.configured && (
        <form
          className="stack"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            const key = apiHash.trim();
            setApiHash("");
            void run(() => api.tgAccountCredentials(Number(apiId), key), "API ID and API Hash saved (encrypted on this computer).");
          }}
        >
          <div className="field-label">Step 1 · API ID and API Hash</div>
          <p className="muted small">
            Get them at{" "}
            <a className="link" href="https://my.telegram.org" target="_blank" rel="noreferrer">
              my.telegram.org
            </a>{" "}
            › API development tools (log in with your phone number, create an app with any name).
          </p>
          <div className="row">
            <input className="input" inputMode="numeric" placeholder="API ID (a number)" value={apiId} onChange={(e) => setApiId(e.target.value.replace(/\D/g, ""))} />
            <input className="input secret-input" type="password" autoComplete="off" spellCheck={false} placeholder="API Hash (32 characters)" value={apiHash} onChange={(e) => setApiHash(e.target.value)} />
            <button className="btn btn-primary" disabled={busy || !apiId || apiHash.trim().length !== 32}>
              Save
            </button>
          </div>
        </form>
      )}

      {data.configured && (s === "needs_login" || s === "error" || (s === "not_configured" && data.session === "none")) && data.session === "none" && (
        <form
          className="stack"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            void run(() => api.tgLogin(phone.trim()), "Telegram sent a login code to your Telegram app (or by SMS).");
          }}
        >
          <div className="field-label">Step 2 · Phone number of the Telegram account</div>
          <div className="row">
            <input className="input" type="tel" autoComplete="off" placeholder="+44 7700 900123 (with country code)" value={phone} onChange={(e) => setPhone(e.target.value)} />
            <button className="btn btn-primary" disabled={busy || phone.trim().length < 7}>
              {busy ? "Sending…" : "Send login code"}
            </button>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <span className="muted small">No code arriving?</span>
            <button type="button" className="btn btn-sm" disabled={busy} onClick={() => void startQr()}>Log in with a QR code instead</button>
          </div>
          <div className="muted small">
            API ID {data.api_id} · API Hash saved {relativeTime(data.api_hash.saved_at)} ·{" "}
            <button type="button" className="link-btn" onClick={() => void run(() => api.tgAccountForget(), "API keys removed.")}>
              change keys
            </button>
          </div>
        </form>
      )}

      {s === "qr_waiting" && (
        <div className="stack">
          <div className="field-label">Scan with your Telegram app</div>
          <p className="muted small">On your phone: Telegram › Settings › Devices › <b>Link Desktop Device</b>, then point the camera at this code. No login code needed.</p>
          {qr ? <img src={qr} alt="Telegram login QR code" width={280} height={280} style={{ background: "#fff", borderRadius: 12, padding: 8 }} />
            : <button className="btn btn-primary" disabled={busy} onClick={() => void startQr()}>Show the QR code</button>}
          <button type="button" className="btn btn-ghost" onClick={() => { setQr(null); void run(() => api.tgCancel(), "Login cancelled."); }}>Cancel</button>
        </div>
      )}

      {s === "code_sent" && (
        <form
          className="stack"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            const c = code.trim();
            setCode("");
            void run(() => api.tgCode(c));
          }}
        >
          <div className="field-label">Step 3 · Login code</div>
          <p className="muted small">Telegram sent a code to the Telegram app on your phone ({data.phone_hint}). Type it here. It is passed straight to Telegram and never stored.</p>
          <div className="row">
            <input className="input" inputMode="numeric" autoComplete="one-time-code" placeholder="12345" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} maxLength={8} />
            <button className="btn btn-primary" disabled={busy || code.length < 3}>
              {busy ? "Checking…" : "Log in"}
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => void run(() => api.tgCancel(), "Login cancelled.")}>
              Cancel
            </button>
          </div>
          <div className="row">
            <span className="muted small">No code arriving?</span>
            <button type="button" className="btn btn-sm" disabled={busy} onClick={() => void startQr()}>Log in with a QR code instead</button>
          </div>
        </form>
      )}

      {s === "password_needed" && (
        <form
          className="stack"
          onSubmit={(e: FormEvent) => {
            e.preventDefault();
            const p = password;
            setPassword("");
            void run(() => api.tgPassword(p));
          }}
        >
          <div className="field-label">Step 3b · Two-step verification password</div>
          <p className="muted small">This account has a cloud password (Telegram › Settings › Privacy and Security › Two-Step Verification). It is sent straight to Telegram and never stored.</p>
          <div className="row">
            <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            <button className="btn btn-primary" disabled={busy || !password}>
              {busy ? "Checking…" : "Continue"}
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => void run(() => api.tgCancel(), "Login cancelled.")}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {data.session === "active" && (
        <>
          <dl className="kv">
            <dt>Logged in as</dt>
            <dd>
              <strong>{data.display_name}</strong> {data.username ? `@${data.username}` : ""} <span className="muted small">{data.phone_hint}</span>
            </dd>
            <dt>Since</dt>
            <dd>{formatTime(data.authorized_at)}</dd>
            <dt>Last successful request</dt>
            <dd>{relativeTime(data.last_success_at)}</dd>
          </dl>
          <div className="btn-group">
            <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => void refreshDialogs()}>
              {busy ? "Loading…" : "Show my groups and channels"}
            </button>
            <button
              className="btn btn-sm tone-bad"
              disabled={busy}
              onClick={() => {
                if (window.confirm("Log out? The session is ended on Telegram (it disappears from Settings › Devices) and deleted from this computer. Authorised sources and data stay."))
                  void run(() => api.tgLogout(), "Logged out. The session was revoked and deleted.");
              }}
            >
              Log out / revoke session
            </button>
          </div>
          {dialogs && (
            <div className="stack" style={{ marginTop: 10 }}>
              <div className="field-label">Step 4 · Authorise the Price Chat community</div>
              <p className="muted small">Names only: no messages are read until you click Authorize. Nothing else is ever processed.</p>
              {dialogs.length === 0 ? (
                <Empty>This account is not in any group or channel.</Empty>
              ) : (
                <table className="table">
                  <tbody>
                    {dialogs.map((d) => (
                      <tr key={d.chat_id}>
                        <td>
                          {d.title}
                          <div className="muted small">
                            {d.chat_type}
                            {d.username ? ` · @${d.username}` : ""}
                            {d.members != null ? ` · ${d.members.toLocaleString()} members` : ""}
                          </div>
                        </td>
                        <td>
                          {d.authorized ? (
                            <Badge tone="good">Authorised</Badge>
                          ) : (
                            <button
                              className="btn btn-sm"
                              onClick={() =>
                                void run(async () => {
                                  await api.tgAuthorize(d.chat_id);
                                  setDialogs(await api.tgDialogs());
                                }, `“${d.title}” authorised. New messages from now on will be read; you can import recent history below.`)
                              }
                            >
                              Authorize for Price Chat
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </>
      )}
    </Card>
  );
}

const IMPORTS: { hours: number; label: string }[] = [
  { hours: 24, label: "Last 24 hours" },
  { hours: 168, label: "Last 7 days" },
  { hours: 720, label: "Last 30 days" },
];

/** History import for one account-mode source: estimate first, then a bounded import. */
export function HistoryImport({ source, onDone }: { source: TelegramSource; onDone: () => void }) {
  const [busy, setBusy] = useState<number | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  async function go(hours: number, label: string) {
    setBusy(hours);
    try {
      const est = await api.tgImportEstimate(source.id, hours);
      const n = est.estimated_messages;
      const capNote = n != null && n > est.cap ? ` Only the newest ${est.cap.toLocaleString()} will be imported.` : "";
      if (!window.confirm(`Import ${label.toLowerCase()} from “${source.display_name}”? About ${n ?? "?"} messages.${capNote}`)) return;
      const r = await api.tgImport(source.id, hours);
      setMsg(`Imported ${r.stored} messages (${r.duplicates} already stored)${r.capped ? `; stopped at ${r.cap}` : ""}.`);
      setError(null);
      onDone();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }
  return (
    <div className="stack">
      <div className="btn-group">
        <span className="muted small">Import history:</span>
        {IMPORTS.map((i) => (
          <button key={i.hours} className="btn btn-sm btn-ghost" disabled={busy !== null} onClick={() => void go(i.hours, i.label)}>
            {busy === i.hours ? "Importing…" : i.label}
          </button>
        ))}
      </div>
      {msg && <div className="muted small">{msg}</div>}
      <ApiErrorBanner error={error} />
    </div>
  );
}
