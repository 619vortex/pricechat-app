import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { MemberApiError, memberApi } from "./members";
import { SitePage } from "./community";
import { pageHead } from "./seo";
import { configQr, saveConfig } from "./vpnConfig";
import "./vpn.css";

/**
 * Kiwi VPN: a WireGuard VPN. Admin decides whether it costs anything. Free (no `billing` from the server): sign in with a Passport,
 * add a device, and no price or payment appears anywhere. Paid: $9.99 a month, any amount buys its share of time, with a Passport
 * (card or USDT) or an anonymous key (USDT only, no email or name; a lost key can't be recovered). The server decides who may use it;
 * this page only shows it. The key lives in this tab (sessionStorage) unless the visitor saves it elsewhere themselves. A new device's config (the only copy of its private key) comes back ONCE: it is shown as a QR code and a
 * .conf download, then forgotten. Lost it → delete the device and add a new one.
 */

export interface VpnServer { id: number; name: string; region: string; country_code: string; online: boolean; load: number;
  /** its resolver blocks ads & trackers */ adblock: boolean; /** the pick for a new device (online, least busy) */ recommended: boolean; /** the browser extension can use it */ browser?: boolean }
export interface VpnDevice { id: number; name: string; server_id: number; ip: string; created_at: string; last_handshake: string | null;
  rx_bytes: number; tx_bytes: number; paused_until: string | null }
/** This UTC month's data across the member's devices. cap_bytes 0 = unlimited; paused_until: over the cap, back on then. */
export interface VpnUsage { month: string; used_bytes: number; cap_bytes: number; paused_until: string | null }
export interface VpnBilling { price_usd: number; period_days: number; min_usd: number; max_usd: number; usdt: boolean; card: boolean; networks: { key: string; name: string; min_usd?: number }[] }
export interface VpnState { enabled: boolean; test_mode?: boolean; eligible: boolean; reason: string | null; max_devices: number; paid_until?: string | null; anonymous?: boolean; has_card?: boolean; billing?: VpnBilling | null;
  servers: VpnServer[]; devices: VpnDevice[]; usage: VpnUsage }
export interface NewDevice { device: VpnDevice; config: string; filename: string }

const FALLBACK: Record<number, string> = {
  401: "Sign in to your Kiwi Passport to use Kiwi VPN.",
  403: "Your account doesn't have Kiwi VPN yet.",
  404: "That device no longer exists.",
  409: "You've reached your device limit. Delete a device to add a new one.",
  503: "That server is offline right now. Pick another server or try again in a few minutes.",
};
/** The server's message when it sent one, else a plain explanation for the status. */
export function vpnError(e: unknown): string {
  if (e instanceof MemberApiError) return e.message.startsWith("Something went wrong") ? FALLBACK[e.status] ?? e.message : e.message;
  return "Couldn't reach Price Chat. Check your connection and try again.";
}

/** "NZ" → 🇳🇿 (regional indicator letters); anything else → a globe. */
export function flag(cc: string): string {
  const c = (cc || "").trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(c)) return "🌐";
  return String.fromCodePoint(...[...c].map((ch) => 0x1f1e6 + ch.charCodeAt(0) - 65));
}

