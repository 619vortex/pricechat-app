import { useState } from "react";
import { request } from "../api/client";
import { useApi } from "../api/useApi";
import { ApiErrorBanner, Badge, Card, Empty, Loading, PageHeader, Tabs, relativeTime, type Tone } from "../components/ui";

/** Admin → Rank Rewards: what each Kiwi rank unlocks, the Kiwi Chick lessons, the Knight trading reward
 *  (funding, terms, claims) and Royal WhatsApp VIP access. Every change is in the history tab. The WhatsApp link is write-only
 *  here: it's never sent back, only shown to approved Royal members. Nothing on this page sends money. */

interface Reward { rank_key: string; kind: string; title: string; description: string; enabled: boolean }
interface Settings {
  knight_status: "NOT_CONFIGURED" | "ACTIVE" | "PAUSED"; knight_amount_usd: number; knight_terms: string; knight_regions: string; knight_budget: number;
  knight_expires_at: string | null; vip_description: string; vip_invite_set: boolean;
  arena_kills_per_xp: number; arena_run_xp_cap: number; arena_daily_xp_cap: number; arena_min_run_seconds: number; arena_runs_per_hour: number;
}
interface Content { id: number; title: string; description: string; video_url: string | null; thumbnail_url: string | null; guide_text: string; position: number; published: boolean; updated_at: string }
interface Overview { rewards: Reward[]; settings: Settings; knight: { active: boolean; status: string }; content: Content[] }
interface Claim {
  id: string; status: string; created_at: string; reviewed_at: string | null; reward: "knight" | "royal"; member: { id: string; username: string; rank_level: number };
  rank_at_claim: number; wallet: string | null; member_note: string; admin_note: string; fulfillment_ref: string | null; reviewed_by: string | null;
}
interface Audit { id: number; at: string; actor: string; action: string; member_id: number | null; claim_id: number | null; details: Record<string, unknown> }

type Tab = "rewards" | "content" | "knight" | "royal" | "history";
const TONE: Record<string, Tone> = { PENDING: "warn", APPROVED: "good", FULFILLED: "good", REJECTED: "bad", REVOKED: "bad", ACTIVE: "good", PAUSED: "warn", NOT_CONFIGURED: "neutral" };
const put = <T,>(path: string, body: unknown) => request<T>(path, { method: "PUT", body: JSON.stringify(body) });
const post = <T,>(path: string, body: unknown = {}) => request<T>(path, { method: "POST", body: JSON.stringify(body) });

export function RewardsAdminPage() {
  const [tab, setTab] = useState<Tab>(() => (new URLSearchParams(window.location.search).get("tab") as Tab | null) ?? "rewards");
  const ov = useApi(() => request<Overview>("/rewards-admin/overview"));
  return (
    <div className="page">
      <PageHeader title="Rank rewards" subtitle="What each Kiwi rank unlocks, and reward claims waiting for you." />
      <Tabs<Tab> value={tab} onChange={setTab} tabs={[
        { key: "rewards", label: "Rewards" }, { key: "knight", label: "$10 claims" }, { key: "royal", label: "VIP requests" },
        { key: "content", label: "Beginner lessons" }, { key: "history", label: "History", more: true },
      ]} />
      <ApiErrorBanner error={ov.failure} />
      {!ov.data ? <Loading /> : (
        <>
          {tab === "rewards" && <RewardsTab rewards={ov.data.rewards} reload={ov.reload} />}
          {tab === "content" && <ContentTab items={ov.data.content} reload={ov.reload} />}
          {tab === "knight" && <KnightTab s={ov.data.settings} reload={ov.reload} />}
          {tab === "royal" && <RoyalTab s={ov.data.settings} reload={ov.reload} />}
          {tab === "history" && <HistoryTab />}
        </>
      )}
    </div>
  );
}

