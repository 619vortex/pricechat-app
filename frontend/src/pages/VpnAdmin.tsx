import { useEffect, useState } from "react";
import { request } from "../api/client";
import { useApi } from "../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Field, Loading, PageHeader, Stat, Tabs, relativeTime } from "../components/ui";
import { configQr, saveConfig } from "../website/vpnConfig";
import "./VpnAdmin.css";

/* Kiwi VPN (OWNER ONLY): who can use it, whether it costs anything, devices made here, the WireGuard servers, customers and payments.
   An anonymous customer is only a number here. Each server runs vpn-node's agent; the backend talks to it over HTTPS with a pinned certificate and a bearer token. */

export type VpnSettings = { enabled: boolean; payments_enabled: boolean; trial_enabled?: boolean; max_devices: number; /** per member per month, 0 = unlimited */ monthly_cap_gb: number };
export type VpnAdminServer = { id: number; name: string; region: string; country_code: string; endpoint_host: string | null; listen_port: number | null;
  agent_url: string; capacity: number; enabled: boolean; online: boolean; last_seen: string | null; last_error: string | null; devices: number; active_24h: number;
  adblock: boolean; /** its browser proxy's public name; empty = none */ proxy_host?: string;
  /** only the owner and testers see it (a new server being tried out) */ testers_only?: boolean };