export function bytes(n: number): string {
  if (!n || n < 0) return "0 MB";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  const v = n / 1024 ** i;
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${u[i]}`;
}

/** "1 Nov": the day a paused VPN switches back on (UTC: always the 1st of a month). */
export function resumeDay(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
}

/** The server a new device should use: the API's recommendation, else the least busy online one, else the first. */
export function pickServer(servers: VpnServer[]): number {
  return servers.find((x) => x.recommended && x.online)?.id
    ?? [...servers].filter((x) => x.online).sort((a, b) => a.load - b.load || a.id - b.id)[0]?.id ?? servers[0]?.id ?? 0;
}

export function ago(iso: string | null, now = Date.now()): string {
  if (!iso) return "Never connected";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return "Just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d} ${d === 1 ? "day" : "days"} ago`;
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export const INSTALL = [
  { os: "iPhone / iPad", href: "https://apps.apple.com/us/app/wireguard/id1441195209" },
  { os: "Android", href: "https://play.google.com/store/apps/details?id=com.wireguard.android" },
  { os: "Windows", href: "https://www.wireguard.com/install/" },
  { os: "Mac", href: "https://apps.apple.com/us/app/wireguard/id1451685025" },
];

/** Every account call goes through this: the Passport session cookie, or the anonymous key in a header. */
type Call = <T>(path: string, init?: RequestInit) => Promise<T>;
const KEY_STORE = "kiwi-vpn-key";
const callFor = (key: string | null): Call => (key
  ? (path, init = {}) => memberApi(`/members/vpn/anon${path}`, { ...init, headers: { ...(init.headers ?? {}), "X-Kiwi-Key": key } })
  : (path, init) => memberApi(`/members/vpn${path}`, init));
const CallCtx = createContext<Call>(callFor(null));
function readKey(): string | null { try { return sessionStorage.getItem(KEY_STORE); } catch { return null; } }
function saveKey(k: string | null): void { try { if (k) sessionStorage.setItem(KEY_STORE, k); else sessionStorage.removeItem(KEY_STORE); } catch { /* the key stays in memory only */ } }

type Loaded = VpnState | { error: string; status: number } | { visitor: true; enabled: boolean; test_mode?: boolean; billing: VpnBilling | null };

export function VpnPage() {
  const [key, setKeyState] = useState<string | null>(readKey);
  const [fresh, setFresh] = useState<string | null>(null);  // a key just made: shown once, before anything else
  const [state, setState] = useState<Loaded | null>(null);
  const setKey = useCallback((k: string | null) => { saveKey(k); setKeyState(k); }, []);
  const load = useCallback(async () => {
    try { setState(await callFor(key)<VpnState>("")); }
    catch (e) {
      const status = e instanceof MemberApiError ? e.status : 0;
      if (key && status === 401) { setKey(null); setState({ error: "That key isn't valid.", status: 0 }); return; }
      if (status === 401) {
        try { setState({ visitor: true, ...(await memberApi<{ enabled: boolean; test_mode?: boolean; billing: VpnBilling | null }>("/members/vpn/public")) }); return; } catch { /* the plain error below */ }
      }
      setState({ error: vpnError(e), status });
    }
  }, [key, setKey]);
  useEffect(() => { void load(); }, [load]);
  const open = !!state && "enabled" in state && state.enabled;
  const paid = open && !!state.billing;
  return (
    <SitePage {...pageHead("vpn")}>
      <section className="vpn">
        <header className="vpn-hero">
          <svg className="vpn-mark" viewBox="0 0 48 56" aria-hidden><path d="M24 2 44 9v18c0 13-8.4 22.4-20 27C12.4 49.4 4 40 4 27V9z" /><path className="vpn-mark-tick" d="m15 28 7 7 12-14" /></svg>
          <div>
            {open && <span className={`vpn-pill${paid ? "" : " is-free"}`}>{paid ? "$9.99 a month" : "Free while we test"}</span>}
            <h1>Kiwi VPN</h1>
            <p>Your connection, private. We never log what you do or where you go.</p>
            <ul className="vpn-facts"><li>WireGuard</li><li>No activity logs</li><li>Phone, tablet and computer</li></ul>
          </div>
        </header>
        {state && "test_mode" in state && state.test_mode && (
          <p className="vpn-paused" role="status"><b>Test mode</b> Kiwi VPN is closed to everyone else. You see this because you're signed in to Admin.{paid ? " Payments here are real." : ""}</p>
        )}
        {fresh ? <KeyOnce k={fresh} onDone={() => { setKey(fresh); setFresh(null); }} />
          : !state ? <p className="site-empty">Loading…</p>
          : "visitor" in state ? (
            !state.enabled ? <div className="vpn-soon"><b>Coming soon</b><p>Kiwi VPN isn't open yet. It will appear here as soon as it is.</p></div>
              : state.billing ? <Choose billing={state.billing} onKey={setKey} onFresh={setFresh} /> : <FreeStart onKey={setKey} />
          ) : "error" in state ? (
            <div className="vpn-gate">
              <p>{state.error}</p>
              <a className="site-button" href={state.status === 401 ? "/join" : "/passport"}>{state.status === 401 ? "Sign in" : "Back to your Passport"}</a>
            </div>
          ) : !state.enabled ? (
            <div className="vpn-soon"><b>Coming soon</b><p>Kiwi VPN isn't open yet. It will appear here as soon as it is.</p></div>
          ) : (
            <CallCtx.Provider value={callFor(key)}>
              {state.anonymous && <p className="vpn-keybar">🔑 Using your anonymous key. <button className="vpn-link" onClick={() => { setKey(null); setState(null); }}>Forget it on this browser</button></p>}
              {!state.eligible ? <Locked s={state} /> : <Unlocked s={state} reload={load} />}
              <AddTime s={state} reload={load} />
            </CallCtx.Provider>
          )}
      </section>
    </SitePage>
  );
}

/** Free: one way in, nothing about money. A key from before still works. */
function FreeStart({ onKey }: { onKey: (k: string) => void }) {
  const [typed, setTyped] = useState("");
  return (
    <section className="vpn-card vpn-start" aria-labelledby="vpn-start">
      <h2 id="vpn-start">Get connected in three steps</h2>
      <ol className="vpn-path">
        <li><b>Sign in</b><span>With your free Kiwi Passport.</span></li>
        <li><b>Add a device</b><span>Name it and pick a server.</span></li>
        <li><b>Scan and switch on</b><span>In the free WireGuard app.</span></li>
      </ol>
      <a className="site-button" href="/join">Sign in to start</a>
      <details className="vpn-havekey">
        <summary>I have a key</summary>
        <form className="vpn-form" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) onKey(typed.trim()); }}>
          <label className="site-field"><span>Your key</span>
            <input value={typed} autoComplete="off" spellCheck={false} placeholder="KIWI-XXXX-…" onChange={(e) => setTyped(e.target.value)} /></label>
          <button className="site-button is-ghost" type="submit" disabled={!typed.trim()}>Use my key</button>
        </form>
      </details>
    </section>
  );
}

