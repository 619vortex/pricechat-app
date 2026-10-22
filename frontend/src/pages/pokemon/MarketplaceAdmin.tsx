import { useCallback, useEffect, useState } from "react";
import { request } from "../../api/client";
import { Card } from "../../components/ui";
import { MarketShell } from "./mx";
import { day } from "./format";
import { STATUS_LABEL, usdt, type Listing, type Order, type Redemption } from "./marketplace/api";
import "./marketplace/marketplace.css";
import "./portfolio/portfolio.css";

/** Owner-only marketplace control panel (the admin login protects every /api/market/admin route). Ownership- and money-changing
 * actions need a written reason and the target typed back; everything lands in the audit log. */

type AdminListing = Listing & { item: { id: number; custody: string; vault_partner: string | null; vault_inventory_id: string | null; storage_location: string | null; verification: string } };
interface Overview {
  mode: string; requested_mode: string; gates: { gate: string; ok: boolean; detail: string }[]; listings: Record<string, number>; orders: Record<string, number>;
  stats: { sales: number; volume: number; sales_30d: number; volume_30d: number; active_listings: number };
  revenue: { platform_fees: number; simulated_platform_fees: number }; inventory: { vaulted: number; awaiting_custody: number; sandbox: number };
  open_reports: number; open_disputes: number; refunds_pending: number; redemptions_open: number; pending_sellers: number;
  fees: { seller_bps: number; buyer_bps: number }; vault_partners: string[];
}

const TABS = ["Pending listings", "Custody", "Orders", "Payments", "Redemptions", "Disputes", "Fraud flags", "Sellers", "Sandbox", "Audit log"] as const;
type Tab = (typeof TABS)[number];
const post = <T,>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": "application/json" } });

export function MarketplaceAdmin() {
  const [o, setO] = useState<Overview | null>(null);
  const [tab, setTab] = useState<Tab>("Pending listings");
  const [msg, setMsg] = useState<string | null>(null);
  const [n, setN] = useState(0);
  const refresh = () => setN((x) => x + 1);
  useEffect(() => { void request<Overview>("/market/admin/overview").then(setO).catch((e) => setMsg(String(e))); }, [n]);
  const act = async (fn: () => Promise<unknown>, done = "Saved.") => {
    setMsg(null);
    try { await fn(); setMsg(done); refresh(); } catch (e) { setMsg(e instanceof Error ? e.message : "Failed."); }
  };
  return (
    <MarketShell crumbs={[["Markets", "/pokemon"], ["Marketplace admin", null]]}>
      <div className="pm">
        <h1 className="mx-h1">Card Marketplace · control panel</h1>
        {o && (
          <>
            <Card title={`Mode: ${o.mode}${o.requested_mode !== o.mode ? ` (requested ${o.requested_mode}: gates not met)` : ""}`}>
              <ul className="mk-list">{o.gates.map((g) => <li key={g.gate}>{g.ok ? "✅" : "⛔"} <b>{g.gate}</b> <span className="pf-muted">{g.detail}</span></li>)}</ul>
              <p className="pf-fine">Live USDT settlement stays off until every gate passes (and needs explicit owner approval).</p>
            </Card>
            <div className="mk-stats" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", margin: "14px 0" }}>
              {[["Active listings", o.stats.active_listings], ["Awaiting review", o.listings.awaiting_verification ?? 0], ["Awaiting custody", o.inventory.awaiting_custody],
                ["Vaulted (real)", o.inventory.vaulted], ["Volume 30D", usdt(o.stats.volume_30d)], ["Platform fees", usdt(o.revenue.platform_fees)],
                ["Simulated fees", usdt(o.revenue.simulated_platform_fees)], ["Refunds pending", o.refunds_pending], ["Open disputes", o.open_disputes],
                ["Fraud flags", o.open_reports], ["Redemptions open", o.redemptions_open], ["Sellers pending", o.pending_sellers]].map(([k, v]) => (
                <div key={String(k)}><dt>{k}</dt><dd>{v}</dd></div>))}
            </div>
          </>
        )}
        <nav className="pf-tabs">{TABS.map((t) => <a key={t} href="#" className={tab === t ? "active" : ""} onClick={(e) => { e.preventDefault(); setTab(t); }}>{t}</a>)}</nav>
        {msg && <p className="pf-muted" role="status">{msg}</p>}
        <div key={`${tab}-${n}`}>
          {tab === "Pending listings" && <ListingQueue status="awaiting_verification" act={act} />}
          {tab === "Custody" && <ListingQueue status="awaiting_custody" act={act} partners={o?.vault_partners ?? []} />}
          {tab === "Orders" && <Orders act={act} />}
          {tab === "Payments" && <Simple path="/market/admin/payments" k="payments" cols={["order_id", "network", "tx", "status", "problem", "simulated", "verified_at"]} />}
          {tab === "Redemptions" && <Redemptions act={act} />}
          {tab === "Disputes" && <Disputes act={act} />}
          {tab === "Fraud flags" && <Reports act={act} />}
          {tab === "Sellers" && <Sellers act={act} />}
          {tab === "Sandbox" && <Sandbox act={act} />}
          {tab === "Audit log" && <Audit />}
        </div>
      </div>
    </MarketShell>
  );
}