const RANK_NAME: Record<string, string> = {
  egg: "The Egg", chick: "Kiwi Chick", kiwi: "Kiwi", scout: "Scout Kiwi", warrior: "Warrior Kiwi", knight: "Knight Kiwi", royal: "Royal Kiwi",
  golden: "Golden Kiwi", legend: "Kiwi Legend", mythic: "Mythic Kiwi",
};

function RewardsTab({ rewards, reload }: { rewards: Reward[]; reload: () => Promise<void> }) {
  return (
    <Card title="What each rank unlocks">
      <p className="muted small">Turning a reward off locks it for everyone. Golden, Legend and Mythic stay "To Be Announced".</p>
      <div className="rwa-list">{rewards.map((r) => <RewardRow key={r.rank_key} r={r} reload={reload} />)}</div>
    </Card>
  );
}

function RewardRow({ r, reload }: { r: Reward; reload: () => Promise<void> }) {
  const [v, setV] = useState(r);
  const [err, setErr] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  async function save(next: Partial<Reward>) {
    setErr(null); setSaved(false);
    try { await put(`/rewards-admin/rewards/${r.rank_key}`, next); setSaved(true); await reload(); } catch (e) { setErr(e); }
  }
  const [edit, setEdit] = useState(false);
  const fixed = r.kind === "none" || r.kind === "tba";
  return (
    <div className="rwa-row">
      <div className="rwa-row-head">
        <b className="rwa-rank">{RANK_NAME[r.rank_key] ?? r.rank_key}</b>
        <span className="rwa-title">{r.title}</span>
        {!fixed && <Badge tone={r.enabled ? "good" : "neutral"}>{r.enabled ? "On" : "Off"}</Badge>}
        <span className="btn-group">
          {!fixed && <button className="btn btn-sm" onClick={() => void save({ enabled: !r.enabled })}>{r.enabled ? "Turn off" : "Turn on"}</button>}
          <button className="btn btn-sm btn-ghost" aria-expanded={edit} onClick={() => setEdit((x) => !x)}>{edit ? "Close" : "Edit text"}</button>
        </span>
      </div>
      {edit && (
        <>
          <input className="input" value={v.title} maxLength={96} onChange={(e) => setV({ ...v, title: e.target.value })} aria-label={`${r.rank_key} title`} />
          <textarea className="input" rows={2} value={v.description} onChange={(e) => setV({ ...v, description: e.target.value })} aria-label={`${r.rank_key} description`} />
          <div><button className="btn btn-sm" onClick={() => void save({ title: v.title, description: v.description })}>Save text</button>
            {saved && <span className="muted small"> Saved ✓</span>}</div>
        </>
      )}
      <ApiErrorBanner error={err} />
    </div>
  );
}

function ContentTab({ items, reload }: { items: Content[]; reload: () => Promise<void> }) {
  return (
    <>
      <Card title="New beginner lesson">
        <p className="muted small">Shown at /learn to Kiwi Chicks and above, only once published. Until something is published members see "Coming soon".</p>
        <ContentForm onSaved={reload} />
      </Card>
      <Card title={`Lessons (${items.length})`}>
        {items.length === 0 ? <Empty>No lessons yet.</Empty> : items.map((c) => <ContentForm key={c.id} item={c} onSaved={reload} />)}
      </Card>
    </>
  );
}