/** A signed-out visitor picks: a Passport account, or an anonymous key. */
function Choose({ billing, onKey, onFresh }: { billing: VpnBilling; onKey: (k: string) => void; onFresh: (k: string) => void }) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function make() {
    setBusy(true); setErr(null);
    try { onFresh((await memberApi<{ key: string }>("/members/vpn/anon", { method: "POST" })).key); }
    catch (e) { setErr(vpnError(e)); } finally { setBusy(false); }
  }
  return (
    <div className="vpn-grid">
      <section className="vpn-card" aria-labelledby="vpn-passport">
        <h2 id="vpn-passport">With a Passport</h2>
        <p className="site-muted">Sign in with your email. Pay by {billing.card ? "card or USDT" : "USDT"}, and recover your account if you lose a device.</p>
        <a className="site-button" href="/join">Sign in</a>
      </section>
      <section className="vpn-card" aria-labelledby="vpn-anon">
        <h2 id="vpn-anon">Anonymous, with USDT</h2>
        <p className="site-muted">No email, no username, no password. You get a key, and that key is your only login.
          <b> Lose it and your time is gone: nobody can recover it.</b> Keeping your own crypto anonymous is up to you.</p>
        {!billing.usdt && <p className="site-muted">USDT payments open soon.</p>}
        <button className="site-button" disabled={busy || !billing.usdt} onClick={() => void make()}>{busy ? "Creating…" : "Get a key"}</button>
        {err && <p className="site-error" role="alert">{err}</p>}
        <form className="vpn-form" onSubmit={(e) => { e.preventDefault(); if (typed.trim()) onKey(typed.trim()); }}>
          <label className="site-field"><span>Already have a key?</span>
            <input value={typed} autoComplete="off" spellCheck={false} placeholder="KIWI-XXXX-…" onChange={(e) => setTyped(e.target.value)} /></label>
          <button className="site-button is-ghost" type="submit" disabled={!typed.trim()}>Use my key</button>
        </form>
      </section>
    </div>
  );
}

/** The new key, once. Continuing needs a tick: there is no recovery, so this is the one moment that matters. */
function KeyOnce({ k, onDone }: { k: string; onDone: () => void }) {
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);
  return (
    <section className="vpn-card vpn-keyonce" aria-labelledby="vpn-key-title">
      <h2 id="vpn-key-title">Save your key</h2>
      <p>This is your only login, and it's shown <b>once</b>. We keep only a scrambled form of it, so we can't show it again or recover it.</p>
      <code className="vpn-key">{k}</code>
      <button className="site-button is-ghost" onClick={() => { void navigator.clipboard?.writeText(k); setCopied(true); }}>{copied ? "Copied ✓" : "Copy key"}</button>
      <label className="vpn-check"><input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} /> I saved my key somewhere safe. If I lose it, my time is gone.</label>
      <button className="site-button" disabled={!saved} onClick={onDone}>Continue</button>
    </section>
  );
}

