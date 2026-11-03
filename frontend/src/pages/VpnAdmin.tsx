import { useEffect, useState } from "react";
import { request } from "../api/client";
import { useApi } from "../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Field, Loading, PageHeader, Stat, relativeTime } from "../components/ui";

/* Kiwi VPN (OWNER ONLY): public sales, the WireGuard servers, customers, payments and blocking. An anonymous customer is only a number here.
   Each server runs vpn-node's agent; the backend talks to it over HTTPS with a pinned certificate and a bearer token. */

export type VpnSettings = { enabled: boolean; max_devices: number; /** per member per month, 0 = unlimited */ monthly_cap_gb: number };
export type VpnAdminServer = { id: number; name: string; region: string; country_code: string; endpoint_host: string | null; listen_port: number | null;
  agent_url: string; capacity: number; enabled: boolean; online: boolean; last_seen: string | null; last_error: string | null; devices: number; active_24h: number;
  adblock: boolean };
export type VpnOverview = { settings: VpnSettings; servers: VpnAdminServer[]; totals: { devices: number; members: number; active_24h: number };
  billing?: { paying: number; anonymous: number; revenue_30d_usd: number; usdt: boolean; card: boolean } };

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

function SettingsCard({ s, onSaved }: { s: VpnSettings; onSaved: () => void }) {
  const [v, setV] = useState(s);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => setV(s), [s]);
  const dirty = v.enabled !== s.enabled || v.max_devices !== s.max_devices || v.monthly_cap_gb !== s.monthly_cap_gb;
  async function save() {
    if (v.enabled && !s.enabled && !window.confirm("Open Kiwi VPN to the public? Anyone can then buy it, with real money.")) return;
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
    <Card title="Settings">
      <div className="form">
        <div className="row">
          <span><b>Public sales</b> <Badge tone={s.enabled ? "good" : "warn"}>{s.enabled ? "on" : "off · test mode"}</Badge><br /><span className="muted small">Off: the public sees "coming soon" and nobody can buy, get a key or add a device. You still can, in this browser, while signed in to Admin: open <a href="/vpn">the VPN page</a> to test (payments there are real). On: anyone can buy.</span></span>
          <label className="toggle" title={v.enabled ? "On" : "Off"}>
            <input type="checkbox" aria-label="VPN enabled" checked={v.enabled} onChange={(e) => setV({ ...v, enabled: e.target.checked })} /><span />
          </label>
        </div>
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
          <button className="btn btn-primary" disabled={busy || !dirty} onClick={() => void save()}>Save settings</button>
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
      <td><b>{x.name}</b>{x.adblock && <> <Badge tone="good">ad blocking</Badge></>}<br /><span className="muted small">{x.country_code.toUpperCase()} · {x.region}</span>
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

const EMPTY = { name: "", region: "", country_code: "", agent_url: "", agent_token: "", cert_sha256: "", capacity: "" };

function AddServer({ onAdded }: { onAdded: () => Promise<void> }) {
  const [f, setF] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const set = (k: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const ready = f.name.trim() && f.region.trim() && /^[a-z]{2}$/i.test(f.country_code.trim()) && f.agent_url.trim() && f.agent_token.trim() && f.cert_sha256.trim();
  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true); setErr(null); setOk(null);
    const body: Record<string, string | number> = {
      name: f.name.trim(), region: f.region.trim(), country_code: f.country_code.trim().toUpperCase(), agent_url: f.agent_url.trim(),
      agent_token: f.agent_token.trim(), cert_sha256: f.cert_sha256.trim().replace(/:/g, "").toLowerCase(),
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
        <div className="btn-group">
          <button className="btn btn-primary" type="submit" disabled={busy || !ready}>{busy ? "Checking the agent…" : "Add server"}</button>
          {ok && <Badge tone="good">{ok}</Badge>}
        </div>
        {err && <p className="small" role="alert" style={{ color: "var(--bad)" }}>{err}</p>}
      </form>
    </Card>
  );
}

type AdminDevice = { id: number; name: string; server: string; ip: string; last_handshake: string | null; rx_bytes: number; tx_bytes: number };
export type VpnCustomer = { id: number; anonymous: boolean; label: string; state: "active" | "lapsed" | "blocked"; paid_until: string | null; lapsed_at: string | null;
  has_card: boolean; payments: number; paid_usd: number; devices: AdminDevice[] };
export type VpnPaymentRow = { id: number; provider: string; network: string | null; amount_usd: number; paid_usd: number; status: string; days: number;
  created_at: string | null; credited_at: string | null; account: string | null };
const CONNECTED_MS = 10 * 60_000;  // the sync reads handshakes every 5 minutes
const STATE_TONE = { active: "good", lapsed: "warn", blocked: "bad" } as const;
const STATE_LABEL = { active: "active", lapsed: "out of time", blocked: "blocked" } as const;
const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "—");