function ContentForm({ item, onSaved }: { item?: Content; onSaved: () => Promise<void> }) {
  const blank = { title: "", description: "", video_url: "", thumbnail_url: "", guide_text: "", position: 0, published: false };
  const [v, setV] = useState(item ? { ...item, video_url: item.video_url ?? "", thumbnail_url: item.thumbnail_url ?? "" } : blank);
  const [err, setErr] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  async function save(publish?: boolean) {
    setErr(null); setSaved(false);
    const body = { title: v.title, description: v.description, video_url: v.video_url, thumbnail_url: v.thumbnail_url, guide_text: v.guide_text,
                   position: Number(v.position) || 0, published: publish ?? v.published };
    try {
      if (item) await put(`/rewards-admin/content/${item.id}`, body); else { await post("/rewards-admin/content", body); setV(blank); }
      setSaved(true); await onSaved();
    } catch (e) { setErr(e); }
  }
  async function remove() {
    if (!item || !window.confirm(`Delete "${item.title}"?`)) return;
    try { await request(`/rewards-admin/content/${item.id}`, { method: "DELETE" }); await onSaved(); } catch (e) { setErr(e); }
  }
  return (
    <form className="rwa-form" onSubmit={(e) => { e.preventDefault(); void save(); }}>
      {item && <div className="rwa-row-head"><b>{item.title}</b> <Badge tone={item.published ? "good" : "neutral"}>{item.published ? "published" : "draft"}</Badge></div>}
      <label>Title<input className="input" required maxLength={160} value={v.title} onChange={(e) => setV({ ...v, title: e.target.value })} /></label>
      <label>Description<textarea className="input" rows={2} value={v.description} onChange={(e) => setV({ ...v, description: e.target.value })} /></label>
      <label>Video URL (https; YouTube links play on the page)<input className="input" value={v.video_url} onChange={(e) => setV({ ...v, video_url: e.target.value })} /></label>
      <label>Thumbnail URL (https)<input className="input" value={v.thumbnail_url} onChange={(e) => setV({ ...v, thumbnail_url: e.target.value })} /></label>
      <label>Guide text (plain text; a blank line starts a new paragraph)<textarea className="input" rows={5} value={v.guide_text} onChange={(e) => setV({ ...v, guide_text: e.target.value })} /></label>
      <label>Order<input className="input" type="number" min={0} value={v.position} onChange={(e) => setV({ ...v, position: Number(e.target.value) })} /></label>
      <div className="rwa-actions">
        <button className="btn btn-primary" type="submit">{item ? "Save" : "Save draft"}</button>
        {item && <button className="btn btn-primary" type="button" onClick={() => void save(!item.published)}>{item.published ? "Unpublish" : "Publish"}</button>}
        {item && <button className="btn btn-ghost" type="button" onClick={() => void remove()}>Delete</button>}
        {saved && <span className="muted small">Saved ✓</span>}
      </div>
      <ApiErrorBanner error={err} />
    </form>
  );
}

type Field = { key: keyof Settings; label: string; type: "number" | "text" | "textarea" | "status" | "date"; hint?: string };
const KNIGHT_FIELDS: Field[] = [
  { key: "knight_status", label: "Status", type: "status", hint: "Members can only claim while ACTIVE with written terms." },
  { key: "knight_amount_usd", label: "Amount (USD)", type: "number" },
  { key: "knight_budget", label: "Funded rewards (max approved + fulfilled claims)", type: "number" },
  { key: "knight_terms", label: "Terms shown to members (trading-credit conditions, how it's paid)", type: "textarea" },
  { key: "knight_regions", label: "Geographic restrictions shown to members", type: "textarea" },
  { key: "knight_expires_at", label: "Expires (optional)", type: "date" },
];