type Invoice = { network_name: string; address: string; amount: number; amount_usd: number; expires_at: string | null; days: number };

/** Time left, and buying more. Any amount buys its share of $9.99 for 30 days. */
function AddTime({ s, reload }: { s: VpnState; reload: () => Promise<void> }) {
  const call = useContext(CallCtx);
  const b = s.billing;
  const [amount, setAmount] = useState("9.99");
  const [network, setNetwork] = useState("solana");  // the lowest fees; each network's minimum comes from the server
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {  // the time appears by itself once the network confirms the payment
    if (!invoice) return;
    const id = window.setInterval(() => void reload(), 20000);
    return () => window.clearInterval(id);
  }, [invoice, reload]);
  if (!b) return null;
  const usd = Number(amount);
  const days = usd > 0 ? (usd / b.price_usd) * b.period_days : 0;
  const floor = Math.max(b.min_usd, b.networks.find((n) => n.key === network)?.min_usd ?? 0);
  const okAmount = usd >= floor && usd <= b.max_usd;
  const until = s.paid_until ? new Date(s.paid_until) : null;
  const active = until !== null && until.getTime() > Date.now();
  async function usdt(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try { setInvoice(await call<Invoice>("/usdt", { method: "POST", body: JSON.stringify({ amount_usd: usd, network }) })); }
    catch (e2) { setErr(vpnError(e2)); } finally { setBusy(false); }
  }
  async function go(path: string) {
    setBusy(true); setErr(null);
    try { window.location.assign((await call<{ url: string }>(path, { method: "POST" })).url); }
    catch (e2) { setErr(vpnError(e2)); setBusy(false); }
  }
  return (
    <section className="vpn-card vpn-time" aria-labelledby="vpn-time">
      <h2 id="vpn-time">Your time</h2>
      <p>{active ? <>Paid until <b>{until!.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}</b>.</>
        : until ? "Your time has run out. Your devices are off and kept for 30 days: add time and they switch back on within 5 minutes." : "No paid time yet."}</p>
      {!b.usdt && !b.card && <p className="site-muted">Payments open soon.</p>}
      {b.card && !s.anonymous && (
        s.has_card ? <button className="site-button is-ghost" disabled={busy} onClick={() => void go("/card/portal")}>Manage card subscription</button>
          : <button className="site-button" disabled={busy} onClick={() => void go("/card")}>Subscribe by card: ${b.price_usd.toFixed(2)} a month</button>
      )}
      {b.usdt && (invoice ? (
        <div className="vpn-invoice" role="status">
          <p>Send exactly <b>{invoice.amount} USDT</b> on <b>{invoice.network_name}</b> to:</p>
          <code className="vpn-key">{invoice.address}</code>
          <button className="site-button is-ghost" onClick={() => void navigator.clipboard?.writeText(invoice.address)}>Copy address</button>
          <p className="site-muted">Only {invoice.network_name}: other coins or networks can be lost. You'll get time for what actually arrives, about {invoice.days} days for this amount, a few minutes after the network confirms.</p>
          <button className="vpn-link" onClick={() => setInvoice(null)}>Start over</button>
        </div>
      ) : (
        <form className="vpn-form" onSubmit={usdt}>
          <label className="site-field"><span>Amount (USD)</span>
            <input type="number" inputMode="decimal" min={b.min_usd} max={b.max_usd} step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} />
            <small>{okAmount ? `Buys about ${days.toFixed(1)} days` : `$${floor} to $${b.max_usd} on this network`}</small></label>
          <label className="site-field"><span>Pay with</span>
            <select className="site-input" value={network} onChange={(e) => setNetwork(e.target.value)}>
              {b.networks.map((n) => <option key={n.key} value={n.key}>{n.name}{n.min_usd && n.min_usd > b.min_usd ? ` (min $${n.min_usd})` : ""}</option>)}
            </select></label>
          <button className="site-button" type="submit" disabled={busy || !okAmount}>{busy ? "Creating…" : "Pay with USDT"}</button>
        </form>
      ))}
      {err && <p className="site-error" role="alert">{err}</p>}
      <p className="site-muted vpn-fine">$9.99 buys 30 days and any amount buys its share. Network fees are yours. USDT payments can't be refunded.</p>
    </section>
  );
}