function Customers({ onChanged }: { onChanged: () => Promise<void> }) {
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
  return (
    <Card title="Customers">
      {r.failure ? <ApiErrorBanner error={r.failure} /> : null}
      {note && <p><Badge tone={note.ok ? "good" : "bad"}>{note.text}</Badge></p>}
      {r.loading && !r.data ? <Loading /> : !r.data?.items?.length ? <Empty>No customers yet.</Empty> : (
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Customer</th><th>Status</th><th>Paid until</th><th>Paid</th><th>Devices</th><th /></tr></thead>
          <tbody>
            {r.data.items.map((c) => (
              <tr key={c.id}>
                <td><b>{c.label}</b>{c.has_card && <> <Badge>card</Badge></>}</td>
                <td><Badge tone={STATE_TONE[c.state]}>{STATE_LABEL[c.state]}</Badge></td>
                <td>{day(c.paid_until)}</td>
                <td>${c.paid_usd.toFixed(2)} <span className="muted small">({c.payments})</span></td>
                <td>{c.devices.length === 0 ? <span className="muted small">none</span> : c.devices.map((d) => {
                  const on = d.last_handshake !== null && Date.now() - new Date(d.last_handshake).getTime() < CONNECTED_MS;
                  return (
                    <div key={d.id} className="small">
                      <b>{d.name}</b> · {d.server} <span className="mono muted">{d.ip}</span> <Badge tone={on ? "good" : undefined}>{on ? "connected" : `seen ${d.last_handshake ? relativeTime(d.last_handshake) : "never"}`}</Badge>{" "}
                      <button className="btn btn-sm" onClick={() => revoke(c, d)}>Revoke</button>
                    </div>
                  );
                })}</td>
                <td>{c.state === "blocked" ? <button className="btn btn-sm" onClick={() => block(c, false)}>Unblock</button>
                  : <button className="btn btn-sm btn-kill-sm" onClick={() => block(c, true)}>Block</button>}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      <BlockKey onDone={async () => { await r.reload(); await onChanged(); }} />
    </Card>
  );
}

/** Someone reported a key: paste it to block whoever holds it. It's only hashed for the lookup, never stored or shown. */
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

function Payments() {
  const r = useApi(() => request<{ items: VpnPaymentRow[] }>("/vpn/admin/payments"), 60_000);
  return (
    <Card title="Payments">
      {r.failure ? <ApiErrorBanner error={r.failure} /> : null}
      {r.loading && !r.data ? <Loading /> : !r.data?.items?.length ? <Empty>No payments yet.</Empty> : (
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

export function VpnAdminPage() {
  const r = useApi(() => request<VpnOverview>("/vpn/admin/overview"), 60_000);
  const d = r.data;
  return (
    <div className="stack">
      <PageHeader title="Kiwi VPN" subtitle="The paid WireGuard VPN ($9.99 a month): public sales, servers, customers and payments. No traffic is logged. Owner only."
        actions={<button className="btn" onClick={() => void r.reload()} disabled={r.loading}>Refresh</button>} />
      {r.failure ? <ApiErrorBanner error={r.failure} /> : null}
      {r.loading && !d ? <Loading /> : null}
      {d && <>
        <div className="stats">
          <Stat label="Devices" value={d.totals.devices} />
          <Stat label="Customers with devices" value={d.totals.members} />
          <Stat label="Active in 24h" value={d.totals.active_24h} tone={d.totals.active_24h > 0 ? "good" : "neutral"} />
          <Stat label="Servers online" value={`${d.servers.filter((x) => x.online).length} / ${d.servers.length}`}
                tone={d.servers.some((x) => x.enabled && !x.online) ? "warn" : "good"} />
        </div>
        {d.billing && (
          <p className="muted small">Paying accounts: <b>{d.billing.paying}</b> · anonymous keys: <b>{d.billing.anonymous}</b> · last 30 days: <b>${d.billing.revenue_30d_usd.toFixed(2)}</b> ·
            USDT {d.billing.usdt ? "on" : "off (no NOWPayments keys)"} · cards {d.billing.card ? "on" : "off (no Stripe keys)"}</p>
        )}
        <SettingsCard s={d.settings} onSaved={() => void r.reload()} />
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
        <Customers onChanged={r.reload} />
        <Payments />
      </>}
    </div>
  );
}