function SettingsForm({ s, reload, fields, title, note }: { s: Settings; reload: () => Promise<void>; fields: Field[]; title: React.ReactNode; note?: string }) {
  const [v, setV] = useState<Settings>(s);
  const [err, setErr] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  async function save() {
    setErr(null); setSaved(false);
    const body: Record<string, unknown> = {};
    for (const f of fields) {
      const val = v[f.key];
      body[f.key] = f.type === "number" ? Number(val) : f.type === "date" ? (val ? new Date(String(val)).toISOString() : null) : val;
    }
    try { await put("/rewards-admin/settings", body); setSaved(true); await reload(); } catch (e) { setErr(e); }
  }
  return (
    <Card title={title}>
      {note && <p className="muted small">{note}</p>}
      <form className="rwa-form" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        {fields.map((f) => (
          <label key={f.key}>{f.label}
            {f.type === "status" ? (
              <select className="input" value={String(v[f.key])} onChange={(e) => setV({ ...v, [f.key]: e.target.value })}>
                <option value="NOT_CONFIGURED">Not configured (members see "Pending activation")</option>
                <option value="ACTIVE">Active (claimable)</option>
                <option value="PAUSED">Paused</option>
              </select>
            ) : f.type === "textarea" ? (
              <textarea className="input" rows={3} value={String(v[f.key] ?? "")} onChange={(e) => setV({ ...v, [f.key]: e.target.value })} />
            ) : f.type === "date" ? (
              <input className="input" type="date" value={v[f.key] ? String(v[f.key]).slice(0, 10) : ""} onChange={(e) => setV({ ...v, [f.key]: e.target.value || null })} />
            ) : (
              <input className="input" type={f.type} min={0} value={String(v[f.key] ?? "")} onChange={(e) => setV({ ...v, [f.key]: e.target.value })} />
            )}
            {f.hint && <span className="muted small">{f.hint}</span>}
          </label>
        ))}
        <div className="rwa-actions"><button className="btn btn-primary" type="submit">Save</button>{saved && <span className="muted small">Saved ✓</span>}</div>
        <ApiErrorBanner error={err} />
      </form>
    </Card>
  );
}

function KnightTab({ s, reload }: { s: Settings; reload: () => Promise<void> }) {
  return (
    <>
      <div className="banner banner-warn">Nothing is sent automatically. Approve a claim once it's eligible, pay it yourself, then mark it fulfilled with the reference.</div>
      <SettingsForm s={s} reload={reload} fields={KNIGHT_FIELDS} title={<>Knight trading reward <Badge tone={TONE[s.knight_status]}>{s.knight_status}</Badge></>} />
      <Claims reward="knight" />
    </>
  );
}