type Act = (fn: () => Promise<unknown>, done?: string) => Promise<void>;

function useGet<T>(path: string): T | null {
  const [d, setD] = useState<T | null>(null);
  useEffect(() => { void request<T>(path).then(setD).catch(() => setD(null)); }, [path]);
  return d;
}

function ListingQueue({ status, act, partners = [] }: { status: string; act: Act; partners?: string[] }) {
  const d = useGet<{ listings: AdminListing[] }>(`/market/admin/listings?status=${status}`);
  const [note, setNote] = useState<Record<number, string>>({});
  const [c, setC] = useState<Record<number, Record<string, string>>>({});
  if (!d) return <p className="pf-muted">Loading…</p>;
  if (!d.listings.length) return <p className="pf-muted">Nothing here.</p>;
  return (
    <ul className="mk-rows">{d.listings.map((l) => {
      const f = c[l.item.id] ?? {};
      const setF = (k: string, v: string) => setC({ ...c, [l.item.id]: { ...f, [k]: v } });
      return (
        <li key={l.id} style={{ alignItems: "start" }}>
          <div>{l.photos.map((p) => <a key={p.id} href={`/api/market/admin/photos/${p.id}`} target="_blank" rel="noreferrer"><img src={`/api/market/admin/photos/${p.id}?size=thumb`} alt={p.side} title={p.side} /></a>)}
            {l.card.image_url && <img src={l.card.image_url} alt="catalogue" title="catalogue image" />}</div>
          <div>
            <b>#{l.id} {l.card.name}</b> · {l.card.year} {l.card.set_name} {l.card.card_number} · {l.card.edition} {l.card.variant}<br />
            {l.grading_company} {l.grade} · cert <b>{l.cert}</b> · {usdt(l.price)} · est. {l.estimate.value !== null ? `$${(l.estimate.value / 100).toFixed(2)}` : "—"} ({l.diff_pct ?? "—"}%)<br />
            Seller {l.seller?.username} ({l.seller?.verified ? `verified L${l.seller.level}` : "unverified"}) · item {l.item.id} · custody {l.item.custody} · {l.item.vault_partner ?? ""} {l.item.vault_inventory_id ?? ""}
            {l.flags && l.flags.length > 0 && <p className="mk-warn">⚑ {l.flags.join(" · ")}</p>}
            <p className="pf-fine">Evidence: {l.ownership_evidence}</p>
            <p className="pf-fine">Check: slab label ↔ artwork ↔ catalogue printing, cert on the grader's site, photos consistent, no duplicate cert/photo flags.</p>
            {status === "awaiting_verification" ? (
              <div className="pf-actions">
                <input placeholder="Review note (required to reject)" value={note[l.id] ?? ""} onChange={(e) => setNote({ ...note, [l.id]: e.target.value })} />
                <button type="button" className="pf-shiny pf-sm" onClick={() => void act(() => post(`/market/admin/listings/${l.id}/verify`, { approve: true, note: note[l.id] ?? "" }), "Verified: awaiting custody.")}>Approve identity</button>
                <button type="button" className="pf-btn pf-sm pf-danger" onClick={() => void act(() => post(`/market/admin/listings/${l.id}/verify`, { approve: false, note: note[l.id] ?? "" }), "Rejected.")}>Reject</button>
              </div>
            ) : (
              <div className="pf-actions">
                <select value={f.partner ?? ""} onChange={(e) => setF("partner", e.target.value)}><option value="">Vault partner…</option>{partners.map((p) => <option key={p}>{p}</option>)}</select>
                <button type="button" className="pf-btn pf-sm" onClick={() => void act(() => post(`/market/admin/items/${l.item.id}/custody`, { to: "submitted" }))}>Mark shipped to vault</button>
                <button type="button" className="pf-btn pf-sm" onClick={() => void act(() => post(`/market/admin/items/${l.item.id}/custody`, { to: "received", partner: f.partner }))}>Vault received</button>
                <input placeholder="Vault inventory id" value={f.inventory_id ?? ""} onChange={(e) => setF("inventory_id", e.target.value)} />
                <input placeholder="Storage location" value={f.location ?? ""} onChange={(e) => setF("location", e.target.value)} />
                <label className="pf-check"><input type="checkbox" checked={f.ins === "1"} onChange={(e) => setF("ins", e.target.checked ? "1" : "")} /> insurance confirmed by partner</label>
                <input placeholder="Insurance coverage & limits" value={f.ins_note ?? ""} onChange={(e) => setF("ins_note", e.target.value)} />
                <input placeholder="Reason" value={f.reason ?? ""} onChange={(e) => setF("reason", e.target.value)} />
                <input placeholder={`Type ITEM-${l.item.id}`} value={f.confirm ?? ""} onChange={(e) => setF("confirm", e.target.value)} />
                <button type="button" className="pf-shiny pf-sm" onClick={() => void act(() => post(`/market/admin/items/${l.item.id}/custody`, {
                  to: "vaulted", partner: f.partner, inventory_id: f.inventory_id, location: f.location, insurance_confirmed: f.ins === "1", insurance_note: f.ins_note ?? "",
                  reason: f.reason, confirm: f.confirm }), "Custody confirmed: listing verified.")}>Confirm custody</button>
                {partners.length === 0 && <p className="mk-warn">No vault partner is contracted: custody can't be confirmed (Price Chat has no vault of its own).</p>}
              </div>
            )}
          </div>
          <div className="mk-row-side"><button type="button" className="pf-link" onClick={() => {
            const r = window.prompt("Reason for cancelling this listing (min 8 chars)");
            if (r) void act(() => post(`/market/admin/listings/${l.id}/cancel`, { reason: r }), "Cancelled.");
          }}>Cancel listing</button></div>
        </li>
      );
    })}</ul>
  );
}

function Orders({ act }: { act: Act }) {
  const d = useGet<{ orders: Order[] }>("/market/admin/orders");
  if (!d) return <p className="pf-muted">Loading…</p>;
  const confirm = (o: Order, path: string, label: string) => {
    const reason = window.prompt(`${label}: reason (audit log)`);
    if (!reason) return;
    const c = window.prompt(`Type ORDER-${o.ref} to confirm`);
    if (c) void act(() => post(`/market/admin/orders/${o.id}/${path}`, { reason, confirm: c }), `${label} done.`);
  };
  return d.orders.length ? (
    <ul className="mk-list">{d.orders.map((o) => (
      <li key={o.id}><b>#{o.ref}</b> {o.card?.name} {o.grading_company} {o.grade} · {usdt(o.total)} · {o.network_label} · <span className={`mk-status mk-st-${o.status}`}>{STATUS_LABEL[o.status] ?? o.status}</span>
        {o.sandbox && <span className="mk-sandbox">SANDBOX</span>} <small className="pf-muted">{o.note} {day(o.created_at)}</small>
        {o.status === "refund_pending" && <button type="button" className="pf-btn pf-sm" onClick={() => confirm(o, "refund", "Refund")}>Record refund</button>}
        {o.status === "paid" && <button type="button" className="pf-btn pf-sm" onClick={() => confirm(o, "settle", "Retry settlement")}>Retry settlement</button>}
      </li>))}</ul>
  ) : <p className="pf-muted">No orders.</p>;
}

function Redemptions({ act }: { act: Act }) {
  const d = useGet<{ redemptions: Redemption[] }>("/market/admin/redemptions");
  if (!d) return <p className="pf-muted">Loading…</p>;
  const step = (r: Redemption, to: string) => {
    const body: Record<string, string> = { to };
    if (to === "shipped") { body.carrier = window.prompt("Carrier") ?? ""; body.tracking = window.prompt("Tracking number") ?? ""; }
    if (to === "released" || to === "delivered") { body.reason = window.prompt("Reason (audit log)") ?? ""; body.confirm = window.prompt(`Type REDEMPTION-${r.id}`) ?? ""; }
    void act(() => post(`/market/admin/redemptions/${r.id}`, body), `Redemption ${to}.`);
  };
  const NEXT: Record<string, string[]> = { requested: ["approved", "cancelled"], approved: ["released", "cancelled"], released: ["shipped"], shipped: ["delivered", "returned", "lost"] };
  return d.redemptions.length ? (
    <ul className="mk-list">{d.redemptions.map((r) => (
      <li key={r.id}><b>#{r.id}</b> item {r.item_id} · {r.status} · {usdt(r.total)} · {r.country} {r.tracking ? `· ${r.carrier} ${r.tracking}` : ""}
        <small className="pf-muted">{r.address ? Object.values(r.address).filter(Boolean).join(", ") : ""}</small>
        {(NEXT[r.status] ?? []).map((to) => <button key={to} type="button" className="pf-btn pf-sm" onClick={() => step(r, to)}>{to}</button>)}</li>))}</ul>
  ) : <p className="pf-muted">No redemptions.</p>;
}

function Disputes({ act }: { act: Act }) {
  const d = useGet<{ disputes: { id: number; order_id: number | null; redemption_id: number | null; reason: string; status: string; resolution: string; at: string }[] }>("/market/admin/disputes");
  if (!d) return <p className="pf-muted">Loading…</p>;
  return d.disputes.length ? (
    <ul className="mk-list">{d.disputes.map((x) => (
      <li key={x.id}><b>#{x.id}</b> {x.order_id ? `order ${x.order_id}` : `redemption ${x.redemption_id}`} · {x.status} · {x.reason} {x.resolution && <i>→ {x.resolution}</i>}
        {x.status === "open" && ["resolved", "rejected"].map((s) => <button key={s} type="button" className="pf-btn pf-sm" onClick={() => {
          const res = window.prompt("Resolution (min 8 chars)");
          if (res) void act(() => post(`/market/admin/disputes/${x.id}`, { status: s, resolution: res }));
        }}>{s}</button>)}</li>))}</ul>
  ) : <p className="pf-muted">No disputes.</p>;
}

function Reports({ act }: { act: Act }) {
  const d = useGet<{ reports: { id: number; listing_id: number; reason: string; auto: boolean; status: string; at: string }[] }>("/market/admin/reports");
  if (!d) return <p className="pf-muted">Loading…</p>;
  return d.reports.length ? (
    <ul className="mk-list">{d.reports.map((r) => (
      <li key={r.id}>{r.auto ? "🤖" : "👤"} listing {r.listing_id} · {r.reason} · {r.status} <small className="pf-muted">{day(r.at)}</small>
        {r.status === "open" && <><button type="button" className="pf-btn pf-sm" onClick={() => void act(() => post(`/market/admin/reports/${r.id}`, { status: "dismissed" }))}>Dismiss</button>
          <button type="button" className="pf-btn pf-sm" onClick={() => void act(() => post(`/market/admin/reports/${r.id}`, { status: "actioned" }))}>Actioned</button></>}</li>))}</ul>
  ) : <p className="pf-muted">No flags.</p>;
}

function Sellers({ act }: { act: Act }) {
  const d = useGet<{ sellers: { username: string; status: string; level: number; sandbox: boolean; country: string | null; notes: string }[] }>("/market/admin/sellers");
  const [u, setU] = useState("");
  const set = (body: Record<string, unknown>) => void act(() => post("/market/admin/sellers", body));
  return (
    <div>
      <div className="pf-actions"><input placeholder="username" value={u} onChange={(e) => setU(e.target.value)} />
        <button type="button" className="pf-btn pf-sm" onClick={() => set({ username: u, status: "verified", level: 1, note: "identity checked" })}>Verify (L1)</button>
        <button type="button" className="pf-btn pf-sm" onClick={() => set({ username: u, status: "verified", level: 2, note: "enhanced verification" })}>Enhanced (L2)</button>
        <button type="button" className="pf-btn pf-sm pf-danger" onClick={() => set({ username: u, status: "suspended", note: "suspended" })}>Suspend</button>
        <button type="button" className="pf-btn pf-sm" onClick={() => set({ username: u, sandbox: true })}>Grant sandbox</button>
        <button type="button" className="pf-btn pf-sm" onClick={() => set({ username: u, sandbox: false })}>Revoke sandbox</button></div>
      {d && <ul className="mk-list">{[...d.sellers].sort((a, b) => Number(b.status === "pending") - Number(a.status === "pending")).map((s) => (
        <li key={s.username}><b>{s.username}</b> {s.status === "pending" ? <span className="mk-sandbox">PENDING REVIEW</span> : s.status} · L{s.level} · {s.country ?? "—"}
          {s.sandbox && <span className="mk-sandbox">SANDBOX</span>}
          {s.status === "pending" && <>
            <button type="button" className="pf-btn pf-sm" onClick={() => set({ username: s.username, status: "verified", level: 1, note: "verification request approved" })}>Approve (L1)</button>
            <button type="button" className="pf-btn pf-sm pf-danger" onClick={() => set({ username: s.username, status: "unverified", note: "verification request declined" })}>Decline</button>
            {s.notes && <p className="pf-fine" style={{ whiteSpace: "pre-wrap" }}>{s.notes.trim().split("\n").slice(-1)[0]}</p>}</>}
        </li>))}</ul>}
    </div>
  );
}

function Sandbox({ act }: { act: Act }) {
  const [f, setF] = useState({ username: "", card_id: "", grading_company: "PSA", grade: "9", price: "" });
  return (
    <div>
      <p className="pf-muted">Creates SIMULATION inventory (catalogue image, synthetic cert, sandbox vault). Visible only to members with sandbox access; never counted as volume.</p>
      <div className="pf-actions">
        {(["username", "card_id", "grade", "price"] as const).map((k) => <input key={k} placeholder={k} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} />)}
        <select value={f.grading_company} onChange={(e) => setF({ ...f, grading_company: e.target.value })}><option>PSA</option><option>BGS</option><option>CGC</option></select>
        <button type="button" className="pf-shiny pf-sm" onClick={() => void act(() => post("/market/admin/sandbox/listings", { ...f, card_id: Number(f.card_id), price: Number(f.price) }), "Sandbox listing live.")}>Create sandbox listing</button>
      </div>
    </div>
  );
}