function Locked({ s }: { s: VpnState }) {
  return (
    <div className="vpn-locked">
      <b>{s.reason || "Kiwi VPN isn't available on this account."}</b>
    </div>
  );
}

const LIVE_MS = 10 * 60_000;  // the server reads handshakes every 5 minutes

function Unlocked({ s, reload }: { s: VpnState; reload: () => Promise<void> }) {
  const [created, setCreated] = useState<NewDevice | null>(null);
  const full = s.devices.length >= s.max_devices;
  const serverName = (id: number) => s.servers.find((x) => x.id === id);
  const paused = s.usage?.paused_until ?? null;
  const live = s.devices.filter((d) => d.last_handshake && Date.now() - new Date(d.last_handshake).getTime() < LIVE_MS).length;
  return (
    <div className="vpn-grid">
      <dl className="vpn-summary">
        <div className={live ? "is-live" : ""}><dt>Status</dt><dd>{live ? `${live} connected` : s.devices.length ? "Not connected" : "Not set up"}</dd></div>
        <div><dt>Devices</dt><dd>{s.devices.length} / {s.max_devices}</dd></div>
        <div><dt>Data this month</dt><dd>{bytes(s.usage?.used_bytes ?? 0)}</dd></div>
      </dl>
      {paused && (
        <p className="vpn-paused" role="status"><b>⏸ Paused for the rest of the month</b> You've used this month's {bytes(s.usage.cap_bytes)} of
          Kiwi VPN data. Your devices are kept and switch back on by themselves on {resumeDay(paused)}.</p>
      )}
      {created ? (
        <ConfigOnce created={created} server={serverName(created.device.server_id)} onDone={() => setCreated(null)} />
      ) : (
        <AddDevice servers={s.servers} full={full} max={s.max_devices} count={s.devices.length} paused={!!paused}
                   onCreated={(n) => { setCreated(n); void reload(); }} />
      )}
      <section className="vpn-card" aria-labelledby="vpn-devices">
        <h2 id="vpn-devices">Your devices <small>{s.devices.length} / {s.max_devices}</small></h2>
        {s.usage && <UsageMeter u={s.usage} />}
        {s.devices.length === 0 ? <p className="site-muted">No devices yet. Add your phone or laptop above.</p> : (
          <ul className="vpn-devices">
            {s.devices.map((d) => <DeviceRow key={d.id} d={d} server={serverName(d.server_id)} reload={reload} />)}
          </ul>
        )}
      </section>
      {s.servers.some((x) => x.browser) && <BrowserCard />}
      <section className="vpn-card" aria-labelledby="vpn-servers">
        <h2 id="vpn-servers">Servers</h2>
        <ul className="vpn-servers">
          {s.servers.map((x) => (
            <li key={x.id} className={x.online ? "" : "is-off"}>
              <span className="vpn-flag" aria-hidden>{flag(x.country_code)}</span>
              <span className="vpn-sname">
                <b>{x.name}{x.recommended && <span className="vpn-tag">Recommended</span>}</b>
                <small>{x.region}</small>
                {x.adblock && <small className="vpn-adblock">🛡️ Blocks ads & trackers</small>}
              </span>
              <span className={`vpn-dot${x.online ? " is-on" : ""}`} aria-label={x.online ? "Online" : "Offline"} title={x.online ? "Online" : "Offline"} />
              <LoadBar load={x.load} />
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

/** Testers install the extension by hand until it's in the browser stores. */
function BrowserCard() {
  return (
    <section className="vpn-card vpn-browser" aria-labelledby="vpn-browser">
      <h2 id="vpn-browser">Browser extension</h2>
      <p className="site-muted">Chrome or Edge, one switch, no app. It covers that browser only. For everything on your device, add it above and use WireGuard.</p>
      <a className="site-button" href="/downloads/kiwi-vpn-extension.zip" download>Download the extension</a>
      <ol className="vpn-steps">
        <li>Unzip it somewhere you'll keep it.</li>
        <li>Open <b>chrome://extensions</b> or <b>edge://extensions</b> and turn on <b>Developer mode</b>.</li>
        <li>Click <b>Load unpacked</b> and pick the <b>kiwi-vpn-extension</b> folder.</li>
        <li>Click the Kiwi VPN icon and press the switch.</li>
      </ol>
    </section>
  );
}

function LoadBar({ load }: { load: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, load || 0)) * 100);
  return (
    <span className={`vpn-load${pct >= 85 ? " is-busy" : pct >= 60 ? " is-mid" : ""}`} role="meter" aria-label="Server load"
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
      <i style={{ width: `${pct}%` }} />
    </span>
  );
}

/** This month's data: "1.5 GB of 50 GB this month" with a bar when there's a cap, else the total and "no limit". */
function UsageMeter({ u }: { u: VpnUsage }) {
  const pct = u.cap_bytes > 0 ? Math.round(Math.min(1, u.used_bytes / u.cap_bytes) * 100) : 0;
  return (
    <div className="vpn-usage">
      <span><b>{bytes(u.used_bytes)}</b> {u.cap_bytes > 0 ? <>of {bytes(u.cap_bytes)} this month</> : <>this month · no limit</>}</span>
      {u.cap_bytes > 0 && (
        <span className={`vpn-load${pct >= 90 ? " is-busy" : pct >= 70 ? " is-mid" : ""}`} role="meter" aria-label="Data used this month"
              aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
          <i style={{ width: `${pct}%` }} />
        </span>
      )}
    </div>
  );
}

function AddDevice({ servers, full, max, count, paused, onCreated }: {
  servers: VpnServer[]; full: boolean; max: number; count: number; paused: boolean; onCreated: (n: NewDevice) => void;
}) {
  const pick = () => pickServer(servers);
  const [name, setName] = useState("");
  const [server, setServer] = useState<number>(pick);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { if (!servers.some((x) => x.id === server)) setServer(pick()); }, [servers]);  // eslint-disable-line react-hooks/exhaustive-deps
  const clean = name.trim();
  const call = useContext(CallCtx);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!clean || !server || busy) return;
    setBusy(true); setErr(null);
    try {
      const n = await call<NewDevice>("/devices", { method: "POST", body: JSON.stringify({ name: clean, server_id: server }) });
      setName("");
      onCreated(n);
    } catch (e2) {
      setErr(vpnError(e2));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="vpn-card vpn-add" aria-labelledby="vpn-add">
      <h2 id="vpn-add">Add a device</h2>
      {paused ? <p className="site-muted">You can add devices again when your data resets on the 1st.</p>
        : full ? <p className="site-muted">You're using all {max} of your devices ({count} / {max}). Delete one below to add another.</p> : servers.length === 0 ? (
        <p className="site-muted">No servers are available yet. Check back soon.</p>
      ) : (
        <form className="vpn-form" onSubmit={submit}>
          <label className="site-field">
            <span>Device name</span>
            <input value={name} maxLength={32} placeholder="e.g. My iPhone" onChange={(e) => setName(e.target.value)} required />
          </label>
          <label className="site-field">
            <span>Server</span>
            <select className="site-input" value={server} onChange={(e) => setServer(Number(e.target.value))}>
              {servers.map((x) => (
                <option key={x.id} value={x.id} disabled={!x.online}>
                  {flag(x.country_code)} {x.name} · {x.region}{x.online ? "" : " (offline)"}{x.recommended ? " · Recommended" : ""}
                </option>
              ))}
            </select>
          </label>
          <button className="site-button" type="submit" disabled={busy || !clean || !server}>{busy ? "Creating…" : "Add device"}</button>
        </form>
      )}
      {err && <p className="site-error" role="alert">{err}</p>}
    </section>
  );
}