function RoyalTab({ s, reload }: { s: Settings; reload: () => Promise<void> }) {
  const [link, setLink] = useState("");
  const [desc, setDesc] = useState(s.vip_description);
  const [err, setErr] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  async function save(body: Record<string, unknown>) {
    setErr(null); setSaved(false);
    try { await put("/rewards-admin/settings", body); setSaved(true); setLink(""); await reload(); } catch (e) { setErr(e); }
  }
  return (
    <>
      <Card title="WhatsApp VIP group">
        <p className="muted small">The invite link is stored encrypted and only shown to members whose request you approved (while they're Royal Kiwi or above).
          It's never displayed here again. Describe the group with what you can confirm: don't name experts or investors unless they've agreed.</p>
        <form className="rwa-form" onSubmit={(e) => { e.preventDefault(); void save({ vip_invite_url: link, vip_description: desc }); }}>
          <label>Invite link {s.vip_invite_set ? <Badge tone="good">set</Badge> : <Badge tone="warn">not set</Badge>}
            <input className="input" type="url" placeholder={s.vip_invite_set ? "Leave empty to keep the current link" : "https://chat.whatsapp.com/..."} value={link}
                   onChange={(e) => setLink(e.target.value)} autoComplete="off" /></label>
          <label>Description shown to approved members<textarea className="input" rows={3} value={desc} onChange={(e) => setDesc(e.target.value)} /></label>
          <div className="rwa-actions">
            <button className="btn btn-primary" type="submit" onClick={(e) => { if (!link) { e.preventDefault(); void save({ vip_description: desc }); } }}>Save</button>
            {s.vip_invite_set && <button className="btn btn-ghost" type="button" onClick={() => { if (window.confirm("Remove the invite link?")) void save({ vip_invite_url: "" }); }}>Remove link</button>}
            {saved && <span className="muted small">Saved ✓</span>}
          </div>
          <ApiErrorBanner error={err} />
        </form>
      </Card>
      <Claims reward="royal" />
    </>
  );
}

const ACTIONS: Record<"knight" | "royal", Record<string, string[]>> = {
  knight: { PENDING: ["approve", "reject"], APPROVED: ["fulfill", "reject"], REJECTED: ["reopen"], FULFILLED: [], REVOKED: [] },
  royal: { PENDING: ["approve", "reject"], APPROVED: ["revoke"], REVOKED: ["restore"], REJECTED: ["reopen"], FULFILLED: [] },
};

function Claims({ reward }: { reward: "knight" | "royal" }) {
  const [status, setStatus] = useState("");
  const list = useApi(() => request<{ claims: Claim[] }>(`/rewards-admin/claims?reward=${reward}${status ? `&status=${status}` : ""}`));
  const [err, setErr] = useState<unknown>(null);
  async function act(c: Claim, action: string) {
    setErr(null);
    let reference: string | null = null;
    if (action === "fulfill") {
      reference = window.prompt("Fulfilment reference (how and when it was paid):") ?? null;
      if (!reference) return;
    }
    const note = window.prompt(`Note for "${action}" (internal, optional):`) ?? "";
    try { await post(`/rewards-admin/claims/${c.id}/${action}`, { note, reference }); await list.reload(); } catch (e) { setErr(e); }
  }
  return (
    <Card title={reward === "knight" ? "Knight claims" : "VIP access requests"} actions={
      <select className="input" value={status} onChange={(e) => { setStatus(e.target.value); window.setTimeout(() => void list.reload(), 0); }} aria-label="Filter by status">
        <option value="">All</option>{["PENDING", "APPROVED", "FULFILLED", "REJECTED", "REVOKED"].map((x) => <option key={x}>{x}</option>)}
      </select>}>
      <ApiErrorBanner error={err ?? list.failure} />
      {!list.data ? <Loading /> : list.data.claims.length === 0 ? <Empty>Nothing here yet.</Empty> : (
        <table className="table">
          <thead><tr><th>Member</th><th>Status</th><th>Rank</th>{reward === "knight" && <th>Wallet</th>}<th>Requested</th><th>Notes</th><th /></tr></thead>
          <tbody>{list.data.claims.map((c) => (
            <tr key={c.id}>
              <td>@{c.member.username}</td>
              <td><Badge tone={TONE[c.status]}>{c.status}</Badge>{c.fulfillment_ref && <div className="muted small">{c.fulfillment_ref}</div>}</td>
              <td>{c.member.rank_level} <span className="muted small">(at claim {c.rank_at_claim})</span></td>
              {reward === "knight" && <td className="mono small">{c.wallet ? `${c.wallet.slice(0, 6)}…${c.wallet.slice(-4)}` : "–"}</td>}
              <td>{relativeTime(c.created_at)}</td>
              <td className="small">{c.member_note && <div>“{c.member_note}”</div>}{c.admin_note && <div className="muted">{c.admin_note}</div>}</td>
              <td className="rwa-actions">{(ACTIONS[reward][c.status] ?? []).map((a) => <button key={a} className="btn btn-sm" onClick={() => void act(c, a)}>{a}</button>)}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </Card>
  );
}

function HistoryTab() {
  const h = useApi(() => request<{ items: Audit[] }>("/rewards-admin/history"));
  return (
    <Card title="Reward history (claims, decisions, settings, content)">
      <ApiErrorBanner error={h.failure} />
      {!h.data ? <Loading /> : h.data.items.length === 0 ? <Empty>No activity yet.</Empty> : (
        <table className="table">
          <thead><tr><th>When</th><th>Who</th><th>What</th><th>Details</th></tr></thead>
          <tbody>{h.data.items.map((a) => (
            <tr key={a.id}><td>{relativeTime(a.at)}</td><td>{a.actor}</td><td>{a.action}</td><td className="mono small">{JSON.stringify(a.details)}</td></tr>
          ))}</tbody>
        </table>
      )}
    </Card>
  );
}
