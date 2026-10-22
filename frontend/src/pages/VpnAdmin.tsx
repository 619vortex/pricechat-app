import { useEffect, useState } from "react";
import { request } from "../api/client";
import { useApi } from "../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Field, Loading, PageHeader, Stat, relativeTime } from "../components/ui";

/* Kiwi VPN (OWNER ONLY): who may use it (min Kiwi Rank, devices per member), and the WireGuard servers members connect to.
   Each server runs vpn-node's agent; the backend talks to it over HTTPS with a pinned certificate and a bearer token. */

export type VpnSettings = { enabled: boolean; min_rank: string; max_devices: number };
export type VpnAdminServer = { id: number; name: string; region: string; country_code: string; endpoint_host: string | null; listen_port: number | null;
  agent_url: string; capacity: number; enabled: boolean; online: boolean; last_seen: string | null; last_error: string | null; devices: number; active_24h: number };
export type VpnOverview = { settings: VpnSettings; ranks: string[]; servers: VpnAdminServer[]; totals: { devices: number; members: number; active_24h: number } };

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

function SettingsCard({ s, ranks, onSaved }: { s: VpnSettings; ranks: string[]; onSaved: () => void }) {
  const [v, setV] = useState(s);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => setV(s), [s]);
  const dirty = v.enabled !== s.enabled || v.min_rank !== s.min_rank || v.max_devices !== s.max_devices;
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
    <Card title="Settings">
      <div className="form">
        <div className="row">
          <span><b>Kiwi VPN open to members</b><br /><span className="muted small">Off: the page says "coming soon" and nobody can add devices.</span></span>
          <label className="toggle" title={v.enabled ? "On" : "Off"}>
            <input type="checkbox" aria-label="VPN enabled" checked={v.enabled} onChange={(e) => setV({ ...v, enabled: e.target.checked })} /><span />
          </label>
        </div>
        <div className="row-fields">
          <Field label="Minimum Kiwi Rank" hint="Members below it see the locked page; devices of members who drop below are revoked.">
            <select className="input" aria-label="Minimum rank" value={v.min_rank} onChange={(e) => setV({ ...v, min_rank: e.target.value })}>
              {(ranks.includes(v.min_rank) ? ranks : [v.min_rank, ...ranks]).map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </Field>
          <Field label="Devices per member" hint="1 to 10.">
            <input className="input" type="number" min={1} max={10} aria-label="Max devices" value={v.max_devices}
                   onChange={(e) => setV({ ...v, max_devices: Math.max(1, Math.min(10, Number(e.target.value) || 1)) })} />
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
    <tr>
      <td><b>{x.name}</b><br /><span className="muted small">{x.country_code.toUpperCase()} · {x.region}</span>
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
          <button className="btn btn-sm btn-kill-sm" disabled={busy} onClick={remove}>Delete</button>
        </div>
        {note && <Badge tone={note.ok ? "good" : "bad"}>{note.text}</Badge>}
      </td>
    </tr>
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
      <p className="muted small">Run <span className="mono">vpn-node/install.sh</span> on a fresh Ubuntu VPS and paste what it prints.</p>
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

export function VpnAdminPage() {
  const r = useApi(() => request<VpnOverview>("/vpn/admin/overview"), 60_000);
  const d = r.data;
  return (
    <div className="stack">
      <PageHeader title="Kiwi VPN" subtitle="The WireGuard VPN members unlock with their Kiwi Rank: who can use it, and the servers. No traffic is logged. Owner only."
        actions={<button className="btn" onClick={() => void r.reload()} disabled={r.loading}>Refresh</button>} />
      {r.failure ? <ApiErrorBanner error={r.failure} /> : null}
      {r.loading && !d ? <Loading /> : null}
      {d && <>
        <div className="stats">
          <Stat label="Devices" value={d.totals.devices} />
          <Stat label="Members using it" value={d.totals.members} />
          <Stat label="Active in 24h" value={d.totals.active_24h} tone={d.totals.active_24h > 0 ? "good" : "neutral"} />
          <Stat label="Servers online" value={`${d.servers.filter((x) => x.online).length} / ${d.servers.length}`}
                tone={d.servers.some((x) => x.enabled && !x.online) ? "warn" : "good"} />
        </div>
        <SettingsCard s={d.settings} ranks={d.ranks} onSaved={() => void r.reload()} />
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
    </div>
  );
}
