import { useEffect, useState } from "react";
import { request } from "../../api/client";
import { Card } from "../../components/ui";
import { MarketShell } from "./mx";
import { ago, money } from "./vault/api";
import "./marketplace/marketplace.css";
import "./portfolio/portfolio.css";

/** Owner-only Vault control panel: seller sources and their eBay sync, the approval queue (only approved vintage Japanese
 * cards are public), Requests to Buy, and the physical-card registry (architecture for future card-backed NFTs). */

interface Source {
  id: number; kind: string; key: string; name: string; url: string | null; enabled: boolean; last_sync_at: string | null; last_ok_at: string | null;
  last_status: string; last_message: string; last_counts: Record<string, number | null> | null; calls_today: number;
  counts: { pending: number; approved: number; hidden: number; excluded: number };
}
interface Overview {
  ebay: { connected: boolean; message: string }; sources: Source[]; new_requests: number;
  settings: { sync_hours: number; max_age_hours: number; daily_calls: number; include_graded: boolean; requests_enabled: boolean };
}
interface Row {
  id: number; title: string; url: string | null; image: string | null; price: number | null; currency: string | null; availability: string; checked_at: string | null;
  eligible: boolean; exclude_reason: string | null; evidence: string[]; pokemon: string | null; set_name: string | null; year: number | null;
  pokemon_override: string | null; set_override: string | null; year_override: number | null; review: string; featured: boolean;
  condition: string | null; condition_detail: string | null; seller_grade: string | null;
}
interface Req {
  id: number; listing_id: number; status: string; contact: string; note: string; price: number | null; currency: string | null; created_at: string; admin_note: string;
  listing?: { title: string; url: string | null; availability: string }; member?: { username: string };
}
interface PCard { id: number; uid: string; title: string; acquisition_status: string; custody_status: string; nft_status: string; events?: { at: string; field: string; new: string | null; note: string }[] }

const json = (method: string, body: unknown) => ({ method, body: JSON.stringify(body), headers: { "Content-Type": "application/json" } });
const TABS = ["Review queue", "Approved", "Hidden", "Excluded", "Requests", "Physical cards"] as const;
type Tab = (typeof TABS)[number];

export function VaultAdmin() {
  const [o, setO] = useState<Overview | null>(null);
  const [tab, setTab] = useState<Tab>("Review queue");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [n, setN] = useState(0);
  useEffect(() => { void request<Overview>("/vault/admin/overview").then(setO).catch((e) => setMsg(String(e))); }, [n]);
  const act = async (fn: () => Promise<unknown>, done = "Saved.") => {
    setMsg(null);
    setBusy(true);
    try { const r = await fn(); setMsg(typeof r === "string" ? r : done); setN((x) => x + 1); } catch (e) { setMsg(e instanceof Error ? e.message : "Failed."); } finally { setBusy(false); }
  };
  return (
    <MarketShell crumbs={[["Markets", "/pokemon"], ["Vault admin", null]]}>
      <div className="pm">
        <h1 className="mx-h1">Price Chat Vault · control panel</h1>
        <p className="pf-muted">Public store: <a href="/pokemon" target="_blank" rel="noreferrer">pricechat.live/pokemon</a>. Only <b>approved</b> listings the classifier marks
          vintage Japanese are shown. Listings sync every {o?.settings.sync_hours ?? 3} h; a price older than {o?.settings.max_age_hours ?? 6} h is shown as unconfirmed.</p>
        {o && !o.ebay.connected && (
          <Card title="⛔ eBay isn't connected">
            <p>Add the eBay <b>App ID</b> and <b>Cert ID</b> (Production keyset) in Pokémon › Data sources. {o.ebay.message}</p>
            <p className="pf-fine">eBay keeps new Production keysets disabled until the marketplace account deletion notification endpoint is set up (or an exemption is granted)
              in the eBay developer portal.</p>
          </Card>
        )}
        {o?.sources.map((s) => (
          <Card key={s.id} title={`${s.enabled ? "🟢" : "⚪"} ${s.name} · ${s.kind === "ebay_seller" ? `eBay seller “${s.key}”` : "seller catalog"}`}>
            <p><b>{s.last_status}</b> · last sync {ago(s.last_sync_at)}{s.last_ok_at ? ` · last success ${ago(s.last_ok_at)}` : ""} · {s.calls_today} eBay calls today
              (limit {o.settings.daily_calls})</p>
            {s.last_message && <p className="pf-muted">{s.last_message}</p>}
            <p>{s.counts.pending} to review · {s.counts.approved} live · {s.counts.hidden} hidden · {s.counts.excluded} excluded by the classifier</p>
            <div className="mk-row">
              {s.kind === "ebay_seller" && <button className="pf-btn" disabled={busy || !o.ebay.connected}
                onClick={() => void act(async () => { const r = await request<{ message: string }>(`/vault/admin/sources/${s.id}/sync`, { method: "POST" }); return r.message; })}>
                {busy ? "Working…" : "Sync now"}</button>}
              <button className="pf-btn" disabled={busy} onClick={() => void act(() => request(`/vault/admin/sources/${s.id}`, json("POST", { enabled: !s.enabled })))}>
                {s.enabled ? "Disable source" : "Enable source"}</button>
            </div>
          </Card>
        ))}
        <nav className="pf-tabs">{TABS.map((t) => <a key={t} href="#" className={tab === t ? "active" : ""} onClick={(e) => { e.preventDefault(); setTab(t); }}>
          {t}{t === "Requests" && o?.new_requests ? ` (${o.new_requests} new)` : ""}</a>)}</nav>
        {msg && <p className="pf-muted" role="status">{msg}</p>}
        <div key={`${tab}-${n}`}>
          {tab === "Review queue" && <Listings query="review=pending&eligible=true&availability=available" act={act} />}
          {tab === "Approved" && <Listings query="review=approved" act={act} />}
          {tab === "Hidden" && <Listings query="review=hidden" act={act} />}
          {tab === "Excluded" && <Listings query="eligible=false&availability=available" act={act} />}
          {tab === "Requests" && <Requests act={act} />}
          {tab === "Physical cards" && <Cards act={act} />}
        </div>
      </div>
    </MarketShell>
  );
}

