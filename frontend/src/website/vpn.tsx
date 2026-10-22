import { useCallback, useEffect, useState } from "react";
import { MemberApiError, memberApi } from "./members";
import { SitePage } from "./community";
import "./vpn.css";

/**
 * Kiwi VPN: a WireGuard VPN that is a Kiwi Rank perk. The server decides who may use it (/api/members/vpn); this page
 * only shows it. A new device's config (the only copy of its private key) comes back ONCE: it is shown as a QR code and a
 * .conf download, then forgotten. Lost it → delete the device and add a new one.
 */

export interface VpnServer { id: number; name: string; region: string; country_code: string; online: boolean; load: number;
  /** its resolver blocks ads & trackers */ adblock: boolean; /** the pick for a new device (online, least busy) */ recommended: boolean }
export interface VpnDevice { id: number; name: string; server_id: number; ip: string; created_at: string; last_handshake: string | null;
  rx_bytes: number; tx_bytes: number; paused_until: string | null }
/** This UTC month's data across the member's devices. cap_bytes 0 = unlimited; paused_until: over the cap, back on then. */
export interface VpnUsage { month: string; used_bytes: number; cap_bytes: number; paused_until: string | null }
export interface VpnState { enabled: boolean; eligible: boolean; reason: string | null; required_rank: string; max_devices: number;
  servers: VpnServer[]; devices: VpnDevice[]; usage: VpnUsage }
export interface NewDevice { device: VpnDevice; config: string; filename: string }

const FALLBACK: Record<number, string> = {
  401: "Sign in to your Kiwi Passport to use Kiwi VPN.",
  403: "Your Kiwi Rank doesn't unlock Kiwi VPN yet.",
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

export function VpnPage() {
  const [state, setState] = useState<VpnState | { error: string; status: number } | null>(null);
  const load = useCallback(async () => {
    try { setState(await memberApi<VpnState>("/members/vpn")); }
    catch (e) { setState({ error: vpnError(e), status: e instanceof MemberApiError ? e.status : 0 }); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  return (
    <SitePage title="Kiwi VPN" description="A private WireGuard VPN for Kiwi Passport members. No traffic logs." path="/vpn">
      <section className="vpn">
        <header className="vpn-head">
          <span className="vpn-shield" aria-hidden>🛡️</span>
          <div>
            <h1>Kiwi VPN</h1>
            <p className="site-muted">A private WireGuard VPN, unlocked by your Kiwi Rank. We never log what you do or where you go.</p>
          </div>
        </header>
        {!state ? <p className="site-empty">Loading…</p>
          : "error" in state ? (
            <div className="vpn-gate">
              <p>{state.error}</p>
              <a className="site-button" href={state.status === 401 ? "/join" : "/passport"}>{state.status === 401 ? "Sign in" : "Back to your Passport"}</a>
            </div>
          ) : !state.enabled ? (
            <div className="vpn-soon"><b>Coming soon</b><p>Kiwi VPN isn't open yet. It will appear here as soon as it is.</p></div>
          ) : !state.eligible ? <Locked s={state} />
          : <Unlocked s={state} reload={load} />}
      </section>
    </SitePage>
  );
}

function Locked({ s }: { s: VpnState }) {
  return (
    <div className="vpn-locked">
      <span className="vpn-lock" aria-hidden>🔒</span>
      <b>{s.reason || `Reach ${s.required_rank} to unlock Kiwi VPN`}</b>
      <p>Kiwi VPN unlocks at <strong>{s.required_rank}</strong>. Earn XP with missions, videos and the Telegram chat to rank up.</p>
      <a className="site-button" href="/passport">Rank up in your Passport →</a>
    </div>
  );
}

function Unlocked({ s, reload }: { s: VpnState; reload: () => Promise<void> }) {
  const [created, setCreated] = useState<NewDevice | null>(null);
  const full = s.devices.length >= s.max_devices;
  const serverName = (id: number) => s.servers.find((x) => x.id === id);
  const paused = s.usage?.paused_until ?? null;
  return (
    <div className="vpn-grid">
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
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!clean || !server || busy) return;
    setBusy(true); setErr(null);
    try {
      const n = await memberApi<NewDevice>("/members/vpn/devices", { method: "POST", body: JSON.stringify({ name: clean, server_id: server }) });
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
            {servers.find((x) => x.id === server)?.recommended && <small className="vpn-tag">Recommended</small>}
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
    import("qrcode")
      .then((Q) => Q.default.toString(created.config, { type: "svg", margin: 1, errorCorrectionLevel: "M", color: { dark: "#05120c", light: "#ffffff" } }))
      .then((svg) => alive && setQr(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`))
      .catch(() => alive && setQr(""));
    return () => { alive = false; };
  }, [created.config]);
  function download() {
    const blob = new Blob([created.config], { type: "application/octet-stream" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = created.filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
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
  const [ask, setAsk] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function remove() {
    setBusy(true); setErr(null);
    try { await memberApi<void>(`/members/vpn/devices/${d.id}`, { method: "DELETE" }); await reload(); }
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
        <small>{open ? `Unlocked: ${s.devices.length} / ${s.max_devices} devices` : s.reason || `Unlocks at ${s.required_rank}`}</small></span>
      <span className="pp-vip-go">{open ? "Open →" : "View requirements →"}</span>
    </a>
  );
}