function Simple({ path, k, cols }: { path: string; k: string; cols: string[] }) {
  const d = useGet<Record<string, Record<string, unknown>[]>>(path);
  if (!d) return <p className="pf-muted">Loading…</p>;
  const rows = d[k] ?? [];
  return rows.length ? <table className="mk-fees"><thead><tr>{cols.map((c) => <th key={c} style={{ textAlign: "left" }}>{c}</th>)}</tr></thead>
    <tbody>{rows.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c} style={{ textAlign: "left" }}>{String(r[c] ?? "")}</td>)}</tr>)}</tbody></table> : <p className="pf-muted">None.</p>;
}

function Audit() {
  const d = useGet<{ audit: { at: string; actor: string; action: string; target: string; data: Record<string, unknown> }[] }>("/market/admin/audit");
  const [rec, setRec] = useState<{ ok: boolean; problems: string[] } | null>(null);
  const run = useCallback(() => void request<{ ok: boolean; problems: string[] }>("/market/admin/reconcile").then(setRec), []);
  return (
    <div>
      <button type="button" className="pf-btn pf-sm" onClick={run}>Run reconciliation</button>
      {rec && <p className={rec.ok ? "pf-ok" : "pf-error"}>{rec.ok ? "Ledger, ownership and reservations are consistent." : rec.problems.join(" · ")}</p>}
      {d && <ul className="mk-list">{d.audit.map((a, i) => <li key={i}><small>{day(a.at)}</small> <b>{a.action}</b> {a.target} <span className="pf-muted">{a.actor} {JSON.stringify(a.data)}</span></li>)}</ul>}
    </div>
  );
}