/** The new device's config, shown once: QR code, .conf download, copy, and how to set it up. */
function ConfigOnce({ created, server, onDone }: { created: NewDevice; server?: VpnServer; onDone: () => void }) {
  const [qr, setQr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    let alive = true;
    configQr(created.config).then((url) => alive && setQr(url)).catch(() => alive && setQr(""));
    return () => { alive = false; };
  }, [created.config]);
  const download = () => saveConfig(created.config, created.filename);
  async function copy() {
    try { await navigator.clipboard.writeText(created.config); setCopied(true); setTimeout(() => setCopied(false), 2000); }
    catch { setCopied(false); }
  }
  function done() {
    if (window.confirm("Did you save the config? It can't be shown again.")) onDone();
  }
  return (
    <section className="vpn-card vpn-once" aria-labelledby="vpn-once">
      <h2 id="vpn-once">{created.device.name} is ready{server ? ` · ${flag(server.country_code)} ${server.name}` : ""}</h2>
      <p className="vpn-warn" role="alert"><b>⚠️ This is shown once — save it now.</b> We don't keep a copy of your private key. If you lose it,
        delete this device and add a new one.</p>
      <div className="vpn-qr">
        {qr ? <img src={qr} alt={`QR code for ${created.device.name}`} width={260} height={260} />
          : qr === "" ? <p className="site-muted">Couldn't draw the QR code: download the file instead.</p>
          : <span className="vpn-qr-wait" aria-label="Drawing the QR code" />}
      </div>
      <div className="vpn-actions">
        <button className="site-button" type="button" onClick={download}>Download .conf</button>
        <button className="site-textbutton" type="button" onClick={() => void copy()}>{copied ? "Copied ✓" : "Copy config"}</button>
      </div>
      <ol className="vpn-steps">
        <li><b>Install WireGuard</b> (free, official):{" "}
          {INSTALL.map((i, n) => <span key={i.os}>{n > 0 && " · "}<a href={i.href} target="_blank" rel="noopener noreferrer">{i.os}</a></span>)}
          <small> All apps: <a href="https://www.wireguard.com/install/" target="_blank" rel="noopener noreferrer">wireguard.com/install</a></small></li>
        <li><b>Add the tunnel:</b> on a phone, tap + → “Scan from QR code”. On a computer, “Import tunnel(s) from file” and pick the .conf.</li>
        <li><b>Switch it on.</b> That's it: you're browsing through Kiwi VPN.</li>
      </ol>
      <button className="site-textbutton" type="button" onClick={done}>I saved it · Done</button>
    </section>
  );
}