export type VpnOverview = { settings: VpnSettings; servers: VpnAdminServer[]; totals: { devices: number; members: number; active_24h: number };
  billing?: { paying: number; anonymous: number; revenue_30d_usd: number; usdt: boolean; card: boolean } };

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The two decisions that matter, each one switch: who can use it, and whether it costs anything. Both apply as soon as they're flipped. */
function Controls({ s, canCharge, onSaved }: { s: VpnSettings; canCharge: boolean; onSaved: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function flip(change: Partial<VpnSettings>, question: string) {
    if (!window.confirm(question)) return;
    setBusy(true); setErr(null);
    try { await request("/vpn/admin/settings", { method: "PUT", body: JSON.stringify(change) }); await onSaved(); }
    catch (e) { setErr(errText(e)); } finally { setBusy(false); }
  }
  const free = !s.payments_enabled;
  return (
    <div className="stack">
      <div className="vpna-controls">
        <section className={`vpna-control${s.enabled ? " is-on" : ""}`}>
          <span className="vpna-control-label">Access</span>
          <span className="vpna-control-state">{s.enabled ? "Open to members" : "Closed"}</span>
          <label className="toggle" title={s.enabled ? "Open" : "Closed"}>
            <input type="checkbox" aria-label="VPN open" checked={s.enabled} disabled={busy} onChange={(e) => void flip({ enabled: e.target.checked }, e.target.checked
              ? `Open Kiwi VPN? ${free ? (s.trial_enabled ? "Anyone with a Passport gets one 30-day free trial when they sign in to the app, or start it at /vpn" : "Anyone with a Passport can use it for free") : "Anyone can buy it, with real money"}, and their traffic leaves through your servers' IP addresses.`
              : "Close Kiwi VPN? Nobody new can join and members can't add devices. Devices already connected keep working.")} /><span />
          </label>
          <p>{s.enabled ? (free && !s.trial_enabled ? "Anyone with a Passport can sign in at /vpn and connect." : s.trial_enabled ? "Members can start one free trial at /vpn." : "Anyone can pay and connect at /vpn.")
            : "The public sees “coming soon”. You can still make devices here, and test /vpn in this browser."}</p>
        </section>
        <section className={`vpna-control${s.trial_enabled ? " is-on" : ""}`}>
          <span className="vpna-control-label">Free trial</span>
          <span className="vpna-control-state">{s.trial_enabled ? "On · 30 days, once per account" : "Off"}</span>
          <label className="toggle" title={s.trial_enabled ? "On" : "Off"}>
            <input type="checkbox" aria-label="Free trial on" checked={!!s.trial_enabled} disabled={busy} onChange={(e) => void flip({ trial_enabled: e.target.checked }, e.target.checked
              ? "Turn the 30-day free trial on? While Kiwi VPN is open, any signed-in member who hasn't had one can start it, and devices connect through your server."
              : "Turn the free trial off? Nobody new can start one, and trials already running carry on. If payments are also off, Kiwi VPN becomes free for every member while it's open.")} /><span />
          </label>
          <p>{s.trial_enabled ? (s.enabled ? "Members can start a trial at /vpn." : "Waiting: it only appears while Kiwi VPN is open.") : "The trial isn't offered to anyone."}</p>
        </section>
        <section className={`vpna-control${s.payments_enabled ? " is-on" : ""}`}>
          <span className="vpna-control-label">Payments</span>
          <span className="vpna-control-state">{free ? (s.trial_enabled ? "Off · trial only" : "Off · free") : "On · $9.99 for 30 days"}</span>
          <label className="toggle" title={free ? "Off" : "On"}>
            <input type="checkbox" aria-label="Payments on" checked={s.payments_enabled} disabled={busy || (free && !canCharge)} onChange={(e) => void flip({ payments_enabled: e.target.checked }, e.target.checked
              ? "Turn payments on? Anyone without paid time loses access within 5 minutes (their devices are kept for 30 days). Devices you made here stay free. Payments are real money."
              : "Turn payments off? Kiwi VPN becomes free and everyone's devices switch back on within 5 minutes. A payment already started still gets credited.")} /><span />
          </label>
          <p>{free ? (canCharge ? "Nobody is asked to pay and no payment option is shown. Turn on when you're ready to charge." : "Nobody is asked to pay. Add the NOWPayments or Stripe keys on the server to allow turning this on.")
            : "Members need paid time to connect. Any amount buys its share of 30 days."}</p>
        </section>
      </div>
      {err && <p className="small vpna-note" role="alert" style={{ color: "var(--bad)" }}>{err}</p>}
    </div>
  );
}

function LimitsCard({ s, onSaved }: { s: VpnSettings; onSaved: () => void }) {
  const [v, setV] = useState({ max_devices: s.max_devices, monthly_cap_gb: s.monthly_cap_gb });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => setV({ max_devices: s.max_devices, monthly_cap_gb: s.monthly_cap_gb }), [s]);
  const dirty = v.max_devices !== s.max_devices || v.monthly_cap_gb !== s.monthly_cap_gb;
  async function save() {
    setBusy(true); setMsg(null);
    try {
      await request("/vpn/admin/settings", { method: "PUT", body: JSON.stringify(v) });
      setMsg({ ok: true, text: "Saved" });
      onSaved();
    } catch (e) {
      setMsg({ ok: false, text: errText(e) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card title="Limits for members">
      <div className="form">
        <div className="row-fields">
          <Field label="Devices per member" hint="1 to 10.">
            <input className="input" type="number" min={1} max={10} aria-label="Max devices" value={v.max_devices}
                   onChange={(e) => setV({ ...v, max_devices: Math.max(1, Math.min(10, Number(e.target.value) || 1)) })} />
          </Field>
          <Field label="Monthly data per member (GB)" hint="0 = unlimited. Over it, a member's devices pause until the 1st (UTC).">
            <input className="input" type="number" min={0} max={100000} aria-label="Monthly cap GB" value={v.monthly_cap_gb ?? 0}
                   onChange={(e) => setV({ ...v, monthly_cap_gb: Math.max(0, Math.min(100000, Math.floor(Number(e.target.value) || 0))) })} />
          </Field>
        </div>
        <div className="btn-group">
          <button className="btn btn-primary" disabled={busy || !dirty} onClick={() => void save()}>Save limits</button>
          {msg && <Badge tone={msg.ok ? "good" : "bad"}>{msg.text}</Badge>}
        </div>
      </div>
    </Card>
  );
}

function ServerRow({ x, reload }: { x: VpnAdminServer; reload: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [editing, setEditing] = useState(false);
  async function run(f: () => Promise<void>) {
    setBusy(true); setNote(null);
    try { await f(); } catch (e) { setNote({ ok: false, text: errText(e) }); } finally { setBusy(false); }
  }
  const toggle = (enabled: boolean) => run(async () => { await request(`/vpn/admin/servers/${x.id}`, { method: "PATCH", body: JSON.stringify({ enabled }) }); await reload(); });
  const audience = (testers_only: boolean) => {
    if (!testers_only && !window.confirm(`Show "${x.name}" to everyone? Every member can then pick it in the apps and the extension.`)) return;
    void run(async () => { await request(`/vpn/admin/servers/${x.id}`, { method: "PATCH", body: JSON.stringify({ testers_only }) }); await reload(); });
  };
  const test = () => run(async () => {
    const r = await request<{ ok: boolean; error: string | null; peers: number }>(`/vpn/admin/servers/${x.id}/test`, { method: "POST" });
    setNote(r.ok ? { ok: true, text: `OK · ${r.peers} peers` } : { ok: false, text: r.error || "Failed" });
    await reload();
  });
  const remove = () => {
    if (!window.confirm(`Delete server "${x.name}"? Its ${x.devices} device(s) stop working and members must add new ones.`)) return;
    void run(async () => { await request(`/vpn/admin/servers/${x.id}`, { method: "DELETE" }); await reload(); });
  };
  return (
    <>
    <tr>
      <td><b>{x.name}</b>{x.adblock && <> <Badge tone="good">ad blocking</Badge></>}{x.proxy_host && <> <Badge tone="good" title={x.proxy_host}>browser extension</Badge></>}{x.testers_only && <> <Badge tone="warn" title="Only you and testers see it">testers only</Badge></>}<br /><span className="muted small">{x.country_code.toUpperCase()} · {x.region}</span>
        <br /><span className="muted small mono">{x.endpoint_host ? `${x.endpoint_host}:${x.listen_port ?? ""}` : x.agent_url}</span></td>
      <td><Badge tone={x.online ? "good" : "bad"}>{x.online ? "online" : "offline"}</Badge><br /><span className="muted small">seen {relativeTime(x.last_seen)}</span>
        {x.last_error && <><br /><span className="small" style={{ color: "var(--bad)" }}>{x.last_error}</span></>}</td>
      <td>{x.devices} / {x.capacity}</td>
      <td>{x.active_24h}</td>
      <td>
        <label className="toggle" title={x.enabled ? "Enabled" : "Disabled"}>
          <input type="checkbox" aria-label={`Enable ${x.name}`} checked={x.enabled} disabled={busy} onChange={(e) => void toggle(e.target.checked)} /><span />
        </label>
      </td>
      <td>
        <div className="btn-group">
          <button className="btn btn-sm" disabled={busy} onClick={() => void test()}>Test</button>
          <button className="btn btn-sm" disabled={busy} onClick={() => audience(!x.testers_only)}>{x.testers_only ? "Show to everyone" : "Testers only"}</button>
          <button className="btn btn-sm" disabled={busy} aria-expanded={editing} onClick={() => setEditing(!editing)}>Edit</button>
          <button className="btn btn-sm btn-kill-sm" disabled={busy} onClick={remove}>Delete</button>
        </div>
        {note && <Badge tone={note.ok ? "good" : "bad"}>{note.text}</Badge>}
      </td>
    </tr>
    {editing && <tr><td colSpan={6}><EditServer x={x} onDone={async () => { setEditing(false); await reload(); }} /></td></tr>}
    </>
  );
}

/** Name, region and capacity. The token and certificate only change when a new value is typed; the current ones are never shown. */
function EditServer({ x, onDone }: { x: VpnAdminServer; onDone: () => Promise<void> }) {
  const [f, setF] = useState({ name: x.name, region: x.region, capacity: String(x.capacity), agent_token: "", cert_sha256: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    const body: Record<string, string | number> = { name: f.name.trim(), region: f.region.trim(), capacity: Number(f.capacity) };
    if (f.agent_token.trim()) body.agent_token = f.agent_token.trim();
    if (f.cert_sha256.trim()) body.cert_sha256 = f.cert_sha256.trim().replace(/:/g, "").toLowerCase();
    try { await request(`/vpn/admin/servers/${x.id}`, { method: "PATCH", body: JSON.stringify(body) }); await onDone(); }
    catch (e2) { setErr(errText(e2)); } finally { setBusy(false); }
  }
  return (
    <form className="form" onSubmit={save}>
      <div className="form-grid">
        <Field label="Name"><input className="input" aria-label="Edit name" value={f.name} onChange={set("name")} /></Field>
        <Field label="Region"><input className="input" aria-label="Edit region" value={f.region} onChange={set("region")} /></Field>
        <Field label="Capacity"><input className="input" aria-label="Edit capacity" type="number" min={1} value={f.capacity} onChange={set("capacity")} /></Field>
        <Field label="New agent token" hint="Empty keeps the current one"><input className="input mono" aria-label="New agent token" type="password" autoComplete="off" value={f.agent_token} onChange={set("agent_token")} /></Field>
        <Field label="New cert SHA-256" hint="Empty keeps the current one"><input className="input mono" aria-label="New cert" value={f.cert_sha256} onChange={set("cert_sha256")} /></Field>
      </div>
      <div className="btn-group"><button className="btn btn-primary" type="submit" disabled={busy || !f.name.trim() || !(Number(f.capacity) > 0)}>{busy ? "Saving…" : "Save server"}</button></div>
      {err && <p className="small" role="alert" style={{ color: "var(--bad)" }}>{err}</p>}
    </form>
  );
}

type Made = { device: { id: number; name: string }; config: string; filename: string };

/** Make a device right here: no account, no key, free whatever the payment setting. The config exists only until this panel is closed. */
function MakeDevice({ servers, onMade }: { servers: VpnAdminServer[]; onMade: () => Promise<void> }) {
  const usable = servers.filter((x) => x.enabled && x.online);
  const [name, setName] = useState("");
  const [server, setServer] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [made, setMade] = useState<Made | null>(null);
  const chosen = usable.some((x) => x.id === server) ? server : usable[0]?.id ?? 0;
  async function go(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      setMade(await request<Made>("/vpn/admin/devices", { method: "POST", body: JSON.stringify({ name: name.trim(), server_id: chosen }) }));
      setName("");
      await onMade();
    } catch (e2) { setErr(errText(e2)); } finally { setBusy(false); }
  }
  if (made) return <ConfigOnce made={made} onDone={() => setMade(null)} />;
  return (
    <Card title="Make a device">
      {!usable.length ? <Empty>{servers.length ? "No server is online right now." : "Connect a server first, in the Servers tab."}</Empty> : (
        <form className="form" onSubmit={go}>
          <p className="muted small vpna-note">Connect your phone or laptop, or set one up for someone. Free, and it works even while Kiwi VPN is closed.</p>
          <div className="form-grid">
            <Field label="Device name"><input className="input" aria-label="Device name" maxLength={32} value={name} onChange={(e) => setName(e.target.value)} placeholder="My iPhone" /></Field>
            <Field label="Server">
              <select className="input" aria-label="Server" value={chosen} onChange={(e) => setServer(Number(e.target.value))}>
                {usable.map((x) => <option key={x.id} value={x.id}>{x.name} · {x.region}</option>)}
              </select>
            </Field>
          </div>
          <div className="btn-group"><button className="btn btn-primary" type="submit" disabled={busy || !name.trim()}>{busy ? "Creating…" : "Create device"}</button></div>
          {err && <p className="small vpna-note" role="alert" style={{ color: "var(--bad)" }}>{err}</p>}
        </form>
      )}
    </Card>
  );
}

function ConfigOnce({ made, onDone }: { made: Made; onDone: () => void }) {
  const [qr, setQr] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    configQr(made.config).then((u) => { if (alive) setQr(u); }).catch(() => { if (alive) setQr(""); });
    return () => { alive = false; };
  }, [made.config]);
  return (
    <Card title={`${made.device.name} is ready`}>
      <div className="vpna-config">
        {qr ? <img className="vpna-config-qr" src={qr} alt={`QR code for ${made.device.name}`} />
          : qr === "" ? <p className="muted small">Couldn't draw the QR code: download the file instead.</p>
          : <span className="vpna-config-qr vpna-config-wait" aria-label="Drawing the QR code" />}
        <div className="vpna-config-side">
          <p className="vpna-warn" role="alert"><b>Shown once.</b> The private key isn't kept anywhere. If you lose it, revoke this device under People and make a new one.</p>
          <ol className="vpna-steps">
            <li><span>Install the <a className="link" href="https://www.wireguard.com/install/" target="_blank" rel="noopener noreferrer">WireGuard app</a>.</span></li>
            <li><span>On a phone: + → <b>Scan from QR code</b>. On a computer: <b>Import tunnel(s) from file</b>, with the file below.</span></li>
            <li><span>Switch it on.</span></li>
          </ol>
          <div className="btn-group">
            <button className="btn" onClick={() => saveConfig(made.config, made.filename)}>Download .conf</button>
            <button className="btn btn-primary" onClick={() => { if (window.confirm("Did you save it? It can't be shown again.")) onDone(); }}>Done</button>
          </div>
        </div>
      </div>
    </Card>
  );
}

const EMPTY = { name: "", region: "", country_code: "", agent_url: "", agent_token: "", cert_sha256: "", capacity: "" };


function AddServer({ onAdded }: { onAdded: () => Promise<void> }) {
  const [f, setF] = useState(EMPTY);
  const [testersOnly, setTestersOnly] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const set = (k: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const ready = f.name.trim() && f.region.trim() && /^[a-z]{2}$/i.test(f.country_code.trim()) && f.agent_url.trim() && f.agent_token.trim() && f.cert_sha256.trim();
  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true); setErr(null); setOk(null);
    const body: Record<string, string | number | boolean> = {
      name: f.name.trim(), region: f.region.trim(), country_code: f.country_code.trim().toUpperCase(), agent_url: f.agent_url.trim(),
      agent_token: f.agent_token.trim(), cert_sha256: f.cert_sha256.trim().replace(/:/g, "").toLowerCase(), testers_only: testersOnly,
    };
    if (f.capacity.trim()) body.capacity = Number(f.capacity);
    try {
      await request("/vpn/admin/servers", { method: "POST", body: JSON.stringify(body) });
      setOk(`Added ${body.name}.`);
      setF(EMPTY);
      await onAdded();
    } catch (e2) {
      setErr(errText(e2));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card title="Add server">
      <p className="muted small">Run <span className="mono">vpn-node/install.sh</span> on a fresh Ubuntu VPS (<span className="mono">install-shared.sh</span> on a server with another tunnel) and paste what it prints.</p>
      <form className="form" onSubmit={add}>
        <div className="form-grid">
          <Field label="Name"><input className="input" aria-label="Name" value={f.name} onChange={set("name")} placeholder="Auckland 1" /></Field>
          <Field label="Region"><input className="input" aria-label="Region" value={f.region} onChange={set("region")} placeholder="New Zealand" /></Field>
          <Field label="Country code" hint="2 letters, e.g. NZ"><input className="input" aria-label="Country code" value={f.country_code} maxLength={2} onChange={set("country_code")} placeholder="NZ" /></Field>
          <Field label="Capacity" hint="Max devices (optional)"><input className="input" aria-label="Capacity" type="number" min={1} value={f.capacity} onChange={set("capacity")} placeholder="250" /></Field>
          <Field label="Agent URL"><input className="input mono" aria-label="Agent URL" value={f.agent_url} onChange={set("agent_url")} placeholder="https://203.0.113.10:8443" /></Field>
          <Field label="Agent token"><input className="input mono" aria-label="Agent token" type="password" autoComplete="off" value={f.agent_token} onChange={set("agent_token")} /></Field>
          <Field label="Cert SHA-256"><input className="input mono" aria-label="Cert SHA-256" value={f.cert_sha256} onChange={set("cert_sha256")} placeholder="64 hex characters" /></Field>
        </div>
        <label className="small"><input type="checkbox" aria-label="Testers only" checked={testersOnly} onChange={(e) => setTestersOnly(e.target.checked)} /> Testers only: only you and testers see it until you choose Show to everyone</label>
        <div className="btn-group">
          <button className="btn btn-primary" type="submit" disabled={busy || !ready}>{busy ? "Checking the agent…" : "Add server"}</button>
          {ok && <Badge tone="good">{ok}</Badge>}
        </div>
        {err && <p className="small" role="alert" style={{ color: "var(--bad)" }}>{err}</p>}
      </form>
    </Card>
  );
}

type AdminBrowser = { id: number; server: string; last_active: string | null; rx_bytes: number; tx_bytes: number };
type AdminDevice = { id: number; name: string; server: string; ip: string; last_handshake: string | null; rx_bytes: number; tx_bytes: number };
export type VpnCustomer = { tester_until?: string | null; id: number; owner?: boolean; anonymous: boolean; label: string; state: "active" | "lapsed" | "blocked"; paid_until: string | null; lapsed_at: string | null;
  has_card: boolean; payments: number; paid_usd: number; devices: AdminDevice[]; browsers?: AdminBrowser[] };
export type VpnPaymentRow = { id: number; provider: string; network: string | null; amount_usd: number; paid_usd: number; status: string; days: number;
  created_at: string | null; credited_at: string | null; account: string | null };
const CONNECTED_MS = 10 * 60_000;  // the sync reads handshakes every 5 minutes
const STATE_TONE = { active: "good", lapsed: "warn", blocked: "bad" } as const;
const STATE_LABEL = { active: "active", lapsed: "out of time", blocked: "blocked" } as const;
const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "—");

function Customers({ payments, onChanged }: { payments: boolean; onChanged: () => Promise<void> }) {
  const r = useApi(() => request<{ items: VpnCustomer[] }>("/vpn/admin/customers"), 60_000);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  async function act(f: () => Promise<unknown>, ok: string) {
    setNote(null);
    try { await f(); setNote({ ok: true, text: ok }); await r.reload(); await onChanged(); } catch (e) { setNote({ ok: false, text: errText(e) }); }
  }
  const block = (c: VpnCustomer, blocked: boolean) => {
    if (blocked && !window.confirm(`Block ${c.label}? All ${c.devices.length} device(s) lose access now and are deleted. Paid time isn't refunded.`)) return;
    void act(() => request(`/vpn/admin/accounts/${c.id}/block`, { method: "POST", body: JSON.stringify({ blocked }) }), blocked ? `Blocked ${c.label}.` : `Unblocked ${c.label}.`);
  };
  const revoke = (c: VpnCustomer, d: AdminDevice) => {
    if (!window.confirm(`Revoke "${d.name}" of ${c.label}? Its config stops working now.`)) return;
    void act(() => request(`/vpn/admin/devices/${d.id}`, { method: "DELETE" }), `Revoked ${d.name}.`);
  };
  const revokeBrowser = (c: VpnCustomer, b: AdminBrowser) => {
    if (!window.confirm(`Revoke this browser of ${c.label}? It's cut off within seconds, and can sign in again unless you block them.`)) return;
    void act(() => request(`/vpn/admin/browsers/${b.id}`, { method: "DELETE" }), "Revoked the browser.");
  };
  return (
    <Card title="People">
      {r.failure ? <ApiErrorBanner error={r.failure} /> : null}
      {note && <p><Badge tone={note.ok ? "good" : "bad"}>{note.text}</Badge></p>}
      <AddTester onAdd={(username, days) => act(() => request("/vpn/admin/testers", { method: "POST", body: JSON.stringify({ username, days }) }), days ? `${username} can test for ${days} days. It stays closed to everyone else.` : `${username} is no longer a tester.`)} />
      {r.loading && !r.data ? <Loading /> : !r.data?.items?.length ? <Empty>Nobody yet. Devices you make and people who join appear here.</Empty> : (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Who</th><th>Status</th>{payments && <><th>Paid until</th><th>Paid</th></>}<th>Devices</th><th /></tr></thead>
          <tbody>
            {r.data.items.map((c) => (
              <tr key={c.id}>
                <td><b>{c.label}</b>{c.has_card && <> <Badge>card</Badge></>}</td>
                <td><Badge tone={STATE_TONE[c.state]}>{STATE_LABEL[c.state]}</Badge></td>
                {payments && <>
                  <td>{c.owner ? "free" : day(c.paid_until)}</td>
                  <td>${c.paid_usd.toFixed(2)} <span className="muted small">({c.payments})</span></td>
                </>}
                <td>{c.devices.length === 0 && !c.browsers?.length ? <span className="muted small">none</span> : <div className="vpna-devices">{c.devices.map((d) => {
                  const on = d.last_handshake !== null && Date.now() - new Date(d.last_handshake).getTime() < CONNECTED_MS;
                  return (
                    <div key={d.id} className="vpna-device">
                      <b>{d.name}</b> · {d.server} <span className="mono muted">{d.ip}</span> <Badge tone={on ? "good" : undefined}>{on ? "connected" : d.last_handshake ? `seen ${relativeTime(d.last_handshake)}` : "not connected yet"}</Badge>{" "}
                      <button className="btn btn-sm" onClick={() => revoke(c, d)}>Revoke</button>
                    </div>
                  );
                })}{c.browsers?.map((b) => (
                  <div key={`b${b.id}`} className="vpna-device">
                    <b>Browser</b> · {b.server} <Badge>{b.last_active ? `active ${relativeTime(b.last_active)}` : "not used yet"}</Badge>{" "}
                    <button className="btn btn-sm" onClick={() => revokeBrowser(c, b)}>Revoke browser</button>
                  </div>
                ))}</div>}</td>
                <td>{c.owner ? null : c.state === "blocked" ? <button className="btn btn-sm" onClick={() => block(c, false)}>Unblock</button>
                  : <button className="btn btn-sm btn-kill-sm" onClick={() => block(c, true)}>Block</button>}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      {payments && <BlockKey onDone={async () => { await r.reload(); await onChanged(); }} />}
    </Card>
  );
}

/** Someone reported a key: paste it to block whoever holds it. It's only hashed for the lookup, never stored or shown. */
/** One named member may use the VPN while it's closed, until a date. */
function AddTester({ onAdd }: { onAdd: (username: string, days: number) => Promise<void> }) {
  const [name, setName] = useState("");
  const [days, setDays] = useState("14");
  const clean = name.trim();
  return (
    <form className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center", marginBottom: 12 }} onSubmit={(e) => { e.preventDefault(); if (clean) void onAdd(clean, Number(days) || 0).then(() => setName("")); }}>
      <input className="input" aria-label="Tester's username" placeholder="Tester's username" value={name} onChange={(e) => setName(e.target.value)} />
      <input className="input" aria-label="Days" type="number" min={0} max={90} style={{ width: 80 }} value={days} onChange={(e) => setDays(e.target.value)} />
      <button className="btn btn-sm" type="submit" disabled={!clean}>{Number(days) > 0 ? "Add tester" : "End tester"}</button>
      <span className="muted small">Can use Kiwi VPN while it's closed. 0 days ends it.</span>
    </form>
  );
}

function BlockKey({ onDone }: { onDone: () => Promise<void> }) {
  const [key, setKey] = useState("");
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  async function go(e: React.FormEvent) {
    e.preventDefault();
    if (!window.confirm("Block whoever holds this key? Their devices lose access now and paid time isn't refunded.")) return;
    setNote(null);
    try {
      const out = await request<{ id: number; devices_removed: number }>("/vpn/admin/block-key", { method: "POST", body: JSON.stringify({ key: key.trim() }) });
      setNote({ ok: true, text: `Blocked Anonymous #${out.id}: ${out.devices_removed} device(s) removed.` });
      setKey("");
      await onDone();
    } catch (e2) { setNote({ ok: false, text: errText(e2) }); }
  }
  return (
    <form className="form" onSubmit={go}>
      <Field label="Block an anonymous key" hint="Paste the key. It isn't stored.">
        <input className="input mono" type="password" autoComplete="off" aria-label="Key to block" value={key} onChange={(e) => setKey(e.target.value)} placeholder="KIWI-XXXX-…" />
      </Field>
      <div className="btn-group">
        <button className="btn btn-kill-sm" type="submit" disabled={key.trim().length < 8}>Block key</button>
        {note && <Badge tone={note.ok ? "good" : "bad"}>{note.text}</Badge>}
      </div>
    </form>
  );
}

function Payments({ on }: { on: boolean }) {
  const r = useApi(() => request<{ items: VpnPaymentRow[] }>("/vpn/admin/payments"), 60_000);
  return (
    <Card title="Payments">
      {r.failure ? <ApiErrorBanner error={r.failure} /> : null}
      {r.loading && !r.data ? <Loading /> : !r.data?.items?.length ? <Empty>{on ? "No payments yet." : "Payments are off, so nobody is charged. Turn them on above when you're ready."}</Empty> : (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Started</th><th>Customer</th><th>Via</th><th>Asked</th><th>Received</th><th>Time</th><th>Status</th></tr></thead>
          <tbody>
            {r.data.items.map((p) => (
              <tr key={p.id}>
                <td>{day(p.created_at)}</td>
                <td>{p.account ?? <span className="muted small">unlinked after 7 days</span>}</td>
                <td>{p.provider === "stripe" ? "card" : p.network ? `USDT ${p.network}` : p.provider}</td>
                <td>${p.amount_usd.toFixed(2)}</td>
                <td>${p.paid_usd.toFixed(2)}</td>
                <td>{p.days ? `${p.days} days` : "—"}</td>
                <td><Badge tone={p.status === "credited" ? "good" : p.status === "pending" ? "warn" : "bad"}>{p.status}</Badge></td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
    </Card>
  );
}

type Tab = "overview" | "servers" | "customers" | "payments";

export function VpnAdminPage() {
  const r = useApi(() => request<VpnOverview>("/vpn/admin/overview"), 60_000);
  const d = r.data;
  const [tab, setTab] = useState<Tab>("overview");
  return (
    <div className="stack">
      <PageHeader title="Kiwi VPN" subtitle="Your WireGuard VPN: who can use it, whether it costs anything, servers and devices. No traffic is logged."
        actions={<button className="btn" onClick={() => void r.reload()} disabled={r.loading}>Refresh</button>} />
      {r.failure ? <ApiErrorBanner error={r.failure} /> : null}
      {r.loading && !d ? <Loading /> : null}
      {d && <>
        <Controls s={d.settings} canCharge={!!(d.billing?.usdt || d.billing?.card)} onSaved={r.reload} />
        <div className="stats">
          <Stat label="Devices" value={d.totals.devices} />
          <Stat label="People with devices" value={d.totals.members} />
          <Stat label="Active in 24h" value={d.totals.active_24h} tone={d.totals.active_24h > 0 ? "good" : "neutral"} />
          <Stat label="Servers online" value={`${d.servers.filter((x) => x.online).length} / ${d.servers.length}`}
                tone={d.servers.some((x) => x.enabled && !x.online) ? "warn" : "good"} />
        </div>
        <Tabs<Tab> value={tab} onChange={setTab} tabs={[{ key: "overview", label: "Overview" }, { key: "servers", label: `Servers (${d.servers.length})` },
          { key: "customers", label: "People" }, { key: "payments", label: "Payments" }]} />
        {tab === "overview" && <>
          {!d.servers.some((x) => x.online) && (
            <Card title="Get connected">
              <ol className="vpna-steps">
                <li className={d.servers.length ? "is-done" : ""}><span><b>Connect a server</b> in the <button className="btn btn-sm" onClick={() => setTab("servers")}>Servers</button> tab. It has to show online.</span></li>
                <li><span><b>Make a device</b> below and scan its QR code with the WireGuard app.</span></li>
                <li><span><b>Open it to members</b> with the Access switch when you want others on it.</span></li>
              </ol>
            </Card>
          )}
          <MakeDevice servers={d.servers} onMade={r.reload} />
          <LimitsCard s={d.settings} onSaved={() => void r.reload()} />
        </>}
        {tab === "servers" && <>
        <Card title={`Servers (${d.servers.length})`}>
          {!d.servers.length ? <Empty>No servers yet. Add one below.</Empty> : (
            <div className="table-wrap"><table className="table">
              <thead><tr><th>Server</th><th>Status</th><th>Devices / capacity</th><th>Active 24h</th><th>Enabled</th><th></th></tr></thead>
              <tbody>{d.servers.map((x) => <ServerRow key={x.id} x={x} reload={r.reload} />)}</tbody>
            </table></div>
          )}
          <p className="muted small">Synced every 5 minutes by the <span className="mono">vpn.sync</span> job (online, last seen, handshakes, monthly data).</p>
        </Card>
        <AddServer onAdded={r.reload} />
        </>}
        {tab === "customers" && <Customers payments={d.settings.payments_enabled} onChanged={r.reload} />}
        {tab === "payments" && <>
          {d.billing && (
            <div className="stats">
              <Stat label="With paid time" value={d.billing.paying} />
              <Stat label="Anonymous keys" value={d.billing.anonymous} />
              <Stat label="Last 30 days" value={`$${d.billing.revenue_30d_usd.toFixed(2)}`} />
              <Stat label="Ways to pay" value={[d.billing.usdt && "USDT", d.billing.card && "Card"].filter(Boolean).join(" · ") || "None"} hint={!d.billing.usdt && !d.billing.card ? "No NOWPayments or Stripe keys" : undefined} />
            </div>
          )}
          <Payments on={d.settings.payments_enabled} />
        </>}
      </>}
    </div>
  );
}