type Act = (fn: () => Promise<unknown>, done?: string) => Promise<void>;

function Listings({ query, act }: { query: string; act: Act }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<Set<number>>(new Set());
  useEffect(() => {
    void request<{ items: Row[]; total: number }>(`/vault/admin/listings?${query}&q=${encodeURIComponent(q)}`).then((r) => { setRows(r.items); setTotal(r.total); });
  }, [query, q]);
  const edit = (id: number, body: Record<string, unknown>, done: string) => act(() => request(`/vault/admin/listings/${id}`, json("PATCH", body)), done);
  const bulk = (review: string) => act(() => request(`/vault/admin/listings/bulk`, json("POST", { ids: [...picked], review })), `${picked.size} updated.`);
  if (!rows) return <p className="pf-muted">Loading…</p>;
  return (
    <>
      <div className="mk-row" style={{ margin: "10px 0" }}>
        <input className="pf-input" placeholder="Search titles" value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="pf-muted">{total} listings</span>
        {picked.size > 0 && <>
          <button className="pf-btn" onClick={() => void bulk("approved")}>Approve {picked.size}</button>
          <button className="pf-btn" onClick={() => void bulk("hidden")}>Hide {picked.size}</button>
        </>}
      </div>
      {rows.length === 0 && <p className="pf-muted">Nothing here.</p>}
      <div style={{ display: "grid", gap: 10 }}>
        {rows.map((r) => (
          <div key={r.id} className="mk-admin-row" style={{ display: "grid", gridTemplateColumns: "24px 72px 1fr", gap: 12, padding: 10, border: "1px solid var(--line, rgba(255,255,255,.1))", borderRadius: 12 }}>
            <input type="checkbox" aria-label="Select" checked={picked.has(r.id)} onChange={(e) => { const s = new Set(picked); if (e.target.checked) s.add(r.id); else s.delete(r.id); setPicked(s); }} />
            {r.image ? <img src={r.image} alt="" style={{ width: 72, height: 96, objectFit: "contain" }} referrerPolicy="no-referrer" /> : <span />}
            <div>
              <b>{r.title}</b> {r.url && <a href={r.url} target="_blank" rel="noreferrer">eBay ↗</a>}
              <p className="pf-muted" style={{ margin: "4px 0" }}>
                {money(r.price, r.currency)} · {r.availability} · checked {ago(r.checked_at)} · {r.condition ?? "—"}{r.seller_grade ? ` · ${r.seller_grade}` : ""} · review: <b>{r.review}</b>
              </p>
              <p style={{ margin: "4px 0" }}>{r.eligible ? "✅ vintage Japanese" : `⛔ ${r.exclude_reason}`} · {r.pokemon_override ?? r.pokemon ?? "?"} · {r.set_override ?? r.set_name ?? "set ?"} · {r.year_override ?? r.year ?? "year ?"}
                <span className="pf-fine"> ({r.evidence.join("; ")})</span></p>
              <Overrides r={r} onSave={(b) => void edit(r.id, b, "Corrected.")} />
              <div className="mk-row">
                {r.review !== "approved" && <button className="pf-btn" disabled={!r.eligible} onClick={() => void edit(r.id, { review: "approved" }, "Approved: it's in the store.")}>Approve</button>}
                {r.review !== "hidden" && <button className="pf-btn" onClick={() => void edit(r.id, { review: "hidden" }, "Hidden.")}>Hide</button>}
                {r.review === "approved" && <button className="pf-btn" onClick={() => void edit(r.id, { featured: !r.featured }, r.featured ? "Unfeatured." : "Featured.")}>{r.featured ? "★ Unfeature" : "☆ Feature"}</button>}
              </div>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

function Overrides({ r, onSave }: { r: Row; onSave: (b: Record<string, unknown>) => void }) {
  const [open, setOpen] = useState(false);
  const [p, setP] = useState(r.pokemon_override ?? "");
  const [s, setS] = useState(r.set_override ?? "");
  const [y, setY] = useState(r.year_override ? String(r.year_override) : "");
  if (!open) return <button className="pf-link" onClick={() => setOpen(true)}>Correct name / set / year</button>;
  return (
    <div className="mk-row">
      <input className="pf-input" placeholder={r.pokemon ?? "Pokémon"} value={p} onChange={(e) => setP(e.target.value)} />
      <input className="pf-input" placeholder={r.set_name ?? "Set"} value={s} onChange={(e) => setS(e.target.value)} />
      <input className="pf-input" placeholder={r.year ? String(r.year) : "Year"} value={y} inputMode="numeric" onChange={(e) => setY(e.target.value.replace(/\D/g, ""))} style={{ width: 90 }} />
      <button className="pf-btn" onClick={() => { onSave({ pokemon_override: p, set_override: s, year_override: y ? Number(y) : null }); setOpen(false); }}>Save</button>
    </div>
  );
}

function Requests({ act }: { act: Act }) {
  const [rows, setRows] = useState<Req[] | null>(null);
  useEffect(() => { void request<{ requests: Req[] }>("/vault/admin/requests").then((r) => setRows(r.requests)); }, []);
  if (!rows) return <p className="pf-muted">Loading…</p>;
  if (!rows.length) return <p className="pf-muted">No requests yet.</p>;
  const set = (id: number, body: Record<string, unknown>) => act(() => request(`/vault/admin/requests/${id}`, json("PATCH", body)));
  return (
    <table className="pf-table">
      <thead><tr><th>When</th><th>Member</th><th>Card</th><th>Price seen</th><th>Contact / note</th><th>Status</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id}>
            <td>{new Date(r.created_at).toLocaleString()}</td>
            <td>{r.member?.username}</td>
            <td>{r.listing?.url ? <a href={r.listing.url} target="_blank" rel="noreferrer">{r.listing.title}</a> : r.listing?.title} <small>({r.listing?.availability})</small></td>
            <td>{money(r.price, r.currency)}</td>
            <td>{r.contact}<br /><small>{r.note}</small>{r.admin_note && <><br /><small>📝 {r.admin_note}</small></>}</td>
            <td>
              <select value={r.status} onChange={(e) => void set(r.id, { status: e.target.value })}>
                {["new", "contacted", "closed", "declined"].map((s) => <option key={s} value={s}>{s}</option>)}
                {r.status === "withdrawn" && <option value="withdrawn">withdrawn</option>}
              </select>
              <button className="pf-link" onClick={() => { const note = window.prompt("Private note", r.admin_note); if (note !== null) void set(r.id, { admin_note: note }); }}>Note</button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Cards({ act }: { act: Act }) {
  const [rows, setRows] = useState<PCard[] | null>(null);
  useEffect(() => { void request<{ cards: PCard[] }>("/vault/admin/cards").then((r) => setRows(r.cards)); }, []);
  if (!rows) return <p className="pf-muted">Loading…</p>;
  return (
    <>
      <p className="pf-muted">Each physical card Price Chat starts acquiring gets a permanent id (PCV-…). An NFT can only be recorded after the card is received, inspected,
        photographed, verified and in custody; a card can't ship while its token exists. Nothing is minted from here.</p>
      {rows.length === 0 ? <p className="pf-muted">No physical cards yet.</p> : (
        <table className="pf-table">
          <thead><tr><th>ID</th><th>Card</th><th>Acquisition</th><th>Custody</th><th>NFT</th><th>Last change</th></tr></thead>
          <tbody>
            {rows.map((c) => {
              const last = c.events?.[c.events.length - 1];
              return (
                <tr key={c.id}>
                  <td className="vt-mono">{c.uid}</td><td>{c.title}</td><td>{c.acquisition_status}</td><td>{c.custody_status}</td><td>{c.nft_status}</td>
                  <td>{last ? `${last.field} → ${last.new ?? ""} ${ago(last.at)}` : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <button className="pf-btn" style={{ marginTop: 10 }} onClick={() => {
        const title = window.prompt("Card title (start an acquisition record)");
        if (title) void act(() => request("/vault/admin/cards", json("POST", { title })), "Acquisition record created.");
      }}>＋ Start an acquisition record</button>
    </>
  );
}