function DeviceRow({ d, server, reload }: { d: VpnDevice; server?: VpnServer; reload: () => Promise<void> }) {
  const call = useContext(CallCtx);
  const [ask, setAsk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function remove() {
    setBusy(true); setErr(null);
    try { await call<void>(`/devices/${d.id}`, { method: "DELETE" }); await reload(); }
    catch (e) { setErr(vpnError(e)); setBusy(false); setAsk(false); }
  }
  return (
    <li className="vpn-device">
      <span className="vpn-flag" aria-hidden>{server ? flag(server.country_code) : "🌐"}</span>
      <span className="vpn-dmain">
        <b>{d.name}</b>
        <small>{server ? `${server.name} · ${server.region}` : "Server removed"} · {ago(d.last_handshake)} · {bytes(d.rx_bytes + d.tx_bytes)} this month</small>
        {d.paused_until && <small className="vpn-dpaused">Paused until {resumeDay(d.paused_until)}: monthly data used up</small>}
        {err && <small className="site-error">{err}</small>}
      </span>
      {ask ? (
        <span className="vpn-confirm">
          <button className="site-textbutton vpn-danger" type="button" disabled={busy} onClick={() => void remove()}>{busy ? "Deleting…" : "Delete for good"}</button>
          <button className="site-textbutton" type="button" disabled={busy} onClick={() => setAsk(false)}>Cancel</button>
        </span>
      ) : (
        <button className="site-textbutton vpn-danger" type="button" aria-label={`Delete ${d.name}`} onClick={() => setAsk(true)}>Delete</button>
      )}
    </li>
  );
}

/** One compact line on the Passport (like the VIP Trading Bot line): locked / unlocked, linking to /vpn. Hidden until it's open. */
export function VpnStatus() {
  const [s, setS] = useState<VpnState | null>(null);
  useEffect(() => { void memberApi<VpnState>("/members/vpn").then(setS).catch(() => undefined); }, []);
  if (!s || !s.enabled) return null;
  const open = s.eligible;
  return (
    <a className={`pp-vip${open ? " is-open" : ""}`} href="/vpn" aria-label="Kiwi VPN">
      <span className="pp-vip-icon" aria-hidden>{open ? "✓" : "🔒"}</span>
      <span className="pp-vip-text"><b>Kiwi VPN</b>
        <small>{open ? `${s.devices.length} / ${s.max_devices} devices${s.billing ? "" : " · free while we test"}` : s.reason || "Not available"}</small></span>
      <span className="pp-vip-go">{open ? "Open →" : "View →"}</span>
    </a>
  );
}
